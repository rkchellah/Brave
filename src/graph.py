"""
graph.py — Brave v3.0 LangGraph agent.

Pipeline:
    DETECT → ANALYSE → RISK_CHECK → EXECUTE or HITL

DETECT:     Flow strategy scans the symbol for a signal
ANALYSE:    DeepSeek reads Finnhub headlines, returns CONFIRM/OPPOSE/UNCERTAIN
RISK_CHECK: Pure Python — open positions, daily loss limit, signal sanity
EXECUTE:    Order placed through trade_executor, Firebase alert written
HITL:       Signal pushed to Firebase pending_signals for human confirmation

Routing:
    DETECT     → no signal            → END
    ANALYSE    → OPPOSE               → END
    ANALYSE    → CONFIRM/UNCERTAIN    → RISK_CHECK
    RISK_CHECK → fail                 → END
    RISK_CHECK → MANUAL mode          → HITL
    RISK_CHECK → pass + UNCERTAIN     → HITL   (genuine mixed news only)
    ANALYSE    → data unavailable + AUTO → END (skip, do not HITL)
    RISK_CHECK → pass + CONFIRM       → EXECUTE

Every node is total: it catches its own exceptions and returns a state that
routes safely to END rather than letting the loop in bot.py crash.
"""

import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Literal, TypedDict

import MetaTrader5 as mt5
from langgraph.graph import END, StateGraph

from config import DAILY_LOSS_LIMIT_PCT, MAX_TRADES, SIGNAL_EXPIRY_SECONDS
from flow import Flow
from news_fetcher import fetch_news_for_symbol, format_headlines_for_llm
from risk import daily_loss_limit_hit
from trade_executor import ExecutionError, place_order, validate_signal
from trade_logger import log_detect_attempt, log_signal

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL    = "deepseek-chat"
DEEPSEEK_TIMEOUT  = 30      # Seconds — the loop must not hang on the API
DEEPSEEK_ATTEMPTS = 2
VALID_VERDICTS    = ("CONFIRM", "OPPOSE", "UNCERTAIN")


# ── State ─────────────────────────────────────────────────────────────
class BraveState(TypedDict):
    symbol:           str
    config:           dict
    execution_mode:   str
    signal:           dict | None
    sentiment:        Literal["CONFIRM", "OPPOSE", "UNCERTAIN"] | None
    sentiment_reason: str
    headlines:        list[dict]
    analysis_unavailable: bool
    hitl_kind:        str
    risk_ok:          bool
    risk_reason:      str
    abort:            bool
    abort_reason:     str
    hitl_required:    bool
    executed:         bool
    mt5_ticket:       int
    fill_price:       float
    firebase:         dict


def _abort(state: BraveState, reason: str) -> BraveState:
    return {**state, "signal": state.get("signal"), "abort": True, "abort_reason": reason}


# ── Node 1: DETECT ────────────────────────────────────────────────────
def node_detect(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    log.info(f"[DETECT] Running Flow on {symbol}...")

    try:
        flow = Flow(state["config"])
        signal = flow.analyze(symbol)
    except Exception as e:
        log.error(f"[DETECT] {symbol}: Strategy error — {e}")
        traceback.print_exc()
        log_detect_attempt({
            "symbol":  symbol,
            "outcome": "no-signal",
            "reason":  f"strategy_error: {e}",
        })
        return _abort(state, f"Strategy error: {e}")

    if signal is None:
        log.info(f"[DETECT] {symbol}: No signal")
        _log_detect_attempt(symbol, flow.last_attempt)
        return _abort(state, "No signal from Flow")

    # Reject a malformed signal here rather than at the broker
    try:
        signal = validate_signal(symbol, signal)
    except ExecutionError as e:
        log.warning(f"[DETECT] {symbol}: Rejected malformed signal — {e}")
        attempt = dict(flow.last_attempt or {})
        attempt.update({
            "symbol":        symbol,
            "outcome":       "no-signal",
            "reason":        f"malformed_signal: {e}",
            "sweep_reclaim": attempt.get("sweep_reclaim") or "yes",
        })
        log_detect_attempt(attempt)
        return _abort(state, str(e))

    _log_detect_attempt(symbol, flow.last_attempt)
    log.info(f"[DETECT] {symbol}: {signal['direction']} signal — Entry {signal['entry_price']}")
    return {**state, "signal": signal, "abort": False, "abort_reason": ""}


def _log_detect_attempt(symbol: str, attempt: dict | None) -> None:
    """Write one flow_attempts.csv row for this DETECT (including near-misses)."""
    row = dict(attempt or {})
    row.setdefault("symbol", symbol)
    row.setdefault("outcome", "no-signal")
    log_detect_attempt(row)


# ── Node 2: ANALYSE ───────────────────────────────────────────────────
def node_analyse(state: BraveState) -> BraveState:
    symbol    = state["symbol"]
    direction = state["signal"]["direction"]
    log.info(f"[ANALYSE] Fetching news and querying DeepSeek for {symbol}...")

    articles: list[dict] = []
    data_ok = False
    try:
        articles, data_ok = fetch_news_for_symbol(symbol)
        if not data_ok:
            verdict, reason = "UNCERTAIN", "analysis_unavailable — no usable headlines"
            log.warning(f"[ANALYSE] {symbol}: {reason}")
        else:
            verdict, reason, data_ok = _ask_deepseek(symbol, direction, articles)
    except Exception as e:
        log.error(f"[ANALYSE] {symbol}: {e}")
        verdict, reason, data_ok = "UNCERTAIN", f"News analysis failed: {e}", False

    unavailable = not data_ok
    hitl_kind = "data_unavailable" if unavailable else (
        "genuine_uncertainty" if verdict == "UNCERTAIN" else ""
    )

    log.info(f"[ANALYSE] {symbol}: {verdict} — {reason}")

    next_state: BraveState = {
        **state,
        "sentiment":             verdict,
        "sentiment_reason":      reason,
        "headlines":             articles,
        "analysis_unavailable":  unavailable,
        "hitl_kind":             hitl_kind,
    }
    _publish_news_analysis(next_state, verdict, reason, direction, articles)
    if unavailable and str(state.get("execution_mode", "AUTO")).upper() == "AUTO":
        log.info(f"[ANALYSE] {symbol}: AUTO skip — analysis_unavailable (not a HITL judgment)")
        return _abort(next_state, "analysis_unavailable")
    return next_state


def _ask_deepseek(symbol: str, direction: str, articles: list[dict]) -> tuple[str, str, bool]:
    """
    Ask DeepSeek whether the news supports the trade.

    Returns (verdict, reason, data_ok). data_ok is False on tool/API failure
    so AUTO does not treat an outage as genuine UNCERTAIN.
    """
    from config import DEEPSEEK_API_KEY

    if not DEEPSEEK_API_KEY:
        return "UNCERTAIN", "DEEPSEEK_API_KEY not configured", False

    try:
        from openai import OpenAI
    except ImportError as e:
        return "UNCERTAIN", f"openai package not installed: {e}", False

    headlines = format_headlines_for_llm(symbol, articles)
    prompt = f"""You are a professional forex news analyst.

A trading system wants to place a {direction} trade on {symbol}.

{headlines}

Based only on the news above, does the current news sentiment CONFIRM, OPPOSE, or is it UNCERTAIN about a {direction} trade on {symbol}?

Rules:
- CONFIRM: news clearly supports the {direction} direction
- OPPOSE: news clearly contradicts the {direction} direction
- UNCERTAIN: news is mixed, neutral, or insufficient to judge

Reply with exactly one word on line 1: CONFIRM, OPPOSE, or UNCERTAIN
Reply with one sentence on line 2 explaining why.

Example:
CONFIRM
Fed hawkish tone supports dollar strength, aligning with the BUY signal on USDCHF.
"""

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, timeout=DEEPSEEK_TIMEOUT)
    last_error = "unknown error"

    for attempt in range(1, DEEPSEEK_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.1,
            )
            return _parse_verdict(symbol, response)

        except Exception as e:
            last_error = str(e)
            log.warning(f"[ANALYSE] {symbol}: DeepSeek attempt {attempt}/{DEEPSEEK_ATTEMPTS} failed — {e}")
            if attempt < DEEPSEEK_ATTEMPTS:
                time.sleep(2)

    return "UNCERTAIN", f"DeepSeek unavailable: {last_error}", False


def _parse_verdict(symbol: str, response) -> tuple[str, str, bool]:
    """Pull the verdict and reason out of a DeepSeek completion, defensively.

    data_ok is True only when DeepSeek produced a usable CONFIRM/OPPOSE/UNCERTAIN.
    Empty or unparseable replies are tool failure, not genuine mixed sentiment.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        return "UNCERTAIN", "DeepSeek returned no choices", False

    content = getattr(choices[0].message, "content", None)
    if not content or not content.strip():
        return "UNCERTAIN", "DeepSeek returned an empty response", False

    lines   = content.strip().split("\n", 1)
    verdict = lines[0].strip().upper().strip(".:*# ")
    reason  = lines[1].strip() if len(lines) > 1 else "No reason provided"

    if verdict not in VALID_VERDICTS:
        # Tolerate a verdict wrapped in prose before giving up
        match = next((v for v in VALID_VERDICTS if v in content.upper()), None)
        if match:
            return match, reason or content.strip()[:200], True
        log.warning(f"[ANALYSE] {symbol}: Unexpected verdict '{verdict}' — defaulting to UNCERTAIN")
        return "UNCERTAIN", f"Unexpected response: {content.strip()[:150]}", False

    return verdict, reason, True


def _publish_news_analysis(state: BraveState, verdict: str, reason: str,
                           direction: str, articles: list[dict]) -> None:
    """Publish the DeepSeek verdict and Finnhub headlines for the app Insights screen."""
    ref = state.get("firebase", {}).get("news_analysis_ref")
    if ref is None:
        return
    try:
        ref.child(state["symbol"]).set({
            "symbol":        state["symbol"],
            "direction":     direction,
            "verdict":       verdict,
            "reason":        reason,
            "headlines":     [
                {
                    "headline": a.get("headline", ""),
                    "summary":  a.get("summary", "")[:200],
                    "url":      a.get("url", ""),
                    "datetime": a.get("datetime", 0),
                }
                for a in articles[:5]
            ],
            "article_count":          len(articles),
            "news_source":            "Finnhub",
            "model":                  DEEPSEEK_MODEL,
            "analysis_unavailable":   bool(state.get("analysis_unavailable")),
            "hitl_kind":              state.get("hitl_kind") or "",
            "updated_at":             datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        log.error(f"[ANALYSE] Failed to publish news analysis: {e}")


# ── Node 3: RISK_CHECK ────────────────────────────────────────────────
def node_risk_check(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    config = state["config"]
    signal = state["signal"]
    log.info(f"[RISK_CHECK] Checking {symbol}...")

    try:
        # Positions AND pending orders — otherwise the same signal stacks
        # a new order every 60-second cycle.
        max_trades = int(config.get("max_trades", MAX_TRADES) or MAX_TRADES)
        positions  = mt5.positions_get(symbol=symbol) or ()
        orders     = mt5.orders_get(symbol=symbol) or ()
        open_count = len(positions) + len(orders)
        if open_count >= max_trades:
            return _risk_fail(state, f"Max trades ({max_trades}) already open on {symbol}")

        # HITL queue is exposure too — MT5 cannot see a PENDING confirm.
        pending_ref = state.get("firebase", {}).get("pending_signals_ref")
        existing_hitl = _open_hitl_key(pending_ref, symbol)
        if existing_hitl:
            return _risk_fail(state, "duplicate_pending_signal")

        account = mt5.account_info()
        if account is None:
            return _risk_fail(state, "No MT5 account info — terminal disconnected?")
        if not account.trade_allowed:
            return _risk_fail(state, "Trading not allowed on this account")

        # Same definition bot.py's limiter uses — session drawdown against the
        # equity the day opened at. Comparing equity to *balance* here instead
        # measured unrealised P&L on open positions, so the two gates disagreed.
        daily_limit = float(config.get("daily_loss_limit_pct", DAILY_LOSS_LIMIT_PCT))
        limit_hit, limit_reason = daily_loss_limit_hit(account, daily_limit)
        if limit_hit:
            return _risk_fail(state, limit_reason)

        # Signal sanity — same rules the executor enforces, checked early
        validate_signal(symbol, signal)

        log.info(f"[RISK_CHECK] {symbol}: All checks passed")
        return {**state, "risk_ok": True, "risk_reason": "All risk checks passed"}

    except ExecutionError as e:
        return _risk_fail(state, str(e))
    except Exception as e:
        log.error(f"[RISK_CHECK] Error: {e}")
        traceback.print_exc()
        return _risk_fail(state, f"Risk check error: {e}")


def _risk_fail(state: BraveState, reason: str) -> BraveState:
    return {**state, "risk_ok": False, "risk_reason": reason}


def _open_hitl_key(pending_ref, symbol: str) -> str | None:
    """Return the Firebase key of a PENDING/EXECUTING HITL row for this symbol."""
    if pending_ref is None:
        return None
    try:
        pending = pending_ref.get() or {}
    except Exception as e:
        log.warning(f"[HITL] Could not read pending_signals ({e})")
        return None

    if not isinstance(pending, dict):
        return None

    want = str(symbol).upper()
    for key, sig in pending.items():
        if not isinstance(sig, dict):
            continue
        if str(sig.get("symbol", "")).upper() != want:
            continue
        if str(sig.get("status", "")).upper() in ("PENDING", "EXECUTING"):
            return str(key)
    return None


# ── Node 4a: EXECUTE ──────────────────────────────────────────────────
def node_execute(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    signal = state["signal"]
    log.info(f"[EXECUTE] Placing {signal['direction']} on {symbol}...")

    result = place_order(symbol, signal, state["config"],
                         comment=f"Brave Flow {signal['direction']}")

    alerts_ref = state.get("firebase", {}).get("alerts_ref")
    if alerts_ref is not None:
        try:
            alerts_ref.push({
                **signal,
                "alert_type":       "TRADE_EXECUTED" if result["ok"] else "EXECUTION_FAILED",
                "sentiment":        state.get("sentiment"),
                "sentiment_reason": state.get("sentiment_reason"),
                "ticket":           result["ticket"],
                "filled_price":     result["price"],
                "lot":              result["lot"],
                "strategy":         signal.get("strategy_name", "Flow"),
                "error":            result["error"],
                "sent_at":          datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            log.error(f"[EXECUTE] {symbol}: Alert push failed: {e}")

    return {
        **state,
        "executed":   result["ok"],
        "mt5_ticket": int(result["ticket"] or 0) if result["ok"] else 0,
        "fill_price": float(result["price"] or 0.0) if result["ok"] else 0.0,
    }


# ── Node 4b: HITL ─────────────────────────────────────────────────────
def node_hitl(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    signal = state["signal"]
    reason = (
        "Manual execution mode"
        if str(state.get("execution_mode", "AUTO")).upper() == "MANUAL"
        else state.get("sentiment_reason", "News sentiment uncertain")
    )
    log.info(f"[HITL] Pushing {symbol} to pending_signals — {reason}")

    pending_ref = state.get("firebase", {}).get("pending_signals_ref")
    if pending_ref is None:
        log.warning(f"[HITL] {symbol}: Firebase unavailable — signal dropped")
        return {**state, "hitl_required": False}

    existing = _open_hitl_key(pending_ref, symbol)
    if existing:
        log.info(
            f"[HITL] {symbol}: skipped — duplicate_pending_signal "
            f"(key={existing} still PENDING/EXECUTING)"
        )
        return {
            **state,
            "hitl_required": False,
            "abort": True,
            "abort_reason": "duplicate_pending_signal",
        }

    try:
        pending_ref.push({
            **signal,
            "status":       "PENDING",
            "hitl_reason":  reason,
            "hitl_kind":    state.get("hitl_kind") or (
                "data_unavailable" if state.get("analysis_unavailable")
                else "genuine_uncertainty" if state.get("sentiment") == "UNCERTAIN"
                else "manual_mode"
            ),
            "sentiment":    state.get("sentiment"),
            "pushed_at":    datetime.now(timezone.utc).isoformat(),
            "expires_at":   datetime.now(timezone.utc).timestamp() + SIGNAL_EXPIRY_SECONDS,
        })
        log.info(f"[HITL] {symbol}: Signal pushed — expires in {SIGNAL_EXPIRY_SECONDS}s")
        return {**state, "hitl_required": True}
    except Exception as e:
        log.error(f"[HITL] {symbol}: Failed to push pending signal: {e}")
        return {**state, "hitl_required": False}


# ── Routing ───────────────────────────────────────────────────────────
def route_after_detect(state: BraveState) -> str:
    return END if state.get("abort") or not state.get("signal") else "analyse"


def route_after_analyse(state: BraveState) -> str:
    if state.get("abort"):
        log.info(
            f"[ROUTE] {state['symbol']}: ANALYSE abort — {state.get('abort_reason')}"
        )
        return END
    if state.get("sentiment") == "OPPOSE":
        log.info(f"[ROUTE] {state['symbol']}: OPPOSE — trade aborted")
        return END
    return "risk_check"


def route_after_risk(state: BraveState) -> str:
    if not state.get("risk_ok"):
        log.info(f"[ROUTE] {state['symbol']}: Risk failed — {state.get('risk_reason')}")
        return END
    if str(state.get("execution_mode", "AUTO")).upper() == "MANUAL":
        return "hitl"
    return "hitl" if state.get("sentiment") == "UNCERTAIN" else "execute"


# ── Build ─────────────────────────────────────────────────────────────
_GRAPH = None


def build_brave_graph():
    """Compile the graph once and reuse it — compiling per symbol per cycle is waste."""
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH

    g = StateGraph(BraveState)

    g.add_node("detect",     node_detect)
    g.add_node("analyse",    node_analyse)
    g.add_node("risk_check", node_risk_check)
    g.add_node("execute",    node_execute)
    g.add_node("hitl",       node_hitl)

    g.set_entry_point("detect")

    g.add_conditional_edges("detect",     route_after_detect,  {"analyse": "analyse", END: END})
    g.add_conditional_edges("analyse",    route_after_analyse, {"risk_check": "risk_check", END: END})
    g.add_conditional_edges("risk_check", route_after_risk,
                            {"execute": "execute", "hitl": "hitl", END: END})

    g.add_edge("execute", END)
    g.add_edge("hitl",    END)

    _GRAPH = g.compile()
    return _GRAPH


# ── Entry point ───────────────────────────────────────────────────────
def run_brave_graph(symbol: str, config: dict, firebase: dict,
                    execution_mode: str = "AUTO") -> BraveState:
    """Called by bot.py for each symbol each cycle. Never raises."""
    initial: BraveState = {
        "symbol":           symbol,
        "config":           config or {},
        "execution_mode":   str(execution_mode or "AUTO").upper(),
        "signal":           None,
        "sentiment":        None,
        "sentiment_reason": "",
        "headlines":        [],
        "analysis_unavailable": False,
        "hitl_kind":        "",
        "risk_ok":          False,
        "risk_reason":      "",
        "abort":            False,
        "abort_reason":     "",
        "hitl_required":    False,
        "executed":         False,
        "mt5_ticket":       0,
        "fill_price":       0.0,
        "firebase":         firebase or {},
    }

    try:
        result = build_brave_graph().invoke(initial)
    except Exception as e:
        log.error(f"[GRAPH] {symbol}: Pipeline failed — {e}")
        traceback.print_exc()
        result = {**initial, "abort": True, "abort_reason": f"Pipeline error: {e}"}

    # One CSV row per signal that reached ANALYSE (DETECT produced a setup).
    # Written once from final state — exit fields stay blank until close.
    if result.get("signal"):
        _log_signal_row(result)

    return result


def _log_signal_row(state: BraveState) -> None:
    """Map final BraveState → trade_log.csv columns."""
    signal = state.get("signal") or {}
    entry  = state.get("fill_price") or signal.get("entry_price", "")

    log_signal({
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "symbol":            state.get("symbol", signal.get("symbol", "")),
        "direction":         signal.get("direction", ""),
        "entry":             entry,
        "sl":                signal.get("suggested_sl", ""),
        "tp":                signal.get("suggested_tp", ""),
        "rr":                signal.get("risk_reward_ratio", ""),
        "deepseek_verdict":  state.get("sentiment") or "",
        "deepseek_reason":   state.get("sentiment_reason") or "",
        "risk_check_result": _risk_result_label(state),
        "outcome":           _outcome_label(state),
        "mt5_ticket":        state.get("mt5_ticket") or "",
        "exit_price":        "",
        "exit_reason":       "",
        "pnl":               "",
    })


def _risk_result_label(state: BraveState) -> str:
    if state.get("abort_reason") == "analysis_unavailable" or (
        state.get("analysis_unavailable")
        and str(state.get("execution_mode", "")).upper() == "AUTO"
    ):
        return "SKIPPED"
    if state.get("sentiment") == "OPPOSE":
        return "SKIPPED"           # Never reached RISK_CHECK
    if state.get("risk_ok"):
        return "PASS"
    reason = state.get("risk_reason") or ""
    return f"FAIL: {reason}" if reason else "FAIL"


def _outcome_label(state: BraveState) -> str:
    if state.get("abort_reason") == "analysis_unavailable" or (
        state.get("analysis_unavailable")
        and str(state.get("execution_mode", "")).upper() == "AUTO"
    ):
        return "ANALYSIS_UNAVAILABLE"
    if state.get("sentiment") == "OPPOSE":
        return "OPPOSE"
    if not state.get("risk_ok"):
        # ANALYSE ran but risk never passed (or never reached — shouldn't happen
        # unless the graph aborted mid-flight after ANALYSE)
        if state.get("risk_reason"):
            return "RISK_FAIL"
        return "ABORT"
    if state.get("executed"):
        return "EXECUTED"
    if state.get("hitl_required"):
        return "HITL"
    if state.get("mt5_ticket"):
        return "EXECUTED"
    # EXECUTE node ran and failed, or HITL push failed
    if str(state.get("execution_mode", "")).upper() == "MANUAL" or state.get("sentiment") == "UNCERTAIN":
        return "HITL_FAILED"
    return "EXECUTION_FAILED"
