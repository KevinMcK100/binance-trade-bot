from collections import defaultdict
from datetime import datetime
from math import log
from typing import Dict, List, Optional, Union

from sqlalchemy.orm import Session

from .binance_api_manager import BinanceAPIManager
from .config import Config
from .database import Database, LogScout, Transaction
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

        min_notional = self.manager.get_min_notional(pair.from_coin.symbol, self.config.BRIDGE.symbol)
        if balance and balance * sell_price > min_notional:
            can_sell = True
        else:
            self.logger.info("Skipping sell")

        # If there is some bridge coin present before jump, assume it's a manual deposit.
        bridge_deposit = False
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)
        if can_sell and bridge_balance > min_notional:
            bridge_deposit = True

        if can_sell and self.manager.sell_alt(pair.from_coin, self.config.BRIDGE, sell_price) is None:
            self.logger.info("Couldn't sell, going back to scouting mode...")
            return None

        path = self.get_coin_path(pair.from_coin)

        if bridge_deposit:
            self.logger.info(
                f"Detected bridge coin deposit of {bridge_balance} {self.config.BRIDGE.symbol}. Updating PNL records.")
            # self.db.set_manual_transaction(self.config.BRIDGE.symbol, bridge_balance, 1.0, True)
            self.set_deposited_coin_pnl_existing_path(pair.from_coin, path, bridge_balance, 1.0)
            self.update_usd_pnl(self.config.BRIDGE.symbol, path, bridge_balance)

        result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE, buy_price)

        if result is not None:
            price = self.get_price(result)
            is_merging = self.is_paths_merging(pair.to_coin)
            if is_merging:
                self.logger.info(f"Merging paths for {pair.from_coin} and {pair.to_coin}")
                path = self.merge_paths(pair.from_coin, pair.to_coin, result, price)
                self.logger.info(f"Merged paths into new path {path}")
            else:
                coin_amt = self.manager.get_currency_balance(pair.to_coin.symbol)
                coin_pnl = self.update_coin_pnl(pair.to_coin, path, coin_amt, price)
                self.logger.info(f"New coin PNL: {coin_pnl.info()}")

            self.db.set_current_coin(pair.to_coin, path)
            self.update_trade_threshold(pair.to_coin, price)
            self.update_all_usd_pnl()
            self.failed_buy_order = False
            self.failed_buy_path = None
            return result

        self.logger.info("Couldn't buy, going back to scouting mode...")
        self.failed_buy_order = True
        self.failed_buy_path = path
        return None

    @staticmethod
    def get_price(result):
        """
        Get the coin price from the BinanceOrder result object

        :param result: BinanceOrder result object
        :return: price paid for coin in this order
        """
        if abs(result.price) < 1e-15:
            return result.cumulative_quote_qty / result.cumulative_filled_quantity
        return result.price

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
                
                # check if we hold above min_notional coins of from_coin. If so skip ratio update.
                from_coin_balance = self.manager.get_currency_balance(pair.from_coin.symbol)          
                min_notional = self.manager.get_min_notional(pair.from_coin.symbol, self.config.BRIDGE.symbol)
                if from_coin_price * from_coin_balance > min_notional:
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
            to_fee = self.manager.get_fee(pair.to_coin, self.config.BRIDGE, False)

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

    ################################
    # Handling manual transactions #
    ################################

    def handle_manual_transactions(self, transactions: List[Transaction], exchange_active_coins=None, bot_active_coins=None):
        """
        Handle transactions manually executed by the user on Binance.
          - Creates a new path if deposit is on coin not already active
          - Deactivates existing path if withdrawal removed all funds from an active coins
          - Recalculates PNL values to "ignore" added or removed funds from "gains" when deposit or part-withdrawal on
            active coin. This is to avoid incorrectly reported gains when funds are added and removed outside the bot

        :param transactions: transactions (either deposits or withdrawals) manually executed by the user
        :param exchange_active_coins: enabled coins currently active/bought
        :param bot_active_coins: all coins enabled on the bot, ie. in the supported_coin_list
        :return:
        """
        if exchange_active_coins is None:
            exchange_active_coins = []
        if bot_active_coins is None:
            bot_active_coins = []

        # Coins deposited on exchange but not yet activated on the bot
        new_coins = [x for x in exchange_active_coins if x not in bot_active_coins]

        # Coins withdrawn on exchange not yet deactivated on the bot
        removed_coins = [x for x in bot_active_coins if x not in exchange_active_coins]

        for transaction in transactions:
            self.logger.info(f"Handling manual transaction: {transaction.info()}")
            coin = transaction.coin
            tx_quantity = transaction.coin_amount
            tx_coin_price = transaction.bridge_price
            deposit = transaction.deposit

            if coin.symbol in new_coins and deposit:
                # Activate coin on bot and create new PNL records
                new_path = self.db.set_new_coin_path()
                if new_path is None:
                    self.logger.error(f"Failed to activate new path for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                self.logger.info(f"Activating new path {new_path.id} for deposited amount {tx_quantity} on {coin}")
                self.db.set_current_coin(coin, new_path)
                self.set_deposited_coin_pnl_new_path(coin, new_path, tx_quantity, tx_coin_price)
                self.update_all_usd_pnl()
            elif coin.symbol in removed_coins and not deposit:
                # Deactivate coin on bot
                old_path = self.db.get_coin_path(coin)
                if old_path is None:
                    self.logger.error(f"Failed to retrieve old path from DB for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                self.logger.info(f"Deactivating old path {old_path.id} for deposited amount {tx_quantity} on {coin}")
                self.db.deactivate_path(old_path)
            elif coin.symbol in exchange_active_coins and coin.symbol in bot_active_coins:
                # Deposit or withdrawal on already active coin so we just need to adjust PNLs to new quantity
                quantity = tx_quantity if deposit else - tx_quantity
                coin_path = self.db.get_coin_path(coin)
                if coin_path is None:
                    self.logger.error(f"Failed to retrieve path from DB when attempting to update Coin PNL for deposit/withdrawal. Coin: {coin.symbol}")
                    continue
                self.logger.info(f"Adjusting PNL records for existing path {coin_path.id} for deposited amount {tx_quantity} on {coin}")
                self.set_deposited_coin_pnl_existing_path(coin, coin_path, quantity, tx_coin_price)
                tx_amt = quantity * tx_coin_price
                self.update_usd_pnl(coin.symbol, coin_path, tx_amt)

    ##########################
    # Handling merging paths #
    ##########################

    def get_coin_path(self, coin: Coin) -> Path:
        """
        Get path for a coin.
          - Return path of coin if it exists
          - Create a new path if it doesn't already exist

        :param coin: coin to fetch path for
        :return: path for coin
        """
        path = self.db.get_coin_path(coin)
        # Check if we need to create new path
        if path is None:
            return self.db.set_new_coin_path()
        return path

    def is_paths_merging(self, coin: Coin):
        """
        Check if coin is already avtive on another path.

        :param coin: coin to check if it is already active
        :return: true if coin is already active, otherwise false
        """
        active_coins = self.db.get_active_coins()
        return coin.symbol in active_coins

    def merge_paths(self, merge_from: Union[Coin, Path], merge_to: Coin, result, price) -> Optional[Path]:
        """
        Handles merging one path into another. Happens when trading onto a coin which is already active on another path.
        Will activate a new path for merged coins and deactivate two old paths.

        :param merge_from: either a coin or path the bot is jumping from. This is the coin SOLD during the jump
        :param merge_to: coin the bot is jumping to. This is the coin BOUGHT during the jump and the one it is merging into
        :param result: BinanceOrder object resulting from buying the merge_to coin
        :param price: price paid per coin when buying merge_to coin
        :return: new path activated after successful merge of old paths
        """
        path_a = self.get_path(merge_from)
        path_b = self.get_path(merge_to)
        new_path = self.db.set_new_coin_path()
        if path_a is None or path_b is None:
            self.logger.error("Failed to merge paths. Path ID to merge could not be fetched from DB.")
            return None
        self.merge_paths_coin_pnl(merge_to, path_a, path_b, new_path, result.cumulative_filled_quantity, price)
        self.merge_path_usd_pnl(merge_to.symbol, path_a, path_b, new_path)
        self.db.deactivate_path(path_a)
        self.db.deactivate_path(path_b)
        return new_path

    def get_path(self, path: Union[Coin, Path]):
        """
        Given a coin or path, return the path.

        :param path: coin or path to fetch path for
        :return: path for coin or path
        """
        if isinstance(path, Path):
            return path
        return self.db.get_coin_path(path)

    ########################
    # USD PNL calculations #
    ########################

    def update_all_usd_pnl(self):
        """
        Update USD PNL records for all coins currently active/bought on the bot.

        :return:
        """

        active_coins = self.db.get_active_coins()
        for coin in active_coins:
            path = self.db.get_coin_path(coin)
            self.update_usd_pnl(coin, path)

    def update_usd_pnl(self, coin: str, path: Path, tx_amt=0.0):
        """
        Update USD PNL record for specific coin and path.
        May also take transaction amount to add/deduct quantity from PNL calculation (for deposits and withdrawals).

        :param coin: coin to calculate USD PNL for
        :param path: path of coin
        :param tx_amt: amount to add or deduct from calculation (positive for deposits, negative for withdrawals)
        :return:
        """
        coin_price = 1.0 if coin == "USDT" else self.manager.get_ticker_price(coin + "USDT")
        balance = self.manager.get_currency_balance(coin)
        usd_value = round(balance * coin_price, 2)
        last_pnl = self.db.get_last_usd_pnl(path)
        if last_pnl is None:
            usd_pnl = self.db.set_usd_pnl(path, usd_value, 0, 0, 0, 0)
        else:
            gain = round(usd_value - (last_pnl.value + tx_amt), 2)
            percent_gain = round(gain / (last_pnl.value + tx_amt) * 100, 2)
            total_gain = round(last_pnl.total_gain + gain, 2)
            total_percent_gain = round(last_pnl.total_percent_gain + percent_gain, 2)
            usd_pnl = self.db.set_usd_pnl(path, usd_value, gain, percent_gain, total_gain, total_percent_gain)
        self.logger.debug(f"Saved USD PNL record: {usd_pnl.info()}")

    def merge_path_usd_pnl(self, coin: str, path_a: Path, path_b: Path, new_path: Path):
        """
        Update USD PNL for merging paths.
        Merged percentage gain values will be weighted averages where coin USD value is used as weight.

        :param coin: coin bot is merging on
        :param path_a: first path with coin we want to merge
        :param path_b: second path with coin we want to merge
        :param new_path: new path the two old paths are merging into
        :return:
        """
        coin_price = self.manager.get_ticker_price(coin + "USDT")
        balance = self.manager.get_currency_balance(coin)
        usd_value = round(balance * coin_price, 2)
        path_a_last_pnl = self.db.get_last_usd_pnl(path_a)
        path_b_last_pnl = self.db.get_last_usd_pnl(path_b)
        if path_a_last_pnl is not None and path_b_last_pnl is not None:
            # If old USD PNL records exist, merge them into one
            old_value = path_a_last_pnl.value + path_b_last_pnl.value
            old_gain = path_a_last_pnl.gain + path_b_last_pnl.gain
            gain = round(usd_value - old_value, 2)
            percent_gain = round(gain / old_value * 100, 2)
            total_gain = round(old_gain + gain, 2)
            old_total_percent_gain = self.calculate_weighted_average(path_a_last_pnl.value, path_b_last_pnl.value,
                                                                     path_a_last_pnl.total_percent_gain, path_b_last_pnl.total_percent_gain)
            total_percent_gain = round(old_total_percent_gain + percent_gain, 2)
            usd_pnl = self.db.set_usd_pnl(new_path, usd_value, gain, percent_gain, total_gain, total_percent_gain)
            self.logger.info(usd_pnl.info())
        else:
            # If we don't have previous USD PNL records for merging paths, then create a USD PNL record for this path
            self.update_usd_pnl(coin, new_path)

    #########################
    # Coin PNL calculations #
    #########################

    def update_coin_pnl(self, coin: Coin, path: Path, quantity: float, price: float) -> CoinPnl:
        """
        Calculate coin PNL for the given coin and path.

        :param coin: coin to calculate new coin PNL for
        :param path: path this coin is on
        :param quantity: coin quantity of the latest transaction
        :param price: price of coin in latest transaction
        :return: coin PNL record persisted to DB
        """
        coin_gain, percent_gain, total_coin_gain, total_percent_gain = 0.0, 0.0, 0.0, 0.0
        previous_coin_pnl = self.db.get_last_coin_pnl(coin, path)
        if previous_coin_pnl is not None:
            coin_gain = quantity - previous_coin_pnl.coin_amount
            percent_gain = coin_gain / previous_coin_pnl.coin_amount * 100
            total_coin_gain = previous_coin_pnl.total_coin_gain + coin_gain
            total_percent_gain = previous_coin_pnl.total_percent_gain + percent_gain

        return self.set_coin_pnl(path, coin, quantity, price, coin_gain, percent_gain, total_coin_gain, total_percent_gain)

    def merge_paths_coin_pnl(self, to_coin: Coin, path_a: Path, path_b: Path, new_path: Path, quantity: float,
                             price: float):
        """
        Calculate coin PNL when two existing coin paths merge into a new single path.

        :param to_coin: merging coin
        :param path_a: first path with coin we want to merge
        :param path_b: second path with coin we want to merge
        :param new_path: new path we are merging path_a and path_b into
        :param quantity: quantity of coin bought after selling path_a coin (doesn't include path_b coin quantity)
        :param price: price paid per coin
        :return:
        """
        enabled_coins = self.db.get_coins()
        for coin in enabled_coins:
            path_a_coin_pnl = self.db.get_last_coin_pnl(coin, path_a)
            path_b_coin_pnl = self.db.get_last_coin_pnl(coin, path_b)

            if coin.symbol == to_coin.symbol:
                coin_pnl = self.merge_jumping_to_coin_pnl(new_path, path_a_coin_pnl, path_b_coin_pnl, price, quantity)
                self.logger.info(f"Merged coin PNL: Jump to coin: {coin_pnl.info()}")
            elif path_a_coin_pnl is None and path_b_coin_pnl is None:
                continue
            elif path_b_coin_pnl is None and path_a_coin_pnl is not None:
                path_a_coin_amt = (quantity * price) / path_a_coin_pnl.coin_price
                path_b_previous_coin_pnl = self.db.get_last_coin_pnl(to_coin, path_b)
                path_b_bridge_value = path_b_previous_coin_pnl.bridge_value
                path_b_coin_amt = path_b_bridge_value / path_a_coin_pnl.coin_price
                coin_pnl = self.merge_single_path_coin_pnl(new_path, path_a_coin_amt, path_b_coin_amt, path_a_coin_pnl)
                self.logger.info(f"Merged coin PNL: Path A, no Path B: {coin_pnl.info()}")
            elif path_a_coin_pnl is None and path_b_coin_pnl is not None:
                path_a_coin_amt = (quantity * price) / path_b_coin_pnl.coin_price
                coin_pnl = self.merge_single_path_coin_pnl(new_path, path_a_coin_amt, path_b_coin_pnl.coin_amount,
                                                           path_b_coin_pnl)
                self.logger.info(f"Merged coin PNL: Path B, no Path A: {coin_pnl.info()}")
            else:
                coin_pnl = self.merge_double_path_coin_pnl(new_path, path_a_coin_pnl, path_b_coin_pnl)
                self.logger.info(f"Merged coin PNL: Both Paths: {coin_pnl.info()}")

    def merge_jumping_to_coin_pnl(self, new_path: Path, path_a_coin_pnl: CoinPnl, path_b_coin_pnl: CoinPnl,
                                  price: float, quantity: float):
        """
        Merge coin PNL between existing paths for the coin we are merging on

        :param new_path: new path after merging two old paths
        :param path_a_coin_pnl: coin PNL record of path_a (merging path)
        :param path_b_coin_pnl: coin PNL record of path_b (merging path)
        :param price: price pair per coin
        :param quantity: quantity of coin bought after selling path_a coin (doesn't include path_b coin quantity)
        :return: merged coin PNL record persisted to DB
        """
        coin_gain = path_b_coin_pnl.coin_gain
        percent_gain = path_b_coin_pnl.percent_gain
        total_coin_gain = path_b_coin_pnl.total_coin_gain
        total_percent_gain = path_b_coin_pnl.total_percent_gain
        merged_quantity = quantity + path_b_coin_pnl.coin_amount
        if path_a_coin_pnl is not None:
            previous_amount = path_a_coin_pnl.coin_amount + path_b_coin_pnl.coin_amount
            price = self.calculate_weighted_average(quantity, path_b_coin_pnl.coin_amount, path_a_coin_pnl.coin_price, path_b_coin_pnl.coin_price)
            coin_gain = merged_quantity - previous_amount
            percent_gain = coin_gain / previous_amount * 100
            total_coin_gain = path_a_coin_pnl.total_coin_gain + path_b_coin_pnl.total_coin_gain + coin_gain
            total_percent_gain = path_a_coin_pnl.total_percent_gain + path_b_coin_pnl.total_percent_gain + percent_gain

        return self.set_coin_pnl(new_path, path_b_coin_pnl.coin, merged_quantity, price, coin_gain,
                                 percent_gain, total_coin_gain, total_percent_gain)

    def merge_single_path_coin_pnl(self, new_path: Path, path_a_coin_amt: float, path_b_coin_amt: float,
                                   merging_coin_pnl: CoinPnl) -> CoinPnl:
        """
        Merge a coin PNL which has only got history on one of two merging paths.
        Eg. Merge BTC coin PNL, but path_a has a coin PNL history for BTC whereas path_b does not.
        Conditions:
            - Should only be used to merge coin PNL history - not to be used to merge coin actually traded in this order
            - Should only be used when one of the merging paths has a history of a given coin and the other does not

        :param new_path: new path after merging two old paths
        :param path_a_coin_amt: approximate calculation of coin quantity after merging (using bridge coin price to calculate)
        :param path_b_coin_amt: approximate calculation of coin quantity after merging (using bridge coin price to calculate)
        :param merging_coin_pnl: last coin PNL record from path which has a coin PNL history
        :return: merged coin PNL record persisted to DB
        """
        merged_amount = path_a_coin_amt + path_b_coin_amt
        path_a_weight_multiplier = 1 - merging_coin_pnl.coin_amount / merged_amount
        weighted_avg_percent_gain = merging_coin_pnl.percent_gain * path_a_weight_multiplier
        weighted_avg_total_percent_gain = merging_coin_pnl.total_percent_gain * path_a_weight_multiplier

        return self.set_coin_pnl(new_path, merging_coin_pnl.coin, merged_amount, merging_coin_pnl.coin_price,
                                 merging_coin_pnl.coin_gain, weighted_avg_percent_gain, merging_coin_pnl.total_coin_gain,
                                 weighted_avg_total_percent_gain)

    def merge_double_path_coin_pnl(self, new_path: Path, path_a_coin_pnl: CoinPnl, path_b_coin_pnl: CoinPnl) -> CoinPnl:
        """
        Merge a coin PNL which has got history on both of two merging paths.
        Eg. Merge BTC coin PNL where both path_a and path_b has a coin PNL history for BTC.
        Conditions:
            - Should only be used to merge coin PNL history - not to be used to merge coin actually traded in this order
            - Should only be used when both of the merging paths has a history of a given coin

        :param new_path: new path after merging two old paths
        :param path_a_coin_pnl: coin PNL record of path_a (merging path)
        :param path_b_coin_pnl: coin PNL record of path_b (merging path)
        :return: merged coin PNL record persisted to DB
        """
        path_a_amt = path_a_coin_pnl.coin_amount
        path_b_amt = path_b_coin_pnl.coin_amount
        merged_amount = path_a_amt + path_b_amt

        weighted_avg_coin_price = self.calculate_weighted_average(path_a_amt, path_b_amt, path_a_coin_pnl.coin_price, path_b_coin_pnl.coin_price)
        merged_coin_gain = path_a_coin_pnl.coin_gain + path_b_coin_pnl.coin_gain
        merged_total_coin_gain = path_a_coin_pnl.total_coin_gain + path_b_coin_pnl.total_coin_gain
        weighted_avg_percent_gain = self.calculate_weighted_average(path_a_amt, path_b_amt, path_a_coin_pnl.percent_gain, path_b_coin_pnl.percent_gain)
        weighted_avg_total_percent_gain = self.calculate_weighted_average(path_a_amt, path_b_amt, path_a_coin_pnl.total_percent_gain, path_b_coin_pnl.total_percent_gain)

        return self.set_coin_pnl(new_path, path_a_coin_pnl.coin, merged_amount, weighted_avg_coin_price,
                                 merged_coin_gain, weighted_avg_percent_gain, merged_total_coin_gain,
                                 weighted_avg_total_percent_gain)

    def set_deposited_coin_pnl_new_path(self, coin: Coin, path: Path, quantity: float, price: float):
        """
        Calculate coin PNL for the given coin and path when the user has manually deposited funds onto a new
        coin.

        :param coin: coin funds were added to
        :param path: new path for this coin
        :param quantity: quantity of coins deposited on coin
        :param price: price paid per coin
        :return: coin PNL record persisted to DB
        """
        coin_gain, percent_gain, total_coin_gain, total_percent_gain = 0.0, 0.0, 0.0, 0.0
        previous_coin_pnl = self.db.get_last_coin_pnl(coin, path)
        if previous_coin_pnl is not None:
            quantity = previous_coin_pnl.coin_amount + quantity
            coin_gain = previous_coin_pnl.coin_gain
            percent_gain = previous_coin_pnl.percent_gain
            total_coin_gain = previous_coin_pnl.total_coin_gain
            total_percent_gain = previous_coin_pnl.total_percent_gain

        coin_pnl = self.set_coin_pnl(path, coin, quantity, price, coin_gain, percent_gain, total_coin_gain, total_percent_gain)
        if coin_pnl is None:
            self.logger.error(f"Error occurred when creating coin PNL for new path: {path.id}, coin: {coin.symbol}")
        self.logger.info(f"Set coin PNL for new path {coin_pnl.info()}")

    def set_deposited_coin_pnl_existing_path(self, coin: Coin, path: Path, quantity: float, price: float):
        """
        Recalculate coin PNL when user has manually deposited or withdrawn funds from a coin.

        :param coin: coin funds were deposited or withdrawn from
        :param path: path for this coin
        :param quantity: quantity of coins deposited or withdrawn from coin
        :param price: price paid per coin
        :return:
        """
        enabled_coins = self.db.get_coins()
        for enabled_coin in enabled_coins:
            previous_coin_pnl = self.db.get_last_coin_pnl(enabled_coin, path)
            if previous_coin_pnl is None:
                continue
            estimated_quantity = quantity
            last_price = price
            if enabled_coin.symbol != coin.symbol:
                bridge_value = estimated_quantity * last_price
                last_price = previous_coin_pnl.coin_price
                estimated_quantity = bridge_value / last_price
            total_quantity = previous_coin_pnl.coin_amount + estimated_quantity
            avg_price = ((estimated_quantity * last_price) + previous_coin_pnl.bridge_value) / total_quantity if estimated_quantity > 0 else previous_coin_pnl.coin_price
            coin_gain = previous_coin_pnl.coin_gain
            percent_gain = previous_coin_pnl.percent_gain
            total_coin_gain = previous_coin_pnl.total_coin_gain
            total_percent_gain = previous_coin_pnl.total_percent_gain
            coin_pnl = self.set_coin_pnl(path, enabled_coin, total_quantity, avg_price, coin_gain, percent_gain,
                                         total_coin_gain, total_percent_gain)
            if coin_pnl is None:
                self.logger.error(
                    f"Error occurred when updated coin PNL for existing path: {path.id}, coin: {enabled_coin.symbol}")
            self.logger.info(f"Updated coin PNL for existing path {coin_pnl.info()}")

    def set_coin_pnl(self, path: Path, coin: Coin, quantity: float, price: float, coin_gain: float,
                     percent_gain: float, total_coin_gain: float, total_percent_gain: float):
        base_asset_precision = self.manager.get_base_asset_precision(coin.symbol, self.config.BRIDGE.symbol)
        bridge_precision = self.manager.get_quote_asset_precision(coin.symbol, self.config.BRIDGE.symbol)
        return self.db.set_coin_pnl(path, coin, quantity, price, coin_gain, percent_gain, total_coin_gain,
                                    total_percent_gain, base_asset_precision, bridge_precision)

    @staticmethod
    def calculate_weighted_average(weight_a: float, weight_b: float, value_a: float, value_b: float):
        """
        Calculate the weighted average value given 2 weights and 2 corresponding values.

        :param weight_a: weight to be applied to value_a
        :param weight_b: weight to be applied to value_b
        :param value_a: first value to calculate weighted average from
        :param value_b: second value to calculate weighted average from
        :return: weighted average of two values
        """
        total_amount = weight_a + weight_b
        path_a_weight_multiplier = weight_a / total_amount
        path_b_weight_multiplier = weight_b / total_amount
        path_a_weighted_value = value_a * path_a_weight_multiplier
        path_b_weighted_value = value_b * path_b_weight_multiplier

        return path_a_weighted_value + path_b_weighted_value
