from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Float, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin
from .path import Path


class CoinPnl(Base):  # pylint: disable=too-few-public-methods
    """
    Records coin PNL.
    """

    __tablename__ = "coin_pnl"

    id = Column(Integer, primary_key=True)
    path_id = Column(Integer, ForeignKey("paths.id"))
    path = relationship("Path", foreign_keys=[path_id], lazy="joined")
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin", foreign_keys=[coin_id], lazy="joined")
    coin_amount = Column(Float)
    coin_price = Column(Float)
    bridge_value = Column(Float)
    coin_gain = Column(Float)
    percent_gain = Column(Float)
    total_coin_gain = Column(Float)
    total_percent_gain = Column(Float)
    datetime = Column(DateTime)

    def __init__(self, path: Path, coin: Coin, coin_amount: float, coin_price: float, coin_gain: float, percent_gain: float,
                 total_coin_gain: float, total_percent_gain: float, base_asset_precision: int, bridge_precision: int):
        self.path = path
        self.coin = coin
        self.coin_amount = round(coin_amount, base_asset_precision)
        self.coin_price = round(coin_price, bridge_precision)
        self.bridge_value = round(coin_amount * coin_price, bridge_precision)
        self.coin_gain = round(coin_gain, base_asset_precision)
        self.percent_gain = round(percent_gain, 2)
        self.total_coin_gain = round(total_coin_gain, base_asset_precision)
        self.total_percent_gain = round(total_percent_gain, 2)
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "path": self.path.info(),
            "coin": self.coin.info(),
            "coin_amount": self.coin_amount,
            "coin_price": self.coin_price,
            "bridge_value": self.bridge_value,
            "coin_gain": self.coin_gain,
            "percent_gain": self.percent_gain,
            "total_coin_gain": self.total_coin_gain,
            "total_percent_gain": self.total_percent_gain,
            "datetime": self.datetime,
        }
