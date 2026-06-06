from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import WSIHistory
from datetime import datetime, timedelta

router = APIRouter()

@router.get("/sentiment/current")
async def get_current(db: AsyncSession = Depends(get_db)):
    latest = (await db.execute(select(WSIHistory).order_by(desc(WSIHistory.timestamp)).limit(1))).scalar_one_or_none()
    if not latest:
        return {"wsi": 0.0, "long_pct": 0.0, "short_pct": 0.0, "total_ntl": 0.0, "wallet_count": 0}
    return {"wsi": latest.wsi_value, "total_long_ntl": latest.total_long_ntl, "total_short_ntl": latest.total_short_ntl, "wallet_count": latest.wallet_count, "timestamp": latest.timestamp}

@router.get("/sentiment/history")
async def get_history(days: int = 30, db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(select(WSIHistory).where(WSIHistory.timestamp >= since).order_by(WSIHistory.timestamp))).scalars().all()
    return [{"timestamp": r.timestamp, "wsi_value": r.wsi_value, "total_long_ntl": r.total_long_ntl, "total_short_ntl": r.total_short_ntl, "reversal_score": r.reversal_score} for r in rows]
