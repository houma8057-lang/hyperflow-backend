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

@router.get("/diag/rsi-failure-swing-backtest")
async def diag_rsi_failure_swing_backtest(
    start_date: str = "2025-01-01",
    end_date: str = "2025-11-01",
    step_days: int = 7,
    db: AsyncSession = Depends(get_db)
):
    """Temporary diagnostic endpoint. Walks weekly through [start_date,
    end_date], recomputing RSI and running both failure-swing detectors
    using only price data on or before each checkpoint date - simulating
    what would have been knowable at that point in time, not hindsight.
    Default range covers the 2025 topping process (Oct 6 top). Call again
    with start_date=2022-08-01&end_date=2023-02-01 for the Nov 2022 FTX
    bottom. Safe to remove once reviewed."""
    from services.rsi_divergence import (
        compute_rsi_series, find_local_extrema,
        detect_bearish_failure_swing, detect_bullish_failure_swing
    )
    from datetime import datetime, timedelta
    from models import MVRVHistory

    all_rows = (await db.execute(
        select(MVRVHistory)
        .where(MVRVHistory.btc_price.isnot(None))
        .order_by(MVRVHistory.date)
    )).scalars().all()
    all_prices = [(r.date, r.btc_price) for r in all_rows]

    results = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while current <= end:
        as_of = current.date().isoformat()
        truncated = [(d, p) for d, p in all_prices if d <= as_of]
        rsi = compute_rsi_series(truncated, period=14)
        if len(rsi) < 30:
            results.append({"as_of": as_of, "price": None, "rsi": None, "note": "insufficient history"})
            current += timedelta(days=step_days)
            continue
        extrema = find_local_extrema(rsi, window=5)
        bearish = detect_bearish_failure_swing(rsi, extrema)
        bullish = detect_bullish_failure_swing(rsi, extrema)
        results.append({
            "as_of": as_of,
            "price": truncated[-1][1],
            "rsi": round(rsi[-1][1], 2),
            "bearish_stage": bearish.get("stage"),
            "bearish_break_date": bearish.get("break_date"),
            "bullish_stage": bullish.get("stage"),
            "bullish_break_date": bullish.get("break_date"),
        })
        current += timedelta(days=step_days)

    return {"results": results, "count": len(results)}
