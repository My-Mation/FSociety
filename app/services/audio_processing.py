import math
import os
from app.db import get_db

# Configurable limits
IQR_LIMIT = None
try:
    IQR_LIMIT = float(os.getenv('IQR_LIMIT', '80'))
except Exception:
    IQR_LIMIT = 80.0

# =========================
# SILENCE DETECTION THRESHOLDS
# =========================
AMPLITUDE_THRESHOLD = 0.02  # Capture background sounds (lowered from 0.1)
CONFIDENCE_THRESHOLD = 0.1  # Lower FFT confidence requirement for quieter sounds

# =========================
# ONLINE ML MODEL (EWMA for anomaly)
# =========================
class NoiseModel:
    def __init__(self, alpha=0.02):
        self.alpha = alpha
        self.expected_noise = None
        self.variance = None
        self.initialized = False

    def update(self, amplitude):
        if not self.initialized:
            self.expected_noise = amplitude
            self.variance = 1.0
            self.initialized = True
            return 0.0, False

        diff = amplitude - self.expected_noise
        self.expected_noise += self.alpha * diff
        self.variance += self.alpha * (diff**2 - self.variance)

        std = math.sqrt(self.variance) if self.variance > 0 else 1.0
        z_score = abs(diff) / std

        anomaly = z_score >= 3.0
        return z_score, anomaly

noise_model = NoiseModel()

# =========================
# MULTI-MACHINE IDENTIFICATION (MULTI-BAND MATCHING)
# =========================
# Minimum amplitude threshold for peaks to be considered in detection
MIN_PEAK_AMP = 0.15
# Minimum number of frequency bands that must match for detection
MIN_BAND_MATCHES = 2

def identify_machines(peaks_list):
    """
    Match detected peaks to machine profiles using multi-band matching.
    A machine is detected ONLY if ≥2 frequency bands match in the same frame.
    
    Args:
        peaks_list: List of {freq, amp} for a single frame (should be top 3-5 peaks)
    
    Returns:
        List of machine_ids detected in this frame
    """
    if not peaks_list or len(peaks_list) == 0:
        return {"detected": [], "anomaly": []}

    conn = get_db()
    if conn is None:
        return {"detected": [], "anomaly": []}
        
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT machine_id, freq_bands, iqr_low, iqr_high FROM machine_profiles ORDER BY machine_id"
        )
        profiles = cursor.fetchall()
        if not profiles:
            return {"detected": [], "anomaly": []}

        detected_machines = set()
        anomaly_machines = set()

        for machine_id, freq_bands, iqr_low, iqr_high in profiles:
            if freq_bands and len(freq_bands) > 0:
                match_count = 0
                anomaly_count = 0
                for band in freq_bands:
                    band_low = band.get("low", 0)
                    band_high = band.get("high", 0)
                    for peak in peaks_list:
                        freq = peak.get("freq")
                        amp = peak.get("amp", 0)
                        if not freq or freq <= 0 or amp < MIN_PEAK_AMP:
                            continue
                        if band_low <= freq <= band_high:
                            match_count += 1
                            break
                        # Check for anomaly: within 5-10Hz outside the band
                        elif (band_low - 10 <= freq < band_low - 5) or (band_high + 5 < freq <= band_high + 10):
                            anomaly_count += 1
                            break
                if match_count >= MIN_BAND_MATCHES:
                    detected_machines.add(machine_id)
                elif anomaly_count >= MIN_BAND_MATCHES:
                    anomaly_machines.add(machine_id)
            elif iqr_low is not None and iqr_high is not None:
                normal = False
                anomaly = False
                for peak in peaks_list:
                    freq = peak.get("freq")
                    amp = peak.get("amp", 0)
                    if not freq or freq <= 0 or amp < MIN_PEAK_AMP:
                        continue
                    if iqr_low <= freq <= iqr_high:
                        normal = True
                    elif (iqr_low - 10 <= freq < iqr_low - 5) or (iqr_high + 5 < freq <= iqr_high + 10):
                        anomaly = True
                if normal:
                    detected_machines.add(machine_id)
                elif anomaly:
                    anomaly_machines.add(machine_id)

        return {"detected": list(detected_machines), "anomaly": list(anomaly_machines)}
    finally:
        cursor.close()
