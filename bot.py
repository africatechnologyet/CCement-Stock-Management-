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
