from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import psycopg2
import psycopg2.extras
import math
from datetime import datetime
import os
import threading
import queue
import json
import time
import traceback

print(">>> MACHINE SOUND CALIBRATION & DETECTION SYSTEM <<<")

# =========================
# FLASK APP
# =========================
app = Flask(__name__)

# ✅ FIX: Trust forwarded headers from ngrok
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True
)

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_port=1
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================
# DATABASE CONNECTION
# =========================
conn = psycopg2.connect(
    host="localhost",
    database="soundml",
    user="postgres",
    password="Debargha"
)

# Configurable limits
IQR_LIMIT = None
try:
    IQR_LIMIT = float(os.getenv('IQR_LIMIT', '80'))
except Exception:
    IQR_LIMIT = 80.0


def ensure_db_schema():
    """Create required tables and indexes if they do not exist."""
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

        # Ensure columns exist on existing table (safe with IF NOT EXISTS behavior via ALTER)
        # PostgreSQL doesn't support IF NOT EXISTS for ADD COLUMN until newer versions,
        # so we guard with a simple check.
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='raw_audio'")
        cols = {r[0] for r in cur.fetchall()}
        if 'machine_id' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN machine_id VARCHAR(50);")
        if 'mode' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN mode VARCHAR(20) DEFAULT 'live';")
        if 'peaks' not in cols:
            cur.execute("ALTER TABLE raw_audio ADD COLUMN peaks JSONB;")

        # Create machine_profiles table with freq_bands for multi-band detection
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
        
        # Ensure freq_bands column exists (for existing databases)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='machine_profiles'")
        profile_cols = {r[0] for r in cur.fetchall()}
        if 'freq_bands' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN freq_bands JSONB;")
        if 'vibration_data' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN vibration_data JSONB;")
        if 'gas_data' not in profile_cols:
            cur.execute("ALTER TABLE machine_profiles ADD COLUMN gas_data JSONB;")

        # Create esp32_data table for ESP32 sensor data
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
        
        # Create indexes for esp32_data
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_device_id ON esp32_data(device_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_esp32_data_timestamp ON esp32_data(timestamp);")

        # Create indexes
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

# Ensure schema on startup
ensure_db_schema()

# ❌ REMOVED: cursor = conn.cursor()
# ✅ Create new cursor per request instead

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
# TEMPORAL STABILITY TRACKING (for multi-machine detection)
# =========================
# Track last N detections per machine for stability filtering
STABILITY_WINDOW = 15  # Track last 15 batches
STABILITY_THRESHOLD = 0.6  # Require 60% detection rate

# machine_id -> list of 1s (detected) and 0s (not detected) in last N batches
detection_history = {}

def update_detection_history(running_machines, all_machines_in_profile):
    """Update detection history for temporal stability filtering"""
    for machine_id in all_machines_in_profile:
        if machine_id not in detection_history:
            detection_history[machine_id] = []
        
        detected = 1 if machine_id in running_machines else 0
        detection_history[machine_id].append(detected)
        
        # Keep only last STABILITY_WINDOW entries
        if len(detection_history[machine_id]) > STABILITY_WINDOW:
            detection_history[machine_id].pop(0)

def get_stable_machines(all_machines_in_profile):
    """Return only machines detected in ≥60% of last STABILITY_WINDOW batches"""
    stable = []
    for machine_id in all_machines_in_profile:
        if machine_id not in detection_history or len(detection_history[machine_id]) < 5:
            continue  # Need at least 5 observations
        
        detection_rate = sum(detection_history[machine_id]) / len(detection_history[machine_id])
        if detection_rate >= STABILITY_THRESHOLD:
            stable.append(machine_id)
    
    return stable

# =========================
# SILENCE DETECTION THRESHOLDS
# =========================
AMPLITUDE_THRESHOLD = 0.02  # Capture background sounds (lowered from 0.1)
CONFIDENCE_THRESHOLD = 0.1  # Lower FFT confidence requirement for quieter sounds

# =========================
# BATCH QUEUE FOR ASYNC PROCESSING
# =========================
# Queue to hold incoming large payloads so the HTTP response can return quickly
BATCH_QUEUE = queue.Queue()
FAILED_BATCH_DIR = os.path.join(BASE_DIR, 'failed_batches')
os.makedirs(FAILED_BATCH_DIR, exist_ok=True)


def persist_failed_batch(payload):
    try:
        ts = int(time.time() * 1000)
        path = os.path.join(FAILED_BATCH_DIR, f'failed_{ts}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        print(f"[WARN] Persisted failed batch to {path}")
    except Exception as e:
        print("[ERROR] Failed to persist batch:", str(e))


def batch_worker():
    """Background worker to process queued batches."""
    while True:
        batch = BATCH_QUEUE.get()
        if batch is None:
            break

        try:
            # Reuse ingest logic but in worker context (create fresh cursor)
            mode = batch.get('mode', 'live')

            if mode == 'calibration':
                frames = batch.get('frames', [])
                frames_captured = batch.get('frames_captured', len(frames))
                machine_id = batch.get('machine_id')

                if not frames or not machine_id:
                    print('[WARN] Skipping invalid calibration batch (missing fields)')
                    continue

                cursor = conn.cursor()
                inserted_count = 0
                for frame in frames:
                    amplitude = frame.get('amplitude')
                    peaks = frame.get('peaks', [])
                    timestamp = frame.get('timestamp')

                    if amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0:
                        continue

                    dominant_freq = peaks[0].get('freq') if len(peaks) > 0 else None
                    freq_confidence = peaks[0].get('amp') if len(peaks) > 0 else None

                    cursor.execute(
                        """
                        INSERT INTO raw_audio
                        (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            datetime.fromtimestamp(timestamp / 1000),
                            amplitude,
                            dominant_freq,
                            freq_confidence,
                            psycopg2.extras.Json(peaks),
                            machine_id,
                            mode
                        )
                    )
                    inserted_count += 1

                conn.commit()
                cursor.close()
                print(f"\n[OK] (worker) CALIBRATION BATCH: {machine_id} inserted: {inserted_count} (captured={frames_captured})")

            elif mode == 'live':
                frames = batch.get('frames', [])
                frames_captured = batch.get('frames_captured', len(frames))
                if not frames:
                    print('[WARN] Skipping empty live batch')
                    continue

                cursor = conn.cursor()
                inserted_count = 0
                running_machines = set()

                for frame in frames:
                    amplitude = frame.get('amplitude')
                    peaks = frame.get('peaks', [])
                    timestamp = frame.get('timestamp')

                    if amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0:
                        continue

                    z_score, anomaly = noise_model.update(amplitude)

                    dominant_freq = peaks[0].get('freq') if len(peaks) > 0 else None
                    freq_confidence = peaks[0].get('amp') if len(peaks) > 0 else None

                    cursor.execute(
                        """
                        INSERT INTO raw_audio
                        (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            datetime.fromtimestamp(timestamp / 1000),
                            amplitude,
                            dominant_freq,
                            freq_confidence,
                            psycopg2.extras.Json(peaks),
                            None,
                            mode
                        )
                    )
                    inserted_count += 1

                    machines_in_frame = identify_machines(peaks)
                    running_machines.update(machines_in_frame)

                conn.commit()

                # Update temporal stability (fetch all machine ids)
                cursor.execute("SELECT machine_id FROM machine_profiles")
                all_machines = [row[0] for row in cursor.fetchall()]
                update_detection_history(running_machines, all_machines)
                stable_machines = get_stable_machines(all_machines)

                cursor.close()
                print(f"\n[OK] (worker) LIVE BATCH: {len(frames)} frames, {inserted_count} inserted (captured={frames_captured})")
                print(f"   Detected (raw): {sorted(running_machines)}")
                print(f"   Stable machines: {sorted(stable_machines)}")

            else:
                print('[WARN] Unknown batch mode:', mode)

        except Exception as e:
            print('[ERROR] Batch worker error:', str(e))
            traceback.print_exc()
            try:
                persist_failed_batch(batch)
            except Exception:
                pass

        finally:
            BATCH_QUEUE.task_done()


# Start worker thread
worker_thread = threading.Thread(target=batch_worker, daemon=True)
worker_thread.start()

# =========================
# SERVE HTML
# =========================
@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))


@app.route("/esp32")
def esp32_dashboard():
    """Serve ESP32 sensor monitoring dashboard"""
    return send_file(os.path.join(BASE_DIR, "esp32_dashboard.html"))


@app.route("/esp32_style.css")
def esp32_style():
    """Serve ESP32 dashboard CSS"""
    return send_file(os.path.join(BASE_DIR, "esp32_style.css"))


@app.route("/esp32_app.js")
def esp32_app():
    """Serve ESP32 dashboard JavaScript"""
    return send_file(os.path.join(BASE_DIR, "esp32_app.js"))


@app.route("/ingest_batch", methods=["POST"])
def ingest_batch():
    """Accept large batch payloads and enqueue for background processing.

    Returns 202 Accepted immediately so upstream proxies (nginx) won't timeout.
    """
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        print('[ERROR] ingest_batch: invalid JSON', str(e))
        return jsonify({"error": "invalid json"}), 400

    # Simple validation
    if not payload or 'frames' not in payload:
        return jsonify({"error": "frames required"}), 400

    try:
        BATCH_QUEUE.put_nowait(payload)
        qsize = BATCH_QUEUE.qsize()
        return jsonify({
            "status": "accepted",
            "queue_size": qsize
        }), 202
    except queue.Full:
        # Very unlikely; persist to disk for manual recovery
        persist_failed_batch(payload)
        return jsonify({"error": "queue full, persisted"}), 503

# =========================
# INGEST ROUTE (BATCH PROCESSING)
# =========================
@app.route("/ingest", methods=["POST"])
def ingest():
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        data = request.get_json(force=True)
        mode = data.get("mode", "live")

        # ---- CALIBRATION MODE: Bulk insert from client ----
        # Allow caller to force storing all frames (even low amplitude / no peaks)
        store_all = data.get("store_all", False)

        if mode == "calibration":
            frames = data.get("frames", [])  # List of frame objects with peaks
            machine_id = data.get("machine_id")
            # Number of frames actually captured by the client (optional)
            frames_captured = data.get("frames_captured", len(frames))

            if not frames or not machine_id:
                return jsonify({"error": "frames and machine_id required"}), 400

            inserted_count = 0
            for frame in frames:
                amplitude = frame.get("amplitude")
                peaks = frame.get("peaks", [])  # Array of {freq, amp}
                timestamp = frame.get("timestamp")

                # If not forcing storage, apply silence/peak filtering as before
                if not store_all and (amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0):
                    continue

                # Store strongest peak if available, otherwise NULLs
                if len(peaks) > 0:
                    dominant_freq = peaks[0].get("freq")
                    freq_confidence = peaks[0].get("amp")
                else:
                    dominant_freq = None
                    freq_confidence = None

                ts = datetime.fromtimestamp(timestamp / 1000) if timestamp else datetime.now()

                cursor.execute(
                    """
                    INSERT INTO raw_audio
                    (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ts,
                        amplitude,
                        dominant_freq,
                        freq_confidence,
                        psycopg2.extras.Json(peaks),
                        machine_id,
                        mode
                    )
                )
                inserted_count += 1

            conn.commit()

            print(f"\n[OK] CALIBRATION BATCH: {machine_id}")
            print(f"   Frames received: {len(frames)}, Frames inserted: {inserted_count} (store_all={store_all})")

            return jsonify({
                "status": "calibration_batch_saved",
                "frames_received": len(frames),
                "frames_captured": frames_captured,
                "frames_inserted": inserted_count,
                "machine_id": machine_id
            })

        # ---- LIVE DETECTION MODE: Process frames and detect ----
        elif mode == "live":
            frames = data.get("frames", [])  # List of frame objects
            frames_captured = data.get("frames_captured", len(frames))

            if not frames:
                return jsonify({"error": "frames required"}), 400

            # ✅ NEW: Track machines detected in this batch
            running_machines = set()
            anomaly_machines = set()
            inserted_count = 0

            for frame in frames:
                amplitude = frame.get("amplitude")
                peaks = frame.get("peaks", [])  # Array of {freq, amp}
                timestamp = frame.get("timestamp")

                if not store_all and (amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0):
                    continue

                z_score, anomaly = noise_model.update(amplitude)

                if len(peaks) > 0:
                    dominant_freq = peaks[0].get("freq")
                    freq_confidence = peaks[0].get("amp")
                else:
                    dominant_freq = None
                    freq_confidence = None

                ts = datetime.fromtimestamp(timestamp / 1000) if timestamp else datetime.now()

                cursor.execute(
                    """
                    INSERT INTO raw_audio
                    (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ts,
                        amplitude,
                        dominant_freq,
                        freq_confidence,
                        psycopg2.extras.Json(peaks),
                        None,  # No machine_id during live detection
                        mode
                    )
                )
                inserted_count += 1

                # Multi-machine detection using peaks if present
                if len(peaks) > 0:
                    result = identify_machines(peaks)
                    running_machines.update(result["detected"])
                    anomaly_machines.update(result["anomaly"])

            conn.commit()

            cursor.execute("SELECT machine_id FROM machine_profiles")
            all_machines = [row[0] for row in cursor.fetchall()]
            update_detection_history(running_machines, all_machines)
            stable_machines = get_stable_machines(all_machines)

            print(f"\n[OK] LIVE BATCH: {len(frames)} frames, {inserted_count} inserted (store_all={store_all})")
            print(f"   Detected (raw): {sorted(running_machines)}")
            print(f"   Stable machines: {sorted(stable_machines)}")
            print(f"   Anomaly machines: {sorted(anomaly_machines)}")

            return jsonify({
                "status": "ok",
                "frames_received": len(frames),
                "frames_captured": frames_captured,
                "frames_inserted": inserted_count,
                "running_machines": sorted(stable_machines),
                "running_machines_raw": sorted(running_machines),
                "anomaly_machines": sorted(anomaly_machines),
                "all_machines": all_machines
            })

        else:
            return jsonify({"error": "invalid mode"}), 400

    except Exception as e:
        conn.rollback()
        print("[ERROR] INGEST ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

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

# =========================
# SAVE CALIBRATION PROFILE (MULTI-BAND CLUSTERING)
# =========================
# Bin size for frequency clustering (Hz)
FREQ_BIN_SIZE = 15
# Minimum samples per cluster to be considered valid
MIN_CLUSTER_SAMPLES = 15

@app.route("/save_profile", methods=["POST"])
def save_profile():
    """
    Analyze calibration data and save machine profile with multiple frequency bands.
    Uses clustering to identify harmonic frequencies.
    Expects: {"machine_id": "machine_1"}
    """
    cursor = conn.cursor()

    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")
        # ESP32 calibration data from frontend
        vibration_samples = data.get("vibration_samples", [])
        gas_samples = data.get("gas_samples", [])
        
        # Debug logging
        print(f"[DEBUG] save_profile received: machine_id={machine_id}")
        print(f"[DEBUG] vibration_samples count: {len(vibration_samples) if vibration_samples else 0}")
        print(f"[DEBUG] gas_samples count: {len(gas_samples) if gas_samples else 0}")

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        # Process vibration data (IMPORTANT: 0 = vibration detected, 1 = no vibration)
        vibration_data = None
        if vibration_samples and len(vibration_samples) > 0:
            # Count how many samples had vibration (value 0)
            vibration_count = sum(1 for v in vibration_samples if v == 0)
            total_samples = len(vibration_samples)
            # Invert: 100% = no vibration, 0% = full vibration
            vibration_percent = (1 - (vibration_count / total_samples)) * 100 if total_samples > 0 else 0
            avg_raw = sum(vibration_samples) / total_samples if total_samples > 0 else 0
            vibration_data = {
                "samples": total_samples,
                "vibration_detected_count": vibration_count,
                "vibration_percent": round(vibration_percent, 1),
                "avg_raw_value": round(avg_raw, 3),
                "has_vibration": vibration_percent < 50
            }
        
        # Process gas data
        gas_data = None
        if gas_samples and len(gas_samples) > 0:
            raw_values = [g.get("raw", 0) if isinstance(g, dict) else g for g in gas_samples]
            # Filter out invalid readings (0 usually means sensor disconnected)
            valid_raw_values = [v for v in raw_values if v > 0]
            
            if valid_raw_values:
                avg_gas = sum(valid_raw_values) / len(valid_raw_values)
                max_gas = max(valid_raw_values)
                min_gas = min(valid_raw_values)
            else:
                avg_gas = 0
                max_gas = 0
                min_gas = 0
            
            # Determine overall status based on ESP32's own classification (most common)
            # or use thresholds appropriate for MQ gas sensors (0-4095 ADC range)
            # Typical: <800 = SAFE, 800-2000 = MODERATE, >2000 = HAZARDOUS
            if avg_gas == 0:
                gas_status = "NO_DATA"
            elif avg_gas < 800:
                gas_status = "SAFE"
            elif avg_gas < 2000:
                gas_status = "MODERATE"
            else:
                gas_status = "HAZARDOUS"
            
            gas_data = {
                "samples": len(gas_samples),
                "valid_samples": len(valid_raw_values),
                "avg_raw": round(avg_gas, 1),
                "max_raw": round(max_gas, 1),
                "min_raw": round(min_gas, 1),
                "status": gas_status
            }

        # 1. FETCH CALIBRATION DATA (all peaks, not just dominant)
        cursor.execute(
            """
            SELECT peaks
            FROM raw_audio
            WHERE machine_id = %s AND mode = 'calibration'
            ORDER BY timestamp DESC
            LIMIT 3000
            """,
            (machine_id,)
        )
        rows = cursor.fetchall()

        if not rows:
            return jsonify({"error": "No calibration data found"}), 400

        # 2. EXTRACT ALL FREQUENCIES FROM TOP PEAKS
        all_frequencies = []
        for (peaks,) in rows:
            if peaks:
                # Sort peaks by amplitude (descending) and take top 5
                sorted_peaks = sorted(
                    [p for p in peaks if isinstance(p, dict) and p.get("freq", 0) > 0],
                    key=lambda x: x.get("amp", 0),
                    reverse=True
                )[:5]
                
                for p in sorted_peaks:
                    freq = p.get("freq")
                    amp = p.get("amp", 0)
                    if freq and freq > 0 and amp >= 0.1:  # Only include peaks with reasonable amplitude
                        all_frequencies.append(freq)

        if len(all_frequencies) < 20:
            return jsonify({
                "error": f"Not enough valid frequencies ({len(all_frequencies)} < 20)"
            }), 400

        # 3. CLUSTER FREQUENCIES INTO BINS
        clusters = {}
        for freq in all_frequencies:
            # Round to nearest bin
            bucket = round(freq / FREQ_BIN_SIZE) * FREQ_BIN_SIZE
            clusters.setdefault(bucket, []).append(freq)

        # 4. BUILD FREQUENCY BANDS FROM SIGNIFICANT CLUSTERS
        freq_bands = []
        for bucket, freqs in sorted(clusters.items()):
            if len(freqs) < MIN_CLUSTER_SAMPLES:
                continue  # Skip sparse clusters
            
            freqs_sorted = sorted(freqs)
            n = len(freqs_sorted)
            
            # Compute quartiles for this cluster
            q1 = freqs_sorted[n // 4]
            q3 = freqs_sorted[3 * n // 4]
            center = sum(freqs_sorted) / n
            
            # IQR-based bounds for this band
            iqr = q3 - q1
            band_low = max(0, q1 - 0.5 * iqr)
            band_high = q3 + 0.5 * iqr
            
            freq_bands.append({
                "center": round(center, 2),
                "low": round(band_low, 2),
                "high": round(band_high, 2),
                "samples": n
            })

        # Limit to top 5 most significant bands (by sample count)
        freq_bands = sorted(freq_bands, key=lambda x: x["samples"], reverse=True)[:5]
        freq_bands = sorted(freq_bands, key=lambda x: x["center"])  # Sort by frequency

        if len(freq_bands) < 1:
            return jsonify({
                "error": "Could not identify any frequency bands from calibration data"
            }), 400

        # 5. COMPUTE OVERALL MEDIAN + IQR (for backward compatibility)
        all_frequencies.sort()
        n_total = len(all_frequencies)
        
        def percentile(vals, p):
            return vals[int(p * (len(vals) - 1))]

        median_freq = percentile(all_frequencies, 0.5)
        q1 = percentile(all_frequencies, 0.25)
        q3 = percentile(all_frequencies, 0.75)
        iqr = q3 - q1
        iqr_low = max(0, q1 - 0.5 * iqr)
        iqr_high = q3 + 0.5 * iqr

        print(f"\n=== PROFILE CREATED: {machine_id} ===")
        print(f"Total frequencies analyzed: {n_total}")
        print(f"Median frequency: {median_freq:.2f} Hz")
        print(f"Overall IQR range: {iqr_low:.2f} – {iqr_high:.2f} Hz")
        print(f"Frequency bands detected: {len(freq_bands)}")
        for i, band in enumerate(freq_bands):
            print(f"  Band {i+1}: {band['low']:.1f} – {band['high']:.1f} Hz (center: {band['center']:.1f}, samples: {band['samples']})")

        # 6. SAVE PROFILE WITH FREQ_BANDS AND ESP32 DATA
        cursor.execute(
            """
            INSERT INTO machine_profiles
                (machine_id, median_freq, iqr_low, iqr_high, freq_bands, vibration_data, gas_data, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (machine_id)
            DO UPDATE SET
                median_freq = EXCLUDED.median_freq,
                iqr_low = EXCLUDED.iqr_low,
                iqr_high = EXCLUDED.iqr_high,
                freq_bands = EXCLUDED.freq_bands,
                vibration_data = EXCLUDED.vibration_data,
                gas_data = EXCLUDED.gas_data,
                updated_at = NOW()
            """,
            (machine_id, median_freq, iqr_low, iqr_high, 
             psycopg2.extras.Json(freq_bands),
             psycopg2.extras.Json(vibration_data) if vibration_data else None,
             psycopg2.extras.Json(gas_data) if gas_data else None)
        )
        conn.commit()

        print(f"   Vibration data: {vibration_data}")
        print(f"   Gas data: {gas_data}")

        return jsonify({
            "status": "profile_saved",
            "machine_id": machine_id,
            "median_freq": round(median_freq, 2),
            "iqr": round(iqr, 2),
            "iqr_low": round(iqr_low, 2),
            "iqr_high": round(iqr_high, 2),
            "freq_bands": freq_bands,
            "bands_count": len(freq_bands),
            "frames_used": n_total,
            "vibration_data": vibration_data,
            "gas_data": gas_data
        })

    except Exception as e:
        conn.rollback()
        print("[ERROR] SAVE_PROFILE ERROR:", str(e))
        traceback.print_exc()
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()
# =========================
@app.route("/profiles", methods=["GET"])
def get_profiles():
    """Return all trained machine profiles with IQR-based ranges"""
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        cursor.execute(
            """
            SELECT machine_id, median_freq, iqr_low, iqr_high, freq_bands, vibration_data, gas_data, created_at
            FROM machine_profiles
            ORDER BY machine_id
            """
        )
        
        rows = cursor.fetchall()
        profiles = []
        for row in rows:
            machine_id = row[0]
            median_freq = row[1]
            iqr_low = row[2]
            iqr_high = row[3]
            freq_bands = row[4]
            vibration_data = row[5]
            gas_data = row[6]
            created_at = row[7]

            def safe_round(val):
                return round(val, 2) if val is not None else None

            iqr_val = None
            if iqr_low is not None and iqr_high is not None:
                iqr_val = round(iqr_high - iqr_low, 2)

            # Add human-readable vibration status
            vibration_status_text = None
            if vibration_data and "vibration_percent" in vibration_data:
                percent = vibration_data["vibration_percent"]
                if percent >= 99.9:
                    vibration_status_text = "No vibration detected"
                elif percent <= 0.1:
                    vibration_status_text = "Always vibrating"
                else:
                    vibration_status_text = f"Intermittent vibration: {100-percent:.1f}% active"

            profiles.append({
                "machine_id": machine_id,
                "median_freq": safe_round(median_freq),
                "iqr_low": safe_round(iqr_low),
                "iqr_high": safe_round(iqr_high),
                "iqr": iqr_val,
                "freq_bands": freq_bands if freq_bands else [],
                "bands_count": len(freq_bands) if freq_bands else 0,
                "vibration_data": vibration_data,
                "vibration_status_text": vibration_status_text,
                "gas_data": gas_data,
                "created_at": str(created_at) if created_at is not None else None
            })

        return jsonify(profiles)

    except Exception as e:
        # Log error but return empty list instead of a 500 to keep frontend robust
        print("[ERROR] GET_PROFILES ERROR:", str(e))
        traceback.print_exc()
        return jsonify([]), 200
    finally:
        cursor.close()  # ✅ ALWAYS CLOSE


def preview_profile():
    """Compute median/IQR for a machine without saving the profile.
    Expects: {"machine_id": "machine_1", "frames_captured": 123}
    Returns metrics JSON for UI preview.
    """
    cursor = conn.cursor()
    try:
        data = request.get_json(force=True)
        machine_id = data.get('machine_id')

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        cursor.execute(
            """
            SELECT dominant_freq, peaks
            FROM raw_audio
            WHERE machine_id = %s AND mode = 'calibration'
            ORDER BY timestamp DESC
            LIMIT 2000
            """,
            (machine_id,)
        )
        rows = cursor.fetchall()
        freqs = []
        for r in rows:
            dom = r[0]
            peaks = r[1]
            if peaks:
                try:
                    for p in peaks:
                        f = p.get('freq') if isinstance(p, dict) else None
                        if f and f > 0:
                            freqs.append(f)
                except Exception:
                    pass
            elif dom and dom > 0:
                freqs.append(dom)

        freqs = sorted(freqs)

        frames_captured = data.get('frames_captured', len(freqs))
        frames_used = len(freqs)

        if frames_used == 0:
            return jsonify({"error": "no_valid_frequencies", "frames_captured": frames_captured, "frames_used": frames_used}), 200

        def percentile(sorted_vals, pct):
            idx = int(pct * (len(sorted_vals) - 1))
            return sorted_vals[idx]

        median_freq = percentile(freqs, 0.5)
        q1 = percentile(freqs, 0.25)
        q3 = percentile(freqs, 0.75)
        iqr = q3 - q1
        iqr_low = max(0, q1 - 0.5 * iqr)
        iqr_high = q3 + 0.5 * iqr

        return jsonify({
            "machine_id": machine_id,
            "frames_captured": frames_captured,
            "frames_used": frames_used,
            "median_freq": round(median_freq,2),
            "iqr": round(iqr,2),
            "iqr_low": round(iqr_low,2),
            "iqr_high": round(iqr_high,2)
        })

    except Exception as e:
        print('[ERROR] PREVIEW_PROFILE ERROR:', str(e))
        traceback.print_exc()
        return jsonify({"error": "server error"}), 500
    finally:
        cursor.close()

# =========================
# DELETE MACHINE PROFILE
# =========================
@app.route("/delete_profile", methods=["POST"])
def delete_profile():
    """Delete a trained machine profile by machine_id"""
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        # Delete from machine_profiles
        cursor.execute(
            "DELETE FROM machine_profiles WHERE machine_id = %s",
            (machine_id,)
        )
        
        deleted_count = cursor.rowcount
        conn.commit()

        if deleted_count > 0:
            print(f"[OK] PROFILE DELETED: {machine_id}")
            return jsonify({
                "status": "profile_deleted",
                "machine_id": machine_id
            })
        else:
            return jsonify({"error": f"No profile found for {machine_id}"}), 404

    except Exception as e:
        conn.rollback()
        print("[ERROR] DELETE_PROFILE ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# =========================
@app.route("/export_raw", methods=["GET"])
def export_raw():
    """Export raw audio data for analysis"""
    since = request.args.get("since", default=0, type=float)
    
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        cursor.execute(
            """
                        SELECT
                            EXTRACT(EPOCH FROM timestamp) AS ts,
                            amplitude,
                            dominant_freq,
                            freq_confidence,
                            peaks,
                            machine_id,
                            mode
                        FROM raw_audio
                        WHERE EXTRACT(EPOCH FROM timestamp) > %s
                        ORDER BY timestamp
                        """,
                        (since,)
        )

        rows = cursor.fetchall()
        data = [
            {
                "ts": r[0],
                "amplitude": r[1],
                                "dominant_freq": r[2],
                                "freq_confidence": r[3],
                                "peaks": r[4],
                                "machine_id": r[5],
                                "mode": r[6]
            }
            for r in rows
        ]

        return jsonify(data)

    except Exception as e:
        print("[ERROR] EXPORT_RAW ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# ESP32 SENSOR DATA INGESTION
# =========================
@app.route("/ingest_esp32", methods=["POST"])
def ingest_esp32():
    """Dedicated endpoint for ESP32 sensor data (vibration, gas, etc.)"""
    cursor = conn.cursor()
    
    try:
        data = request.get_json(force=True)
        
        device_id   = data.get("device_id")
        vibration   = data.get("vibration")
        event_count = data.get("event_count")
        gas_raw     = data.get("gas_raw")
        gas_status  = data.get("gas_status")
        
        if not device_id:
            return jsonify({"error": "device_id required"}), 400
        
        cursor.execute(
            """
            INSERT INTO esp32_data
            (device_id, timestamp, vibration, event_count, gas_raw, gas_status)
            VALUES (%s, NOW(), %s, %s, %s, %s)
            """,
            (device_id, vibration, event_count, gas_raw, gas_status)
        )
        conn.commit()
        
        print(f"[OK] ESP32 STORED: device={device_id}, vibration={vibration}, gas={gas_raw} ({gas_status})")
        
        return jsonify({"status": "stored"}), 200
        
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] ESP32 INGEST ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
        
    finally:
        cursor.close()


@app.route("/esp32_data", methods=["GET"])
def get_esp32_data():
    """Retrieve recent ESP32 sensor data"""
    cursor = conn.cursor()
    
    try:
        limit = request.args.get("limit", default=100, type=int)
        device_id = request.args.get("device_id", default=None, type=str)
        
        if device_id:
            cursor.execute(
                """
                SELECT id, device_id, timestamp, vibration, event_count, gas_raw, gas_status
                FROM esp32_data
                WHERE device_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (device_id, limit)
            )
        else:
            cursor.execute(
                """
                SELECT id, device_id, timestamp, vibration, event_count, gas_raw, gas_status
                FROM esp32_data
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (limit,)
            )
        
        rows = cursor.fetchall()
        data = [
            {
                "id": r[0],
                "device_id": r[1],
                "timestamp": str(r[2]) if r[2] else None,
                "vibration": r[3],
                "event_count": r[4],
                "gas_raw": r[5],
                "gas_status": r[6]
            }
            for r in rows
        ]
        
        return jsonify(data)
        
    except Exception as e:
        print(f"[ERROR] ESP32 GET ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500
        
    finally:
        cursor.close()


@app.route("/latest_esp32", methods=["GET"])
def latest_esp32():
    """Get the most recent ESP32 sensor reading"""
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            SELECT device_id, vibration, event_count, gas_raw, gas_status, timestamp
            FROM esp32_data
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        
        if row:
            return jsonify({
                "device_id": row[0],
                "vibration": row[1],
                "event_count": row[2],
                "gas_raw": row[3],
                "gas_status": row[4],
                "timestamp": str(row[5]) if row[5] else None
            })
        else:
            return jsonify({
                "device_id": None,
                "vibration": 0,
                "event_count": 0,
                "gas_raw": 0,
                "gas_status": "UNKNOWN"
            })
            
    except Exception as e:
        print(f"[ERROR] LATEST_ESP32 ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500
        
    finally:
        cursor.close()


@app.route("/live_status", methods=["GET"])
def live_status():
    """Get current machine detection status"""
    try:
        # Get all machines from profiles
        cursor = conn.cursor()
        cursor.execute("SELECT machine_id FROM machine_profiles")
        all_machines = [row[0] for row in cursor.fetchall()]
        cursor.close()
        
        # Get stable machines from detection history
        stable = get_stable_machines(all_machines)
        
        # Get raw detected machines from last batch (from detection_history)
        detected = []
        for machine_id in all_machines:
            if machine_id in detection_history and len(detection_history[machine_id]) > 0:
                if detection_history[machine_id][-1] == 1:
                    detected.append(machine_id)
        
        return jsonify({
            "detected": sorted(detected),
            "stable": sorted(stable)
        })
        
    except Exception as e:
        print(f"[ERROR] LIVE_STATUS ERROR: {str(e)}")
        return jsonify({"detected": [], "stable": []})


# =========================
# SESSION AGGREGATION & GEMINI PREVIEW (ADDITIVE - NO EXISTING CODE MODIFIED)
# =========================
from session_aggregator import aggregate_session_data, get_gemini_api_key_status, call_gemini_with_fallback, get_latest_data_range, validate_time_range


@app.route("/session-preview-page")
def session_preview_page():
    """Serve the session preview debug page"""
    return send_file(os.path.join(BASE_DIR, "session_preview.html"))


@app.route("/gemini-analysis")
def gemini_analysis_page():
    """Serve the Gemini analysis page"""
    return send_file(os.path.join(BASE_DIR, "gemini_analysis.html"))


@app.route("/latest-data-range", methods=["GET"])
def latest_data_range():
    """
    Get the latest data time range from the database.
    Used by frontend to auto-select valid time range.
    
    Query params:
        duration: Seconds of data to include (default 60)
    
    Returns:
        { start, stop, has_data, audio_count, esp32_count, message }
    """
    duration = request.args.get('duration', default=60, type=int)
    
    try:
        result = get_latest_data_range(conn, duration_seconds=duration)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] LATEST_DATA_RANGE ERROR: {str(e)}")
        return jsonify({"error": str(e), "has_data": False}), 500


@app.route("/validate-time-range", methods=["GET"])
def validate_range():
    """
    Validate that a time range has data before allowing Gemini analysis.
    
    Query params:
        start: ISO timestamp
        stop: ISO timestamp
    
    Returns:
        { valid, audio_count, esp32_count, audio_earliest, audio_latest }
    """
    start_ts = request.args.get('start')
    stop_ts = request.args.get('stop')
    
    if not start_ts or not stop_ts:
        return jsonify({"error": "start and stop are required", "valid": False}), 400
    
    try:
        result = validate_time_range(conn, start_ts, stop_ts)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] VALIDATE_TIME_RANGE ERROR: {str(e)}")
        return jsonify({"error": str(e), "valid": False}), 500


@app.route("/session-preview", methods=["GET"])
def get_session_preview():
    """
    Generate a preview of the aggregated session payload for Gemini.
    This does NOT call Gemini - it only shows what WOULD be sent.
    
    Query params:
        start: ISO timestamp (required)
        stop: ISO timestamp (required)
        machine_id: Filter by machine (optional)
        device_id: Filter by ESP32 device (optional)
    
    Returns:
        JSON payload in the exact format for Gemini
    """
    start_ts = request.args.get('start')
    stop_ts = request.args.get('stop')
    machine_id = request.args.get('machine_id')
    device_id = request.args.get('device_id')

    # URL decode the timestamps if needed
    if start_ts:
        start_ts = start_ts.replace('%3A', ':')
    if stop_ts:
        stop_ts = stop_ts.replace('%3A', ':')

    if not start_ts or not stop_ts:
        return jsonify({"error": "start and stop timestamps are required"}), 400
    
    try:
        payload = aggregate_session_data(
            conn=conn,
            start_ts=start_ts,
            stop_ts=stop_ts,
            machine_id=machine_id or None,
            device_id=device_id or None
        )
        
        print(f"[OK] SESSION PREVIEW: {start_ts} → {stop_ts}")
        print(f"     Machine: {payload.get('machine_id')}, Device: {payload.get('device_id')}")
        print(f"     Duration: {payload['session']['duration_sec']}s")
        
        return jsonify(payload)
        
    except Exception as e:
        print(f"[ERROR] SESSION_PREVIEW ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api-key-status", methods=["GET"])
def api_key_status():
    """
    Check if Gemini API key is configured (without exposing the full key).
    Frontend can use this to show configuration status.
    """
    status = get_gemini_api_key_status()
    return jsonify(status)


@app.route("/debug-db", methods=["GET"])
def debug_db():
    """
    Debug endpoint to check what data exists in the database.
    Shows row counts and latest timestamps for each table.
    """
    cursor = conn.cursor()
    
    try:
        result = {}
        
        # Check raw_audio table
        cursor.execute("SELECT COUNT(*) FROM raw_audio")
        result['raw_audio_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM raw_audio")
        row = cursor.fetchone()
        result['raw_audio_earliest'] = str(row[0]) if row[0] else None
        result['raw_audio_latest'] = str(row[1]) if row[1] else None
        
        # Check esp32_data table
        cursor.execute("SELECT COUNT(*) FROM esp32_data")
        result['esp32_data_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM esp32_data")
        row = cursor.fetchone()
        result['esp32_earliest'] = str(row[0]) if row[0] else None
        result['esp32_latest'] = str(row[1]) if row[1] else None
        
        # Check machine_profiles
        cursor.execute("SELECT COUNT(*) FROM machine_profiles")
        result['profiles_count'] = cursor.fetchone()[0]
        
        # Get sample of recent raw_audio
        cursor.execute("""
            SELECT timestamp, amplitude, dominant_freq, machine_id, mode 
            FROM raw_audio 
            ORDER BY timestamp DESC 
            LIMIT 5
        """)
        result['recent_raw_audio'] = [
            {
                'timestamp': str(r[0]),
                'amplitude': r[1],
                'dominant_freq': r[2],
                'machine_id': r[3],
                'mode': r[4]
            }
            for r in cursor.fetchall()
        ]
        
        # Get sample of recent esp32_data
        cursor.execute("""
            SELECT timestamp, device_id, vibration, gas_raw, gas_status 
            FROM esp32_data 
            ORDER BY timestamp DESC 
            LIMIT 5
        """)
        result['recent_esp32_data'] = [
            {
                'timestamp': str(r[0]),
                'device_id': r[1],
                'vibration': r[2],
                'gas_raw': r[3],
                'gas_status': r[4]
            }
            for r in cursor.fetchall()
        ]
        
        return jsonify(result)
        
    except Exception as e:
        print(f"[ERROR] DEBUG_DB ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500
        
    finally:
        cursor.close()


@app.route("/gemini-analyze", methods=["POST"])
def gemini_analyze():
    """
    Send session data to Gemini API for analysis with multi-model fallback.
    
    Expects JSON body:
        { "session_data": { ... aggregated session payload ... } }
    
    Returns:
        { "ai_used": true, "model": "gemini-1.5-flash", "analysis": {...} }
        or
        { "ai_used": false, "reason": "...", "fallback": "Rule-based diagnostics only" }
    """
    try:
        data = request.get_json(force=True)
        session_data = data.get('session_data')
        
        if not session_data:
            return jsonify({"error": "session_data is required"}), 400
        
        print(f"[OK] GEMINI ANALYZE: Sending session to Gemini API...")
        print(f"     Machine: {session_data.get('machine_id')}")
        print(f"     Duration: {session_data.get('session', {}).get('duration_sec')}s")
        
        # Call Gemini API with multi-model fallback
        try:
            result = call_gemini_with_fallback(session_data)
            
            print(f"[OK] GEMINI RESPONSE: model={result['model_used']}, health_status={result['analysis'].get('health_status')}")
            
            return jsonify({
                "status": "success",
                "ai_used": True,
                "model": result["model_used"],
                "analysis": result["analysis"]
            })
            
        except RuntimeError as e:
            # All models failed - return graceful fallback
            print(f"[WARN] GEMINI FALLBACK: {str(e)}")
            return jsonify({
                "status": "fallback",
                "ai_used": False,
                "reason": str(e),
                "fallback": "Rule-based diagnostics only",
                "analysis": generate_rule_based_analysis(session_data)
            })
        
    except Exception as e:
        print(f"[ERROR] GEMINI_ANALYZE ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def generate_rule_based_analysis(session_data: dict) -> dict:
    """
    Generate basic rule-based analysis when AI is unavailable.
    This provides meaningful diagnostics without Gemini.
    """
    findings = []
    actions = []
    health_status = "NORMAL"
    severity = "LOW"
    
    # Analyze sound data
    sound = session_data.get("sound_summary", {})
    freq = sound.get("dominant_freq_median", 0)
    out_of_profile = sound.get("out_of_profile_events", 0)
    
    if freq > 0:
        findings.append({
            "signal": "sound",
            "observation": f"Dominant frequency: {freq:.1f} Hz",
            "interpretation": "Frequency detected within operational range",
            "confidence": "MEDIUM"
        })
    
    if out_of_profile > 10:
        findings.append({
            "signal": "sound",
            "observation": f"{out_of_profile} out-of-profile frequency events",
            "interpretation": "Possible mechanical anomaly or environmental noise",
            "confidence": "MEDIUM"
        })
        health_status = "WARNING"
        severity = "MEDIUM"
        actions.append("Investigate source of frequency deviations")
    
    # Analyze vibration data
    vib = session_data.get("vibration_summary", {})
    vib_avg = vib.get("avg", 0)
    vib_peak = vib.get("peak", 0)
    
    if vib_peak > 80:
        findings.append({
            "signal": "vibration",
            "observation": f"High peak vibration: {vib_peak:.1f}%",
            "interpretation": "Excessive vibration detected",
            "confidence": "HIGH"
        })
        health_status = "WARNING"
        severity = "MEDIUM"
        actions.append("Check for loose components or imbalance")
    elif vib_avg > 0:
        findings.append({
            "signal": "vibration",
            "observation": f"Average vibration activity: {vib_avg:.1f}%",
            "interpretation": "Normal operational vibration",
            "confidence": "MEDIUM"
        })
    
    # Analyze gas data
    gas = session_data.get("gas_summary", {})
    gas_status = gas.get("status", "LOW")
    gas_avg = gas.get("avg_raw", 0)
    
    if gas_status == "HIGH":
        findings.append({
            "signal": "gas",
            "observation": f"High gas level detected: {gas_avg:.0f} raw",
            "interpretation": "Air quality concern - possible leak or combustion issue",
            "confidence": "HIGH"
        })
        health_status = "CRITICAL"
        severity = "HIGH"
        actions.append("Immediately check ventilation and gas sources")
        actions.append("Ensure proper exhaust system operation")
    elif gas_status == "MEDIUM":
        findings.append({
            "signal": "gas",
            "observation": f"Moderate gas level: {gas_avg:.0f} raw",
            "interpretation": "Elevated but not critical air quality",
            "confidence": "MEDIUM"
        })
        if health_status == "NORMAL":
            health_status = "WARNING"
            severity = "MEDIUM"
        actions.append("Monitor air quality trends")
    
    # Default action if everything looks good
    if not actions:
        actions.append("Continue normal monitoring")
        actions.append("No immediate action required")
    
    return {
        "health_status": health_status,
        "key_findings": findings if findings else [{
            "signal": "combined",
            "observation": "Insufficient data for detailed analysis",
            "interpretation": "More sensor data needed",
            "confidence": "LOW"
        }],
        "overall_severity": severity,
        "recommended_actions": actions,
        "notes": "Analysis generated using rule-based fallback (AI unavailable)"
    }


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

