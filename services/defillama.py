import httpx
import math

async def get_dry_powder() -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
            data = resp.json()
            
            if len(data) < 2:
                return {"status": "error", "dry_powder_pct": 0}
            
            total_oi = 0
            total_notional = 0
            
            for i, asset in enumerate(data[0].get("universe", [])):
                try:
                    ctx = data[1][i]
                    mark_px = float(ctx.get("markPx", 0))
                    oi = float(ctx.get("openInterest", 0)) * mark_px
                    volume = float(ctx.get("dayNtlVlm", 0))
                    total_oi += oi
                    total_notional += volume
                except:
                    pass
            
            if total_notional == 0:
                return {"status": "error", "dry_powder_pct": 0}
            
            # OI/Volume ratio — higher means more capital sitting vs actively trading
            # Low ratio = heavy trading = money flowing IN = positive dry powder
            # High ratio = low trading = money sitting = negative dry powder
            ratio = total_oi / total_notional if total_notional > 0 else 1
            
            # Normalize: typical ratio is around 2-5
            # ratio < 2 = heavy inflow, ratio > 8 = heavy outflow
            normalized = math.tanh((3 - ratio) / 2) * 100
            
            return {
                "status": "ok",
                "dry_powder_pct": round(normalized, 2),
                "total_oi": round(total_oi, 0),
                "total_volume": round(total_notional, 0),
                "oi_volume_ratio": round(ratio, 2)
            }
    except Exception as e:
        print(f"Dry powder error: {e}")
        return {"status": "error", "dry_powder_pct": 0}
