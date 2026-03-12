# Brave — Multi-Strategy Trading Bot
# Reads active_strategy from Firebase brave_config each loop.
# Mobile app switches strategy by writing to brave_config/active_strategy.

import MetaTrader5 as mt5
import firebase_admin
from firebase_admin import credentials, db
import numpy as np
import time
import logging
import sys
import os
import traceback
from datetime import datetime

# We add the project root to sys.path so we can import config.py
# now that the bot has been moved into the src/ folder.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    FIREBASE_DATABASE_URL, USER_ID,
    SYMBOLS, TIMEFRAME, LOT_SIZE, MAX_TRADES,
    STOP_LOSS_PIPS, TAKE_PROFIT_PIPS
)

# ── Strategy registry ─────────────────────────────────────────────────
# Add new strategies here as they are built.
# Key must match the string stored in Firebase brave_config/active_strategy.
from thunder import Thunder

STRATEGY_REGISTRY: dict = {
    "thunder": Thunder,
}

# ── Logging ───────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
log_filename = f'logs/bot_log_{datetime.now().strftime("%Y%m%d")}.txt'
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)


class BraveBot:
    """
    Brave trading bot.

    Each main loop iteration:
        1. Read brave_config/active_strategy from Firebase
        2. If strategy changed, hot-swap the strategy instance
        3. Check market status
        4. Run strategy.analyze() on open pairs
        5. Push alerts to Firebase for mobile review
    """

    CHECK_INTERVAL        = 60    # seconds between signal checks
    HEALTH_CHECK_INTERVAL = 600   # seconds between health checks
    PAIR_REFRESH_INTERVAL = 3600  # seconds between dynamic pair re-selection

    def __init__(self):
        self.firebase_enabled     = False  # ← MUST BE FIRST
        self.is_running           = False
        self.last_health_check    = datetime.now()
        self.last_market_check    = None
        self.last_pair_refresh    = None
        self.market_status_cache  = {}
        self.active_pairs         = []

        # Strategy state
        self.active_strategy_name: str | None   = None
        self.strategy_instance                  = None

        logging.info("=" * 60)
        logging.info("INITIALIZING BRAVE BOT")
        logging.info("=" * 60)

        if not self._init_mt5():
            raise RuntimeError("MT5 initialization failed")

        self.firebase_enabled = self._init_firebase()
        if not self.firebase_enabled:
            logging.warning("Firebase disabled — running in LOCAL MODE")

        logging.info("Brave bot ready")

    # ═══════════════════════════════════════════════════════════════
    # INITIALIZATION
    # ═══════════════════════════════════════════════════════════════

    def _init_mt5(self) -> bool:
        if not mt5.initialize():
            logging.error("MT5 initialization failed")
            return False

        authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not authorized:
            logging.error(f"MT5 login failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        logging.info(f"MT5 connected — Account: {info.login} | Balance: ${info.balance}")
        return True

    def _init_firebase(self) -> bool:
        if not os.path.exists("serviceAccountKey.json"):
            logging.error("serviceAccountKey.json not found")
            return False

        try:
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DATABASE_URL})

            self.config_ref       = db.reference(f'users/{USER_ID}/bot_config')
            self.brave_config_ref = db.reference(f'users/{USER_ID}/brave_config')
            self.status_ref       = db.reference(f'users/{USER_ID}/bot_status')
            self.command_ref      = db.reference(f'users/{USER_ID}/commands')
            self.trades_ref       = db.reference(f'users/{USER_ID}/trades')
            self.health_ref       = db.reference(f'users/{USER_ID}/health')
            self.alerts_ref       = db.reference(f'users/{USER_ID}/alerts')
            self.market_status_ref = db.reference(f'users/{USER_ID}/market_status')

            # Test connection
            self.config_ref.get()

            # Initialize brave_config if missing
            if self.brave_config_ref.get() is None:
                self.brave_config_ref.set({
                    'active_strategy':     'thunder',
                    'available_strategies': list(STRATEGY_REGISTRY.keys()),
                    'last_switched':       None,
                    'switched_by':         None,
                })
                logging.info("brave_config initialized in Firebase")

            # Initialize bot_config defaults if missing
            if self.config_ref.get() is None:
                self.config_ref.set({
                    'symbols':          SYMBOLS,
                    'timeframe':        TIMEFRAME,
                    'lot_size':         LOT_SIZE,
                    'max_trades':       MAX_TRADES,
                    'stop_loss_pips':   STOP_LOSS_PIPS,
                    'take_profit_pips': TAKE_PROFIT_PIPS,
                })

            # Start command listener for mobile start/stop
            self.command_ref.listen(self._handle_command)

            self._update_status({
                'is_running':       False,
                'bot_version':      f'Brave v1.0',
                'active_strategy':  'unknown',
            })

            logging.info("Firebase connected — command listener active")
            return True

        except Exception as e:
            logging.error(f"Firebase init failed: {e}")
            traceback.print_exc()
            return False

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY SWITCHING
    # ═══════════════════════════════════════════════════════════════

    def _load_active_strategy(self, config: dict) -> bool:
        """
        Read brave_config/active_strategy from Firebase.
        If it differs from the currently loaded strategy, hot-swap.

        Returns True if a valid strategy is loaded, False otherwise.
        """
        if self.firebase_enabled:
            try:
                brave_config = self.brave_config_ref.get()
                requested = brave_config.get('active_strategy', 'thunder') if brave_config else 'thunder'
            except Exception as e:
                logging.error(f"Failed to read brave_config: {e}")
                requested = self.active_strategy_name or 'thunder'
        else:
            requested = self.active_strategy_name or 'thunder'

        # No change needed
        if requested == self.active_strategy_name and self.strategy_instance is not None:
            return True

        # Validate the requested strategy exists in registry
        if requested not in STRATEGY_REGISTRY:
            logging.error(
                f"Strategy '{requested}' not found in registry. "
                f"Available: {list(STRATEGY_REGISTRY.keys())}"
            )
            return False

        # Hot-swap
        strategy_class         = STRATEGY_REGISTRY[requested]
        self.strategy_instance = strategy_class(config)
        old                    = self.active_strategy_name
        self.active_strategy_name = requested

        if old is None:
            logging.info(f"Strategy loaded: {requested}")
        else:
            logging.info(f"Strategy switched: {old} → {requested}")

        # Update Firebase so mobile app reflects the change
        self._update_status({'active_strategy': requested})

        return True

    # ═══════════════════════════════════════════════════════════════
    # COMMAND HANDLER (mobile start / stop)
    # ═══════════════════════════════════════════════════════════════

    def _handle_command(self, event) -> None:
        try:
            command = event.data
            if not command:
                return

            action    = command.get('action')
            timestamp = command.get('timestamp', 'unknown')
            logging.info(f"Command received: {action} (at {timestamp})")

            if action == 'start':
                self.is_running = True
                self._update_status({
                    'is_running':   True,
                    'last_started': datetime.now().isoformat(),
                })
                logging.info("Bot STARTED via mobile command")

            elif action == 'stop':
                self.is_running = False
                self._update_status({
                    'is_running':   False,
                    'last_stopped': datetime.now().isoformat(),
                })
                logging.info("Bot STOPPED via mobile command")

        except Exception as e:
            logging.error(f"Error handling command: {e}")

    # ═══════════════════════════════════════════════════════════════
    # MARKET STATUS
    # ═══════════════════════════════════════════════════════════════

    def _refresh_market_status(self, force: bool = False) -> dict:
        now = datetime.now()
        stale = (
            self.last_market_check is None or
            (now - self.last_market_check).total_seconds() >= 60
        )

        if not (force or stale):
            return self.market_status_cache

        status = {}
        for symbol in SYMBOLS:
            info = mt5.symbol_info(symbol)
            if info is None:
                status[symbol] = "UNAVAILABLE"
                continue

            if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                status[symbol] = "CLOSED"
            elif info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
                tick = mt5.symbol_info_tick(symbol)
                if tick and (datetime.now().timestamp() - tick.time) < 300:
                    status[symbol] = "OPEN"
                else:
                    status[symbol] = "CLOSED"
            else:
                status[symbol] = "RESTRICTED"

        self.market_status_cache = status
        self.last_market_check   = now

        if self.firebase_enabled:
            try:
                self.market_status_ref.set({
                    'timestamp':    now.isoformat(),
                    'status':       status,
                    'overall_open': any(v == "OPEN" for v in status.values()),
                    'all_closed':   all(v == "CLOSED" for v in status.values()),
                })
            except Exception as e:
                logging.error(f"Failed to update market status in Firebase: {e}")

        return status

    def _open_markets(self) -> list[str]:
        return [s for s, v in self._refresh_market_status().items() if v == "OPEN"]

    # ═══════════════════════════════════════════════════════════════
    # PAIR SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_pairs(self, max_pairs: int = 3) -> list[str]:
        """Score available pairs by spread and volatility, return top N."""
        logging.info("Selecting trading pairs...")
        scored = []

        for symbol in SYMBOLS:
            try:
                info = mt5.symbol_info(symbol)
                if info is None or info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                    continue

                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    continue

                point = info.point
                if info.digits in (5, 3):
                    point *= 10

                spread = (tick.ask - tick.bid) / point

                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 20)
                if rates is None or len(rates) < 20:
                    continue

                avg_range = np.mean([(r['high'] - r['low']) / point for r in rates])

                spread_score    = max(0, 10 - spread)
                volatility_score = min(avg_range / 10, 10)
                major_bonus     = 3 if symbol in ["EURUSD", "GBPUSD", "XAUUSD"] else 0

                scored.append({
                    'symbol': symbol,
                    'score':  spread_score + volatility_score + major_bonus,
                })

            except Exception:
                continue

        scored.sort(key=lambda x: x['score'], reverse=True)
        selected = [p['symbol'] for p in scored[:max_pairs]]

        if not selected:
            logging.warning("No pairs scored — falling back to EURUSD")
            return ["EURUSD"]

        logging.info(f"Selected pairs: {', '.join(selected)}")
        return selected

    # ═══════════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════

    def _health_check(self) -> bool:
        logging.info("Running health check...")

        health: dict = {
            'timestamp':            datetime.now().isoformat(),
            'mt5_connected':        False,
            'firebase_connected':   False,
            'account_trade_allowed': False,
            'symbols_available':    {},
            'market_status':        {},
            'active_strategy':      self.active_strategy_name,
            'status':               'UNKNOWN',
        }

        terminal = mt5.terminal_info()
        if terminal:
            health['mt5_connected']              = True
            health['mt5_connected_to_broker']    = terminal.connected

        if self.firebase_enabled:
            try:
                self.status_ref.get()
                health['firebase_connected'] = True
            except Exception:
                health['firebase_connected'] = False

        market_status = self._refresh_market_status(force=True)
        health['market_status'] = market_status

        for symbol in SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            health['symbols_available'][symbol] = tick is not None

        account = mt5.account_info()
        if account:
            health['account_trade_allowed'] = account.trade_allowed
            health['balance']  = account.balance
            health['equity']   = account.equity

        checks = [
            health['mt5_connected'],
            health['firebase_connected'],
            all(health['symbols_available'].values()),
            health['account_trade_allowed'],
        ]

        health['status'] = 'HEALTHY' if all(checks) else 'DEGRADED'

        if health['status'] == 'HEALTHY':
            logging.info("Health check PASSED")
        else:
            logging.warning("Health check DEGRADED — review logs")

        if self.firebase_enabled:
            try:
                self.health_ref.set(health)
            except Exception as e:
                logging.error(f"Failed to push health to Firebase: {e}")

        self.last_health_check = datetime.now()
        return health['status'] == 'HEALTHY'

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL CHECKING
    # ═══════════════════════════════════════════════════════════════

    def _check_signals(self, config: dict) -> None:
        logging.info(f"Checking signals... ({datetime.now().strftime('%H:%M:%S')})")

        # ── Load / hot-swap strategy ──────────────────────────────
        if not self._load_active_strategy(config):
            logging.error("No valid strategy loaded — skipping signal check")
            return

        # ── Refresh pairs every hour ──────────────────────────────
        now = datetime.now()
        if (self.last_pair_refresh is None or
                (now - self.last_pair_refresh).total_seconds() >= self.PAIR_REFRESH_INTERVAL):
            self.active_pairs      = self._select_pairs(max_pairs=3)
            self.last_pair_refresh = now

        # ── Check open markets ────────────────────────────────────
        open_markets = self._open_markets()
        if not open_markets:
            logging.info("All markets closed — skipping")
            self._update_status({
                'is_running':     True,
                'trading_active': False,
                'market_status':  'ALL_CLOSED',
            })
            return

        # ── Intersect selected pairs with open markets ────────────
        pairs_to_analyze = [p for p in self.active_pairs if p in open_markets]
        if not pairs_to_analyze:
            logging.info(f"Selected pairs {self.active_pairs} not open right now")
            return

        logging.info(f"Analyzing: {', '.join(pairs_to_analyze)} | Strategy: {self.active_strategy_name}")

        # ── Run strategy on each pair ─────────────────────────────
        for symbol in pairs_to_analyze:
            try:
                if self._count_positions(symbol) > 0:
                    logging.info(f"[{symbol}] Position already open — skipping")
                    continue

                signal = self.strategy_instance.analyze(symbol)

                if signal is None:
                    logging.info(f"[{symbol}] No signal")
                    continue

                logging.info(
                    f"[{symbol}] Signal: {signal['direction']} | "
                    f"Entry: {signal['entry_price']} | "
                    f"SL: {signal['suggested_sl']} | "
                    f"TP: {signal['suggested_tp']} | "
                    f"RR: {signal['risk_reward_ratio']}"
                )

                # ── Auto-execution ──────────────────────────────────────────
                # We execute the trade automatically if it meets all strategy criteria.
                # The user can still manually close the trade in the MT5 terminal.
                self.execute_signal(symbol, signal, config)

                # Push alert to Firebase for mobile review
                if self.firebase_enabled:
                    try:
                        self.alerts_ref.push({
                            **signal,
                            'alert_type':       f'{self.active_strategy_name.upper()}_SETUP',
                            'action_required':  'REVIEW_AND_EXECUTE',
                            'sent_at':          datetime.now().isoformat(),
                        })
                        logging.info(f"[{symbol}] Alert pushed to Firebase")
                    except Exception as e:
                        logging.error(f"[{symbol}] Failed to push alert: {e}")

            except Exception as e:
                logging.error(f"[{symbol}] Error during analysis: {e}")
                traceback.print_exc()

        # ── Update bot status ─────────────────────────────────────
        account = mt5.account_info()
        self._update_status({
            'is_running':       True,
            'trading_active':   True,
            'active_strategy':  self.active_strategy_name,
            'open_markets':     open_markets,
            'markets_analyzed': pairs_to_analyze,
            'balance':          account.balance if account else None,
            'equity':           account.equity  if account else None,
            'profit':           account.profit  if account else None,
            'open_positions':   self._count_positions(),
            'market_open':      True,
        })

    # ═══════════════════════════════════════════════════════════════
    # TRADE EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def execute_signal(self, symbol: str, signal: dict, config: dict):
        """Execute a signal from any strategy with 1% risk-based lot sizing."""
        try:
            strategy_name = signal.get('strategy_name', self.active_strategy_name)

            # Re-verify market is still open
            market_status = self._refresh_market_status(force=True)
            if market_status.get(symbol) != "OPEN":
                logging.warning(f"[{symbol}] Market closed — cannot execute")
                return None

            account = mt5.account_info()
            tick    = mt5.symbol_info_tick(symbol)
            info    = mt5.symbol_info(symbol)
            if account is None or tick is None or info is None:
                logging.error(f"[{symbol}] Could not get account/tick/symbol info")
                return None

            direction  = signal['direction']
            entry      = signal['entry_price']
            sl         = signal['suggested_sl']
            tp         = signal['suggested_tp']
            
            # ── 1% Risk-Based Lot Sizing ──────────────────────────────────
            # We calculate lot size so that hitting SL results in a 1% balance loss.
            # This matches the successful backtest results (+273% return logic).
            risk_per_trade = account.balance * 0.01  # $1 for $100 balance
            price_risk     = abs(entry - sl)
            
            if price_risk == 0:
                logging.error(f"[{symbol}] Entry and SL are identical — skipping")
                return None

            # Lot calculation: risk / (price_risk * contract_size)
            # Default contract size for FX is 100,000.
            # We use info.trade_contract_size for accuracy across all symbols (Gold, Indices).
            lot = risk_per_trade / (price_risk * info.trade_contract_size)
            
            # Normalize lot to symbol's volume step and range
            lot = max(info.volume_min, min(info.volume_max, round(lot / info.volume_step) * info.volume_step))
            
            logging.info(f"[{symbol}] Risk sizing: Account=${account.balance:.2f} | "
                         f"Risk=${risk_per_trade:.2f} | Lot={lot:.2f}")

            # ── Order Configuration ──────────────────────────────────────
            # "Thunder" uses STOP orders (BUY_STOP / SELL_STOP) as per backtest.
            # If entry_price is already exceeded, we fallback to MARKET execution.
            order_type_map = {
                ("BUY", "STOP"):   mt5.ORDER_TYPE_BUY_STOP,
                ("SELL", "STOP"):  mt5.ORDER_TYPE_SELL_STOP,
                ("BUY", "MARKET"): mt5.ORDER_TYPE_BUY,
                ("SELL", "MARKET"): mt5.ORDER_TYPE_SELL,
            }
            
            # Use provided order_type or default to MARKET
            sig_type   = signal.get('order_type', 'MARKET')
            order_type = order_type_map.get((direction, sig_type), mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL)
            
            # For STOP orders, we use the signal's entry price.
            # For MARKET orders, we use current ask/bid.
            price = entry if sig_type != "MARKET" else (tick.ask if direction == "BUY" else tick.bid)

            request = {
                "action":       mt5.TRADE_ACTION_PENDING if sig_type == "STOP" else mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       round(lot, 2),
                "type":         order_type,
                "price":        price,
                "sl":           sl,
                "tp":           tp,
                "deviation":    20,
                "magic":        300001,
                "comment":      f"brave_{strategy_name[:10]}", # comment max 31 chars
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC if sig_type == "MARKET" else mt5.ORDER_FILLING_RETURN,
            }

            result = mt5.order_send(request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"[{symbol}] Order submitted successfully — Result: {result.retcode}")
                # Log to Firebase
                if self.firebase_enabled:
                    try:
                        self.trades_ref.push({
                            'timestamp':  datetime.now().isoformat(),
                            'symbol':     symbol,
                            'type':       f"{direction}_{sig_type}",
                            'entry':      price,
                            'sl':         sl,
                            'tp':         tp,
                            'lot':        lot,
                            'ticket':     getattr(result, 'order', 0),
                            'strategy':   strategy_name,
                        })
                    except Exception as e:
                        logging.error(f"[{symbol}] Failed to log trade to Firebase: {e}")
                return result
            else:
                err_msg = result.comment if result else "No result from order_send"
                logging.error(f"[{symbol}] Order failed: {err_msg}")
                return None

        except Exception as e:
            logging.error(f"[{symbol}] Execution error: {e}")
            traceback.print_exc()
            return None

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _count_positions(self, symbol: str | None = None) -> int:
        # Check active positions
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        position_count = len(positions) if positions else 0

        # Also check pending orders — a pending STOP not yet triggered is NOT a position
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        order_count = len(orders) if orders else 0

        return position_count + order_count

    def _get_config(self) -> dict:
        if self.firebase_enabled:
            try:
                config = self.config_ref.get()
                if config:
                    return config
            except Exception:
                pass
        return {
            'symbols':          SYMBOLS,
            'timeframe':        TIMEFRAME,
            'lot_size':         LOT_SIZE,
            'max_trades':       MAX_TRADES,
            'stop_loss_pips':   STOP_LOSS_PIPS,
            'take_profit_pips': TAKE_PROFIT_PIPS,
        }

    def _update_status(self, data: dict) -> None:
        if not self.firebase_enabled:
            return
        try:
            data['last_updated'] = datetime.now().isoformat()
            self.status_ref.update(data)
        except Exception as e:
            logging.error(f"Failed to update status: {e}")

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def run(self) -> None:
        logging.info("=" * 60)
        logging.info("BRAVE BOT STARTED")
        logging.info(f"Check interval:  {self.CHECK_INTERVAL}s")
        logging.info(f"Health interval: {self.HEALTH_CHECK_INTERVAL}s")
        logging.info(f"Strategies available: {list(STRATEGY_REGISTRY.keys())}")
        logging.info("=" * 60)

        self.is_running = True
        self._update_status({'is_running': True})
        self._health_check()

        # Initial pair selection
        self.active_pairs      = self._select_pairs(max_pairs=3)
        self.last_pair_refresh = datetime.now()

        try:
            while True:
                if not self.is_running:
                    logging.info("Bot paused — waiting for start command...")
                    time.sleep(self.CHECK_INTERVAL)
                    continue

                # Periodic health check
                elapsed = (datetime.now() - self.last_health_check).total_seconds()
                if elapsed >= self.HEALTH_CHECK_INTERVAL:
                    self._health_check()

                config = self._get_config()
                self._check_signals(config)

                logging.info(f"Sleeping {self.CHECK_INTERVAL}s...\n")
                time.sleep(self.CHECK_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Bot stopped by user (Ctrl+C)")
        except Exception as e:
            logging.error(f"Critical error: {e}")
            traceback.print_exc()
        finally:
            self.is_running = False
            self._update_status({'is_running': False})
            mt5.shutdown()
            logging.info("Bot shut down")


if __name__ == "__main__":
    try:
        bot = BraveBot()
        bot.run()
    except Exception as e:
        logging.error(f"Failed to start bot: {e}")
        traceback.print_exc()