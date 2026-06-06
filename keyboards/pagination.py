from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def pagination_kb(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup | None:
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"{prefix}_{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{prefix}_{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
