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
