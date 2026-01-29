import secrets
import sys
import psycopg2
from app.db import get_db, ensure_db_schema

# Mock the app context or just direct DB connection since we are a script
# But app.db depends on some structure. Let's try to use the raw connection logic from db.py if possible,
# or just copy the connection string logic.
# Actually, db.py creates a global `conn` when imported.

def generate_key_for_user(email):
    conn = get_db()
    if conn is None:
        print("[ERROR] Could not connect to database.")
        return

    # Ensure schema updates (like adding api_key column) are applied
    ensure_db_schema()
    
    cur = conn.cursor()
    try:
        # Check if user exists
        cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user_row = cur.fetchone()
        
        if not user_row:
            print(f"[ERROR] User with email '{email}' not found. Please log in via the Web UI first to create the account.")
            return

        user_id = user_row[0]
        user_name = user_row[1]
        
        # Generate Secure Key
        new_key = secrets.token_urlsafe(32) # robust 43 char string
        
        cur.execute("UPDATE users SET api_key = %s WHERE id = %s", (new_key, user_id))
        conn.commit()
        
        print(f"\n[SUCCESS] API Key generated for {user_name} ({email})")
        print(f"User ID: {user_id}")
        print("="*60)
        print(f"API KEY: {new_key}")
        print("="*60)
        print("Use this key in your ESP32 code as the Bearer token.")
        
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] {e}")
    finally:
        cur.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_key.py <email>")
        print("Example: python generate_key.py debargha@example.com")
    else:
        generate_key_for_user(sys.argv[1])
