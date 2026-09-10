from fastapi import FastAPI, Depends, HTTPException, status, Request, Form, Response, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from . import crud, models, schemas, auth
from .database import engine, SessionLocal
from datetime import timedelta, datetime
from typing import Optional
from urllib.parse import quote
from pathlib import Path
import os
import json
import re
import httpx
import asyncio
import html
from urllib.parse import urlparse, urlsplit, parse_qs, unquote
import io
import csv
import pandas as pd
import time

def _excel_stream_response(rows: list[dict], filename: str, sheet_name: str):
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)

    output.seek(0)
    headers_resp = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_resp,
    )


def _load_dotenv():
    def parse_line(line: str):
        s = (line or "").strip()
        if not s or s.startswith("#"):
            return None, None
        if s.startswith("export "):
            s = s[len("export ") :].strip()
        if "=" not in s:
            return None, None
        key, val = s.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            return None, None
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        return key, val

    root = Path(__file__).resolve().parent.parent
    candidates = [root / ".env", Path(__file__).resolve().parent / ".env"]
    for p in candidates:
        try:
            if not p.exists() or not p.is_file():
                continue
            for raw in p.read_text(encoding="utf-8").splitlines():
                k, v = parse_line(raw)
                if not k:
                    continue
                if k in os.environ:
                    continue
                os.environ[k] = v or ""
        except Exception:
            continue


_load_dotenv()

_VENDOR_OFFERS_CACHE: dict[tuple, tuple[float, list]] = {}
_VENDOR_AI_SUMMARY_CACHE: dict[tuple, tuple[float, dict]] = {}

models.Base.metadata.create_all(bind=engine)
with engine.connect() as conn:
    cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('client_faulty_logs')").fetchall()]
    if "ticket_no" not in cols and cols:
        conn.exec_driver_sql("ALTER TABLE client_faulty_logs ADD COLUMN ticket_no VARCHAR")
    if "disposed_flag" not in cols and cols:
        conn.exec_driver_sql("ALTER TABLE client_faulty_logs ADD COLUMN disposed_flag BOOLEAN DEFAULT 0")
    if "disposed_at" not in cols and cols:
        conn.exec_driver_sql("ALTER TABLE client_faulty_logs ADD COLUMN disposed_at DATETIME")

    sp_cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('sku_parts')").fetchall()]
    if sp_cols:
        if "part_category" not in sp_cols:
            conn.exec_driver_sql("ALTER TABLE sku_parts ADD COLUMN part_category VARCHAR")

    mr_cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('machine_registry')").fetchall()]
    if mr_cols:
        if "rack_level" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN rack_level VARCHAR")
        if "room_no" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN room_no VARCHAR")
        if "rack_type" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN rack_type VARCHAR")
        if "tray_type" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN tray_type VARCHAR")
        if "platform" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN platform VARCHAR")
        if "criticality" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN criticality VARCHAR")
        if "customer" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN customer VARCHAR")
        if "price_usd" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN price_usd FLOAT DEFAULT 0.0")
        if "conversion_rate" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN conversion_rate FLOAT DEFAULT 0.0")
        if "price_myr" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN price_myr FLOAT DEFAULT 0.0")
        if "lead_time_days" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN lead_time_days INTEGER DEFAULT 7")
        if "holding_cost_percentage" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN holding_cost_percentage FLOAT DEFAULT 0.2")
        if "annual_demand" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN annual_demand INTEGER DEFAULT 0")
        if "shipping_cost" not in mr_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry ADD COLUMN shipping_cost FLOAT DEFAULT 0.0")

    mrs_cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('machine_registry_serials')").fetchall()]
    if mrs_cols:
        if "outgoing_flag" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN outgoing_flag BOOLEAN DEFAULT 0")
        if "date_out" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN date_out DATETIME")
        if "customer" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN customer VARCHAR")
        if "engineer" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN engineer VARCHAR")
        if "remarks" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN remarks VARCHAR")
        if "ticket_no" not in mrs_cols:
            conn.exec_driver_sql("ALTER TABLE machine_registry_serials ADD COLUMN ticket_no VARCHAR")

app = FastAPI(title="Inventory Management System")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"Validation Error: {exc.errors()}")
    # exc.body might be FormData which is not JSON serializable
    # We should avoid including the raw body in the JSON response if it's FormData
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API Endpoints ---

@app.post("/token", response_model=schemas.Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = crud.get_user_by_email(db, email=form_data.username)
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not bool(getattr(user, "is_active", True)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    crud.log_activity(db, user_id=user.id, action="LOGIN", description="User logged in via API token endpoint")
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/api/users/", response_model=schemas.User)
def create_user_api(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    return crud.create_user(db=db, user=user)

@app.get("/api/parts/", response_model=list[schemas.SkuPart])
def read_parts_api(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: schemas.User = Depends(auth.get_current_user)):
    parts, total = crud.get_parts(db, skip=skip, limit=limit)
    return parts

@app.post("/api/parts/", response_model=schemas.SkuPart)
def create_part_api(part: schemas.SkuPartCreate, db: Session = Depends(get_db), current_user: schemas.User = Depends(auth.get_current_user)):
    return crud.create_part(db=db, part=part)

# --- Web UI Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    page: int = 1,
    parts_page: Optional[int] = None,
    machine_page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    stock_tab: Optional[str] = "parts",
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")
    
    if parts_page is None:
        parts_page = page
    parts_page = max(parts_page, 1)
    machine_page = max(machine_page, 1)

    parts_skip = (parts_page - 1) * limit
    machine_skip = (machine_page - 1) * limit

    parts, total_count = crud.get_parts_by_platform(db, is_machine=False, skip=parts_skip, limit=limit, search_query=search)
    machine_stock_items, machine_total_count = crud.get_machine_registry_entries(
        db, skip=machine_skip, limit=limit, search_query=search
    )
    machine_ids = [r.id for r in (machine_stock_items or []) if r and getattr(r, "id", None)]
    machine_serial_counts = crud.get_machine_registry_serial_counts(db, machine_ids)
    config = crud.get_config(db)
    machine_categories = crud.ensure_default_machine_categories(db)
    machine_registry_tabs = {
        "Machine": crud.get_machine_registry_by_type(db, "Machine", limit=20),
        "Storage": crud.get_machine_registry_by_type(db, "Storage", limit=20),
        "Tape Library": crud.get_machine_registry_by_type(db, "Tape Library", limit=20),
        "Networking": crud.get_machine_registry_by_type(db, "Networking", limit=20),
    }
    stock_tab = "machine" if stock_tab == "machine" else "parts"
    
    total_pages = max((total_count + limit - 1) // limit, 1)
    machine_total_pages = max((machine_total_count + limit - 1) // limit, 1)
    
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "parts": parts,
            "page": parts_page,
            "parts_page": parts_page,
            "machine_page": machine_page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "machine_stock_items": machine_stock_items,
            "machine_serial_counts": machine_serial_counts,
            "machine_total_pages": machine_total_pages,
            "machine_total_count": machine_total_count,
            "search": search,
            "stock_tab": stock_tab,
            "config": config,
            "machine_categories": machine_categories,
            "machine_registry_tabs": machine_registry_tabs,
            "success_msg": success_msg,
            "error_msg": error_msg,
        },
    )


@app.post("/machine-categories/add")
async def add_machine_category(
    request: Request,
    name: str = Form(None),
    description: str = Form(None),
    category_type: str = Form("Machine"),
    brand: str = Form(None),
    model_no: str = Form(None),
    product_type: str = Form(None),
    rack_level: str = Form(None),
    room_no: str = Form(None),
    tray_type: str = Form(None),
    platform: str = Form(None),
    criticality: str = Form(None),
    customer: str = Form(None),
    cpu_count: str = Form(None),
    core_count_speed: str = Form(None),
    memory_spec: str = Form(None),
    disk_drives: str = Form(None),
    nic_card: str = Form(None),
    adapter_card: str = Form(None),
    power_supply: str = Form(None),
    tape_drives: str = Form(None),
    network_ports: str = Form(None),
    notes: str = Form(None),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    crud.ensure_default_machine_categories(db)

    has_registry_payload = any(
        [
            brand,
            model_no,
            product_type,
            rack_level,
            room_no,
            tray_type,
            platform,
            cpu_count,
            core_count_speed,
            memory_spec,
            disk_drives,
            nic_card,
            adapter_card,
            power_supply,
            tape_drives,
            network_ports,
            notes,
            (image and getattr(image, "filename", None)),
        ]
    )

    if has_registry_payload:
        image_filename = None
        if image and getattr(image, "filename", None):
            suffix = Path(image.filename).suffix.lower()
            if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                suffix = ""
            uploads_dir = Path("app/static/machine_registry")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            safe_category = (category_type or "machine").lower().replace(" ", "_")
            image_filename = f"{safe_category}_{stamp}{suffix}"
            dest = uploads_dir / image_filename
            dest.write_bytes(await image.read())

        row = crud.create_machine_registry_entry(
            db=db,
            category_type=category_type,
            serial_number=None,
            brand=brand,
            model_no=model_no,
            product_type=product_type,
            rack_level=rack_level,
            room_no=room_no,
            tray_type=tray_type,
            platform=platform,
            criticality=criticality,
            customer=customer,
            cpu_count=cpu_count,
            core_count_speed=core_count_speed,
            memory_spec=memory_spec,
            disk_drives=disk_drives,
            nic_card=nic_card,
            adapter_card=adapter_card,
            power_supply=power_supply,
            tape_drives=tape_drives,
            network_ports=network_ports,
            notes=notes,
            image_filename=image_filename,
        )
        if not row:
            return RedirectResponse(url="/?stock_tab=machine&error_msg=Invalid category type", status_code=status.HTTP_302_FOUND)
        crud.log_activity(db, user_id=user.id, action="ADD_MACHINE_REGISTRY", part_id=None, description=f"Added {category_type} registry record")
        return RedirectResponse(url="/?stock_tab=machine&success_msg=Machine registry record added", status_code=status.HTTP_302_FOUND)

    if name:
        row = crud.create_machine_category(db, name=name, description=description)
        if row:
            crud.log_activity(db, user_id=user.id, action="ADD_MACHINE_CATEGORY", part_id=None, description=f"Added machine category {row.name}")
            return RedirectResponse(url="/?success_msg=Machine category added", status_code=status.HTTP_302_FOUND)

    return RedirectResponse(url="/?stock_tab=machine&error_msg=Please fill at least one registry field", status_code=status.HTTP_302_FOUND)


@app.post("/machine-registry/{entry_id}/update")
async def update_machine_registry(
    entry_id: int,
    request: Request,
    category_type: str = Form(...),
    brand: str = Form(None),
    model_no: str = Form(None),
    product_type: str = Form(None),
    rack_level: str = Form(None),
    room_no: str = Form(None),
    rack_type: str = Form(None),
    tray_type: str = Form(None),
    platform: str = Form(None),
    criticality: str = Form(None),
    customer: str = Form(None),
    price_usd: float | None = Form(None),
    conversion_rate: float | None = Form(None),
    price_myr: float | None = Form(None),
    lead_time_days: int = Form(None),
    holding_cost_percentage: float = Form(None),
    annual_demand: int = Form(None),
    shipping_cost: float = Form(None),
    cpu_count: str = Form(None),
    core_count_speed: str = Form(None),
    memory_spec: str = Form(None),
    disk_drives: str = Form(None),
    nic_card: str = Form(None),
    adapter_card: str = Form(None),
    power_supply: str = Form(None),
    tape_drives: str = Form(None),
    network_ports: str = Form(None),
    notes: str = Form(None),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return RedirectResponse(url="/?stock_tab=machine&error_msg=Record not found", status_code=status.HTTP_302_FOUND)

    image_filename = None
    if image and getattr(image, "filename", None):
        suffix = Path(image.filename).suffix.lower()
        if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
            suffix = ""
        uploads_dir = Path("app/static/machine_registry")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_category = (category_type or "machine").lower().replace(" ", "_")
        image_filename = f"{safe_category}_{stamp}{suffix}"
        dest = uploads_dir / image_filename
        dest.write_bytes(await image.read())

    prev_image = row.image_filename
    updated = crud.update_machine_registry_entry(
        db=db,
        entry_id=entry_id,
        category_type=category_type,
        brand=brand,
        model_no=model_no,
        product_type=product_type,
        rack_level=rack_level,
        room_no=room_no,
        rack_type=rack_type,
        tray_type=tray_type,
        platform=platform,
        criticality=criticality,
        customer=customer,
        price_usd=price_usd,
        conversion_rate=conversion_rate,
        price_myr=price_myr,
        lead_time_days=lead_time_days,
        holding_cost_percentage=holding_cost_percentage,
        annual_demand=annual_demand,
        shipping_cost=shipping_cost,
        cpu_count=cpu_count,
        core_count_speed=core_count_speed,
        memory_spec=memory_spec,
        disk_drives=disk_drives,
        nic_card=nic_card,
        adapter_card=adapter_card,
        power_supply=power_supply,
        tape_drives=tape_drives,
        network_ports=network_ports,
        notes=notes,
        image_filename=image_filename,
    )
    if updated:
        crud.log_activity(db, user_id=user.id, action="UPDATE_MACHINE_REGISTRY", part_id=None, description=f"Updated machine registry record {entry_id}")

    if image_filename and prev_image:
        try:
            (Path("app/static/machine_registry") / prev_image).unlink(missing_ok=True)
        except Exception:
            pass

    return RedirectResponse(url="/?stock_tab=machine&success_msg=Machine registry updated", status_code=status.HTTP_302_FOUND)


@app.post("/machine-registry/{entry_id}/serial")
async def update_machine_registry_serial(
    entry_id: int,
    request: Request,
    serial_number: str = Form(None),
    notes: str = Form(None),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return RedirectResponse(url="/?stock_tab=machine&error_msg=Record not found", status_code=status.HTTP_302_FOUND)

    image_filename = None
    if image and getattr(image, "filename", None):
        suffix = Path(image.filename).suffix.lower()
        if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
            suffix = ""
        uploads_dir = Path("app/static/machine_registry")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        image_filename = f"machine_{entry_id}_{stamp}{suffix}"
        dest = uploads_dir / image_filename
        dest.write_bytes(await image.read())

    prev_image = row.image_filename
    updated = crud.update_machine_registry_entry(
        db=db,
        entry_id=entry_id,
        serial_number=serial_number,
        notes=notes,
        image_filename=image_filename,
    )
    if not updated:
        return RedirectResponse(url="/?stock_tab=machine&error_msg=Record not found", status_code=status.HTTP_302_FOUND)

    if image_filename and prev_image:
        try:
            (Path("app/static/machine_registry") / prev_image).unlink(missing_ok=True)
        except Exception:
            pass

    crud.log_activity(db, user_id=user.id, action="UPDATE_MACHINE_SERIAL", part_id=None, description=f"Updated machine serial for record {entry_id}")
    return RedirectResponse(url="/?stock_tab=machine&success_msg=Machine part updated", status_code=status.HTTP_302_FOUND)


@app.get("/api/machine-registry/{entry_id}/serials")
async def api_machine_registry_serials(
    entry_id: int,
    request: Request,
    type: str = "available",
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"serials": []}, status_code=status.HTTP_401_UNAUTHORIZED)

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return JSONResponse({"serials": []}, status_code=status.HTTP_404_NOT_FOUND)

    if type == "outgoing":
        serials = crud.get_machine_registry_serials(db, registry_id=entry_id, outgoing_flag=True)
    else:
        serials = crud.get_machine_registry_serials(db, registry_id=entry_id, outgoing_flag=False)
    return JSONResponse(
        {
            "serials": [
                {
                    "id": s.id,
                    "serial_no": s.serial_no,
                    "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
                    "outgoing_flag": bool(s.outgoing_flag),
                    "date_out": s.date_out.strftime("%Y-%m-%d %H:%M:%S") if s.date_out else "",
                    "customer": s.customer or "",
                    "engineer": s.engineer or "",
                    "remarks": s.remarks or "",
                    "ticket_no": s.ticket_no or "",
                }
                for s in serials
            ],
            "count": len(serials),
        }
    )


@app.post("/api/machine-registry/{entry_id}/serials/add")
async def api_add_machine_registry_serial(
    entry_id: int,
    request: Request,
    serial_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return JSONResponse({"success": False, "error": "Record not found"}, status_code=status.HTTP_404_NOT_FOUND)

    created = crud.add_machine_registry_serial(db, registry_id=entry_id, serial_no=serial_no)
    if not created:
        return JSONResponse({"success": False, "error": "Invalid or duplicate serial"}, status_code=status.HTTP_400_BAD_REQUEST)

    serials = crud.get_machine_registry_serials(db, registry_id=entry_id, outgoing_flag=False)
    return JSONResponse({"success": True, "count": len(serials)})


@app.post("/api/machine-registry/{entry_id}/serials/outgoing")
async def api_machine_registry_serials_outgoing(
    entry_id: int,
    request: Request,
    serial_ids: str = Form(""),
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return JSONResponse({"success": False, "error": "Record not found"}, status_code=status.HTTP_404_NOT_FOUND)

    ids = [int(x) for x in serial_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return JSONResponse({"success": False, "error": "Please select at least one serial"}, status_code=status.HTTP_400_BAD_REQUEST)

    updated = crud.set_machine_registry_serials_outgoing(
        db=db,
        registry_id=entry_id,
        serial_ids=ids,
        outgoing_flag=True,
        customer=client_name,
        engineer=engineer_name,
        remarks=remark,
        ticket_no=ticket_no,
        date_out=datetime.utcnow(),
    )
    if updated:
        crud.log_activity(db, user_id=user.id, action="MACHINE_OUTGOING", part_id=None, description=f"Outgoing {row.category_type} {row.model_no or ''} | qty={updated}")

    available = crud.get_machine_registry_serials(db, registry_id=entry_id, outgoing_flag=False)
    return JSONResponse({"success": True, "updated": updated, "available_count": len(available)})


@app.post("/api/machine-registry/{entry_id}/serials/returning")
async def api_machine_registry_serials_returning(
    entry_id: int,
    request: Request,
    serial_ids: str = Form(""),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return JSONResponse({"success": False, "error": "Record not found"}, status_code=status.HTTP_404_NOT_FOUND)

    ids = [int(x) for x in serial_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return JSONResponse({"success": False, "error": "Please select at least one serial"}, status_code=status.HTTP_400_BAD_REQUEST)

    updated = crud.set_machine_registry_serials_outgoing(
        db=db,
        registry_id=entry_id,
        serial_ids=ids,
        outgoing_flag=False,
        remarks=remark,
        ticket_no=ticket_no,
    )
    if updated:
        crud.log_activity(db, user_id=user.id, action="MACHINE_RETURNING", part_id=None, description=f"Returning {row.category_type} {row.model_no or ''} | qty={updated}")

    available = crud.get_machine_registry_serials(db, registry_id=entry_id, outgoing_flag=False)
    return JSONResponse({"success": True, "updated": updated, "available_count": len(available)})


@app.post("/api/machine-registry/serials/{serial_id}/delete")
async def api_delete_machine_registry_serial(
    serial_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    deleted = crud.delete_machine_registry_serial(db, serial_id=serial_id)
    if not deleted:
        return JSONResponse({"success": False, "error": "Serial not found"}, status_code=status.HTTP_404_NOT_FOUND)

    available = crud.get_machine_registry_serials(db, registry_id=deleted.registry_id, outgoing_flag=False)
    return JSONResponse({"success": True, "count": len(available), "registry_id": deleted.registry_id})


@app.post("/machine-registry/{entry_id}/delete")
async def delete_machine_registry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    row = crud.delete_machine_registry_entry(db, entry_id)
    if not row:
        return RedirectResponse(url="/?stock_tab=machine&error_msg=Record not found", status_code=status.HTTP_302_FOUND)

    if row.image_filename:
        try:
            (Path("app/static/machine_registry") / row.image_filename).unlink(missing_ok=True)
        except Exception:
            pass

    crud.log_activity(db, user_id=user.id, action="DELETE_MACHINE_REGISTRY", part_id=None, description=f"Deleted machine registry record {entry_id}")
    return RedirectResponse(url="/?stock_tab=machine&success_msg=Machine registry deleted", status_code=status.HTTP_302_FOUND)


@app.get("/activity-log", response_class=HTMLResponse)
async def activity_log(
    request: Request,
    page: int = 1,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    skip = (page - 1) * limit
    logs, total_count = crud.get_activity_logs(db, skip=skip, limit=limit)
    total_pages = (total_count + limit - 1) // limit

    return templates.TemplateResponse(
        request,
        "activity_log.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
        },
    )


@app.get("/movements", response_class=HTMLResponse)
async def movements_report(
    request: Request,
    page: int = 1,
    limit: int = 20,
    type: str | None = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    if type == "returning":
        actions = ["RETURNING_PART"]
        include_prefix = None
        exclude_actions = None
        report_title = "Returning Log"
        report_type = "returning"
    elif type == "outgoing":
        actions = ["OUTGOING_PART"]
        include_prefix = None
        exclude_actions = None
        report_title = "Outgoing Log"
        report_type = "outgoing"
    else:
        actions = None
        include_prefix = None
        exclude_actions = None
        report_title = "Returning & Outgoing Report"
        report_type = None

    skip = (page - 1) * limit
    logs, total_count = crud.get_movement_logs(
        db,
        skip=skip,
        limit=limit,
        actions=actions,
        include_prefix=include_prefix,
        exclude_actions=exclude_actions,
    )
    total_pages = (total_count + limit - 1) // limit

    movements = []
    for log in logs:
        created_at = log.created_at
        created_at_str = created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else ""
        user_label = ""
        if log.user:
            user_label = log.user.full_name or log.user.email or ""
        part_no = log.part.sku if log.part else ""
        part_name = log.part.name if log.part else ""

        quantity = ""
        client_name = ""
        engineer_name = ""
        part_status = ""
        remark = ""
        serials = ""

        if log.description:
            segments = [s.strip() for s in log.description.split(";")]
            for seg in segments:
                if seg.lower().startswith("quantity:"):
                    quantity = seg.split(":", 1)[1].strip()
                elif seg.lower().startswith("client:"):
                    client_name = seg.split(":", 1)[1].strip()
                elif seg.lower().startswith("engineer:"):
                    engineer_name = seg.split(":", 1)[1].strip()
                elif seg.lower().startswith("status:"):
                    part_status = seg.split(":", 1)[1].strip()
                elif seg.lower().startswith("serial no:"):
                    serials = seg.split(":", 1)[1].strip()
                elif seg.lower().startswith("remark:"):
                    remark = seg.split(":", 1)[1].strip()

        movements.append(
            {
                "created_at": created_at_str,
                "action": log.action,
                "user": user_label,
                "part_no": part_no,
                "part_name": part_name,
                "quantity": quantity,
                "client_name": client_name,
                "engineer_name": engineer_name,
                "part_status": part_status,
                "remark": remark,
                "serials": serials,
            }
        )

    return templates.TemplateResponse(
        request,
        "movements.html",
        {
            "request": request,
            "user": user,
            "movements": movements,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "report_title": report_title,
            "report_type": report_type,
        },
    )


@app.get("/reports/rack-inventory", response_class=HTMLResponse)
async def rack_inventory_report(
    request: Request,
    group_by: str = Query("rack"),
    rack_page: int = Query(1),
    tray_page: int = Query(1),
    all_page: int = Query(1),
    limit: int = Query(50),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    active_tab = "all" if group_by == "all" else ("tray" if group_by == "tray" else "rack")

    rack_page = max(int(rack_page or 1), 1)
    tray_page = max(int(tray_page or 1), 1)
    all_page = max(int(all_page or 1), 1)
    limit = max(min(int(limit or 50), 200), 1)

    rack_skip = (rack_page - 1) * limit
    tray_skip = (tray_page - 1) * limit
    all_skip = (all_page - 1) * limit

    rack_items, rack_total_count = crud.get_rack_inventory_items(db, skip=rack_skip, limit=limit)
    tray_items, tray_total_count = crud.get_tray_inventory_items(db, skip=tray_skip, limit=limit)
    all_items, all_total_count = crud.get_all_inventory_items(db, skip=all_skip, limit=limit)

    rack_total_pages = max((int(rack_total_count) + limit - 1) // limit, 1)
    tray_total_pages = max((int(tray_total_count) + limit - 1) // limit, 1)
    all_total_pages = max((int(all_total_count) + limit - 1) // limit, 1)

    if rack_total_pages > 0 and rack_page > rack_total_pages:
        rack_page = rack_total_pages
        rack_skip = (rack_page - 1) * limit
        rack_items, rack_total_count = crud.get_rack_inventory_items(db, skip=rack_skip, limit=limit)

    if tray_total_pages > 0 and tray_page > tray_total_pages:
        tray_page = tray_total_pages
        tray_skip = (tray_page - 1) * limit
        tray_items, tray_total_count = crud.get_tray_inventory_items(db, skip=tray_skip, limit=limit)

    if all_total_pages > 0 and all_page > all_total_pages:
        all_page = all_total_pages
        all_skip = (all_page - 1) * limit
        all_items, all_total_count = crud.get_all_inventory_items(db, skip=all_skip, limit=limit)

    return templates.TemplateResponse(
        request,
        "rack_inventory.html",
        {
            "request": request,
            "user": user,
            "active_tab": active_tab,
            "rack_items": rack_items,
            "tray_items": tray_items,
            "all_items": all_items,
            "rack_page": rack_page,
            "tray_page": tray_page,
            "all_page": all_page,
            "limit": limit,
            "rack_total_pages": rack_total_pages,
            "tray_total_pages": tray_total_pages,
            "all_total_pages": all_total_pages,
            "rack_total_count": rack_total_count,
            "tray_total_count": tray_total_count,
            "all_total_count": all_total_count,
            "rack_offset": rack_skip,
            "tray_offset": tray_skip,
            "all_offset": all_skip,
        },
    )


@app.get("/export/reports/rack-inventory.xlsx")
async def export_rack_inventory_excel(
    request: Request,
    group_by: str = Query("rack"),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    active_tab = "all" if group_by == "all" else ("tray" if group_by == "tray" else "rack")
    export_limit = 1_000_000

    if active_tab == "tray":
        items, _ = crud.get_tray_inventory_items(db, skip=0, limit=export_limit)
        rows = [
            {
                "Tray Type": row.tray_type or "-",
                "Part No": row.part_no or "-",
                "Part Description": row.part_description or "-",
                "Category": row.part_category or "-",
                "Serial No": row.serial_no or "-",
                "Quantity": int(row.quantity or 0),
                "Unit Price (USD)": float(row.unit_price_usd or 0) if row.unit_price_usd is not None else "",
                "Unit Price (MYR)": float(row.unit_price_myr or 0) if row.unit_price_myr is not None else "",
            }
            for row in items
        ]
        return _excel_stream_response(rows, "rack_inventory_tray.xlsx", "TrayInventory")

    if active_tab == "all":
        items, _ = crud.get_all_inventory_items(db, skip=0, limit=export_limit)
        rows = [
            {
                "Rack Type": row.rack_type or "-",
                "Tray Type": row.tray_type or "-",
                "Part No": row.part_no or "-",
                "Part Description": row.part_description or "-",
                "Category": row.part_category or "-",
                "Serial No": row.serial_no or "-",
                "Quantity": int(row.quantity or 0),
                "Unit Price (USD)": float(row.unit_price_usd or 0) if row.unit_price_usd is not None else "",
                "Unit Price (MYR)": float(row.unit_price_myr or 0) if row.unit_price_myr is not None else "",
            }
            for row in items
        ]
        return _excel_stream_response(rows, "rack_inventory_all.xlsx", "AllInventory")

    items, _ = crud.get_rack_inventory_items(db, skip=0, limit=export_limit)
    rows = [
        {
            "Rack Type": row.rack_type or "-",
            "Part No": row.part_no or "-",
            "Part Description": row.part_description or "-",
            "Category": row.part_category or "-",
            "Serial No": row.serial_no or "-",
            "Quantity": int(row.quantity or 0),
            "Unit Price (USD)": float(row.unit_price_usd or 0) if row.unit_price_usd is not None else "",
            "Unit Price (MYR)": float(row.unit_price_myr or 0) if row.unit_price_myr is not None else "",
        }
        for row in items
    ]
    return _excel_stream_response(rows, "rack_inventory_rack.xlsx", "RackInventory")


@app.post("/client-faulty-log/add")
async def add_client_faulty_log(
    request: Request,
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    part_no: str = Form(None),
    part_description: str = Form(None),
    serial_no: str = Form(None),
    part_status: str = Form("Faulty"),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    crud.create_client_faulty_log(
        db=db,
        user_id=user.id,
        client_name=client_name,
        engineer_name=engineer_name,
        part_no=part_no,
        part_description=part_description,
        serial_no=serial_no,
        part_status=part_status,
        remark=remark,
        ticket_no=ticket_no,
    )
    return RedirectResponse(url="/?success_msg=Client returning log recorded", status_code=status.HTTP_302_FOUND)


@app.get("/reports/client-faulty-log", response_class=HTMLResponse)
async def client_faulty_log_report(
    request: Request,
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    success_msg: str | None = None,
    error_msg: str | None = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    page = max(page, 1)
    limit = max(min(limit, 200), 1)
    skip = (page - 1) * limit

    rows, total_count = crud.get_client_faulty_logs(db, skip=skip, limit=limit, search_query=search, disposed=False)
    total_pages = max((total_count + limit - 1) // limit, 1)

    return templates.TemplateResponse(
        request,
        "client_faulty_log.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "search": search,
            "mode": "client_faulty",
            "success_msg": success_msg,
            "error_msg": error_msg,
        },
    )


@app.post("/reports/client-faulty-log/dispose")
async def dispose_client_faulty_logs(
    request: Request,
    dispose_ids: list[int] = Form([]),
    page: int = Form(1),
    limit: int = Form(20),
    search: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    ids = []
    for x in dispose_ids or []:
        try:
            v = int(x)
            if v > 0:
                ids.append(v)
        except Exception:
            continue

    if not ids:
        return RedirectResponse(
            url=f"/reports/client-faulty-log?page={max(int(page or 1),1)}&limit={max(min(int(limit or 20),200),1)}"
            + (f"&search={quote(search)}" if (search or "").strip() else "")
            + "&error_msg=Please select at least one item",
            status_code=status.HTTP_302_FOUND,
        )

    now = datetime.utcnow()
    updated = (
        db.query(models.ClientFaultyLog)
        .filter(models.ClientFaultyLog.id.in_(ids))
        .update({"disposed_flag": True, "disposed_at": now}, synchronize_session=False)
    )
    db.commit()
    crud.log_activity(db, user_id=user.id, action="DISPOSE_CLIENT_FAULTY_LOG", part_id=None, description=f"Disposed {int(updated or 0)} client returning log(s)")

    return RedirectResponse(
        url="/reports/disposal-report?success_msg=Disposed successfully",
        status_code=status.HTTP_302_FOUND,
    )


@app.get("/reports/disposal-report", response_class=HTMLResponse)
async def disposal_report(
    request: Request,
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    success_msg: str | None = None,
    error_msg: str | None = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    page = max(page, 1)
    limit = max(min(limit, 200), 1)
    skip = (page - 1) * limit

    rows, total_count = crud.get_client_faulty_logs(db, skip=skip, limit=limit, search_query=search, disposed=True)
    total_pages = max((total_count + limit - 1) // limit, 1)

    return templates.TemplateResponse(
        request,
        "client_faulty_log.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "search": search,
            "mode": "disposal",
            "success_msg": success_msg,
            "error_msg": error_msg,
        },
    )


@app.get("/export/reports/client-faulty-log.xlsx")
async def export_client_faulty_log_excel(
    request: Request,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    rows, _ = crud.get_client_faulty_logs(db, skip=0, limit=1_000_000, search_query=search, disposed=False)
    data = []
    for row in rows:
        user_label = row.user.full_name or row.user.email or "" if row.user else ""
        data.append(
            {
                "Date & Time": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
                "Client Name": row.client_name or "",
                "Engineer Name": row.engineer_name or "",
                "Part No": row.part_no or "",
                "Part Description": row.part_description or "",
                "Serial No": row.serial_no or "",
                "Part Status": row.part_status or "",
                "Remark": row.remark or "",
                "Ticket No": row.ticket_no or "",
                "User": user_label,
            }
        )
    return _excel_stream_response(data, "client_faulty_log.xlsx", "ClientFaultyLog")


@app.get("/export/reports/disposal-report.xlsx")
async def export_disposal_report_excel(
    request: Request,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    rows, _ = crud.get_client_faulty_logs(db, skip=0, limit=1_000_000, search_query=search, disposed=True)
    data = []
    for row in rows:
        user_label = row.user.full_name or row.user.email or "" if row.user else ""
        data.append(
            {
                "Date & Time": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
                "Client Name": row.client_name or "",
                "Engineer Name": row.engineer_name or "",
                "Part No": row.part_no or "",
                "Part Description": row.part_description or "",
                "Serial No": row.serial_no or "",
                "Part Status": row.part_status or "",
                "Remark": row.remark or "",
                "Ticket No": row.ticket_no or "",
                "Disposal Timestamp": row.disposed_at.strftime("%Y-%m-%d %H:%M:%S") if row.disposed_at else "",
                "User": user_label,
            }
        )
    return _excel_stream_response(data, "disposal_report.xlsx", "DisposalReport")


async def _render_settings_page(
    request: Request,
    db: Session,
    settings_section: str,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    section = (settings_section or "").strip().lower()
    if section not in ["email", "users"]:
        section = "email"

    if section == "users" and not bool(getattr(user, "is_admin", False)):
        return RedirectResponse(url="/?error_msg=" + quote("Unauthorized"), status_code=status.HTTP_302_FOUND)

    config = crud.get_config(db)
    users = crud.list_users(db) if section == "users" else None

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "user": user,
            "config": config,
            "users": users,
            "settings_section": section,
            "success_msg": success_msg,
            "error_msg": error_msg,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: Session = Depends(get_db),
):
    return RedirectResponse(url="/settings/email", status_code=status.HTTP_302_FOUND)


@app.get("/settings/email", response_class=HTMLResponse)
async def settings_email_page(
    request: Request,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return await _render_settings_page(request, db, "email", success_msg=success_msg, error_msg=error_msg)


@app.get("/settings/users", response_class=HTMLResponse)
async def settings_users_page(
    request: Request,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return await _render_settings_page(request, db, "users", success_msg=success_msg, error_msg=error_msg)


@app.post("/admin/users/create")
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(None),
    password: str = Form(...),
    role: str = Form("user"),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")
    if not bool(getattr(user, "is_admin", False)):
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Unauthorized"), status_code=status.HTTP_302_FOUND)

    e = (email or "").strip().lower()
    if not e:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Email is required"), status_code=status.HTTP_302_FOUND)
    if crud.get_user_by_email(db, e):
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Email already exists"), status_code=status.HTTP_302_FOUND)

    pw = (password or "").strip()
    if len(pw) < 6:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Password must be at least 6 characters"), status_code=status.HTTP_302_FOUND)

    is_admin = (role or "").strip().lower() == "admin"
    active = str(is_active or "").strip() in ["1", "true", "yes", "on"]

    user_in = schemas.UserCreate(
        email=e,
        password=pw,
        full_name=(full_name or "").strip() or None,
        is_active=active,
        is_admin=is_admin,
    )
    crud.create_user(db, user_in)
    crud.log_activity(db, user_id=user.id, action="ADMIN_CREATE_USER", description=f"Created user {e}")
    return RedirectResponse(url="/settings/users?success_msg=" + quote("User created"), status_code=status.HTTP_302_FOUND)


@app.post("/admin/users/{user_id}/update")
async def admin_update_user(
    user_id: int,
    request: Request,
    full_name: str = Form(None),
    role: str = Form("user"),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")
    if not bool(getattr(user, "is_admin", False)):
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Unauthorized"), status_code=status.HTTP_302_FOUND)

    target = crud.get_user(db, user_id)
    if not target:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("User not found"), status_code=status.HTTP_302_FOUND)

    new_is_admin = (role or "").strip().lower() == "admin"
    new_is_active = str(is_active or "").strip() in ["1", "true", "yes", "on"]
    if int(user_id) == int(user.id):
        if not new_is_active:
            return RedirectResponse(url="/settings/users?error_msg=" + quote("You cannot disable your own account"), status_code=status.HTTP_302_FOUND)
        if not new_is_admin:
            return RedirectResponse(url="/settings/users?error_msg=" + quote("You cannot remove your own admin role"), status_code=status.HTTP_302_FOUND)

    updated = crud.update_user_admin_fields(
        db,
        user_id=user_id,
        full_name=full_name,
        is_active=new_is_active,
        is_admin=new_is_admin,
    )
    if not updated:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("User not found"), status_code=status.HTTP_302_FOUND)
    crud.log_activity(db, user_id=user.id, action="ADMIN_UPDATE_USER", description=f"Updated user {updated.email}")
    return RedirectResponse(url="/settings/users?success_msg=" + quote("User updated"), status_code=status.HTTP_302_FOUND)


@app.post("/admin/users/{user_id}/reset-password")
async def admin_reset_user_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")
    if not bool(getattr(user, "is_admin", False)):
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Unauthorized"), status_code=status.HTTP_302_FOUND)

    pw = (new_password or "").strip()
    if len(pw) < 6:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("Password must be at least 6 characters"), status_code=status.HTTP_302_FOUND)

    updated = crud.set_user_password(db, user_id=user_id, new_password=pw)
    if not updated:
        return RedirectResponse(url="/settings/users?error_msg=" + quote("User not found"), status_code=status.HTTP_302_FOUND)
    crud.log_activity(db, user_id=user.id, action="ADMIN_RESET_PASSWORD", description=f"Reset password for user {updated.email}")
    return RedirectResponse(url="/settings/users?success_msg=" + quote("Password reset"), status_code=status.HTTP_302_FOUND)


@app.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    email_sender: Optional[str] = Form(None),
    smtp_server: Optional[str] = Form(None),
    smtp_port: int = Form(587),
    smtp_use_tls: str = Form("true"),
    smtp_username: Optional[str] = Form(None),
    smtp_password: Optional[str] = Form(None),
    notification_email: Optional[str] = Form(None),
    default_conversion_rate: float = Form(0.0),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    use_tls = True if smtp_use_tls.lower() == "true" else False

    PLACEHOLDER = "********"

    data = {
        "email_sender": email_sender,
        "smtp_server": smtp_server,
        "smtp_port": smtp_port,
        "smtp_use_tls": use_tls,
        "smtp_username": smtp_username,
        "notification_email": notification_email,
        "default_conversion_rate": default_conversion_rate,
    }

    if smtp_password and smtp_password != PLACEHOLDER:
        data["smtp_password"] = smtp_password

    config_in = schemas.ConfigUpdate(**data)

    crud.update_config(db, config_in)

    return RedirectResponse(url="/settings/email?success_msg=Settings+saved+successfully", status_code=status.HTTP_302_FOUND)


@app.post("/settings/test-email", response_class=HTMLResponse)
async def settings_test_email(
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    cfg = crud.get_config(db)

    subject = "Inventory System Test Email"
    body = "This is a test email from the Inventory Management System."

    success, error = crud.send_email_notification(cfg, subject, body)

    if success:
        msg = "Test email sent successfully. Please check your inbox."
        url = f"/settings/email?success_msg={quote(msg)}"
    else:
        base = "Test email failed."
        if error:
            msg = f"{base} Error: {error}"
        else:
            msg = base + " Please check SMTP settings and app password."
        url = f"/settings/email?error_msg={quote(msg)}"

    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


EXPORT_COLUMN_DEFS = {
    "part_no": ("Part No", lambda part, item: part.sku),
    "part_description": ("Part Description", lambda part, item: part.name),
    "criticality": ("Critical", lambda part, item: part.criticality),
    "vendor": ("Vendor", lambda part, item: item.vendor if item else ""),
    "serial_no": ("Serial No", lambda part, item: item.serial_no if item else ""),
    "unit_cost": ("Unit Cost", lambda part, item: item.unit_cost if item else ""),
    "price_usd": ("Price (USD)", lambda part, item: item.price_usd if item else ""),
    "conversion_rate": ("Conversion Rate", lambda part, item: item.conversion_rate if item else ""),
    "price_myr": ("Price (MYR)", lambda part, item: item.price_myr if item else ""),
    "lead_time_days": ("Lead Time (Days)", lambda part, item: item.lead_time_days if item else ""),
    "stock_in_date": (
        "Stock In Date",
        lambda part, item: item.created_at.strftime("%Y-%m-%d")
        if item and getattr(item, "created_at", None)
        else "",
    ),
}


@app.get("/export/parts.csv")
async def export_parts_csv(
    search: Optional[str] = None,
    columns: Optional[str] = None,
    db: Session = Depends(get_db),
):
    parts, total_count = crud.get_parts(db, skip=0, limit=1_000_000, search_query=search)

    selected_keys = [c.strip() for c in columns.split(",")] if columns else []

    # This line is the problematic one
    headers, rows = _build_export_rows(parts, selected_keys)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    content = output.getvalue()

    output.close()

    headers_resp = {
        "Content-Disposition": 'attachment; filename="parts.csv"'
    }
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers=headers_resp,
    )


@app.get("/export/parts.xlsx")
async def export_parts_excel(
    search: Optional[str] = None,
    columns: Optional[str] = None,
    db: Session = Depends(get_db),
):
    parts, total_count = crud.get_parts(db, skip=0, limit=1_000_000, search_query=search)

    selected_keys = [c.strip() for c in columns.split(",")] if columns else []
    headers, rows = _build_export_rows(parts, selected_keys)

    df = pd.DataFrame(rows, columns=headers)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Parts")

    output.seek(0)
    headers_resp = {
        "Content-Disposition": 'attachment; filename="parts.xlsx"'
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_resp,
    )

def _build_export_rows(parts: list[models.SkuPart], selected_keys: list[str]):
    keys = selected_keys or list(EXPORT_COLUMN_DEFS.keys())
    keys = [k for k in keys if k in EXPORT_COLUMN_DEFS]

    if not keys:
        keys = list(EXPORT_COLUMN_DEFS.keys())

    rows = []
    headers = [EXPORT_COLUMN_DEFS[k][0] for k in keys]

    for part in parts:
        part_excel_item = part.items or [None]
        part_test = part.name
                
        for item in part_excel_item:
            row = []
            for key in keys:
                _, fn = EXPORT_COLUMN_DEFS[key]
                row.append(fn(part, item))
            rows.append(row)
    
    return headers, rows


@app.get("/export/machine-registry.csv")
async def export_machine_registry_csv(
    request: Request,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    rows, total = crud.get_machine_registry_entries(db, skip=0, limit=1_000_000, search_query=search)
    registry_ids = [int(r.id) for r in rows if getattr(r, "id", None) is not None]
    serials_by_registry: dict[int, list[str]] = {}
    if registry_ids:
        serial_rows = (
            db.query(models.MachineRegistrySerial.registry_id, models.MachineRegistrySerial.serial_no)
            .filter(
                models.MachineRegistrySerial.registry_id.in_(registry_ids),
                or_(models.MachineRegistrySerial.outgoing_flag.is_(False), models.MachineRegistrySerial.outgoing_flag.is_(None)),
            )
            .order_by(models.MachineRegistrySerial.registry_id.asc(), models.MachineRegistrySerial.serial_no.asc())
            .all()
        )
        for rid, sn in serial_rows:
            try:
                rid_int = int(rid)
            except Exception:
                continue
            serials_by_registry.setdefault(rid_int, []).append(str(sn))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Date Time",
            "Type",
            "Serial Number",
            "Brand",
            "Model",
            "Product Type",
            "CPU",
            "Core Count & Speed",
            "Memory",
            "Disk Drives",
            "NIC Card",
            "Adapter Card",
            "Power Supply",
            "Tape Drives",
            "Network Ports",
            "Notes",
            "Image",
        ]
    )

    for r in rows:
        created_at = r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
        image_url = f"/static/machine_registry/{r.image_filename}" if r.image_filename else ""
        serials = ", ".join(serials_by_registry.get(int(r.id), [])) if getattr(r, "id", None) is not None else ""
        if not serials:
            serials = r.serial_number or ""
        writer.writerow(
            [
                created_at,
                r.category_type or "",
                serials,
                r.brand or "",
                r.model_no or "",
                r.product_type or "",
                r.cpu_count or "",
                r.core_count_speed or "",
                r.memory_spec or "",
                r.disk_drives or "",
                r.nic_card or "",
                r.adapter_card or "",
                r.power_supply or "",
                r.tape_drives or "",
                r.network_ports or "",
                r.notes or "",
                image_url,
            ]
        )

    content = output.getvalue()
    output.close()
    headers_resp = {"Content-Disposition": 'attachment; filename="machine_registry.csv"'}
    return StreamingResponse(iter([content]), media_type="text/csv", headers=headers_resp)


@app.get("/export/machine-registry.xlsx")
async def export_machine_registry_excel(
    request: Request,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    rows, total = crud.get_machine_registry_entries(db, skip=0, limit=1_000_000, search_query=search)
    registry_ids = [int(r.id) for r in rows if getattr(r, "id", None) is not None]
    serials_by_registry: dict[int, list[str]] = {}
    if registry_ids:
        serial_rows = (
            db.query(models.MachineRegistrySerial.registry_id, models.MachineRegistrySerial.serial_no)
            .filter(
                models.MachineRegistrySerial.registry_id.in_(registry_ids),
                or_(models.MachineRegistrySerial.outgoing_flag.is_(False), models.MachineRegistrySerial.outgoing_flag.is_(None)),
            )
            .order_by(models.MachineRegistrySerial.registry_id.asc(), models.MachineRegistrySerial.serial_no.asc())
            .all()
        )
        for rid, sn in serial_rows:
            try:
                rid_int = int(rid)
            except Exception:
                continue
            serials_by_registry.setdefault(rid_int, []).append(str(sn))
    data = []
    for r in rows:
        created_at = r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
        image_url = f"/static/machine_registry/{r.image_filename}" if r.image_filename else ""
        serials = ", ".join(serials_by_registry.get(int(r.id), [])) if getattr(r, "id", None) is not None else ""
        if not serials:
            serials = r.serial_number or ""
        data.append(
            {
                "Date Time": created_at,
                "Type": r.category_type or "",
                "Serial Number": serials,
                "Brand": r.brand or "",
                "Model": r.model_no or "",
                "Product Type": r.product_type or "",
                "CPU": r.cpu_count or "",
                "Core Count & Speed": r.core_count_speed or "",
                "Memory": r.memory_spec or "",
                "Disk Drives": r.disk_drives or "",
                "NIC Card": r.nic_card or "",
                "Adapter Card": r.adapter_card or "",
                "Power Supply": r.power_supply or "",
                "Tape Drives": r.tape_drives or "",
                "Network Ports": r.network_ports or "",
                "Notes": r.notes or "",
                "Image": image_url,
            }
        )

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="MachineRegistry")

    output.seek(0)
    headers_resp = {"Content-Disposition": 'attachment; filename="machine_registry.xlsx"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_resp,
    )


@app.get("/export/activity-log.csv")
async def export_activity_log_csv(
    db: Session = Depends(get_db),
):
    logs, total_count = crud.get_activity_logs(db, skip=0, limit=1_000_000)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Date Time",
            "User",
            "Action",
            "Part No",
            "Description",
        ]
    )

    for log in logs:
        user_label = ""
        if log.user:
            user_label = log.user.full_name or log.user.email or ""
        part_no = log.part.sku if log.part else ""
        created_at = log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""

        writer.writerow(
            [
                created_at,
                user_label,
                log.action,
                part_no,
                log.description or "",
            ]
        )

    content = output.getvalue()
    output.close()

    headers = {
        "Content-Disposition": 'attachment; filename="activity_log.csv"'
    }
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers=headers,
    )


@app.get("/export/activity-log.xlsx")
async def export_activity_log_excel(
    db: Session = Depends(get_db),
):
    logs, total_count = crud.get_activity_logs(db, skip=0, limit=1_000_000)

    rows = []
    for log in logs:
        user_label = ""
        if log.user:
            user_label = log.user.full_name or log.user.email or ""
        part_no = log.part.sku if log.part else ""
        created_at = log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""

        rows.append(
            {
                "Date Time": created_at,
                "User": user_label,
                "Action": log.action,
                "Part No": part_no,
                "Description": log.description or "",
            }
        )

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="ActivityLog")

    output.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="activity_log.xlsx"'
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session_expired: bool = False, db: Session = Depends(get_db)):
    can_register = crud.count_users(db) == 0
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "session_expired": session_expired, "can_register": can_register},
    )

@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = crud.get_user_by_email(db, email=email)
    if not user or not auth.verify_password(password, user.hashed_password):
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Invalid credentials"})
    if not bool(getattr(user, "is_active", True)):
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "User disabled"})
    
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    crud.log_activity(db, user_id=user.id, action="LOGIN", description="User logged in via web form")
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request})

@app.post("/register")
async def register_submit(request: Request, email: str = Form(...), password: str = Form(...), full_name: str = Form(...), db: Session = Depends(get_db)):
    e = (email or "").strip().lower()
    user = crud.get_user_by_email(db, email=e)
    if user:
        return templates.TemplateResponse(request, "register.html", {"request": request, "error": "Email already registered"})
    
    is_first_user = crud.count_users(db) == 0
    user_in = schemas.UserCreate(email=e, password=password, full_name=full_name, is_admin=is_first_user)
    crud.create_user(db, user_in)
    
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

@app.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    user = await auth.get_current_user_from_cookie(request, db)
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    if user:
        crud.log_activity(db, user_id=user.id, action="LOGOUT", description="User logged out")
    return response

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", {"request": request})

@app.post("/reset-password")
async def reset_password_submit(request: Request, email: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db)):
    current = await auth.get_current_user_from_cookie(request, db)
    if not current:
        return RedirectResponse(url="/login?session_expired=true")
    if not bool(getattr(current, "is_admin", False)):
        return RedirectResponse(url="/?error_msg=" + quote("Unauthorized"), status_code=status.HTTP_302_FOUND)

    user = crud.get_user_by_email(db, email=(email or "").strip().lower())
    if not user:
        return templates.TemplateResponse(request, "reset_password.html", {"request": request, "error": "Email not found"})

    pw = (new_password or "").strip()
    if len(pw) < 6:
        return templates.TemplateResponse(request, "reset_password.html", {"request": request, "error": "Password must be at least 6 characters"})

    crud.set_user_password(db, user_id=user.id, new_password=pw)
    crud.log_activity(db, user_id=current.id, action="ADMIN_RESET_PASSWORD", description=f"Reset password via reset-password page for user {user.email}")
    return templates.TemplateResponse(request, "login.html", {"request": request, "success": "Password reset successfully. Please login."})

@app.post("/parts/add")
async def add_part_ui(
    request: Request,
    sku: str = Form(...),
    serial_no: str = Form(None),
    name: str = Form(...),
    description: str = Form(None),
    part_category: str = Form(None),
    rack_type: str = Form(None),
    tray_type: str = Form(None),
    platform: str = Form(None),
    lead_time_days: int = Form(7),
    unit_cost: float = Form(0.0),
    holding_cost_percentage: float = Form(0.20),
    annual_demand: int = Form(0),
    eoq_min_threshold: int = Form(0),
    criticality: str = Form("Medium"),
    price_usd: float = Form(0.0),
    price_myr: float = Form(0.0),
    conversion_rate: float = Form(0.0),
    vendor: str = Form(None),
    customer_usage: str = Form(None),
    image: UploadFile = File(None),
    machine_category_id: str = Form(None),
    clone_from_existing: str = Form("0"),
    clone_source_part_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    is_clone = clone_from_existing == "1"

    if is_clone:
        if not clone_source_part_id:
            msg = quote("Missing source part for item creation.")
            return RedirectResponse(
                url=f"/?error_msg={msg}",
                status_code=status.HTTP_302_FOUND,
            )

        if serial_no:
            existing_serial = (
                db.query(models.PartItem)
                .filter(models.PartItem.serial_no == serial_no)
                .first()
            )
            if existing_serial:
                msg = quote("Serial No already exists. Please use a unique Serial No.")
                return RedirectResponse(
                    url=f"/?error_msg={msg}",
                    status_code=status.HTTP_302_FOUND,
                )

        item_in = schemas.PartItemCreate(
            sku_id=clone_source_part_id,
            serial_no=serial_no,
            description=description,
            outgoing_flag=False,
            returning_flag=False,
            price_usd=price_usd,
            price_myr=price_myr,
            conversion_rate=conversion_rate,
            vendor=vendor,
        )
        item = crud.create_part_item(db, item_in)

        if part_category is not None:
            part_to_update = db.query(models.SkuPart).filter(models.SkuPart.id == clone_source_part_id).first()
            if part_to_update:
                val = (part_category or "").strip() or None
                part_to_update.part_category = val
                db.commit()

        if machine_category_id and machine_category_id.isdigit():
            crud.set_part_machine_category(db, part_id=clone_source_part_id, machine_category_id=int(machine_category_id))

        if customer_usage or image:
            image_filename = None
            if image and getattr(image, "filename", None):
                suffix = Path(image.filename).suffix.lower()
                if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                    suffix = ""
                uploads_dir = Path("app/static/part_images")
                uploads_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                image_filename = f"part_{clone_source_part_id}_{stamp}{suffix}"
                dest = uploads_dir / image_filename
                dest.write_bytes(await image.read())

            crud.upsert_sku_part_meta(
                db,
                part_id=clone_source_part_id,
                customer_usage=customer_usage if customer_usage else None,
                image_filename=image_filename,
            )

        crud.log_activity(
            db,
            user_id=user.id,
            action="ADD_PART_ITEM",
            part_id=clone_source_part_id,
            description=f"Added item {item.serial_no or ''} for part id {clone_source_part_id}",
        )
        return RedirectResponse(url="/?success_msg=Part item added successfully", status_code=status.HTTP_302_FOUND)

    existing_sku = db.query(models.SkuPart).filter(models.SkuPart.sku == sku).first()
    if existing_sku:
        msg = quote("Part No already exists. Please use a different Part No.")
        return RedirectResponse(
            url=f"/?error_msg={msg}",
            status_code=status.HTTP_302_FOUND,
        )

    existing_name = db.query(models.SkuPart).filter(models.SkuPart.name == name).first()
    if existing_name:
        msg = quote("Part Description already exists. Please use a different description.")
        return RedirectResponse(
            url=f"/?error_msg={msg}",
            status_code=status.HTTP_302_FOUND,
        )

    part_in = schemas.SkuPartCreate(
        sku=sku,
        name=name,
        description=description,
        part_category=(part_category or "").strip() or None,
        rack_type=rack_type,
        tray_type=tray_type,
        platform=platform,
        annual_demand=annual_demand,
        criticality=criticality,
    )
    part = crud.create_part(db, part_in)
    if machine_category_id and machine_category_id.isdigit():
        crud.set_part_machine_category(db, part_id=part.id, machine_category_id=int(machine_category_id))
    if customer_usage or image:
        image_filename = None
        if image and getattr(image, "filename", None):
            suffix = Path(image.filename).suffix.lower()
            if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                suffix = ""
            uploads_dir = Path("app/static/part_images")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            image_filename = f"part_{part.id}_{stamp}{suffix}"
            dest = uploads_dir / image_filename
            dest.write_bytes(await image.read())

        crud.upsert_sku_part_meta(
            db,
            part_id=part.id,
            customer_usage=customer_usage,
            image_filename=image_filename,
        )
    crud.log_activity(db, user_id=user.id, action="ADD_PART", part_id=part.id, description=f"Added part {part.sku}")
    return RedirectResponse(url="/?success_msg=Part added successfully", status_code=status.HTTP_302_FOUND)

@app.post("/parts/update/{part_id}")
async def update_part_ui(
    part_id: int,
    request: Request,
    sku: str = Form(...),
    serial_no: str = Form(None),
    name: str = Form(...),
    description: str = Form(None),
    part_category: str = Form(None),
    current_stock: int = Form(...),
    rack_type: str = Form(None),
    tray_type: str = Form(None),
    platform: str = Form(None),
    lead_time_days: Optional[int] = Form(None),
    shipping_cost: Optional[float] = Form(None),
    unit_cost: Optional[float] = Form(None),
    holding_cost_percentage: Optional[float] = Form(None),
    annual_demand: Optional[int] = Form(None),
    eoq_min_threshold: int = Form(0),
    criticality: str = Form("Medium"),
    customer_usage: str = Form(None),
    image: UploadFile = File(None),
    machine_category_id: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if not part:
        return RedirectResponse(
            url="/?error_msg=Part not found",
            status_code=status.HTTP_302_FOUND,
        )

    existing_sku = (
        db.query(models.SkuPart)
        .filter(models.SkuPart.sku == sku, models.SkuPart.id != part_id)
        .first()
    )
    if existing_sku:
        msg = quote("Part No already exists. Please use a different Part No.")
        return RedirectResponse(
            url=f"/?error_msg={msg}",
            status_code=status.HTTP_302_FOUND,
        )

    existing_name = (
        db.query(models.SkuPart)
        .filter(models.SkuPart.name == name, models.SkuPart.id != part_id)
        .first()
    )
    if existing_name:
        msg = quote("Part Description already exists. Please use a different description.")
        return RedirectResponse(
            url=f"/?error_msg={msg}",
            status_code=status.HTTP_302_FOUND,
        )

    part.sku = sku
    part.name = name
    part.description = description
    part.part_category = (part_category or "").strip() or None
    part.rack_type = rack_type
    part.tray_type = tray_type
    part.platform = platform
    part.criticality = criticality

    if machine_category_id is not None:
        if machine_category_id.strip().isdigit():
            crud.set_part_machine_category(db, part_id=part.id, machine_category_id=int(machine_category_id.strip()))
        else:
            crud.set_part_machine_category(db, part_id=part.id, machine_category_id=None)

    image_filename = None
    if image and getattr(image, "filename", None):
        suffix = Path(image.filename).suffix.lower()
        if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
            suffix = ""
        uploads_dir = Path("app/static/part_images")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        image_filename = f"part_{part.id}_{stamp}{suffix}"
        dest = uploads_dir / image_filename
        dest.write_bytes(await image.read())

    if customer_usage is not None or image_filename is not None:
        crud.upsert_sku_part_meta(
            db,
            part_id=part.id,
            customer_usage=customer_usage if customer_usage is not None else None,
            image_filename=image_filename,
        )

    db.commit()
    db.refresh(part)

    crud.log_activity(db, user_id=user.id, action="UPDATE_PART", part_id=part.id, description=f"Updated part {part.sku}")
    return RedirectResponse(url="/?success_msg=Part updated successfully", status_code=status.HTTP_302_FOUND)


@app.get("/parts/{part_id}/meta")
async def get_part_meta(
    part_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({}, status_code=status.HTTP_401_UNAUTHORIZED)

    meta = crud.get_sku_part_meta(db, part_id)
    machine_link = crud.get_part_machine_category(db, part_id)
    machine_category_id = None
    machine_category_name = None
    if machine_link and machine_link.machine_category_id:
        machine_category_id = machine_link.machine_category_id
        if machine_link.machine_category:
            machine_category_name = machine_link.machine_category.name
    image_url = None
    if meta and meta.image_filename:
        image_url = f"/static/part_images/{meta.image_filename}"

    return JSONResponse(
        {
            "customer_usage": meta.customer_usage if meta else "",
            "image_url": image_url,
            "machine_category_id": machine_category_id,
            "machine_category_name": machine_category_name,
        }
    )



@app.get("/reports/faulty-parts", response_class=HTMLResponse)
async def faulty_parts_report(
    request: Request,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    page = max(page, 1)
    limit = max(min(limit, 200), 1)
    skip = (page - 1) * limit

    items, total_count = crud.get_faulty_items(db, skip=skip, limit=limit)
    total_pages = max((total_count + limit - 1) // limit, 1)

    return templates.TemplateResponse(
        request,
        "faulty_parts.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
        },
    )


@app.get("/export/reports/faulty-parts.xlsx")
async def export_faulty_parts_excel(
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    items, _ = crud.get_faulty_items(db, skip=0, limit=1_000_000)
    rows = []
    for item in items:
        rows.append(
            {
                "Part No": item.sku_part.sku if item.sku_part else "",
                "Part Description": item.sku_part.name if item.sku_part else "",
                "Serial No": item.serial_no or f"Item #{item.id}",
                "Vendor": item.vendor or "",
                "Date Out": item.date_out.strftime("%Y-%m-%d %H:%M:%S") if item.date_out else "",
                "Remark": item.remarks or "",
            }
        )
    return _excel_stream_response(rows, "internal_returning.xlsx", "InternalReturning")


async def _extract_usd_price_from_html(html_text: str):
    if not html_text:
        return None
    html_text = html.unescape(html_text)
    text_only = re.sub(r"<[^>]+>", " ", html_text)

    def score_window(window: str):
        w = (window or "").lower()
        score = 0
        if "add to cart" in w or "add to basket" in w:
            score += 8
        if "your price" in w or "unit price" in w:
            score += 6
        if "price" in w:
            score += 4
        if "in stock" in w or "availability" in w:
            score += 2
        if "woocommerce" in w or "price-amount" in w or "amount" in w:
            score += 2
        if "shipping" in w or "delivery" in w:
            score -= 7
        if "tax" in w or "vat" in w:
            score -= 5
        if "discount" in w or "coupon" in w or "save" in w or "% off" in w:
            score -= 3
        if "quantity" in w or "per quantity" in w or "qty" in w:
            score -= 2
        return score

    try:
        import json

        for m in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html_text, flags=re.IGNORECASE | re.DOTALL):
            chunk = (m or "").strip()
            if not chunk:
                continue
            try:
                data = json.loads(chunk)
            except Exception:
                continue

            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                offers = node.get("offers")
                if isinstance(offers, dict):
                    offers = [offers]
                if not isinstance(offers, list):
                    continue
                for o in offers:
                    if not isinstance(o, dict):
                        continue
                    cur = str(o.get("priceCurrency") or "").strip().upper()
                    if cur != "USD":
                        cur2 = ""
                        ps = o.get("priceSpecification")
                        if isinstance(ps, dict):
                            cur2 = str(ps.get("priceCurrency") or "").strip().upper()
                        elif isinstance(ps, list) and ps:
                            for item in ps:
                                if isinstance(item, dict):
                                    cur2 = str(item.get("priceCurrency") or "").strip().upper()
                                    if cur2:
                                        break
                        if cur2 != "USD":
                            continue

                    direct = o.get("price")
                    if direct is not None:
                        try:
                            f = float(str(direct).replace(",", "").strip())
                            if 1.0 <= f <= 1_000_000:
                                return f
                        except Exception:
                            pass
                    for key in ["lowPrice", "highPrice"]:
                        v = o.get(key)
                        if v is None:
                            continue
                        try:
                            f = float(str(v).replace(",", "").strip())
                            if 1.0 <= f <= 1_000_000:
                                return f
                        except Exception:
                            continue
                    ps = o.get("priceSpecification")
                    if isinstance(ps, dict):
                        v = ps.get("price")
                        if v is not None:
                            try:
                                f = float(str(v).replace(",", "").strip())
                                if 1.0 <= f <= 1_000_000:
                                    return f
                            except Exception:
                                pass
                    elif isinstance(ps, list):
                        for item in ps:
                            if not isinstance(item, dict):
                                continue
                            v = item.get("price")
                            if v is None:
                                continue
                            try:
                                f = float(str(v).replace(",", "").strip())
                                if 1.0 <= f <= 1_000_000:
                                    return f
                            except Exception:
                                continue
    except Exception:
        pass

    m = re.search(r'property=["\']product:price:currency["\'][^>]*content=["\']USD["\']', html_text, flags=re.IGNORECASE)
    if m:
        m2 = re.search(r'property=["\']product:price:amount["\'][^>]*content=["\']([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)["\']', html_text, flags=re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1).replace(",", ""))
            except Exception:
                pass
    m = re.search(r'itemprop=["\']priceCurrency["\'][^>]*content=["\']USD["\']', html_text, flags=re.IGNORECASE)
    if m:
        m2 = re.search(r'itemprop=["\']price["\'][^>]*content=["\']([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)["\']', html_text, flags=re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1).replace(",", ""))
            except Exception:
                pass

    candidates = []
    for m in re.finditer(r"(?:US\s?\$|USD\s*\$?|\$)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", text_only, flags=re.IGNORECASE):
        raw = m.group(1)
        try:
            val = float(str(raw).replace(",", ""))
        except Exception:
            continue
        if val < 1.0 or val > 1_000_000:
            continue
        window = text_only[max(0, m.start() - 180) : min(len(text_only), m.end() + 180)]
        candidates.append((val, score_window(window)))
    for m in re.finditer(r"\bUSD\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\b", text_only, flags=re.IGNORECASE):
        raw = m.group(1)
        try:
            val = float(str(raw).replace(",", ""))
        except Exception:
            continue
        if 1.0 <= val <= 1_000_000:
            window = text_only[max(0, m.start() - 180) : min(len(text_only), m.end() + 180)]
            candidates.append((val, score_window(window)))
    for m in re.finditer(r"\b([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*USD\b", text_only, flags=re.IGNORECASE):
        raw = m.group(1)
        try:
            val = float(str(raw).replace(",", ""))
        except Exception:
            continue
        if 1.0 <= val <= 1_000_000:
            window = text_only[max(0, m.start() - 180) : min(len(text_only), m.end() + 180)]
            candidates.append((val, score_window(window)))
    if not candidates:
        return None
    filtered = []
    for val, score in candidates:
        if score < 3:
            continue
        if val < 5.0 and score < 8:
            continue
        filtered.append((val, score))
    if not filtered:
        return None
    best = max(filtered, key=lambda x: (x[1], x[0]))
    return best[0]


async def _extract_myr_price_from_html(html_text: str):
    if not html_text:
        return None
    html_text = html.unescape(html_text)
    text_only = re.sub(r"<[^>]+>", " ", html_text)

    def score_window(window: str):
        w = (window or "").lower()
        score = 0
        if "add to cart" in w or "add to basket" in w:
            score += 8
        if "harga" in w:
            score += 5
        if "price" in w:
            score += 4
        if "in stock" in w or "availability" in w:
            score += 2
        if "shipping" in w or "delivery" in w:
            score -= 7
        if "tax" in w or "sst" in w or "vat" in w:
            score -= 5
        if "discount" in w or "coupon" in w or "save" in w or "% off" in w:
            score -= 3
        if "quantity" in w or "per quantity" in w or "qty" in w:
            score -= 2
        return score

    try:
        import json

        for m in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html_text, flags=re.IGNORECASE | re.DOTALL):
            chunk = (m or "").strip()
            if not chunk:
                continue
            try:
                data = json.loads(chunk)
            except Exception:
                continue

            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                offers = node.get("offers")
                if isinstance(offers, dict):
                    offers = [offers]
                if not isinstance(offers, list):
                    continue
                for o in offers:
                    if not isinstance(o, dict):
                        continue
                    cur = str(o.get("priceCurrency") or "").strip().upper()
                    if cur not in ["MYR", "RM"]:
                        cur2 = ""
                        ps = o.get("priceSpecification")
                        if isinstance(ps, dict):
                            cur2 = str(ps.get("priceCurrency") or "").strip().upper()
                        elif isinstance(ps, list) and ps:
                            for item in ps:
                                if isinstance(item, dict):
                                    cur2 = str(item.get("priceCurrency") or "").strip().upper()
                                    if cur2:
                                        break
                        if cur2 not in ["MYR", "RM"]:
                            continue

                    direct = o.get("price")
                    if direct is not None:
                        try:
                            f = float(str(direct).replace(",", "").strip())
                            if 5.0 <= f <= 10_000_000:
                                return f
                        except Exception:
                            pass
                    for key in ["lowPrice", "highPrice"]:
                        v = o.get(key)
                        if v is None:
                            continue
                        try:
                            f = float(str(v).replace(",", "").strip())
                            if 5.0 <= f <= 10_000_000:
                                return f
                        except Exception:
                            continue
                    ps = o.get("priceSpecification")
                    if isinstance(ps, dict):
                        v = ps.get("price")
                        if v is not None:
                            try:
                                f = float(str(v).replace(",", "").strip())
                                if 5.0 <= f <= 10_000_000:
                                    return f
                            except Exception:
                                pass
                    elif isinstance(ps, list):
                        for item in ps:
                            if not isinstance(item, dict):
                                continue
                            v = item.get("price")
                            if v is None:
                                continue
                            try:
                                f = float(str(v).replace(",", "").strip())
                                if 5.0 <= f <= 10_000_000:
                                    return f
                            except Exception:
                                continue
    except Exception:
        pass

    candidates = []
    for m in re.finditer(r"(?<![A-Za-z])(?:RM|MYR)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", text_only, flags=re.IGNORECASE):
        raw = m.group(1)
        try:
            val = float(str(raw).replace(",", ""))
        except Exception:
            continue
        if val < 5.0 or val > 10_000_000:
            continue
        window = text_only[max(0, m.start() - 180) : min(len(text_only), m.end() + 180)]
        candidates.append((val, score_window(window)))
    if not candidates:
        return None
    filtered = []
    for val, score in candidates:
        if score < 3:
            continue
        if val < 20.0 and score < 8:
            continue
        filtered.append((val, score))
    if not filtered:
        return None
    best = max(filtered, key=lambda x: (x[1], x[0]))
    return best[0]

def _normalize_vendor_url(raw_url: str | None):
    if not raw_url:
        return None

    url = html.unescape(str(raw_url)).strip()
    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://duckduckgo.com" + url

    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        if target:
            target_url = html.unescape(unquote(target)).strip()
            if target_url.startswith("//"):
                target_url = "https:" + target_url
            if target_url.startswith("http://") or target_url.startswith("https://"):
                return target_url

    return url


def _search_results_url(query: str):
    q = quote((query or "").strip())
    now = datetime.utcnow()
    cd_max = now.strftime("%m/%d/%Y")
    cd_min = (now - timedelta(days=183)).strftime("%m/%d/%Y")
    return f"https://www.google.com/search?q={q}&tbs=cdr:1,cd_min:{quote(cd_min)},cd_max:{quote(cd_max)}"


VENDOR_SEARCH_MAX_AGE_DAYS = 183


async def _bing_top_links(query: str, limit: int = 10, max_age_days: int = 183):
    try:
        import base64
        import html as _html

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        qft = f"+filterui:age-lt{max(1, int(max_age_days or 183))}"
        async with httpx.AsyncClient(timeout=12.0, headers=headers, follow_redirects=True) as client:
            r = await client.get("https://www.bing.com/search", params={"q": query, "qft": qft})
            text = r.text or ""
            if r.status_code != 200 or not text:
                return []

        raw = re.findall(
            r'<li class="b_algo"[\s\S]*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"',
            text,
            flags=re.IGNORECASE,
        )
        out = []
        seen = set()
        for u in raw:
            if len(out) >= limit:
                break
            try:
                u = _html.unescape(u)
                qs = parse_qs(urlsplit(u).query)
                enc = (qs.get("u") or [None])[0]
                if isinstance(enc, str) and enc.startswith("a1"):
                    enc = enc[2:]
                final = None
                if isinstance(enc, str) and enc:
                    pad = "=" * ((4 - (len(enc) % 4)) % 4)
                    decoded = base64.b64decode(enc + pad).decode("utf-8", "ignore")
                    if decoded.startswith("http://") or decoded.startswith("https://"):
                        final = decoded
                if not final:
                    continue
            except Exception:
                continue

            normalized = _normalize_vendor_url(final)
            if not normalized:
                continue
            try:
                host = (urlparse(normalized).hostname or "").lower()
            except Exception:
                host = ""
            if not host:
                continue
            if host.endswith(("bing.com", "duckduckgo.com", "google.com")):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out
    except Exception:
        return []


def _default_vendor_query(base: str):
    b = (base or "").strip()
    if not b:
        return ""
    return f"{b} price new refurbished used second hand"


def _simplify_vendor_match_query(query: str):
    q = (query or "").strip()
    if not q:
        return ""
    cleaned = re.sub(r"\b(price|buy|new|refurbished|used|second|hand|for|sale)\b", " ", q, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or q


def _primary_product_code(raw: str | None):
    s = (raw or "").strip()
    if not s:
        return ""
    if "/" in s:
        parts = [p.strip() for p in re.split(r"\s*/\s*", s) if p.strip()]
        if parts:
            return parts[0]
    return s


def _best_required_code(query: str):
    q = (query or "").strip()
    if not q:
        return ""
    raw_candidates = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}", q)
    candidates: list[str] = []
    for c in raw_candidates:
        if "/" in c:
            candidates.extend([p for p in c.split("/") if p])
        else:
            candidates.append(c)
    best = ""
    best_key = (-1, -1, -1)
    for c in candidates:
        cc = c.strip("._/-")
        if not cc:
            continue
        digits = sum(1 for ch in cc if ch.isdigit())
        alnum_len = sum(1 for ch in cc if ch.isalnum())
        has_alpha = any(ch.isalpha() for ch in cc)
        if digits >= 4 or (has_alpha and digits >= 2 and alnum_len >= 6):
            key = (digits, alnum_len, 1 if has_alpha else 0)
            if key > best_key:
                best_key = key
                best = cc
    return best


def _extract_exclusive_markers(text: str):
    t = (text or "").lower()
    markers: dict[str, str] = {}
    for v in ["ddr2", "ddr3", "ddr4", "ddr5"]:
        if re.search(rf"\b{re.escape(v)}\b", t):
            markers["ddr"] = v
            break
    for v in ["pc2", "pc3", "pc4", "pc5"]:
        if re.search(rf"\b{re.escape(v)}\b", t) or re.search(rf"\b{re.escape(v)}[-_ ]", t):
            markers["pc"] = v
            break
    if re.search(r"\brdimm\b", t):
        markers["dimm_type"] = "rdimm"
    elif re.search(r"\budimm\b", t):
        markers["dimm_type"] = "udimm"
    return markers


def _has_exclusive_mismatch(haystack: str, markers: dict[str, str]):
    h = (haystack or "").lower()
    ddr = markers.get("ddr")
    if ddr:
        others = [v for v in ["ddr2", "ddr3", "ddr4", "ddr5"] if v != ddr]
        if any(re.search(rf"\b{re.escape(v)}\b", h) for v in others) and not re.search(rf"\b{re.escape(ddr)}\b", h):
            return True
    pc = markers.get("pc")
    if pc:
        others = [v for v in ["pc2", "pc3", "pc4", "pc5"] if v != pc]
        if any(re.search(rf"\b{re.escape(v)}\b", h) for v in others) and not re.search(rf"\b{re.escape(pc)}\b", h):
            return True
    dimm_type = markers.get("dimm_type")
    if dimm_type in ["rdimm", "udimm"]:
        other = "udimm" if dimm_type == "rdimm" else "rdimm"
        if re.search(rf"\b{other}\b", h) and not re.search(rf"\b{dimm_type}\b", h):
            return True
    return False


async def _link_only_vendor_offers(query: str, limit: int = 10, exclude_urls: set[str] | None = None):
    q = (query or "").strip()
    if not q:
        return []
    exclude_urls = exclude_urls or set()
    links = await _vendor_top_links(q, limit=max(limit * 3, 20))
    out = []
    seen = set(exclude_urls)
    for u in links:
        if len(out) >= limit:
            break
        url = (u or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
        except Exception:
            host = ""
        if not host:
            continue
        if host.endswith("duckduckgo.com") or host.endswith("google.com") or host.endswith("bing.com"):
            continue
        slug = ""
        try:
            parts = [p for p in (parsed.path or "").split("/") if p]
            slug = parts[-1] if parts else ""
        except Exception:
            slug = ""
        slug = re.sub(r"[-_]+", " ", slug).strip()
        title = (host + (" " + slug if slug else "")).strip()[:140]
        out.append({"title": title or host, "url": url, "price_usd": None, "price_myr": None, "source": "Search"})
    return out


def _offer_has_price(offer: dict):
    if not isinstance(offer, dict):
        return False
    usd = offer.get("price_usd")
    myr = offer.get("price_myr")
    usd_val = None
    myr_val = None
    try:
        usd_val = float(usd) if usd is not None else None
    except Exception:
        usd_val = None
    try:
        myr_val = float(myr) if myr is not None else None
    except Exception:
        myr_val = None

    return (usd_val is not None and usd_val > 0) or (myr_val is not None and myr_val > 0)


def _offer_has_hyperlink(offer: dict):
    if not isinstance(offer, dict):
        return False
    url = str(offer.get("url") or "").strip()
    return url.startswith("http://") or url.startswith("https://")


def _fill_offer_currency(offer: dict, myr_per_usd: float | None):
    if not isinstance(offer, dict):
        return offer
    try:
        rate = float(myr_per_usd or 0.0)
    except Exception:
        rate = 0.0
    if rate <= 0:
        return offer

    out = dict(offer)
    usd = out.get("price_usd")
    myr = out.get("price_myr")
    try:
        usd_val = float(usd) if usd is not None else None
    except Exception:
        usd_val = None
    try:
        myr_val = float(myr) if myr is not None else None
    except Exception:
        myr_val = None

    if usd_val is not None and myr_val is None:
        out["price_myr"] = round(usd_val * rate, 2)
    elif myr_val is not None and usd_val is None:
        out["price_usd"] = round(myr_val / rate, 2)
    return out


def _strip_code_fences(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _try_parse_json_array(text: str):
    t = _strip_code_fences(text)
    if not t:
        return None
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            return parsed.get("results")
    except Exception:
        pass
    m = re.search(r"\[[\s\S]*\]", t)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        if isinstance(parsed, list):
            return parsed
    except Exception:
        return None
    return None


def _try_parse_json_object(text: str):
    t = _strip_code_fences(text)
    if not t:
        return None
    try:
        parsed = json.loads(t)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


async def _llm_vendor_candidates(query: str, limit: int = 10):
    q = (query or "").strip()
    if not q:
        return []

    required_code = _best_required_code(q)
    prompt = (
        "Given this product query, return a JSON array (max "
        + str(limit * 3)
        + ") of objects with keys: title, url. "
        "Prefer pages that show an actual price (product page or listing page). "
        "Prefer sites that allow sorting/filtering by price (low to high) or listing pages already sorted by price. "
        "If you cannot find exact product pages, return vendor-site search pages (e.g. amazon.com search, ebay search) or vendor category pages. "
        "Do not use general search engines (no google.com, bing.com, duckduckgo.com). "
        "Match the query precisely; do not return mismatched variants (e.g. DDR4 when the query says DDR3, PC4 when the query says PC3). "
        + (f" If a part number is present, it must match exactly: {required_code}." if required_code else "")
        + "If unsure, still return vendor-site search pages. Return an empty array only if the query is empty.\n\nQuery: "
        + q
    )

    async def call_ollama():
        host = (os.getenv("VENDOR_SOURCES_OLLAMA_HOST") or os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        model = (os.getenv("VENDOR_SOURCES_OLLAMA_MODEL") or "llama3.1").strip()
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Return only JSON. Task: propose vendor/product URLs for a procurement search."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(f"{host}/api/chat", json=payload)
                if r.status_code >= 400:
                    return []
                data = r.json()
                content = ((data.get("message") or {}).get("content") or "").strip()
                arr = _try_parse_json_array(content)
                return arr or []
        except Exception:
            return []

    async def call_azure_openai():
        endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
        deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
        api_version = (os.getenv("AZURE_OPENAI_API_VERSION") or "").strip()
        api_key = (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY") or os.getenv("VENDOR_SOURCES_LLM_API_KEY") or "").strip()
        if not (endpoint and deployment and api_version and api_key):
            return []
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
        params = {"api-version": api_version}
        payload = {
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": "Return only JSON. Task: propose vendor/product URLs for a procurement search."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"api-key": api_key}
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
                r = await client.post(url, params=params, json=payload)
                if r.status_code >= 400:
                    return []
                data = r.json()
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                arr = _try_parse_json_array(content)
                return arr or []
        except Exception:
            return []

    async def call_openai():
        api_key = (os.getenv("VENDOR_SOURCES_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return []
        base_url = (os.getenv("VENDOR_SOURCES_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        if base_url.endswith("/v1"):
            endpoint = f"{base_url}/chat/completions"
        else:
            endpoint = f"{base_url}/v1/chat/completions"
        model = (os.getenv("VENDOR_SOURCES_LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": "Return only JSON. Task: propose vendor/product URLs for a procurement search."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
                r = await client.post(endpoint, json=payload)
                if r.status_code >= 400:
                    return []
                data = r.json()
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                arr = _try_parse_json_array(content)
                return arr or []
        except Exception:
            return []

    provider = (os.getenv("VENDOR_SOURCES_LLM_PROVIDER") or "").strip().lower()
    if provider in ["azure", "azure_openai"]:
        return await call_azure_openai()
    if provider == "openai":
        return await call_openai()
    if provider == "ollama":
        return await call_ollama()

    candidates = await call_azure_openai()
    if candidates:
        return candidates
    candidates = await call_openai()
    if candidates:
        return candidates
    return await call_ollama()


def _percentile(sorted_vals: list[float], p: float):
    if not sorted_vals:
        return None
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 1:
        return float(sorted_vals[-1])
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def _format_money_range(low: float | None, high: float | None, currency: str):
    if low is None and high is None:
        return ""
    if low is None:
        return f"≤ {currency} {high:.2f}"
    if high is None:
        return f"≥ {currency} {low:.2f}"
    if abs(low - high) < 0.01:
        return f"{currency} {low:.2f}"
    return f"{currency} {low:.2f} – {currency} {high:.2f}"


async def _azure_vendor_ai_summary(product_query: str, part_no: str, offers: list[dict], myr_per_usd: float):
    q = (product_query or "").strip()
    if not q:
        return None
    pn = (part_no or "").strip()
    prices = []
    for o in offers or []:
        try:
            v = float(o.get("price_usd")) if o.get("price_usd") is not None else None
        except Exception:
            v = None
        if v is not None and v > 0:
            prices.append(v)
    prices.sort()
    p25 = _percentile(prices, 0.25)
    p50 = _percentile(prices, 0.50)
    p75 = _percentile(prices, 0.75)
    pmin = prices[0] if prices else None
    pmax = prices[-1] if prices else None
    rate = float(myr_per_usd or 0.0)

    ranges = {
        "cheapest_usd": _format_money_range(pmin, p25, "USD") if prices else "",
        "typical_usd": _format_money_range(p25, p75, "USD") if prices else "",
        "high_end_usd": _format_money_range(p75, pmax, "USD") if prices else "",
        "cheapest_myr": _format_money_range(pmin * rate if (pmin is not None and rate > 0) else None, p25 * rate if (p25 is not None and rate > 0) else None, "MYR") if prices and rate > 0 else "",
        "typical_myr": _format_money_range(p25 * rate if (p25 is not None and rate > 0) else None, p75 * rate if (p75 is not None and rate > 0) else None, "MYR") if prices and rate > 0 else "",
        "high_end_myr": _format_money_range(p75 * rate if (p75 is not None and rate > 0) else None, pmax * rate if (pmax is not None and rate > 0) else None, "MYR") if prices and rate > 0 else "",
    }

    cache_key = ("vendor_ai_summary", pn.lower(), q.lower(), round(rate, 6), ranges.get("cheapest_usd"), ranges.get("typical_usd"), ranges.get("high_end_usd"))
    now_s = time.time()
    cached = _VENDOR_AI_SUMMARY_CACHE.get(cache_key)
    if cached and (now_s - float(cached[0])) < 3600:
        return dict(cached[1])

    endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
    deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
    api_version = (os.getenv("AZURE_OPENAI_API_VERSION") or "").strip()
    api_key = (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY") or "").strip()
    if not (endpoint and deployment and api_version and api_key):
        return None

    offer_titles = []
    for o in (offers or [])[:8]:
        title = (o.get("title") or "").strip()
        url = (o.get("url") or "").strip()
        usd = o.get("price_usd")
        if title or url:
            offer_titles.append({"title": title[:120], "url": url[:200], "price_usd": usd})

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
    params = {"api-version": api_version}
    headers = {"api-key": api_key}
    payload = {
        "temperature": 0,
        "max_tokens": 520,
        "messages": [
            {
                "role": "system",
                "content": "Return only JSON object. Do not hallucinate. Use only the provided query and price ranges.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "part_no": pn,
                        "query": q,
                        "price_ranges": ranges,
                        "sample_offers": offer_titles,
                        "output_schema": {
                            "product_overview": "string (1-2 sentences, based only on query)",
                            "key_specs": ["string"],
                            "best_value_ranges": {
                                "cheapest_usd": "string",
                                "typical_usd": "string",
                                "high_end_usd": "string",
                                "cheapest_myr": "string",
                                "typical_myr": "string",
                                "high_end_myr": "string",
                            },
                            "buying_advice": ["string (short bullets, no vendor endorsement)"],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            r = await client.post(url, params=params, json=payload)
            if r.status_code >= 400:
                return None
            data = r.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            obj = _try_parse_json_object(content)
            if not isinstance(obj, dict):
                return None
            obj.setdefault("best_value_ranges", ranges)
            _VENDOR_AI_SUMMARY_CACHE[cache_key] = (now_s, dict(obj))
            return obj
    except Exception:
        return None


async def _llm_top_links(query: str, limit: int = 10):
    candidates = await _llm_vendor_candidates(query, limit=limit)
    if not candidates:
        return []

    preferred_hosts = {
        "amazon.com",
        "ebay.com",
        "newegg.com",
        "aliexpress.com",
        "rakuten.com",
        "walmart.com",
        "shopee.com.my",
        "lazada.com.my",
        "carousell.com",
        "carousell.com.my",
    }
    required_code = _best_required_code(_simplify_vendor_match_query(query))
    required = _normalize_code(required_code)
    digit_count = sum(1 for ch in required if ch.isdigit())
    markers = _extract_exclusive_markers(query)

    scored: list[tuple[int, str]] = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = _normalize_vendor_url(item.get("url"))
        if not url:
            continue
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if not host:
            continue
        if host.endswith(("duckduckgo.com", "google.com", "bing.com")):
            continue
        if url in seen:
            continue
        seen.add(url)

        u_norm = _normalize_code(url)
        if markers and _has_exclusive_mismatch(f"{url} {item.get('title','')}", markers):
            continue
        score = 0
        if required and digit_count >= 4 and required in u_norm:
            score += 200
        elif required and digit_count >= 4:
            score -= 50
        elif required and required in u_norm:
            score += 80

        for ph in preferred_hosts:
            if host == ph or host.endswith("." + ph):
                score += 10
                break

        lowered = url.lower()
        if any(x in lowered for x in ["/dp/", "/gp/", "/itm/", "/product/", "/products/", "/p/", "/item/"]):
            score += 8
        if any(x in lowered for x in ["search", "query", "q="]):
            score += 4
        if lowered.count("/") <= 2:
            score -= 3

        scored.append((score, url))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = [u for _, u in scored[:limit]]
    return out


async def _duckduckgo_top_links(query: str, limit: int = 5):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://duckduckgo.com/",
    }

    async def fetch_and_parse(url: str, regexes: list[str]):
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers, follow_redirects=True) as client:
                r = await client.get(url, params={"q": query})
                text = r.text or ""
                if r.status_code != 200 or not text:
                    raise RuntimeError("non-200 or empty")
                found: list[str] = []
                for rx in regexes:
                    found.extend(re.findall(rx, text))
                return found
        except Exception:
            try:
                q = quote((query or "").strip())
                fallback_url = f"https://r.jina.ai/{url}?q={q}"
                async with httpx.AsyncClient(timeout=12.0, headers=headers, follow_redirects=True) as client:
                    r = await client.get(fallback_url)
                    text = r.text or ""
                    if r.status_code != 200 or not text:
                        return []
                    found: list[str] = []
                    for rx in regexes:
                        found.extend(re.findall(rx, text))
                    found.extend(re.findall(r"\]\((https?://[^)\s]+)\)", text))
                    return found
            except Exception:
                return []

    links = await fetch_and_parse(
        "https://duckduckgo.com/html/",
        [
            r'class="result__a" href="([^"]+)"',
            r'class="result__url[^"]*" href="([^"]+)"',
            r'href="(https?://[^"]+)"[^>]*class="result__a"',
        ],
    )
    if not links:
        links = await fetch_and_parse(
            "https://lite.duckduckgo.com/lite/",
            [
                r'<a rel="nofollow" href="([^"]+)"',
            ],
        )
    deduped = []
    seen = set()
    for l in links:
        normalized = _normalize_vendor_url(l)
        if not normalized:
            continue
        try:
            host = (urlparse(normalized).hostname or "").lower()
        except Exception:
            host = ""
        if host.endswith("duckduckgo.com"):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    if deduped:
        return deduped

    try:
        import base64
        import html as _html

        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
            r = await client.get("https://www.bing.com/search", params={"q": query})
            text = r.text or ""
            if r.status_code == 200 and text:
                raw = re.findall(
                    r'<li class="b_algo"[\s\S]*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"',
                    text,
                    flags=re.IGNORECASE,
                )
                out = []
                for u in raw:
                    if len(out) >= limit:
                        break
                    try:
                        u = _html.unescape(u)
                        qs = parse_qs(urlsplit(u).query)
                        enc = (qs.get("u") or [None])[0]
                        if isinstance(enc, str) and enc.startswith("a1"):
                            enc = enc[2:]
                        final = None
                        if isinstance(enc, str) and enc:
                            pad = "=" * ((4 - (len(enc) % 4)) % 4)
                            decoded = base64.b64decode(enc + pad).decode("utf-8", "ignore")
                            if decoded.startswith("http://") or decoded.startswith("https://"):
                                final = decoded
                        if not final:
                            continue
                    except Exception:
                        continue

                    normalized = _normalize_vendor_url(final)
                    if not normalized:
                        continue
                    try:
                        host = (urlparse(normalized).hostname or "").lower()
                    except Exception:
                        host = ""
                    if host.endswith("bing.com") or host.endswith("duckduckgo.com"):
                        continue
                    if normalized in seen:
                        continue
                    seen.add(normalized)
                    out.append(normalized)
                if out:
                    return out
    except Exception:
        pass

    return []


async def _vendor_top_links(query: str, limit: int = 10):
    provider = (os.getenv("VENDOR_SOURCES_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "llm" if (os.getenv("VENDOR_SOURCES_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY") or os.getenv("OLLAMA_HOST") or os.getenv("VENDOR_SOURCES_OLLAMA_HOST")) else "duckduckgo"

    if provider in ["llm", "openai", "ollama"]:
        llm_task = asyncio.create_task(_llm_top_links(query, limit=limit))
        ddg_task = asyncio.create_task(_duckduckgo_top_links(query, limit=limit))
        llm_links: list[str] = []
        ddg_links: list[str] = []
        try:
            llm_links = await llm_task
        except Exception:
            llm_links = []
        try:
            ddg_links = await ddg_task
        except Exception:
            ddg_links = []

        merged: list[str] = []
        seen = set()
        for u in (llm_links or []):
            if u and u not in seen:
                seen.add(u)
                merged.append(u)
        for u in (ddg_links or []):
            if u and u not in seen:
                seen.add(u)
                merged.append(u)
            if len(merged) >= limit:
                break
        if merged:
            required = _normalize_code(_best_required_code(_simplify_vendor_match_query(query)))
            digit_count = sum(1 for ch in required if ch.isdigit())
            if required and digit_count >= 4:
                markers = _extract_exclusive_markers(query)
                filtered = []
                for u in merged:
                    if required not in _normalize_code(u):
                        continue
                    if markers and _has_exclusive_mismatch(u, markers):
                        continue
                    filtered.append(u)
                if filtered:
                    return filtered[:limit]
            return merged[:limit]
        return []

    return await _duckduckgo_top_links(query, limit=limit)


async def _web_vendor_offers(query: str, myr_per_usd: float | None = None, limit: int = 5):
    q = (query or "").strip()
    if not q:
        return []

    cache_key = ("simple", q, float(myr_per_usd or 0.0), int(limit or 0))
    now_s = time.time()
    cached = _VENDOR_OFFERS_CACHE.get(cache_key)
    if cached and (now_s - float(cached[0])) < 600:
        return list(cached[1])[:limit]

    ql = q.lower()
    markers = _extract_exclusive_markers(_simplify_vendor_match_query(q))
    required_code = _best_required_code(_simplify_vendor_match_query(q))
    required_norm = _normalize_code(required_code)
    required_pat = _build_code_pattern(required_code) if required_code else None
    query_variants = [q]
    if "price" not in ql:
        query_variants.append(f"{q} price")
    if "refurbished" not in ql:
        query_variants.append(f"{q} refurbished")
    if "used" not in ql:
        query_variants.append(f"{q} used")
    links = []
    seen = set()
    for qq in query_variants:
        found = await _vendor_top_links(qq, limit=10)
        for u in found:
            if u in seen:
                continue
            if markers and _has_exclusive_mismatch(u, markers):
                continue
            seen.add(u)
            links.append(u)
            if len(links) >= 18:
                break
        if len(links) >= 18:
            break

    if not links:
        return []

    sem = asyncio.Semaphore(12)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    rate = float(myr_per_usd or 0.0)

    async def fetch_html_range(url: str, client: httpx.AsyncClient):
        try:
            r = await client.get(url, headers={"Range": "bytes=0-180000"})
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        try:
            proxy_url = f"https://r.jina.ai/{url}"
            r = await client.get(proxy_url, timeout=4.5)
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        return ""

    async def fetch_html_full(url: str, client: httpx.AsyncClient):
        try:
            r = await client.get(url)
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        return ""

    async def probe(url: str, client: httpx.AsyncClient):
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if not host:
                return None
            if host.endswith("duckduckgo.com") or host.endswith("google.com") or host.endswith("bing.com"):
                return None
            if url.lower().endswith(".pdf"):
                return None
            async with sem:
                html_text = await fetch_html_range(url, client)
                if not html_text:
                    return None
            is_malaysia_host = host.endswith(".my") or host.endswith(".com.my")
            usd_raw = await _extract_usd_price_from_html(html_text)
            myr_raw = await _extract_myr_price_from_html(html_text)
            if usd_raw is None and myr_raw is None:
                async with sem:
                    html_text2 = await fetch_html_full(url, client)
                if html_text2:
                    usd_raw = await _extract_usd_price_from_html(html_text2)
                    myr_raw = await _extract_myr_price_from_html(html_text2)
            usd = usd_raw
            myr = myr_raw

            if usd is None and myr is not None and rate > 0 and is_malaysia_host:
                usd = myr / rate
            if myr is None and usd is not None and rate > 0:
                myr = usd * rate

            if usd is not None and myr is not None and rate > 0:
                expected_myr = usd * rate
                if expected_myr > 0:
                    ratio = myr / expected_myr
                    if ratio < 0.5 or ratio > 2.0:
                        if is_malaysia_host:
                            usd = None
                        else:
                            myr = None

            if usd is None and myr is None:
                return None
            m = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
            title = host
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:140] or host
            if required_norm and (sum(1 for ch in required_norm if ch.isdigit()) >= 4 or len(required_norm) >= 6):
                hay = f"{title} {url} {html_text[:50000]}"
                if required_pat:
                    if not required_pat.search(hay):
                        return None
                else:
                    if required_norm not in _normalize_code(hay):
                        return None
            if markers and _has_exclusive_mismatch(f"{title} {url}", markers):
                return None
            return {
                "title": title,
                "url": url,
                "price_usd": float(usd) if usd is not None else None,
                "price_myr": float(myr) if myr is not None else None,
                "age_days": 0,
                "host": host,
                "source": "Web",
            }
        except Exception:
            return None

    raw = []
    timeout_s = 20.0
    target_hits = max(min(limit * 2, 12), limit)
    async with httpx.AsyncClient(timeout=7.0, headers=headers, follow_redirects=True) as client:
        tasks = [asyncio.create_task(probe(u, client)) for u in links]
        try:
            for fut in asyncio.as_completed(tasks, timeout=timeout_s):
                item = await fut
                if item:
                    raw.append(item)
                    if len(raw) >= target_hits:
                        break
        except Exception:
            pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    if not raw:
        return []

    by_host = {}
    for r in raw:
        host = r.get("host") or r.get("url")
        by_host.setdefault(host, []).append(r)

    offers = []
    for host, rows in by_host.items():
        rows.sort(key=lambda o: (o.get("price_usd") is None, o.get("price_usd") if o.get("price_usd") is not None else 0.0))
        offers.extend(rows[:2])
    offers.sort(key=lambda o: (o.get("price_usd") is None, o.get("price_usd") if o.get("price_usd") is not None else 0.0))
    for o in offers:
        o.pop("host", None)
    out = offers[:limit]
    _VENDOR_OFFERS_CACHE[cache_key] = (now_s, list(out))
    return out

async def _web_vendor_offers_extended(
    query: str,
    myr_per_usd: float | None = None,
    limit: int = 10,
    per_variant_limit: int = 30,
    max_links: int = 80,
):
    q = (query or "").strip()
    if not q:
        return []

    cache_key = ("extended", q, float(myr_per_usd or 0.0), int(limit or 0), int(per_variant_limit or 0), int(max_links or 0))
    now_s = time.time()
    cached = _VENDOR_OFFERS_CACHE.get(cache_key)
    if cached and (now_s - float(cached[0])) < 600:
        return list(cached[1])[:limit]

    per_variant_limit = max(1, min(per_variant_limit, 80))
    max_links = max(1, min(max_links, 200))
    limit = max(1, min(limit, 200))

    ql = q.lower()
    markers = _extract_exclusive_markers(_simplify_vendor_match_query(q))
    required_code = _best_required_code(_simplify_vendor_match_query(q))
    required_norm = _normalize_code(required_code)
    required_pat = _build_code_pattern(required_code) if required_code else None
    query_variants = [q]
    if "price" not in ql:
        query_variants.append(f"{q} price")
    if "refurbished" not in ql:
        query_variants.append(f"{q} refurbished")
    if "used" not in ql:
        query_variants.append(f"{q} used")
    links = []
    seen = set()
    for qq in query_variants:
        found = await _vendor_top_links(qq, limit=per_variant_limit)
        for u in found:
            if u in seen:
                continue
            if markers and _has_exclusive_mismatch(u, markers):
                continue
            seen.add(u)
            links.append(u)
            if len(links) >= max_links:
                break
        if len(links) >= max_links:
            break

    if not links:
        return []

    sem = asyncio.Semaphore(12)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    rate = float(myr_per_usd or 0.0)

    async def fetch_html_range(url: str, client: httpx.AsyncClient):
        try:
            r = await client.get(url, headers={"Range": "bytes=0-220000"})
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        try:
            proxy_url = f"https://r.jina.ai/{url}"
            r = await client.get(proxy_url, timeout=4.5)
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        return ""

    async def fetch_html_full(url: str, client: httpx.AsyncClient):
        try:
            r = await client.get(url)
            if r.status_code < 400 and (r.text or "").strip():
                return r.text or ""
        except Exception:
            pass
        return ""

    async def probe(url: str, client: httpx.AsyncClient):
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if not host:
                return None
            if host.endswith("duckduckgo.com") or host.endswith("google.com") or host.endswith("bing.com"):
                return None
            if url.lower().endswith(".pdf"):
                return None
            async with sem:
                html_text = await fetch_html_range(url, client)
                if not html_text:
                    return None
            is_malaysia_host = host.endswith(".my") or host.endswith(".com.my")
            usd_raw = await _extract_usd_price_from_html(html_text)
            myr_raw = await _extract_myr_price_from_html(html_text)
            if usd_raw is None and myr_raw is None:
                async with sem:
                    html_text2 = await fetch_html_full(url, client)
                if html_text2:
                    usd_raw = await _extract_usd_price_from_html(html_text2)
                    myr_raw = await _extract_myr_price_from_html(html_text2)
            usd = usd_raw
            myr = myr_raw

            if usd is None and myr is not None and rate > 0 and is_malaysia_host:
                usd = myr / rate
            if myr is None and usd is not None and rate > 0:
                myr = usd * rate

            if usd is not None and myr is not None and rate > 0:
                expected_myr = usd * rate
                if expected_myr > 0:
                    ratio = myr / expected_myr
                    if ratio < 0.5 or ratio > 2.0:
                        if is_malaysia_host:
                            usd = None
                        else:
                            myr = None

            if usd is None and myr is None:
                return None
            m = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
            title = host
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:140] or host
            if required_norm and (sum(1 for ch in required_norm if ch.isdigit()) >= 4 or len(required_norm) >= 6):
                hay = f"{title} {url} {html_text[:50000]}"
                if required_pat:
                    if not required_pat.search(hay):
                        return None
                else:
                    if required_norm not in _normalize_code(hay):
                        return None
            if markers and _has_exclusive_mismatch(f"{title} {url}", markers):
                return None
            return {
                "title": title,
                "url": url,
                "price_usd": float(usd) if usd is not None else None,
                "price_myr": float(myr) if myr is not None else None,
                "age_days": 0,
                "host": host,
                "source": "Web",
            }
        except Exception:
            return None

    raw = []
    timeout_s = 18.0
    target_hits = max(min(limit * 2, 20), limit)
    async with httpx.AsyncClient(timeout=7.0, headers=headers, follow_redirects=True) as client:
        tasks = [asyncio.create_task(probe(u, client)) for u in links]
        try:
            for fut in asyncio.as_completed(tasks, timeout=timeout_s):
                item = await fut
                if item:
                    raw.append(item)
                    if len(raw) >= target_hits:
                        break
        except Exception:
            pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    if not raw:
        return []

    by_host = {}
    for r in raw:
        host = r.get("host") or r.get("url")
        by_host.setdefault(host, []).append(r)

    offers = []
    for host, rows in by_host.items():
        rows.sort(key=lambda o: (o.get("price_usd") is None, o.get("price_usd") if o.get("price_usd") is not None else 0.0))
        offers.extend(rows[:2])
    offers.sort(key=lambda o: (o.get("price_usd") is None, o.get("price_usd") if o.get("price_usd") is not None else 0.0))
    for o in offers:
        o.pop("host", None)
    out = offers[:limit]
    _VENDOR_OFFERS_CACHE[cache_key] = (now_s, list(out))
    return out

def _query_tokens(text: str):
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if len(t) > 1]

def _annotate_offer_match(offer: dict, query: str):
    q_tokens = _query_tokens(query)
    hay = f"{offer.get('title', '')} {offer.get('url', '')}".lower()
    matched = []
    missing = []
    for t in q_tokens:
        if t in hay:
            matched.append(t)
        else:
            missing.append(t)

    total = len(q_tokens) if q_tokens else 1
    score = int(round((len(matched) / total) * 100))
    out = dict(offer)
    out["match_score"] = score
    out["matched_terms"] = matched
    out["missing_terms"] = missing
    out["is_fallback_match"] = bool(missing)
    return out


def _normalize_code(text: str):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _build_code_pattern(code: str):
    c = (code or "").strip()
    if not c:
        return None
    parts = re.findall(r"[A-Za-z0-9]+", c)
    if not parts:
        return None
    body = r"[-_\s/]*".join(re.escape(p) for p in parts)
    pat = rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"
    try:
        return re.compile(pat, flags=re.IGNORECASE)
    except Exception:
        return None


def _offer_matches_required_code(offer: dict, code: str):
    required = (code or "").strip()
    if not required:
        return True
    pat = _build_code_pattern(required)
    title = str(offer.get("title", "") or "")
    url = str(offer.get("url", "") or "")
    hay = f"{title} {url}"
    if pat:
        return bool(pat.search(hay))
    required_norm = _normalize_code(required)
    if not required_norm:
        return True
    hay_norm = _normalize_code(hay)
    return required_norm in hay_norm

async def _web_vendor_fallback_matches(query: str, limit: int = 10):
    q = (query or "").strip()
    if not q:
        return []
    links = await _vendor_top_links(q, limit=max(limit * 4, 20))
    if not links:
        return [
            {
                "title": "Search results",
                "url": _search_results_url(q),
                "price_usd": None,
                "price_myr": None,
                "source": "Search",
            }
        ]
    out = []
    for u in links:
        try:
            host = (urlparse(u).hostname or "").lower()
        except Exception:
            host = ""
        if not host:
            continue
        if host.endswith("duckduckgo.com") or host.endswith("google.com") or host.endswith("bing.com"):
            continue
        title = host
        out.append(
            {
                "title": title,
                "url": u,
                "price_usd": None,
                "price_myr": None,
                "source": "Fallback",
            }
        )
        if len(out) >= limit:
            break
    return out


def _get_internal_vendor_offers(db: Session, sku_id: int, limit: int = 5):
    cutoff = datetime.utcnow() - timedelta(days=183)
    rows = (
        db.query(
            models.PartItem.vendor,
            func.min(models.PartItem.price_usd),
            func.min(models.PartItem.price_myr),
            func.count(models.PartItem.id),
            func.max(func.coalesce(models.PartItem.date_out, models.PartItem.created_at)),
        )
        .filter(
            models.PartItem.sku_id == sku_id,
            models.PartItem.vendor.isnot(None),
            models.PartItem.vendor != "",
            func.coalesce(models.PartItem.date_out, models.PartItem.created_at) >= cutoff,
        )
        .group_by(models.PartItem.vendor)
        .all()
    )
    offers = []
    for vendor, min_usd, min_myr, cnt, last_seen in rows:
        price_usd = float(min_usd) if min_usd is not None and float(min_usd) > 0 else None
        price_myr = float(min_myr) if min_myr is not None and float(min_myr) > 0 else None
        if price_usd is None and price_myr is None:
            continue
        age_days = None
        if last_seen:
            try:
                now = datetime.now(last_seen.tzinfo) if getattr(last_seen, "tzinfo", None) else datetime.utcnow()
                age_days = max(0, int((now - last_seen).total_seconds() // 86400))
            except Exception:
                age_days = None
        offers.append(
            {
                "title": str(vendor),
                "url": "",
                "price_usd": price_usd,
                "price_myr": price_myr,
                "count": int(cnt or 0),
                "age_days": age_days,
                "source": "History",
            }
        )

    offers.sort(key=lambda o: (o["price_usd"] is None, o["price_usd"] if o["price_usd"] is not None else 0.0))
    return offers[:limit]


@app.get("/parts/{part_id}/vendor-sources", response_class=HTMLResponse)
async def vendor_sources(
    part_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    if not str(part_id).isdigit():
        return RedirectResponse(url="/?error_msg=Invalid part id", status_code=status.HTTP_302_FOUND)
    part_id_int = int(part_id)

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id_int).first()
    if not part:
        return RedirectResponse(url="/?error_msg=Part not found", status_code=status.HTTP_302_FOUND)

    part_no = _primary_product_code(part.sku)
    base_query = " ".join([x for x in [part_no, (part.name or "").strip()] if x]).strip()
    vendor_query = _default_vendor_query(base_query)
    cfg = crud.get_config(db)
    rate = float(cfg.default_conversion_rate or 0.0) if cfg else 0.0

    history_offers = _get_internal_vendor_offers(db, sku_id=part_id_int, limit=12)
    web_offers = await _web_vendor_offers(vendor_query, myr_per_usd=rate, limit=8)
    merged = []
    seen_keys = set()
    for r in history_offers + web_offers:
        key = (str(r.get("title", "")).strip().lower(), str(r.get("url", "")).strip().lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(r)
    match_query = _simplify_vendor_match_query(base_query)
    merged = [_annotate_offer_match(o, match_query) for o in merged]

    if not merged:
        merged = await _web_vendor_fallback_matches(vendor_query, limit=12)

    merged = [_annotate_offer_match(o, match_query) for o in merged]
    merged = [_fill_offer_currency(o, rate) for o in merged]
    merged = [o for o in merged if _offer_has_price(o)]
    merged = [o for o in merged if _offer_has_hyperlink(o)]
    required_code = part_no
    if required_code:
        merged = [o for o in merged if (not o.get("url")) or _offer_matches_required_code(o, required_code)]
    merged.sort(
        key=lambda o: (
            o.get("price_usd") is None,
            o.get("price_usd") if o.get("price_usd") is not None else 0.0,
            -(o.get("match_score") or 0),
        )
    )
    results = merged[:10]
    priced_found = len(results)
    ai_summary = await _azure_vendor_ai_summary(base_query, part_no, results, rate)

    return templates.TemplateResponse(
        request,
        "vendor_sources.html",
        {
            "request": request,
            "user": user,
            "part": part,
            "query": vendor_query,
            "results": results,
            "priced_found": priced_found,
            "ai_summary": ai_summary,
        },
    )

@app.get("/parts/{part_id}/vendor-sources/offers")
async def vendor_sources_offers(
    part_id: int,
    request: Request,
    q: str | None = None,
    batch: int = 10,
    exclude_urls: str | None = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"offers": []}, status_code=status.HTTP_401_UNAUTHORIZED)

    batch = max(1, min(int(batch or 10), 30))

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if not part:
        return JSONResponse({"offers": []}, status_code=status.HTTP_404_NOT_FOUND)

    cfg = crud.get_config(db)
    rate = float(cfg.default_conversion_rate or 0.0) if cfg else 0.0

    query = (q or "").strip()
    if not query:
        part_no = _primary_product_code(part.sku)
        base_query = " ".join([x for x in [part_no, (part.name or "").strip()] if x]).strip()
        query = _default_vendor_query(base_query)

    exclude_set = set()
    if exclude_urls:
        for x in str(exclude_urls).split(","):
            u = x.strip()
            if u:
                exclude_set.add(u)

    desired = min(len(exclude_set) + batch + 10, 120)
    offers = await _web_vendor_offers_extended(
        query,
        myr_per_usd=rate,
        limit=desired,
        per_variant_limit=40,
        max_links=120,
    )
    if not offers:
        offers = await _web_vendor_fallback_matches(query, limit=max(batch * 2, 10))

    match_query = _simplify_vendor_match_query(query)
    annotated = [_annotate_offer_match(o, match_query) for o in offers]
    annotated = [_fill_offer_currency(o, rate) for o in annotated]
    annotated = [o for o in annotated if _offer_has_price(o)]
    annotated = [o for o in annotated if _offer_has_hyperlink(o)]
    required_code = _primary_product_code(part.sku)
    if required_code:
        annotated = [o for o in annotated if _offer_matches_required_code(o, required_code)]
    annotated.sort(
        key=lambda o: (
            o.get("price_usd") is None,
            o.get("price_usd") if o.get("price_usd") is not None else 0.0,
            -(o.get("match_score") or 0),
        )
    )
    fresh = [o for o in annotated if o.get("url") and o.get("url") not in exclude_set]
    return JSONResponse({"offers": fresh[:batch], "query": query})


@app.get("/machine-registry/{entry_id}/vendor-sources", response_class=HTMLResponse)
async def machine_vendor_sources(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true")

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return RedirectResponse(url="/?stock_tab=machine&error_msg=Record not found", status_code=status.HTTP_302_FOUND)

    base_query = (row.model_no or "").strip()
    vendor_query = base_query
    if not vendor_query:
        base_query = " ".join([x for x in [row.brand, row.product_type, row.category_type] if x])
        vendor_query = base_query
    vendor_query = _default_vendor_query(vendor_query)
    rate = float(row.conversion_rate or 0.0)
    if rate <= 0:
        cfg = crud.get_config(db)
        rate = float(cfg.default_conversion_rate or 0.0) if cfg else 0.0
    offers = await _web_vendor_offers_extended(
        vendor_query,
        myr_per_usd=rate,
        limit=10,
        per_variant_limit=25,
        max_links=80,
    )
    if not offers:
        offers = await _web_vendor_fallback_matches(vendor_query, limit=12)

    match_query = _simplify_vendor_match_query(base_query)
    annotated = [_annotate_offer_match(o, match_query) for o in offers]
    annotated = [_fill_offer_currency(o, rate) for o in annotated]
    annotated = [o for o in annotated if _offer_has_price(o)]
    annotated.sort(
        key=lambda o: (
            o.get("price_usd") is None,
            o.get("price_usd") if o.get("price_usd") is not None else 0.0,
            -(o.get("match_score") or 0),
        )
    )
    results = annotated[:10]
    priced_found = len(results)

    return templates.TemplateResponse(
        request,
        "machine_vendor_sources.html",
        {
            "request": request,
            "user": user,
            "item": row,
            "query": vendor_query,
            "results": results,
            "priced_found": priced_found,
        },
    )


@app.get("/machine-registry/{entry_id}/vendor-sources/offers")
async def machine_vendor_sources_offers(
    entry_id: int,
    request: Request,
    q: str | None = None,
    batch: int = 10,
    exclude_urls: str | None = None,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"offers": []}, status_code=status.HTTP_401_UNAUTHORIZED)

    batch = max(1, min(int(batch or 10), 30))

    row = crud.get_machine_registry_entry(db, entry_id)
    if not row:
        return JSONResponse({"offers": []}, status_code=status.HTTP_404_NOT_FOUND)

    rate = float(row.conversion_rate or 0.0)
    if rate <= 0:
        cfg = crud.get_config(db)
        rate = float(cfg.default_conversion_rate or 0.0) if cfg else 0.0

    query = (q or "").strip()
    if not query:
        query = (row.model_no or "").strip()
    if not query:
        query = " ".join([x for x in [row.brand, row.product_type, row.category_type] if x])
    if not (q or "").strip():
        query = _default_vendor_query(query)

    exclude_set = set()
    if exclude_urls:
        for x in str(exclude_urls).split(","):
            u = x.strip()
            if u:
                exclude_set.add(u)

    desired = min(len(exclude_set) + batch + 10, 120)
    offers = await _web_vendor_offers_extended(
        query,
        myr_per_usd=rate,
        limit=desired,
        per_variant_limit=40,
        max_links=120,
    )
    if not offers:
        offers = await _web_vendor_fallback_matches(query, limit=max(batch * 2, 10))

    match_query = _simplify_vendor_match_query(query)
    annotated = [_annotate_offer_match(o, match_query) for o in offers]
    annotated = [_fill_offer_currency(o, rate) for o in annotated]
    annotated = [o for o in annotated if _offer_has_price(o)]
    annotated.sort(
        key=lambda o: (
            o.get("price_usd") is None,
            o.get("price_usd") if o.get("price_usd") is not None else 0.0,
            -(o.get("match_score") or 0),
        )
    )
    fresh = [o for o in annotated if o.get("url") and o.get("url") not in exclude_set]
    return JSONResponse({"offers": fresh[:batch], "query": query})

@app.post("/parts/delete/{part_id}")
async def delete_part_ui(
    part_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        
    deleted = crud.delete_part(db, part_id)
    if deleted:
        crud.log_activity(db, user_id=user.id, action="DELETE_PART", part_id=part_id, description="Deleted part")
    return RedirectResponse(url="/?success_msg=Part deleted successfully", status_code=status.HTTP_302_FOUND)


@app.post("/parts/outgoing/{part_id}")
async def outgoing_part_ui(
    part_id: int,
    request: Request,
    item_ids: str = Form(""),
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    vendor: str = Form(None),
    price_usd: float = Form(0.0),
    price_myr: float = Form(0.0),
    conversion_rate: float = Form(0.0),
    part_status: str = Form("Other"),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login?session_expired=true", status_code=status.HTTP_302_FOUND)

    ids = [int(x) for x in item_ids.split(",") if x.strip().isdigit()]
    quantity = len(ids)

    if quantity <= 0:
        return RedirectResponse(url="/?error_msg=Please select at least one item", status_code=status.HTTP_302_FOUND)

    now = datetime.utcnow()
    serials = []
    if ids:
        items = (
            db.query(models.PartItem.id, models.PartItem.serial_no)
            .filter(models.PartItem.id.in_(ids))
            .all()
        )
        for item in items:
            serials.append(item.serial_no or f"Item #{item.id}")

    crud.update_part_items_flags(
        db,
        item_ids=ids,
        outgoing_flag=True,
        returning_flag=False,
        date_out=now,
        customer=client_name,
        engineer=engineer_name,
        vendor=vendor,
        price_usd=price_usd,
        price_myr=price_myr,
        conversion_rate=conversion_rate,
        remarks=remark,
    )

    crud.record_part_movement(
        db=db,
        part_id=part_id,
        user_id=user.id,
        movement_type="OUTGOING_PART",
        quantity=quantity,
        client_name=client_name,
        engineer_name=engineer_name,
        part_status=part_status,
        remark=remark,
        ticket_no=ticket_no,
        serials=serials,
        base_url=str(request.base_url),
    )

    return RedirectResponse(url="/?success_msg=Outgoing part recorded", status_code=status.HTTP_302_FOUND)


@app.post("/api/parts/{part_id}/stock-detail/outgoing")
async def stock_detail_outgoing_part_items(
    part_id: int,
    request: Request,
    item_ids: str = Form(""),
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    ids = [int(x) for x in item_ids.split(",") if x.strip().isdigit()]
    quantity = len(ids)

    if quantity <= 0:
        return JSONResponse({"success": False, "error": "Please select at least one item"}, status_code=status.HTTP_400_BAD_REQUEST)

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if not part:
        return JSONResponse({"success": False, "error": "Part not found"}, status_code=status.HTTP_404_NOT_FOUND)

    now = datetime.utcnow()
    serials = []
    if ids:
        items = (
            db.query(models.PartItem.id, models.PartItem.serial_no)
            .filter(models.PartItem.id.in_(ids))
            .all()
        )
        for item in items:
            serials.append(item.serial_no or f"Item #{item.id}")
    updated = crud.update_part_items_flags(
        db,
        item_ids=ids,
        outgoing_flag=True,
        returning_flag=False,
        date_out=now,
        customer=client_name,
        engineer=engineer_name,
        vendor=None,
        price_usd=None,
        price_myr=None,
        conversion_rate=None,
        remarks=remark,
    )

    crud.record_part_movement(
        db=db,
        part_id=part_id,
        user_id=user.id,
        movement_type="OUTGOING_PART",
        quantity=quantity,
        client_name=client_name,
        engineer_name=engineer_name,
        part_status="Outgoing",
        remark=remark,
        ticket_no=ticket_no,
        serials=serials,
        base_url=str(request.base_url),
    )

    return JSONResponse({"success": True, "quantity": quantity, "updated": updated})


@app.get("/parts/{part_id}/serials")
async def get_part_serials(
    part_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"serials": []}, status_code=status.HTTP_401_UNAUTHORIZED)

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    if not part:
        return JSONResponse({"serials": []})

    items = crud.get_part_items_for_sku(db, sku_id=part.id, outgoing_flag=False, returning_flag=False)
    serials = [i.serial_no or f"Item #{i.id}" for i in items]
    serials.sort()

    return JSONResponse({"serials": serials})


@app.get("/parts/{part_id}/items")
async def get_part_items(
    part_id: int,
    request: Request,
    type: str = "available",
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"items": []}, status_code=status.HTTP_401_UNAUTHORIZED)

    part = db.query(models.SkuPart).filter(models.SkuPart.id == part_id).first()
    print(part.name)
    if not part:
        return JSONResponse({"items": []})

    if type == "outgoing":
        items = crud.get_part_items_for_sku(db, sku_id=part.id, outgoing_flag=True, returning_flag=False)
    else:
        items = crud.get_part_items_for_sku(db, sku_id=part.id, outgoing_flag=False, returning_flag=False)


    return JSONResponse(
        {
            "items": [
                {
                    "id": item.id,
                    "serial_no": item.serial_no,
                    "vendor": item.vendor,
                    "created_at": item.created_at.isoformat() if getattr(item, "created_at", None) else None,
                    "price_usd": item.price_usd,
                    "price_myr": item.price_myr,
                    "unit_cost": item.unit_cost,
                    "outgoing_flag": item.outgoing_flag,
                    "returning_flag": item.returning_flag,
                }
                for item in items
            ]
        }
    )


@app.get("/part-items/{item_id}")
async def get_part_item_detail(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({}, status_code=status.HTTP_401_UNAUTHORIZED)

    item = db.query(models.PartItem).filter(models.PartItem.id == item_id).first()
    if not item:
        return JSONResponse({}, status_code=status.HTTP_404_NOT_FOUND)

    part = item.sku_part

    return JSONResponse(
        {
            "id": item.id,
            "serial_no": item.serial_no,
            "description": item.description,
            "date_out": item.date_out.isoformat() if item.date_out else None,
            "price_usd": item.price_usd,
            "price_myr": item.price_myr,
            "conversion_rate": item.conversion_rate,
            "customer": item.customer,
            "engineer": item.engineer,
            "vendor": item.vendor,
            "remarks": item.remarks,
            "outgoing_flag": item.outgoing_flag,
            "returning_flag": item.returning_flag,
            "sku": part.sku if part else None,
            "part_name": part.name if part else None,
            "rack_type": part.rack_type if part else None,
            "tray_type": part.tray_type if part else None,
            "platform": part.platform if part else None,
            "created_at": item.created_at.isoformat() if getattr(item, "created_at", None) else None,
            "unit_cost": item.unit_cost,
        }
    )


@app.post("/part-items/{item_id}/outgoing")
async def outgoing_part_item(
    item_id: int,
    request: Request,
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    item = db.query(models.PartItem).filter(models.PartItem.id == item_id).first()
    if not item:
        return JSONResponse({"success": False, "error": "Item not found"}, status_code=status.HTTP_404_NOT_FOUND)

    now = datetime.utcnow()

    crud.update_part_items_flags(
        db,
        item_ids=[item.id],
        outgoing_flag=True,
        returning_flag=False,
        date_out=now,
        customer=client_name,
        engineer=engineer_name,
        vendor=item.vendor,
        price_usd=item.price_usd,
        price_myr=item.price_myr,
        conversion_rate=item.conversion_rate,
        remarks=remark,
    )

    crud.record_part_movement(
        db=db,
        part_id=item.sku_id,
        user_id=user.id,
        movement_type="OUTGOING_PART",
        quantity=1,
        client_name=client_name,
        engineer_name=engineer_name,
        part_status="Outgoing",
        remark=remark,
        ticket_no=ticket_no,
        serials=[item.serial_no or f"Item #{item.id}"],
        base_url=str(request.base_url),
    )

    return JSONResponse({"success": True})


@app.post("/part-items/{item_id}/edit")
async def edit_part_item(
    item_id: int,
    request: Request,
    serial_no: str = Form(None),
    description: str = Form(None),
    customer: str = Form(None),
    engineer: str = Form(None),
    vendor: str = Form(None),
    price_usd: float = Form(None),
    price_myr: float = Form(None),
    conversion_rate: float = Form(None),
    remarks: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    item = db.query(models.PartItem).filter(models.PartItem.id == item_id).first()
    if not item:
        return JSONResponse({"success": False, "error": "Item not found"}, status_code=status.HTTP_404_NOT_FOUND)

    if serial_no is not None:
        serial_no = serial_no.strip() or None
        if serial_no:
            existing_serial = (
                db.query(models.PartItem)
                .filter(models.PartItem.serial_no == serial_no, models.PartItem.id != item.id)
                .first()
            )
            if existing_serial:
                return JSONResponse(
                    {"success": False, "error": "Serial No already exists. Please use a different Serial No."},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        item.serial_no = serial_no

    if description is not None:
        item.description = description
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

    db.commit()
    db.refresh(item)

    return JSONResponse({"success": True})

@app.post("/parts/returning-faulty/{part_id}")
async def returning_faulty_part_ui(
    part_id: int,
    request: Request,
    item_ids: str = Form(""),
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    vendor: str = Form(None),
    price_usd: float = Form(0.0),
    price_myr: float = Form(0.0),
    conversion_rate: float = Form(0.0),
    part_status: str = Form("Faulty"),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    ids = [int(x) for x in item_ids.split(",") if x.strip().isdigit()]
    quantity = len(ids)

    if quantity <= 0:
        return RedirectResponse(url="/?error_msg=Please select at least one item", status_code=status.HTTP_302_FOUND)

    serials = []
    if ids:
        items = (
            db.query(models.PartItem.id, models.PartItem.serial_no)
            .filter(models.PartItem.id.in_(ids))
            .all()
        )
        for item in items:
            serials.append(item.serial_no or f"Item #{item.id}")

    is_faulty = (part_status or "").strip().lower() == "faulty"
    crud.update_part_items_flags(
        db,
        item_ids=ids,
        outgoing_flag=False,
        returning_flag=is_faulty,
        customer=client_name,
        engineer=engineer_name,
        vendor=vendor,
        price_usd=price_usd,
        price_myr=price_myr,
        conversion_rate=conversion_rate,
        remarks=remark,
    )

    crud.record_part_movement(
        db=db,
        part_id=part_id,
        user_id=user.id,
        movement_type="RETURNING_FAULTY" if is_faulty else "RETURNING_PART",
        quantity=quantity,
        client_name=client_name,
        engineer_name=engineer_name,
        part_status=part_status,
        remark=remark,
        ticket_no=ticket_no,
        serials=serials,
    )

    return RedirectResponse(url="/?success_msg=Returning recorded", status_code=status.HTTP_302_FOUND)


@app.post("/parts/returning/{part_id}")
async def returning_part_ui(
    part_id: int,
    request: Request,
    item_ids: str = Form(""),
    client_name: str = Form(None),
    engineer_name: str = Form(None),
    vendor: str = Form(None),
    price_usd: float = Form(0.0),
    price_myr: float = Form(0.0),
    conversion_rate: float = Form(0.0),
    part_status: str = Form("Other"),
    remark: str = Form(None),
    ticket_no: str = Form(None),
    db: Session = Depends(get_db),
):
    user = await auth.get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    ids = [int(x) for x in item_ids.split(",") if x.strip().isdigit()]
    quantity = len(ids)
    if quantity <= 0:
        return RedirectResponse(url="/?error_msg=Please select at least one item", status_code=status.HTTP_302_FOUND)

    serials = []
    if ids:
        items = (
            db.query(models.PartItem.id, models.PartItem.serial_no)
            .filter(models.PartItem.id.in_(ids))
            .all()
        )
        for item in items:
            serials.append(item.serial_no or f"Item #{item.id}")

    is_faulty = (part_status or "").strip().lower() == "faulty"

    crud.update_part_items_flags(
        db,
        item_ids=ids,
        outgoing_flag=False,
        returning_flag=is_faulty,
        customer=client_name,
        engineer=engineer_name,
        vendor=vendor,
        price_usd=price_usd,
        price_myr=price_myr,
        conversion_rate=conversion_rate,
        remarks=remark,
    )

    crud.record_part_movement(
        db=db,
        part_id=part_id,
        user_id=user.id,
        movement_type="RETURNING_FAULTY" if is_faulty else "RETURNING_PART",
        quantity=quantity,
        client_name=client_name,
        engineer_name=engineer_name,
        part_status=part_status,
        remark=remark,
        ticket_no=ticket_no,
        serials=serials,
    )

    return RedirectResponse(url="/?success_msg=Returning recorded", status_code=status.HTTP_302_FOUND)
