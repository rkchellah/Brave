import krakenex
import logging
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Executor:
    """
    Execution layer for QuantifyX.
    Takes a signal from Frost and places the order on Kraken via krakenex.
    Replaces MT5 order_send().
    """

    def __init__(self):
        self.api = krakenex.API()
        self.api.key    = os.getenv("KRAKEN_API_KEY")
        self.api.secret = os.getenv("KRAKEN_API_SECRET")
        logging.info("Executor ready — Kraken API connected")

    def execute(self, signal: dict, dry_run: bool = True) -> dict:
        """
        Execute a signal.
        dry_run=True  → log the order but don't actually send it (safe testing)
        dry_run=False → live execution on Kraken
        """
        if not signal:
            logging.warning("Executor: No signal to execute")
            return {"status": "skipped", "reason": "no signal"}

        symbol    = signal["symbol"]
        direction = signal["direction"]
        volume    = str(signal["volume"])

        # Kraken uses 'buy' or 'sell' lowercase
        order_type = direction.lower()

        order_params = {
            "pair":      symbol,
            "type":      order_type,
            "ordertype": "market",
            "volume":    volume,
        }

        logging.info(
            f"Executor: {'[DRY RUN] ' if dry_run else ''}Placing {order_type.upper()} "
            f"{volume} {symbol} @ market"
        )
        logging.info(f"   SL target: {signal['suggested_sl']}")
        logging.info(f"   TP target: {signal['suggested_tp']}")
        logging.info(f"   RR: {signal['risk_reward_ratio']}")

        if dry_run:
            return {
                "status":  "dry_run",
                "order":   order_params,
                "signal":  signal,
            }

        # Live execution
        try:
            response = self.api.query_private("AddOrder", order_params)

            if response.get("error"):
                logging.error(f"Executor: Order failed — {response['error']}")
                return {"status": "error", "error": response["error"]}

            txid = response["result"].get("txid", [])
            logging.info(f"Executor: Order placed successfully — TXID: {txid}")

            return {
                "status": "executed",
                "txid":   txid,
                "order":  order_params,
                "signal": signal,
            }

        except Exception as e:
            logging.error(f"Executor: Exception during order — {e}")
            return {"status": "error", "error": str(e)}