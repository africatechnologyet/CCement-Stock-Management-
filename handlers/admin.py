"""
Admin-only handlers: /adduser, /users, /adjust, /set_low_stock,
/add_recipient, /recipients, /del_recipient, /audit
"""
import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery

from database.models import UserRole, AdjustmentType
from database.queries import (
    AsyncSessionLocal,
    get_user_by_telegram_id,
    create_user,
    get_all_users,
    add_adjustment,
    set_setting,
    get_low_stock_threshold,
    add_recipient,
    list_recipients,
    remove_recipient,
    get_audit_logs,
    add_audit_log,
    get_audit_logs_count,
)
from keyboards.reply import (
    main_menu, cancel_kb, adjustment_type_kb, back_kb, back_confirm_kb,
    group_type_kb, confirm_kb, role_selection_kb
)
from keyboards.pagination import pagination_kb
from utils.alerts import check_and_send_low_stock_alert
from utils.formatters import fmt_kg, fmt_dt
from utils.logger import logger

router = Router()

def _is_admin(db_user):
    return db_user and db_user.is_active and db_user.role == UserRole.ADMIN


# ========== ADD USER ==========
class AddUserStates(StatesGroup):
    telegram_id = State()
    full_name = State()
    role = State()
    confirm = State()

@router.message(Command("adduser"))
async def cmd_adduser(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    await state.clear()
    await state.set_state(AddUserStates.telegram_id)
    await message.answer(
        "👤 <b>Add New User</b>\n\nStep 1/3 — Enter Telegram ID:",
        parse_mode="HTML", reply_markup=cancel_kb()
    )

@router.message(AddUserStates.telegram_id)
async def adduser_telegram_id(message: Message, state: FSMContext, db_user):
    if message.text in ("❌ Cancel", "🔙 Back"):
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    try:
        tid = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Invalid ID.")
        return
    async with AsyncSessionLocal() as session:
        existing = await get_user_by_telegram_id(session, tid)
    if existing:
        await message.answer(f"⚠️ User {existing.full_name} already registered.", reply_markup=main_menu(db_user.role))
        await state.clear()
        return
    await state.update_data(telegram_id=tid)
    await state.set_state(AddUserStates.full_name)
    await message.answer("Step 2/3 — Enter Full Name:", reply_markup=back_kb())

@router.message(AddUserStates.full_name)
async def adduser_full_name(message: Message, state: FSMContext):
    if message.text == "🔙 Back":
        await state.set_state(AddUserStates.telegram_id)
        await message.answer("Step 1/3 — Enter Telegram ID:", reply_markup=cancel_kb())
        return
    full_name = message.text.strip()
    if not full_name:
        await message.answer("⚠️ Name cannot be empty.")
        return
    await state.update_data(full_name=full_name)
    await state.set_state(AddUserStates.role)
    await message.answer("Step 3/3 — Select Role:", reply_markup=role_selection_kb())

ROLE_MAP = {"🏪 Storekeeper": UserRole.STOREKEEPER, "⚙️ Operator": UserRole.OPERATOR, "📊 Management": UserRole.MANAGEMENT, "🔑 Admin": UserRole.ADMIN}
@router.message(AddUserStates.role)
async def adduser_role(message: Message, state: FSMContext):
    if message.text == "🔙 Back":
        await state.set_state(AddUserStates.full_name)
        await message.answer("Step 2/3 — Enter Full Name:", reply_markup=back_kb())
        return
    role = ROLE_MAP.get(message.text)
    if not role:
        await message.answer("⚠️ Use the buttons.")
        return
    await state.update_data(role=role)
    data = await state.get_data()
    await state.set_state(AddUserStates.confirm)
    await message.answer(f"📋 Confirm New User\n━━━━━━━━━━━━━━━━━━\n🆔 ID: {data['telegram_id']}\n👤 Name: {data['full_name']}\n🎭 Role: {role.value.title()}\n━━━━━━━━━━━━━━━━━━\nConfirm?", reply_markup=back_confirm_kb())

@router.message(AddUserStates.confirm)
async def adduser_confirm(message: Message, state: FSMContext, db_user):
    if message.text == "🔙 Back":
        await state.set_state(AddUserStates.role)
        await message.answer("Select Role:", reply_markup=role_selection_kb())
        return
    if message.text != "✅ Confirm":
        await state.clear()
        await message.answer("❌ Cancelled.", reply_markup=main_menu(db_user.role))
        return
    data = await state.get_data()
    await state.clear()
    async with AsyncSessionLocal() as session:
        await create_user(session, data["telegram_id"], data["full_name"], data["role"], created_by=message.from_user.id)
        await add_audit_log(session, message.from_user.id, "USER_ADDED", f"Added {data['full_name']} as {data['role'].value}", user_id=db_user.id)
    await message.answer(f"✅ User {data['full_name']} added.", reply_markup=main_menu(db_user.role))


# ========== USERS LIST ==========
@router.message(Command("users"))
@router.message(F.text == "👥 Users")
async def cmd_users(message: Message, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)
    if not users:
        await message.answer("No users.", reply_markup=main_menu(db_user.role))
        return
    lines = ["👥 Registered Users:\n"]
    for u in users:
        status = "✅" if u.is_active else "❌"
        lines.append(f"{status} {u.full_name} | {u.role.value.title()} | ID: {u.telegram_id}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=main_menu(db_user.role))


# ========== STOCK ADJUSTMENT ==========
class AdjustStates(StatesGroup):
    adj_type = State()
    quantity = State()
    reason = State()
    confirm = State()

@router.message(Command("adjust"))
@router.message(F.text == "⚙️ Adjust Stock")
async def cmd_adjust(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    await state.clear()
    await state.set_state(AdjustStates.adj_type)
    await message.answer("Select adjustment type:", reply_markup=adjustment_type_kb())

@router.message(AdjustStates.adj_type)
async def adjust_type(message: Message, state: FSMContext):
    if message.text == "➕ Add Stock":
        adj_type = AdjustmentType.ADD
    elif message.text == "➖ Deduct Stock":
        adj_type = AdjustmentType.DEDUCT
    else:
        await message.answer("⚠️ Choose Add or Deduct.")
        return
    await state.update_data(adj_type=adj_type.value)
    await state.set_state(AdjustStates.quantity)
    await message.answer("Enter Quantity (kg):", reply_markup=back_kb())

@router.message(AdjustStates.quantity)
async def adjust_quantity(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("Cancelled.")
        return
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(AdjustStates.adj_type)
        await message.answer("Select type:", reply_markup=adjustment_type_kb())
        return
    try:
        qty = float(message.text.strip().replace(",",""))
        if qty <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid quantity.")
        return
    await state.update_data(quantity=qty)
    await state.set_state(AdjustStates.reason)
    await message.answer("Enter Reason:", reply_markup=back_kb())

@router.message(AdjustStates.reason)
async def adjust_reason(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(AdjustStates.quantity)
        await message.answer("Enter Quantity:", reply_markup=back_kb())
        return
    await state.update_data(reason=message.text.strip())
    data = await state.get_data()
    emoji = "➕" if data["adj_type"] == AdjustmentType.ADD.value else "➖"
    await state.set_state(AdjustStates.confirm)
    await message.answer(f"📋 Confirm\nType: {emoji} {data['adj_type'].title()}\nQty: {fmt_kg(data['quantity'])}\nReason: {data['reason']}\nConfirm?", reply_markup=back_confirm_kb())

@router.message(AdjustStates.confirm)
async def adjust_confirm(message: Message, state: FSMContext, db_user, bot):
    if message.text == "🔙 Back":
        await state.set_state(AdjustStates.reason)
        await message.answer("Enter Reason:", reply_markup=back_kb())
        return
    if message.text != "✅ Confirm":
        await state.clear()
        await message.answer("❌ Cancelled.", reply_markup=main_menu(db_user.role))
        return
    data = await state.get_data()
    await state.clear()
    try:
        async with AsyncSessionLocal() as session:
            adj, new_stock = await add_adjustment(session, db_user.id, AdjustmentType(data["adj_type"]), data["quantity"], data["reason"])
            await add_audit_log(session, message.from_user.id, "STOCK_ADJUSTED", f"Type: {data['adj_type']}, Qty: {data['quantity']} kg, Reason: {data['reason']}", user_id=db_user.id)
        await message.answer(f"✅ Stock adjusted! New stock: {fmt_kg(new_stock)}", reply_markup=main_menu(db_user.role))
        await check_and_send_low_stock_alert(bot, new_stock)
    except Exception as e:
        await message.answer(f"❌ Error: {e}", reply_markup=main_menu(db_user.role))


# ========== SET LOW STOCK ==========
class SetLowStockStates(StatesGroup):
    value = State()

@router.message(Command("set_low_stock"))
@router.message(F.text == "🔔 Set Min Level")
async def cmd_set_low_stock(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    async with AsyncSessionLocal() as session:
        current = await get_low_stock_threshold(session)
    await state.clear()
    await state.set_state(SetLowStockStates.value)
    await message.answer(f"Current threshold: {fmt_kg(current)}\nEnter new threshold in kg:", reply_markup=cancel_kb())

@router.message(SetLowStockStates.value)
async def set_low_stock_value(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("Cancelled.")
        return
    if message.text in ("❌ Cancel", "🔙 Back"):
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    try:
        val = float(message.text.strip().replace(",",""))
        if val < 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid number.")
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        await set_setting(session, "low_stock_threshold", str(val), updated_by=message.from_user.id)
        await add_audit_log(session, message.from_user.id, "LOW_STOCK_THRESHOLD_SET", f"Threshold = {val} kg", user_id=db_user.id)
    await message.answer(f"✅ Threshold set to {fmt_kg(val)}.", reply_markup=main_menu(db_user.role))


# ========== ADD RECIPIENT ==========
class AddRecipientStates(StatesGroup):
    telegram_id = State()
    label = State()
    is_group = State()

@router.message(Command("add_recipient"))
async def cmd_add_recipient(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    await state.clear()
    await state.set_state(AddRecipientStates.telegram_id)
    await message.answer("Step 1/2 — Enter Telegram ID (negative for group):", reply_markup=cancel_kb())

@router.message(AddRecipientStates.telegram_id)
async def add_recipient_id(message: Message, state: FSMContext, db_user):
    if message.text in ("❌ Cancel", "🔙 Back"):
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    try:
        tid = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Invalid ID.")
        return
    await state.update_data(telegram_id=tid)
    await state.set_state(AddRecipientStates.label)
    await message.answer("Step 2/2 — Enter Label:", reply_markup=back_kb())

@router.message(AddRecipientStates.label)
async def add_recipient_label(message: Message, state: FSMContext):
    if message.text == "🔙 Back":
        await state.set_state(AddRecipientStates.telegram_id)
        await message.answer("Step 1/2 — Enter Telegram ID:", reply_markup=cancel_kb())
        return
    await state.update_data(label=message.text.strip())
    await state.set_state(AddRecipientStates.is_group)
    await message.answer("Is this a group?", reply_markup=group_type_kb())

@router.message(AddRecipientStates.is_group)
async def add_recipient_type(message: Message, state: FSMContext, db_user):
    if message.text == "🔙 Back":
        await state.set_state(AddRecipientStates.label)
        await message.answer("Step 2/2 — Enter Label:", reply_markup=back_kb())
        return
    is_group = message.text == "👥 Group"
    data = await state.get_data()
    await state.clear()
    async with AsyncSessionLocal() as session:
        await add_recipient(session, data["telegram_id"], data["label"], is_group, added_by=message.from_user.id)
        await add_audit_log(session, message.from_user.id, "RECIPIENT_ADDED", f"Label: {data['label']}, ID: {data['telegram_id']}", user_id=db_user.id)
    await message.answer(f"✅ Recipient {data['label']} added.", reply_markup=main_menu(db_user.role))


# ========== LIST RECIPIENTS ==========
@router.message(Command("recipients"))
@router.message(F.text == "📋 Recipients")
async def cmd_recipients(message: Message, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    async with AsyncSessionLocal() as session:
        recs = await list_recipients(session)
    if not recs:
        await message.answer("No recipients.", reply_markup=main_menu(db_user.role))
        return
    lines = ["📋 Alert Recipients:\n"]
    for r in recs:
        status = "✅" if r.is_active else "❌"
        type_str = "Group" if r.is_group else "Individual"
        lines.append(f"{status} {r.label} | {type_str} | ID: {r.telegram_id}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=main_menu(db_user.role))


# ========== REMOVE RECIPIENT ==========
class DelRecipientStates(StatesGroup):
    confirm = State()

@router.message(Command("del_recipient"))
@router.message(F.text == "🗑️ Remove Recipient")
async def cmd_del_recipient(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    async with AsyncSessionLocal() as session:
        recipients = await list_recipients(session)
        if not recipients:
            await message.answer("No recipients.", reply_markup=main_menu(db_user.role))
            return
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=f"{r.label} (ID: {r.telegram_id})")] for r in recipients] + [[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)
    await state.set_state(DelRecipientStates.confirm)
    await state.update_data(recipients=recipients)
    await message.answer("Select recipient to deactivate:", reply_markup=kb)

@router.message(DelRecipientStates.confirm)
async def del_recipient_confirm(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    match = re.search(r"ID: (\d+)", message.text)
    if not match:
        await message.answer("⚠️ Invalid selection.")
        return
    tid = int(match.group(1))
    async with AsyncSessionLocal() as session:
        success = await remove_recipient(session, tid)
        if success:
            await add_audit_log(session, message.from_user.id, "RECIPIENT_REMOVED", f"Removed ID {tid}", user_id=db_user.id)
            await message.answer(f"✅ Recipient deactivated.", reply_markup=main_menu(db_user.role))
        else:
            await message.answer("❌ Not found.")
    await state.clear()


# ========== AUDIT LOG (WITH PAGINATION) ==========
@router.message(Command("audit"))
@router.message(F.text == "🕵️ Audit Log")
async def cmd_audit(message: Message, state: FSMContext, db_user):
    if not _is_admin(db_user):
        await message.answer("❌ Admin only.")
        return
    await state.update_data(audit_page=1, db_user=db_user)
    await show_audit_page(message, 1, state)

async def show_audit_page(message: Message, page: int, state: FSMContext, callback_query: CallbackQuery = None):
    data = await state.get_data()
    db_user = data.get("db_user")
    if not db_user:
        return
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
