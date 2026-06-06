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
