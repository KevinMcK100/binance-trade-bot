from datetime import datetime as _datetime

from sqlalchemy import Column, DateTime, Integer, Float

from .base import Base


class Deposit(Base):
    __tablename__ = "deposits"
    id = Column(Integer, primary_key=True)
    usd_amount = Column(Float)
    datetime = Column(DateTime)

    def __init__(self, usd_amount: Float, datetime: _datetime = None):
        self.usd_amount = usd_amount
        self.datetime = datetime or _datetime.now()

    def info(self):
        return {
            "usd_amount": self.usd_amount,
            "datetime": self.datetime.isoformat(),
        }
