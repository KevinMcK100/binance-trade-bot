from datetime import datetime as _datetime

from sqlalchemy import Float

from .config import Config
from .database import Database
from .logger import Logger


def deposit(deposit_amount: Float, datetime: _datetime, db_path="data/crypto_trading.db", config: Config = None):
    logger = Logger()
    logger.info("Starting add deposit")

    logger.info(f'Will be using {db_path} as database')
    dbPathUri = f"sqlite:///{db_path}"

    config = config or Config()
    db = Database(logger, config, dbPathUri)

    logger.info("Creating database schema if it doesn't already exist")
    db.create_database()
    logger.info("Done creating database schema")

    db.set_deposit(deposit_amount, datetime)

    logger.info(f"Done. Added new deposit amount of ${deposit_amount} on {datetime}")
