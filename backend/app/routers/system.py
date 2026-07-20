"""Host system monitoring endpoint."""

from fastapi import APIRouter, Depends

from .. import sysmon
from ..deps import require_superadmin
from ..schemas import SystemStats

# Host CPU / RAM / disk / hostname / kernel describe the OPERATOR's node, not a
# reseller's tenancy — superadmin only.
router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_superadmin)])


@router.get("", response_model=SystemStats)
async def system_stats():
    return sysmon.collect()
