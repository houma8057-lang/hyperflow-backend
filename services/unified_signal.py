import httpx
import asyncio
import time
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from models import WSIHistory, Wallet
from services.bgeometrics import (
    get_latest_mvrv_zscore, get_latest_nupl, get_latest_sopr,
    mvrv_zscore_to_score, nupl_to_score, sopr_to_score
)

# ─────────────────────────────────────────
# Weights (must sum to 100)
# ─────────────────────────────────────────
WEIGHTS = {
    "wsi":        20,
    "funding":    15,
    "mvrv":       20,
    "nupl":       20,
    "sopr":       10,
    "whale_flip":  5,
    "oi_change":  10,
}

# ─────────────────────────────────────────
# Score converters
# ─────────────────────────────────────────

def wsi_to_score(wsi: float) -> float:
    return round(wsi * 100, 2)

def funding_to_score(funding: float) -> float:
    normalized = funding / 0.005
    return round(max(-100, min(100, normalized * 100)), 2)

def whale_flip_to_score(flips: int, direction: str) -> float:
    if flips == 0:
        return 0.0
    score = min(100, flips * 30)
    return -score if direction == "bullish" else score

def oi_to_score(oi_change_pct: float, wsi: float) -> float:
    """
    OI change combined with WSI direction.
    OI dropping + WSI negative = closing SHORTs = bottom = -100
    OI rising  + WSI positive = opening LONGs  = top    = +100
    OI change alone without direction context = weaker signal.
    """
    if abs(oi_change_pct) < 2:
        return 0.0

    if oi_change_pct < -10:
        base = -80.0
    elif oi_change_pct < -5:
        base = -40.0
    elif oi_change_pct < -2:
        base = -20.0
    elif oi_change_pct > 10:
        base = 80.0
    elif oi_change_pct > 5:
        base = 40.0
    else:
        base = 20.0

    # Amplify if aligned with WSI direction
    if base < 0 and wsi < -0.3:
        base = max(-100, base * 1.25)
    elif base > 0 and wsi > 0.3:
        base = min(100, base * 1.25)

    return round(base, 2)

# ─────────────────────────────────────────
# Signal label
# ─────────────────────────────────────────

def score_to_signal(score: float) -> str:
    if score <= -70:
        return "STRONG BUY"
    elif score <= -40:
        return "WEAK BUY"
    elif score <= 40:
        return "NEUTRAL"
    elif score <= 70:
        return "WEAK SELL"
    else:
        return "STRONG SELL"

# ─────────────────────────────────────────
# OI change calculator
# ─────────────────────────────────────────

async def get_btc_oi_change(db: AsyncSession) -> float:
    """
    Compare BTC OI now vs 24h ago.
    Returns percentage change.
    """
    try:
        from models import OIHistory
        now = datetime.utcnow()
        ago24 = now - timedelta(hours=24)
        ago2h = now - timedelta(hours=2)

        latest = (await db.execute(
            select(OIHistory)
            .where(OIHistory.coin == "BTC")
            .where(OIHistory.timestamp >= ago2h)
            .order_by(desc(OIHistory.timestamp))
            .limit(1)
        )).scalar_one_or_none()

        old = (await db.execute(
            select(OIHistory)
            .where(OIHistory.coin == "BTC")
            .where(OIHistory.timestamp >= ago24)
            .where(OIHistory.timestamp < ago2h)
            .order_by(desc(OIHistory.timestamp))
            .limit(1)
        )).scalar_one_or_none()

        if not latest or not old or old.open_interest_usd == 0:
            return 0.0

        change_pct = ((latest.open_interest_usd - old.open_interest_usd)
                      / old.open_interest_usd * 100)
        return round(change_pct, 2)

    except Exception as e:
        print(f"oi_change error: {e}")
        return 0.0

# ─────────────────────────────────────────
# Whale flip detector
# ─────────────────────────────────────────

async def detect_whale_flips(
    db: AsyncSession,
    current_states: list,
    price_map: dict
) -> dict:
    """
    Detect whale direction changes vs 24h ago.
    Uses percentage threshold (60%) so it scales automatically
    when new whales are added.
    """
    try:
        ago24 = datetime.utcnow() - timedelta(hours=24)
        from models import PositionSnapshot, Wallet
        result = await db.execute(
            select(PositionSnapshot)
            .where(PositionSnapshot.timestamp >= ago24)
            .order_by(desc(PositionSnapshot.timestamp))
        )
        snapshots = result.scalars().all()

        # Get total whale count from DB
        wallets_result = await db.execute(select(Wallet))
        total_whales = len(wallets_result.scalars().all())
        threshold_pct = 0.60  # 60% threshold

        # snapshots is ordered newest-first; overwriting on every match
        # (instead of "if key not in prev_sides") means the LAST write
        # wins, i.e. the OLDEST snapshot inside the 24h window survives.
        # The previous version kept the first (newest) match, which made
        # this compare "now vs a few minutes ago" instead of "now vs 24h
        # ago", so flips appeared and vanished within one snapshot_job
        # cycle instead of reflecting a real 24h direction change.
        prev_sides = {}
        for snap in snapshots:
            prev_sides[snap.wallet_address] = snap.side

        bullish_flips = []
        bearish_flips = []

        for state in current_states:
            addr = state.get("wallet_address", "")
            label = state.get("wallet_label", state.get("label", addr[:8]))
            prev = prev_sides.get(addr)
            if not prev:
                continue

            long_ntl = 0.0
            short_ntl = 0.0
            for ap in state.get("assetPositions", []):
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

            current_side = "LONG" if long_ntl > short_ntl else "SHORT"

            if prev == "SHORT" and current_side == "LONG":
                bullish_flips.append(label)
            elif prev == "LONG" and current_side == "SHORT":
                bearish_flips.append(label)

        bullish_count = len(bullish_flips)
        bearish_count = len(bearish_flips)
        majority_threshold = max(1, round(total_whales * threshold_pct))

        # Determine direction
        if bullish_count > bearish_count:
            flip_count = bullish_count
            direction = "bullish"
            flipped_whales = bullish_flips
        elif bearish_count > bullish_count:
            flip_count = bearish_count
            direction = "bearish"
            flipped_whales = bearish_flips
        else:
            flip_count = 0
            direction = "neutral"
            flipped_whales = []

        # Major alert if >= 60% of whales flipped
        is_major = flip_count >= majority_threshold

        return {
            "flip_count": flip_count,
            "direction": direction,
            "flipped_whales": flipped_whales,
            "total_whales": total_whales,
            "majority_threshold": majority_threshold,
            "is_major": is_major,
            "alert_type": (
                "MAJOR BOTTOM SIGNAL" if is_major and direction == "bullish"
                else "MAJOR TOP SIGNAL" if is_major and direction == "bearish"
                else None
            )
        }

    except Exception as e:
        print(f"whale_flip error: {e}")
        return {
            "flip_count": 0,
            "direction": "neutral",
            "flipped_whales": [],
            "total_whales": 0,
            "majority_threshold": 5,
            "is_major": False,
            "alert_type": None
        }

# ─────────────────────────────────────────
# Main unified signal calculator
# ─────────────────────────────────────────

async def _timed(label: str, coro):
    """Diagnostic wrapper: times an individual coroutine inside a gather()."""
    t = time.monotonic()
    result = await coro
    print(f"timing: {label} = {time.monotonic()-t:.2f}s")
    return result

async def calculate_unified_signal(
    db: AsyncSession,
    wsi: float,
    funding: float,
    current_states: list,
    price_map: dict
) -> dict:

    # MVRV/NUPL/SOPR share the same `db` session and touch the DB
    # (read + possible commit) via metric_cache, so they run sequentially
    # to avoid concurrent use of one AsyncSession, which SQLAlchemy
    # async does not support safely.
    t0 = time.monotonic()
    mvrv_z = await get_latest_mvrv_zscore(db)
    nupl = await get_latest_nupl(db)
    sopr = await get_latest_sopr(db)
    print(f"timing: unified_signal - mvrv/nupl/sopr cache reads (sequential) = {time.monotonic()-t0:.2f}s")

    # OI change and whale-flip detection are read-only against `db`.
    # Each timed individually to find which one is the real bottleneck
    # (previously only the combined gather time was visible).
    oi_change, flip_result = await asyncio.gather(
        _timed("get_btc_oi_change", get_btc_oi_change(db)),
        _timed("detect_whale_flips", detect_whale_flips(db, current_states, price_map))
    )

    flip_data = flip_result
    flip_count = flip_data["flip_count"]
    flip_dir = flip_data["direction"]

    # Convert each metric to -100/+100 score
    scores = {
        "wsi":        wsi_to_score(wsi),
        "funding":    funding_to_score(funding),
        "mvrv":       mvrv_zscore_to_score(mvrv_z) if mvrv_z is not None else 0.0,
        "nupl":       nupl_to_score(nupl) if nupl is not None else 0.0,
        "sopr":       sopr_to_score(sopr) if sopr is not None else 0.0,
        "whale_flip": whale_flip_to_score(flip_count, flip_dir),
        "oi_change":  oi_to_score(oi_change, wsi),
    }

    # Weighted sum
    total = sum(scores[k] * WEIGHTS[k] / 100 for k in WEIGHTS)
    total = round(max(-100, min(100, total)), 1)

    signal = score_to_signal(total)

    return {
        "score": total,
        "signal": signal,
        "whale_alert": {
            "is_major":       flip_data["is_major"],
            "alert_type":     flip_data["alert_type"],
            "flip_count":     flip_count,
            "direction":      flip_dir,
            "flipped_whales": flip_data["flipped_whales"],
            "total_whales":   flip_data["total_whales"],
            "threshold":      flip_data["majority_threshold"],
        },
        "components": {
            "wsi_score":        round(scores["wsi"] * WEIGHTS["wsi"] / 100, 2),
            "funding_score":    round(scores["funding"] * WEIGHTS["funding"] / 100, 2),
            "mvrv_score":       round(scores["mvrv"] * WEIGHTS["mvrv"] / 100, 2),
            "nupl_score":       round(scores["nupl"] * WEIGHTS["nupl"] / 100, 2),
            "sopr_score":       round(scores["sopr"] * WEIGHTS["sopr"] / 100, 2),
            "whale_flip_score": round(scores["whale_flip"] * WEIGHTS["whale_flip"] / 100, 2),
            "oi_change_score":  round(scores["oi_change"] * WEIGHTS["oi_change"] / 100, 2),
        },
        "raw": {
            "wsi":           round(wsi, 3),
            "funding":       round(funding * 100, 4),
            "mvrv_z":        round(mvrv_z, 3) if mvrv_z is not None else None,
            "nupl":          round(nupl, 3) if nupl is not None else None,
            "sopr":          round(sopr, 3) if sopr is not None else None,
            "oi_change_pct": oi_change,
            "whale_flips":   flip_count,
            "flip_dir":      flip_dir,
        },
        "timestamp": datetime.utcnow().isoformat()
    }
