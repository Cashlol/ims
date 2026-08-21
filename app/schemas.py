from datetime import datetime
from pydantic import BaseModel, EmailStr
from typing import Optional


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    is_active: Optional[bool] = True
    is_admin: Optional[bool] = False


class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: int

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None


class SkuPartBase(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    part_category: Optional[str] = None
    rack_type: Optional[str] = None
    tray_type: Optional[str] = None
    platform: Optional[str] = None
    annual_demand: int = 0
    criticality: str = "Medium"


class SkuPartCreate(SkuPartBase):
    pass


class SkuPartUpdate(SkuPartBase):
    pass


class SkuPart(SkuPartBase):
    id: int

    class Config:
        from_attributes = True


class PartItemBase(BaseModel):
    sku_id: int
    vendor: Optional[str] = None
    serial_no: Optional[str] = None
    description: Optional[str] = None
    outgoing_flag: bool = False
    returning_flag: bool = False
    date_out: Optional[datetime] = None
    price_usd: float = 0.0
    price_myr: float = 0.0
    conversion_rate: float = 0.0
    customer: Optional[str] = None
    engineer: Optional[str] = None
    remarks: Optional[str] = None
    lead_time_days: int = 7
    unit_cost: float = 0.0
    holding_cost_percentage: float = 0.20
    annual_demand: int = 0
    shipping_cost: float = 0.0
    eoq_min_threshold: int = 0


class PartItemCreate(PartItemBase):
    pass


class PartItemUpdate(BaseModel):
    serial_no: Optional[str] = None
    description: Optional[str] = None
    outgoing_flag: Optional[bool] = None
    returning_flag: Optional[bool] = None


class PartItem(PartItemBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ActivityLog(BaseModel):
    id: int
    user_id: Optional[int] = None
    action: str
    part_id: Optional[int] = None
    description: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ConfigBase(BaseModel):
    email_sender: Optional[EmailStr] = None
    smtp_server: Optional[str] = None
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: Optional[EmailStr] = None
    smtp_password: Optional[str] = None
    notification_email: Optional[str] = None
    default_conversion_rate: float = 0.0


class ConfigUpdate(ConfigBase):
    pass


class Config(ConfigBase):
    id: int

    class Config:
        from_attributes = True
