"""Traffic time-series for dashboard charts."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_superadmin
from ..models import TrafficSample
from ..schemas import TrafficPoint

# TrafficSample is a NODE-WIDE aggregate (every user's throughput and the total
# online count) with no per-owner breakdown, so it cannot be scoped — a reseller
# would read the whole platform's load off it. Superadmin only; the reseller
# dashboard simply does not render this chart.
router = APIRouter(prefix="/api/traffic", tags=["traffic"], dependencies=[Depends(require_superadmin)])


@router.get("/history", response_model=list[TrafficPoint])
async def history(
    minutes: int = Query(60, ge=1, le=1440),
    db: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (
        await db.execute(
            select(TrafficSample)
            .where(TrafficSample.ts >= since)
            .order_by(TrafficSample.ts.asc())
        )
    ).scalars().all()
    return [
        TrafficPoint(ts=r.ts, online_count=r.online_count, rx_bps=r.rx_bps, tx_bps=r.tx_bps)
        for r in rows
    ]
