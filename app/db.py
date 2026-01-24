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
        # Create raw_audio if missing
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
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        # Ensure columns exist on existing table
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='raw_audio'")
        cols = {r[0] for r in cur.fetchall()}
        if 'machine_id' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN machine_id VARCHAR(50);")
        if 'mode' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN mode VARCHAR(20) DEFAULT 'live';")
        if 'peaks' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN peaks JSONB;")

        # Create machine_profiles table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS machine_profiles (
            machine_id VARCHAR(50) PRIMARY KEY,
            median_freq FLOAT NOT NULL,
            iqr_low FLOAT NOT NULL,
            iqr_high FLOAT NOT NULL,
            freq_bands JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """)
        
        # Ensure profile columns
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='machine_profiles'")
        profile_cols = {r[0] for r in cur.fetchall()}
        if 'freq_bands' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN freq_bands JSONB;")
        if 'vibration_data' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN vibration_data JSONB;")
        if 'gas_data' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN gas_data JSONB;")

        # Create esp32_data table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS esp32_data (
            id SERIAL PRIMARY KEY,
            device_id VARCHAR(50),
            timestamp TIMESTAMP DEFAULT NOW(),
            vibration FLOAT,
            event_count INTEGER,
            gas_raw FLOAT,
            gas_status VARCHAR(20),
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)
        
        # Create indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_device_id ON esp32_data(device_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_timestamp ON esp32_data(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_timestamp ON raw_audio(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_id ON raw_audio(machine_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_mode ON raw_audio(mode);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_mode ON raw_audio(machine_id, mode);")

        conn.commit()
        print("[OK] Database schema ensured")
    except Exception as e:
        conn.rollback()
        print("[ERROR] Error ensuring DB schema:", str(e))
        traceback.print_exc()
    finally:
        cur.close()
