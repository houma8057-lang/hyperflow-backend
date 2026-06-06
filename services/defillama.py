import httpx
import math

async def get_dry_powder() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            eth = await client.get("https://api.llama.fi/stablecoincharts/Ethereum")
            arb = await client.get("https://api.llama.fi/stablecoincharts/Arbitrum")
            if eth.status_code != 200 or arb.status_code != 200:
                return {"status": "error", "dry_powder_pct": 0}
            eth_data = sorted(eth.json(), key=lambda x: x["date"])
            arb_data = sorted(arb.json(), key=lambda x: x["date"])
            eth_change = float(eth_data[-1]["totalCirculatingUSD"]) - float(eth_data[-2]["totalCirculatingUSD"])
            arb_change = float(arb_data[-1]["totalCirculatingUSD"]) - float(arb_data[-2]["totalCirculatingUSD"])
            net = eth_change + arb_change
            normalized = math.tanh(net / 100_000_000)
            return {
                "status": "ok",
                "dry_powder_pct": round(normalized * 100, 2),
                "eth_change": eth_change,
                "arb_change": arb_change
            }
    except Exception as e:
        print(f"DeFiLlama error: {e}")
        return {"status": "error", "dry_powder_pct": 0}
