import httpx
import asyncio

BASE_URL = "https://api.hyperliquid.xyz/info"

async def get_clearinghouse_state(address: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(BASE_URL, json={"type": "clearinghouseState", "user": address})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"Hyperliquid error for {address}: {e}")
    return {}

async def get_meta_and_asset_ctxs() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(BASE_URL, json={"type": "metaAndAssetCtxs"})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"Hyperliquid meta error: {e}")
    return []
