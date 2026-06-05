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
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Operation cancelled. Use /start to go to main menu.")
        return
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    await state.update_data(project_name=message.text.strip())
    await state.set_state(ConsumeStates.concrete_grade)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=g, callback_data=f"grade_{g}") for g in GRADES[i:i+3]]
        for i in range(0, len(GRADES), 3)
    ])
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
        # If the callback query is too old, just ignore the answer but still proceed
        pass
    await callback.message.answer("Enter kg/m³:", reply_markup=cancel_kb())

@router.message(ConsumeStates.kg_per_m3)
async def consume_kg_per_m3(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Operation cancelled.")
        return
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    try:
        kg_m3 = float(message.text.strip().replace(",", ""))
        if kg_m3 <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid value. Enter a positive number (e.g. 350).")
        return
    await state.update_data(kg_per_m3=kg_m3)
    await state.set_state(ConsumeStates.cubic_meters)
    await message.answer(f"✅ kg/m³ set to <b>{kg_m3}</b>\n\nStep 4/4 — Enter <b>m³ Done</b>:", parse_mode="HTML", reply_markup=back_kb())

@router.message(ConsumeStates.cubic_meters)
async def consume_cubic_meters(message: Message, state: FSMContext, db_user):
    if message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Operation cancelled.")
        return
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("🏠 Main Menu", reply_markup=main_menu(db_user.role))
        return
    if message.text == "🔙 Back":
        await state.set_state(ConsumeStates.kg_per_m3)
        await message.answer("Step 3/4 — Enter <b>Cement per m³ (kg/m³)</b>:", parse_mode="HTML", reply_markup=cancel_kb())
        return
    try:
        m3 = float(message.text.strip().replace(",", ""))
        if m3 <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Invalid value. Enter a positive number (e.g. 120).")
        return
    data = await state.get_data()
    kg_per_m3 = data.get("kg_per_m3", 0)
    if kg_per_m3 <= 0:
        await message.answer("❌ Invalid kg/m³. Please restart the process.")
        await state.clear()
        return
    total_kg = m3 * kg_per_m3
    await state.update_data(cubic_meters=m3, total_kg=total_kg)
    await state.set_state(ConsumeStates.confirm)
    await message.answer(
        f"📋 <b>Confirm Consumption</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🏗️ Project:      <b>{data.get('project_name', 'N/A')}</b>\n📊 Grade:        <b>{data.get('concrete_grade', 'N/A')}</b>\n📐 m³ Done:      <b>{m3} m³</b>\n⚖️  kg/m³:        <b>{kg_per_m3} kg/m³</b>\n🧱 Total Used:   <b>{fmt_kg(total_kg)}</b>\n━━━━━━━━━━━━━━━━━━━━━━\nConfirm?",
        parse_mode="HTML", reply_markup=confirm_kb()
    )

@router.message(ConsumeStates.confirm)
async def consume_confirm(message: Message, state: FSMContext, db_user, bot):
    if message.text == "🔙 Back":
        await state.set_state(ConsumeStates.cubic_meters)
        await message.answer("Step 4/4 — Enter <b>m³ Done</b>:", parse_mode="HTML", reply_markup=back_kb())
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
