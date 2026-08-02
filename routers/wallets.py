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

@router.get("/diag/bgeometrics-raw")
async def diag_bgeometrics_raw(limit: int = 1000):
    """Temporary diagnostic endpoint. Calls BGeometrics mvrv-zscore with a
    given limit and returns HTTP status, raw response type/length, and a
    sample item verbatim - to see exactly why a large-limit historical
    request returned nothing usable, instead of guessing. Safe to remove
    once the backfill works."""
    import httpx
    from services.bgeometrics import BGEOMETRICS_TOKEN, BASE_URL

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/mvrv-zscore",
            params={"token": BGEOMETRICS_TOKEN, "limit": limit}
        )
        result = {
            "requested_limit": limit,
            "http_status": resp.status_code,
            "raw_text_first_500": resp.text[:500],
        }
        try:
            data = resp.json()
            result["parsed_type"] = type(data).__name__
            if isinstance(data, list):
                result["list_length"] = len(data)
                result["first_item"] = data[0] if data else None
                result["last_item"] = data[-1] if data else None
            else:
                result["parsed_value"] = data
        except Exception as e:
            result["json_parse_error"] = str(e)
        return result

@router.get("/diag/backfill-mvrv-price-history")
async def diag_backfill_mvrv_price_history(db: AsyncSession = Depends(get_db)):
    """Temporary one-time diagnostic endpoint. Backfills mvrv_history with
    up to ~1000 days of historical MVRV Z-score (BGeometrics) and BTC daily
    close price (Binance), matched by date, so the planned price-vs-onchain
    divergence detector has real multi-year depth immediately instead of
    waiting months for live collection to accumulate it.

    Safe to re-run: existing rows (from the live daily snapshot job) only
    get their btc_price filled in if missing; their zscore is never
    touched. Only genuinely new (older) dates get inserted. Remove this
    endpoint once a successful run is confirmed."""
    import httpx
    from datetime import datetime, timezone
    from models import MVRVHistory
    from services.bgeometrics import BGEOMETRICS_TOKEN, BASE_URL

    mvrv_by_date = {}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/mvrv-zscore",
            params={"token": BGEOMETRICS_TOKEN, "limit": 1000}
        )
        http_status = resp.status_code
        try:
            data = resp.json()
        except Exception as e:
            return {"status": "error", "stage": "bgeometrics", "http_status": http_status, "detail": f"invalid JSON: {e}", "raw_snippet": resp.text[:300]}
        if not isinstance(data, list):
            return {"status": "error", "stage": "bgeometrics", "http_status": http_status, "detail": f"response is {type(data).__name__}, not a list", "raw_value": data}
        if len(data) == 0:
            return {"status": "error", "stage": "bgeometrics", "http_status": http_status, "detail": "response was an empty list"}
        for item in data:
            if not isinstance(item, dict) or "d" not in item:
                continue
            val = None
            for key in item:
                if key not in ("d", "unixTs"):
                    v = item[key]
                    if v is not None and str(v) != "NaN":
                        try:
                            val = float(v)
                        except (TypeError, ValueError):
                            val = None
                    break
            if val is None:
                continue
            try:
                d = datetime.strptime(item["d"], "%d-%m-%Y").date().isoformat()
            except Exception:
                continue
            mvrv_by_date[d] = val

    if not mvrv_by_date:
        return {
            "status": "error", "stage": "bgeometrics", "http_status": http_status,
            "detail": "list had items but none parsed into a usable date+value",
            "list_length": len(data), "sample_item": data[0]
        }

    oldest_date = min(mvrv_by_date.keys())
    start_ms = int(datetime.strptime(oldest_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    price_by_date = {}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1d", "startTime": start_ms, "limit": 1000}
        )
        try:
            klines = resp.json()
        except Exception as e:
            return {"status": "error", "stage": "binance", "detail": f"invalid JSON: {e}"}
        if not isinstance(klines, list):
            return {"status": "error", "stage": "binance", "detail": f"unexpected response: {klines}"}
        for k in klines:
            try:
                d = datetime.utcfromtimestamp(k[0] / 1000).date().isoformat()
                price_by_date[d] = float(k[4])
            except Exception:
                continue

    combined = {d: (mvrv_by_date[d], price_by_date[d]) for d in mvrv_by_date if d in price_by_date}
    if not combined:
        return {
            "status": "error", "stage": "merge", "detail": "no overlapping dates between BGeometrics and Binance",
            "bgeometrics_dates": len(mvrv_by_date), "binance_dates": len(price_by_date),
        }

    existing_rows = (await db.execute(select(MVRVHistory))).scalars().all()
    existing_by_date = {r.date: r for r in existing_rows}

    inserted = 0
    updated = 0
    for d, (zscore, price) in combined.items():
        if d in existing_by_date:
            row = existing_by_date[d]
            if row.btc_price is None:
                row.btc_price = price
                updated += 1
        else:
            db.add(MVRVHistory(
                timestamp=datetime.now(timezone.utc),
                date=d,
                zscore=zscore,
                btc_price=price
            ))
            inserted += 1

    await db.commit()
    return {
        "status": "ok",
        "bgeometrics_dates": len(mvrv_by_date),
        "binance_dates": len(price_by_date),
        "matched_dates": len(combined),
        "inserted": inserted,
        "updated_existing_with_price": updated,
        "date_range": {"oldest": min(combined.keys()), "newest": max(combined.keys())}
    }
