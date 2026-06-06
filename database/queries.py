from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy import select, func, and_, text, Float, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from config import config
from database.models import Base, User, UserRole, CementReceipt, CementConsumption, StockAdjustment, StockSettings, ManagementRecipient, AuditLog, AdjustmentType

_db_url = config.DATABASE_URL
if _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Engine with pooling for PostgreSQL, simple for SQLite
if _db_url.startswith("postgresql+asyncpg://"):
    engine = create_async_engine(
        _db_url,
        echo=False,
        pool_size=config.DB_POOL_SIZE,
        max_overflow=config.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
    )
else:
    engine = create_async_engine(_db_url, echo=False)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await _seed_defaults(session)

async def _seed_defaults(session):
    defaults = {
        "low_stock_threshold": str(config.DEFAULT_LOW_STOCK_KG),
        "current_stock": "0",
    }
    for k, v in defaults.items():
        if not await session.scalar(select(StockSettings).where(StockSettings.key == k)):
            session.add(StockSettings(key=k, value=v))
    await session.commit()

async def _update_stock_atomic(session, delta):
    stmt = update(StockSettings).where(StockSettings.key == "current_stock").values(value=func.cast(StockSettings.value, Float) + delta).returning(StockSettings.value)
    result = await session.execute(stmt)
    await session.commit()
    return float(result.scalar_one())

async def get_setting(session, key):
    row = await session.scalar(select(StockSettings).where(StockSettings.key == key))
    return row.value if row else None

async def set_setting(session, key, value, updated_by=None):
    if key in ("current_stock","low_stock_threshold"):
        try: float(value)
        except: raise ValueError(f"{key} must be numeric")
    row = await session.scalar(select(StockSettings).where(StockSettings.key == key))
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
        row.updated_by = updated_by
    else:
        session.add(StockSettings(key=key, value=value, updated_by=updated_by))
    await session.commit()

async def get_current_stock(session):
    v = await get_setting(session, "current_stock")
    return float(v) if v else 0.0

async def get_low_stock_threshold(session):
    v = await get_setting(session, "low_stock_threshold")
    return float(v) if v else config.DEFAULT_LOW_STOCK_KG

# User functions (unchanged)
async def get_user_by_telegram_id(session, tid):
    return await session.scalar(select(User).where(User.telegram_id == tid))

async def create_user(session, telegram_id, full_name, role, username=None, created_by=None):
    u = User(telegram_id=telegram_id, full_name=full_name, role=role, username=username, created_by=created_by)
    session.add(u); await session.commit(); await session.refresh(u); return u

async def get_all_users(session):
    return (await session.execute(select(User).order_by(User.created_at))).scalars().all()

async def deactivate_user(session, tid):
    u = await get_user_by_telegram_id(session, tid)
    if u: u.is_active = False; await session.commit(); return True
    return False

async def update_user_role(session, tid, role):
    u = await get_user_by_telegram_id(session, tid)
    if u: u.role = role; await session.commit(); return True
    return False

# Receipt – with pagination
async def add_receipt(session, storekeeper_id, supplier_name, truck_number, quantity_kg):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    new_stock = await _update_stock_atomic(session, quantity_kg)
    r = CementReceipt(storekeeper_id=storekeeper_id, supplier_name=supplier_name, truck_number=truck_number, quantity_kg=quantity_kg, stock_after=new_stock, note=None)
    session.add(r); await session.commit(); await session.refresh(r); return r, new_stock

async def get_receipts(session, user_id=None, offset=0, limit=10):
    q = select(CementReceipt).order_by(CementReceipt.timestamp.desc()).offset(offset).limit(limit)
    if user_id:
        q = q.where(CementReceipt.storekeeper_id == user_id)
    return (await session.execute(q)).scalars().all()

async def get_receipts_count(session, user_id=None):
    q = select(func.count(CementReceipt.id))
    if user_id:
        q = q.where(CementReceipt.storekeeper_id == user_id)
    return await session.scalar(q) or 0

# Consumption – with pagination
async def add_consumption(session, operator_id, quantity_kg, cubic_meters=None, kg_per_m3=None, project_name=None, concrete_grade=None):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    current = await get_current_stock(session)
    if quantity_kg > current: raise ValueError(f"Insufficient stock. Available: {current:,.0f} kg")
    new_stock = await _update_stock_atomic(session, -quantity_kg)
    c = CementConsumption(operator_id=operator_id, quantity_kg=quantity_kg, cubic_meters=cubic_meters, kg_per_m3=kg_per_m3, project_name=project_name, concrete_grade=concrete_grade, stock_after=new_stock, note=None)
    session.add(c); await session.commit(); await session.refresh(c); return c, new_stock

async def get_consumptions(session, user_id=None, offset=0, limit=10):
    q = select(CementConsumption).order_by(CementConsumption.timestamp.desc()).offset(offset).limit(limit)
    if user_id:
        q = q.where(CementConsumption.operator_id == user_id)
    return (await session.execute(q)).scalars().all()

async def get_consumptions_count(session, user_id=None):
    q = select(func.count(CementConsumption.id))
    if user_id:
        q = q.where(CementConsumption.operator_id == user_id)
    return await session.scalar(q) or 0

# Adjustment (unchanged)
async def add_adjustment(session, admin_id, adjustment_type, quantity_kg, reason):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    delta = quantity_kg if adjustment_type == AdjustmentType.ADD else -quantity_kg
    if adjustment_type == AdjustmentType.DEDUCT:
        current = await get_current_stock(session)
        if quantity_kg > current: raise ValueError(f"Cannot deduct more than current stock ({current:,.0f} kg)")
    new_stock = await _update_stock_atomic(session, delta)
    a = StockAdjustment(admin_id=admin_id, adjustment_type=adjustment_type, quantity_kg=quantity_kg, reason=reason, stock_after=new_stock)
    session.add(a); await session.commit(); await session.refresh(a); return a, new_stock

# Reports (unchanged)
async def get_daily_summary(session, start_utc, end_utc):
    r = (await session.scalar(select(func.sum(CementReceipt.quantity_kg)).where(and_(CementReceipt.timestamp>=start_utc, CementReceipt.timestamp<end_utc)))) or 0.0
    c = (await session.scalar(select(func.sum(CementConsumption.quantity_kg)).where(and_(CementConsumption.timestamp>=start_utc, CementConsumption.timestamp<end_utc)))) or 0.0
    m3 = (await session.scalar(select(func.sum(CementConsumption.cubic_meters)).where(and_(CementConsumption.timestamp>=start_utc, CementConsumption.timestamp<end_utc)))) or 0.0
    add_adj = (await session.scalar(select(func.sum(StockAdjustment.quantity_kg)).where(and_(StockAdjustment.timestamp>=start_utc, StockAdjustment.timestamp<end_utc, StockAdjustment.adjustment_type==AdjustmentType.ADD)))) or 0.0
    sub_adj = (await session.scalar(select(func.sum(StockAdjustment.quantity_kg)).where(and_(StockAdjustment.timestamp>=start_utc, StockAdjustment.timestamp<end_utc, StockAdjustment.adjustment_type==AdjustmentType.DEDUCT)))) or 0.0
    cur = await get_current_stock(session)
    return {"received":r, "consumed":c, "cubic_meters":m3, "adjusted_add":add_adj, "adjusted_deduct":sub_adj, "closing_stock":cur}

async def get_monthly_summary(session, year, month):
    start = datetime(year, month, 1)
    end = datetime(year+1,1,1) if month==12 else datetime(year, month+1, 1)
    r = (await session.scalar(select(func.sum(CementReceipt.quantity_kg)).where(and_(CementReceipt.timestamp>=start, CementReceipt.timestamp<end)))) or 0.0
    c = (await session.scalar(select(func.sum(CementConsumption.quantity_kg)).where(and_(CementConsumption.timestamp>=start, CementConsumption.timestamp<end)))) or 0.0
    m3 = (await session.scalar(select(func.sum(CementConsumption.cubic_meters)).where(and_(CementConsumption.timestamp>=start, CementConsumption.timestamp<end)))) or 0.0
    add_adj = (await session.scalar(select(func.sum(StockAdjustment.quantity_kg)).where(and_(StockAdjustment.timestamp>=start, StockAdjustment.timestamp<end, StockAdjustment.adjustment_type==AdjustmentType.ADD)))) or 0.0
    sub_adj = (await session.scalar(select(func.sum(StockAdjustment.quantity_kg)).where(and_(StockAdjustment.timestamp>=start, StockAdjustment.timestamp<end, StockAdjustment.adjustment_type==AdjustmentType.DEDUCT)))) or 0.0
    cur = await get_current_stock(session)
    return {"year":year,"month":month,"received":r,"consumed":c,"cubic_meters":m3,"adjusted_add":add_adj,"adjusted_deduct":sub_adj,"closing_stock":cur}

async def get_monthly_details(session, year, month):
    start = datetime(year, month, 1)
    end = datetime(year+1,1,1) if month==12 else datetime(year, month+1, 1)
    receipts = (await session.execute(select(CementReceipt).where(and_(CementReceipt.timestamp>=start, CementReceipt.timestamp<end)).order_by(CementReceipt.timestamp))).scalars().all()
    consumptions = (await session.execute(select(CementConsumption).where(and_(CementConsumption.timestamp>=start, CementConsumption.timestamp<end)).order_by(CementConsumption.timestamp))).scalars().all()
    return {"receipts":receipts, "consumptions":consumptions, "year":year, "month":month}

# Recipients
async def add_recipient(session, telegram_id, label, is_group=False, added_by=None):
    existing = await session.scalar(select(ManagementRecipient).where(ManagementRecipient.telegram_id == telegram_id))
    if existing: existing.label=label; existing.is_group=is_group; existing.is_active=True; await session.commit(); return existing
    r = ManagementRecipient(telegram_id=telegram_id, label=label, is_group=is_group, is_active=True, added_by=added_by)
    session.add(r); await session.commit(); await session.refresh(r); return r

async def get_active_recipients(session):
    return (await session.execute(select(ManagementRecipient).where(ManagementRecipient.is_active == True))).scalars().all()

async def remove_recipient(session, telegram_id):
    r = await session.scalar(select(ManagementRecipient).where(ManagementRecipient.telegram_id == telegram_id))
    if r: r.is_active = False; await session.commit(); return True
    return False

async def list_recipients(session):
    return (await session.execute(select(ManagementRecipient))).scalars().all()

# Audit log with pagination
async def add_audit_log(session, telegram_id, action, details=None, user_id=None):
    session.add(AuditLog(user_id=user_id, telegram_id=telegram_id, action=action, details=details))
    await session.commit()

async def get_audit_logs(session, offset=0, limit=20):
    return (await session.execute(select(AuditLog).order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit))).scalars().all()

async def get_audit_logs_count(session):
    return await session.scalar(select(func.count(AuditLog.id))) or 0

# Cooldown
async def get_last_low_stock_alert(session):
    v = await get_setting(session, "last_low_stock_alert")
    return datetime.fromisoformat(v) if v else None

async def set_last_low_stock_alert(session, dt):
    if dt is None:
        row = await session.scalar(select(StockSettings).where(StockSettings.key == "last_low_stock_alert"))
        if row: await session.delete(row); await session.commit()
    else:
        await set_setting(session, "last_low_stock_alert", dt.isoformat())

# Pagination count helpers
async def get_receipts_count(session, user_id=None) -> int:
    q = select(func.count(CementReceipt.id))
    if user_id:
        q = q.where(CementReceipt.storekeeper_id == user_id)
    return await session.scalar(q) or 0

async def get_consumptions_count(session, user_id=None) -> int:
    q = select(func.count(CementConsumption.id))
    if user_id:
        q = q.where(CementConsumption.operator_id == user_id)
    return await session.scalar(q) or 0

async def get_audit_logs_count(session) -> int:
    return await session.scalar(select(func.count(AuditLog.id))) or 0
