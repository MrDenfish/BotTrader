# TableModels/active_symbols.py
from sqlalchemy import Column, String, Integer, DateTime, Boolean, Float
from TableModels.base import Base

class ActiveSymbol(Base):
    __tablename__ = "active_symbols"

    symbol = Column(String, primary_key=True)
    as_of = Column(DateTime(timezone=True), nullable=False)
    window_hours = Column(Integer, nullable=False, default=24)
    n = Column(Integer, nullable=False)
    wins = Column(Integer, nullable=False)
    losses = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)
    mean_pnl = Column(Float, nullable=False)
    gross_profit = Column(Float, nullable=False)
    gross_loss = Column(Float, nullable=False)
    profit_factor = Column(Float)       # nullable when no losses
    score = Column(Float)               # composite ranking metric
    eligible = Column(Boolean, nullable=False)
