from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Float, String
from sqlalchemy.orm import relationship

from .base import Base
from .path import Path


class UsdPnl(Base):  # pylint: disable=too-few-public-methods
    """
    Records USD PNL.
    """

    __tablename__ = "usd_pnl"

    id = Column(Integer, primary_key=True)
    path_id = Column(String, ForeignKey("paths.id"))
    path = relationship("Path", foreign_keys=[path_id], lazy="joined")
    value = Column(Float)
    gain = Column(Float)
    percent_gain = Column(Float)
    total_gain = Column(Float)
    total_percent_gain = Column(Float)
    datetime = Column(DateTime)

    def __init__(self, path: Path, value: float, gain: float, percent_gain: float, total_gain: float, total_percent_gain: float):
        self.path = path
        self.value = value
        self.gain = gain
        self.percent_gain = percent_gain
        self.total_gain = total_gain
        self.total_percent_gain = total_percent_gain
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "id": self.id,
            "path": self.path.info(),
            "value": self.value,
            "gain": self.gain,
            "percent_gain": self.percent_gain,
            "total_gain": self.total_gain,
            "total_percent_gain": self.total_percent_gain,
            "datetime": self.datetime,
        }
