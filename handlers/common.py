from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from database.models import UserRole, User
from database.queries import AsyncSessionLocal, create_user, get_current_stock, get_low_stock_threshold, add_audit_log, redeem_invite
from keyboards.reply import main_menu, REMOVE_KB
from utils.formatters import fmt_kg, fmt_dt, stock_status_text, now_local
from utils.logger import logger

router = Router()

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
    if token:
        async with AsyncSessionLocal() as session:
            role, status = await redeem_invite(session, token, message.from_user.id, message.from_user.full_name, message.from_user.username)
            if status == "ok":
                user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
                await add_audit_log(session, message.from_user.id, "REGISTERED_VIA_INVITE_LINK", f"Registered as {role.value}", user.id if user else None)
        if status == "ok":
            await message.answer(f"👋 Welcome, {message.from_user.full_name}!\n\n✅ You are registered as {role.value.title()}.", parse_mode="HTML", reply_markup=main_menu(role))
            return
        if status == "registered":
            await message.answer("⚠️ Your Telegram account is already registered. Ask an administrator to restore or change your access.")
            return
        await message.answer("❌ This invitation is invalid, expired, or already used. Ask an administrator for a new link.")
        return
    await message.answer("👋 Welcome to the Cement Stock Bot.\n\nYou are not registered. Please ask an administrator for an invite link.", parse_mode="HTML")

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
