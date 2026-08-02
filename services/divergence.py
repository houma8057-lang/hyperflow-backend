from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import MVRVHistory

WINDOW_DAYS = 450
NEAR_EXTREME_PCT = 3.0
MEANINGFUL_GAP_PCT = 20.0


async def detect_price_onchain_divergence(db: AsyncSession) -> dict:
    """
    Compares BTC price against MVRV Z-score over a trailing window to
    catch the pattern that preceded the Oct 2025 top by ~10 months:
    price still making new highs while Z-score had already peaked
    (Dec 2024) and was declining - a bearish divergence. Same logic
    mirrored for bottoms (bullish divergence).

    Uses mvrv_history.date (a YYYY-MM-DD string, sorts correctly as
    text) for all time filtering - NOT .timestamp. The one-time
    backfill set timestamp to the backfill run time for every
    historical row (an oversight caught after the fact), so timestamp
    is meaningless for anything before that backfill. date is correct
    throughout, going back to 2022-08-02.

    Returned as a standalone alert, like whale_alert - deliberately
    NOT blended into the weighted score. Blending it in would let it
    get diluted/delayed by the same on-chain consensus mechanism it's
    meant to counteract (see unified_signal.py's STRONG-signal notes).

    WINDOW_DAYS and the two threshold constants are first-pass
    defaults, not yet backtested against the 2022-2026 history now
    available in mvrv_history.
    """
    try:
        since_date = (datetime.utcnow() - timedelta(days=WINDOW_DAYS)).date().isoformat()
        rows = (await db.execute(
            select(MVRVHistory)
            .where(MVRVHistory.date >= since_date)
            .where(MVRVHistory.btc_price.isnot(None))
            .order_by(MVRVHistory.date)
        )).scalars().all()

        if len(rows) < 30:
            return {"direction": None, "is_active": False, "detail": "insufficient history", "rows_found": len(rows)}

        latest = rows[-1]
        max_price_row = max(rows, key=lambda r: r.btc_price)
        min_price_row = min(rows, key=lambda r: r.btc_price)
        max_z_row = max(rows, key=lambda r: r.zscore)
        min_z_row = min(rows, key=lambda r: r.zscore)

        price_from_high_pct = (latest.btc_price - max_price_row.btc_price) / max_price_row.btc_price * 100
        price_from_low_pct = (latest.btc_price - min_price_row.btc_price) / min_price_row.btc_price * 100
        z_from_high_pct = (
            (latest.zscore - max_z_row.zscore) / abs(max_z_row.zscore) * 100
            if max_z_row.zscore != 0 else 0.0
        )
        z_from_low_pct = (
            (latest.zscore - min_z_row.zscore) / abs(min_z_row.zscore) * 100
            if min_z_row.zscore != 0 else 0.0
        )

        bearish = price_from_high_pct > -NEAR_EXTREME_PCT and z_from_high_pct < -MEANINGFUL_GAP_PCT
        bullish = price_from_low_pct < NEAR_EXTREME_PCT and z_from_low_pct > MEANINGFUL_GAP_PCT
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
            "zscore_from_high_pct": round(z_from_high_pct, 2),
            "price_from_low_pct": round(price_from_low_pct, 2),
            "zscore_from_low_pct": round(z_from_low_pct, 2),
            "window_days": WINDOW_DAYS,
            "rows_in_window": len(rows),
        }
    except Exception as e:
        print(f"divergence detector error: {e}")
        return {"direction": None, "is_active": False, "error": str(e)}
