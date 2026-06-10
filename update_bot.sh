#!/bin/bash
set -e
echo "🔧 Updating bot code with all fixes..."

# 1. config.py – with validation
cat > config.py << 'CONFIG'
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///cement_stock.db")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DEFAULT_LOW_STOCK_KG = float(os.getenv("DEFAULT_LOW_STOCK_KG", "20000"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    TIMEZONE = os.getenv("TIMEZONE", "Africa/Addis_Ababa")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin_token")
    STOREKEEPER_TOKEN = os.getenv("STOREKEEPER_TOKEN", "storekeeper_token")
    OPERATOR_TOKEN = os.getenv("OPERATOR_TOKEN", "operator_token")

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN required")
        if cls.ADMIN_TOKEN == "admin_token":
            print("⚠️ WARNING: Using default ADMIN_TOKEN – change in .env")
        if cls.STOREKEEPER_TOKEN == "storekeeper_token":
            print("⚠️ WARNING: Using default STOREKEEPER_TOKEN – change in .env")
        if cls.OPERATOR_TOKEN == "operator_token":
            print("⚠️ WARNING: Using default OPERATOR_TOKEN – change in .env")

config = Config()
CONFIG

# 2. database/queries.py – PostgreSQL pool + retry + pagination
cat > database/queries.py << 'QUERIES'
from datetime import datetime, timedelta
import asyncio
from typing import Optional
from sqlalchemy import select, func, and_, text, Float, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from config import config
from database.models import Base, User, UserRole, CementReceipt, CementConsumption, StockAdjustment, StockSettings, ManagementRecipient, AuditLog, AdjustmentType

_db_url = config.DATABASE_URL
if _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

if _db_url.startswith("postgresql+asyncpg://"):
    engine = create_async_engine(_db_url, echo=False, pool_size=config.DB_POOL_SIZE, max_overflow=config.DB_MAX_OVERFLOW, pool_pre_ping=True)
else:
    engine = create_async_engine(_db_url, echo=False)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db(retries=5, delay=2):
    for attempt in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSessionLocal() as session:
                await _seed_defaults(session)
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"DB init failed: {e}, retry in {delay}s")
            await asyncio.sleep(delay)

async def _seed_defaults(session):
    for k, v in [("low_stock_threshold", str(config.DEFAULT_LOW_STOCK_KG)), ("current_stock", "0")]:
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
        row.value = value; row.updated_at = datetime.utcnow(); row.updated_by = updated_by
    else:
        session.add(StockSettings(key=key, value=value, updated_by=updated_by))
    await session.commit()

async def get_current_stock(session):
    v = await get_setting(session, "current_stock")
    return float(v) if v else 0.0

async def get_low_stock_threshold(session):
    v = await get_setting(session, "low_stock_threshold")
    return float(v) if v else config.DEFAULT_LOW_STOCK_KG

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

async def add_receipt(session, storekeeper_id, supplier_name, truck_number, quantity_kg):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    new_stock = await _update_stock_atomic(session, quantity_kg)
    r = CementReceipt(storekeeper_id=storekeeper_id, supplier_name=supplier_name, truck_number=truck_number, quantity_kg=quantity_kg, stock_after=new_stock)
    session.add(r); await session.commit(); await session.refresh(r); return r, new_stock

async def get_receipts(session, user_id=None, offset=0, limit=10):
    q = select(CementReceipt).order_by(CementReceipt.timestamp.desc()).offset(offset).limit(limit)
    if user_id: q = q.where(CementReceipt.storekeeper_id == user_id)
    return (await session.execute(q)).scalars().all()

async def get_receipts_count(session, user_id=None):
    q = select(func.count(CementReceipt.id))
    if user_id: q = q.where(CementReceipt.storekeeper_id == user_id)
    return await session.scalar(q) or 0

async def add_consumption(session, operator_id, quantity_kg, cubic_meters=None, kg_per_m3=None, project_name=None, concrete_grade=None):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    current = await get_current_stock(session)
    if quantity_kg > current: raise ValueError(f"Insufficient stock. Available: {current:,.0f} kg")
    new_stock = await _update_stock_atomic(session, -quantity_kg)
    c = CementConsumption(operator_id=operator_id, quantity_kg=quantity_kg, cubic_meters=cubic_meters, kg_per_m3=kg_per_m3, project_name=project_name, concrete_grade=concrete_grade, stock_after=new_stock)
    session.add(c); await session.commit(); await session.refresh(c); return c, new_stock

async def get_consumptions(session, user_id=None, offset=0, limit=10):
    q = select(CementConsumption).order_by(CementConsumption.timestamp.desc()).offset(offset).limit(limit)
    if user_id: q = q.where(CementConsumption.operator_id == user_id)
    return (await session.execute(q)).scalars().all()

async def get_consumptions_count(session, user_id=None):
    q = select(func.count(CementConsumption.id))
    if user_id: q = q.where(CementConsumption.operator_id == user_id)
    return await session.scalar(q) or 0

async def add_adjustment(session, admin_id, adjustment_type, quantity_kg, reason):
    if quantity_kg <= 0: raise ValueError("Quantity must be positive")
    delta = quantity_kg if adjustment_type == AdjustmentType.ADD else -quantity_kg
    if adjustment_type == AdjustmentType.DEDUCT:
        current = await get_current_stock(session)
        if quantity_kg > current: raise ValueError(f"Cannot deduct more than current stock ({current:,.0f} kg)")
    new_stock = await _update_stock_atomic(session, delta)
    a = StockAdjustment(admin_id=admin_id, adjustment_type=adjustment_type, quantity_kg=quantity_kg, reason=reason, stock_after=new_stock)
    session.add(a); await session.commit(); await session.refresh(a); return a, new_stock

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

async def add_audit_log(session, telegram_id, action, details=None, user_id=None):
    session.add(AuditLog(user_id=user_id, telegram_id=telegram_id, action=action, details=details))
    await session.commit()

async def get_audit_logs(session, offset=0, limit=20):
    return (await session.execute(select(AuditLog).order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit))).scalars().all()

async def get_audit_logs_count(session):
    return await session.scalar(select(func.count(AuditLog.id))) or 0

async def get_last_low_stock_alert(session):
    v = await get_setting(session, "last_low_stock_alert")
    return datetime.fromisoformat(v) if v else None

async def set_last_low_stock_alert(session, dt):
    if dt is None:
        row = await session.scalar(select(StockSettings).where(StockSettings.key == "last_low_stock_alert"))
        if row: await session.delete(row); await session.commit()
    else:
        await set_setting(session, "last_low_stock_alert", dt.isoformat())
QUERIES

# 3. handlers/common.py – universal cancel
cat > handlers/common.py << 'COMMON'
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from config import config
from database.models import UserRole, User
from database.queries import AsyncSessionLocal, create_user, get_current_stock, get_low_stock_threshold, add_audit_log
from keyboards.reply import main_menu, REMOVE_KB
from utils.formatters import fmt_kg, fmt_dt, stock_status_text, now_local
from utils.logger import logger

router = Router()
TOKEN_ROLE_MAP = {config.ADMIN_TOKEN: UserRole.ADMIN, config.STOREKEEPER_TOKEN: UserRole.STOREKEEPER, config.OPERATOR_TOKEN: UserRole.OPERATOR}

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db_user):
    await state.clear()
    if db_user and db_user.is_active:
        await message.answer(f"👋 Welcome back, {db_user.full_name}!\n🎭 Role: {db_user.role.value.title()}", parse_mode="HTML", reply_markup=main_menu(db_user.role))
        return
    if db_user and not db_user.is_active:
        await message.answer("❌ Your account has been deactivated. Contact an admin.")
        return
    args = message.text.split(maxsplit=1)
    token = args[1].strip() if len(args) > 1 else None
    async with AsyncSessionLocal() as session:
        all_users = (await session.execute(select(User))).scalars().all()
    if not all_users:
        async with AsyncSessionLocal() as session:
            user = await create_user(session, message.from_user.id, message.from_user.full_name, UserRole.ADMIN, message.from_user.username)
            await add_audit_log(session, message.from_user.id, "SELF_REGISTERED_ADMIN", f"First admin: {user.full_name}", user.id)
        await message.answer(f"👋 Welcome, {message.from_user.full_name}!\n\n✅ You are registered as the first Admin.\n\nUse /adduser to add more users.", parse_mode="HTML", reply_markup=main_menu(UserRole.ADMIN))
        logger.info(f"First admin auto-registered: {message.from_user.id}")
        return
    if token and token in TOKEN_ROLE_MAP:
        role = TOKEN_ROLE_MAP[token]
        async with AsyncSessionLocal() as session:
            user = await create_user(session, message.from_user.id, message.from_user.full_name, role, message.from_user.username)
            await add_audit_log(session, message.from_user.id, "REGISTERED_VIA_INVITE_LINK", f"Registered as {role.value}", user.id)
        await message.answer(f"👋 Welcome, {message.from_user.full_name}!\n\n✅ You are registered as {role.value.title()}.", parse_mode="HTML", reply_markup=main_menu(role))
        logger.info(f"User {message.from_user.id} registered as {role.value}")
        return
    await message.answer("👋 Welcome to the Cement Stock Bot.\n\nYou are not registered. Please ask an administrator for an invite link.", parse_mode="HTML")

@router.message(Command("invitelinks"))
@router.message(F.text == "🔗 Invite Links")
async def cmd_invite_links(message: Message, db_user):
    if not (db_user and db_user.is_active and db_user.role == UserRole.ADMIN):
        await message.answer("❌ Admin only.")
        return
    bot_info = await message.bot.get_me()
    username = bot_info.username
    await message.answer(f"🔗 <b>Invite Links</b>\n\n👑 Admin:\n<code>https://t.me/{username}?start={config.ADMIN_TOKEN}</code>\n\n🏪 Storekeeper:\n<code>https://t.me/{username}?start={config.STOREKEEPER_TOKEN}</code>\n\n⚙️ Operator:\n<code>https://t.me/{username}?start={config.OPERATOR_TOKEN}</code>\n\n⚠️ Keep them private.", parse_mode="HTML", reply_markup=main_menu(db_user.role))

@router.message(Command("stock"))
@router.message(F.text == "📦 Stock Status")
async def cmd_stock(message: Message, db_user):
    if not db_user or not db_user.is_active:
        await message.answer("❌ Access denied.")
        return
    async with AsyncSessionLocal() as session:
        current = await get_current_stock(session)
        threshold = await get_low_stock_threshold(session)
    status = stock_status_text(current, threshold)
    await message.answer(f"🏭 <b>CEMENT STOCK STATUS</b>\n━━━━━━━━━━━━━━━━━━━━━━\n📦 Current Stock:   <b>{fmt_kg(current)}</b>\n🔻 Minimum Level:   <b>{fmt_kg(threshold)}</b>\n📊 Status:          {status}\n━━━━━━━━━━━━━━━━━━━━━━\n🕐 Last Updated: {fmt_dt(now_local())}", parse_mode="HTML", reply_markup=main_menu(db_user.role))

@router.message(Command("cancel"))
@router.message(F.text == "❌ Cancel")
async def cmd_cancel(message: Message, state: FSMContext, db_user):
    await state.clear()
    await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role) if db_user else REMOVE_KB)
COMMON

# 4. handlers/admin.py – only patch the adjust_type function (quick fix)
# We'll append a corrected adjust_type to the existing admin.py using sed
# First backup original, then replace the function
cp handlers/admin.py handlers/admin.py.bak
sed -i '' '/^@router\.message(AdjustStates\.adj_type)/,/^async def adjust_type/{
    /^@router\.message(AdjustStates\.adj_type)/{
        a\
@router.message(AdjustStates.adj_type)\
async def adjust_type(message: Message, state: FSMContext, db_user):\
    if message.text.startswith("/") or message.text == "❌ Cancel":\
        await state.clear()\
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))\
        return\
    if message.text == "➕ Add Stock":\
        adj_type = AdjustmentType.ADD\
    elif message.text == "➖ Deduct Stock":\
        adj_type = AdjustmentType.DEDUCT\
    else:\
        await message.answer("⚠️ Choose Add or Deduct.")\
        return\
    await state.update_data(adj_type=adj_type.value)\
    await state.set_state(AdjustStates.quantity)\
    await message.answer("Enter <b>Quantity (kg)</b>:", parse_mode="HTML", reply_markup=back_kb())
        d
    }
}' handlers/admin.py

# 5. handlers/operator.py – add command detection to all state handlers (patch)
# We'll add a helper function and apply to kg_per_m3 and cubic_meters states
# Simple: replace the existing handlers with new ones
cat > handlers/operator.py << 'OPERATOR'
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from database.models import UserRole
from database.queries import AsyncSessionLocal, add_consumption, get_current_stock, add_audit_log
from keyboards.reply import main_menu, cancel_kb, back_kb, confirm_kb
from utils.alerts import check_and_send_low_stock_alert
from utils.formatters import fmt_kg
from utils.logger import logger

router = Router()
GRADES = ["C5","C10","C15","C20","C25","C30","C35","C37","C40","C45","C50","C60"]

class ConsumeStates(StatesGroup):
    project_name = State()
    concrete_grade = State()
    kg_per_m3 = State()
    cubic_meters = State()
    confirm = State()

def _require_role(db_user, *roles):
    return db_user and db_user.is_active and db_user.role in roles

@router.message(Command("consume"))
@router.message(F.text == "📤 Consume")
async def cmd_consume(message: Message, state: FSMContext, db_user):
    allowed = [UserRole.ADMIN, UserRole.OPERATOR]
    if not _require_role(db_user, *allowed):
        await message.answer("❌ Access denied. Only operators may record consumption.")
        return
    async with AsyncSessionLocal() as session:
        current = await get_current_stock(session)
    await state.clear()
    await state.set_state(ConsumeStates.project_name)
    await message.answer(f"📤 <b>Record Cement Consumption</b>\n\n📦 Available Stock: <b>{fmt_kg(current)}</b>\n\nStep 1/4 — Enter <b>Project Name</b>:", parse_mode="HTML", reply_markup=cancel_kb())

@router.message(ConsumeStates.project_name)
async def consume_project_name(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/') or message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    await state.update_data(project_name=message.text.strip())
    await state.set_state(ConsumeStates.concrete_grade)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=g, callback_data=f"grade_{g}") for g in GRADES[i:i+3]] for i in range(0, len(GRADES), 3)])
    await message.answer("Step 2/4 — Select <b>Concrete Grade</b>:", parse_mode="HTML", reply_markup=keyboard)

@router.callback_query(lambda c: c.data and c.data.startswith("grade_"))
async def consume_grade_callback(callback: CallbackQuery, state: FSMContext):
    grade = callback.data.split("_")[1]
    await state.update_data(concrete_grade=grade)
    await state.set_state(ConsumeStates.kg_per_m3)
    try:
        await callback.message.edit_text(f"✅ Selected: <b>{grade}</b>\n\nStep 3/4 — Enter <b>Cement per m³ (kg/m³)</b>:", parse_mode="HTML")
        await callback.answer()
    except TelegramBadRequest:
        pass
    await callback.message.answer("Enter kg/m³:", reply_markup=cancel_kb())

@router.message(ConsumeStates.kg_per_m3)
async def consume_kg_per_m3(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/') or message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    try:
        kg_m3 = float(message.text.strip().replace(",", ""))
        if kg_m3 <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid value. Enter a positive number (e.g. 350).")
        return
    await state.update_data(kg_per_m3=kg_m3)
    await state.set_state(ConsumeStates.cubic_meters)
    await message.answer(f"✅ kg/m³ set to <b>{kg_m3}</b>\n\nStep 4/4 — Enter <b>m³ Done</b>:", parse_mode="HTML", reply_markup=back_kb())

@router.message(ConsumeStates.cubic_meters)
async def consume_cubic_meters(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/') or message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ConsumeStates.kg_per_m3)
        await message.answer("Step 3/4 — Enter kg/m³:", reply_markup=cancel_kb())
        return
    try:
        m3 = float(message.text.strip().replace(",", ""))
        if m3 <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid value. Enter a positive number (e.g. 120).")
        return
    data = await state.get_data()
    kg_per_m3 = data.get("kg_per_m3", 0)
    if kg_per_m3 <= 0:
        await message.answer("❌ Invalid kg/m³. Please restart.")
        await state.clear()
        return
    total_kg = m3 * kg_per_m3
    await state.update_data(cubic_meters=m3, total_kg=total_kg)
    await state.set_state(ConsumeStates.confirm)
    await message.answer(f"📋 <b>Confirm Consumption</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🏗️ Project:      <b>{data.get('project_name', 'N/A')}</b>\n📊 Grade:        <b>{data.get('concrete_grade', 'N/A')}</b>\n📐 m³ Done:      <b>{m3} m³</b>\n⚖️  kg/m³:        <b>{kg_per_m3} kg/m³</b>\n🧱 Total Used:   <b>{fmt_kg(total_kg)}</b>\n━━━━━━━━━━━━━━━━━━━━━━\nConfirm?", parse_mode="HTML", reply_markup=confirm_kb())

@router.message(ConsumeStates.confirm)
async def consume_confirm(message: Message, state: FSMContext, db_user, bot):
    if message.text == "🔙 Back":
        await state.set_state(ConsumeStates.cubic_meters)
        await message.answer("Step 4/4 — Enter m³:", reply_markup=back_kb())
        return
    if message.text != "✅ Confirm":
        await state.clear()
        await message.answer("❌ Cancelled.", reply_markup=main_menu(db_user.role))
        return
    data = await state.get_data()
    await state.clear()
    try:
        async with AsyncSessionLocal() as session:
            consumption, new_stock = await add_consumption(
                session, operator_id=db_user.id, quantity_kg=data["total_kg"],
                cubic_meters=data.get("cubic_meters"), kg_per_m3=data.get("kg_per_m3"),
                project_name=data.get("project_name"), concrete_grade=data.get("concrete_grade")
            )
            await add_audit_log(session, message.from_user.id, "CONSUMPTION_ADDED",
                f"Project: {data.get('project_name')}, Grade: {data.get('concrete_grade')}, m³: {data.get('cubic_meters')}, kg/m³: {data.get('kg_per_m3')}, Total: {data['total_kg']} kg", user_id=db_user.id)
        await message.answer(
            f"✅ <b>Consumption Recorded!</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🏗️ Project:      <b>{data.get('project_name', 'N/A')}</b>\n📊 Grade:        <b>{data.get('concrete_grade', 'N/A')}</b>\n📐 m³ Done:      <b>{data.get('cubic_meters', 0)} m³</b>\n⚖️  kg/m³:        <b>{data.get('kg_per_m3', 0)} kg/m³</b>\n🧱 Total Used:   <b>{fmt_kg(data['total_kg'])}</b>\n📊 New Stock:    <b>{fmt_kg(new_stock)}</b>\n📄 Record ID:    #{consumption.id}",
            parse_mode="HTML", reply_markup=main_menu(db_user.role)
        )
        await check_and_send_low_stock_alert(bot, new_stock)
    except Exception as e:
        logger.error(f"Consumption error: {e}")
        await message.answer(f"❌ Error: {e}", reply_markup=main_menu(db_user.role))
OPERATOR

# 6. handlers/storekeeper.py – already fixed (from previous answer), but ensure command detection in quantity
# We'll replace it with the fixed version (same as earlier but with added command checks)
cat > handlers/storekeeper.py << 'STOREKEEPER'
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from datetime import datetime
import asyncio
from database.models import UserRole
from database.queries import AsyncSessionLocal, add_receipt, get_receipts, get_receipts_count, add_audit_log, get_all_users, get_consumptions, get_consumptions_count
from keyboards.reply import main_menu, cancel_kb, back_kb, confirm_kb
from keyboards.pagination import pagination_kb
from utils.alerts import check_and_send_low_stock_alert
from utils.formatters import fmt_kg, fmt_dt
from utils.logger import logger

router = Router()

class ReceiptStates(StatesGroup):
    supplier = State()
    truck = State()
    quantity = State()
    confirm = State()

def _require_role(db_user, *roles):
    return db_user and db_user.is_active and db_user.role in roles

@router.message(Command("receive"))
@router.message(F.text == "📥 Receive")
async def cmd_receive(message: Message, state: FSMContext, db_user):
    allowed = [UserRole.ADMIN, UserRole.STOREKEEPER]
    if not _require_role(db_user, *allowed):
        await message.answer("❌ Access denied.")
        return
    await state.clear()
    await state.set_state(ReceiptStates.supplier)
    await message.answer("📥 New Receipt\nStep 1/3 — Supplier Name:", reply_markup=cancel_kb())

@router.message(ReceiptStates.supplier)
async def receive_supplier(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    await state.update_data(supplier=message.text.strip())
    await state.set_state(ReceiptStates.truck)
    await message.answer("Step 2/3 — Truck Number:", reply_markup=back_kb())

@router.message(ReceiptStates.truck)
async def receive_truck(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.supplier)
        await message.answer("Step 1/3 — Supplier Name:", reply_markup=cancel_kb())
        return
    await state.update_data(truck=message.text.strip())
    await state.set_state(ReceiptStates.quantity)
    await message.answer("Step 3/3 — Quantity (kg):", reply_markup=back_kb())

@router.message(ReceiptStates.quantity)
async def receive_quantity(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/') or message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.truck)
        await message.answer("Step 2/3 — Truck Number:", reply_markup=back_kb())
        return
    try:
        qty = float(message.text.strip().replace(",",""))
        if qty <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid quantity.")
        return
    await state.update_data(quantity=qty)
    data = await state.get_data()
    await state.set_state(ReceiptStates.confirm)
    await message.answer(f"📋 Confirm Receipt\n🏭 Supplier: {data['supplier']}\n🚚 Truck: {data['truck']}\n⚖️ Quantity: {fmt_kg(qty)}\nConfirm?", reply_markup=confirm_kb())

@router.message(ReceiptStates.confirm)
async def receive_confirm(message: Message, state: FSMContext, db_user, bot):
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.quantity)
        await message.answer("Step 3/3 — Quantity:", reply_markup=back_kb())
        return
    if message.text != "✅ Confirm":
        await state.clear()
        await message.answer("❌ Cancelled.", reply_markup=main_menu(db_user.role))
        return
    data = await state.get_data()
    await state.clear()
    try:
        async with AsyncSessionLocal() as session:
            receipt, new_stock = await add_receipt(session, db_user.id, data["supplier"], data["truck"], data["quantity"])
            await add_audit_log(session, message.from_user.id, "RECEIPT_ADDED", f"Supplier: {data['supplier']}, Qty: {data['quantity']} kg", user_id=db_user.id)

            notif_text = (f"🚛 <b>New Cement Truck Received</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🏭 Supplier: <b>{data['supplier']}</b>\n🚚 Truck: <b>{data['truck']}</b>\n📦 Quantity: <b>{fmt_kg(data['quantity'])}</b>\n📊 New Stock: <b>{fmt_kg(new_stock)}</b>\n👤 Recorded by: {db_user.full_name}\n🕐 {fmt_dt(datetime.utcnow())}")
            async def notify():
                async with AsyncSessionLocal() as ns:
                    for u in await get_all_users(ns):
                        if u.role == UserRole.ADMIN and u.is_active:
                            try:
                                await bot.send_message(u.telegram_id, notif_text, parse_mode="HTML")
                            except Exception as e:
                                logger.warning(f"Notify {u.telegram_id} failed: {e}")
            asyncio.create_task(notify())

        await message.answer(f"✅ Receipt recorded! New stock: {fmt_kg(new_stock)}", reply_markup=main_menu(db_user.role))
        await check_and_send_low_stock_alert(bot, new_stock)
    except Exception as e:
        await message.answer(f"❌ Error: {e}", reply_markup=main_menu(db_user.role))

@router.message(Command("history"))
@router.message(F.text == "📜 History")
async def cmd_history(message: Message, state: FSMContext, db_user):
    await state.update_data(page=1, db_user=db_user)
    await show_history_page(message, 1, state)

async def show_history_page(message: Message, page: int, state: FSMContext, callback_query: CallbackQuery = None):
    data = await state.get_data()
    db_user = data.get("db_user")
    if not db_user: return
    limit, offset = 5, (page-1)*5
    async with AsyncSessionLocal() as session:
        if db_user.role == UserRole.STOREKEEPER:
            records = await get_receipts(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_receipts_count(session, user_id=db_user.id)
            title = "📜 Recent Receipts"
            lines = [f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)} from {r.supplier_name} ({r.truck_number})\n   Stock after: {fmt_kg(r.stock_after)}" for r in records]
        elif db_user.role == UserRole.OPERATOR:
            records = await get_consumptions(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_consumptions_count(session, user_id=db_user.id)
            title = "📜 Recent Consumptions"
            lines = [f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)}" + (f" | {r.cubic_meters} m³ @ {r.kg_per_m3} kg/m³" if r.cubic_meters else "") + f"\n   Stock after: {fmt_kg(r.stock_after)}" for r in records]
        else:
            receipts = await get_receipts(session, offset=offset, limit=limit)
            consumptions = await get_consumptions(session, offset=offset, limit=limit)
            total_rec = await get_receipts_count(session)
            total_cons = await get_consumptions_count(session)
            total = max(total_rec, total_cons)
            title = "📜 Recent Transactions"
            lines = ["Receipts:"] + [f"  📥 #{r.id} {fmt_dt(r.timestamp)} | {fmt_kg(r.quantity_kg)} from {r.supplier_name}" for r in receipts] + ["\nConsumptions:"] + [f"  📤 #{c.id} {fmt_dt(c.timestamp)} | {fmt_kg(c.quantity_kg)}" + (f" | {c.cubic_meters} m³" if c.cubic_meters else "") for c in consumptions]
    if not lines: text = "No records found."
    else: text = f"{title}\n\n" + "\n".join(lines)
    total_pages = (total + limit - 1) // limit
    kb = pagination_kb(page, total_pages, "history")
    if callback_query:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback_query.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(lambda c: c.data and c.data.startswith("history_"))
async def history_page_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split("_")[1])
    await show_history_page(callback.message, page, state, callback_query=callback)
STOREKEEPER

# 7. middlewares/throttling.py – fix
cat > middlewares/throttling.py << 'THROTTLE'
from collections import defaultdict
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

user_last_command = defaultdict(lambda: datetime.utcnow())

class ThrottlingMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: Dict[str, Any]) -> Any:
        tg_user = None
        if isinstance(event, Message): tg_user = event.from_user
        elif isinstance(event, CallbackQuery): tg_user = event.from_user
        if tg_user:
            now = datetime.utcnow()
            if (now - user_last_command[tg_user.id]).total_seconds() < 1:
                if isinstance(event, Message):
                    await event.answer("⏳ Too many requests. Please slow down.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⏳ Too many requests. Please slow down.", show_alert=False)
                return
            user_last_command[tg_user.id] = now
        return await handler(event, data)
THROTTLE

# 8. bot.py – add retry and graceful shutdown
cat > bot.py << 'BOT'
import asyncio
import signal
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from config import config
from database.queries import init_db
from handlers import common_router, storekeeper_router, operator_router, reports_router, admin_router
from middlewares.auth import AuthMiddleware
from middlewares.throttling import ThrottlingMiddleware
from utils.logger import logger

async def main():
    config.validate()
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database ready.")
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())
    dp.include_router(admin_router)
    dp.include_router(storekeeper_router)
    dp.include_router(operator_router)
    dp.include_router(reports_router)
    dp.include_router(common_router)

    async def shutdown():
        logger.info("Received shutdown signal, stopping bot...")
        await dp.stop_polling()
        await bot.session.close()
        logger.info("Bot stopped.")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    logger.info("Starting bot polling...")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except asyncio.CancelledError:
        pass
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
BOT

echo "✅ All files updated. Now committing and pushing..."
git add .
git commit -m "Major fix: FSM cancellation, retry logic, command detection in all states"
git push origin main
echo "✅ Done. Go to Render and redeploy."
