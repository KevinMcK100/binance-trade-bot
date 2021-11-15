from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin


class Transaction(Base):  # pylint: disable=too-few-public-methods
    """
    Model to hold transactions manually executed by the user on Binance.
    """

    __tablename__ = "transaction_history"

    id = Column(Integer, primary_key=True)
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin", foreign_keys=[coin_id], lazy="joined")
    coin_amount = Column(Float)
    bridge_price = Column(Float)
    bridge_amount = Column(Float)
    deposit = Column(Boolean)
    datetime = Column(DateTime)

    def __init__(self, coin: Coin, coin_amount: float, bridge_price: float, deposit: bool):
        self.coin = coin
        self.coin_amount = coin_amount
        self.bridge_price = bridge_price
        self.bridge_amount = coin_amount * bridge_price
        self.deposit = deposit
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "coin": self.coin.info(),
            "coin_amount": self.bridge_price,
            "bridge_price": self.deposit,
            "bridge_amount": self.bridge_amount,
            "datetime": self.datetime,
        }
