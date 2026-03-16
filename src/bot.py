# bot.py — Brave Multi-Strategy Trading Bot
# ─────────────────────────────────────────────────────────────────────
# Phase 2 changes:
#   1. _count_positions() now includes pending STOP orders
#   2. NewsFilter integrated — skips signal during high-impact events
#   3. Log rotation — daily files, max 7 days kept
#   4. Firebase SSE reconnect handled gracefully (no crash on timeout)
# ─────────────────────────────────────────────────────────────────────

import MetaTrader5 as mt5
import firebase_admin
from firebase_admin import credentials, db
import numpy as np
import time as time_module
import logging
import logging.handlers
import sys
import os
import traceback
from datetime import datetime, time, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    FIREBASE_DATABASE_URL, USER_ID,
    SYMBOLS, TIMEFRAME, LOT_SIZE, MAX_TRADES,
    STOP_LOSS_PIPS, TAKE_PROFIT_PIPS
)

from thunder import Thunder
from news_filter import NewsFilter

# ── Strategy registry ─────────────────────────────────────────────────
STRATEGY_REGISTRY: dict = {
    "thunder": Thunder,
}

# ── Logging — rotating daily, keep 7 days ─────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_handler = logging.handlers.TimedRotatingFileHandler(
    filename="logs/brave_bot.log",
    when="midnight",
    backupCount=7,
    encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_log_handler, _console])


class BraveBot:
    """
    Brave trading bot — Phase 2.

    Each main loop iteration:
        1. Read brave_config/active_strategy from Firebase
        2. Hot-swap strategy if changed
        3. Check market status
        4. News filter — skip pairs with high-impact events nearby
        5. Run strategy.analyze() on open pairs
        6. Place order if signal valid (1% risk sizing)
        7. Push alert to Firebase
    """

    CHECK_INTERVAL        = 60     # seconds between signal checks
    HEALTH_CHECK_INTERVAL = 600    # seconds between health checks
    PAIR_REFRESH_INTERVAL = 3600   # seconds between pair re-scoring

    # ── Session windows (UTC) ─────────────────────────────────────
    LONDON_OPEN  = time(8, 0)
    LONDON_CLOSE = time(11, 0)
    NY_OPEN      = time(13, 0)
    NY_CLOSE     = time(16, 0)

    def __init__(self):
        # firebase_enabled MUST be set first — the Firebase listener
        # thread fires before __init__ completes and calls _update_status
        self.firebase_enabled     = False
        self.is_running           = False
        self.last_health_check    = datetime.now()
        self.last_market_check    = None
        self.last_pair_refresh    = None
        self.market_status_cache  = {}
        self.active_pairs         = []
        self._session_start_equity = None
        self._last_session_date    = None

        self.active_strategy_name: str | None = None
        self.strategy_instance                = None

        # Phase 2: news filter
        self.news_filter = NewsFilter()

        logging.info("=" * 60)
        logging.info("INITIALIZING BRAVE BOT — Phase 2")
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
        logging.info(f"MT5 connected — Account: {info.login} | Balance: ${info.balance:.2f}")
        return True

    def _init_firebase(self) -> bool:
        if not os.path.exists("serviceAccountKey.json"):
            logging.error("serviceAccountKey.json not found")
            return False

        try:
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})

            self.config_ref        = db.reference(f"users/{USER_ID}/bot_config")
            self.brave_config_ref  = db.reference(f"users/{USER_ID}/brave_config")
            self.status_ref        = db.reference(f"users/{USER_ID}/bot_status")
            self.command_ref       = db.reference(f"users/{USER_ID}/commands")
            self.trades_ref        = db.reference(f"users/{USER_ID}/trades")
            self.health_ref        = db.reference(f"users/{USER_ID}/health")
            self.alerts_ref        = db.reference(f"users/{USER_ID}/alerts")
            self.market_status_ref = db.reference(f"users/{USER_ID}/market_status")

            self.config_ref.get()  # connection test

            if self.brave_config_ref.get() is None:
                self.brave_config_ref.set({
                    "active_strategy":      "thunder",
                    "available_strategies": list(STRATEGY_REGISTRY.keys()),
                    "last_switched":        None,
                    "switched_by":          None,
                })
                logging.info("brave_config initialized in Firebase")

            if self.config_ref.get() is None:
                self.config_ref.set({
                    "symbols":          SYMBOLS,
                    "timeframe":        TIMEFRAME,
                    "lot_size":         LOT_SIZE,
                    "max_trades":       MAX_TRADES,
                    "stop_loss_pips":   STOP_LOSS_PIPS,
                    "take_profit_pips": TAKE_PROFIT_PIPS,
                })

            self.command_ref.listen(self._handle_command)

            self._update_status({
                "is_running":      False,
                "bot_version":     "Brave v2.0",
                "active_strategy": "unknown",
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
        if self.firebase_enabled:
            try:
                brave_config = self.brave_config_ref.get()
                requested = brave_config.get("active_strategy", "thunder") if brave_config else "thunder"
            except Exception as e:
                logging.error(f"Failed to read brave_config: {e}")
                requested = self.active_strategy_name or "thunder"
        else:
            requested = self.active_strategy_name or "thunder"

        if requested == self.active_strategy_name and self.strategy_instance is not None:
            return True

        if requested not in STRATEGY_REGISTRY:
            logging.error(
                f"Strategy '{requested}' not in registry. "
                f"Available: {list(STRATEGY_REGISTRY.keys())}"
            )
            return False

        old                       = self.active_strategy_name
        self.strategy_instance    = STRATEGY_REGISTRY[requested](config)
        self.active_strategy_name = requested

        logging.info(f"Strategy {'loaded' if old is None else f'switched: {old} →'} {requested}")
        self._update_status({"active_strategy": requested})
        return True

    # ═══════════════════════════════════════════════════════════════
    # COMMAND HANDLER
    # ═══════════════════════════════════════════════════════════════

    def _handle_command(self, event) -> None:
        try:
            command = event.data
            if not command:
                return

            action = command.get("action")
            logging.info(f"Command received: {action}")

            if action == "start":
                self.is_running = True
                self._update_status({"is_running": True, "last_started": datetime.now().isoformat()})
                logging.info("Bot STARTED via command")

            elif action == "stop":
                self.is_running = False
                self._update_status({"is_running": False, "last_stopped": datetime.now().isoformat()})
                logging.info("Bot STOPPED via command")

        except Exception as e:
            logging.error(f"Error handling command: {e}")

    # ═══════════════════════════════════════════════════════════════
    # MARKET STATUS
    # ═══════════════════════════════════════════════════════════════

    def _refresh_market_status(self, force: bool = False) -> dict:
        now   = datetime.now()
        stale = (
            self.last_market_check is None
            or (now - self.last_market_check).total_seconds() >= 60
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
                    "timestamp":    now.isoformat(),
                    "status":       status,
                    "overall_open": any(v == "OPEN" for v in status.values()),
                    "all_closed":   all(v == "CLOSED" for v in status.values()),
                })
            except Exception as e:
                logging.error(f"Failed to update market status: {e}")

        return status

    def _open_markets(self) -> list[str]:
        return [s for s, v in self._refresh_market_status().items() if v == "OPEN"]

    # ═══════════════════════════════════════════════════════════════
    # PAIR SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_pairs(self, max_pairs: int = 3) -> list[str]:
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

                point  = info.point
                if info.digits in (5, 3):
                    # Multiplier for 5/3 degimal brokers to treat 1.0 as 1 pip
                    point *= 10

                spread = (tick.ask - tick.bid) / point

                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 20)
                if rates is None or len(rates) < 20:
                    continue

                avg_range        = np.mean([(r["high"] - r["low"]) / point for r in rates])
                spread_score     = max(0, 10 - spread)
                volatility_score = min(avg_range / 10, 10)
                major_bonus      = 3 if symbol in ["EURUSD", "GBPUSD", "XAUUSD"] else 0

                if symbol == "USDJPY":  # Pending backtest
                    continue

                scored.append({"symbol": symbol, "score": spread_score + volatility_score + major_bonus})

            except Exception:
                continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        selected = [p["symbol"] for p in scored[:max_pairs]]

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
            "timestamp":             datetime.now().isoformat(),
            "mt5_connected":         False,
            "firebase_connected":    False,
            "account_trade_allowed": False,
            "symbols_available":     {},
            "market_status":         {},
            "active_strategy":       self.active_strategy_name,
            "bot_version":           "Brave v2.0",
            "status":                "UNKNOWN",
        }

        terminal = mt5.terminal_info()
        if terminal:
            health["mt5_connected"]           = True
            health["mt5_connected_to_broker"] = terminal.connected

        if self.firebase_enabled:
            try:
                self.status_ref.get()
                health["firebase_connected"] = True
            except Exception:
                health["firebase_connected"] = False

        health["market_status"] = self._refresh_market_status(force=True)

        for symbol in SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            health["symbols_available"][symbol] = tick is not None

        account = mt5.account_info()
        if account:
            health["account_trade_allowed"] = account.trade_allowed
            health["balance"]               = account.balance
            health["equity"]                = account.equity

        checks = [
            health["mt5_connected"],
            health["firebase_connected"],
            health["account_trade_allowed"],
        ]
        health["status"] = "HEALTHY" if all(checks) else "DEGRADED"

        if health["status"] == "HEALTHY":
            logging.info("Health check PASSED")
        else:
            logging.warning("Health check DEGRADED — review logs")

        if self.firebase_enabled:
            try:
                self.health_ref.set(health)
            except Exception as e:
                logging.error(f"Failed to push health: {e}")

        self.last_health_check = datetime.now()
        return health["status"] == "HEALTHY"

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL CHECKING — Phase 2
    # ═══════════════════════════════════════════════════════════════

    def _check_signals(self, config: dict) -> None:
        logging.info(f"Checking signals... ({datetime.now().strftime('%H:%M:%S')})")

        if not self._load_active_strategy(config):
            logging.error("No valid strategy — skipping")
            return

        # ── Daily Loss Limiter ────────────────────────────────────────
        account = mt5.account_info()
        if account:
            today = datetime.now().date().isoformat()
            
            if self._last_session_date != today or self._session_start_equity is None:
                # Use current equity as the high-water mark for the new daily session
                self._session_start_equity = account.equity
                self._last_session_date    = today
                logging.info(f"Daily session started — Equity: ${self._session_start_equity:.2f}")

            session_pnl = account.equity - self._session_start_equity
            loss_limit  = -(self._session_start_equity * 0.05)

            if session_pnl <= loss_limit:
                logging.warning(
                    f"Daily loss limit hit (${session_pnl:.2f} <= ${loss_limit:.2f}) "
                    "— bot paused for today"
                )
                self.is_running = False
                self._update_status({
                    "is_running":     False,
                    "trading_active": False,
                    "paused_reason":  "DAILY_LOSS_LIMIT",
                    "pnl_at_pause":   session_pnl
                })
                return

        if (
            self.last_pair_refresh is None
            or (datetime.now() - self.last_pair_refresh).total_seconds() >= self.PAIR_REFRESH_INTERVAL
        ):
            # Recalculate best pairs periodically to adapt to changing volatility/spreads
            now = datetime.now()
            self.active_pairs      = self._select_pairs(max_pairs=3)
            self.last_pair_refresh = now

        open_markets = self._open_markets()
        if not open_markets:
            logging.info("All markets closed — skipping")
            self._update_status({"is_running": True, "trading_active": False})
            return

        pairs_to_analyze = [p for p in self.active_pairs if p in open_markets]
        if not pairs_to_analyze:
            logging.info(f"Selected pairs {self.active_pairs} not open right now")
            return

        logging.info(f"Analyzing: {', '.join(pairs_to_analyze)} | Strategy: {self.active_strategy_name}")

        for symbol in pairs_to_analyze:
            try:
                # ── Phase 2: News filter ──────────────────────────────
                if not self.news_filter.is_safe_to_trade(symbol):
                    logging.info(f"[{symbol}] Skipped — high-impact news window")
                    continue

                # ── Phase 2: Check positions AND pending orders ───────
                if self._count_positions(symbol) > 0:
                    logging.info(f"[{symbol}] Position/order already exists — skipping")
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

                self.execute_signal(symbol, signal, config)

                if self.firebase_enabled:
                    try:
                        self.alerts_ref.push({
                            **signal,
                            "alert_type":      f"{self.active_strategy_name.upper()}_SETUP",
                            "action_required": "AUTO_EXECUTED",
                            "sent_at":         datetime.now().isoformat(),
                        })
                        logging.info(f"[{symbol}] Alert pushed to Firebase")
                    except Exception as e:
                        logging.error(f"[{symbol}] Failed to push alert: {e}")

            except Exception as e:
                logging.error(f"[{symbol}] Error during analysis: {e}")
                traceback.print_exc()

        account = mt5.account_info()
        self._update_status({
            "is_running":       True,
            "trading_active":   True,
            "active_strategy":  self.active_strategy_name,
            "open_markets":     open_markets,
            "markets_analyzed": pairs_to_analyze,
            "balance":          account.balance if account else None,
            "equity":           account.equity  if account else None,
            "profit":           account.profit  if account else None,
            "open_positions":   self._count_positions(),
        })

    # ═══════════════════════════════════════════════════════════════
    # TRADE EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def execute_signal(self, symbol: str, signal: dict, config: dict):
        """Execute a signal with 1% risk-based lot sizing."""
        try:
            strategy_name = signal.get("strategy_name", self.active_strategy_name)

            market_status = self._refresh_market_status(force=True)
            if market_status.get(symbol) != "OPEN":
                logging.warning(f"[{symbol}] Market closed — cannot execute")
                return None

            account = mt5.account_info()
            tick    = mt5.symbol_info_tick(symbol)
            info    = mt5.symbol_info(symbol)
            if not all([account, tick, info]):
                logging.error(f"[{symbol}] Could not get account/tick/symbol info")
                return None

            direction = signal["direction"]
            entry     = signal["entry_price"]
            sl        = signal["suggested_sl"]
            tp        = signal["suggested_tp"]

            risk_per_trade = account.balance * 0.01
            price_risk     = abs(entry - sl)

            if price_risk == 0:
                logging.error(f"[{symbol}] Entry == SL — skipping")
                return None

            lot = risk_per_trade / (price_risk * info.trade_contract_size)
            lot = max(info.volume_min, min(info.volume_max,
                      round(lot / info.volume_step) * info.volume_step))

            logging.info(
                f"[{symbol}] Risk sizing: Account=${account.balance:.2f} | "
                f"Risk=${risk_per_trade:.2f} | Lot={lot:.2f}"
            )

            order_type_map = {
                ("BUY",  "STOP"):   mt5.ORDER_TYPE_BUY_STOP,
                ("SELL", "STOP"):   mt5.ORDER_TYPE_SELL_STOP,
                ("BUY",  "MARKET"): mt5.ORDER_TYPE_BUY,
                ("SELL", "MARKET"): mt5.ORDER_TYPE_SELL,
            }

            sig_type   = signal.get("order_type", "MARKET")
            order_type = order_type_map.get(
                (direction, sig_type),
                mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
            )

            price = entry if sig_type == "STOP" else (tick.ask if direction == "BUY" else tick.bid)

            # Phase 2 fix: Round to symbol digits so XAUUSD doesn't drop SL/TP
            price = round(price, info.digits)
            sl    = round(sl, info.digits)
            tp    = round(tp, info.digits)

            xauusd_manual = symbol == "XAUUSD"

            request = {
                "action":       mt5.TRADE_ACTION_PENDING if sig_type == "STOP" else mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       round(lot, 2),
                "type":         order_type,
                "price":        price,
                "sl":           0.0 if xauusd_manual else sl,
                "tp":           0.0 if xauusd_manual else tp,
                "deviation":    20,
                "magic":        300001,
                "comment":      f"brave_{strategy_name[:10]}",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC if sig_type == "MARKET" else mt5.ORDER_FILLING_RETURN,
            }

            if xauusd_manual:
                logging.warning(
                    f"[{symbol}] set manually in MT5 -> "
                    f"Direction: {direction} | Entry: {price} | SL: {sl} | TP: {tp}"
                )

            result = mt5.order_send(request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"[{symbol}] Order placed — ticket: {getattr(result, 'order', 0)}")
                self._log_trade_to_csv(symbol, signal, lot, result)
                if self.firebase_enabled:
                    # Log execution details for remote monitoring and later CSV analysis
                    try:
                        self.trades_ref.push({
                            "timestamp": datetime.now().isoformat(),
                            "symbol":    symbol,
                            "type":      f"{direction}_{sig_type}",
                            "entry":     price,
                            "sl":        sl,
                            "tp":        tp,
                            "lot":       lot,
                            "ticket":    getattr(result, "order", 0),
                            "strategy":  strategy_name,
                            "session":   self._get_current_session(),
                        })
                    except Exception as e:
                        logging.error(f"[{symbol}] Failed to log trade: {e}")
                return result
            else:
                err = result.comment if result else "No result from order_send"
                logging.error(f"[{symbol}] Order failed: {err}")
                return None

        except Exception as e:
            logging.error(f"[{symbol}] Execution error: {e}")
            traceback.print_exc()
            return None

    def _log_trade_to_csv(self, symbol: str, signal: dict, lot: float, result) -> None:
        import csv
        from pathlib import Path

        os.makedirs("logs/trades", exist_ok=True)
        today    = datetime.now().strftime("%Y-%m-%d")
        csv_path = f"logs/trades/trades_{today}.csv"

        headers = [
            "timestamp", "symbol", "strategy", "direction", "order_type",
            "entry_price", "sl", "tp", "lot", "risk_reward",
            "ticket", "session", "account_balance", "account_equity"
        ]

        account = mt5.account_info()
        row = {
            "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":          symbol,
            "strategy":        signal.get("strategy_name", self.active_strategy_name),
            "direction":       signal["direction"],
            "order_type":      signal.get("order_type", "MARKET"),
            "entry_price":     signal["entry_price"],
            "sl":              signal["suggested_sl"],
            "tp":              signal["suggested_tp"],
            "lot":             lot,
            "risk_reward":     signal["risk_reward_ratio"],
            "ticket":          getattr(result, "order", 0),
            "session":         self._get_current_session(),
            "account_balance": account.balance if account else None,
            "account_equity":  account.equity  if account else None,
        }

        file_exists = Path(csv_path).exists()
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logging.info(f"[{symbol}] Trade logged → {csv_path}")

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _count_positions(self, symbol: str | None = None) -> int:
        """
        Phase 2 fix: counts both active positions AND pending orders.
        Previously only checked positions — caused duplicate pending
        orders to stack on the same signal every 60-second cycle.
        """
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        orders    = mt5.orders_get(symbol=symbol)    if symbol else mt5.orders_get()

        return (len(positions) if positions else 0) + (len(orders) if orders else 0)

    def _get_current_session(self) -> str:
        """UTC session windows mapped for performance attribution."""
        now_utc = datetime.now(timezone.utc).time()
        
        if self.LONDON_OPEN <= now_utc < self.LONDON_CLOSE:
            return "LONDON"
        if self.NY_OPEN <= now_utc < self.NY_CLOSE:
            return "NEW_YORK"
        
        return "OTHER"

    def _get_config(self) -> dict:
        if self.firebase_enabled:
            try:
                config = self.config_ref.get()
                if config:
                    return config
            except Exception:
                pass
        return {
            "symbols":          SYMBOLS,
            "timeframe":        TIMEFRAME,
            "lot_size":         LOT_SIZE,
            "max_trades":       MAX_TRADES,
            "stop_loss_pips":   STOP_LOSS_PIPS,
            "take_profit_pips": TAKE_PROFIT_PIPS,
        }

    def _update_status(self, data: dict) -> None:
        if not self.firebase_enabled:
            return
        try:
            data["last_updated"] = datetime.now().isoformat()
            self.status_ref.update(data)
        except Exception as e:
            # Phase 2: log at DEBUG level — Firebase SSE reconnects are noisy
            logging.debug(f"Status update failed (will retry): {e}")

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def run(self) -> None:
        logging.info("=" * 60)
        logging.info("BRAVE BOT — Phase 2")
        logging.info(f"Check interval:       {self.CHECK_INTERVAL}s")
        logging.info(f"Health interval:      {self.HEALTH_CHECK_INTERVAL}s")
        logging.info(f"News filter:          ENABLED (±{30} min around high-impact events)")
        logging.info(f"Strategies available: {list(STRATEGY_REGISTRY.keys())}")
        logging.info("=" * 60)

        self.is_running = True
        self._update_status({"is_running": True})
        self._health_check()

        self.active_pairs      = self._select_pairs(max_pairs=3)
        self.last_pair_refresh = datetime.now()

        try:
            while True:
                if not self.is_running:
                    logging.info("Bot paused — waiting for start command...")
                    time_module.sleep(self.CHECK_INTERVAL)
                    continue

                elapsed = (datetime.now() - self.last_health_check).total_seconds()
                if elapsed >= self.HEALTH_CHECK_INTERVAL:
                    self._health_check()

                config = self._get_config()
                self._check_signals(config)

                logging.info(f"Sleeping {self.CHECK_INTERVAL}s...\n")
                time_module.sleep(self.CHECK_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Bot stopped (Ctrl+C)")
        except Exception as e:
            logging.error(f"Critical error: {e}")
            traceback.print_exc()
        finally:
            self.is_running = False
            self._update_status({"is_running": False})
            mt5.shutdown()
            logging.info("Bot shut down cleanly")


if __name__ == "__main__":
    try:
        bot = BraveBot()
        bot.run()
    except Exception as e:
        logging.error(f"Failed to start: {e}")
        traceback.print_exc()