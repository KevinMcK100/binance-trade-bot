from datetime import datetime


class MockTransaction:
    """
    Class used to "fake" a manual transaction made on Binance by a user. Only used by the dev_backtest for the purposes
    of testing the code during development.
    """
    def __init__(self, coin_id: str, quantity: float, cum_price: float, deposit: bool, timestamp: datetime):
        self.coin_id = coin_id
        self.quantity = quantity
        self.cum_price = cum_price
        self.deposit = deposit
        self.timestamp = timestamp
        self.applied = False

    def info(self):
        return {
            "coin_id": self.coin_id,
            "quantity": self.quantity,
            "cum_price": self.cum_price,
            "deposit": self.deposit,
            "timestamp": self.timestamp,
            "applied": self.applied,
        }
