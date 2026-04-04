import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

CAT = timezone(timedelta(hours=2))  # Central Africa Time — Zambia

# --- Logging setup ---
os.makedirs("logs", exist_ok=True)
log_filename = f"logs/agent_{datetime.now(CAT).strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s CAT | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler(),
    ],
)

from src.frost_kraken import Frost
from src.executor import Executor
from src.firebase_logger import FirebaseLogger

# --- Config ---
SYMBOLS  = ["XBTUSD", "ETHUSD"]
INTERVAL = 15 * 60  # 15 minutes
DRY_RUN  = True     # Switch to False for live trading

config = {
    "volume": 0.001,
}


def run():
    logging.info("=" * 60)
    logging.info("QuantifyX Agent starting")
    logging.info(f"Symbols: {SYMBOLS}")
    logging.info(f"Interval: {INTERVAL // 60} minutes")
    logging.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE TRADING'}")
    logging.info(f"Asian session: 02:00–08:00 CAT")
    logging.info("=" * 60)

    frost    = Frost(config)
    executor = Executor()
    firebase = FirebaseLogger()

    firebase.update_status(is_running=True, active_strategy="frost_kraken")

    while True:
        try:
            now = datetime.now(CAT)
            logging.info(f"--- Cycle: {now.strftime('%Y-%m-%d %H:%M:%S')} CAT ---")

            for symbol in SYMBOLS:
                logging.info(f"Analyzing {symbol}...")
                signal = frost.analyze(symbol)

                if signal:
                    logging.info(
                        f"Signal: {signal['direction']} {symbol} | "
                        f"Entry={signal['entry_price']} | "
                        f"RR={signal['risk_reward_ratio']}"
                    )
                    # Log signal to Firebase
                    firebase.log_signal(signal)

                    # Execute order
                    result = executor.execute(signal, dry_run=DRY_RUN)
                    logging.info(f"Execution: {result['status']}")

                    # Log trade to Firebase
                    firebase.log_trade(signal, result)

                else:
                    logging.info(f"{symbol}: No signal this cycle")

            logging.info(f"Sleeping {INTERVAL // 60} minutes...")
            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            logging.info("Agent stopped by user")
            firebase.update_status(is_running=False, active_strategy="frost_kraken")
            break

        except Exception as e:
            logging.error(f"Cycle error: {e}", exc_info=True)
            logging.info("Recovering — sleeping 60 seconds")
            time.sleep(60)


if __name__ == "__main__":
    run()