from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)


class SkuPart(Base):
    __tablename__ = "sku_parts"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sku = Column(String, index=True)
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    part_category = Column(String, nullable=True)

    rack_type = Column(String, nullable=True)
    tray_type = Column(String, nullable=True)
    platform = Column(String, nullable=True)

    criticality = Column(String, default="Medium")
    annual_demand = Column(Integer, default=0)

    items = relationship("PartItem", back_populates="sku_part", cascade="all, delete-orphan")
    meta = relationship("SkuPartMeta", back_populates="sku_part", uselist=False, cascade="all, delete-orphan")

    @property
    def current_stock(self):
        if not self.items:
            return 0
        return sum(
            1
            for item in self.items
            if not item.outgoing_flag and not item.returning_flag
        )

    @property
    def unit_cost(self):
        item = self._eoq_source_item
        return float(item.unit_cost) if item and item.unit_cost is not None else 0.0

    @property
    def lead_time_days(self):
        item = self._eoq_source_item
        return int(item.lead_time_days) if item and item.lead_time_days is not None else 0

    @property
    def holding_cost_percentage(self):
        item = self._eoq_source_item
        return float(item.holding_cost_percentage) if item and item.holding_cost_percentage is not None else 0.0

    @property
    def shipping_cost(self):
        item = self._eoq_source_item
        return float(item.shipping_cost) if item and item.shipping_cost is not None else 0.0

    @property
    def eoq_min_threshold(self):
        item = self._eoq_source_item
        return int(item.eoq_min_threshold) if item and item.eoq_min_threshold is not None else 0

    @property
    def _eoq_source_item(self):
        if not self.items:
            return None
        for item in self.items:
            if not item.outgoing_flag and not item.returning_flag:
                return item
        return self.items[0]

    @property
    def eoq(self):
        item = self._eoq_source_item
        default_eoq = 1
        
        if not item:
            return default_eoq

        unit = float(item.unit_cost or 0.0)
        holding = float(item.holding_cost_percentage or 0.0)
        demand = int((item.annual_demand if item.annual_demand is not None else self.annual_demand) or 0)
        shipping = float(item.shipping_cost or 0.0)

        if unit <= 0 or holding <= 0 or demand <= 0 or shipping <= 0:
            return default_eoq

        holding_cost_per_unit = unit * holding
        if holding_cost_per_unit <= 0:
            return default_eoq

        try:
            eoq_val = (2 * demand * shipping) / holding_cost_per_unit
            if eoq_val <= 0:
                return default_eoq
            val = int(eoq_val ** 0.5)
            return val if val > 0 else default_eoq
        except ZeroDivisionError:
            return default_eoq


class SkuPartMeta(Base):
    __tablename__ = "sku_part_meta"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sku_id = Column(Integer, ForeignKey("sku_parts.id"), index=True, nullable=False, unique=True)
    customer_usage = Column(String, nullable=True)
    image_filename = Column(String, nullable=True)

    sku_part = relationship("SkuPart", back_populates="meta")


class PartItem(Base):
    __tablename__ = "part_items"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sku_id = Column(Integer, ForeignKey("sku_parts.id"), index=True, nullable=False)
    vendor = Column(String, nullable=True)
    serial_no = Column(String, nullable=True, index=True)
    description = Column(String, nullable=True)
    outgoing_flag = Column(Boolean, default=False)
    returning_flag = Column(Boolean, default=False)
    date_out = Column(DateTime(timezone=True), nullable=True)
    price_usd = Column(Float, default=0.0)
    price_myr = Column(Float, default=0.0)
    conversion_rate = Column(Float, default=0.0)
    customer = Column(String, nullable=True)
    engineer = Column(String, nullable=True)
    remarks = Column(String, nullable=True)
    lead_time_days = Column(Integer, default=7)
    unit_cost = Column(Float, default=0.0)
    holding_cost_percentage = Column(Float, default=0.20)
    annual_demand = Column(Integer, default=0)
    shipping_cost = Column(Float, default=0.0)
    eoq_min_threshold = Column(Integer, default=0)

    sku_part = relationship("SkuPart", back_populates="items")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, index=True)
    part_id = Column(Integer, ForeignKey("sku_parts.id"), nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
    part = relationship("SkuPart")


class ClientFaultyLog(Base):
    __tablename__ = "client_faulty_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    part_id = Column(Integer, ForeignKey("sku_parts.id"), nullable=True)

    client_name = Column(String, nullable=True, index=True)
    engineer_name = Column(String, nullable=True)

    part_no = Column(String, nullable=True, index=True)
    part_description = Column(String, nullable=True)
    serial_no = Column(String, nullable=True, index=True)

    part_status = Column(String, nullable=True)
    remark = Column(String, nullable=True)
    ticket_no = Column(String, nullable=True, index=True)
    disposed_flag = Column(Boolean, default=False)
    disposed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    part = relationship("SkuPart")


class MachineCategory(Base):
    __tablename__ = "machine_categories"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    name = Column(String, nullable=False, unique=True, index=True)
    description = Column(String, nullable=True)


class SkuPartMachineCategory(Base):
    __tablename__ = "sku_part_machine_categories"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sku_id = Column(Integer, ForeignKey("sku_parts.id"), index=True, nullable=False, unique=True)
    machine_category_id = Column(Integer, ForeignKey("machine_categories.id"), index=True, nullable=False)

    sku_part = relationship("SkuPart")
    machine_category = relationship("MachineCategory")


class MachineRegistry(Base):
    __tablename__ = "machine_registry"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category_type = Column(String, nullable=False, index=True)

    serial_number = Column(String, nullable=True, index=True)
    brand = Column(String, nullable=True)
    model_no = Column(String, nullable=True, index=True)
    product_type = Column(String, nullable=True)

    rack_level = Column(String, nullable=True)
    room_no = Column(String, nullable=True)
    rack_type = Column(String, nullable=True)
    tray_type = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    criticality = Column(String, nullable=True, default="Medium")
    customer = Column(String, nullable=True)

    cpu_count = Column(String, nullable=True)
    core_count_speed = Column(String, nullable=True)
    memory_spec = Column(String, nullable=True)
    disk_drives = Column(String, nullable=True)
    nic_card = Column(String, nullable=True)
    adapter_card = Column(String, nullable=True)
    power_supply = Column(String, nullable=True)
    tape_drives = Column(String, nullable=True)
    network_ports = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    image_filename = Column(String, nullable=True)

    price_usd = Column(Float, default=0.0)
    conversion_rate = Column(Float, default=0.0)
    price_myr = Column(Float, default=0.0)
    lead_time_days = Column(Integer, default=7)
    holding_cost_percentage = Column(Float, default=0.20)
    annual_demand = Column(Integer, default=0)
    shipping_cost = Column(Float, default=0.0)

    @property
    def eoq(self):
        default_eoq = 1
        unit = float(self.price_myr or 0.0)
        holding = float(self.holding_cost_percentage or 0.0)
        demand = int(self.annual_demand or 0)
        shipping = float(self.shipping_cost or 0.0)

        if holding > 1:
            holding = holding / 100.0

        if unit <= 0 or holding <= 0 or demand <= 0 or shipping <= 0:
            return default_eoq

        holding_cost_per_unit = unit * holding
        if holding_cost_per_unit <= 0:
            return default_eoq

        try:
            eoq_val = (2 * demand * shipping) / holding_cost_per_unit
            if eoq_val <= 0:
                return default_eoq
            val = int(eoq_val ** 0.5)
            return val if val > 0 else default_eoq
        except ZeroDivisionError:
            return default_eoq

    serials = relationship("MachineRegistrySerial", back_populates="registry", cascade="all, delete-orphan")


class MachineRegistrySerial(Base):
    __tablename__ = "machine_registry_serials"
    __table_args__ = (UniqueConstraint("registry_id", "serial_no", name="uq_machine_registry_serial"),)

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    registry_id = Column(Integer, ForeignKey("machine_registry.id"), index=True, nullable=False)
    serial_no = Column(String, nullable=False, index=True)

    outgoing_flag = Column(Boolean, default=False)
    date_out = Column(DateTime(timezone=True), nullable=True)
    customer = Column(String, nullable=True)
    engineer = Column(String, nullable=True)
    remarks = Column(String, nullable=True)
    ticket_no = Column(String, nullable=True)

    registry = relationship("MachineRegistry", back_populates="serials")


class Config(Base):
    __tablename__ = "config"

    id = Column(Integer, primary_key=True, index=True)
    email_sender = Column(String, nullable=True)
    smtp_server = Column(String, nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_use_tls = Column(Boolean, default=True)
    smtp_username = Column(String, nullable=True)
    smtp_password = Column(String, nullable=True)
    notification_email = Column(String, nullable=True)
    default_conversion_rate = Column(Float, default=0.0)
