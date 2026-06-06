import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///cement_stock.db")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DEFAULT_LOW_STOCK_KG = float(os.getenv("DEFAULT_LOW_STOCK_KG", "20000"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    TIMEZONE = os.getenv("TIMEZONE", "Africa/Addis_Ababa")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin_token")
    STOREKEEPER_TOKEN = os.getenv("STOREKEEPER_TOKEN", "storekeeper_token")
    OPERATOR_TOKEN = os.getenv("OPERATOR_TOKEN", "operator_token")

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN required in .env")
        if cls.ADMIN_TOKEN == "admin_token":
            print("⚠️ WARNING: Using default ADMIN_TOKEN – change in .env")
        if cls.STOREKEEPER_TOKEN == "storekeeper_token":
            print("⚠️ WARNING: Using default STOREKEEPER_TOKEN – change in .env")
        if cls.OPERATOR_TOKEN == "operator_token":
            print("⚠️ WARNING: Using default OPERATOR_TOKEN – change in .env")

config = Config()
