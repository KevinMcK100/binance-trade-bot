from datetime import datetime

from binance_trade_bot import backtest
from binance_trade_bot.mock_transaction import MockTransaction


if __name__ == "__main__":
    history = []
    start_time = datetime(2021, 10, 6, 11, 0)
    end_time = datetime(2021, 10, 7, 23, 30)
    print(f"BACKTEST from {start_time} to {end_time}")
    deposit = MockTransaction("COMP", 10, 3000, True, datetime(2021, 10, 7, 18, 0))
    withdraw = MockTransaction("BCH", 3.2607, 2235.3305, False, datetime(2021, 10, 7, 21, 0))
    transactions = [deposit, withdraw]
    current_date = start_time.strftime("%d/%m/%Y")
    start_balances = {"BCH": 500, "COMP": 100}
    for manager in backtest(start_time, end_time, start_balances=start_balances, transactions=transactions):
        btc_value = manager.collate_coins("BTC")
        bridge_value = manager.collate_coins(manager.config.BRIDGE.symbol)
        btc_fees_value = manager.collate_fees("BTC")
        bridge_fees_value = manager.collate_fees(manager.config.BRIDGE.symbol)
        trades = manager.trades
        history.append((btc_value, bridge_value, trades, btc_fees_value, bridge_fees_value))
        btc_diff = round((btc_value - history[0][0]) / history[0][0] * 100, 3)
        bridge_diff = round((bridge_value - history[0][1]) / history[0][1] * 100, 3)
        if manager.datetime.strftime("%d/%m/%Y") != current_date:
            current_date = manager.datetime.strftime("%d/%m/%Y")
            print("------")
            print("TIME:", manager.datetime)
            print("TRADES:", trades)
            #print("PAID FEES:", manager.paid_fees)
            #print("BTC FEES VALUE:", btc_fees_value)
            print(f"{manager.config.BRIDGE.symbol} FEES VALUE:", bridge_fees_value)
            #print("BALANCES:", manager.balances)
            print("BTC VALUE:", btc_value, f"({btc_diff}%)")
            print(f"{manager.config.BRIDGE.symbol} VALUE:", bridge_value, f"({bridge_diff}%)")
            print("------")
    print("------")
    print("TIME:", manager.datetime)
    print("TRADES:", trades)
    print("POSITIVE COIN JUMPS:", manager.positve_coin_jumps)
    print("NEVATIVE COIN JUMPS:", manager.negative_coin_jumps)
    #print("PAID FEES:", manager.paid_fees)
    #print("BTC FEES VALUE:", btc_fees_value)
    print(f"{manager.config.BRIDGE.symbol} FEES VALUE:", bridge_fees_value)
    #print("BALANCES:", manager.balances)
    print("BTC VALUE:", btc_value, f"({btc_diff}%)")
    print(f"{manager.config.BRIDGE.symbol} VALUE:", bridge_value, f"({bridge_diff}%)")
    print("------")
