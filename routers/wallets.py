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

@router.get("/diag/data-range-check")
async def diag_data_range_check(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Reports the earliest and latest
    date/timestamp available in each historical table, to determine
    which components of the weighted score can realistically be
    backtested against past top/bottom events. Safe to remove once
    its job is done."""
    from sqlalchemy import func as sqlfunc, select
    from models import MVRVHistory, OIHistory, PositionSnapshot

    result = {}

    # mvrv_history: use the `date` string column (reliable), not timestamp
    mvrv_min = (await db.execute(select(sqlfunc.min(MVRVHistory.date)))).scalar()
    mvrv_max = (await db.execute(select(sqlfunc.max(MVRVHistory.date)))).scalar()
    mvrv_count = (await db.execute(select(sqlfunc.count(MVRVHistory.id)))).scalar()
    mvrv_with_price = (await db.execute(
        select(sqlfunc.count(MVRVHistory.id)).where(MVRVHistory.btc_price.isnot(None))
    )).scalar()
    result["mvrv_history"] = {
        "earliest_date": mvrv_min,
        "latest_date": mvrv_max,
        "total_rows": mvrv_count,
        "rows_with_btc_price": mvrv_with_price,
    }

    # oi_history: only has `timestamp`, no date string column
    oi_min = (await db.execute(select(sqlfunc.min(OIHistory.timestamp)))).scalar()
    oi_max = (await db.execute(select(sqlfunc.max(OIHistory.timestamp)))).scalar()
    oi_count = (await db.execute(select(sqlfunc.count(OIHistory.id)))).scalar()
    result["oi_history"] = {
        "earliest_timestamp": oi_min.isoformat() if oi_min else None,
        "latest_timestamp": oi_max.isoformat() if oi_max else None,
        "total_rows": oi_count,
    }

    # positions_snapshot: only has `timestamp`
    pos_min = (await db.execute(select(sqlfunc.min(PositionSnapshot.timestamp)))).scalar()
    pos_max = (await db.execute(select(sqlfunc.max(PositionSnapshot.timestamp)))).scalar()
    pos_count = (await db.execute(select(sqlfunc.count(PositionSnapshot.id)))).scalar()
    result["positions_snapshot"] = {
        "earliest_timestamp": pos_min.isoformat() if pos_min else None,
        "latest_timestamp": pos_max.isoformat() if pos_max else None,
        "total_rows": pos_count,
    }

    return result

@router.get("/diag/metric-cache-check")
async def diag_metric_cache_check(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Confirms whether metric_cache holds
    any historical series or just the single latest value per metric key
    (NUPL, SOPR have no dedicated history table like mvrv_history does).
    Safe to remove once its job is done."""
    from sqlalchemy import select
    from models import MetricCache

    rows = (await db.execute(select(MetricCache))).scalars().all()
    return {
        "total_rows": len(rows),
        "rows": [
            {"metric": r.metric, "value": r.value, "fetched_at": r.fetched_at.isoformat()}
            for r in rows
        ],
    }
