from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Union

from sqlalchemy.orm import Session

from .binance_api_manager import BinanceAPIManager
from .config import Config
from .database import Database, LogScout
from .logger import Logger
from .models import Coin, CoinValue, Pair, Path, CoinPnl


class AutoTrader:
    def __init__(self, binance_manager: BinanceAPIManager, database: Database, logger: Logger, config: Config):
        self.manager = binance_manager
        self.db = database
        self.logger = logger
        self.config = config
        self.failed_buy_order = False
        self.failed_buy_path = None

    def initialize(self):
        self.initialize_trade_thresholds()

    def transaction_through_bridge(self, pair: Pair, sell_price: float, buy_price: float):
        """
        Jump from the source coin to the destination coin through bridge coin
        """
        can_sell = False
        balance = self.manager.get_currency_balance(pair.from_coin.symbol)

        if balance and balance * sell_price > self.manager.get_min_notional(
            pair.from_coin.symbol, self.config.BRIDGE.symbol
        ):
            can_sell = True
        else:
            self.logger.info("Skipping sell")

        if can_sell and self.manager.sell_alt(pair.from_coin, self.config.BRIDGE, sell_price) is None:
            self.logger.info("Couldn't sell, going back to scouting mode...")
            return None

        path = self.get_coin_path(pair.from_coin)

        # temporary force fail to test PNL
        self.logger.info(f"Log timestamp for testing: {self.manager.datetime}")
        if self.manager.datetime == datetime(2021, 10, 6, 16, 1, 0):
            self.logger.info("Fake failure")
            self.failed_buy_order = True
            self.failed_buy_path = path
            return None

        result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE, buy_price)

        if result is not None:
            price = self.get_price(result)
            is_merging = self.is_paths_merging(pair.to_coin)
            if is_merging:
                self.logger.info(f"Merging paths for {pair.from_coin} and {pair.to_coin}")
                path = self.merge_paths(pair.from_coin, pair.to_coin, result, price)
                self.logger.info(f"Merged paths into new path {path}")
            else:
                coin_pnl = self.set_coin_pnl(pair.to_coin, path, result.cumulative_filled_quantity, price)
                self.logger.info(f"New coin PNL: {coin_pnl.info()}")

            self.db.set_current_coin(pair.to_coin, path)

            self.update_trade_threshold(pair.to_coin, price)
            self.failed_buy_order = False
            self.failed_buy_path = None
            return result

        self.logger.info("Couldn't buy, going back to scouting mode...")
        self.failed_buy_order = True
        self.failed_buy_path = path
        return None

    def get_price(self, result):
        if abs(result.price) < 1e-15:
            return result.cumulative_quote_qty / result.cumulative_filled_quantity
        return result.price

    def set_coin_pnl(self, coin: Coin, path: Path, quantity: float, price: float) -> CoinPnl:
        coin_gain, percent_gain, total_coin_gain, total_percent_gain = 0.0, 0.0, 0.0, 0.0
        previous_coin_pnl = self.db.get_last_coin_pnl(coin, path)
        if previous_coin_pnl is not None:
            coin_gain = quantity - previous_coin_pnl.coin_amount
            percent_gain = coin_gain / previous_coin_pnl.coin_amount * 100
            total_coin_gain = previous_coin_pnl.total_coin_gain + coin_gain
            total_percent_gain = previous_coin_pnl.total_percent_gain + percent_gain

        return self.db.set_coin_pnl(path, coin, quantity, price, coin_gain, percent_gain, total_coin_gain, total_percent_gain)

    def set_deposited_coin_pnl(self, coin: Coin, path: Path, quantity: float, price: float) -> CoinPnl:
        coin_gain, percent_gain, total_coin_gain, total_percent_gain = 0.0, 0.0, 0.0, 0.0
        previous_coin_pnl = self.db.get_last_coin_pnl(coin, path)
        if previous_coin_pnl is not None:
            quantity = previous_coin_pnl.coin_amount + quantity
            coin_gain = previous_coin_pnl.coin_gain
            percent_gain = previous_coin_pnl.percent_gain
            total_coin_gain = previous_coin_pnl.total_coin_gain
            total_percent_gain = previous_coin_pnl.total_percent_gain

        return self.db.set_coin_pnl(path, coin, quantity, price, coin_gain, percent_gain, total_coin_gain, total_percent_gain)

    def merge_coin_pnl(self, to_coin: Coin, path_a: Path, path_b: Path, new_path: Path, quantity: float, price: float) -> CoinPnl:
        coin_gain, percent_gain, total_coin_gain, total_percent_gain = 0.0, 0.0, 0.0, 0.0
        old_path_coin_pnl = self.db.get_last_coin_pnl(to_coin, path_a)
        new_path_coin_pnl = self.db.get_last_coin_pnl(to_coin, path_b)
        self.logger.info(f"Will attempt to merge coin paths for coin PNL")
        if old_path_coin_pnl is not None and new_path_coin_pnl is not None:
            self.logger.info(f"Merging coin PNL {old_path_coin_pnl.info()} into {new_path_coin_pnl.info()}")
            cum_old_quantity = old_path_coin_pnl.coin_amount + new_path_coin_pnl.coin_amount
            coin_gain = quantity - cum_old_quantity
            percent_gain = coin_gain / cum_old_quantity * 100
            total_coin_gain = new_path_coin_pnl.total_coin_gain + coin_gain
            total_percent_gain = new_path_coin_pnl.total_percent_gain + percent_gain

        return self.db.set_coin_pnl(new_path, to_coin, quantity, price, coin_gain, percent_gain, total_coin_gain, total_percent_gain)

    def get_path(self, path: Union[Coin, Path]):
        if isinstance(path, Path):
            return path
        return self.db.get_coin_path(path)

    def merge_paths(self, merge_from: Union[Coin, Path], merge_to: Union[Coin, Path], result, price) -> Optional[Path]:
        path_a = self.get_path(merge_from)
        path_b = self.get_path(merge_to)
        new_path = self.db.set_new_coin_path()
        if path_a is None or path_b is None:
            self.logger.error("Failed to merge paths. Path ID to merge could not be fetched from DB.")
            return None
        coin_pnl = self.merge_coin_pnl(merge_to, path_a, path_b, new_path, result.cumulative_filled_quantity, price)
        self.logger.info(f"Merged coin PNL: {coin_pnl.info()}")
        self.db.deactivate_path(path_a)
        self.db.deactivate_path(path_b)
        return new_path

    def is_paths_merging(self, to_coin: Coin):
        active_coins = self.db.get_active_coins()
        return to_coin.symbol in active_coins

    def get_coin_path(self, from_coin: Coin) -> Path:
        path = self.db.get_coin_path(from_coin)
        # Check if we need to create new path
        if path is None:
            return self.db.set_new_coin_path()
        return path

    def update_trade_threshold(self, coin: Coin, coin_price: float):
        """
        Update all the coins with the threshold of buying the current held coin
        """

        if coin_price is None or coin_price == 0.0:
            self.logger.info("Skipping update... current coin {} not found".format(coin + self.config.BRIDGE))
            return

        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.to_coin == coin):
                from_coin_price = self.manager.get_sell_price(pair.from_coin + self.config.BRIDGE)

                if from_coin_price is None:
                    self.logger.info(
                        "Skipping update for coin {} not found".format(pair.from_coin + self.config.BRIDGE)
                    )
                    continue

                pair.ratio = from_coin_price / coin_price

    def initialize_trade_thresholds(self):
        """
        Initialize the buying threshold of all the coins for trading between them
        """
        session: Session
        with self.db.db_session() as session:
            pairs = session.query(Pair).filter(Pair.ratio.is_(None)).all()
            grouped_pairs = defaultdict(list)
            for pair in pairs:
                if pair.from_coin.enabled and pair.to_coin.enabled:
                    grouped_pairs[pair.from_coin.symbol].append(pair)
            for from_coin_symbol, group in grouped_pairs.items():
                self.logger.info(f"Initializing {from_coin_symbol} vs [{', '.join([p.to_coin.symbol for p in group])}]")
                for pair in group:
                    from_coin_price = self.manager.get_sell_price(pair.from_coin + self.config.BRIDGE)
                    if from_coin_price is None:
                        self.logger.info(
                            "Skipping initializing {}, symbol not found".format(pair.from_coin + self.config.BRIDGE)
                        )
                        continue

                    to_coin_price = self.manager.get_buy_price(pair.to_coin + self.config.BRIDGE)
                    if to_coin_price is None or to_coin_price == 0.0:
                        self.logger.info(
                            "Skipping initializing {}, symbol not found".format(pair.to_coin + self.config.BRIDGE)
                        )
                        continue

                    pair.ratio = from_coin_price / to_coin_price

    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """
        raise NotImplementedError()

    def _get_ratios(self, coin: Coin, coin_price, excluded_coins: List[Coin] = []):
        """
        Given a coin, get the current price ratio for every other enabled coin
        """
        ratio_dict: Dict[Pair, float] = {}
        prices: Dict[str, float] = {}

        scout_logs = []
        excluded_coin_symbols = [c.symbol for c in excluded_coins]
        for pair in self.db.get_pairs_from(coin):
            #skip excluded coins
            if pair.to_coin.symbol in excluded_coin_symbols:
                continue

            optional_coin_price = self.manager.get_buy_price(pair.to_coin + self.config.BRIDGE)
            prices[pair.to_coin_id] = optional_coin_price

            if optional_coin_price is None or optional_coin_price == 0.0:
                self.logger.info(
                    "Skipping scouting... optional coin {} not found".format(pair.to_coin + self.config.BRIDGE)
                )
                continue

            scout_logs.append(LogScout(pair, pair.ratio, coin_price, optional_coin_price))

            # Obtain (current coin)/(optional coin)
            coin_opt_coin_ratio = coin_price / optional_coin_price

            from_fee = self.manager.get_fee(pair.from_coin, self.config.BRIDGE, True)
            to_fee =  self.manager.get_fee(pair.to_coin, self.config.BRIDGE, False)

            if self.config.RATIO_CALC == self.config.RATIO_CALC_DEFAULT:
                transaction_fee = from_fee + to_fee

                ratio_dict[pair] = (
                    coin_opt_coin_ratio - transaction_fee * self.config.SCOUT_MULTIPLIER * coin_opt_coin_ratio
                ) - pair.ratio
            if self.config.RATIO_CALC == self.config.RATIO_CALC_SCOUT_MARGIN:
                transaction_fee = from_fee + to_fee - from_fee * to_fee

                ratio_dict[pair] = (1 - transaction_fee) * coin_opt_coin_ratio / pair.ratio - (1 + self.config.SCOUT_MULTIPLIER / 100)
        self.db.batch_log_scout(scout_logs)
        return (ratio_dict, prices)

    def _jump_to_best_coin(self, coin: Coin, coin_price: float, excluded_coins: List[Coin] = []):
        """
        Given a coin, search for a coin to jump to
        """
        ratio_dict, prices = self._get_ratios(coin, coin_price, excluded_coins)

        # keep only ratios bigger than zero
        ratio_dict = {k: v for k, v in ratio_dict.items() if v > 0}

        # if we have any viable options, pick the one with the biggest ratio
        if ratio_dict:
            best_pair = max(ratio_dict, key=ratio_dict.get)
            self.logger.info(f"Will be jumping from {coin} to {best_pair.to_coin_id}")
            self.transaction_through_bridge(best_pair, coin_price, prices[best_pair.to_coin_id])

    def bridge_scout(self):
        """
        If we have any bridge coin leftover, buy a coin with it that we won't immediately trade out of
        """
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)

        for coin in self.db.get_coins():
            current_coin_price = self.manager.get_sell_price(coin + self.config.BRIDGE)

            if current_coin_price is None:
                continue

            ratio_dict, _ = self._get_ratios(coin, current_coin_price)
            if not any(v > 0 for v in ratio_dict.values()):
                # There will only be one coin where all the ratios are negative. When we find it, buy it if we can
                if bridge_balance > self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol):
                    self.logger.info(f"Will be purchasing {coin} using bridge coin")
                    result = self.manager.buy_alt(
                        coin, self.config.BRIDGE, self.manager.get_sell_price(coin + self.config.BRIDGE)
                    )
                    if result is not None:
                        self.db.set_current_coin(coin)
                        self.failed_buy_order = False
                        return coin
                    else:
                        self.failed_buy_order = True
        return None

    def update_values(self):
        """
        Log current value state of all altcoin balances against BTC and USDT in DB.
        """
        now = datetime.now()

        coins = self.db.get_coins(True)
        cv_batch = []
        for coin in coins:
            balance = self.manager.get_currency_balance(coin.symbol)
            if balance == 0:
                continue
            usd_value = self.manager.get_ticker_price(coin + self.config.BRIDGE_SYMBOL)
            btc_value = self.manager.get_ticker_price(coin + "BTC")
            cv = CoinValue(coin, balance, usd_value, btc_value, datetime=now)
            cv_batch.append(cv)
        self.db.batch_update_coin_values(cv_batch)

    def handle_manual_transactions(self, transactions: List[Dict], exchange_active_coins=None, bot_active_coins=None):
        if exchange_active_coins is None:
            exchange_active_coins = []
        if bot_active_coins is None:
            bot_active_coins = []

        # Coins deposited on exchange but not yet activated on the bot
        new_coins = [x for x in exchange_active_coins if x not in bot_active_coins]

        # Coins withdrawn on exchange not yet deactivated on the bot
        removed_coins = [x for x in bot_active_coins if x not in exchange_active_coins]

        for transaction in transactions:
            print(f"Handling transaction: {transaction}")
            coin = Coin(transaction.get("symbol"))
            tx_quantity = float(transaction.get("cum_quantity"))
            tx_coin_price = float(transaction.get("coin_price"))
            deposit = bool(transaction.get("deposit"))
            self.db.set_manual_transaction(coin, tx_quantity, tx_coin_price, deposit)

            if coin.symbol in new_coins and deposit:
                # Activate coin on bot and create new PNL records
                new_path = self.db.set_new_coin_path()
                if new_path is None:
                    self.logger.warning(f"Failed to activate new path for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                self.logger.info(f"Activating new path {new_path.id} for deposited amount {tx_quantity} on {coin}")
                self.db.set_current_coin(coin, new_path)
                new_pnl = self.set_deposited_coin_pnl(coin, new_path, tx_quantity, tx_coin_price)
                self.logger.info(f"Successfully updated coin PNL quantity to {new_pnl.coin_amount} for coin {new_pnl.coin.symbol} on path {new_pnl.path.id}")
            elif coin.symbol in removed_coins and not deposit:
                # Deactivate coin on bot
                old_path = self.db.get_coin_path(coin)
                if old_path is None:
                    self.logger.warning(f"Failed to retrieve old path from DB for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                self.logger.info(f"Deactivating old path {old_path.id} for deposited amount {tx_quantity} on {coin}")
                self.db.deactivate_path(old_path)
            elif coin.symbol in exchange_active_coins and coin.symbol in bot_active_coins:
                # Deposit or withdrawal on already active coin so we just need to adjust PNLs to new quantity
                quantity = tx_quantity if deposit else - tx_quantity
                coin_path = self.db.get_coin_path(coin)
                if coin_path is None:
                    self.logger.warning(f"Failed to retrieve path from DB when attempting to update Coin PNL for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                updated_pnl = self.set_deposited_coin_pnl(coin, coin_path, quantity, tx_coin_price)
                if updated_pnl is None:
                    self.logger.warning(f"Failed to update PNL record for deposit/withdrawal. Coin: {coin.symbol} Path: {coin_path.id}")
                    continue
                self.logger.info(f"Successfully updated coin PNL quantity to {updated_pnl.coin_amount} for coin {updated_pnl.coin.symbol} on path {updated_pnl.path.id}")
