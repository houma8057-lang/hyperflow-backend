from fastapi import APIRouter
from services.defillama import get_dry_powder

router = APIRouter()

@router.get("/liquidity")
async def get_liquidity():
    return await get_dry_powder()
