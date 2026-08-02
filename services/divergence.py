from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import MVRVHistory

WINDOW_DAYS = 450
NEAR_EXTREME_PCT = 3.0     # price within this % of its rolling high/low counts as "at" it
MEANINGFUL_GAP_ABS = 0.5   # Z-score must be at least this many POINTS off its own extreme.
                            # Absolute, not %: Z-score oscillates near/through zero, so a %
                            # gap blows up meaninglessly near a small base (e.g. 0.20 -> 0.38
                            # is a trivial move but reads as a "92% gap"). Not yet backtested.


def _assess_divergence(rows: list) -> dict:
    """Pure function: given MVRVHistory rows already filtered to the
    desired window (sorted by date, all with btc_price set), assesses
    divergence for the LAST row as 'now'. Shared by the live check and
    the historical backtest so both run identical logic."""
    if len(rows) < 30:
        return {"direction": None, "is_active": False, "detail": "insufficient history", "rows_found": len(rows)}

    latest = rows[-1]
    max_price_row = max(rows, key=lambda r: r.btc_price)
    min_price_row = min(rows, key=lambda r: r.btc_price)
    max_z_row = max(rows, key=lambda r: r.zscore)
    min_z_row = min(rows, key=lambda r: r.zscore)

    price_from_high_pct = (latest.btc_price - max_price_row.btc_price) / max_price_row.btc_price * 100
    price_from_low_pct = (latest.btc_price - min_price_row.btc_price) / min_price_row.btc_price * 100
    z_gap_from_high = max_z_row.zscore - latest.zscore
    z_gap_from_low = latest.zscore - min_z_row.zscore

    bearish = price_from_high_pct > -NEAR_EXTREME_PCT and z_gap_from_high > MEANINGFUL_GAP_ABS
    bullish = price_from_low_pct < NEAR_EXTREME_PCT and z_gap_from_low > MEANINGFUL_GAP_ABS
    direction = "bearish" if bearish else "bullish" if bullish else None

    return {
        "direction": direction,
        "is_active": direction is not None,
        "latest_date": latest.date,
        "latest_price": latest.btc_price,
        "latest_zscore": latest.zscore,
        "rolling_high_price": {"value": max_price_row.btc_price, "date": max_price_row.date},
        "rolling_high_zscore": {"value": max_z_row.zscore, "date": max_z_row.date},
        "rolling_low_price": {"value": min_price_row.btc_price, "date": min_price_row.date},
        "rolling_low_zscore": {"value": min_z_row.zscore, "date": min_z_row.date},
        "price_from_high_pct": round(price_from_high_pct, 2),
        "z_gap_from_high": round(z_gap_from_high, 3),
        "price_from_low_pct": round(price_from_low_pct, 2),
        "z_gap_from_low": round(z_gap_from_low, 3),
        "rows_in_window": len(rows),
    }


async def detect_price_onchain_divergence(db: AsyncSession) -> dict:
    """
    Compares BTC price against MVRV Z-score over a trailing WINDOW_DAYS
    window to catch price making new highs/lows while Z-score isn't
    confirming - the pattern that preceded the Oct 2025 top by ~10
    months (Z-score peaked 2024-12, price kept climbing to 2025-10-06).

    A rolling window naturally "forgets" extremes older than
    WINDOW_DAYS - expected for a live monitor, not a bug. It means a
    check run long after a top has fully resolved (like today) will
    correctly show nothing active; validating whether this would have
    fired DURING the actual 2025 process requires the separate
    backtest endpoint, not this live check.

    Returned as a standalone alert, like whale_alert - deliberately
    NOT blended into the weighted score, so it isn't delayed by the
    same on-chain consensus mechanism it's meant to counteract.

    Uses mvrv_history.date, not .timestamp (the one-time backfill
    stamped timestamp with the backfill run time for historical rows).
    """
    try:
        since_date = (datetime.utcnow() - timedelta(days=WINDOW_DAYS)).date().isoformat()
        rows = (await db.execute(
            select(MVRVHistory)
            .where(MVRVHistory.date >= since_date)
            .where(MVRVHistory.btc_price.isnot(None))
            .order_by(MVRVHistory.date)
        )).scalars().all()
        result = _assess_divergence(rows)
        result["window_days"] = WINDOW_DAYS
        return result
    except Exception as e:
        print(f"divergence detector error: {e}")
        return {"direction": None, "is_active": False, "error": str(e)}
