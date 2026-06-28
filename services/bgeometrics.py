import os
import httpx

BGEOMETRICS_TOKEN = os.environ.get("BGEOMETRICS_API_KEY", "3peU12OVuQ")
BASE_URL = "https://api.bgeometrics.com/v1"

async def _fetch_metric(endpoint: str) -> float | None:
    """Generic fetcher for any BGeometrics metric."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/{endpoint}",
                params={"token": BGEOMETRICS_TOKEN, "limit": 5}
            )
            data = resp.json()
            if not data or not isinstance(data, list):
                return None
            for item in reversed(data):
                for key in item:
                    if key not in ("d", "unixTs"):
                        val = item[key]
                        if val is not None and str(val) != "NaN":
                            return float(val)
            return None
    except Exception as e:
        print(f"bgeometrics error ({endpoint}): {e}")
        return None

async def get_latest_mvrv_zscore() -> float | None:
    return await _fetch_metric("mvrv-zscore")

async def get_latest_nupl() -> float | None:
    return await _fetch_metric("nupl")

async def get_latest_sopr() -> float | None:
    return await _fetch_metric("sopr")

def mvrv_zscore_to_score(zscore: float) -> float:
    """
    MVRV Z-Score to signal score (-100 to +100).
    Negative score = bottom zone, Positive = top zone.
    """
    if zscore < 0:
        return -100.0
    elif zscore < 2:
        return 0.0
    elif zscore < 4:
        return 50.0
    else:
        return 100.0

def nupl_to_score(nupl: float) -> float:
    """
    NUPL to signal score (-100 to +100).
    """
    if nupl < 0:
        return -100.0
    elif nupl < 0.5:
        return 0.0
    elif nupl < 0.75:
        return 50.0
    else:
        return 100.0

def sopr_to_score(sopr: float) -> float:
    """
    SOPR to signal score (-100 to +100).
    """
    if sopr < 1.0:
        return -100.0
    elif sopr < 1.05:
        return 0.0
    elif sopr < 1.1:
        return 50.0
    else:
        return 100.0
