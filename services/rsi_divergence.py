"""
RSI Failure Swing detector - a momentum-based complement to the
price-vs-MVRV divergence detector (services/divergence.py).

Deliberately NOT classic RSI/price divergence: research (2026-08-04)
found classic divergence has a well-documented weakness during strong/
parabolic trends - exactly the price action that tends to precede
major cycle tops, i.e. exactly our use case. It fires repeatedly
("burns through") while price keeps climbing.

Failure Swing (Wilder's original RSI concept, not a price comparison
at all) is specifically noted as more reliable in that scenario: it
only watches RSI against its OWN prior swings, requiring a full
peak -> retreat -> weaker rally -> break sequence, not just one
lower high.

Different mechanism from WSI (services/unified_signal.py wsi_to_score):
WSI reflects the CURRENT position of 8 specific tracked wallets - a
small, idiosyncratic sample, and a snapshot with no memory of trend.
RSI is derived from BTC's own price across ALL market participants,
and Failure Swing specifically encodes multi-week MOMENTUM TRAJECTORY,
not current stance. The two can diverge (e.g. whales staying long
while each new price high carries weaker momentum) and there is no
historical whale position data far back enough to backtest whether
they actually did diverge at past tops - that comparison can only be
observed going forward once both run live.
"""

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
    +/- window days. Flat plateaus (e.g. RSI pinned at 100 for several days
    during an uninterrupted rally) are handled explicitly and reported once,
    at the plateau's start - an earlier version silently dropped these by
    requiring a strictly unique value, which meant a real, sustained
    overbought excursion could be invisible to the detector entirely.
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


def detect_bearish_failure_swing(rsi_series: list, extrema: list) -> dict:
    """
    A -> T -> B -> break, all in RSI terms only (never compares to price):
      A: a peak with RSI >= 70
      T: the next trough after A, RSI < 70 (the "prior RSI low")
      B: the next peak after T, with B < A (fails to reach the prior high)
      break: RSI later falls below T's value - confirms the failure swing
    Searches candidate A peaks from most recent backward, since the most
    recent overbought peak may not yet have a full sequence after it.
    """
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
        return {
            "stage": "confirmed" if broken else "pending_break",
            "peak_A": candidate_A, "trough_T": trough_T, "peak_B": peak_B,
            "break_date": broken[0][0] if broken else None,
        }
    return {"stage": "no_pattern_yet", "most_recent_ob_peak": peaks_ob[-1]}


def detect_bullish_failure_swing(rsi_series: list, extrema: list) -> dict:
    """Mirror of detect_bearish_failure_swing for oversold (RSI <= 30)."""
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
        return {
            "stage": "confirmed" if broken else "pending_break",
            "trough_A": candidate_A, "peak_T": peak_T, "trough_B": trough_B,
            "break_date": broken[0][0] if broken else None,
        }
    return {"stage": "no_pattern_yet", "most_recent_os_trough": troughs_os[-1]}


async def check_rsi_failure_swing(db) -> dict:
    """Live check: pulls btc_price history from mvrv_history and runs both
    detectors as of today. Mirrors services.divergence's structure."""
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
