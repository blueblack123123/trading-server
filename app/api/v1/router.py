from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.auction.routes import router as auction_router

router = APIRouter()

router.include_router(health_router, tags=["health"])
router.include_router(auction_router, prefix="/auction", tags=["auction"])