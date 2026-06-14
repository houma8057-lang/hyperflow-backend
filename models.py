from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String, nullable=False, unique=True)
    label = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=func.now())

class WSIHistory(Base):
    __tablename__ = "wsi_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=func.now())
    wsi_value = Column(Float, nullable=False)
    total_long_ntl = Column(Float, nullable=False)
    total_short_ntl = Column(Float, nullable=False)
    wallet_count = Column(Integer, nullable=False)
    reversal_score = Column(Float, nullable=True)

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
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=func.now())
    signal = Column(String, nullable=False)
    btc_price = Column(Float, nullable=False)
    wsi = Column(Float, nullable=False)
    funding = Column(Float, nullable=False)
    whale_short = Column(Float, nullable=False)
    buy_conditions_met = Column(Integer, nullable=False)
    sell_conditions_met = Column(Integer, nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
