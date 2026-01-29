from app.db import get_db
import sys

def check_keys():
    conn = get_db()
    if not conn:
        print("No DB Connection")
        return

    cur = conn.cursor()
    try:
        # Check if api_key column exists
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='api_key'")
        if not cur.fetchone():
            print("❌ 'api_key' column MISSING from 'users' table. Restart the server to run migrations.")
            return

        cur.execute("SELECT email, api_key FROM users")
        rows = cur.fetchall()
        print(f"Found {len(rows)} users.")
        for email, key in rows:
            status = "✅ HAS KEY" if key else "❌ NO KEY"
            print(f"User: {email} -> {status}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        cur.close()

if __name__ == "__main__":
    check_keys()
