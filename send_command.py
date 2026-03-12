import firebase_admin
from firebase_admin import credentials, db
from config import FIREBASE_DATABASE_URL, USER_ID
from datetime import datetime
import sys

# Initialize Firebase with proper error handling
def init_firebase():
    try:
        # Check if already initialized
        firebase_admin.get_app()
        print("[INFO] Firebase already initialized")
    except ValueError:
        # Not initialized, so initialize it
        try:
            print("[DEBUG] Initializing Firebase...")
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            print("[SUCCESS] Firebase initialized successfully")
        except FileNotFoundError:
            print("[ERROR] serviceAccountKey.json not found!")
            print("[ERROR] Make sure the file exists in the current directory")
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Failed to initialize Firebase: {e}")
            sys.exit(1)

# Initialize Firebase
init_firebase()

# Validate command line arguments
if len(sys.argv) < 2:
    print("[ERROR] Usage: python send_command.py [start|stop]")
    sys.exit(1)

action = sys.argv[1]

if action not in ['start', 'stop']:
    print("[ERROR] Action must be 'start' or 'stop'")
    sys.exit(1)

print(f"[INFO] Sending '{action}' command...")

try:
    # Get reference to commands
    command_ref = db.reference(f'users/{USER_ID}/commands')
    
    # Send command
    command_data = {
        'action': action,
        'timestamp': datetime.now().isoformat()
    }
    
    command_ref.set(command_data)
    
    print(f"[SUCCESS] Command sent: {action}")
    print(f"[INFO] Timestamp: {command_data['timestamp']}")
    print(f"[INFO] Path: users/{USER_ID}/commands")
    print("[INFO] Watch your bot terminal for response...")
    
except Exception as e:
    print(f"[ERROR] Failed to send command: {e}")
    sys.exit(1)