from sqlalchemy import Column, Integer, Float, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class WSIHistory(Base):
    __tablename__ = "wsi_history"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    wsi_value = Column(Float, nullable=False)
    wallet_count = Column(Integer, nullable=True)  # For roster-change detection
    # Future: add per-wallet breakdown as JSON

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())

class SignalHistory(Base):
    __tablename__ = "signal_history"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    signal = Column(String, nullable=False)
    btc_price = Column(Float, nullable=True)
    wsi = Column(Float, nullable=True)
    funding = Column(Float, nullable=True)
    whale_short = Column(Float, nullable=True)  # NULL = pre-migration
    whale_long = Column(Float, nullable=True)     # NULL = pre-migration
    regime_score = Column(Float, nullable=True)  # NULL = pre-migration, composite score [-1, 1]
    buy_conditions_met = Column(Integer, nullable=True)
    sell_conditions_met = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)

class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id = Column(Integer, primary_key=True, index=True)
    version = Column(Integer, nullable=False, default=0)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())
