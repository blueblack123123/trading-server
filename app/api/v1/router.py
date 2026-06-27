from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.items.routes import router as items_router

router = APIRouter()

router.include_router(health_router, tags=["health"])
router.include_router(items_router, prefix="/items", tags=["items"])