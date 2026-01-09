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
-- NEW TABLE: Machine Profiles
-- ========================================
CREATE TABLE IF NOT EXISTS machine_profiles (
    machine_id VARCHAR(50) PRIMARY KEY,
    mean_freq FLOAT NOT NULL,
    std_freq FLOAT NOT NULL,
    min_freq FLOAT NOT NULL,
    max_freq FLOAT NOT NULL,
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
-- Insert sample machine profiles:
-- INSERT INTO machine_profiles (machine_id, mean_freq, std_freq, min_freq, max_freq)
-- VALUES 
--   ('machine_1', 250.0, 15.0, 235.0, 265.0),
--   ('machine_2', 500.0, 20.0, 480.0, 520.0),
--   ('machine_3', 750.0, 25.0, 725.0, 775.0),
--   ('machine_4', 1000.0, 30.0, 970.0, 1030.0);
