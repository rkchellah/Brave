import firebase_admin
from firebase_admin import credentials, db
from config import FIREBASE_DATABASE_URL, USER_ID, SYMBOLS, TIMEFRAME, LOT_SIZE, MAX_TRADES, STOP_LOSS_PIPS, TAKE_PROFIT_PIPS

# Initialize Firebase
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': FIREBASE_DATABASE_URL
})

# Reference to your user's config
config_ref = db.reference(f'users/{USER_ID}/bot_config')

# Write the complete config
config_data = {
    'symbols': SYMBOLS,  # Automatically handles any number of symbols
    'timeframe': TIMEFRAME,
    'lot_size': LOT_SIZE,
    'max_trades': MAX_TRADES,
    'stop_loss_pips': STOP_LOSS_PIPS,
    'take_profit_pips': TAKE_PROFIT_PIPS
}

print("Writing config to Firebase...")
config_ref.set(config_data)
print(" Config written successfully!")

# Verify it was written
read_config = config_ref.get()
print(f"\n Config in Firebase:")
print(f"   Symbols: {read_config['symbols']}")
print(f"   Timeframe: {read_config['timeframe']}")
print(f"   Lot Size: {read_config['lot_size']}")
print(f"   Max Trades: {read_config['max_trades']}")
print(f"   SL Pips: {read_config['stop_loss_pips']}")
print(f"   TP Pips: {read_config['take_profit_pips']}")

print("\n Setup complete! You can now run bot.py")