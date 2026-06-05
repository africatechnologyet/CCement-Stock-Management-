from datetime import datetime
import pytz
from config import config

TZ = pytz.timezone(config.TIMEZONE)

def fmt_kg(value: float) -> str:
    return f"{value:,.0f} kg"

def fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    local = dt.astimezone(TZ)
    return local.strftime("%d-%b-%Y %H:%M")

def now_local() -> datetime:
    return datetime.now(TZ)

def stock_status_text(current: float, threshold: float) -> str:
    if current <= threshold:
        return "⚠️ LOW STOCK"
    elif current <= threshold * 1.5:
        return "🟡 MODERATE"
    return "✅ NORMAL"
