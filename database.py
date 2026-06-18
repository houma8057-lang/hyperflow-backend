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
        
        # Migration: run once only using schema_version tracking
        if "asyncpg" in DATABASE_URL:
            # Check if schema_version table exists
            result = await conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'schema_version'
                )
            """))
            has_version_table = result.scalar()
            
            if not has_version_table:
                await conn.execute(text("""
                    CREATE TABLE schema_version (
                        id INTEGER PRIMARY KEY,
                        version INTEGER NOT NULL DEFAULT 0,
                        applied_at TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 0)"))
            
            # Check current version
            result = await conn.execute(text("SELECT version FROM schema_version WHERE id = 1"))
            current_version = result.scalar() or 0
            
            # Migration v1: add whale_long and regime_data
            if current_version < 1:
                # Check if columns exist before adding
                for col_name in ['whale_long', 'regime_data']:
                    result = await conn.execute(text(f"""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name = 'signal_history' AND column_name = '{col_name}'
                    """))
                    if not result.scalar():
                        if col_name == 'whale_long':
                            await conn.execute(text("""
                                ALTER TABLE signal_history ADD COLUMN whale_long FLOAT
                            """))
                        else:
                            await conn.execute(text("""
                                ALTER TABLE signal_history ADD COLUMN regime_data TEXT
                            """))
                
                # Update version
                await conn.execute(text("UPDATE schema_version SET version = 1 WHERE id = 1"))
                print("Migration v1 applied: added whale_long and regime_data")

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
