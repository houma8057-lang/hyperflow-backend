from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Wallet
from pydantic import BaseModel
import re

router = APIRouter()

class WalletIn(BaseModel):
    address: str
    label: str = ""

@router.post("/wallets")
async def add_wallet(data: WalletIn, db: AsyncSession = Depends(get_db)):
    if not re.match(r"^0x[a-fA-F0-9]{40}$", data.address):
        raise HTTPException(400, "Invalid address format")
    existing = (await db.execute(select(Wallet).where(Wallet.address == data.address))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Wallet already exists")
    w = Wallet(address=data.address, label=data.label)
    db.add(w)
    await db.commit()
    return {"address": w.address, "label": w.label}

@router.get("/wallets")
async def get_wallets(db: AsyncSession = Depends(get_db)):
    wallets = (await db.execute(select(Wallet))).scalars().all()
    return [{"address": w.address, "label": w.label, "id": w.id} for w in wallets]

@router.delete("/wallets/{address}")
async def delete_wallet(address: str, db: AsyncSession = Depends(get_db)):
    w = (await db.execute(select(Wallet).where(Wallet.address == address))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Wallet not found")
    await db.delete(w)
    await db.commit()
    return {"deleted": address}

@router.get("/diag/bgeometrics-history-check")
async def diag_bgeometrics_history_check():
    """Temporary diagnostic endpoint. Tests whether the nupl and sopr
    BGeometrics endpoints support a limit parameter beyond 1, unlike the
    current _fetch_latest() usage which always requests limit=1. Uses
    exactly 2 BGeometrics API calls total (one per metric) to conserve
    the tight free-tier quota (8-10 req/hour, 15 req/day). Safe to
    remove once its job is done."""
    import httpx
    from services.bgeometrics import BGEOMETRICS_TOKEN, BASE_URL

    result = {}
    async with httpx.AsyncClient(timeout=15) as client:
        for metric, endpoint in [("nupl", "nupl"), ("sopr", "sopr")]:
            try:
                resp = await client.get(
                    f"{BASE_URL}/{endpoint}",
                    params={"token": BGEOMETRICS_TOKEN, "limit": 10}
                )
                data = resp.json()
                result[metric] = {
                    "status_code": resp.status_code,
                    "rows_returned": len(data) if isinstance(data, list) else None,
                    "raw_sample": data if isinstance(data, list) else data,
                }
            except Exception as e:
                result[metric] = {"error": str(e)}

    return result
