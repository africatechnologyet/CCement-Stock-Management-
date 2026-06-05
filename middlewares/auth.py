from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from database.queries import AsyncSessionLocal, get_user_by_telegram_id

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: Dict[str, Any]) -> Any:
        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
        db_user = None
        if tg_user:
            async with AsyncSessionLocal() as session:
                db_user = await get_user_by_telegram_id(session, tg_user.id)
        data["db_user"] = db_user
        return await handler(event, data)
