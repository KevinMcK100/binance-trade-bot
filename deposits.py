import getopt
import os
import sys
from datetime import datetime as _datetime

from sqlalchemy import Float

from binance_trade_bot import deposit

def OK():
    if os.name == 'nt':
        return 0
    return os.EX_OK


def validate_datetime(date_str) -> _datetime:
    try:
        return _datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        raise ValueError("Incorrect datetime format. Must match ISO8601 format YYYY-MM-DDThh:mm:ss")


if __name__ == "__main__":
    db_path = "data/crypto_trading.db"
    found_amount = False
    amount = 0.0
    datetime = _datetime.now()

    try:
        opts, args = getopt.getopt(sys.argv[1:], "ha:d:p:", ["amount=", "datetime=", "dbpath="])
    except getopt.GetoptError:
        pass
    for opt, arg in opts:
        if opt == '-h':
            print('deposits.py - Script to add USD deposit amounts with a timestamp')
            print('parameters:')
            print('-a, --amount <required, USD amount deposited>')
            print('-d, --datetime <optional, deposit date. Must match ISO8601 format YYYY-MM-DDThh:mm:ss. Defaults to NOW if not provided>')
            print('-p, --dbpath <optional, path to db, if not given the default db path will be used>')
            os._exit(OK())
        elif opt in ("-a", "--amount"):
            found_amount = True
            amount = arg
        elif opt in ("-d", "--datetime"):
            datetime = validate_datetime(arg)
            print("TYPE: ", type(datetime))
        elif opt in ("-p", "--dbpath"):
            db_path = arg
    if not found_amount:
        raise ValueError("amount is a required field")

    deposit(amount, datetime, db_path)
    os._exit(OK())

