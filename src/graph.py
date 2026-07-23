"""
graph.py — Brave v3.0 LangGraph Agent

Pipeline:
    DETECT → ANALYSE → RISK_CHECK → EXECUTE or HITL

DETECT:     Flow strategy scans the symbol for a signal
ANALYSE:    DeepSeek reads Finnhub headlines and returns CONFIRM/OPPOSE/UNCERTAIN
RISK_CHECK: Pure Python — daily loss limit, max positions, lot size
EXECUTE:    MT5 order placed, Firebase alert written
HITL:       Signal pushed to Firebase pending_signals for human confirmation

Routing:
    DETECT   → no signal          → END
    ANALYSE  → OPPOSE             → END
    ANALYSE  → CONFIRM/UNCERTAIN  → RISK_CHECK
    RISK_CHECK → fail             → END
    RISK_CHECK → pass + CONFIRM   → EXECUTE
    RISK_CHECK → pass + UNCERTAIN → HITL
"""

import logging
import traceback
import time
from datetime import datetime, timezone
from typing import TypedDict, Literal

from langgraph.graph import StateGraph, END
import MetaTrader5 as mt5

from news_fetcher import fetch_news_for_symbol, format_headlines_for_llm
from flow import Flow


# ── State ─────────────────────────────────────────────────────────
class BraveState(TypedDict):
    symbol:           str
    config:           dict
    signal:           dict | None
    sentiment:        Literal["CONFIRM", "OPPOSE", "UNCERTAIN"] | None
    sentiment_reason: str
    risk_ok:          bool
    risk_reason:      str
    abort:            bool
    abort_reason:     str
    hitl_required:    bool
    firebase:         dict


# ── Node 1: DETECT ────────────────────────────────────────────────
def node_detect(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    logging.info(f"[DETECT] Running Flow on {symbol}...")
    try:
        strategy = Flow(state["config"])
        signal   = strategy.analyze(symbol)
        if signal is None:
            logging.info(f"[DETECT] {symbol}: No signal")
            return {**state, "signal": None, "abort": True, "abort_reason": "No signal from Flow"}
        logging.info(f"[DETECT] {symbol}: {signal['direction']} signal — Entry {signal['entry_price']}")
        return {**state, "signal": signal, "abort": False}
    except Exception as e:
        logging.error(f"[DETECT] Error: {e}")
        traceback.print_exc()
        return {**state, "signal": None, "abort": True, "abort_reason": str(e)}


# ── Node 2: ANALYSE ───────────────────────────────────────────────
def node_analyse(state: BraveState) -> BraveState:
    from openai import OpenAI
    from config import DEEPSEEK_API_KEY

    symbol    = state["symbol"]
    direction = state["signal"]["direction"]
    logging.info(f"[ANALYSE] Fetching news and querying DeepSeek for {symbol}...")

    try:
        articles  = fetch_news_for_symbol(symbol)
        headlines = format_headlines_for_llm(symbol, articles)

        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

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

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.1,
        )

        text    = response.choices[0].message.content.strip()
        lines   = text.split("\n", 1)
        verdict = lines[0].strip().upper()
        reason  = lines[1].strip() if len(lines) > 1 else "No reason provided"

        if verdict not in ("CONFIRM", "OPPOSE", "UNCERTAIN"):
            logging.warning(f"[ANALYSE] Unexpected verdict: {verdict} — defaulting to UNCERTAIN")
            verdict = "UNCERTAIN"
            reason  = f"Unexpected response: {text[:100]}"

        logging.info(f"[ANALYSE] {symbol}: {verdict} — {reason}")
        return {**state, "sentiment": verdict, "sentiment_reason": reason}

    except Exception as e:
        logging.error(f"[ANALYSE] Error: {e}")
        return {**state, "sentiment": "UNCERTAIN", "sentiment_reason": f"Analysis failed: {e}"}


# ── Node 3: RISK_CHECK ────────────────────────────────────────────
def node_risk_check(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    config = state["config"]
    signal = state["signal"]
    logging.info(f"[RISK_CHECK] Checking {symbol}...")

    try:
        # Max positions per symbol
        max_trades = config.get("max_trades_per_symbol", 2)
        positions  = mt5.positions_get(symbol=symbol)
        if positions and len(positions) >= max_trades:
            return {**state, "risk_ok": False,
                    "risk_reason": f"Max trades ({max_trades}) already open on {symbol}"}

        # Daily loss limit
        daily_limit = config.get("daily_loss_limit_pct", 0.05)
        account     = mt5.account_info()
        if account:
            balance  = account.balance
            equity   = account.equity
            loss_pct = (balance - equity) / balance if balance > 0 else 0
            if loss_pct >= daily_limit:
                return {**state, "risk_ok": False,
                        "risk_reason": f"Daily loss limit hit ({loss_pct:.1%} >= {daily_limit:.1%})"}

        # Lot size sanity
        lot = float(signal.get("lot_size") or config.get("lot_size", 0.01))
        if lot <= 0 or lot > 10:
            return {**state, "risk_ok": False, "risk_reason": f"Invalid lot size: {lot}"}

        logging.info(f"[RISK_CHECK] {symbol}: All checks passed")
        return {**state, "risk_ok": True, "risk_reason": "All risk checks passed"}

    except Exception as e:
        logging.error(f"[RISK_CHECK] Error: {e}")
        return {**state, "risk_ok": False, "risk_reason": str(e)}


# ── Node 4a: EXECUTE ──────────────────────────────────────────────
def node_execute(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    signal = state["signal"]
    fb     = state.get("firebase", {})
    logging.info(f"[EXECUTE] Placing {signal['direction']} on {symbol}...")

    try:
        direction  = signal["direction"]
        lot        = float(signal.get("lot_size") or state["config"].get("lot_size", 0.01))
        tick       = mt5.symbol_info_tick(symbol)
        price      = tick.ask if direction == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           float(signal["suggested_sl"]),
            "tp":           float(signal["suggested_tp"]),
            "deviation":    10,
            "magic":        234000,
            "comment":      f"Brave Flow {direction}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"[EXECUTE] ✅ Order placed — ticket {result.order}")
            alerts_ref = fb.get("alerts_ref")
            if alerts_ref:
                alerts_ref.push({
                    **signal,
                    "alert_type":       "FLOW_EXECUTE",
                    "sentiment":        state.get("sentiment"),
                    "sentiment_reason": state.get("sentiment_reason"),
                    "mt5_ticket":       result.order,
                    "sent_at":          datetime.now(timezone.utc).isoformat(),
                })
        else:
            code = result.retcode if result else "unknown"
            logging.error(f"[EXECUTE] ❌ Order failed — retcode {code}")

    except Exception as e:
        logging.error(f"[EXECUTE] Error: {e}")
        traceback.print_exc()

    return state


# ── Node 4b: HITL ─────────────────────────────────────────────────
def node_hitl(state: BraveState) -> BraveState:
    symbol = state["symbol"]
    signal = state["signal"]
    fb     = state.get("firebase", {})
    logging.info(f"[HITL] Pushing {symbol} to pending_signals — sentiment UNCERTAIN")

    try:
        from config import SIGNAL_EXPIRY_SECONDS
        pending_ref = fb.get("pending_signals_ref")
        if pending_ref:
            pending_ref.push({
                **signal,
                "status":           "PENDING",
                "hitl_reason":      state.get("sentiment_reason", "Sentiment uncertain"),
                "pushed_at":        datetime.now(timezone.utc).isoformat(),
                "expires_at":       time.time() + SIGNAL_EXPIRY_SECONDS,
            })
            logging.info(f"[HITL] Signal pushed — expires in {SIGNAL_EXPIRY_SECONDS}s")
    except Exception as e:
        logging.error(f"[HITL] Error: {e}")

    return {**state, "hitl_required": True}


# ── Routing ───────────────────────────────────────────────────────
def route_after_detect(state: BraveState) -> str:
    return END if state.get("abort") else "analyse"

def route_after_analyse(state: BraveState) -> str:
    if state.get("sentiment") == "OPPOSE":
        logging.info(f"[ROUTE] {state['symbol']}: OPPOSE — trade aborted")
        return END
    return "risk_check"

def route_after_risk(state: BraveState) -> str:
    if not state.get("risk_ok"):
        logging.info(f"[ROUTE] {state['symbol']}: Risk failed — {state.get('risk_reason')}")
        return END
    return "hitl" if state.get("sentiment") == "UNCERTAIN" else "execute"


# ── Build ─────────────────────────────────────────────────────────
def build_brave_graph():
    g = StateGraph(BraveState)

    g.add_node("detect",     node_detect)
    g.add_node("analyse",    node_analyse)
    g.add_node("risk_check", node_risk_check)
    g.add_node("execute",    node_execute)
    g.add_node("hitl",       node_hitl)

    g.set_entry_point("detect")

    g.add_conditional_edges("detect",     route_after_detect,
                            {"analyse": "analyse", END: END})
    g.add_conditional_edges("analyse",    route_after_analyse,
                            {"risk_check": "risk_check", END: END})
    g.add_conditional_edges("risk_check", route_after_risk,
                            {"execute": "execute", "hitl": "hitl", END: END})

    g.add_edge("execute", END)
    g.add_edge("hitl",    END)

    return g.compile()


# ── Entry point ───────────────────────────────────────────────────
def run_brave_graph(symbol: str, config: dict, firebase: dict) -> BraveState:
    """Called by bot.py for each symbol each cycle."""
    graph = build_brave_graph()
    return graph.invoke({
        "symbol":           symbol,
        "config":           config,
        "signal":           None,
        "sentiment":        None,
        "sentiment_reason": "",
        "risk_ok":          False,
        "risk_reason":      "",
        "abort":            False,
        "abort_reason":     "",
        "hitl_required":    False,
        "firebase":         firebase,
    })
