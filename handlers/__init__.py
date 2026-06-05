from .common import router as common_router
from .storekeeper import router as storekeeper_router
from .operator import router as operator_router
from .reports import router as reports_router
from .admin import router as admin_router

__all__ = [
    "common_router",
    "storekeeper_router",
    "operator_router",
    "reports_router",
    "admin_router",
]
