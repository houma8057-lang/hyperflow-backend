"""
RSI Failure Swing detector - see prior version's docstring for the
full rationale (classic RSI/price divergence fails during strong
trends; Failure Swing watches RSI against itself only).

UPDATE (2026-08-04): first real backtest (Jan-Oct 2025 and Aug 2022-
Jan 2023) showed the core pattern IS present around both reference
events, but exposed a real flaw - once "confirmed", a pattern stayed
reported as confirmed indefinitely (one break_date persisted unchanged
for ~4 months, Feb 11 to Jun 14 2025, while price moved from $84k to
$123k) until a newer full sequence completed. A signal with no
expiry loses warning value - added an explicit freshness window.
"""
from datetime import datetime, timedelta

FRESHNESS_DAYS = 45  # a confirmed break older than this is reported as stale, not active


def compute_rsi_series(prices: list, period: int = 14) -> list:
    """
    prices: list of (date_str, price_float) sorted by date, no gaps required.
    Returns list of (date_str, rsi_float) using Wilder's original smoothing
    (modified moving average), the standard RSI method.
    """
    if len(prices) < period + 1:
        return []
    changes = [(prices[i][0], prices[i][1] - prices[i - 1][1]) for i in range(1, len(prices))]
    gains = [max(c, 0.0) for _, c in changes]
    losses = [max(-c, 0.0) for _, c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def calc_rsi(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    rsi_series = [(changes[period - 1][0], calc_rsi(avg_gain, avg_loss))]
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_series.append((changes[i][0], calc_rsi(avg_gain, avg_loss)))
    return rsi_series


def find_local_extrema(series: list, window: int = 5) -> list:
    """
    series: list of (date, value), sorted by date.
    A point counts as a peak/trough if it is >= (or <=) every value within
    +/- window days. Flat plateaus are handled explicitly and reported
    once, at the plateau's start.
    """
    extrema = []
    n = len(series)
    i = window
    while i < n - window:
        date, val = series[i]
        neighborhood = [series[j][1] for j in range(i - window, i + window + 1)]
        is_peak = val >= max(neighborhood)
        is_trough = val <= min(neighborhood)
        if is_peak and not is_trough:
            j = i
            while j + 1 < n and series[j + 1][1] == val:
                j += 1
            extrema.append({"date": series[i][0], "value": val, "type": "peak", "index": i})
            i = j + 1
        elif is_trough and not is_peak:
            j = i
            while j + 1 < n and series[j + 1][1] == val:
                j += 1
            extrema.append({"date": series[i][0], "value": val, "type": "trough", "index": i})
            i = j + 1
        else:
            i += 1
    return extrema


def _days_between(d1: str, d2: str) -> int:
    return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days


def detect_bearish_failure_swing(rsi_series: list, extrema: list) -> dict:
    """
    A -> T -> B -> break, all in RSI terms only:
      A: a peak with RSI >= 70
      T: the next trough after A, RSI < 70
      B: the next peak after T, with B < A (fails to reach the prior high)
      break: RSI later falls below T's value
    "confirmed" only if the break happened within FRESHNESS_DAYS of the
    latest data point; older breaks are reported as "confirmed_stale" -
    still historically true, but not an active warning.
    """
    if not rsi_series:
        return {"stage": "no_data"}
    latest_date = rsi_series[-1][0]
    peaks_ob = [e for e in extrema if e["type"] == "peak" and e["value"] >= 70]
    if not peaks_ob:
        return {"stage": "no_overbought_excursion"}

    for candidate_A in reversed(peaks_ob):
        troughs_after = [e for e in extrema if e["type"] == "trough"
                          and e["index"] > candidate_A["index"] and e["value"] < 70]
        if not troughs_after:
            continue
        trough_T = troughs_after[0]
        peaks_after_T = [e for e in extrema if e["type"] == "peak" and e["index"] > trough_T["index"]]
        if not peaks_after_T:
            continue
        peak_B = peaks_after_T[0]
        if peak_B["value"] >= candidate_A["value"]:
            continue
        later_points = [(d, v) for d, v in rsi_series if d > peak_B["date"]]
        broken = [(d, v) for d, v in later_points if v < trough_T["value"]]
        if not broken:
            return {"stage": "pending_break", "peak_A": candidate_A, "trough_T": trough_T, "peak_B": peak_B}
        break_date = broken[0][0]
        days_since = _days_between(break_date, latest_date)
        return {
            "stage": "confirmed" if days_since <= FRESHNESS_DAYS else "confirmed_stale",
            "peak_A": candidate_A, "trough_T": trough_T, "peak_B": peak_B,
            "break_date": break_date, "days_since_break": days_since,
        }
    return {"stage": "no_pattern_yet", "most_recent_ob_peak": peaks_ob[-1]}


def detect_bullish_failure_swing(rsi_series: list, extrema: list) -> dict:
    """Mirror of detect_bearish_failure_swing for oversold (RSI <= 30)."""
    if not rsi_series:
        return {"stage": "no_data"}
    latest_date = rsi_series[-1][0]
    troughs_os = [e for e in extrema if e["type"] == "trough" and e["value"] <= 30]
    if not troughs_os:
        return {"stage": "no_oversold_excursion"}

    for candidate_A in reversed(troughs_os):
        peaks_after = [e for e in extrema if e["type"] == "peak"
                        and e["index"] > candidate_A["index"] and e["value"] > 30]
        if not peaks_after:
            continue
        peak_T = peaks_after[0]
        troughs_after_T = [e for e in extrema if e["type"] == "trough" and e["index"] > peak_T["index"]]
        if not troughs_after_T:
            continue
        trough_B = troughs_after_T[0]
        if trough_B["value"] <= candidate_A["value"]:
            continue
        later_points = [(d, v) for d, v in rsi_series if d > trough_B["date"]]
        broken = [(d, v) for d, v in later_points if v > peak_T["value"]]
        if not broken:
            return {"stage": "pending_break", "trough_A": candidate_A, "peak_T": peak_T, "trough_B": trough_B}
        break_date = broken[0][0]
        days_since = _days_between(break_date, latest_date)
        return {
            "stage": "confirmed" if days_since <= FRESHNESS_DAYS else "confirmed_stale",
            "trough_A": candidate_A, "peak_T": peak_T, "trough_B": trough_B,
            "break_date": break_date, "days_since_break": days_since,
        }
    return {"stage": "no_pattern_yet", "most_recent_os_trough": troughs_os[-1]}


async def check_rsi_failure_swing(db) -> dict:
    """Live check: pulls btc_price history from mvrv_history and runs both
    detectors as of today."""
    from sqlalchemy import select
    from models import MVRVHistory
    try:
        rows = (await db.execute(
            select(MVRVHistory)
            .where(MVRVHistory.btc_price.isnot(None))
            .order_by(MVRVHistory.date)
        )).scalars().all()
        prices = [(r.date, r.btc_price) for r in rows]
        rsi = compute_rsi_series(prices, period=14)
        if len(rsi) < 30:
            return {"is_active": False, "detail": "insufficient history"}
        extrema = find_local_extrema(rsi, window=5)
        bearish = detect_bearish_failure_swing(rsi, extrema)
        bullish = detect_bullish_failure_swing(rsi, extrema)
        return {
            "latest_date": rsi[-1][0],
            "latest_rsi": round(rsi[-1][1], 2),
            "bearish": bearish,
            "bullish": bullish,
        }
    except Exception as e:
        print(f"rsi_failure_swing error: {e}")
        return {"is_active": False, "error": str(e)}
