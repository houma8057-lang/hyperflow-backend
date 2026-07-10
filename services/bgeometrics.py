import os
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

BGEOMETRICS_TOKEN = os.environ.get("BGEOMETRICS_API_KEY", "3peU12OVuQ")
BASE_URL = "https://api.bgeometrics.com/v1"

# Free plan: 10 req/hour, 15 req/day. Underlying data updates once per
# day anyway, so a 24h cache loses no real freshness while cutting
# daily usage from thousands (30s frontend polling) to ~3-6/day.
# Persisted in DB (metric_cache table) so it survives redeploys/cold starts,
# unlike an in-memory dict.
CACHE_TTL = timedelta(hours=24)

async def _get_cached(db: AsyncSession, metric: str):
    from models import MetricCache
    row = (await db.execute(
        select(MetricCache).where(MetricCache.metric == metric)
    )).scalar_one_or_none()
    return row

async def _save_cache(db: AsyncSession, metric: str, value: float, row):
    from models import MetricCache
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(MetricCache(metric=metric, value=value, fetched_at=now))
    else:
        row.value = value
        row.fetched_at = now
    await db.commit()

async def _fetch_latest(db: AsyncSession, endpoint: str, metric: str) -> float | None:
    """Fetch the single latest value for any BGeometrics metric.
    Cached in the metric_cache table for CACHE_TTL; on any failure,
    falls back to the last known DB value (even if stale) instead of None.
    """
    row = await _get_cached(db, metric)
    now = datetime.now(timezone.utc)
    if row is not None and (now - row.fetched_at) < CACHE_TTL:
        return row.value
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/{endpoint}",
                params={"token": BGEOMETRICS_TOKEN, "limit": 1}
            )
            data = resp.json()
            if not data or not isinstance(data, list):
                return row.value if row else None
            item = data[-1]
            for key in item:
                if key not in ("d", "unixTs"):
                    val = item[key]
                    if val is not None and str(val) != "NaN":
                        result = float(val)
                        await _save_cache(db, metric, result, row)
                        print(f"bgeometrics: fetched fresh {metric} = {result}")
                        return result
            return row.value if row else None
    except Exception as e:
        print(f"bgeometrics error ({endpoint}): {e}")
        return row.value if row else None

async def get_latest_mvrv_zscore(db: AsyncSession) -> float | None:
    return await _fetch_latest(db, "mvrv-zscore", "mvrv_z")

async def get_latest_nupl(db: AsyncSession) -> float | None:
    return await _fetch_latest(db, "nupl", "nupl")

async def get_latest_sopr(db: AsyncSession) -> float | None:
    return await _fetch_latest(db, "sopr", "sopr")

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
