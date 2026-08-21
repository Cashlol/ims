from sqlalchemy.orm import Session
from sqlalchemy import or_, func, case
from sqlalchemy.exc import IntegrityError
from . import models, schemas
from .auth import get_password_hash
from email.message import EmailMessage
import smtplib
from datetime import datetime
import os

DEFAULT_MACHINE_CATEGORIES = ["Machine", "Storage", "Tape Library", "Networking"]


def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()


def create_user(db: Session, user: schemas.UserCreate):
    hashed_password = get_password_hash(user.password)
    db_user = models.User(
        email=user.email,
        hashed_password=hashed_password,
        full_name=user.full_name,
        is_active=user.is_active,
        is_admin=user.is_admin,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def list_users(db: Session):
    return db.query(models.User).order_by(models.User.id.asc()).all()


def count_users(db: Session) -> int:
    return int(db.query(func.count(models.User.id)).scalar() or 0)


def update_user_admin_fields(
    db: Session,
    user_id: int,
    full_name: str | None = None,
    is_active: bool | None = None,
    is_admin: bool | None = None,
):
    row = get_user(db, user_id)
    if not row:
        return None
    if full_name is not None:
        row.full_name = (full_name or "").strip() or None
    if is_active is not None:
        row.is_active = bool(is_active)
    if is_admin is not None:
        row.is_admin = bool(is_admin)
    db.commit()
    db.refresh(row)
    return row


def set_user_password(db: Session, user_id: int, new_password: str):
    row = get_user(db, user_id)
    if not row:
        return None
    row.hashed_password = get_password_hash(new_password)
    db.commit()
    db.refresh(row)
    return row


def get_parts(db: Session, skip: int = 0, limit: int = 100, search_query: str | None = None):
    query = db.query(models.SkuPart)

    if search_query:
        search = f"%{search_query}%"
        query = (
            query.outerjoin(models.PartItem)
            .filter(
                or_(
                    models.SkuPart.sku.ilike(search),
                    models.SkuPart.name.ilike(search),
                    models.PartItem.vendor.ilike(search),
                    models.PartItem.serial_no.ilike(search),
                )
            )
        )

    total = query.distinct(models.SkuPart.id).count()
    parts = (
        query.distinct(models.SkuPart.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return parts, total


def get_parts_by_platform(
    db: Session,
    is_machine: bool,
    skip: int = 0,
    limit: int = 100,
    search_query: str | None = None,
):
    query = db.query(models.SkuPart)
    if is_machine:
        query = query.filter(models.SkuPart.platform == "Machine")
    else:
        query = query.filter(or_(models.SkuPart.platform.is_(None), models.SkuPart.platform != "Machine"))

    if search_query:
        search = f"%{search_query}%"
        query = (
            query.outerjoin(models.PartItem)
            .filter(
                or_(
                    models.SkuPart.sku.ilike(search),
                    models.SkuPart.name.ilike(search),
                    models.PartItem.vendor.ilike(search),
                    models.PartItem.serial_no.ilike(search),
                )
            )
        )

    total = query.distinct(models.SkuPart.id).count()
    parts = (
        query.distinct(models.SkuPart.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return parts, total


def create_part(db: Session, part: schemas.SkuPartCreate):
    db_part = models.SkuPart(**part.dict())
    db.add(db_part)
    db.commit()
    db.refresh(db_part)
    return db_part


def create_part_item(db: Session, item: schemas.PartItemCreate):
    db_item = models.PartItem(**item.dict())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def update_part(db: Session, part_id: int, part: schemas.SkuPartUpdate):
    db_part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if db_part:
        for key, value in part.dict(exclude_unset=True).items():
            setattr(db_part, key, value)
        db.commit()
        db.refresh(db_part)
    return db_part


def delete_part(db: Session, part_id: int):
    db_part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if db_part:
        db.delete(db_part)
        db.commit()
        return True
    return False


def get_part_items_for_sku(
    db: Session,
    sku_id: int,
    outgoing_flag: bool | None = None,
    returning_flag: bool | None = None,
):
    query = db.query(models.PartItem).filter(models.PartItem.sku_id == sku_id)
    if outgoing_flag is not None:
        query = query.filter(models.PartItem.outgoing_flag == outgoing_flag)
    if returning_flag is not None:
        query = query.filter(models.PartItem.returning_flag == returning_flag)
    return query.order_by(models.PartItem.serial_no.asc()).all()


def update_part_items_flags(
    db: Session,
    item_ids: list[int],
    outgoing_flag: bool | None = None,
    returning_flag: bool | None = None,
    date_out: datetime | None = None,
    customer: str | None = None,
    engineer: str | None = None,
    vendor: str | None = None,
    remarks: str | None = None,
    price_usd: float | None = None,
    price_myr: float | None = None,
    conversion_rate: float | None = None,
):
    if not item_ids:
        return 0
    items = db.query(models.PartItem).filter(models.PartItem.id.in_(item_ids)).all()
    count = 0
    for item in items:
        if outgoing_flag is not None:
            item.outgoing_flag = outgoing_flag
        if returning_flag is not None:
            item.returning_flag = returning_flag
        if date_out is not None:
            item.date_out = date_out
        if customer is not None:
            item.customer = customer
        if engineer is not None:
            item.engineer = engineer
        if vendor is not None:
            item.vendor = vendor
        if remarks is not None:
            item.remarks = remarks
        if price_usd is not None:
            item.price_usd = price_usd
        if price_myr is not None:
            item.price_myr = price_myr
        if conversion_rate is not None:
            item.conversion_rate = conversion_rate
        count += 1
    db.commit()
    return count


def log_activity(
    db: Session,
    user_id: int | None,
    action: str,
    part_id: int | None = None,
    description: str | None = None,
):
    log = models.ActivityLog(
        user_id=user_id,
        action=action,
        part_id=part_id,
        description=description,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_activity_logs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
):
    query = (
        db.query(models.ActivityLog)
        .filter(~models.ActivityLog.action.in_(["OUTGOING_PART", "RETURNING_FAULTY", "RETURNING_PART"]))
        .order_by(models.ActivityLog.created_at.desc())
    )
    total = query.count()
    logs = query.offset(skip).limit(limit).all()
    return logs, total


def get_movement_logs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    actions: list[str] | None = None,
    include_prefix: str | None = None,
    exclude_actions: list[str] | None = None,
):
    query = (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.created_at.desc())
    )

    if include_prefix:
        query = query.filter(models.ActivityLog.action.like(f"{include_prefix}%"))
    elif actions is None:
        query = query.filter(models.ActivityLog.action.in_(["OUTGOING_PART", "RETURNING_FAULTY"]))
    else:
        query = query.filter(models.ActivityLog.action.in_(actions))

    if exclude_actions:
        query = query.filter(~models.ActivityLog.action.in_(exclude_actions))

    total = query.count()
    logs = query.offset(skip).limit(limit).all()
    return logs, total


def record_part_movement(
    db: Session,
    part_id: int,
    user_id: int | None,
    movement_type: str,
    quantity: int,
    client_name: str | None = None,
    engineer_name: str | None = None,
    part_status: str | None = None,
    remark: str | None = None,
    ticket_no: str | None = None,
    serials: list[str] | None = None,
    base_url: str | None = None,
):
    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if not part:
        return None

    available_count = (
        db.query(models.PartItem)
        .filter(
            models.PartItem.sku_id == part.id,
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .count()
    )

    parts = [f"Quantity: {quantity}"]
    if client_name:
        parts.append(f"Client: {client_name}")
    if engineer_name:
        parts.append(f"Engineer: {engineer_name}")
    if part_status:
        parts.append(f"Status: {part_status}")
    if serials:
        joined = ", ".join([s for s in serials if s])
        if joined:
            parts.append(f"Serial No: {joined}")
    if ticket_no:
        parts.append(f"Ticket No: {ticket_no}")
    if remark:
        parts.append(f"Remark: {remark}")

    description = "; ".join(parts)

    log = models.ActivityLog(
        user_id=user_id,
        action=movement_type,
        part_id=part_id,
        description=description,
    )

    db.add(part)
    db.add(log)
    db.commit()
    db.refresh(part)
    db.refresh(log)

    return part


def get_sku_part_meta(db: Session, part_id: int):
    return (
        db.query(models.SkuPartMeta)
        .filter(models.SkuPartMeta.sku_id == part_id)
        .first()
    )


def upsert_sku_part_meta(
    db: Session,
    part_id: int,
    customer_usage: str | None = None,
    image_filename: str | None = None,
):
    meta = get_sku_part_meta(db, part_id)
    if not meta:
        meta = models.SkuPartMeta(sku_id=part_id)
        db.add(meta)

    if customer_usage is not None:
        meta.customer_usage = customer_usage
    if image_filename is not None:
        meta.image_filename = image_filename

    db.commit()
    db.refresh(meta)
    return meta


def get_faulty_items(db: Session, skip: int = 0, limit: int = 100):
    query = (
        db.query(models.PartItem)
        .filter(models.PartItem.returning_flag.is_(True), models.PartItem.outgoing_flag.is_(False))
        .order_by(models.PartItem.created_at.desc())
    )
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return items, total


def create_client_faulty_log(
    db: Session,
    user_id: int | None,
    client_name: str | None,
    engineer_name: str | None,
    part_no: str | None,
    part_description: str | None,
    serial_no: str | None,
    part_status: str | None,
    remark: str | None,
    ticket_no: str | None,
):
    linked_part_id = None
    sku = (part_no or "").strip()
    if sku:
        p = db.query(models.SkuPart).filter(models.SkuPart.sku == sku).first()
        if p:
            linked_part_id = p.id
            if not part_description:
                part_description = p.name

    row = models.ClientFaultyLog(
        user_id=user_id,
        part_id=linked_part_id,
        client_name=(client_name or "").strip() or None,
        engineer_name=(engineer_name or "").strip() or None,
        part_no=sku or None,
        part_description=(part_description or "").strip() or None,
        serial_no=(serial_no or "").strip() or None,
        part_status=(part_status or "").strip() or None,
        remark=(remark or "").strip() or None,
        ticket_no=(ticket_no or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_client_faulty_logs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    search_query: str | None = None,
    disposed: bool | None = None,
):
    query = db.query(models.ClientFaultyLog).order_by(models.ClientFaultyLog.created_at.desc())
    if disposed is True:
        query = query.filter(models.ClientFaultyLog.disposed_flag.is_(True))
    elif disposed is False:
        query = query.filter(or_(models.ClientFaultyLog.disposed_flag.is_(False), models.ClientFaultyLog.disposed_flag.is_(None)))
    if search_query:
        s = f"%{search_query}%"
        query = query.filter(
            or_(
                models.ClientFaultyLog.client_name.ilike(s),
                models.ClientFaultyLog.part_no.ilike(s),
                models.ClientFaultyLog.serial_no.ilike(s),
                models.ClientFaultyLog.ticket_no.ilike(s),
            )
        )
    total = query.count()
    rows = query.offset(skip).limit(limit).all()
    return rows, total


def create_machine_category(db: Session, name: str, description: str | None = None):
    n = (name or "").strip()
    if not n:
        return None
    existing = db.query(models.MachineCategory).filter(models.MachineCategory.name == n).first()
    if existing:
        return existing
    row = models.MachineCategory(name=n, description=(description or "").strip() or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_machine_categories(db: Session):
    return db.query(models.MachineCategory).order_by(models.MachineCategory.name.asc()).all()


def ensure_default_machine_categories(db: Session):
    existing = {r.name for r in db.query(models.MachineCategory).all()}
    created = False
    for name in DEFAULT_MACHINE_CATEGORIES:
        if name not in existing:
            db.add(models.MachineCategory(name=name, description=f"Default category: {name}"))
            created = True
    if created:
        db.commit()
    return get_machine_categories(db)


def set_part_machine_category(db: Session, part_id: int, machine_category_id: int | None):
    existing = (
        db.query(models.SkuPartMachineCategory)
        .filter(models.SkuPartMachineCategory.sku_id == part_id)
        .first()
    )

    if not machine_category_id:
        if existing:
            db.delete(existing)
            db.commit()
        return None

    mc = db.query(models.MachineCategory).filter(models.MachineCategory.id == machine_category_id).first()
    if not mc:
        return None

    if not existing:
        existing = models.SkuPartMachineCategory(sku_id=part_id, machine_category_id=machine_category_id)
        db.add(existing)
    else:
        existing.machine_category_id = machine_category_id

    db.commit()
    db.refresh(existing)
    return existing


def get_part_machine_category(db: Session, part_id: int):
    return (
        db.query(models.SkuPartMachineCategory)
        .filter(models.SkuPartMachineCategory.sku_id == part_id)
        .first()
    )


def create_machine_registry_entry(
    db: Session,
    category_type: str,
    serial_number: str | None = None,
    brand: str | None = None,
    model_no: str | None = None,
    product_type: str | None = None,
    rack_level: str | None = None,
    room_no: str | None = None,
    rack_type: str | None = None,
    tray_type: str | None = None,
    platform: str | None = None,
    criticality: str | None = None,
    customer: str | None = None,
    cpu_count: str | None = None,
    core_count_speed: str | None = None,
    memory_spec: str | None = None,
    disk_drives: str | None = None,
    nic_card: str | None = None,
    adapter_card: str | None = None,
    power_supply: str | None = None,
    tape_drives: str | None = None,
    network_ports: str | None = None,
    notes: str | None = None,
    image_filename: str | None = None,
    price_usd: float | None = None,
    conversion_rate: float | None = None,
    price_myr: float | None = None,
    lead_time_days: int | None = None,
    holding_cost_percentage: float | None = None,
    annual_demand: int | None = None,
    shipping_cost: float | None = None,
):
    ctype = (category_type or "").strip()
    if ctype not in DEFAULT_MACHINE_CATEGORIES:
        return None

    row = models.MachineRegistry(
        category_type=ctype,
        serial_number=(serial_number or "").strip() or None,
        brand=(brand or "").strip() or None,
        model_no=(model_no or "").strip() or None,
        product_type=(product_type or "").strip() or None,
        rack_level=(rack_level or "").strip() or None,
        room_no=(room_no or "").strip() or None,
        rack_type=(rack_type or "").strip() or None,
        tray_type=(tray_type or "").strip() or None,
        platform=(platform or "").strip() or None,
        criticality=(criticality or "").strip() or None,
        customer=(customer or "").strip() or None,
        cpu_count=(cpu_count or "").strip() or None,
        core_count_speed=(core_count_speed or "").strip() or None,
        memory_spec=(memory_spec or "").strip() or None,
        disk_drives=(disk_drives or "").strip() or None,
        nic_card=(nic_card or "").strip() or None,
        adapter_card=(adapter_card or "").strip() or None,
        power_supply=(power_supply or "").strip() or None,
        tape_drives=(tape_drives or "").strip() or None,
        network_ports=(network_ports or "").strip() or None,
        notes=(notes or "").strip() or None,
        image_filename=(image_filename or "").strip() or None,
        price_usd=float(price_usd or 0.0),
        conversion_rate=float(conversion_rate or 0.0),
        price_myr=float(price_myr or 0.0),
        lead_time_days=int(lead_time_days or 7),
        holding_cost_percentage=float(holding_cost_percentage or 0.20),
        annual_demand=int(annual_demand or 0),
        shipping_cost=float(shipping_cost or 0.0),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_machine_registry_by_type(db: Session, category_type: str, limit: int = 20):
    ctype = (category_type or "").strip()
    return (
        db.query(models.MachineRegistry)
        .filter(models.MachineRegistry.category_type == ctype)
        .order_by(models.MachineRegistry.created_at.desc())
        .limit(limit)
        .all()
    )


def get_machine_registry_entries(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    search_query: str | None = None,
):
    query = db.query(models.MachineRegistry)
    if not search_query:
        query = query.order_by(models.MachineRegistry.created_at.desc())
        total = query.count()
        rows = query.offset(skip).limit(limit).all()
        return rows, total

    s = f"%{search_query}%"
    query = query.outerjoin(
        models.MachineRegistrySerial,
        models.MachineRegistrySerial.registry_id == models.MachineRegistry.id,
    ).filter(
        or_(
            models.MachineRegistry.category_type.ilike(s),
            models.MachineRegistry.serial_number.ilike(s),
            models.MachineRegistry.model_no.ilike(s),
            models.MachineRegistry.product_type.ilike(s),
            models.MachineRegistry.brand.ilike(s),
            models.MachineRegistrySerial.serial_no.ilike(s),
        )
    )

    total = (
        db.query(func.count(func.distinct(models.MachineRegistry.id)))
        .select_from(models.MachineRegistry)
        .outerjoin(
            models.MachineRegistrySerial,
            models.MachineRegistrySerial.registry_id == models.MachineRegistry.id,
        )
        .filter(
            or_(
                models.MachineRegistry.category_type.ilike(s),
                models.MachineRegistry.serial_number.ilike(s),
                models.MachineRegistry.model_no.ilike(s),
                models.MachineRegistry.product_type.ilike(s),
                models.MachineRegistry.brand.ilike(s),
                models.MachineRegistrySerial.serial_no.ilike(s),
            )
        )
        .scalar()
        or 0
    )

    rows = (
        query.distinct(models.MachineRegistry.id)
        .order_by(models.MachineRegistry.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, total


def get_machine_registry_serials(
    db: Session,
    registry_id: int,
    outgoing_flag: bool | None = None,
):
    query = db.query(models.MachineRegistrySerial).filter(models.MachineRegistrySerial.registry_id == registry_id)
    if outgoing_flag is not None:
        if outgoing_flag:
            query = query.filter(models.MachineRegistrySerial.outgoing_flag.is_(True))
        else:
            query = query.filter(or_(models.MachineRegistrySerial.outgoing_flag.is_(False), models.MachineRegistrySerial.outgoing_flag.is_(None)))
    return query.order_by(models.MachineRegistrySerial.serial_no.asc()).all()


def add_machine_registry_serial(db: Session, registry_id: int, serial_no: str):
    s = (serial_no or "").strip()
    if not s:
        return None
    row = models.MachineRegistrySerial(registry_id=registry_id, serial_no=s)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(row)
    return row


def delete_machine_registry_serial(db: Session, serial_id: int):
    row = db.query(models.MachineRegistrySerial).filter(models.MachineRegistrySerial.id == serial_id).first()
    if not row:
        return None
    db.delete(row)
    db.commit()
    return row


def set_machine_registry_serials_outgoing(
    db: Session,
    registry_id: int,
    serial_ids: list[int],
    outgoing_flag: bool,
    customer: str | None = None,
    engineer: str | None = None,
    remarks: str | None = None,
    ticket_no: str | None = None,
    date_out: datetime | None = None,
):
    if not serial_ids:
        return 0
    query = (
        db.query(models.MachineRegistrySerial)
        .filter(
            models.MachineRegistrySerial.registry_id == registry_id,
            models.MachineRegistrySerial.id.in_(serial_ids),
        )
    )
    rows = query.all()
    count = 0
    for r in rows:
        r.outgoing_flag = outgoing_flag
        if outgoing_flag:
            r.date_out = date_out or datetime.utcnow()
            r.customer = customer
            r.engineer = engineer
            r.remarks = remarks
            r.ticket_no = ticket_no
        else:
            r.outgoing_flag = False
            r.date_out = None
        count += 1
    db.commit()
    return count


def get_machine_registry_serial_counts(db: Session, registry_ids: list[int]):
    if not registry_ids:
        return {}
    rows = (
        db.query(models.MachineRegistrySerial.registry_id, func.count(models.MachineRegistrySerial.id))
        .filter(
            models.MachineRegistrySerial.registry_id.in_(registry_ids),
            or_(models.MachineRegistrySerial.outgoing_flag.is_(False), models.MachineRegistrySerial.outgoing_flag.is_(None)),
        )
        .group_by(models.MachineRegistrySerial.registry_id)
        .all()
    )
    return {rid: int(cnt) for rid, cnt in rows}


def get_machine_registry_entry(db: Session, entry_id: int):
    return db.query(models.MachineRegistry).filter(models.MachineRegistry.id == entry_id).first()


def update_machine_registry_entry(
    db: Session,
    entry_id: int,
    category_type: str | None = None,
    serial_number: str | None = None,
    brand: str | None = None,
    model_no: str | None = None,
    product_type: str | None = None,
    rack_level: str | None = None,
    room_no: str | None = None,
    rack_type: str | None = None,
    tray_type: str | None = None,
    platform: str | None = None,
    criticality: str | None = None,
    customer: str | None = None,
    cpu_count: str | None = None,
    core_count_speed: str | None = None,
    memory_spec: str | None = None,
    disk_drives: str | None = None,
    nic_card: str | None = None,
    adapter_card: str | None = None,
    power_supply: str | None = None,
    tape_drives: str | None = None,
    network_ports: str | None = None,
    notes: str | None = None,
    image_filename: str | None = None,
    price_usd: float | None = None,
    conversion_rate: float | None = None,
    price_myr: float | None = None,
    lead_time_days: int | None = None,
    holding_cost_percentage: float | None = None,
    annual_demand: int | None = None,
    shipping_cost: float | None = None,
):
    row = get_machine_registry_entry(db, entry_id)
    if not row:
        return None

    if category_type is not None:
        ctype = (category_type or "").strip()
        if ctype in DEFAULT_MACHINE_CATEGORIES:
            row.category_type = ctype

    if serial_number is not None:
        row.serial_number = (serial_number or "").strip() or None
    if brand is not None:
        row.brand = (brand or "").strip() or None
    if model_no is not None:
        row.model_no = (model_no or "").strip() or None
    if product_type is not None:
        row.product_type = (product_type or "").strip() or None
    if rack_level is not None:
        row.rack_level = (rack_level or "").strip() or None
    if room_no is not None:
        row.room_no = (room_no or "").strip() or None
    if rack_type is not None:
        row.rack_type = (rack_type or "").strip() or None
    if tray_type is not None:
        row.tray_type = (tray_type or "").strip() or None
    if platform is not None:
        row.platform = (platform or "").strip() or None
    if criticality is not None:
        row.criticality = (criticality or "").strip() or None
    if customer is not None:
        row.customer = (customer or "").strip() or None
    if cpu_count is not None:
        row.cpu_count = (cpu_count or "").strip() or None
    if core_count_speed is not None:
        row.core_count_speed = (core_count_speed or "").strip() or None
    if memory_spec is not None:
        row.memory_spec = (memory_spec or "").strip() or None
    if disk_drives is not None:
        row.disk_drives = (disk_drives or "").strip() or None
    if nic_card is not None:
        row.nic_card = (nic_card or "").strip() or None
    if adapter_card is not None:
        row.adapter_card = (adapter_card or "").strip() or None
    if power_supply is not None:
        row.power_supply = (power_supply or "").strip() or None
    if tape_drives is not None:
        row.tape_drives = (tape_drives or "").strip() or None
    if network_ports is not None:
        row.network_ports = (network_ports or "").strip() or None
    if notes is not None:
        row.notes = (notes or "").strip() or None
    if image_filename is not None:
        row.image_filename = (image_filename or "").strip() or None
    if price_usd is not None:
        row.price_usd = float(price_usd or 0.0)
    if conversion_rate is not None:
        row.conversion_rate = float(conversion_rate or 0.0)
    if price_myr is not None:
        row.price_myr = float(price_myr or 0.0)
    if lead_time_days is not None:
        row.lead_time_days = int(lead_time_days or 0)
    if holding_cost_percentage is not None:
        row.holding_cost_percentage = float(holding_cost_percentage or 0.0)
    if annual_demand is not None:
        row.annual_demand = int(annual_demand or 0)
    if shipping_cost is not None:
        row.shipping_cost = float(shipping_cost or 0.0)

    db.commit()
    db.refresh(row)
    return row


def delete_machine_registry_entry(db: Session, entry_id: int):
    row = get_machine_registry_entry(db, entry_id)
    if not row:
        return None
    db.delete(row)
    db.commit()
    return row


def get_rack_inventory_summary(db: Session):
    rows = (
        db.query(
            models.SkuPart.rack_type.label("rack_type"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("total_stock"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.price_myr,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("total_value_myr"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.price_usd,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("total_value_usd"),
            func.coalesce(
                func.avg(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.conversion_rate,
                        ),
                        else_=None,
                    )
                ),
                0.0,
            ).label("avg_conversion_rate"),
        )
        .outerjoin(models.PartItem, models.PartItem.sku_id == models.SkuPart.id)
        .group_by(models.SkuPart.rack_type)
        .order_by(models.SkuPart.rack_type.asc())
        .all()
    )

    grand_total_stock = sum(r.total_stock or 0 for r in rows)
    grand_total_value_myr = sum(r.total_value_myr or 0.0 for r in rows)
    grand_total_value_usd = sum(r.total_value_usd or 0.0 for r in rows)

    return rows, grand_total_stock, grand_total_value_myr, grand_total_value_usd


def get_tray_inventory_summary(db: Session):
    rows = (
        db.query(
            models.SkuPart.tray_type.label("rack_type"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("total_stock"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.price_myr,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("total_value_myr"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.price_usd,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("total_value_usd"),
            func.coalesce(
                func.avg(
                    case(
                        (
                            (models.PartItem.outgoing_flag.is_(False))
                            & (models.PartItem.returning_flag.is_(False)),
                            models.PartItem.conversion_rate,
                        ),
                        else_=None,
                    )
                ),
                0.0,
            ).label("avg_conversion_rate"),
        )
        .outerjoin(models.PartItem, models.PartItem.sku_id == models.SkuPart.id)
        .group_by(models.SkuPart.tray_type)
        .order_by(models.SkuPart.tray_type.asc())
        .all()
    )

    grand_total_stock = sum(r.total_stock or 0 for r in rows)
    grand_total_value_myr = sum(r.total_value_myr or 0.0 for r in rows)
    grand_total_value_usd = sum(r.total_value_usd or 0.0 for r in rows)

    return rows, grand_total_stock, grand_total_value_myr, grand_total_value_usd


def get_rack_inventory_items(db: Session, skip: int = 0, limit: int = 50):
    rack_nulls_last = case((models.SkuPart.rack_type.is_(None), 1), else_=0)
    part_nulls_last = case((models.SkuPart.sku.is_(None), 1), else_=0)
    serial_nulls_last = case((models.PartItem.serial_no.is_(None), 1), else_=0)

    total_count = (
        db.query(func.count(models.PartItem.id))
        .join(models.SkuPart, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .scalar()
        or 0
    )

    rows = (
        db.query(
            models.SkuPart.rack_type.label("rack_type"),
            models.SkuPart.sku.label("part_no"),
            models.SkuPart.name.label("part_description"),
            models.SkuPart.part_category.label("part_category"),
            models.PartItem.serial_no.label("serial_no"),
            func.count(models.PartItem.id).over(partition_by=[models.SkuPart.rack_type, models.SkuPart.id]).label("quantity"),
            models.PartItem.price_usd.label("unit_price_usd"),
            models.PartItem.price_myr.label("unit_price_myr"),
        )
        .join(models.PartItem, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .order_by(
            rack_nulls_last.asc(),
            models.SkuPart.rack_type.asc(),
            part_nulls_last.asc(),
            models.SkuPart.sku.asc(),
            serial_nulls_last.asc(),
            models.PartItem.serial_no.asc(),
            models.PartItem.id.asc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, int(total_count)


def get_tray_inventory_items(db: Session, skip: int = 0, limit: int = 50):
    tray_nulls_last = case((models.SkuPart.tray_type.is_(None), 1), else_=0)
    part_nulls_last = case((models.SkuPart.sku.is_(None), 1), else_=0)
    serial_nulls_last = case((models.PartItem.serial_no.is_(None), 1), else_=0)

    total_count = (
        db.query(func.count(models.PartItem.id))
        .join(models.SkuPart, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .scalar()
        or 0
    )

    rows = (
        db.query(
            models.SkuPart.tray_type.label("tray_type"),
            models.SkuPart.sku.label("part_no"),
            models.SkuPart.name.label("part_description"),
            models.SkuPart.part_category.label("part_category"),
            models.PartItem.serial_no.label("serial_no"),
            func.count(models.PartItem.id).over(partition_by=[models.SkuPart.tray_type, models.SkuPart.id]).label("quantity"),
            models.PartItem.price_usd.label("unit_price_usd"),
            models.PartItem.price_myr.label("unit_price_myr"),
        )
        .join(models.PartItem, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .order_by(
            tray_nulls_last.asc(),
            models.SkuPart.tray_type.asc(),
            part_nulls_last.asc(),
            models.SkuPart.sku.asc(),
            serial_nulls_last.asc(),
            models.PartItem.serial_no.asc(),
            models.PartItem.id.asc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, int(total_count)


def get_all_inventory_items(db: Session, skip: int = 0, limit: int = 50):
    rack_nulls_last = case((models.SkuPart.rack_type.is_(None), 1), else_=0)
    tray_nulls_last = case((models.SkuPart.tray_type.is_(None), 1), else_=0)
    part_nulls_last = case((models.SkuPart.sku.is_(None), 1), else_=0)
    serial_nulls_last = case((models.PartItem.serial_no.is_(None), 1), else_=0)

    total_count = (
        db.query(func.count(models.PartItem.id))
        .join(models.SkuPart, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .scalar()
        or 0
    )

    rows = (
        db.query(
            models.SkuPart.rack_type.label("rack_type"),
            models.SkuPart.tray_type.label("tray_type"),
            models.SkuPart.sku.label("part_no"),
            models.SkuPart.name.label("part_description"),
            models.SkuPart.part_category.label("part_category"),
            models.PartItem.serial_no.label("serial_no"),
            func.count(models.PartItem.id).over(partition_by=[models.SkuPart.rack_type, models.SkuPart.tray_type, models.SkuPart.id]).label("quantity"),
            models.PartItem.price_usd.label("unit_price_usd"),
            models.PartItem.price_myr.label("unit_price_myr"),
        )
        .join(models.PartItem, models.PartItem.sku_id == models.SkuPart.id)
        .filter(
            models.PartItem.outgoing_flag.is_(False),
            models.PartItem.returning_flag.is_(False),
        )
        .order_by(
            rack_nulls_last.asc(),
            models.SkuPart.rack_type.asc(),
            tray_nulls_last.asc(),
            models.SkuPart.tray_type.asc(),
            part_nulls_last.asc(),
            models.SkuPart.sku.asc(),
            serial_nulls_last.asc(),
            models.PartItem.serial_no.asc(),
            models.PartItem.id.asc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, int(total_count)


def get_country_inventory_summary(db: Session):
    rows = []
    grand_total_stock = 0
    grand_total_value_myr = 0.0
    return rows, grand_total_stock, grand_total_value_myr


def get_country_rack_inventory_summary(db: Session):
    rows = []
    grand_total_stock = 0
    grand_total_value_myr = 0.0
    return rows, grand_total_stock, grand_total_value_myr


def get_config(db: Session):
    cfg = db.query(models.Config).first()
    if not cfg:
        cfg = models.Config()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def update_config(db: Session, config_in: schemas.ConfigUpdate):
    cfg = get_config(db)
    for key, value in config_in.dict(exclude_unset=True).items():
        setattr(cfg, key, value)
    db.commit()
    db.refresh(cfg)
    return cfg


def send_email_notification(
    cfg: models.Config,
    subject: str,
    body: str,
):
    if not cfg.notification_email or not cfg.email_sender or not cfg.smtp_server:
        return False, "Missing notification_email, email_sender, or smtp_server"

    username = cfg.smtp_username or cfg.email_sender
    password = cfg.smtp_password
    if not password:
        return False, "Missing SMTP password"

    recipients = [
        addr.strip()
        for addr in cfg.notification_email.split(",")
        if addr and addr.strip()
    ]
    if not recipients:
        return False, "No valid recipient emails configured"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        if cfg.smtp_use_tls:
            with smtplib.SMTP(cfg.smtp_server, cfg.smtp_port) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp_server, cfg.smtp_port) as server:
                server.login(username, password)
                server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)
