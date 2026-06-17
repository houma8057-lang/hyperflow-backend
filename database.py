import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./hyperflow.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

connect_args = {}
if "asyncpg" in DATABASE_URL:
    connect_args = {"statement_cache_size": 0}

engine = create_async_engine(DATABASE_URL, echo=False, connect_args=connect_args)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def init_db():
    from models import Wallet, WSIHistory, PositionSnapshot, Alert, SystemSettings, SignalHistory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Migration: add new columns if they don't exist
        if "asyncpg" in DATABASE_URL:
            # Check and add whale_long
            result = await conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'signal_history' AND column_name = 'whale_long'
            """))
            if not result.scalar():
                await conn.execute(text("""
                    ALTER TABLE signal_history ADD COLUMN whale_long FLOAT DEFAULT 0.0
                """))
            
            # Check and add regime_data
            result = await conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'signal_history' AND column_name = 'regime_data'
            """))
            if not result.scalar():
                await conn.execute(text("""
                    ALTER TABLE signal_history ADD COLUMN regime_data TEXT
                """))

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
