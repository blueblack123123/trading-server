from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_db_session
from app.modules.crafting.schemas import CraftAnalysisResponse
from app.modules.crafting.service import read_latest_analysis

router = APIRouter()


@router.get("/analysis/latest")
async def get_latest_craft_analysis(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CraftAnalysisResponse:
    analysis = await read_latest_analysis(session)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="craft analysis has not been calculated yet",
        )
    return analysis
