from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import WSIHistory, Wallet
from datetime import datetime, timedelta
import httpx

router = APIRouter()

async def fetch_wsi_live(db: AsyncSession):
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return {"wsi": 0.0, "long_pct": 0.0, "short_pct": 0.0, "total_ntl": 0.0, "wallet_count": 0}
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
        meta_data = meta_resp.json()
        price_map = {}
        if len(meta_data) >= 2:
            for i, asset in enumerate(meta_data[0].get("universe", [])):
                try:
                    price_map[asset["name"]] = float(meta_data[1][i]["markPx"])
                except:
                    pass
        total_long = 0.0
        total_short = 0.0
        for wallet in wallets:
            try:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": wallet.address})
                data = resp.json()
                for ap in data.get("assetPositions", []):
                    pos = ap.get("position", {})
                    szi = float(pos.get("szi", 0))
                    coin = pos.get("coin", "")
                    mark_px = price_map.get(coin, 0)
                    if mark_px == 0 or szi == 0:
                        continue
                    notional = abs(szi) * mark_px
                    if szi > 0:
                        total_long += notional
                    else:
                        total_short += notional
            except:
                continue
    total = total_long + total_short
    wsi = round((total_long - total_short) / total, 3) if total > 0 else 0.0
    long_pct = round(total_long / total * 100, 1) if total > 0 else 0.0
    short_pct = round(total_short / total * 100, 1) if total > 0 else 0.0
    return {"wsi": wsi, "long_pct": long_pct, "short_pct": short_pct, "total_ntl": round(total, 2), "wallet_count": len(wallets)}

@router.get("/sentiment/current")
async def get_current(db: AsyncSession = Depends(get_db)):
    return await fetch_wsi_live(db)

@router.get("/sentiment/history")
async def get_history(days: int = 30, db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(select(WSIHistory).where(WSIHistory.timestamp >= since).order_by(WSIHistory.timestamp))).scalars().all()
    return [{"timestamp": r.timestamp, "wsi_value": r.wsi_value, "total_long_ntl": r.total_long_ntl, "total_short_ntl": r.total_short_ntl, "reversal_score": r.reversal_score} for r in rows]
