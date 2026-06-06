from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import PositionSnapshot
from datetime import datetime, timedelta

router = APIRouter()

@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(minutes=10)
    rows = (await db.execute(select(PositionSnapshot).where(PositionSnapshot.timestamp >= since).order_by(desc(PositionSnapshot.notional)))).scalars().all()
    return [{"wallet": r.wallet_address, "coin": r.coin, "side": r.side, "notional": r.notional, "szi": r.szi, "pnl": r.unrealized_pnl} for r in rows]
