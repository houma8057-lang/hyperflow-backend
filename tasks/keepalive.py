import httpx
import asyncio

async def ping_self():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get("https://hyperflow-backend-3l62.onrender.com/ping")
    except:
        pass

def start_keepalive():
    async def loop():
        while True:
            await ping_self()
            await asyncio.sleep(300)  # كل 5 دقائق
    asyncio.create_task(loop())
