from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database import get_db
from models import WSIHistory, Wallet
from services.calculations import WSICalculator
from services.regime import WhaleRegimeDetector
from datetime import datetime
import httpx
import asyncio

router = APIRouter()

# Rate limiter: max 5 concurrent Hyperliquid API calls
HL_SEMAPHORE = asyncio.Semaphore(5)

async def fetch_wallet_state(client: httpx.AsyncClient, wallet: Wallet):
    """Fetch a single wallet's clearinghouse state with semaphore."""
    async with HL_SEMAPHORE:
        resp = await client.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "clearinghouseState", "user": wallet.address},
            timeout=20
        )
        data = resp.json()
        data["wallet_address"] = wallet.address
        return data

async def fetch_meta_and_prices(client: httpx.AsyncClient):
    """Fetch meta + asset contexts once, return price map + funding."""
    resp = await client.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "metaAndAssetCtxs"},
        timeout=15
    )
    data = resp.json()
    price_map = {}
    avg_funding = 0.0
    btc_price = 0.0
    if len(data) >= 2:
        for i, asset in enumerate(data[0].get("universe", [])):
            try:
                coin = asset["name"]
                mark_px = float(data[1][i].get("markPx", 0))
                funding = float(data[1][i].get("funding", 0))
                price_map[coin] = mark_px
                if coin == "BTC":
                    avg_funding = funding
                    btc_price = mark_px
            except Exception:
                pass
    return price_map, avg_funding, btc_price

def format_whale_value(db_value: float | None) -> bool | None:
    """
    Convert DB float to frontend-friendly value.
    NULL → None (pre-migration, unknown)
    1.0 → True (confirmed)
    0.0 → False (confirmed)
    """
    if db_value is None:
        return None
    return bool(db_value)

async def calculate_signal(db: AsyncSession):
    # 1. WSI
    wallets = (await db.execute(select(Wallet))).scalars().all()
    if not wallets:
        return None

    latest_wsi = (await db.execute(
        select(WSIHistory).order_by(desc(WSIHistory.timestamp)).limit(1)
    )).scalar_one_or_none()

    wsi = latest_wsi.wsi_value if latest_wsi else 0

    # 2. Fetch meta + all wallet states in parallel (rate-limited)
    async with httpx.AsyncClient(timeout=20) as client:
        price_map, avg_funding, btc_price = await fetch_meta_and_prices(client)
        wallet_tasks = [fetch_wallet_state(client, w) for w in wallets]
        all_wallet_states = await asyncio.gather(*wallet_tasks, return_exceptions=True)
        all_wallet_states = [
            ws for ws in all_wallet_states 
            if not isinstance(ws, Exception)
        ]

    # 3. Whale Direction with Delta
    whale_closing_short = False
    whale_closing_long = False
    whale_heavy_short = False
    whale_heavy_long = False
    whale_delta_info = {
        "short_delta_pct": 0,
        "long_delta_pct": 0,
        "has_history": False
    }

    try:
        total_long = 0
        total_short = 0

        for data in all_wallet_states:
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
            whale_heavy_short = total_short / total > 0.65
            whale_heavy_long = total_long / total > 0.65

        calc = WSICalculator()
        delta = await calc.calculate_whale_delta(db, all_wallet_states, price_map)
        whale_delta_info = delta
        whale_closing_short = delta.get("closing_short", False)
        whale_closing_long = delta.get("closing_long", False)

    except Exception:
        pass

    # 4. Regime Score (NEW — reuse fetched data, no extra HTTP)
    regime_score = None
    try:
        detector = WhaleRegimeDetector(db)
        regime_result = await detector.calculate(
            current_wsi=wsi,
            funding_rate=avg_funding,
            whale_states=all_wallet_states,
            price_map=price_map
        )
        regime_score = regime_result.get("score")
    except Exception:
        pass

    # Signal Logic
    buy_conditions = [
        wsi <= -0.8,
        avg_funding < -0.001,
        whale_closing_short
    ]
    sell_conditions = [
        wsi >= 0.8,
        avg_funding > 0.001,
        whale_closing_long
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
        "regime_score": regime_score,  # NEW
        "conditions": {
            "wsi": round(wsi, 3),
            "wsi_met_buy": wsi <= -0.8,
            "wsi_met_sell": wsi >= 0.8,
            "funding": round(avg_funding * 100, 4),
            "funding_met_buy": avg_funding < -0.001,
            "funding_met_sell": avg_funding > 0.001,
            "whale_short": whale_heavy_short,
            "whale_long": whale_heavy_long,
            "whale_closing_short": whale_closing_short,
            "whale_closing_long": whale_closing_long,
            "whale_delta": whale_delta_info
        },
        "buy_conditions_met": buy_count,
        "sell_conditions_met": sell_count
    }

@router.get("/signals/current")
async def get_current_signal(db: AsyncSession = Depends(get_db)):
    result = await calculate_signal(db)
    if not result:
        return {"signal": "NEUTRAL", "btc_price": 0, "regime_score": None, "conditions": {}, "buy_conditions_met": 0, "sell_conditions_met": 0}
    return result

@router.post("/signals/save")
async def save_signal(db: AsyncSession = Depends(get_db)):
    from models import SignalHistory
    result = await calculate_signal(db)
    if not result:
        return {"status": "no wallets"}

    confidence = max(result["buy_conditions_met"], result["sell_conditions_met"]) / 3 * 100

    db.add(SignalHistory(
        signal=result["signal"],
        btc_price=result["btc_price"],
        wsi=result["conditions"]["wsi"],
        funding=result["conditions"]["funding"],
        whale_short=1.0 if result["conditions"]["whale_closing_short"] else 0.0,
        whale_long=1.0 if result["conditions"]["whale_closing_long"] else 0.0,
        regime_score=result.get("regime_score"),  # NEW: NULL if detector failed
        buy_conditions_met=result["buy_conditions_met"],
        sell_conditions_met=result["sell_conditions_met"],
        confidence=round(confidence, 1)
    ))
    await db.commit()
    return {"status": "saved", "signal": result["signal"]}

@router.get("/signals/history")
async def get_signal_history(db: AsyncSession = Depends(get_db)):
    from models import SignalHistory
    rows = (await db.execute(
        select(SignalHistory).order_by(desc(SignalHistory.timestamp)).limit(50)
    )).scalars().all()
    return {"history": [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "signal": r.signal,
            "btc_price": r.btc_price,
            "wsi": r.wsi,
            "funding": r.funding,
            "regime_score": r.regime_score,  # NEW
            "whale_short": format_whale_value(r.whale_short),
            "whale_long": format_whale_value(r.whale_long),
            "buy_conditions_met": r.buy_conditions_met,
            "sell_conditions_met": r.sell_conditions_met,
            "confidence": r.confidence
        } for r in rows
    ]}
