from datetime import datetime


class MockTransaction:
    def __init__(self, coin_id: str, quantity: float, cum_price: float, deposit: bool, timestamp: datetime):
        self.coin_id = coin_id
        self.quantity = quantity
        self.cum_price = cum_price
        self.deposit = deposit
        self.timestamp = timestamp
        self.applied = False
