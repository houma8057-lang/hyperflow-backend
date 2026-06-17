import json
from datetime import datetime, timedelta
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from models import SignalHistory, WSIHistory, Wallet, OIHistory

class WhaleRegimeDetector:
    """
    4-Dimension Whale Regime Detector
    Replaces fragile WSI with composite signal
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dimensions = {
            "position_extremity": {"active": False, "value": 0, "label": "Insufficient History"},
            "wallet_dry_powder": {"active": False, "value": 0, "label": "No Data"},
            "velocity": {"active": False, "value": 0, "label": "No Data"},
            "funding_divergence": {"active": False, "value": 0, "label": "No Data"}
        }
    
    async def calculate(self, current_wsi: float, funding_rate: float, 
                       whale_states: list, price_map: dict) -> dict:
        """
        Main entry: calculates all 4 dimensions and returns composite signal
        """
        # Dimension 1: Position Extremity (needs 30+ WSI history points)
        await self._calc_position_extremity(current_wsi)
        
        # Dimension 2: Wallet-Specific Dry Powder
        await self._calc_wallet_dry_powder(whale_states, price_map)
        
        # Dimension 3: Velocity of WSI change
        await self._calc_velocity(current_wsi)
        
        # Dimension 4: Funding Divergence (whales vs crowd)
        await self._calc_funding_divergence(current_wsi, funding_rate)
        
        # Composite scoring
        active_dims = [d for d in self.dimensions.values() if d["active"]]
        if not active_dims:
            return self._fallback_signal(current_wsi)
        
        # Weight: Funding Divergence = 40%, Velocity = 25%, Dry Powder = 25%, Extremity = 10%
        weights = {
            "funding_divergence": 0.40,
            "velocity": 0.25,
            "wallet_dry_powder": 0.25,
            "position_extremity": 0.10
        }
        
        weighted_score = 0
        total_weight = 0
        for key, dim in self.dimensions.items():
            if dim["active"]:
                weighted_score += dim["value"] * weights[key]
                total_weight += weights[key]
        
        if total_weight > 0:
            normalized_score = weighted_score / total_weight
        else:
            return self._fallback_signal(current_wsi)
        
        # Map score to signal
        # Score range: -1.0 (extreme bearish) to +1.0 (extreme bullish)
        regime = self._score_to_regime(normalized_score)
        confidence = min(95, int(len(active_dims) / 4 * 100 + 15))  # More dims = higher confidence
        
        return {
            "regime": regime,
            "score": round(normalized_score, 3),
            "confidence": confidence,
            "active_dimensions": len(active_dims),
            "dimensions": self.dimensions,
            "raw_wsi": round(current_wsi, 3),
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": self._recommendation(regime, self.dimensions)
        }
    
    async def _calc_position_extremity(self, current_wsi: float):
        """Dimension 1: How rare is current WSI in historical distribution?"""
        try:
            # Get last 90 days of WSI history
            cutoff = datetime.utcnow() - timedelta(days=90)
            rows = await self.db.execute(
                select(WSIHistory.wsi_value)
                .where(WSIHistory.timestamp >= cutoff)
                .order_by(WSIHistory.timestamp)
            )
            history = [r[0] for r in rows.all()]
            
            if len(history) < 30:
                self.dimensions["position_extremity"] = {
                    "active": False,
                    "value": 0,
                    "label": f"Need {30 - len(history)} more days",
                    "history_count": len(history)
                }
                return
            
            # Calculate percentile
            history_sorted = sorted(history)
            n = len(history_sorted)
            
            # Find percentile of current_wsi
            below = sum(1 for h in history_sorted if h <= current_wsi)
            percentile = below / n
            
            # Convert to -1 to +1 scale
            # 0th percentile = -1.0 (most bearish ever), 100th = +1.0 (most bullish ever)
            extremity_value = (percentile - 0.5) * 2
            
            self.dimensions["position_extremity"] = {
                "active": True,
                "value": round(extremity_value, 3),
                "label": "Extreme" if abs(extremity_value) > 0.8 else "Elevated" if abs(extremity_value) > 0.5 else "Normal",
                "percentile": round(percentile * 100, 1),
                "history_count": n
            }
        except Exception as e:
            self.dimensions["position_extremity"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    async def _calc_wallet_dry_powder(self, whale_states: list, price_map: dict):
        """Dimension 2: Per-wallet available margin vs position size"""
        try:
            if not whale_states:
                self.dimensions["wallet_dry_powder"] = {
                    "active": False,
                    "value": 0,
                    "label": "No wallet data"
                }
                return
            
            total_equity = 0
            total_position_notional = 0
            
            for state in whale_states:
                # Get equity from clearinghouseState
                equity = float(state.get("marginSummary", {}).get("accountValue", 0))
                if equity == 0:
                    equity = float(state.get("marginSummary", {}).get("totalMarginUsed", 0)) + \
                             float(state.get("marginSummary", {}).get("totalRawUsd", 0))
                
                total_equity += max(equity, 0)
                
                # Calculate total position notional
                for ap in state.get("assetPositions", []):
                    pos = ap.get("position", {})
                    szi = float(pos.get("szi", 0))
                    coin = pos.get("coin", "")
                    mark_px = price_map.get(coin, 0)
                    if mark_px > 0 and szi != 0:
                        total_position_notional += abs(szi) * mark_px
            
            if total_equity <= 0:
                self.dimensions["wallet_dry_powder"] = {
                    "active": False,
                    "value": 0,
                    "label": "Zero equity"
                }
                return
            
            utilization = total_position_notional / total_equity
            dry_powder_ratio = 1 - min(utilization, 1.0)
            
            # Map to signal: High dry powder = can sustain current direction = continuation
            # Low dry powder = maxed out = reversal risk
            # Value: +1 = high dry powder (bullish continuation if long, bearish continuation if short)
            # But we need to know direction first... we'll use WSI for that
            wsi_row = await self.db.execute(
                select(WSIHistory).order_by(desc(WSIHistory.timestamp)).limit(1)
            )
            latest_wsi = wsi_row.scalar_one_or_none()
            wsi_direction = latest_wsi.wsi_value if latest_wsi else 0
            
            # If whales are short (WSI < 0) and have high dry powder = bearish continuation
            # If whales are short and low dry powder = short squeeze risk (bullish)
            if wsi_direction < -0.3:
                # Short regime
                value = -dry_powder_ratio  # High dry powder = more shorting possible = bearish
            elif wsi_direction > 0.3:
                # Long regime
                value = dry_powder_ratio  # High dry powder = more buying possible = bullish
            else:
                # Neutral
                value = 0
            
            self.dimensions["wallet_dry_powder"] = {
                "active": True,
                "value": round(value, 3),
                "label": "High Powder" if dry_powder_ratio > 0.5 else "Low Powder" if dry_powder_ratio < 0.2 else "Medium",
                "dry_powder_ratio": round(dry_powder_ratio, 3),
                "utilization": round(utilization, 3)
            }
        except Exception as e:
            self.dimensions["wallet_dry_powder"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    async def _calc_velocity(self, current_wsi: float):
        """Dimension 3: Rate of WSI change (smoothed over 3 snapshots)"""
        try:
            # Get last 3 WSI snapshots
            rows = await self.db.execute(
                select(WSIHistory)
                .order_by(desc(WSIHistory.timestamp))
                .limit(4)
            )
            history = list(rows.scalars().all())
            
            if len(history) < 2:
                self.dimensions["velocity"] = {
                    "active": False,
                    "value": 0,
                    "label": "Need more snapshots"
                }
                return
            
            # Calculate smoothed velocity (3-point average of changes)
            changes = []
            for i in range(len(history) - 1):
                time_diff = (history[i].timestamp - history[i+1].timestamp).total_seconds() / 3600  # hours
                if time_diff > 0:
                    wsi_change = history[i].wsi_value - history[i+1].wsi_value
                    changes.append(wsi_change / time_diff)  # change per hour
            
            if not changes:
                self.dimensions["velocity"] = {
                    "active": False,
                    "value": 0,
                    "label": "No time diff"
                }
                return
            
            avg_velocity = sum(changes) / len(changes)
            
            # Normalize: typical WSI range is -1 to 1, changes of 0.1/hour are significant
            normalized_velocity = max(-1, min(1, avg_velocity / 0.1))
            
            self.dimensions["velocity"] = {
                "active": True,
                "value": round(normalized_velocity, 3),
                "label": "Fast Flip" if abs(normalized_velocity) > 0.8 else "Shifting" if abs(normalized_velocity) > 0.3 else "Stable",
                "velocity_per_hour": round(avg_velocity, 4)
            }
        except Exception as e:
            self.dimensions["velocity"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    async def _calc_funding_divergence(self, current_wsi: float, funding_rate: float):
        """Dimension 4: Whales vs Crowd — strongest signal"""
        try:
            # WSI < 0 = whales are short, WSI > 0 = whales are long
            # Funding > 0 = crowd is long (paying to hold longs), Funding < 0 = crowd is short
            
            whale_direction = "short" if current_wsi < -0.2 else "long" if current_wsi > 0.2 else "neutral"
            crowd_direction = "long" if funding_rate > 0.001 else "short" if funding_rate < -0.001 else "neutral"
            
            # Divergence = whales opposite to crowd
            if whale_direction == "short" and crowd_direction == "long":
                # Whales short, crowd long = STRONG BEARISH (smart money against crowd)
                divergence = -1.0
                label = "Strong Divergence"
            elif whale_direction == "long" and crowd_direction == "short":
                # Whales long, crowd short = STRONG BULLISH
                divergence = 1.0
                label = "Strong Divergence"
            elif whale_direction == crowd_direction and whale_direction != "neutral":
                # Both agree = weak signal (no edge)
                divergence = 0.2 if whale_direction == "long" else -0.2
                label = "Aligned (Weak)"
            else:
                divergence = 0
                label = "Neutral"
            
            self.dimensions["funding_divergence"] = {
                "active": True,
                "value": round(divergence, 3),
                "label": label,
                "whale_direction": whale_direction,
                "crowd_direction": crowd_direction,
                "funding_rate": round(funding_rate * 100, 4)
            }
        except Exception as e:
            self.dimensions["funding_divergence"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    def _score_to_regime(self, score: float) -> str:
        """Convert composite score to regime label"""
        if score >= 0.7:
            return "EXTREME_BULLISH"
        elif score >= 0.4:
            return "BULLISH"
        elif score >= 0.1:
            return "SLIGHTLY_BULLISH"
        elif score > -0.1:
            return "NEUTRAL"
        elif score > -0.4:
            return "SLIGHTLY_BEARISH"
        elif score > -0.7:
            return "BEARISH"
        else:
            return "EXTREME_BEARISH"
    
    def _recommendation(self, regime: str, dimensions: dict) -> str:
        """Generate action recommendation based on regime and dimensions"""
        funding = dimensions.get("funding_divergence", {})
        velocity = dimensions.get("velocity", {})
        powder = dimensions.get("wallet_dry_powder", {})
        
        if "EXTREME_BULLISH" in regime:
            if funding.get("label") == "Strong Divergence":
                return "STRONG BUY — Whales long against short crowd"
            return "BUY — Whale consensus bullish"
        elif "EXTREME_BEARISH" in regime:
            if funding.get("label") == "Strong Divergence":
                return "STRONG SELL — Whales short against long crowd"
            return "SELL — Whale consensus bearish"
        elif "BULLISH" in regime:
            if velocity.get("label") == "Fast Flip":
                return "WATCH — Rapid shift to bullish, confirm before entry"
            return "WEAK BUY — Bullish bias"
        elif "BEARISH" in regime:
            if velocity.get("label") == "Fast Flip":
                return "WATCH — Rapid shift to bearish, confirm before short"
            return "WEAK SELL — Bearish bias"
        else:
            return "NEUTRAL — No clear edge"
    
    def _fallback_signal(self, current_wsi: float) -> dict:
        """Fallback when no dimensions are active"""
        return {
            "regime": "NEUTRAL",
            "score": 0,
            "confidence": 0,
            "active_dimensions": 0,
            "dimensions": self.dimensions,
            "raw_wsi": round(current_wsi, 3),
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": "NEUTRAL — Insufficient data for regime detection"
        }
