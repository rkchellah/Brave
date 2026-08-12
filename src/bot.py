"""
bot.py — Brave v3.0 runtime.

Owns everything around the LangGraph pipeline: MT5 and Firebase connections,
market/session state, pair selection, health checks and the main loop.
The trading decision itself lives in graph.py; order placement lives in
trade_executor.py.

Each main loop iteration:
    1. Refresh fast status (balance/equity/positions) for the mobile app
    2. Every CHECK_INTERVAL seconds:
       a. Health check (every HEALTH_CHECK_INTERVAL)
       b. Consume signals the user confirmed in the app (MANUAL mode)
       c. Daily loss limiter
       d. Re-score tradable pairs (every PAIR_REFRESH_INTERVAL)
       e. Run the graph on each open pair that passes the news filter
"""

import logging
import logging.handlers
import os
import sys
import time as time_module
import traceback
from datetime import datetime, timezone

import MetaTrader5 as mt5
import firebase_admin
import numpy as np
from firebase_admin import credentials, db

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (  # noqa: E402
    DAILY_LOSS_LIMIT_PCT,
    EXECUTION_MODE,
    FIREBASE_CREDENTIALS,
    FIREBASE_DATABASE_URL,
    LOT_SIZE,
    MAX_TRADES,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    SIGNAL_EXPIRY_SECONDS,
    STOP_LOSS_PIPS,
    SYMBOLS,
    TAKE_PROFIT_PIPS,
    TIMEFRAME,
    USER_ID,
    ConfigError,
    validate_config,
)
import credentials as mt5_credentials  # noqa: E402
from graph import run_brave_graph  # noqa: E402
from news_filter import NewsFilter  # noqa: E402
from trade_executor import place_order  # noqa: E402
from trade_logger import attach_ticket, log_detect_attempt, mark_hitl_outcome  # noqa: E402

BOT_VERSION = "Brave v3.0"

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
log = logging.getLogger(__name__)


class BraveBot:
    CHECK_INTERVAL        = 60     # seconds between signal checks
    HEALTH_CHECK_INTERVAL = 600    # seconds between health checks
    PAIR_REFRESH_INTERVAL = 3600   # seconds between pair re-scoring
    MARKET_CACHE_SECONDS  = 60     # market status cache lifetime
    MT5_RETRY_ATTEMPTS    = 3      # connection attempts at startup

    def __init__(self):
        # firebase_enabled MUST be set first — the Firebase listener thread
        # can fire before __init__ completes and call _update_status.
        self.firebase_enabled      = False
        self.is_running            = False
        self.last_health_check     = datetime.now()
        self.last_market_check     = None
        self.last_pair_refresh     = None
        self.market_status_cache   = {}
        self.active_pairs          = []
        self.execution_mode        = EXECUTION_MODE
        self._session_start_equity = None
        self._last_session_date    = None
        self._mt5_credentials      = None   # (login, password, server, source), resolved at startup
        self._last_health          = None   # previous health dict — for AutoTrading edge alerts

        self.news_filter = NewsFilter()

        log.info("=" * 60)
        log.info(f"INITIALIZING {BOT_VERSION}")
        log.info("=" * 60)

        for warning in validate_config():
            log.warning(f"Config: {warning}")

        # Firebase comes up first: broker credentials live in mt5_config and
        # must be readable before the MT5 login attempt.
        self.firebase_enabled = self._init_firebase()
        if not self.firebase_enabled:
            log.warning("Firebase disabled — running in LOCAL MODE (no app control, AUTO only)")

        if not self._init_mt5():
            raise RuntimeError("MT5 initialization failed — see log for details")

        log.info("Brave bot ready")

    # ═══════════════════════════════════════════════════════════════
    # INITIALIZATION
    # ═══════════════════════════════════════════════════════════════

    def _resolve_mt5_credentials(self) -> tuple[int, str, str, str]:
        """
        Broker credentials, resolved once per process and cached.

        A mid-run reconnect must reuse the same account — silently switching
        brokers while positions are open would leave them unmonitored. Changing
        the account therefore requires a bot restart, which is what the app
        tells the user.
        """
        if self._mt5_credentials is None:
            self._mt5_credentials = self._read_mt5_credentials()
        return self._mt5_credentials

    def _read_mt5_credentials(self) -> tuple[int, str, str, str]:
        """
        Environment or terminal prompt first, then Firebase, then config.py.

        The password is not stored anywhere on this machine: it is typed at
        startup with echo off, or supplied by MT5_PASSWORD for unattended runs.
        The remote/config paths are the last resort for a headless run with no
        environment set, and Firebase wins there only when login, password and
        server are all present and usable — a half-filled node would otherwise
        lock the bot out of the account with no way to recover from the app.

        Returns (login, password, server, source). The password is never logged.
        """
        from_env = mt5_credentials.env_credentials()
        if from_env is not None:
            return from_env

        if mt5_credentials.can_prompt():
            return mt5_credentials.prompt_credentials(
                default_login=MT5_LOGIN,
                default_server=MT5_SERVER,
            )

        log.warning(
            "No terminal to prompt on and MT5_LOGIN/MT5_PASSWORD/MT5_SERVER not all set "
            "— falling back to stored credentials"
        )

        fallback = (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, "config.py fallback")

        if not self.firebase_enabled:
            return fallback

        try:
            remote = self.mt5_config_ref.get()
        except Exception as e:
            log.warning(f"Could not read mt5_config from Firebase ({e}) — using config.py fallback")
            return fallback

        if not isinstance(remote, dict) or not remote:
            log.info("No mt5_config in Firebase — using config.py fallback")
            return fallback

        raw_login  = remote.get("login")
        password   = str(remote.get("password") or "").strip()
        server     = str(remote.get("server") or "").strip()

        try:
            login = int(str(raw_login).strip())
        except (TypeError, ValueError):
            login = 0

        missing = [
            name for name, ok in (
                ("login", login > 0), ("password", bool(password)), ("server", bool(server)),
            ) if not ok
        ]
        if missing:
            log.warning(
                f"mt5_config in Firebase is incomplete (missing/invalid: {', '.join(missing)}) "
                "— using config.py fallback"
            )
            return fallback

        return login, password, server, "Firebase"

    def _init_mt5(self) -> bool:
        """Connect to MT5, retrying briefly — the terminal is often still booting."""
        login, password, server, source = self._resolve_mt5_credentials()
        if login <= 0 or not password or not server:
            log.error(
                f"Broker credentials incomplete (source: {source}) — set MT5_LOGIN, "
                "MT5_PASSWORD and MT5_SERVER, or run the bot from a terminal so it can prompt"
            )
            return False

        fingerprint = mt5_credentials.password_fingerprint(login, server, password)
        log.info(
            f"MT5 credentials source: {source} | Login: {login} | Server: {server} "
            f"| Password fingerprint: {fingerprint}"
        )

        for attempt in range(1, self.MT5_RETRY_ATTEMPTS + 1):
            try:
                if not mt5.initialize():
                    log.error(f"MT5 initialize failed (attempt {attempt}): {mt5.last_error()}")
                elif not mt5.login(login, password=password, server=server):
                    log.error(f"MT5 login failed (attempt {attempt}): {mt5.last_error()}")
                    mt5.shutdown()
                else:
                    info = mt5.account_info()
                    if info is None:
                        log.error(f"MT5 connected but account info unavailable (attempt {attempt})")
                    else:
                        log.info(f"MT5 connected — Account: {info.login} | Balance: ${info.balance:.2f}")
                        if not info.trade_allowed:
                            log.warning("Account reports trading NOT allowed — orders will be rejected")
                        return True
            except Exception as e:
                log.error(f"MT5 connection error (attempt {attempt}): {e}")

            if attempt < self.MT5_RETRY_ATTEMPTS:
                time_module.sleep(5)

        return False

    def _reconnect_mt5(self) -> bool:
        """Re-establish a dropped terminal connection mid-run."""
        log.warning("MT5 connection lost — attempting to reconnect...")
        try:
            mt5.shutdown()
        except Exception:
            pass
        return self._init_mt5()

    def _init_firebase(self) -> bool:
        if not os.path.exists(FIREBASE_CREDENTIALS):
            log.error(f"Firebase credentials '{FIREBASE_CREDENTIALS}' not found")
            return False

        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(FIREBASE_CREDENTIALS)
                firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})

            base = f"users/{USER_ID}"
            self.config_ref          = db.reference(f"{base}/bot_config")
            self.brave_config_ref    = db.reference(f"{base}/brave_config")
            self.status_ref          = db.reference(f"{base}/bot_status")
            self.command_ref         = db.reference(f"{base}/commands")
            self.health_ref          = db.reference(f"{base}/health")
            self.alerts_ref          = db.reference(f"{base}/alerts")
            self.pending_signals_ref = db.reference(f"{base}/pending_signals")
            self.news_analysis_ref   = db.reference(f"{base}/news_analysis")
            self.mt5_config_ref      = db.reference(f"{base}/mt5_config")
            self.market_status_ref   = db.reference(f"{base}/market_status")

            self.config_ref.get()  # connection test — raises if unreachable

            self._seed_defaults()

            # Listeners run on background threads; both handlers swallow their
            # own exceptions so an SSE hiccup can't kill the stream.
            self.command_ref.listen(self._handle_command)
            self.brave_config_ref.listen(self._handle_config_change)

            self._update_status({
                "is_running":      False,
                "bot_version":     BOT_VERSION,
                "active_strategy": "flow",
            })

            log.info("Firebase connected — command listener active")
            return True

        except Exception as e:
            log.error(f"Firebase init failed: {e}")
            traceback.print_exc()
            return False

    def _seed_defaults(self) -> None:
        """Create bot_config / brave_config on first run, and backfill new keys."""
        brave_config = self.brave_config_ref.get()
        if not brave_config:
            self.brave_config_ref.set({
                "active_strategy": "flow",
                "execution_mode":  EXECUTION_MODE,
                "strategy_config": {"flow": {"enabled": True, "max_trades": MAX_TRADES}},
                "last_switched":   None,
            })
            log.info("brave_config initialized in Firebase")
        else:
            backfill = {}
            if "execution_mode" not in brave_config:
                backfill["execution_mode"] = EXECUTION_MODE
            if "strategy_config" not in brave_config:
                backfill["strategy_config"] = {"flow": {"enabled": True, "max_trades": MAX_TRADES}}
            if "active_strategy" not in brave_config:
                backfill["active_strategy"] = "flow"
            if backfill:
                self.brave_config_ref.update(backfill)

        if not self.config_ref.get():
            self.config_ref.set({
                "symbols":          SYMBOLS,
                "timeframe":        TIMEFRAME,
                "lot_size":         LOT_SIZE,
                "max_trades":       MAX_TRADES,
                "stop_loss_pips":   STOP_LOSS_PIPS,
                "take_profit_pips": TAKE_PROFIT_PIPS,
            })

    # ═══════════════════════════════════════════════════════════════
    # FIREBASE LISTENERS
    # ═══════════════════════════════════════════════════════════════

    def _handle_command(self, event) -> None:
        try:
            command = event.data
            if not isinstance(command, dict):
                return

            action = str(command.get("action", "")).lower()
            if action == "start":
                self.is_running = True
                self._update_status({"is_running": True, "paused_reason": None,
                                     "last_started": datetime.now().isoformat()})
                log.info("Bot STARTED via command")
            elif action == "stop":
                self.is_running = False
                self._update_status({"is_running": False,
                                     "last_stopped": datetime.now().isoformat()})
                log.info("Bot STOPPED via command")
            elif action:
                log.warning(f"Unknown command ignored: {action}")

        except Exception as e:
            log.error(f"Error handling command: {e}")

    def _handle_config_change(self, event) -> None:
        """Mirror app config changes into status so the dashboard updates instantly."""
        try:
            config = event.data
            if not isinstance(config, dict):
                return

            mode = str(config.get("execution_mode", self.execution_mode)).upper()
            if mode in ("AUTO", "MANUAL") and mode != self.execution_mode:
                self.execution_mode = mode
                log.info(f"Execution mode changed → {mode}")

            if self._flow_enabled(config):
                self._update_status({"active_strategy": "flow"})
            else:
                self._update_status({"active_strategy": "none"})
                log.info("Flow disabled from app — no new signals will be taken")

        except Exception as e:
            log.error(f"Error handling config change: {e}")

    @staticmethod
    def _flow_enabled(brave_config: dict | None) -> bool:
        """Flow is on unless the app explicitly disabled it."""
        if not isinstance(brave_config, dict):
            return True
        strategy_config = brave_config.get("strategy_config")
        if not isinstance(strategy_config, dict):
            return True
        flow_cfg = strategy_config.get("flow")
        if not isinstance(flow_cfg, dict):
            return True
        return flow_cfg.get("enabled", True) is not False

    # ═══════════════════════════════════════════════════════════════
    # MARKET STATUS
    # ═══════════════════════════════════════════════════════════════

    def _refresh_market_status(self, force: bool = False) -> dict:
        now   = datetime.now()
        stale = (
            self.last_market_check is None
            or (now - self.last_market_check).total_seconds() >= self.MARKET_CACHE_SECONDS
        )
        if not (force or stale):
            return self.market_status_cache

        status = {}
        for symbol in SYMBOLS:
            try:
                info = mt5.symbol_info(symbol)
                if info is None:
                    status[symbol] = "UNAVAILABLE"
                elif info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                    status[symbol] = "CLOSED"
                elif info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
                    tick = mt5.symbol_info_tick(symbol)
                    fresh = tick and (datetime.now().timestamp() - tick.time) < 300
                    status[symbol] = "OPEN" if fresh else "CLOSED"
                else:
                    status[symbol] = "RESTRICTED"
            except Exception as e:
                log.debug(f"[{symbol}] Market status check failed: {e}")
                status[symbol] = "UNAVAILABLE"

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
                log.debug(f"Failed to update market status: {e}")

        return status

    def _open_markets(self) -> list[str]:
        return [s for s, v in self._refresh_market_status().items() if v == "OPEN"]

    # ═══════════════════════════════════════════════════════════════
    # PAIR SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_pairs(self, max_pairs: int = 3) -> list[str]:
        """Score symbols on spread and volatility, keep the best few."""
        log.info("Selecting trading pairs...")
        scored = []

        for symbol in SYMBOLS:
            try:
                info = mt5.symbol_info(symbol)
                if info is None or info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                    continue

                tick = mt5.symbol_info_tick(symbol)
                if tick is None or tick.ask <= 0 or tick.bid <= 0:
                    continue

                point = info.point
                if info.digits in (5, 3):
                    point *= 10  # 5/3-digit brokers quote fractional pips
                if point <= 0:
                    continue

                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 20)
                if rates is None or len(rates) < 20:
                    continue

                spread           = (tick.ask - tick.bid) / point
                avg_range        = float(np.mean([(r["high"] - r["low"]) / point for r in rates]))
                spread_score     = max(0.0, 10.0 - spread)
                volatility_score = min(avg_range / 10.0, 10.0)
                major_bonus      = 3.0 if symbol in ("EURUSD", "GBPUSD", "XAUUSD") else 0.0

                scored.append({"symbol": symbol, "score": spread_score + volatility_score + major_bonus})

            except Exception as e:
                log.debug(f"[{symbol}] Pair scoring failed: {e}")
                continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        selected = [p["symbol"] for p in scored[:max_pairs]]

        if not selected:
            log.warning("No pairs scored — falling back to EURUSD")
            return ["EURUSD"]

        log.info(f"Selected pairs: {', '.join(selected)}")
        return selected

    # ═══════════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════

    def _health_check(self) -> bool:
        log.info("Running health check...")

        health: dict = {
            # UTC ISO timestamp — app uses this to refuse stale mt5_connected
            "timestamp":                       datetime.now(timezone.utc).isoformat(),
            "mt5_connected":                   False,
            "firebase_connected":              False,
            "account_trade_allowed":           False,
            # Local MT5 toolbar AutoTrading toggle — distinct from account.trade_allowed
            "terminal_autotrading_enabled":    False,
            "symbols_available":               {},
            "market_status":                   {},
            "active_strategy":                 "flow",
            "execution_mode":                  self._get_execution_mode(),
            "bot_version":                     BOT_VERSION,
            "status":                          "UNKNOWN",
        }

        try:
            terminal = mt5.terminal_info()
            if terminal:
                health["mt5_connected"]                = True
                health["mt5_connected_to_broker"]      = terminal.connected
                health["terminal_autotrading_enabled"] = bool(terminal.trade_allowed)
            elif not self._reconnect_mt5():
                log.error("Health check: MT5 unreachable and reconnect failed")
            else:
                health["mt5_connected"] = True
                terminal = mt5.terminal_info()
                if terminal:
                    health["mt5_connected_to_broker"]      = terminal.connected
                    health["terminal_autotrading_enabled"] = bool(terminal.trade_allowed)

            if self.firebase_enabled:
                try:
                    self.status_ref.get()
                    health["firebase_connected"] = True
                except Exception as e:
                    log.warning(f"Health check: Firebase unreachable — {e}")

            health["market_status"] = self._refresh_market_status(force=True)
            health["symbols_available"] = {
                symbol: mt5.symbol_info_tick(symbol) is not None for symbol in SYMBOLS
            }

            account = mt5.account_info()
            if account:
                health["account_trade_allowed"] = account.trade_allowed
                health["balance"]               = account.balance
                health["equity"]                = account.equity

        except Exception as e:
            log.error(f"Health check error: {e}")

        healthy = all((
            health["mt5_connected"],
            health["firebase_connected"],
            health["account_trade_allowed"],
            health["terminal_autotrading_enabled"],
        ))
        health["status"] = "HEALTHY" if healthy else "DEGRADED"
        log.info("Health check PASSED" if healthy else "Health check DEGRADED — review logs")
        if not health["terminal_autotrading_enabled"]:
            log.warning(
                "Health check: MT5 terminal AutoTrading is OFF "
                "(toolbar toggle) — orders will be rejected"
            )

        # Loud alert only on True → False edge (not every DEGRADED cycle)
        prev = self._last_health or {}
        if (
            prev.get("terminal_autotrading_enabled") is True
            and health["terminal_autotrading_enabled"] is False
        ):
            log.error("MT5 AutoTrading disabled at terminal — pushing alert")
            self._push_alert({
                "alert_type": "AUTOTRADING_DISABLED",
                "symbol":     "SYSTEM",
                "error": (
                    "MT5 AutoTrading disabled at terminal — "
                    "no trades will execute until re-enabled."
                ),
            })

        if self.firebase_enabled:
            try:
                self.health_ref.set(health)
            except Exception as e:
                log.debug(f"Failed to push health: {e}")

        self._last_health = health
        self.last_health_check = datetime.now()
        return healthy

    # ═══════════════════════════════════════════════════════════════
    # SIGNAL CHECKING
    # ═══════════════════════════════════════════════════════════════

    def _check_signals(self, config: dict) -> None:
        log.info(f"Checking signals... ({datetime.now().strftime('%H:%M:%S')})")

        if not self._flow_enabled(self._get_brave_config()):
            log.info("Flow disabled in app settings — skipping")
            self._update_status({"is_running": True, "trading_active": False})
            return

        if self._daily_loss_limit_hit():
            return

        if (
            self.last_pair_refresh is None
            or (datetime.now() - self.last_pair_refresh).total_seconds() >= self.PAIR_REFRESH_INTERVAL
        ):
            self.active_pairs      = self._select_pairs(max_pairs=3)
            self.last_pair_refresh = datetime.now()

        open_markets = self._open_markets()
        if not open_markets:
            log.info("All markets closed — skipping")
            self._update_status({"is_running": True, "trading_active": False})
            return

        pairs_to_analyze = [p for p in self.active_pairs if p in open_markets]
        if not pairs_to_analyze:
            log.info(f"Selected pairs {self.active_pairs} not open right now")
            self._update_status({"is_running": True, "trading_active": False})
            return

        mode = self._get_execution_mode()
        log.info(f"Analyzing: {', '.join(pairs_to_analyze)} | Strategy: Flow | Mode: {mode}")

        firebase_refs = {
            "alerts_ref":          getattr(self, "alerts_ref", None),
            "pending_signals_ref": getattr(self, "pending_signals_ref", None),
            "news_analysis_ref":   getattr(self, "news_analysis_ref", None),
        } if self.firebase_enabled else {}

        for symbol in pairs_to_analyze:
            try:
                if not self.news_filter.is_safe_to_trade(symbol):
                    log.info(f"[{symbol}] Skipped — high-impact news window")
                    # Same schema as Flow outside_session rows — gate before DETECT
                    log_detect_attempt({
                        "symbol":            symbol,
                        "h1_trend":          "",
                        "aoi_distance_pips": "",
                        "sweep_reclaim":     "",
                        "outcome":           "no-signal",
                        "reason":            "news_filter_pause",
                    })
                    continue

                result = run_brave_graph(symbol, config, firebase_refs, execution_mode=mode)

                if result.get("hitl_required"):
                    log.info(f"[{symbol}] Awaiting confirmation in app")
                elif result.get("executed"):
                    log.info(f"[{symbol}] Trade executed")
                elif result.get("abort"):
                    log.info(f"[{symbol}] Aborted: {result.get('abort_reason')}")
                elif not result.get("risk_ok"):
                    log.info(f"[{symbol}] Blocked: {result.get('risk_reason')}")

            except Exception as e:
                log.error(f"[{symbol}] Error during analysis: {e}")
                traceback.print_exc()
                # Guarantees a CSV row even if the graph never reached Flow.analyze()
                log_detect_attempt({
                    "symbol":            symbol,
                    "h1_trend":          "",
                    "aoi_distance_pips": "",
                    "sweep_reclaim":     "",
                    "outcome":           "no-signal",
                    "reason":            f"analysis_error: {e}",
                })

        account = mt5.account_info()
        self._update_status({
            "is_running":       True,
            "trading_active":   True,
            "active_strategy":  "flow",
            "execution_mode":   mode,
            "open_markets":     open_markets,
            "markets_analyzed": pairs_to_analyze,
            "balance":          account.balance if account else None,
            "equity":           account.equity  if account else None,
            "profit":           account.profit  if account else None,
            "open_positions":   self._count_positions(),
        })

    def _daily_loss_limit_hit(self) -> bool:
        """Pause the bot for the day once the session drawdown limit is reached."""
        account = mt5.account_info()
        if account is None:
            log.warning("No account info — skipping loss-limit check")
            return False

        session_pnl, _ = self._get_session_metrics(account)
        if not self._session_start_equity:
            return False

        loss_limit = -(self._session_start_equity * DAILY_LOSS_LIMIT_PCT)
        if session_pnl > loss_limit:
            return False

        log.warning(
            f"Daily loss limit hit (${session_pnl:.2f} <= ${loss_limit:.2f}) — bot paused for today"
        )
        self.is_running = False
        self._update_status({
            "is_running":     False,
            "trading_active": False,
            "paused_reason":  "DAILY_LOSS_LIMIT",
            "pnl_at_pause":   session_pnl,
        })
        return True

    # ═══════════════════════════════════════════════════════════════
    # MANUAL MODE — consume signals confirmed in the app
    # ═══════════════════════════════════════════════════════════════

    def _process_pending_signals(self) -> None:
        """
        Execute signals the user confirmed in the app, and expire stale ones.

        The app writes status CONFIRMED/REJECTED onto users/<id>/pending_signals;
        nothing else acts on those, so this is what makes MANUAL mode work.
        """
        if not self.firebase_enabled:
            return

        try:
            pending = self.pending_signals_ref.get()
        except Exception as e:
            log.debug(f"Could not read pending signals: {e}")
            return

        if not isinstance(pending, dict):
            return

        now = datetime.now(timezone.utc).timestamp()

        for key, signal in pending.items():
            if not isinstance(signal, dict):
                continue

            status = str(signal.get("status", "")).upper()

            if status == "PENDING":
                expires_at = signal.get("expires_at")
                try:
                    expired = expires_at is not None and float(expires_at) < now
                except (TypeError, ValueError):
                    expired = True  # Unreadable timestamp — don't leave it hanging
                if expired:
                    log.info(f"[{signal.get('symbol', '?')}] Signal expired — no response in "
                             f"{SIGNAL_EXPIRY_SECONDS}s")
                    self._set_signal_status(key, "EXPIRED")
                    if signal.get("symbol"):
                        mark_hitl_outcome(signal["symbol"], "EXPIRED")
                continue

            if status != "CONFIRMED":
                continue

            symbol = signal.get("symbol")
            if not symbol:
                self._set_signal_status(key, "FAILED", error="Signal has no symbol")
                continue

            # A confirmation that arrived after expiry is stale — the entry
            # price it was judged on is minutes old.
            try:
                if signal.get("expires_at") is not None and float(signal["expires_at"]) < now:
                    log.warning(f"[{symbol}] Confirmation arrived after expiry — not executing")
                    self._set_signal_status(key, "EXPIRED")
                    continue
            except (TypeError, ValueError):
                pass

            # Claim the signal before sending the order. If this write fails we
            # skip it: executing without being able to record the outcome would
            # re-execute the same signal on the next cycle.
            if not self._claim_signal(key):
                log.warning(f"[{symbol}] Could not claim signal — skipping this cycle")
                continue

            log.info(f"[{symbol}] User confirmed signal — executing")
            result = place_order(symbol, signal, self._get_config(),
                                 comment=f"Brave Manual {signal.get('direction', '')}")

            if result["ok"]:
                self._set_signal_status(key, "EXECUTED", ticket=result["ticket"],
                                        filled_price=result["price"], lot=result["lot"])
                # Graph already wrote a HITL row with blank ticket — attach the fill
                attach_ticket(symbol, result["ticket"], outcome="EXECUTED")
                self._push_alert({
                    **signal,
                    "alert_type":   "TRADE_EXECUTED",
                    "ticket":       result["ticket"],
                    "filled_price": result["price"],
                    "lot":          result["lot"],
                    "strategy":     signal.get("strategy_name", "Flow"),
                })
            else:
                self._set_signal_status(key, "FAILED", error=result["error"])
                mark_hitl_outcome(symbol, "EXECUTION_FAILED")
                self._push_alert({
                    **signal,
                    "alert_type": "EXECUTION_FAILED",
                    "error":      result["error"],
                })

    def _claim_signal(self, key: str) -> bool:
        """Mark a signal EXECUTING so a second cycle can't pick it up. False if the write failed."""
        try:
            self.pending_signals_ref.child(key).update({
                "status":      "EXECUTING",
                "claimed_at":  datetime.now(timezone.utc).isoformat(),
            })
            return True
        except Exception as e:
            log.error(f"Could not claim signal {key}: {e}")
            return False

    def _set_signal_status(self, key: str, status: str, **extra) -> None:
        try:
            self.pending_signals_ref.child(key).update({
                "status":       status,
                "resolved_at":  datetime.now(timezone.utc).isoformat(),
                **extra,
            })
        except Exception as e:
            log.error(f"Could not update signal {key} to {status}: {e}")

    def _push_alert(self, payload: dict) -> None:
        if not self.firebase_enabled:
            return
        try:
            self.alerts_ref.push({**payload, "sent_at": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            log.debug(f"Alert push failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════

    def _get_session_metrics(self, account) -> tuple[float, float]:
        """Session P&L against the equity the day opened at."""
        if not account:
            return 0.0, 0.0

        today = datetime.now().date().isoformat()
        if self._last_session_date != today or self._session_start_equity is None:
            self._session_start_equity = account.equity
            self._last_session_date    = today
            log.info(f"Daily session started — Equity: ${self._session_start_equity:.2f}")

        session_pnl = account.equity - self._session_start_equity
        session_pct = (session_pnl / self._session_start_equity * 100) if self._session_start_equity else 0.0
        return session_pnl, session_pct

    def _update_fast_status(self) -> None:
        if not self.firebase_enabled:
            return
        try:
            account = mt5.account_info()
            if not account:
                return
            session_pnl, session_pct = self._get_session_metrics(account)
            self.status_ref.update({
                "balance":         account.balance,
                "equity":          account.equity,
                "profit":          account.profit,
                "open_positions":  self._count_positions(),
                "session_pnl":     session_pnl,
                "session_pnl_pct": session_pct,
                "last_updated":    datetime.now().isoformat(),
            })
        except Exception as e:
            log.debug(f"Fast status update failed: {e}")

    def _count_positions(self, symbol: str | None = None) -> int:
        """Open positions AND pending orders — pending ones still consume risk."""
        try:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            orders    = mt5.orders_get(symbol=symbol)    if symbol else mt5.orders_get()
            return len(positions or ()) + len(orders or ())
        except Exception as e:
            log.debug(f"Position count failed: {e}")
            return 0

    def _get_brave_config(self) -> dict:
        if not self.firebase_enabled:
            return {}
        try:
            config = self.brave_config_ref.get()
            return config if isinstance(config, dict) else {}
        except Exception as e:
            log.debug(f"Could not read brave_config: {e}")
            return {}

    def _get_config(self) -> dict:
        """Trading config from Firebase, falling back to config.py defaults."""
        defaults = {
            "symbols":          SYMBOLS,
            "timeframe":        TIMEFRAME,
            "lot_size":         LOT_SIZE,
            "max_trades":       MAX_TRADES,
            "stop_loss_pips":   STOP_LOSS_PIPS,
            "take_profit_pips": TAKE_PROFIT_PIPS,
        }
        if not self.firebase_enabled:
            return defaults
        try:
            config = self.config_ref.get()
            if isinstance(config, dict) and config:
                return {**defaults, **config}
        except Exception as e:
            log.debug(f"Could not read bot_config: {e}")
        return defaults

    def _get_execution_mode(self) -> str:
        """
        Execution mode from Firebase so the app can switch AUTO/MANUAL without
        a restart. Falls back to the last known mode when Firebase is down.
        """
        brave_config = self._get_brave_config()
        mode = str(brave_config.get("execution_mode", self.execution_mode)).upper()
        if mode in ("AUTO", "MANUAL"):
            self.execution_mode = mode
        return self.execution_mode

    def _update_status(self, data: dict) -> None:
        if not self.firebase_enabled:
            return
        try:
            self.status_ref.update({**data, "last_updated": datetime.now().isoformat()})
        except Exception as e:
            # Firebase SSE reconnects are noisy — debug level on purpose
            log.debug(f"Status update failed (will retry): {e}")

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def run(self) -> None:
        log.info("=" * 60)
        log.info(BOT_VERSION)
        log.info(f"Check interval:  {self.CHECK_INTERVAL}s")
        log.info(f"Health interval: {self.HEALTH_CHECK_INTERVAL}s")
        log.info(f"Execution mode:  {self._get_execution_mode()}")
        log.info("News filter:     ENABLED (±30 min around high-impact events)")
        log.info("=" * 60)

        self.is_running = True
        self._update_status({"is_running": True, "bot_version": BOT_VERSION})
        self._health_check()

        self.active_pairs      = self._select_pairs(max_pairs=3)
        self.last_pair_refresh = datetime.now()

        last_signal_check = 0.0

        try:
            while True:
                now_ts = time_module.time()

                if not self.is_running:
                    if now_ts - last_signal_check >= self.CHECK_INTERVAL:
                        log.info("Bot paused — waiting for start command...")
                        last_signal_check = now_ts
                    time_module.sleep(1)
                    continue

                self._update_fast_status()

                if now_ts - last_signal_check >= self.CHECK_INTERVAL:
                    try:
                        if (datetime.now() - self.last_health_check).total_seconds() >= self.HEALTH_CHECK_INTERVAL:
                            self._health_check()

                        # Act on app confirmations before looking for new signals
                        self._process_pending_signals()
                        self._check_signals(self._get_config())

                    except Exception as e:
                        # One bad cycle must not stop the bot
                        log.error(f"Cycle error: {e}")
                        traceback.print_exc()

                    log.info(f"Sleeping {self.CHECK_INTERVAL}s (fast syncing in background)...\n")
                    last_signal_check = time_module.time()

                time_module.sleep(1)

        except KeyboardInterrupt:
            log.info("Bot stopped (Ctrl+C)")
        except Exception as e:
            log.error(f"Critical error: {e}")
            traceback.print_exc()
        finally:
            self.is_running = False
            self._update_status({"is_running": False, "trading_active": False})
            try:
                mt5.shutdown()
            except Exception:
                pass
            log.info("Bot shut down cleanly")


if __name__ == "__main__":
    try:
        BraveBot().run()
    except ConfigError as e:
        log.error(str(e))
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to start: {e}")
        traceback.print_exc()
        sys.exit(1)
