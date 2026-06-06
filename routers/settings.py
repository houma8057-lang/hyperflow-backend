from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import SystemSettings
from pydantic import BaseModel

router = APIRouter()

class SettingsIn(BaseModel):
    alert_threshold: float = 0.60
    polling_interval: int = 10

@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(SystemSettings).where(SystemSettings.id == 1))).scalar_one_or_none()
    if not s:
        return {"alert_threshold": 0.60, "polling_interval": 10}
    return {"alert_threshold": s.alert_threshold, "polling_interval": s.polling_interval}

@router.put("/settings")
async def update_settings(data: SettingsIn, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(SystemSettings).where(SystemSettings.id == 1))).scalar_one_or_none()
    if not s:
        s = SystemSettings(id=1, alert_threshold=data.alert_threshold, polling_interval=data.polling_interval)
        db.add(s)
    else:
        s.alert_threshold = data.alert_threshold
        s.polling_interval = data.polling_interval
    await db.commit()
    return {"updated": True}

@router.get("/alerts")
async def get_alerts(db: AsyncSession = Depends(get_db)):
    from models import Alert
    from sqlalchemy import desc
    rows = (await db.execute(select(Alert).order_by(desc(Alert.timestamp)).limit(50))).scalars().all()
    return [{"id": r.id, "timestamp": r.timestamp, "signal_type": r.signal_type, "reversal_score": r.reversal_score, "wsi_at_trigger": r.wsi_at_trigger} for r in rows]
