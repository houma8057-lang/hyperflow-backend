from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import AsyncSessionLocal
from models import Wallet, WSIHistory, PositionSnapshot, Alert, SystemSettings, OIHistory
from services.hyperliquid import get_clearinghouse_state, get_meta_and_asset_ctxs
from services.defillama import get_dry_powder
from services.calculations import WSICalculator
from sqlalchemy import select, desc
from datetime import datetime, timedelta, timezone
import asyncio
import httpx

scheduler = AsyncIOScheduler()

async def snapshot_job():
    print(f"snapshot_job: TICK at top of function")
    async with AsyncSessionLocal() as db:
        try:
            wallets = (await db.execute(select(Wallet))).scalars().all()
            if not wallets:
                return
            meta_ctxs = await get_meta_and_asset_ctxs()
            if not meta_ctxs:
                return
            states = []
            for w in wallets:
                state = await get_clearinghouse_state(w.address)
                if state:
                    states.append(state)
                    for ap in state.get("assetPositions", []):
                        pos = ap.get("position", {})
                        szi = float(pos.get("szi", 0))
                        if szi == 0:
                            continue
                        snap = PositionSnapshot(
                            wallet_address=w.address,
                            coin=pos.get("coin", ""),
                            side="LONG" if szi > 0 else "SHORT",
                            szi=szi,
                            entry_px=float(pos.get("entryPx", 0)),
                            notional=float(pos.get("positionValue", 0)),
                            unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                            leverage=float(pos.get("leverage", {}).get("value", 1))
                        )
                        db.add(snap)
                await asyncio.sleep(0.2)
            calc = WSICalculator()
            result = calc.calculate(states, meta_ctxs)
            ago24 = datetime.utcnow() - timedelta(hours=24)
            old = (await db.execute(select(WSIHistory).where(WSIHistory.timestamp.isnot(None)).where(WSIHistory.timestamp <= ago24).order_by(desc(WSIHistory.timestamp)).limit(1))).scalar_one_or_none()
            delta_wsi = result["wsi"] - old.wsi_value if old else 0.0
            dp = await get_dry_powder()
            dry = dp.get("dry_powder_pct", 0) / 100
            reversal = round(0.5 * delta_wsi + 0.2 * dry, 3)
            entry = WSIHistory(timestamp=datetime.now(timezone.utc), wsi_value=result["wsi"], total_long_ntl=result["total_long_ntl"], total_short_ntl=result["total_short_ntl"], wallet_count=len(wallets), reversal_score=reversal)
            db.add(entry)
            settings = (await db.execute(select(SystemSettings).where(SystemSettings.id == 1))).scalar_one_or_none()
            threshold = settings.alert_threshold if settings else 0.60
            if abs(reversal) > threshold:
                alert = Alert(reversal_score=reversal, signal_type="BOTTOM" if reversal > 0 else "TOP", wsi_at_trigger=result["wsi"], delta_wsi_24h=delta_wsi, oi_divergence=0.0, dry_powder=dry)
                db.add(alert)
            await db.commit()
            print(f"snapshot_job: SUCCESS, saved WSI={result['wsi']}")
        except Exception as e:
            print(f"snapshot_job error: {e}")

async def oi_snapshot_job():
    async with AsyncSessionLocal() as db:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
                data = resp.json()
                if len(data) >= 2:
                    for i, asset in enumerate(data[0].get("universe", [])):
                        if asset["name"] in ["BTC", "ETH", "SOL", "BNB", "DOGE", "HYPE"]:
                            try:
                                ctx = data[1][i]
                                mark_px = float(ctx.get("markPx", 0))
                                oi = float(ctx.get("openInterest", 0)) * mark_px
                                db.add(OIHistory(
                                    coin=asset["name"],
                                    open_interest_usd=oi,
                                    mark_price=mark_px
                                ))
                            except:
                                pass
            await db.commit()
        except Exception as e:
            print(f"oi_snapshot_job error: {e}")

async def mvrv_snapshot_job():
    """Fetch and store latest MVRV Z-Score once per day"""
    from models import MVRVHistory
    from services.bgeometrics import get_latest_mvrv_zscore
    async with AsyncSessionLocal() as db:
        try:
            zscore = await get_latest_mvrv_zscore(db)
            if zscore is None:
                print("mvrv_snapshot_job: no data returned")
                return
            from sqlalchemy import func
            from datetime import date
            today = date.today().isoformat()
            db.add(MVRVHistory(
                timestamp=datetime.now(timezone.utc),
                date=today,
                zscore=zscore
            ))
            await db.commit()
            print(f"mvrv_snapshot_job: saved zscore={zscore} for {today}")
        except Exception as e:
            print(f"mvrv_snapshot_job error: {e}")

def start_scheduler():
    scheduler.add_job(snapshot_job, "interval", minutes=5)
    scheduler.add_job(oi_snapshot_job, "interval", hours=1)
    scheduler.add_job(mvrv_snapshot_job, "interval", hours=12)
    scheduler.start()
