from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Wallet
import httpx

router = APIRouter()

@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return {"summary": [], "detail": []}
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
        detail = []
        for wallet in wallets:
            try:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": wallet.address})
                data = resp.json()
                for ap in data.get("assetPositions", []):
                    pos = ap.get("position", {})
                    szi = float(pos.get("szi", 0))
                    if szi == 0:
                        continue
                    coin = pos.get("coin", "")
                    mark_px = price_map.get(coin, 0)
                    notional = abs(szi) * mark_px
                    detail.append({
                        "wallet_address": wallet.address,
                        "label": wallet.label,
                        "coin": coin,
                        "side": "LONG" if szi > 0 else "SHORT",
                        "szi": szi,
                        "notional": round(notional, 2),
                        "leverage": pos.get("leverage", {}).get("value", 1),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0))
                    })
            except:
                continue
    return {"summary": [], "detail": detail}
