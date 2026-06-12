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

@router.get("/sentiment/funding")
async def get_funding_rates():
    coins = ["BTC", "ETH", "SOL", "BNB", "DOGE", "HYPE"]
    result = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            if len(data) >= 2:
                universe = data[0].get("universe", [])
                ctxs = data[1]
                for i, asset in enumerate(universe):
                    name = asset.get("name", "")
                    if name in coins and i < len(ctxs):
                        ctx = ctxs[i]
                        funding = float(ctx.get("funding", 0))
                        open_interest = float(ctx.get("openInterest", 0))
                        mark_px = float(ctx.get("markPx", 0))
                        oi_usd = open_interest * mark_px
                        annual_funding = funding * 24 * 365 * 100
                        result.append({
                            "coin": name,
                            "funding_rate": round(funding * 100, 6),
                            "annual_rate": round(annual_funding, 2),
                            "open_interest_usd": round(oi_usd, 0),
                            "signal": "BEARISH" if funding > 0.01 else "BULLISH" if funding < -0.01 else "NEUTRAL"
                        })
        except:
            pass
    return {"funding_rates": result}

@router.get("/sentiment/whale-changes")
async def get_whale_changes(db: AsyncSession = Depends(get_db)):
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return {"flips": [], "total_change_pct": 0}
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            meta_resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            meta_data = meta_resp.json()
            price_map = {}
            if len(meta_data) >= 2:
                for i, asset in enumerate(meta_data[0].get("universe", [])):
                    try:
                        price_map[asset["name"]] = float(meta_data[1][i]["markPx"])
                    except:
                        pass

            flips = []
            for wallet in wallets:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": wallet.address})
                data = resp.json()
                positions = []
                total_value = 0
                for ap in data.get("assetPositions", []):
                    pos = ap.get("position", {})
                    szi = float(pos.get("szi", 0))
                    coin = pos.get("coin", "")
                    mark_px = price_map.get(coin, 0)
                    if mark_px == 0 or szi == 0:
                        continue
                    notional = abs(szi) * mark_px
                    total_value += notional
                    positions.append({
                        "coin": coin,
                        "side": "LONG" if szi > 0 else "SHORT",
                        "notional": round(notional, 2),
                        "leverage": float(pos.get("leverage", {}).get("value", 1))
                    })
                
                flips.append({
                    "address": wallet.address,
                    "label": wallet.label,
                    "total_value": round(total_value, 2),
                    "positions": positions,
                    "dominant_side": "LONG" if sum(p["notional"] for p in positions if p["side"]=="LONG") > sum(p["notional"] for p in positions if p["side"]=="SHORT") else "SHORT"
                })
        except:
            return {"flips": [], "total_change_pct": 0}

    total = sum(w["total_value"] for w in flips)
    return {
        "flips": flips,
        "total_notional": round(total, 2),
        "wallet_count": len(flips)
    }

@router.get("/sentiment/market-context")
async def get_market_context():
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            price_map = {}
            oi_map = {}
            if len(data) >= 2:
                for i, asset in enumerate(data[0].get("universe", [])):
                    try:
                        name = asset["name"]
                        ctx = data[1][i]
                        price_map[name] = float(ctx.get("markPx", 0))
                        oi_map[name] = float(ctx.get("openInterest", 0)) * float(ctx.get("markPx", 0))
                    except:
                        pass
            
            btc_price = price_map.get("BTC", 0)
            eth_price = price_map.get("ETH", 0)
            sol_price = price_map.get("SOL", 0)
            
            btc_oi = oi_map.get("BTC", 0)
            eth_oi = oi_map.get("ETH", 0)
            total_oi = sum(oi_map.values())
            
            return {
                "btc_price": round(btc_price, 2),
                "eth_price": round(eth_price, 2),
                "sol_price": round(sol_price, 2),
                "btc_oi": round(btc_oi, 0),
                "eth_oi": round(eth_oi, 0),
                "total_oi": round(total_oi, 0)
            }
        except:
            return {"btc_price": 0, "eth_price": 0, "sol_price": 0, "btc_oi": 0, "eth_oi": 0, "total_oi": 0}

@router.get("/sentiment/reversal-score")
async def get_reversal_score(db: AsyncSession = Depends(get_db)):
    try:
        # 1. WSI
        wsi_data = await fetch_wsi_live(db)
        wsi = wsi_data.get("wsi", 0)
        wsi_score = wsi * 100

        # 2. Funding Rate
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            funding_rates = []
            if len(data) >= 2:
                for i, asset in enumerate(data[0].get("universe", [])):
                    if asset["name"] in ["BTC", "ETH", "SOL"]:
                        try:
                            funding_rates.append(float(data[1][i].get("funding", 0)))
                        except:
                            pass
            avg_funding = sum(funding_rates) / len(funding_rates) if funding_rates else 0
            funding_score = avg_funding * 100000

        # 3. Leverage
        wallets = (await db.execute(select(Wallet))).scalars().all()
        leverage_scores = []
        async with httpx.AsyncClient(timeout=30) as client:
            for wallet in wallets:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": wallet.address})
                positions = resp.json().get("assetPositions", [])
                for ap in positions:
                    lev = float(ap.get("position", {}).get("leverage", {}).get("value", 1))
                    leverage_scores.append(lev)
        avg_leverage = sum(leverage_scores) / len(leverage_scores) if leverage_scores else 1
        leverage_score = min((avg_leverage - 1) / 19 * 100, 100)

        # Combined Score (weighted)
        combined = (wsi_score * 0.4) + (funding_score * 0.3) + (leverage_score * 0.3 * (-1 if wsi < 0 else 1))
        combined = max(-100, min(100, combined))

        signal = "STRONG BOTTOM" if combined < -60 else "WEAK BOTTOM" if combined < -30 else "STRONG TOP" if combined > 60 else "WEAK TOP" if combined > 30 else "NEUTRAL"

        return {
            "score": round(combined, 1),
            "signal": signal,
            "components": {
                "wsi": round(wsi, 3),
                "wsi_score": round(wsi_score, 1),
                "avg_funding": round(avg_funding * 100, 4),
                "funding_score": round(funding_score, 1),
                "avg_leverage": round(avg_leverage, 1),
                "leverage_score": round(leverage_score, 1)
            }
        }
    except Exception as e:
        return {"score": 0, "signal": "ERROR", "components": {}}

@router.post("/sentiment/oi-snapshot")
async def save_oi_snapshot(db: AsyncSession = Depends(get_db)):
    from models import OIHistory
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            if len(data) >= 2:
                for i, asset in enumerate(data[0].get("universe", [])):
                    if asset["name"] in ["BTC", "ETH", "SOL", "BNB", "DOGE", "HYPE"]:
                        try:
                            ctx = data[1][i]
                            mark_px = float(ctx.get("markPx", 0))
                            oi = float(ctx.get("openInterest", 0)) * mark_px
                            db.add(OIHistory(
                                coin=asset["name"],
                                open_interest_usd=oi,
                                mark_price=mark_px
                            ))
                        except:
                            pass
            await db.commit()
            return {"status": "saved"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

@router.get("/sentiment/oi-divergence")
async def get_oi_divergence(db: AsyncSession = Depends(get_db)):
    from models import OIHistory
    from sqlalchemy import func as sqlfunc
    from datetime import datetime, timedelta
    
    now = datetime.utcnow()
    since_7d = now - timedelta(days=7)
    since_1h = now - timedelta(hours=2)
    
    try:
        # Current OI (last 2 hours average)
        current = (await db.execute(
            select(OIHistory.coin, sqlfunc.avg(OIHistory.open_interest_usd).label("avg_oi"))
            .where(OIHistory.timestamp >= since_1h)
            .group_by(OIHistory.coin)
        )).all()

        # 7-day average OI
        historical = (await db.execute(
            select(OIHistory.coin, sqlfunc.avg(OIHistory.open_interest_usd).label("avg_oi"))
            .where(OIHistory.timestamp >= since_7d)
            .where(OIHistory.timestamp < since_1h)
            .group_by(OIHistory.coin)
        )).all()

        if not current or not historical:
            return {"divergence": 0, "signal": "Collecting data...", "details": []}

        current_map = {r.coin: r.avg_oi for r in current}
        historical_map = {r.coin: r.avg_oi for r in historical}

        details = []
        total_divergence = 0
        count = 0

        for coin in current_map:
            if coin in historical_map and historical_map[coin] > 0:
                pct_change = ((current_map[coin] - historical_map[coin]) / historical_map[coin]) * 100
                details.append({
                    "coin": coin,
                    "current_oi": round(current_map[coin], 0),
                    "historical_avg": round(historical_map[coin], 0),
                    "pct_change": round(pct_change, 2)
                })
                total_divergence += pct_change
                count += 1

        avg_divergence = total_divergence / count if count > 0 else 0
        
        if avg_divergence > 15:
            signal = "OI Expanding — Trend Continuation"
        elif avg_divergence < -15:
            signal = "OI Contracting — Possible Reversal"
        else:
            signal = "Neutral — watch for breakout"

        return {
            "divergence": round(avg_divergence, 2),
            "signal": signal,
            "details": details
        }
    except Exception as e:
        return {"divergence": 0, "signal": "Collecting data...", "details": []}

@router.get("/sentiment/oi-divergence-v2")
async def get_oi_divergence_v2(db: AsyncSession = Depends(get_db)):
    from models import OIHistory
    from sqlalchemy import func as sqlfunc
    
    try:
        coins = ["BTC", "ETH", "SOL", "BNB", "DOGE", "HYPE"]
        details = []
        total_divergence = 0
        count = 0

        for coin in coins:
            latest = (await db.execute(
                select(OIHistory).where(OIHistory.coin == coin)
                .order_by(desc(OIHistory.timestamp)).limit(1)
            )).scalar_one_or_none()

            oldest = (await db.execute(
                select(OIHistory).where(OIHistory.coin == coin)
                .order_by(OIHistory.timestamp).limit(1)
            )).scalar_one_or_none()

            if latest and oldest and oldest.open_interest_usd > 0:
                pct_change = ((latest.open_interest_usd - oldest.open_interest_usd) / oldest.open_interest_usd) * 100
                details.append({
                    "coin": coin,
                    "current_oi": round(latest.open_interest_usd, 0),
                    "base_oi": round(oldest.open_interest_usd, 0),
                    "pct_change": round(pct_change, 2)
                })
                total_divergence += pct_change
                count += 1

        if count == 0:
            return {"divergence": 0, "signal": "Collecting data...", "details": []}

        avg_divergence = total_divergence / count

        if avg_divergence > 10:
            signal = "OI Expanding — Trend Continuation"
        elif avg_divergence < -10:
            signal = "OI Contracting — Possible Reversal"
        else:
            signal = "Neutral — watch for breakout"

        return {
            "divergence": round(avg_divergence, 2),
            "signal": signal,
            "details": details
        }
    except Exception as e:
        return {"divergence": 0, "signal": "Error", "details": []}
