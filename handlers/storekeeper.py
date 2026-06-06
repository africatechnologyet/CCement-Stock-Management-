from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from database.models import UserRole
from database.queries import AsyncSessionLocal, add_receipt, get_receipts, 
add_audit_log, get_all_users
from keyboards.reply import main_menu, cancel_kb, back_kb, confirm_kb
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
    await message.answer("📥 New Receipt\nStep 1/3 — Supplier Name:", 
reply_markup=cancel_kb())

@router.message(ReceiptStates.supplier)
async def receive_supplier(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", 
reply_markup=main_menu(db_user.role))
        return
    await state.update_data(supplier=message.text.strip())
    await state.set_state(ReceiptStates.truck)
    await message.answer("Step 2/3 — Truck Number:", 
reply_markup=back_kb())

@router.message(ReceiptStates.truck)
async def receive_truck(message: Message, state: FSMContext, db_user):
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", 
reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.supplier)
        await message.answer("Step 1/3 — Supplier Name:", 
reply_markup=cancel_kb())
        return
    await state.update_data(truck=message.text.strip())
    await state.set_state(ReceiptStates.quantity)
    await message.answer("Step 3/3 — Quantity (kg):", 
reply_markup=back_kb())

@router.message(ReceiptStates.quantity)
async def receive_quantity(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("Cancelled.")
        return
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", 
reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.truck)
        await message.answer("Step 2/3 — Truck Number:", 
reply_markup=back_kb())
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
    await message.answer(f"📋 Confirm Receipt\n🏭 Supplier: 
{data['supplier']}\n🚚 Truck: {data['truck']}\n⚖️ Quantity: 
{fmt_kg(qty)}\nConfirm?", reply_markup=confirm_kb())

@router.message(ReceiptStates.confirm)
async def receive_confirm(message: Message, state: FSMContext, db_user, 
bot):
    if message.text == "🔙 Back":
        await state.set_state(ReceiptStates.quantity)
        await message.answer("Step 3/3 — Quantity:", 
reply_markup=back_kb())
        return
    if message.text != "✅ Confirm":
        await state.clear()
        await message.answer("❌ Cancelled.", 
reply_markup=main_menu(db_user.role))
        return
    data = await state.get_data()
    await state.clear()
    try:
        async with AsyncSessionLocal() as session:
            receipt, new_stock = await add_receipt(session, db_user.id, 
data["supplier"], data["truck"], data["quantity"])
            await add_audit_log(session, message.from_user.id, 
"RECEIPT_ADDED", f"Supplier: {data['supplier']}, Qty: {data['quantity']} 
kg", user_id=db_user.id)

            # --- NOTIFY ALL ADMINS ---
            all_users = await get_all_users(session)
            admin_users = [u for u in all_users if u.role == 
UserRole.ADMIN and u.is_active]
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
            for admin in admin_users:
                try:
                    await bot.send_message(admin.telegram_id, notif_text, 
parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"Failed to notify admin 
{admin.telegram_id}: {e}")

        await message.answer(f"✅ Receipt recorded! New stock: 
{fmt_kg(new_stock)}", reply_markup=main_menu(db_user.role))
        await check_and_send_low_stock_alert(bot, new_stock)
    except Exception as e:
        await message.answer(f"❌ Error: {e}", 
reply_markup=main_menu(db_user.role))

# The rest of the file (history command) remains unchanged
# ... (keep your existing cmd_history code)
