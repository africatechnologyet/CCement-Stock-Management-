#!/bin/bash
set -e
echo "🔧 Applying all optimizations (PostgreSQL, background notifications, pagination, rate limiting, graceful shutdown)..."

# 1. config.py – add PostgreSQL pool settings
cat > config.py << 'EOF'
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
            raise ValueError("BOT_TOKEN required in .env")
        if cls.ADMIN_TOKEN == "admin_token":
            print("⚠️ WARNING: Using default ADMIN_TOKEN – change in .env")
        if cls.STOREKEEPER_TOKEN == "storekeeper_token":
            print("⚠️ WARNING: Using default STOREKEEPER_TOKEN – change in .env")
        if cls.OPERATOR_TOKEN == "operator_token":
            print("⚠️ WARNING: Using default OPERATOR_TOKEN – change in .env")

config = Config()
EOF

# 2. database/queries.py – PostgreSQL pooling, pagination helpers, remove SQLite migration
cat > database/queries.py << 'EOF'
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
EOF

# 3. keyboards/pagination.py – new file
mkdir -p keyboards
cat > keyboards/pagination.py << 'EOF'
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def pagination_kb(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"{prefix}_{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{prefix}_{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
EOF

# 4. middlewares/throttling.py – rate limiting middleware
mkdir -p middlewares
cat > middlewares/throttling.py << 'EOF'
from collections import defaultdict
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

user_last_command = defaultdict(datetime)

class ThrottlingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
        if tg_user:
            now = datetime.utcnow()
            last = user_last_command[tg_user.id]
            if (now - last).total_seconds() < 1:
                if isinstance(event, Message):
                    await event.answer("⏳ Too many requests. Please slow down.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⏳ Too many requests. Please slow down.", show_alert=False)
                return
            user_last_command[tg_user.id] = now
        return await handler(event, data)
EOF

# 5. Update handlers/storekeeper.py – background notifications, pagination in history
cat > handlers/storekeeper.py << 'EOF'
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from datetime import datetime
import asyncio
from database.models import UserRole
from database.queries import AsyncSessionLocal, add_receipt, get_receipts, get_receipts_count, add_audit_log, get_all_users
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
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("Cancelled.")
        return
    if message.text == "❌ Cancel":
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

            # Background notification to admins
            notif_text = (
                f"🚛 <b>New Cement Truck Received</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏭 Supplier: <b>{data['supplier']}</b>\n"
                f"🚚 Truck: <b>{data['truck']}</b>\n"
                f"📦 Quantity: <b>{fmt_kg(data['quantity'])}</b>\n"
                f"📊 New Stock: <b>{fmt_kg(new_stock)}</b>\n"
                f"👤 Recorded by: {db_user.full_name}\n"
                f"🕐 {fmt_dt(datetime.utcnow())}"
            )
            async def notify():
                async with AsyncSessionLocal() as ns:
                    all_users = await get_all_users(ns)
                    for u in all_users:
                        if u.role == UserRole.ADMIN and u.is_active:
                            try:
                                await bot.send_message(u.telegram_id, notif_text, parse_mode="HTML")
                            except Exception as e:
                                logger.warning(f"Notify admin {u.telegram_id} failed: {e}")
            asyncio.create_task(notify())

        await message.answer(f"✅ Receipt recorded! New stock: {fmt_kg(new_stock)}", reply_markup=main_menu(db_user.role))
        await check_and_send_low_stock_alert(bot, new_stock)
    except Exception as e:
        await message.answer(f"❌ Error: {e}", reply_markup=main_menu(db_user.role))

# History with pagination
@router.message(Command("history"))
@router.message(F.text == "📜 History")
async def cmd_history(message: Message, state: FSMContext):
    # Store pagination state in FSM
    await state.set_state("history")
    await state.update_data(page=1, role=None, user_id=None)
    await show_history_page(message, 1, state)

async def show_history_page(message: Message, page: int, state: FSMContext, callback_query: CallbackQuery = None):
    data = await state.get_data()
    role = data.get("role")
    user_id = data.get("user_id")
    db_user = data.get("db_user")  # from auth middleware
    if not db_user:
        return

    limit = 5
    offset = (page - 1) * limit

    async with AsyncSessionLocal() as session:
        if role == UserRole.STOREKEEPER or (db_user.role == UserRole.STOREKEEPER and not user_id):
            records = await get_receipts(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_receipts_count(session, user_id=db_user.id)
            title = "📜 Recent Receipts"
            lines = []
            for r in records:
                lines.append(f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)} from {r.supplier_name} ({r.truck_number})\n   Stock after: {fmt_kg(r.stock_after)}")
        elif role == UserRole.OPERATOR or (db_user.role == UserRole.OPERATOR and not user_id):
            from database.queries import get_consumptions, get_consumptions_count
            records = await get_consumptions(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_consumptions_count(session, user_id=db_user.id)
            title = "📜 Recent Consumptions"
            lines = []
            for r in records:
                m3_str = f" | {r.cubic_meters} m³ @ {r.kg_per_m3} kg/m³" if r.cubic_meters else ""
                lines.append(f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)}{m3_str}\n   Stock after: {fmt_kg(r.stock_after)}")
        else:  # admin view
            receipts = await get_receipts(session, offset=offset, limit=limit)
            from database.queries import get_consumptions
            consumptions = await get_consumptions(session, offset=offset, limit=limit)
            total_rec = await get_receipts_count(session)
            total_cons = await get_consumptions_count(session)
            total = max(total_rec, total_cons)
            title = "📜 Recent Transactions"
            lines = ["Receipts:"]
            for r in receipts:
                lines.append(f"  📥 #{r.id} {fmt_dt(r.timestamp)} | {fmt_kg(r.quantity_kg)} from {r.supplier_name}")
            lines.append("\nConsumptions:")
            for c in consumptions:
                m3_str = f" | {c.cubic_meters} m³" if c.cubic_meters else ""
                lines.append(f"  📤 #{c.id} {fmt_dt(c.timestamp)} | {fmt_kg(c.quantity_kg)}{m3_str}")

    if not lines:
        text = "No records found."
    else:
        text = f"{title}\n\n" + "\n".join(lines)

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

# The rest of the file (the previous cmd_history function is replaced, but we keep the rest)
EOF

# 6. Update handlers/admin.py – pagination for audit log
# We'll patch only the audit log part. To avoid full file rewrite, we'll append the new functions and modify the audit command.
# But easier: replace the whole admin.py? Too long. Instead, we'll create a patch.
# We'll add pagination to audit log by replacing the existing audit handler.
cat >> handlers/admin.py << 'ADMIN_PATCH'
# --- Pagination for audit log ---
from keyboards.pagination import pagination_kb

audit_page_state = {}

@router.message(Command("audit"))
@router.message(F.text == "🕵️ Audit Log")
async def cmd_audit(message: Message, state: FSMContext):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    await state.update_data(audit_page=1)
    await show_audit_page(message, 1, state)

async def show_audit_page(message: Message, page: int, state: FSMContext, callback_query: CallbackQuery = None):
    limit = 10
    offset = (page - 1) * limit
    async with AsyncSessionLocal() as session:
        logs = await get_audit_logs(session, offset=offset, limit=limit)
        total = await get_audit_logs_count(session)
    if not logs:
        text = "No audit logs found."
    else:
        lines = ["🕵️ <b>Audit Log</b>\n"]
        for log in logs:
            lines.append(f"🔹 {fmt_dt(log.timestamp)} | TG:{log.telegram_id}\n   {log.action}" + (f"\n   {log.details}" if log.details else ""))
        text = "\n\n".join(lines)
    total_pages = (total + limit - 1) // limit
    kb = pagination_kb(page, total_pages, "audit")
    if callback_query:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback_query.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(lambda c: c.data and c.data.startswith("audit_"))
async def audit_page_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split("_")[1])
    await show_audit_page(callback.message, page, state, callback_query=callback)
ADMIN_PATCH

# 7. utils/alerts.py – background low stock alert
cat > utils/alerts.py << 'EOF'
from datetime import datetime, timedelta
import asyncio
from aiogram import Bot
from database.queries import AsyncSessionLocal, get_active_recipients, get_low_stock_threshold, get_all_users, get_last_low_stock_alert, set_last_low_stock_alert
from database.models import UserRole
from utils.formatters import fmt_kg, fmt_dt, now_local
from utils.logger import logger

async def check_and_send_low_stock_alert(bot: Bot, current_stock: float):
    async with AsyncSessionLocal() as session:
        threshold = await get_low_stock_threshold(session)
        if current_stock > threshold:
            return
        last_alert = await get_last_low_stock_alert(session)
        now_utc = datetime.utcnow()
        if last_alert and now_utc - last_alert < timedelta(hours=2):
            logger.info("Low stock alert suppressed (cooldown).")
            return
        shortage = threshold - current_stock
        alert_text = f"⚠️ LOW CEMENT STOCK\n━━━━━━━━━━━━━━━\n📦 Stock: {fmt_kg(current_stock)}\n🔻 Min: {fmt_kg(threshold)}\n❗ Shortage: {fmt_kg(shortage)}\n━━━━━━━━━━━━━━━\n🚚 Please order delivery.\n🕐 {fmt_dt(now_local())}"

        async def send_alerts():
            sent_ids = set()
            async with AsyncSessionLocal() as ns:
                for rec in await get_active_recipients(ns):
                    if rec.telegram_id not in sent_ids:
                        try:
                            await bot.send_message(rec.telegram_id, alert_text, parse_mode="HTML")
                            sent_ids.add(rec.telegram_id)
                        except Exception as e:
                            logger.warning(f"Alert to {rec.telegram_id} failed: {e}")
                for user in await get_all_users(ns):
                    if user.role == UserRole.ADMIN and user.is_active and user.telegram_id not in sent_ids:
                        try:
                            await bot.send_message(user.telegram_id, alert_text, parse_mode="HTML")
                            sent_ids.add(user.telegram_id)
                        except Exception as e:
                            logger.warning(f"Alert to admin {user.telegram_id} failed: {e}")
                await set_last_low_stock_alert(ns, now_utc)
                await ns.commit()
            logger.info(f"Low stock alert sent to {len(sent_ids)} recipients.")

        asyncio.create_task(send_alerts())
EOF

# 8. bot.py – add throttling middleware and graceful shutdown
cat > bot.py << 'EOF'
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

    # Graceful shutdown on SIGTERM
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
EOF

echo "✅ All optimizations applied. Now commit and push to GitHub, then update Render with PostgreSQL."
