"""Host system monitoring endpoint."""

from fastapi import APIRouter, Depends

from .. import sysmon
from ..deps import require_admin
from ..schemas import SystemStats

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_admin)])


@router.get("", response_model=SystemStats)
async def system_stats():
    return sysmon.collect()
