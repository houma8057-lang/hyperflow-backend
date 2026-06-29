import os
import httpx

BGEOMETRICS_TOKEN = os.environ.get("BGEOMETRICS_API_KEY", "3peU12OVuQ")
BASE_URL = "https://api.bgeometrics.com/v1"

async def _fetch_latest(endpoint: str) -> float | None:
    """Fetch the single latest value for any BGeometrics metric."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/{endpoint}",
                params={"token": BGEOMETRICS_TOKEN, "limit": 1}
            )
            data = resp.json()
            if not data or not isinstance(data, list):
                return None
            item = data[-1]
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
