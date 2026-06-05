from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class UserRole(str, PyEnum):
    ADMIN = "admin"
    STOREKEEPER = "storekeeper"
    OPERATOR = "operator"
    MANAGEMENT = "management"

class AdjustmentType(str, PyEnum):
    ADD = "add"
    DEDUCT = "deduct"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(100))
    full_name = Column(String(200), nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(BigInteger)
    receipts = relationship("CementReceipt", back_populates="storekeeper")
    consumptions = relationship("CementConsumption", back_populates="operator")
    adjustments = relationship("StockAdjustment", back_populates="admin")
    audit_logs = relationship("AuditLog", back_populates="user")

class CementReceipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True)
    storekeeper_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    supplier_name = Column(String(200), nullable=False)
    truck_number = Column(String(50), nullable=False)
    quantity_kg = Column(Float, nullable=False)
    stock_after = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    note = Column(Text)
    storekeeper = relationship("User", back_populates="receipts")

class CementConsumption(Base):
    __tablename__ = "consumption"
    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quantity_kg = Column(Float, nullable=False)
    cubic_meters = Column(Float)
    kg_per_m3 = Column(Float)
    project_name = Column(String(200))
    concrete_grade = Column(String(20))
    stock_after = Column(Float, nullable=False)
    note = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    operator = relationship("User", back_populates="consumptions")

class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    adjustment_type = Column(Enum(AdjustmentType), nullable=False)
    quantity_kg = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    stock_after = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    admin = relationship("User", back_populates="adjustments")

class StockSettings(Base):
    __tablename__ = "stock_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(String(500), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(BigInteger)

class ManagementRecipient(Base):
    __tablename__ = "management_recipients"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    label = Column(String(200), nullable=False)
    is_group = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    added_by = Column(BigInteger)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    telegram_id = Column(BigInteger, nullable=False)
    action = Column(String(200), nullable=False)
    details = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    user = relationship("User", back_populates="audit_logs")
