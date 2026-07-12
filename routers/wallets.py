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

@router.get("/diag/whale-flip-detail")
async def diag_whale_flip_detail(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. For each wallet, shows every
    position row from its OLDEST snapshot moment within the 24h window
    - the same data prev_sides is built from in detect_whale_flips - to
    check whether wallets hold multiple coins with mixed sides there,
    which a single wallet_address->side dict cannot represent, unlike
    current_side which is notional-weighted across all positions."""
    from models import PositionSnapshot
    from sqlalchemy import select, desc
    from datetime import datetime, timedelta

    ago24 = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(PositionSnapshot)
        .where(PositionSnapshot.timestamp >= ago24)
        .order_by(desc(PositionSnapshot.timestamp))
    )
    snapshots = result.scalars().all()

    oldest_ts = {}
    for s in snapshots:
        oldest_ts[s.wallet_address] = s.timestamp

    detail = {}
    for s in snapshots:
        if s.timestamp == oldest_ts.get(s.wallet_address):
            detail.setdefault(s.wallet_address, []).append(
                {"coin": s.coin, "side": s.side, "notional": s.notional}
            )

    return {
        addr: {"position_count": len(rows), "positions": rows}
        for addr, rows in detail.items()
    }

@router.get("/diag/db-ping")
async def diag_db_ping(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Times a trivial round-trip with zero
    table/query complexity, to isolate whether the latency is pure
    network/connection overhead to Supabase vs anything about our tables."""
    import time
    from sqlalchemy import text
    t0 = time.monotonic()
    await db.execute(text("SELECT 1"))
    return {"select_1_seconds": round(time.monotonic() - t0, 3)}

@router.get("/diag/whale-flip-compare")
async def diag_whale_flip_compare(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Recomputes prev_side (oldest 24h,
    notional-weighted) and current_side (live, notional-weighted) for
    each wallet side by side, to verify whether whale_flips=0 is correct
    (all sides match) or a bug remains (some differ but were not
    counted)."""
    from models import PositionSnapshot, Wallet
    from sqlalchemy import select, desc
    from datetime import datetime, timedelta
    import httpx

    ago24 = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(PositionSnapshot)
        .where(PositionSnapshot.timestamp >= ago24)
        .order_by(desc(PositionSnapshot.timestamp))
    )
    snapshots = result.scalars().all()

    oldest_ts = {}
    for s in snapshots:
        oldest_ts[s.wallet_address] = s.timestamp

    prev_long = {}
    prev_short = {}
    for s in snapshots:
        if s.timestamp != oldest_ts.get(s.wallet_address):
            continue
        if s.side == "LONG":
            prev_long[s.wallet_address] = prev_long.get(s.wallet_address, 0.0) + s.notional
        else:
            prev_short[s.wallet_address] = prev_short.get(s.wallet_address, 0.0) + s.notional

    prev_side = {}
    for addr in set(list(prev_long.keys()) + list(prev_short.keys())):
        prev_side[addr] = "LONG" if prev_long.get(addr, 0.0) > prev_short.get(addr, 0.0) else "SHORT"

    wallets = (await db.execute(select(Wallet))).scalars().all()
    current_side = {}
    async with httpx.AsyncClient(timeout=20) as client:
        meta_resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
        meta_data = meta_resp.json()
        price_map = {}
        if isinstance(meta_data, list) and len(meta_data) >= 2:
            for i, asset in enumerate(meta_data[0].get("universe", [])):
                try:
                    price_map[asset["name"]] = float(meta_data[1][i]["markPx"])
                except Exception:
                    pass
        for w in wallets:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": w.address})
            data = resp.json()
            long_ntl = 0.0
            short_ntl = 0.0
            for ap in data.get("assetPositions", []):
                pos = ap.get("position", {})
                szi = float(pos.get("szi", 0))
                coin = pos.get("coin", "")
                px = price_map.get(coin, 0)
                if px == 0 or szi == 0:
                    continue
                ntl = abs(szi) * px
                if szi > 0:
                    long_ntl += ntl
                else:
                    short_ntl += ntl
            current_side[w.address] = "LONG" if long_ntl > short_ntl else "SHORT"

    comparison = []
    for addr in current_side:
        comparison.append({
            "wallet": addr,
            "prev_side": prev_side.get(addr, "NO_DATA"),
            "current_side": current_side[addr],
            "flipped": prev_side.get(addr) is not None and prev_side.get(addr) != current_side[addr]
        })

    return comparison

@router.get("/diag/positions-count")
async def diag_positions_count(db: AsyncSession = Depends(get_db)):
    """Temporary diagnostic endpoint. Safe to remove once its job is done."""
    from sqlalchemy import func as sqlfunc
    from models import PositionSnapshot, OIHistory
    positions_total = (await db.execute(select(sqlfunc.count(PositionSnapshot.id)))).scalar()
    oi_total = (await db.execute(select(sqlfunc.count(OIHistory.id)))).scalar()
    return {"positions_snapshot_rows": positions_total, "oi_history_rows": oi_total}

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
