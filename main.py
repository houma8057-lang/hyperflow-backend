from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routers import wallets, sentiment, positions, liquidity, settings, signals
from tasks.scheduler import start_scheduler
from tasks.keepalive import start_keepalive

app = FastAPI(title="HyperFlow API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(wallets.router, prefix="/api")
app.include_router(sentiment.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(liquidity.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(signals.router, prefix="/api")

@app.on_event("startup")
async def startup():
    await init_db()
    start_scheduler()
    start_keepalive()

@app.get("/")
def root():
    return {"status": "HyperFlow API running"}

@app.get("/ping")
def ping():
    return {"status": "alive", "service": "HyperFlow Backend"}
