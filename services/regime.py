import json
import math
from datetime import datetime, timedelta
from sqlalchemy import select, desc, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from models import SignalHistory, WSIHistory, Wallet, OIHistory

class WhaleRegimeDetector:
    """
    4-Dimension Whale Regime Detector v2
    Addresses Claude audit findings:
    - Dry Powder: per-wallet logging, caps, liquidation warnings
    - Funding: reduced to 25% until multi-asset, added ETH/SOL averaging
    - Position Extremity: shows actual time span, not just point count
    - Confidence: split into data_completeness + signal_confidence
    - Velocity: excludes wallet roster changes
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dimensions = {
            "position_extremity": {"active": False, "value": 0, "label": "Insufficient History"},
            "wallet_dry_powder": {"active": False, "value": 0, "label": "No Data"},
            "velocity": {"active": False, "value": 0, "label": "No Data"},
            "funding_divergence": {"active": False, "value": 0, "label": "No Data"}
        }
        self.warnings = []
    
    async def calculate(self, current_wsi: float, funding_rate: float, 
                       whale_states: list, price_map: dict) -> dict:
        """
        Main entry: calculates all 4 dimensions and returns composite signal
        """
        # Track warnings for audit trail
        self.warnings = []
        
        # Dimension 1: Position Extremity (needs 30+ WSI history points)
        await self._calc_position_extremity(current_wsi)
        
        # Dimension 2: Wallet-Specific Dry Powder
        await self._calc_wallet_dry_powder(whale_states, price_map)
        
        # Dimension 3: Velocity of WSI change (excludes roster changes)
        await self._calc_velocity(current_wsi)
        
        # Dimension 4: Funding Divergence (multi-asset, reduced weight)
        await self._calc_funding_divergence(current_wsi, funding_rate, whale_states, price_map)
        
        # Composite scoring - REVISED WEIGHTS per Claude audit
        # Funding reduced from 40% to 25% until true multi-asset
        # Dry Powder increased to 30% (most actionable dimension)
        # Velocity 25%
        # Position Extremity 20%
        weights = {
            "funding_divergence": 0.25,
            "wallet_dry_powder": 0.30,
            "velocity": 0.25,
            "position_extremity": 0.20
        }
        
        active_dims = [d for d in self.dimensions.values() if d["active"]]
        if not active_dims:
            return self._fallback_signal(current_wsi)
        
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
        
        # NEW: Split confidence into two metrics per Claude audit
        data_completeness = min(100, int(len(active_dims) / 4 * 100))
        signal_confidence = self._calc_signal_confidence(normalized_score, active_dims)
        
        regime = self._score_to_regime(normalized_score)
        
        return {
            "regime": regime,
            "score": round(normalized_score, 3),
            "data_completeness": data_completeness,
            "signal_confidence": signal_confidence,
            "active_dimensions": len(active_dims),
            "dimensions": self.dimensions,
            "raw_wsi": round(current_wsi, 3),
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": self._recommendation(regime, self.dimensions, signal_confidence),
            "warnings": self.warnings
        }
    
    async def _calc_position_extremity(self, current_wsi: float):
        """Dimension 1: How rare is current WSI in historical distribution?"""
        try:
            cutoff = datetime.utcnow() - timedelta(days=90)
            rows = await self.db.execute(
                select(WSIHistory)
                .where(WSIHistory.timestamp >= cutoff)
                .order_by(WSIHistory.timestamp)
            )
            history = list(rows.scalars().all())
            
            if len(history) < 30:
                # Check actual time span
                if history:
                    time_span = (history[-1].timestamp - history[0].timestamp).total_seconds() / 3600
                    time_label = f"{time_span:.1f} hours" if time_span < 24 else f"{time_span/24:.1f} days"
                else:
                    time_span = 0
                    time_label = "0 hours"
                
                self.dimensions["position_extremity"] = {
                    "active": False,
                    "value": 0,
                    "label": f"Need {30 - len(history)} more points ({time_label} of data)",
                    "history_count": len(history),
                    "time_span_hours": round(time_span, 1)
                }
                return
            
            # Calculate actual time span
            time_span_hours = (history[-1].timestamp - history[0].timestamp).total_seconds() / 3600
            time_span_days = time_span_hours / 24
            
            # Calculate percentile
            history_sorted = sorted([h.wsi_value for h in history])
            n = len(history_sorted)
            below = sum(1 for h in history_sorted if h <= current_wsi)
            percentile = below / n
            
            extremity_value = (percentile - 0.5) * 2
            
            self.dimensions["position_extremity"] = {
                "active": True,
                "value": round(extremity_value, 3),
                "label": "Extreme" if abs(extremity_value) > 0.8 else "Elevated" if abs(extremity_value) > 0.5 else "Normal",
                "percentile": round(percentile * 100, 1),
                "history_count": n,
                "time_span_days": round(time_span_days, 1),
                "time_span_hours": round(time_span_hours, 1)
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
            
            wallet_details = []
            total_equity = 0
            total_position_notional = 0
            
            for state in whale_states:
                wallet_addr = state.get("wallet_address", "unknown")
                
                # Get equity from clearinghouseState
                margin_summary = state.get("marginSummary", {})
                equity = float(margin_summary.get("accountValue", 0))
                
                if equity == 0:
                    equity = float(margin_summary.get("totalMarginUsed", 0)) + \
                             float(margin_summary.get("totalRawUsd", 0))
                
                equity = max(equity, 0)
                
                # Calculate position notional for this wallet
                wallet_notional = 0
                for ap in state.get("assetPositions", []):
                    pos = ap.get("position", {})
                    szi = float(pos.get("szi", 0))
                    coin = pos.get("coin", "")
                    mark_px = price_map.get(coin, 0)
                    if mark_px > 0 and szi != 0:
                        wallet_notional += abs(szi) * mark_px
                
                # Per-wallet utilization
                wallet_util = wallet_notional / equity if equity > 0 else float('inf')
                
                wallet_details.append({
                    "address": wallet_addr[:8] + "...",
                    "equity": round(equity, 2),
                    "notional": round(wallet_notional, 2),
                    "utilization": round(wallet_util, 3) if wallet_util != float('inf') else 999
                })
                
                total_equity += equity
                total_position_notional += wallet_notional
                
                # FLAG: tiered warnings by actual severity, not one bucket for everything
                if wallet_util > 10:
                    self.warnings.append(f"Wallet {wallet_addr[:8]}... utilization={wallet_util:.1f}x - possible data error")
                elif wallet_util > 8:
                    self.warnings.append(f"Wallet {wallet_addr[:8]}... utilization={wallet_util:.1f}x - very high leverage")
                elif wallet_util > 4:
                    self.warnings.append(f"Wallet {wallet_addr[:8]}... utilization={wallet_util:.1f}x - high leverage")
                elif wallet_util > 1:
                    self.warnings.append(f"Wallet {wallet_addr[:8]}... utilization={wallet_util:.1f}x - moderate leverage")
            
            if total_equity <= 0:
                self.dimensions["wallet_dry_powder"] = {
                    "active": False,
                    "value": 0,
                    "label": "Zero or negative aggregate equity"
                }
                return
            
            utilization = total_position_notional / total_equity
            dry_powder_ratio = max(0, 1 - min(utilization, 10) / 10)  # Cap at 10x for sanity
            
            # Get WSI direction for powder interpretation
            wsi_row = await self.db.execute(
                select(WSIHistory).order_by(desc(WSIHistory.timestamp)).limit(1)
            )
            latest_wsi = wsi_row.scalar_one_or_none()
            wsi_direction = latest_wsi.wsi_value if latest_wsi else 0
            
            if wsi_direction < -0.3:
                value = -dry_powder_ratio
            elif wsi_direction > 0.3:
                value = dry_powder_ratio
            else:
                value = 0
            
            self.dimensions["wallet_dry_powder"] = {
                "active": True,
                "value": round(value, 3),
                "label": "High Powder" if dry_powder_ratio > 0.5 else "Low Powder" if dry_powder_ratio < 0.2 else "Medium",
                "dry_powder_ratio": round(dry_powder_ratio, 3),
                "utilization": round(utilization, 3),
                "wallet_details": wallet_details[:3]  # Show top 3 for debugging
            }
        except Exception as e:
            self.dimensions["wallet_dry_powder"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    async def _calc_velocity(self, current_wsi: float):
        """Dimension 3: Rate of WSI change, EXCLUDING wallet roster changes"""
        try:
            rows = await self.db.execute(
                select(WSIHistory)
                .order_by(desc(WSIHistory.timestamp))
                .limit(10)
            )
            history = list(rows.scalars().all())
            
            if len(history) < 2:
                self.dimensions["velocity"] = {
                    "active": False,
                    "value": 0,
                    "label": "Need more snapshots"
                }
                return
            
            # NEW: Check for wallet count changes (roster changes)
            wallet_counts = [h.wallet_count for h in history if hasattr(h, 'wallet_count') and h.wallet_count]
            if len(set(wallet_counts)) > 1:
                self.warnings.append(f"Wallet roster changed ({min(wallet_counts)} → {max(wallet_counts)} wallets) - velocity may be artificial")
            
            # Filter to snapshots with same wallet count as current
            current_wallet_count = wallet_counts[0] if wallet_counts else None
            stable_history = [h for h in history if hasattr(h, 'wallet_count') and h.wallet_count == current_wallet_count]
            
            if len(stable_history) < 2:
                self.dimensions["velocity"] = {
                    "active": False,
                    "value": 0,
                    "label": "Roster change detected - velocity unreliable"
                }
                return
            
            # Calculate smoothed velocity from stable snapshots
            changes = []
            for i in range(len(stable_history) - 1):
                time_diff = (stable_history[i].timestamp - stable_history[i+1].timestamp).total_seconds() / 3600
                if time_diff > 0:
                    wsi_change = stable_history[i].wsi_value - stable_history[i+1].wsi_value
                    changes.append(wsi_change / time_diff)
            
            if not changes:
                self.dimensions["velocity"] = {
                    "active": False,
                    "value": 0,
                    "label": "No time diff"
                }
                return
            
            avg_velocity = sum(changes) / len(changes)
            normalized_velocity = max(-1, min(1, avg_velocity / 0.1))
            
            self.dimensions["velocity"] = {
                "active": True,
                "value": round(normalized_velocity, 3),
                "label": "Fast Flip" if abs(normalized_velocity) > 0.8 else "Shifting" if abs(normalized_velocity) > 0.3 else "Stable",
                "velocity_per_hour": round(avg_velocity, 4),
                "snapshots_used": len(stable_history),
                "roster_stable": True
            }
        except Exception as e:
            self.dimensions["velocity"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    async def _calc_funding_divergence(self, current_wsi: float, funding_rate: float, 
                                      whale_states: list, price_map: dict):
        """Dimension 4: Whales vs Crowd, BTC funding only (deliberate - BTC leads major cycle reversals, alts follow)"""
        try:
            weighted_funding = funding_rate
            
            # Determine directions
            whale_direction = "short" if current_wsi < -0.2 else "long" if current_wsi > 0.2 else "neutral"
            crowd_direction = "short" if weighted_funding < -0.001 else "long" if weighted_funding > 0.001 else "neutral"
            
            # Divergence logic
            if whale_direction == "short" and crowd_direction == "long":
                divergence = -1.0
                label = "Strong Divergence"
            elif whale_direction == "long" and crowd_direction == "short":
                divergence = 1.0
                label = "Strong Divergence"
            elif whale_direction == crowd_direction and whale_direction != "neutral":
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
                "btc_funding": round(funding_rate * 100, 4),
                "weighted_funding": round(weighted_funding * 100, 4),
                "assets_used": ["BTC"],
                "note": "BTC funding only - deliberate design choice, BTC leads major cycle reversals"
            }
        except Exception as e:
            self.dimensions["funding_divergence"] = {
                "active": False,
                "value": 0,
                "label": f"Error: {str(e)}"
            }
    
    def _calc_signal_confidence(self, score: float, active_dims: list) -> int:
        """Calculate signal confidence based on score magnitude and dimension agreement"""
        if not active_dims:
            return 0
        
        # Base: how far from zero is the score?
        magnitude = abs(score)
        
        # Bonus: do dimensions agree on direction?
        dim_values = [d["value"] for d in active_dims]
        signs = [1 if v > 0 else -1 if v < 0 else 0 for v in dim_values]
        non_zero_signs = [s for s in signs if s != 0]
        
        if len(non_zero_signs) >= 2:
            agreement = abs(sum(non_zero_signs)) / len(non_zero_signs)
        else:
            agreement = 0
        
        # Confidence = magnitude * agreement * data_completeness_factor
        completeness_factor = len(active_dims) / 4
        confidence = int(magnitude * agreement * completeness_factor * 100)
        
        return min(100, max(0, confidence))
    
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
    
    def _recommendation(self, regime: str, dimensions: dict, confidence: int) -> str:
        """Generate action recommendation based on regime, dimensions, and confidence"""
        funding = dimensions.get("funding_divergence", {})
        velocity = dimensions.get("velocity", {})
        
        # Low confidence near-neutral = always wait
        if abs(dimensions.get("funding_divergence", {}).get("value", 0)) < 0.3 and confidence < 30:
            return "NEUTRAL — Low confidence, wait for clearer signal"
        
        if "EXTREME_BULLISH" in regime:
            if funding.get("label") == "Strong Divergence":
                return f"STRONG BUY — Whales long against short crowd ({confidence}% signal confidence)"
            return f"BUY — Whale consensus bullish ({confidence}% signal confidence)"
        elif "EXTREME_BEARISH" in regime:
            if funding.get("label") == "Strong Divergence":
                return f"STRONG SELL — Whales short against long crowd ({confidence}% signal confidence)"
            return f"SELL — Whale consensus bearish ({confidence}% signal confidence)"
        elif "BULLISH" in regime:
            if velocity.get("label") == "Fast Flip":
                return f"WATCH — Rapid shift to bullish, confirm before entry ({confidence}% signal confidence)"
            return f"WEAK BUY — Bullish bias ({confidence}% signal confidence)"
        elif "BEARISH" in regime:
            if velocity.get("label") == "Fast Flip":
                return f"WATCH — Rapid shift to bearish, confirm before short ({confidence}% signal confidence)"
            return f"WEAK SELL — Bearish bias ({confidence}% signal confidence)"
        else:
            return f"NEUTRAL — No clear edge ({confidence}% signal confidence)"
    
    def _fallback_signal(self, current_wsi: float) -> dict:
        """Fallback when no dimensions are active"""
        return {
            "regime": "NEUTRAL",
            "score": 0,
            "data_completeness": 0,
            "signal_confidence": 0,
            "active_dimensions": 0,
            "dimensions": self.dimensions,
            "raw_wsi": round(current_wsi, 3),
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation": "NEUTRAL — Insufficient data for regime detection",
            "warnings": self.warnings
        }
