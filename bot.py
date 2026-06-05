import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from config import config
from database.queries import init_db
from handlers import common_router, storekeeper_router, operator_router, reports_router, admin_router
from middlewares.auth import AuthMiddleware
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
    dp.include_router(admin_router)
    dp.include_router(storekeeper_router)
    dp.include_router(operator_router)
    dp.include_router(reports_router)
    dp.include_router(common_router)
    logger.info("Starting bot polling...")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    asyncio.run(main())
