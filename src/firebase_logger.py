import firebase_admin
from firebase_admin import credentials, db
import logging
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

CAT = timezone(timedelta(hours=2))  # Central Africa Time — Zambia

class FirebaseLogger:
    """
    Logs signals and trades to the QuantifyX Firebase Realtime Database.
    Separate project from Brave — no data collision.
    """

    def __init__(self):
        # Prevent re-initializing if already connected
        if not firebase_admin._apps:
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred, {
                "databaseURL": os.getenv("FIREBASE_DATABASE_URL")
            })
            logging.info("Firebase connected — QuantifyX database")
        else:
            logging.info("Firebase already connected")

    def log_signal(self, signal: dict) -> None:
        """Push a signal to quantifyx/signals/ in Firebase."""
        try:
            ref = db.reference("quantifyx/signals")
            ref.push({
                "symbol":            signal["symbol"],
                "direction":         signal["direction"],
                "strategy_name":     signal["strategy_name"],
                "entry_price":       signal["entry_price"],
                "suggested_sl":      signal["suggested_sl"],
                "suggested_tp":      signal["suggested_tp"],
                "risk_reward_ratio": signal["risk_reward_ratio"],
                "volume":            signal["volume"],
                "probability":       signal["probability"],
                "timestamp_cat":     datetime.now(CAT).strftime("%Y-%m-%d %H:%M:%S CAT"),
                "timestamp_utc":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
            logging.info(f"Firebase: Signal logged — {signal['direction']} {signal['symbol']}")
        except Exception as e:
            logging.error(f"Firebase: Failed to log signal — {e}")

    def log_trade(self, signal: dict, result: dict) -> None:
        """Push an executed trade to quantifyx/trades/ in Firebase."""
        try:
            ref = db.reference("quantifyx/trades")
            ref.push({
                "symbol":    signal["symbol"],
                "direction": signal["direction"],
                "volume":    signal["volume"],
                "entry":     signal["entry_price"],
                "sl":        signal["suggested_sl"],
                "tp":        signal["suggested_tp"],
                "status":    result["status"],
                "txid":      result.get("txid", "dry_run"),
                "timestamp_cat": datetime.now(CAT).strftime("%Y-%m-%d %H:%M:%S CAT"),
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
            logging.info(f"Firebase: Trade logged — {signal['direction']} {signal['symbol']} | {result['status']}")
        except Exception as e:
            logging.error(f"Firebase: Failed to log trade — {e}")

    def update_status(self, is_running: bool, active_strategy: str) -> None:
        """Update agent status in Firebase — visible in console."""
        try:
            ref = db.reference("quantifyx/status")
            ref.set({
                "is_running":      is_running,
                "active_strategy": active_strategy,
                "last_updated_cat": datetime.now(CAT).strftime("%Y-%m-%d %H:%M:%S CAT"),
                "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
        except Exception as e:
            logging.error(f"Firebase: Failed to update status — {e}")