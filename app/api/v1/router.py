from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.items.routes import router as items_router
from app.modules.admin.routes import router as admin_router

router = APIRouter()

router.include_router(health_router, tags=["health"])
router.include_router(items_router, prefix="/items", tags=["items"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])