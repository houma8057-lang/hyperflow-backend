from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Wallet
from pydantic import BaseModel
import re

router = APIRouter()

class WalletIn(BaseModel):
    address: str
    label: str = ""

@router.post("/wallets")
async def add_wallet(data: WalletIn, db: AsyncSession = Depends(get_db)):
    if not re.match(r"^0x[a-fA-F0-9]{40}$", data.address):
        raise HTTPException(400, "Invalid address format")
    existing = (await db.execute(select(Wallet).where(Wallet.address == data.address))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Wallet already exists")
    w = Wallet(address=data.address, label=data.label)
    db.add(w)
    await db.commit()
    return {"address": w.address, "label": w.label}

@router.get("/wallets")
async def get_wallets(db: AsyncSession = Depends(get_db)):
    wallets = (await db.execute(select(Wallet))).scalars().all()
    return [{"address": w.address, "label": w.label, "id": w.id} for w in wallets]

@router.delete("/wallets/{address}")
async def delete_wallet(address: str, db: AsyncSession = Depends(get_db)):
    w = (await db.execute(select(Wallet).where(Wallet.address == address))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Wallet not found")
    await db.delete(w)
    await db.commit()
    return {"deleted": address}

@router.get("/diag/data-range")
async def diag_data_range(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Reports oldest/newest timestamp and
    row count for OIHistory and PositionSnapshot, to check whether there's
    enough historical depth for OI-threshold recalibration or a long-window
    whale-direction gauge before designing either. Safe to remove after."""
    from sqlalchemy import func as sqlfunc
    from models import OIHistory, PositionSnapshot

    oi_stats = (await db.execute(
        select(sqlfunc.min(OIHistory.timestamp), sqlfunc.max(OIHistory.timestamp), sqlfunc.count(OIHistory.id))
    )).one()
    pos_stats = (await db.execute(
        select(sqlfunc.min(PositionSnapshot.timestamp), sqlfunc.max(PositionSnapshot.timestamp), sqlfunc.count(PositionSnapshot.id))
    )).one()

    return {
        "oi_history": {"oldest": oi_stats[0], "newest": oi_stats[1], "rows": oi_stats[2]},
        "positions_snapshot": {"oldest": pos_stats[0], "newest": pos_stats[1], "rows": pos_stats[2]},
    }
