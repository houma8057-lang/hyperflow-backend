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

@router.get("/diag/divergence-check")
async def diag_divergence_check(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Runs the new price-vs-onchain
    divergence detector against live mvrv_history data and returns its
    full output, to sanity-check it against known reality before wiring
    it into the live signal. Expect is_active=false most of the time -
    this fires only near actual topping/bottoming moments, not
    continuously. Safe to remove once reviewed."""
    from services.divergence import detect_price_onchain_divergence
    return await detect_price_onchain_divergence(db)


@router.get("/diag/divergence-backtest")
async def diag_divergence_backtest(
    start_date: str = "2024-08-01",
    end_date: str = "2025-11-01",
    step_days: int = 7,
    db: AsyncSession = Depends(get_db)
):
    """Temporary diagnostic endpoint. Re-runs the exact same divergence
    logic as-of each date in [start_date, end_date] (every step_days),
    using only rows dated on or before that date - to see whether it
    would have flagged bearish divergence at some point during the
    actual 2025 topping process (Z-score peaked 2024-12-11, price
    peaked 2025-10-06), instead of only checking today (too far past
    both events for the rolling window to still contain them). Safe to
    remove once reviewed."""
    from services.divergence import WINDOW_DAYS, _assess_divergence
    from datetime import datetime, timedelta
    from models import MVRVHistory

    all_rows = (await db.execute(
        select(MVRVHistory)
        .where(MVRVHistory.btc_price.isnot(None))
        .order_by(MVRVHistory.date)
    )).scalars().all()

    results = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while current <= end:
        as_of = current.date().isoformat()
        window_start = (current - timedelta(days=WINDOW_DAYS)).date().isoformat()
        window_rows = [r for r in all_rows if window_start <= r.date <= as_of]
        assessment = _assess_divergence(window_rows)
        results.append({
            "as_of": as_of,
            "price": assessment.get("latest_price"),
            "zscore": assessment.get("latest_zscore"),
            "price_from_high_pct": assessment.get("price_from_high_pct"),
            "z_gap_from_high": assessment.get("z_gap_from_high"),
            "direction": assessment.get("direction"),
        })
        current += timedelta(days=step_days)

    return {"results": results, "count": len(results)}
