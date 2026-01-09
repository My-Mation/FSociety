-- ========================================
-- MACHINE SOUND CALIBRATION SYSTEM
-- PostgreSQL Schema
-- ========================================

-- ========================================
-- EXISTING TABLE (UPDATE WITH NEW COLUMNS)
-- ========================================
-- If raw_audio table already exists, run this ALTER:

ALTER TABLE raw_audio
ADD COLUMN IF NOT EXISTS machine_id VARCHAR(50),
ADD COLUMN IF NOT EXISTS mode VARCHAR(20) DEFAULT 'live';

-- ========================================
-- NEW TABLE: Machine Profiles (UPDATED)
-- ========================================
-- NEW columns for IQR-based detection (replaces mean_freq/std_freq)
CREATE TABLE IF NOT EXISTS machine_profiles (
    machine_id VARCHAR(50) PRIMARY KEY,
    median_freq FLOAT NOT NULL,
    iqr_low FLOAT NOT NULL,
    iqr_high FLOAT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ========================================
-- IF raw_audio DOESN'T EXIST, create it:
-- ========================================
CREATE TABLE IF NOT EXISTS raw_audio (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    amplitude FLOAT NOT NULL,
    dominant_freq FLOAT,
    freq_confidence FLOAT,
    machine_id VARCHAR(50),
    mode VARCHAR(20) DEFAULT 'live',
    created_at TIMESTAMP DEFAULT NOW()
);

-- ========================================
-- INDEXES FOR PERFORMANCE
-- ========================================
CREATE INDEX IF NOT EXISTS idx_raw_audio_timestamp ON raw_audio(timestamp);
CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_id ON raw_audio(machine_id);
CREATE INDEX IF NOT EXISTS idx_raw_audio_mode ON raw_audio(mode);
CREATE INDEX IF NOT EXISTS idx_raw_audio_machine_mode ON raw_audio(machine_id, mode);

-- ========================================
-- SAMPLE DATA (OPTIONAL - for testing)
-- ========================================
-- Insert sample machine profiles with distinct frequency bands (40+ Hz apart):
-- INSERT INTO machine_profiles (machine_id, median_freq, iqr_low, iqr_high)
-- VALUES 
--   ('machine_1', 250.0, 230.0, 270.0),    -- 40 Hz IQR, centered at 250 Hz
--   ('machine_2', 520.0, 500.0, 540.0),    -- 40 Hz IQR, centered at 520 Hz
--   ('machine_3', 780.0, 760.0, 800.0),    -- 40 Hz IQR, centered at 780 Hz
--   ('machine_4', 1040.0, 1020.0, 1060.0); -- 40 Hz IQR, centered at 1040 Hz
