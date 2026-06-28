import os
import httpx

BGEOMETRICS_TOKEN = os.environ.get("BGEOMETRICS_API_KEY", "3peU12OVuQ")
BASE_URL = "https://api.bgeometrics.com/v1"

async def get_latest_mvrv_zscore() -> float | None:
    """
    Fetch the latest MVRV Z-Score from BGeometrics.
    Returns float or None if failed.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/mvrv-zscore",
                params={"token": BGEOMETRICS_TOKEN, "limit": 5}
            )
            data = resp.json()
            if not data or not isinstance(data, list):
                return None
            # Get the latest valid value (last in list)
            for item in reversed(data):
                val = item.get("mvrvZscore")
                if val is not None and str(val) != "NaN":
                    return float(val)
            return None
    except Exception as e:
        print(f"bgeometrics error: {e}")
        return None

def mvrv_zscore_to_signal(zscore: float) -> float:
    """
    Convert MVRV Z-Score to regime signal value (-1 to +1).
    < 0      : bottom zone   -> +1.0
    0 to 2   : neutral       ->  0.0
    2 to 4   : warning zone  -> -0.5
    > 4      : top zone      -> -1.0
    """
    if zscore < 0:
        return 1.0
    elif zscore < 2:
        return 0.0
    elif zscore < 4:
        return -0.5
    else:
        return -1.0
