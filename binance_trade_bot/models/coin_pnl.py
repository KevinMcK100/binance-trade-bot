from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Float, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin
from .path import Path


class CoinPnl(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "coin_pnl"

    id = Column(Integer, primary_key=True)
    path_id = Column(Integer, ForeignKey("paths.id"))
    path = relationship("Path", foreign_keys=[path_id], lazy="joined")
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin", foreign_keys=[coin_id], lazy="joined")
    coin_amount = Column(Float)
    coin_price = Column(Float)
    usd_value = Column(Float)
    coin_gain = Column(Float)
    percent_gain = Column(Float)
    total_coin_gain = Column(Float)
    total_percent_gain = Column(Float)
    datetime = Column(DateTime)

    def __init__(self, path: Path, coin: Coin, coin_amount: float, coin_price: float, coin_gain: float, percent_gain: float, total_coin_gain: float, total_percent_gain: float):
        self.path = path
        self.coin = coin
        self.coin_amount = coin_amount
        self.coin_price = coin_price
        self.usd_value = round(coin_amount * coin_price, 2)
        self.coin_gain = coin_gain
        self.percent_gain = round(percent_gain, 2)
        self.total_coin_gain = total_coin_gain
        self.total_percent_gain = round(total_percent_gain, 2)
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "path": self.path.info(),
            "coin": self.coin.info(),
            "coin_amount": self.coin_amount,
            "coin_price": self.coin_price,
            "usd_value": self.usd_value,
            "coin_gain": self.coin_gain,
            "percent_gain": self.percent_gain,
            "total_coin_gain": self.total_coin_gain,
            "total_percent_gain": self.total_percent_gain,
            "datetime": self.datetime,
        }

