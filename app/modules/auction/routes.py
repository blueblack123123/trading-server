from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping_auction() -> dict[str, str]:
    return {"auction": "ok"}