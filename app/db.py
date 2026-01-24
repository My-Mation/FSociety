import psycopg2
import psycopg2.extras
import traceback
import os

# Global connection to maintain compatibility with original logic
# In a production app, use a connection pool or open per-request
try:
    conn = psycopg2.connect(
        host="localhost",
        database="soundml",
        user="postgres",
        password="Debargha"
    )
    conn.autocommit = False 
except Exception as e:
    print(f"[ERROR] Database connection failed: {e}")
    conn = None

def get_db():
    """Return the global connection object"""
    return conn

def ensure_db_schema():
    """Create required tables and indexes if they do not exist."""
    if conn is None:
        print("[ERROR] Cannot ensure schema: No DB connection")
        return

    cur = conn.cursor()
    try:
        # 1. Create USERS table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            google_id VARCHAR(255) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        # 2. Check if raw_audio has user_id, if not, we are migrating 
        # (Strategy: TRUNCATE tables to enforce NOT NULL user_id immediately)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='raw_audio' AND column_name='user_id'")
        if not cur.fetchone():
            print("[MIGRATION] Adding user_id to tables. TRUNCATING DATA for strict safety.")
            # Verify tables exist before truncating to avoid errors on fresh install
            cur.execute("SELECT to_regclass('raw_audio')")
            if cur.fetchone()[0]:
                cur.execute("TRUNCATE TABLE raw_audio, machine_profiles, esp32_data CASCADE")
                
                cur.execute("ALTER TABLE raw_audio ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL")
                cur.execute("ALTER TABLE machine_profiles ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL")
                cur.execute("ALTER TABLE esp32_data ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL")
                
                # Update Constraints for machine_profiles
                cur.execute("ALTER TABLE machine_profiles DROP CONSTRAINT IF EXISTS machine_profiles_pkey")
                cur.execute("ALTER TABLE machine_profiles ADD PRIMARY KEY (user_id, machine_id)")
        
        # Ensure tables exist (for fresh install)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_audio (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            amplitude FLOAT NOT NULL,
            dominant_freq FLOAT,
            freq_confidence FLOAT,
            peaks JSONB,
            machine_id VARCHAR(50),
            mode VARCHAR(20) DEFAULT 'live',
            created_at TIMESTAMP DEFAULT NOW(),
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS machine_profiles (
            machine_id VARCHAR(50),
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL,
            median_freq FLOAT NOT NULL,
            iqr_low FLOAT NOT NULL,
            iqr_high FLOAT NOT NULL,
            freq_bands JSONB,
            vibration_data JSONB,
            gas_data JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, machine_id)
        );
        """)
        
        cur.execute("""
        CREATE TABLE IF NOT EXISTS esp32_data (
            id SERIAL PRIMARY KEY,
            device_id VARCHAR(50),
            timestamp TIMESTAMP DEFAULT NOW(),
            vibration FLOAT,
            event_count INTEGER,
            gas_raw FLOAT,
            gas_status VARCHAR(20),
            created_at TIMESTAMP DEFAULT NOW(),
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL
        );
        """)
        
        # Create indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_user_id ON raw_audio(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_machine_profiles_user_id ON machine_profiles(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_user_id ON esp32_data(user_id);")
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_device_id ON esp32_data(device_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_timestamp ON esp32_data(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_timestamp ON raw_audio(timestamp);")

        conn.commit()
        print("[OK] Database schema ensured with USER ISOLATION")
    except Exception as e:
        conn.rollback()
        print("[ERROR] Error ensuring DB schema:", str(e))
        traceback.print_exc()
    finally:
        cur.close()
