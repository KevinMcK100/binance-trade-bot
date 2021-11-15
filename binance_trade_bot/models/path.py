from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer

from .base import Base


class Path(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "paths"

    id = Column(Integer, primary_key=True)
    active = Column(Boolean)
    datetime = Column(DateTime)

    def __init__(self, active: bool):
        self.active = active
        self.datetime = datetime.utcnow()

    def __repr__(self):
        return f"<{self.id}, {self.active}>"

    def info(self):
        return {
            "id": self.id,
            "active": self.active,
            "datetime": self.datetime,
        }
