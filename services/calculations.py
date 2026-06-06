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
