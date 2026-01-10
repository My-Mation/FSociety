-- ========================================
-- MACHINE SOUND CALIBRATION SYSTEM
-- PostgreSQL Schema (IQR-based, FINAL)
-- ========================================

BEGIN;

-- ========================================
-- RAW AUDIO TABLE
-- ========================================
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

-- Ensure missing columns (safe for upgrades)
ALTER TABLE raw_audio
ADD COLUMN IF NOT EXISTS peaks JSONB,
ADD COLUMN IF NOT EXISTS machine_id VARCHAR(50),
ADD COLUMN IF NOT EXISTS mode VARCHAR(20) DEFAULT 'live';

-- ========================================
-- MACHINE PROFILES (MULTI-BAND FREQUENCY CLUSTERS)
-- ========================================
CREATE TABLE IF NOT EXISTS machine_profiles (
    machine_id VARCHAR(50) PRIMARY KEY,
    median_freq FLOAT NOT NULL,
    iqr_low FLOAT NOT NULL,
    iqr_high FLOAT NOT NULL,
    freq_bands JSONB,  -- Array of {center, low, high} frequency bands for harmonics
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Add freq_bands column if missing (for existing databases)
ALTER TABLE machine_profiles
ADD COLUMN IF NOT EXISTS freq_bands JSONB;

-- ========================================
-- ESP32 SENSOR DATA TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS esp32_data (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP DEFAULT NOW(),
    vibration FLOAT,
    event_count INTEGER,
    gas_raw INTEGER,
    gas_status VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for ESP32 data
CREATE INDEX IF NOT EXISTS idx_esp32_device_id
    ON esp32_data(device_id);

CREATE INDEX IF NOT EXISTS idx_esp32_timestamp
    ON esp32_data(timestamp);

-- ========================================
-- INDEXES (PERFORMANCE CRITICAL)
-- ========================================
CREATE INDEX IF NOT EXISTS idx_raw_audio_timestamp
    ON raw_audio(timestamp);

CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_id
    ON raw_audio(machine_id);

CREATE INDEX IF NOT EXISTS idx_raw_audio_mode
    ON raw_audio(mode);

CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_mode
    ON raw_audio(machine_id, mode);

COMMIT;
