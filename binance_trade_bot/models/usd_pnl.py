from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Float, String
from sqlalchemy.orm import relationship

from .base import Base
from .path import Path


class UsdPnl(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "usd_pnl"

    id = Column(Integer, primary_key=True)
    path_id = Column(String, ForeignKey("paths.id"))
    path = relationship("Path", foreign_keys=[path_id], lazy="joined")
    usd_value = Column(Float)
    usd_gain = Column(Float)
    percent_gain = Column(Float)
    total_usd_gain = Column(Float)
    total_percent_gain = Column(Float)
    datetime = Column(DateTime)

    def __init__(self, path: Path, usd_value: float, usd_gain: float, percent_gain: float, total_usd_gain: float, total_percent_gain: float):
        self.path = path
        self.usd_value = usd_value
        self.usd_gain = usd_gain
        self.percent_gain = percent_gain
        self.total_usd_gain = total_usd_gain
        self.total_percent_gain = total_percent_gain
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "path": self.path.info(),
            "usd_value": self.usd_value,
            "usd_gain": self.usd_gain,
            "percent_gain": self.percent_gain,
            "total_usd_gain": self.total_usd_gain,
            "total_percent_gain": self.total_percent_gain,
            "datetime": self.datetime,
        }
