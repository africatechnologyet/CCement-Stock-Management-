from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from database.models import UserRole

REMOVE_KB = ReplyKeyboardRemove()

def main_menu(role: UserRole) -> ReplyKeyboardMarkup:
    base = [[KeyboardButton(text="📦 Stock Status")]]
    if role == UserRole.ADMIN:
        buttons = base + [
            [KeyboardButton(text="📥 Receive"), KeyboardButton(text="📤 Consume")],
            [KeyboardButton(text="⚙️ Adjust Stock"), KeyboardButton(text="🔔 Set Min Level")],
            [KeyboardButton(text="👥 Users"), KeyboardButton(text="📋 Recipients")],
            [KeyboardButton(text="📊 Daily Report"), KeyboardButton(text="📈 Monthly Report")],
            [KeyboardButton(text="🕵️ Audit Log"), KeyboardButton(text="📜 History")],
            [KeyboardButton(text="🔗 Invite Links"), KeyboardButton(text="🗑️ Remove Recipient")],
        ]
    elif role == UserRole.STOREKEEPER:
        buttons = base + [[KeyboardButton(text="📥 Receive")], [KeyboardButton(text="📜 History")]]
    elif role == UserRole.OPERATOR:
        buttons = base + [[KeyboardButton(text="📤 Consume")], [KeyboardButton(text="📜 History")]]
    elif role == UserRole.MANAGEMENT:
        buttons = base + [[KeyboardButton(text="📊 Daily Report"), KeyboardButton(text="📈 Monthly Report")]]
    else:
        buttons = base
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def adjustment_type_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Add Stock"), KeyboardButton(text="➖ Deduct Stock")], [KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def confirm_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Confirm"), KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Back"), KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def back_confirm_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Confirm"), KeyboardButton(text="🔙 Back"), KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def skip_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏭️ Skip"), KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def skip_back_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏭️ Skip"), KeyboardButton(text="🔙 Back"), KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def role_selection_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🏪 Storekeeper"), KeyboardButton(text="⚙️ Operator")],
        [KeyboardButton(text="📊 Management"), KeyboardButton(text="🔑 Admin")],
        [KeyboardButton(text="❌ Cancel")],
    ], resize_keyboard=True)

def group_type_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👥 Group"), KeyboardButton(text="👤 Individual")],
        [KeyboardButton(text="🔙 Back"), KeyboardButton(text="❌ Cancel")],
    ], resize_keyboard=True)
