from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import settings
from app.modules.admin.schemas import MarketItemConfig
from app.modules.admin.service import MarketItemsConfigService

router = APIRouter()


def check_admin_key(
    x_admin_key: str | None = Header(default=None),
) -> None:
    if not settings.admin_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_KEY is not configured",
        )

    if x_admin_key != settings.admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )


@router.get("/market-items-config")
async def get_market_items_config(
    _: None = Depends(check_admin_key),
) -> list[MarketItemConfig]:
    return MarketItemsConfigService().get_config()


@router.put("/market-items-config")
async def update_market_items_config(
    items: list[MarketItemConfig],
    _: None = Depends(check_admin_key),
) -> dict[str, int]:
    MarketItemsConfigService().save_config(items)

    return {
        "count": len(items),
    }


@router.post("/sync-market-items")
async def sync_market_items(
    _: None = Depends(check_admin_key),
) -> dict[str, int]:
    items = MarketItemsConfigService().sync_items()

    return {
        "count": len(items),
    }