"""
trade_executor.py — MT5 order placement, validated and logged.

Single execution path for the whole bot. Both the LangGraph EXECUTE node and
the MANUAL-mode confirmation consumer in bot.py call place_order() so risk
sizing, price rounding and error handling can never drift apart.

Guarantees:
    * Signal fields are validated before anything is sent to the broker
    * Lot size is risk-based (RISK_PER_TRADE_PCT of balance), clamped to the
      symbol's volume_min/volume_max/volume_step and to LOT_SIZE as a ceiling
    * Prices, SL and TP are rounded to the symbol's digits and checked against
      the broker's stops level — no more silently dropped stops on XAUUSD
    * An order is never placed without both SL and TP
    * Unsupported filling modes are retried instead of failing the trade

Fills are recorded through trade_logger.log_execution — every CSV Brave writes
goes through that one module so path anchoring and failure handling are shared.
"""

import logging

import MetaTrader5 as mt5

from config import LOT_SIZE, RISK_PER_TRADE_PCT
from trade_logger import log_execution

log = logging.getLogger(__name__)

MAGIC_NUMBER = 234000

# Retcodes worth one retry — transient broker/price conditions
RETRYABLE_RETCODES = {
    mt5.TRADE_RETCODE_REQUOTE,
    mt5.TRADE_RETCODE_PRICE_CHANGED,
    mt5.TRADE_RETCODE_PRICE_OFF,
    mt5.TRADE_RETCODE_TIMEOUT,
    mt5.TRADE_RETCODE_CONNECTION,
}

REQUIRED_SIGNAL_FIELDS = ("direction", "entry_price", "suggested_sl", "suggested_tp")


class ExecutionError(Exception):
    """Raised when a signal cannot be turned into a valid broker order."""


# ── Validation ────────────────────────────────────────────────────────
def validate_signal(symbol: str, signal: dict) -> dict:
    """
    Check a strategy signal is structurally sound and internally consistent.

    Returns the signal with numeric fields coerced to float.
    Raises ExecutionError with a human-readable reason otherwise.
    """
    if not isinstance(signal, dict):
        raise ExecutionError(f"[{symbol}] Signal is not a dict: {type(signal).__name__}")

    missing = [f for f in REQUIRED_SIGNAL_FIELDS if signal.get(f) is None]
    if missing:
        raise ExecutionError(f"[{symbol}] Signal missing fields: {', '.join(missing)}")

    direction = str(signal["direction"]).upper()
    if direction not in ("BUY", "SELL"):
        raise ExecutionError(f"[{symbol}] Invalid direction: {signal['direction']}")

    try:
        entry = float(signal["entry_price"])
        sl    = float(signal["suggested_sl"])
        tp    = float(signal["suggested_tp"])
    except (TypeError, ValueError) as e:
        raise ExecutionError(f"[{symbol}] Non-numeric price in signal: {e}") from e

    if not all(v > 0 for v in (entry, sl, tp)):
        raise ExecutionError(f"[{symbol}] Prices must be positive (entry={entry}, sl={sl}, tp={tp})")

    # SL and TP must sit on the correct side of entry, or the broker rejects
    # the order — and a flipped SL would be an unprotected trade.
    if direction == "BUY" and not (sl < entry < tp):
        raise ExecutionError(f"[{symbol}] BUY needs sl < entry < tp (got {sl} / {entry} / {tp})")
    if direction == "SELL" and not (tp < entry < sl):
        raise ExecutionError(f"[{symbol}] SELL needs tp < entry < sl (got {tp} / {entry} / {sl})")

    return {**signal, "direction": direction, "entry_price": entry, "suggested_sl": sl, "suggested_tp": tp}


# ── Lot sizing ────────────────────────────────────────────────────────
def calculate_lot(symbol: str, entry: float, sl: float, info, account) -> float:
    """
    Risk-based position size: RISK_PER_TRADE_PCT of balance across the
    entry→SL distance, clamped to the symbol's volume constraints and to
    LOT_SIZE as an absolute ceiling.
    """
    price_risk = abs(entry - sl)
    if price_risk <= 0:
        raise ExecutionError(f"[{symbol}] Entry equals SL — cannot size position")

    contract_size = getattr(info, "trade_contract_size", 0) or 100_000
    risk_amount   = max(account.balance, 0) * RISK_PER_TRADE_PCT
    if risk_amount <= 0:
        raise ExecutionError(f"[{symbol}] Account balance is {account.balance} — cannot size position")

    lot = risk_amount / (price_risk * contract_size)

    step = getattr(info, "volume_step", 0.01) or 0.01
    vmin = getattr(info, "volume_min", 0.01) or 0.01
    vmax = getattr(info, "volume_max", 100.0) or 100.0

    lot = round(lot / step) * step
    lot = max(vmin, min(lot, vmax, LOT_SIZE))
    # Re-round after clamping so the value still lands on a valid step
    lot = round(round(lot / step) * step, 8)

    if lot < vmin:
        raise ExecutionError(f"[{symbol}] Risk budget too small for broker minimum lot ({vmin})")

    return lot


def _respect_stops_level(symbol: str, price: float, sl: float, tp: float, info) -> None:
    """Reject orders whose stops are inside the broker's minimum distance."""
    stops_points = getattr(info, "trade_stops_level", 0) or 0
    if stops_points <= 0:
        return

    min_distance = stops_points * info.point
    if abs(price - sl) < min_distance or abs(price - tp) < min_distance:
        raise ExecutionError(
            f"[{symbol}] SL/TP inside broker stops level "
            f"({stops_points} points = {min_distance:.5f}) — order would be rejected"
        )


# ── Order placement ───────────────────────────────────────────────────
def _filling_modes(info) -> list[int]:
    """Broker-supported filling modes, most likely first."""
    supported = getattr(info, "filling_mode", 0) or 0
    modes = []
    if supported & 2:  # SYMBOL_FILLING_IOC
        modes.append(mt5.ORDER_FILLING_IOC)
    if supported & 1:  # SYMBOL_FILLING_FOK
        modes.append(mt5.ORDER_FILLING_FOK)
    modes.append(mt5.ORDER_FILLING_RETURN)
    # Always keep IOC/FOK as fallbacks even if the mask was empty or unreadable
    for fallback in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
        if fallback not in modes:
            modes.append(fallback)
    return modes


def place_order(symbol: str, signal: dict, config: dict, comment: str = "Brave Flow") -> dict:
    """
    Place a market order for a validated signal.

    Returns {"ok": bool, "ticket": int, "price": float, "lot": float,
             "retcode": int | None, "error": str | None}.
    Never raises — callers get a structured result they can log and push.
    """
    try:
        signal = validate_signal(symbol, signal)

        info = mt5.symbol_info(symbol)
        if info is None:
            raise ExecutionError(f"[{symbol}] Symbol not found in MT5")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise ExecutionError(f"[{symbol}] Symbol could not be selected in Market Watch")
        if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            raise ExecutionError(f"[{symbol}] Trading disabled for this symbol (mode {info.trade_mode})")

        account = mt5.account_info()
        if account is None:
            raise ExecutionError(f"[{symbol}] No MT5 account info — terminal disconnected?")
        if not account.trade_allowed:
            raise ExecutionError(f"[{symbol}] Trading not allowed on this account")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.ask <= 0 or tick.bid <= 0:
            raise ExecutionError(f"[{symbol}] No valid tick — market data unavailable")

        direction = signal["direction"]
        lot       = calculate_lot(symbol, signal["entry_price"], signal["suggested_sl"], info, account)

        price = round(tick.ask if direction == "BUY" else tick.bid, info.digits)
        sl    = round(signal["suggested_sl"], info.digits)
        tp    = round(signal["suggested_tp"], info.digits)

        # Re-validate against the live fill price, not the stale signal price
        if direction == "BUY" and not (sl < price < tp):
            raise ExecutionError(f"[{symbol}] Price moved past SL/TP (sl={sl}, price={price}, tp={tp})")
        if direction == "SELL" and not (tp < price < sl):
            raise ExecutionError(f"[{symbol}] Price moved past SL/TP (tp={tp}, price={price}, sl={sl})")

        _respect_stops_level(symbol, price, sl, tp, info)

        request = {
            "action":     mt5.TRADE_ACTION_DEAL,
            "symbol":     symbol,
            "volume":     lot,
            "type":       mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
            "price":      price,
            "sl":         sl,
            "tp":         tp,
            "deviation":  int(config.get("deviation", 20)),
            "magic":      MAGIC_NUMBER,
            "comment":    comment[:31],           # MT5 truncates past 31 chars
            "type_time":  mt5.ORDER_TIME_GTC,
        }

        log.info(
            f"[{symbol}] Sending {direction} {lot} lots @ {price} "
            f"(SL {sl} / TP {tp}, risk {RISK_PER_TRADE_PCT:.1%} of ${account.balance:.2f})"
        )

        result = _send_with_fallbacks(request, info, symbol)

        if result is None:
            return _failure(symbol, None, f"order_send returned nothing ({mt5.last_error()})")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return _failure(symbol, result.retcode, getattr(result, "comment", "") or "Order rejected")

        ticket     = getattr(result, "order", 0)
        fill_price = getattr(result, "price", price) or price
        log.info(f"[{symbol}] Order placed — ticket {ticket} @ {fill_price}")

        log_execution(symbol, signal, lot, ticket, fill_price, sl, tp, account)

        return {"ok": True, "ticket": ticket, "price": fill_price, "lot": lot,
                "sl": sl, "tp": tp, "retcode": result.retcode, "error": None}

    except ExecutionError as e:
        log.error(str(e))
        return _failure(symbol, None, str(e))
    except Exception as e:                                   # broker/API surprises
        log.exception(f"[{symbol}] Unexpected execution error: {e}")
        return _failure(symbol, None, f"Unexpected error: {e}")


def _send_with_fallbacks(request: dict, info, symbol: str):
    """Send the order, retrying on unsupported filling mode and transient errors."""
    result = None
    for filling in _filling_modes(info):
        request["type_filling"] = filling
        result = mt5.order_send(request)

        if result is None:
            log.warning(f"[{symbol}] order_send returned None ({mt5.last_error()}) — retrying")
            continue

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return result

        if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            log.warning(f"[{symbol}] Filling mode {filling} unsupported — trying next")
            continue

        if result.retcode in RETRYABLE_RETCODES:
            log.warning(f"[{symbol}] Retcode {result.retcode} ({result.comment}) — retrying once")
            # Refresh the price before the retry; a stale price re-triggers a requote
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                is_buy = request["type"] == mt5.ORDER_TYPE_BUY
                request["price"] = round(tick.ask if is_buy else tick.bid, info.digits)
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return result

        break  # Non-retryable rejection — stop trying

    return result


def _failure(symbol: str, retcode, error: str) -> dict:
    log.error(f"[{symbol}] Order failed — {error}")
    return {"ok": False, "ticket": 0, "price": 0.0, "lot": 0.0,
            "sl": 0.0, "tp": 0.0, "retcode": retcode, "error": error}
