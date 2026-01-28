import firebase_admin
from firebase_admin import credentials, db
from config import FIREBASE_DATABASE_URL, USER_ID
from datetime import datetime
import sys

# Initialize Firebase
try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DATABASE_URL
    })
except:
    pass

command_ref = db.reference(f'users/{USER_ID}/commands')

if len(sys.argv) < 2:
    print("Usage: python send_command.py [start|stop]")
    sys.exit(1)

action = sys.argv[1]

if action not in ['start', 'stop']:
    print(" Action must be 'start' or 'stop'")
    sys.exit(1)

print(f" Sending '{action}' command...")

command_ref.set({
    'action': action,
    'timestamp': datetime.now().isoformat()
})

print(f" Command sent: {action}")
print("Watch your bot terminal for response...")