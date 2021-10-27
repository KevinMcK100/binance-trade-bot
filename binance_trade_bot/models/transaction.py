from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin


class Transaction(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "transaction_history"

    id = Column(Integer, primary_key=True)
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin", foreign_keys=[coin_id], lazy="joined")
    coin_amount = Column(Float)
    usd_price = Column(Float)
    usd_amount = Column(Float)
    deposit = Column(Boolean)
    datetime = Column(DateTime)

    def __init__(self, coin: Coin, coin_amount: float, usd_price: float, deposit: bool):
        self.coin = coin
        self.coin_amount = coin_amount
        self.usd_price = usd_price
        self.usd_amount = coin_amount * usd_price
        self.deposit = deposit
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "coin": self.coin.info(),
            "coin_amount": self.usd_price,
            "usd_price": self.deposit,
            "usd_amount": self.usd_amount,
            "datetime": self.datetime,
        }
