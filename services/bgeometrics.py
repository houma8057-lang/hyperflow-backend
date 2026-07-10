import os
import time
import httpx

BGEOMETRICS_TOKEN = os.environ.get("BGEOMETRICS_API_KEY", "3peU12OVuQ")
BASE_URL = "https://api.bgeometrics.com/v1"

# Free plan: 10 req/hour, 15 req/day. Underlying data updates once per
# day anyway, so a 24h cache loses no real freshness while cutting
# daily usage from thousands (30s frontend polling) to ~3-6/day.
CACHE_TTL_SECONDS = 24 * 60 * 60

_cache: dict[str, tuple[float, float]] = {}  # endpoint -> (value, fetched_at)

async def _fetch_latest(endpoint: str) -> float | None:
    """Fetch the single latest value for any BGeometrics metric.
    Cached for CACHE_TTL_SECONDS; on any failure, falls back to the
    last known value (even if stale) instead of returning None.
    """
    now = time.monotonic()
    cached = _cache.get(endpoint)
    if cached is not None and (now - cached[1]) < CACHE_TTL_SECONDS:
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/{endpoint}",
                params={"token": BGEOMETRICS_TOKEN, "limit": 1}
            )
            data = resp.json()
            if not data or not isinstance(data, list):
                return cached[0] if cached else None
            item = data[-1]
            for key in item:
                if key not in ("d", "unixTs"):
                    val = item[key]
                    if val is not None and str(val) != "NaN":
                        result = float(val)
                        _cache[endpoint] = (result, now)
                        print(f"bgeometrics: fetched fresh {endpoint} = {result}")
                        return result
            return cached[0] if cached else None
    except Exception as e:
        print(f"bgeometrics error ({endpoint}): {e}")
        return cached[0] if cached else None

async def get_latest_mvrv_zscore() -> float | None:
    return await _fetch_latest("mvrv-zscore")

async def get_latest_nupl() -> float | None:
    return await _fetch_latest("nupl")

async def get_latest_sopr() -> float | None:
    return await _fetch_latest("sopr")

def mvrv_zscore_to_score(zscore: float) -> float:
    if zscore < 0:
        return -100.0
    elif zscore < 2:
        return 0.0
    elif zscore < 4:
        return 50.0
    else:
        return 100.0

def nupl_to_score(nupl: float) -> float:
    if nupl < 0:
        return -100.0
    elif nupl < 0.5:
        return 0.0
    elif nupl < 0.75:
        return 50.0
    else:
        return 100.0

def sopr_to_score(sopr: float) -> float:
    if sopr < 1.0:
        return -100.0
    elif sopr < 1.05:
        return 0.0
    elif sopr < 1.1:
        return 50.0
    else:
        return 100.0
