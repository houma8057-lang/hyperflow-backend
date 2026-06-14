from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import WSIHistory, Wallet
from datetime import datetime
import httpx

router = APIRouter()

async def calculate_signal(db: AsyncSession):
    # 1. WSI
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return None
    
    latest_wsi = (await db.execute(
        select(WSIHistory).order_by(desc(WSIHistory.timestamp)).limit(1)
    )).scalar_one_or_none()
    
    wsi = latest_wsi.wsi_value if latest_wsi else 0

    # 2. Funding Rate (BTC)
    avg_funding = 0
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            if len(data) >= 2:
                for i, asset in enumerate(data[0].get("universe", [])):
                    if asset["name"] == "BTC":
                        avg_funding = float(data[1][i].get("funding", 0))
                        break
            
            # BTC Price
            btc_price = 0
            for i, asset in enumerate(data[0].get("universe", [])):
                if asset["name"] == "BTC":
                    btc_price = float(data[1][i].get("markPx", 0))
                    break
        except:
            btc_price = 0

    # 3. Whale Direction
    whale_short = False
    whale_long = False
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            total_long = 0
            total_short = 0
            meta_resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            meta_data = meta_resp.json()
            price_map = {}
            if len(meta_data) >= 2:
                for i, asset in enumerate(meta_data[0].get("universe", [])):
                    try:
                        price_map[asset["name"]] = float(meta_data[1][i]["markPx"])
                    except:
                        pass
            
            for wallet in wallets:
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
            
            total = total_long + total_short
            if total > 0:
                whale_short = total_short / total > 0.65
                whale_long = total_long / total > 0.65
        except:
            pass

    # Signal Logic
    buy_conditions = [
        wsi <= -0.8,
        avg_funding < -0.001,
        whale_short
    ]
    sell_conditions = [
        wsi >= 0.8,
        avg_funding > 0.001,
        whale_long
    ]

    buy_count = sum(buy_conditions)
    sell_count = sum(sell_conditions)

    if buy_count >= 2:
        signal = "STRONG BUY" if buy_count == 3 else "WEAK BUY"
    elif sell_count >= 2:
        signal = "STRONG SELL" if sell_count == 3 else "WEAK SELL"
    else:
        signal = "NEUTRAL"

    return {
        "signal": signal,
        "btc_price": round(btc_price, 2),
        "timestamp": datetime.utcnow().isoformat(),
        "conditions": {
            "wsi": round(wsi, 3),
            "wsi_met_buy": wsi <= -0.8,
            "wsi_met_sell": wsi >= 0.8,
            "funding": round(avg_funding * 100, 4),
            "funding_met_buy": avg_funding < -0.001,
            "funding_met_sell": avg_funding > 0.001,
            "whale_short": whale_short,
            "whale_long": whale_long
        },
        "buy_conditions_met": buy_count,
        "sell_conditions_met": sell_count
    }

@router.get("/signals/current")
async def get_current_signal(db: AsyncSession = Depends(get_db)):
    result = await calculate_signal(db)
    if not result:
        return {"signal": "NEUTRAL", "btc_price": 0, "conditions": {}, "buy_conditions_met": 0, "sell_conditions_met": 0}
    return result
