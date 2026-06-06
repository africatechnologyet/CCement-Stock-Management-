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

@router.message(Command("history"))
@router.message(F.text == "📜 History")
async def cmd_history(message: Message, state: FSMContext):
    # Store db_user in state for pagination
    await state.update_data(page=1, db_user=db_user)
    await show_history_page(message, 1, state)

async def show_history_page(message: Message, page: int, state: FSMContext, callback_query: CallbackQuery = None):
    data = await state.get_data()
    db_user = data.get("db_user")
    if not db_user:
        return
    limit = 5
    offset = (page - 1) * limit
    async with AsyncSessionLocal() as session:
        if db_user.role == UserRole.STOREKEEPER:
            records = await get_receipts(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_receipts_count(session, user_id=db_user.id)
            title = "📜 Recent Receipts"
            lines = []
            for r in records:
                lines.append(f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)} from {r.supplier_name} ({r.truck_number})\n   Stock after: {fmt_kg(r.stock_after)}")
        elif db_user.role == UserRole.OPERATOR:
            from database.queries import get_consumptions, get_consumptions_count
            records = await get_consumptions(session, user_id=db_user.id, offset=offset, limit=limit)
            total = await get_consumptions_count(session, user_id=db_user.id)
            title = "📜 Recent Consumptions"
            lines = []
            for r in records:
                m3_str = f" | {r.cubic_meters} m³ @ {r.kg_per_m3} kg/m³" if r.cubic_meters else ""
                lines.append(f"#{r.id} | {fmt_dt(r.timestamp)}\n   {fmt_kg(r.quantity_kg)}{m3_str}\n   Stock after: {fmt_kg(r.stock_after)}")
        else:  # admin
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
