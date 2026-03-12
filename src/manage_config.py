import firebase_admin
from firebase_admin import credentials, db
from config import FIREBASE_DATABASE_URL, USER_ID
import sys

# Initialize Firebase
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': FIREBASE_DATABASE_URL
})

config_ref = db.reference(f'users/{USER_ID}/bot_config')

def add_symbol(symbol):
    """Add a new symbol to the config"""
    config = config_ref.get()
    if symbol not in config['symbols']:
        config['symbols'].append(symbol)
        config_ref.set(config)
        print(f" Added {symbol}")
    else:
        print(f"  {symbol} already exists")

def remove_symbol(symbol):
    """Remove a symbol from the config"""
    config = config_ref.get()
    if symbol in config['symbols']:
        config['symbols'].remove(symbol)
        config_ref.set(config)
        print(f" Removed {symbol}")
    else:
        print(f"  {symbol} not found")

def update_lot_size(new_size):
    """Update lot size"""
    config_ref.update({'lot_size': new_size})
    print(f" Lot size updated to {new_size}")

def update_max_trades(new_max):
    """Update max trades"""
    config_ref.update({'max_trades': new_max})
    print(f" Max trades updated to {new_max}")

def show_config():
    """Display current config"""
    config = config_ref.get()
    print("\n Current Config:")
    for key, value in config.items():
        print(f"   {key}: {value}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python manage_config.py show")
        print("  python manage_config.py add GBPUSD")
        print("  python manage_config.py remove USDJPY")
        print("  python manage_config.py lot_size 0.05")
        print("  python manage_config.py max_trades 5")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "show":
        show_config()
    elif action == "add" and len(sys.argv) == 3:
        add_symbol(sys.argv[2])
        show_config()
    elif action == "remove" and len(sys.argv) == 3:
        remove_symbol(sys.argv[2])
        show_config()
    elif action == "lot_size" and len(sys.argv) == 3:
        update_lot_size(float(sys.argv[3]))
        show_config()
    elif action == "max_trades" and len(sys.argv) == 3:
        update_max_trades(int(sys.argv[3]))
        show_config()
    else:
        print(" Invalid command")
