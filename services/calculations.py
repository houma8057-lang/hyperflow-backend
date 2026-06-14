from sqlalchemy import select, desc
from models import PositionSnapshot
from datetime import datetime, timedelta

class WSICalculator:
    def calculate(self, all_wallet_states: list, meta_and_ctxs: list) -> dict:
        if not meta_and_ctxs or len(meta_and_ctxs) < 2:
            return {"wsi": 0.0, "total_long_ntl": 0.0, "total_short_ntl": 0.0, "total_ntl": 0.0, "long_pct": 0.0, "short_pct": 0.0}
        meta = meta_and_ctxs[0]
        asset_ctxs = meta_and_ctxs[1]
        price_map = {}
        for i, asset in enumerate(meta.get("universe", [])):
            if i < len(asset_ctxs):
                try:
                    price_map[asset["name"]] = float(asset_ctxs[i]["markPx"])
                except:
                    pass
        total_long = 0.0
        total_short = 0.0
        for wallet in all_wallet_states:
            for asset_pos in wallet.get("assetPositions", []):
                pos = asset_pos.get("position", {})
                coin = pos.get("coin", "")
                szi = float(pos.get("szi", 0))
                mark_px = price_map.get(coin, 0)
                if mark_px == 0 or szi == 0:
                    continue
                notional = abs(szi) * mark_px
                if szi > 0:
                    total_long += notional
                elif szi < 0:
                    total_short += notional
        total = total_long + total_short
        wsi = (total_long - total_short) / total if total > 0 else 0.0
        return {
            "wsi": round(wsi, 3),
            "total_long_ntl": round(total_long, 2),
            "total_short_ntl": round(total_short, 2),
            "total_ntl": round(total, 2),
            "long_pct": round(total_long / total * 100, 1) if total > 0 else 0.0,
            "short_pct": round(total_short / total * 100, 1) if total > 0 else 0.0
        }

    async def calculate_whale_delta(self, db, all_wallet_states: list, price_map: dict) -> dict:
        """Compare current positions with previous snapshot to detect closing/adding"""
        # Get latest snapshot (within last hour)
        ago1h = datetime.utcnow() - timedelta(hours=1)
        result = await db.execute(
            select(PositionSnapshot).where(PositionSnapshot.timestamp >= ago1h).order_by(desc(PositionSnapshot.timestamp))
        )
        snapshots = result.scalars().all()

        # Build map of previous positions
        prev_map = {}
        for snap in snapshots:
            key = f"{snap.wallet_address}:{snap.coin}"
            if key not in prev_map:
                prev_map[key] = snap

        # Calculate deltas
        current_short_ntl = 0.0
        current_long_ntl = 0.0
        prev_short_ntl = 0.0
        prev_long_ntl = 0.0

        for wallet in all_wallet_states:
            wallet_addr = wallet.get("wallet_address", "")
            for asset_pos in wallet.get("assetPositions", []):
                pos = asset_pos.get("position", {})
                coin = pos.get("coin", "")
                szi = float(pos.get("szi", 0))
                mark_px = price_map.get(coin, 0)
                if mark_px == 0 or szi == 0:
                    continue
                notional = abs(szi) * mark_px

                key = f"{wallet_addr}:{coin}"
                prev = prev_map.get(key)

                if szi < 0:
                    current_short_ntl += notional
                    if prev and prev.side == "SHORT":
                        prev_short_ntl += abs(prev.szi) * prev.notional / abs(prev.szi) if prev.szi != 0 else 0
                elif szi > 0:
                    current_long_ntl += notional
                    if prev and prev.side == "LONG":
                        prev_long_ntl += abs(prev.szi) * prev.notional / abs(prev.szi) if prev.szi != 0 else 0

        # If no previous data, use current as baseline
        if prev_short_ntl == 0 and prev_long_ntl == 0:
            return {
                "short_delta_pct": 0.0,
                "long_delta_pct": 0.0,
                "closing_short": False,
                "closing_long": False,
                "adding_short": False,
                "adding_long": False,
                "has_history": False
            }

        short_delta = ((current_short_ntl - prev_short_ntl) / prev_short_ntl * 100) if prev_short_ntl > 0 else 0
        long_delta = ((current_long_ntl - prev_long_ntl) / prev_long_ntl * 100) if prev_long_ntl > 0 else 0

        return {
            "short_delta_pct": round(short_delta, 2),
            "long_delta_pct": round(long_delta, 2),
            "closing_short": short_delta < -10,  # Reduced short by >10%
            "closing_long": long_delta < -10,
            "adding_short": short_delta > 10,
            "adding_long": long_delta > 10,
            "has_history": True
        }
