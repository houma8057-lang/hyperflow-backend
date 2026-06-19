from sqlalchemy import Column, Integer, Float, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class WSIHistory(Base):
    __tablename__ = "wsi_history"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    wsi_value = Column(Float, nullable=False)
    wallet_count = Column(Integer, nullable=True)

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False, default="")

class PositionSnapshot(Base):
    __tablename__ = "positions_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)
    coin = Column(String, nullable=False)
    side = Column(String, nullable=False)
    szi = Column(Float, nullable=False)
    entry_px = Column(Float, nullable=False)
    notional = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    leverage = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=func.now())

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=func.now())
    reversal_score = Column(Float, nullable=False)
    signal_type = Column(String, nullable=False)
    wsi_at_trigger = Column(Float, nullable=False)
    delta_wsi_24h = Column(Float, nullable=False)
    oi_divergence = Column(Float, nullable=False)
    dry_powder = Column(Float, nullable=False)

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True)
    alert_threshold = Column(Float, nullable=False, default=0.60)
    polling_interval = Column(Integer, nullable=False, default=10)
    history_days = Column(Integer, nullable=False, default=30)

class OIHistory(Base):
    __tablename__ = "oi_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=func.now())
    coin = Column(String, nullable=False)
    open_interest_usd = Column(Float, nullable=False)
    mark_price = Column(Float, nullable=False)

class SignalHistory(Base):
    __tablename__ = "signal_history"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    signal = Column(String, nullable=False)
    btc_price = Column(Float, nullable=True)
    wsi = Column(Float, nullable=True)
    funding = Column(Float, nullable=True)
    whale_short = Column(Float, nullable=True)
    whale_long = Column(Float, nullable=True)
    regime_score = Column(Float, nullable=True)
    buy_conditions_met = Column(Integer, nullable=True)
    sell_conditions_met = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)

class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id = Column(Integer, primary_key=True, index=True)
    version = Column(Integer, nullable=False, default=0)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())
