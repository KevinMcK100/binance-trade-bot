from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin
from .path import Path


class CurrentCoin(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "current_coin_history"
    id = Column(Integer, primary_key=True)
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin")
    path_id = Column(Integer, ForeignKey("paths.id"))
    path = relationship("Path", foreign_keys=[path_id], lazy="joined")
    datetime = Column(DateTime)

    def __init__(self, coin: Coin, path: Path):
        self.coin = coin
        self.path = path
        self.datetime = datetime.utcnow()

    def info(self):
        return {"datetime": self.datetime.isoformat(), "coin": self.coin.info(), "path": self.path.info()}
