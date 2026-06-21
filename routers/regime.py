from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import WSIHistory, Wallet
from services.regime import WhaleRegimeDetector
from datetime import datetime
import httpx

router = APIRouter()

async def get_regime_data(db: AsyncSession):
    """Gather all data needed for regime detection"""
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return None

    # Get latest WSI
    latest_wsi = (await db.execute(
        select(WSIHistory).order_by(desc(WSIHistory.id)).limit(1)
    )).scalar_one_or_none()
    wsi = latest_wsi.wsi_value if latest_wsi else 0

    # Get funding rate
    funding_rate = 0
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            if len(data) >= 2:
                for i, asset in enumerate(data[0].get("universe", [])):
                    if asset["name"] == "BTC":
                        funding_rate = float(data[1][i].get("funding", 0))
                        break
        except:
            pass

    # Get whale states
    whale_states = []
    price_map = {}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            meta_resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            meta_data = meta_resp.json()
            if len(meta_data) >= 2:
                for i, asset in enumerate(meta_data[0].get("universe", [])):
                    try:
                        price_map[asset["name"]] = float(meta_data[1][i]["markPx"])
                    except:
                        pass

            for wallet in wallets:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": wallet.address})
                data = resp.json()
                data["wallet_address"] = wallet.address
                whale_states.append(data)
        except:
            pass

    return {
        "wsi": wsi,
        "funding_rate": funding_rate,
        "whale_states": whale_states,
        "price_map": price_map
    }

@router.get("/regime/current")
async def get_current_regime(db: AsyncSession = Depends(get_db)):
    """Get current Whale Regime Detector signal"""
    data = await get_regime_data(db)
    if not data:
        return {
            "regime": "NEUTRAL",
            "score": 0,
            "confidence": 0,
            "active_dimensions": 0,
            "dimensions": {},
            "raw_wsi": 0,
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": "NEUTRAL — No wallets configured"
        }

    detector = WhaleRegimeDetector(db)
    result = await detector.calculate(
        current_wsi=data["wsi"],
        funding_rate=data["funding_rate"],
        whale_states=data["whale_states"],
        price_map=data["price_map"]
    )
    return result

@router.get("/regime/dimensions")
async def get_dimension_status(db: AsyncSession = Depends(get_db)):
    """Get status of each dimension (for debugging/monitoring)"""
    detector = WhaleRegimeDetector(db)
    # Trigger calculation to populate dimensions
    data = await get_regime_data(db)
    if data:
        await detector.calculate(
            current_wsi=data["wsi"],
            funding_rate=data["funding_rate"],
            whale_states=data["whale_states"],
            price_map=data["price_map"]
        )
    
    return {
        "dimensions": detector.dimensions,
        "timestamp": datetime.utcnow().isoformat()
    }
