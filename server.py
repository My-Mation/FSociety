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

        # Create machine_profiles table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS machine_profiles (
            machine_id VARCHAR(50) PRIMARY KEY,
            median_freq FLOAT NOT NULL,
            iqr_low FLOAT NOT NULL,
            iqr_high FLOAT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """)

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
            inserted_count = 0

            for frame in frames:
                amplitude = frame.get("amplitude")
                peaks = frame.get("peaks", [])  # Array of {freq, amp}
                timestamp = frame.get("timestamp")

                # If not forcing storage, skip low-amplitude/no-peak frames
                if not store_all and (amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0):
                    continue

                # Update noise model regardless
                z_score, anomaly = noise_model.update(amplitude)

                if len(peaks) > 0:
                    dominant_freq = peaks[0].get("freq")
                    freq_confidence = peaks[0].get("amp")
                else:
                    dominant_freq = None
                    freq_confidence = None

                ts = datetime.fromtimestamp(timestamp / 1000) if timestamp else datetime.now()

                # Database insert
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
                    machines_in_frame = identify_machines(peaks)
                    running_machines.update(machines_in_frame)

            conn.commit()

            # ✅ NEW: Update temporal stability tracking
            cursor.execute("SELECT machine_id FROM machine_profiles")
            all_machines = [row[0] for row in cursor.fetchall()]
            update_detection_history(running_machines, all_machines)
            
            # ✅ NEW: Apply temporal stability filter (60% of recent batches)
            stable_machines = get_stable_machines(all_machines)

            print(f"\n[OK] LIVE BATCH: {len(frames)} frames, {inserted_count} inserted (store_all={store_all})")
            print(f"   Detected (raw): {sorted(running_machines)}")
            print(f"   Stable machines: {sorted(stable_machines)}")

            return jsonify({
                "status": "ok",
                "frames_received": len(frames),
                "frames_captured": frames_captured,
                "frames_inserted": inserted_count,
                "running_machines": sorted(stable_machines),  # ✅ MAIN OUTPUT
                "running_machines_raw": sorted(running_machines),  # For debugging
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
# MULTI-MACHINE IDENTIFICATION (UPDATED)
# =========================
def identify_machines(peaks_list):
    """
    Match detected peaks to machine profiles.
    Returns list of machine IDs currently running.
    
    Args:
        peaks_list: List of {freq, amp} for a single frame
    
    Returns:
        List of machine_ids detected in this frame
    """
    if not peaks_list or len(peaks_list) == 0:
        return []

    cursor = conn.cursor()
    
    try:
        # Fetch all machine profiles
        cursor.execute(
            "SELECT machine_id, median_freq, iqr_low, iqr_high FROM machine_profiles ORDER BY machine_id"
        )
        profiles = cursor.fetchall()

        if not profiles:
            return []

        # For each peak, find matching machine(s)
        detected_machines = set()

        for peak in peaks_list:
            freq = peak.get("freq")
            if not freq or freq <= 0:
                continue

            # Find best matching machine profile
            best_match = None
            best_distance = float('inf')

            for profile in profiles:
                machine_id, median_freq, iqr_low, iqr_high = profile

                # Check if frequency is within IQR bounds
                if iqr_low <= freq <= iqr_high:
                    # Distance from median (prefer closer matches)
                    distance = abs(freq - median_freq)
                    
                    if distance < best_distance:
                        best_distance = distance
                        best_match = machine_id

            # Only assign if match found
            if best_match:
                detected_machines.add(best_match)

        return list(detected_machines)

    finally:
        cursor.close()

# =========================
# SAVE CALIBRATION PROFILE (UPDATED FOR MULTI-PEAK)
# =========================
@app.route("/save_profile", methods=["POST"])
def save_profile():
    """
    Analyze calibration data and save machine profile using IQR.
    Expects: {"machine_id": "machine_1"}
    """
    cursor = conn.cursor()

    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        # ✅ 1. FETCH CALIBRATION DATA (THIS WAS MISSING)
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

        if not rows:
            return jsonify({"error": "No calibration data found"}), 400

        # ✅ 2. EXTRACT FREQUENCIES
        frequencies = []
        for dom, peaks in rows:
            if peaks:
                for p in peaks:
                    if isinstance(p, dict):
                        f = p.get("freq")
                        if f and f > 0:
                            frequencies.append(f)
            elif dom and dom > 0:
                frequencies.append(dom)

        if len(frequencies) < 10:
            return jsonify({
                "error": f"Not enough valid frequencies ({len(frequencies)} < 10)"
            }), 400

        frequencies.sort()

        # ✅ 3. COMPUTE MEDIAN + IQR
        def percentile(vals, p):
            return vals[int(p * (len(vals) - 1))]

        median_freq = percentile(frequencies, 0.5)
        q1 = percentile(frequencies, 0.25)
        q3 = percentile(frequencies, 0.75)

        iqr = q3 - q1
        iqr_low = max(0, q1 - 0.5 * iqr)
        iqr_high = q3 + 0.5 * iqr

        print(f"\n=== PROFILE CREATED: {machine_id} ===")
        print(f"Frames analyzed: {len(frequencies)}")
        print(f"Median frequency: {median_freq:.2f} Hz")
        print(f"IQR range: {iqr_low:.2f} – {iqr_high:.2f} Hz")

        # ✅ 4. SAVE PROFILE (ONLY AFTER COMPUTATION)
        cursor.execute(
            """
            INSERT INTO machine_profiles
                (machine_id, median_freq, iqr_low, iqr_high, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (machine_id)
            DO UPDATE SET
                median_freq = EXCLUDED.median_freq,
                iqr_low = EXCLUDED.iqr_low,
                iqr_high = EXCLUDED.iqr_high,
                updated_at = NOW()
            """,
            (machine_id, median_freq, iqr_low, iqr_high)
        )
        conn.commit()

        return jsonify({
            "status": "profile_saved",
            "machine_id": machine_id,
            "median_freq": round(median_freq, 2),
            "iqr": round(iqr, 2),
            "iqr_low": round(iqr_low, 2),
            "iqr_high": round(iqr_high, 2),
            "frames_used": len(frequencies)
        })

    except Exception as e:
        conn.rollback()
        print("[ERROR] SAVE_PROFILE ERROR:", str(e))
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
            SELECT machine_id, median_freq, iqr_low, iqr_high, created_at
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
            created_at = row[4]

            # Safely round numeric values that may be NULL in DB
            def safe_round(val):
                return round(val, 2) if val is not None else None

            iqr_val = None
            if iqr_low is not None and iqr_high is not None:
                iqr_val = round(iqr_high - iqr_low, 2)

            profiles.append({
                "machine_id": machine_id,
                "median_freq": safe_round(median_freq),
                "iqr_low": safe_round(iqr_low),
                "iqr_high": safe_round(iqr_high),
                "iqr": iqr_val,
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
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

