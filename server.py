from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import psycopg2
import psycopg2.extras
import math
from datetime import datetime
import os

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
# SILENCE DETECTION THRESHOLDS
# =========================
AMPLITUDE_THRESHOLD = 0.1  # Ignore very quiet frames
CONFIDENCE_THRESHOLD = 0.2  # Require decent FFT peak

# =========================
# SERVE HTML
# =========================
@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))

# =========================
# INGEST ROUTE (UPDATED)
# =========================
@app.route("/ingest", methods=["POST"])
def ingest():
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        data = request.get_json(force=True)

        amplitude = data.get("amplitude")
        timestamp = data.get("timestamp")
        dominant_freq = data.get("dominant_freq")
        freq_confidence = data.get("freq_confidence")
        machine_id = data.get("machine_id")
        mode = data.get("mode", "live")

        if amplitude is None or timestamp is None:
            return jsonify({"error": "invalid data"}), 400

        # Silence filtering
        if amplitude < AMPLITUDE_THRESHOLD or freq_confidence < CONFIDENCE_THRESHOLD:
            return jsonify({
                "status": "filtered",
                "reason": "silence or low confidence",
                "anomaly": False,
                "z_score": 0
            }), 200

        # ---- EXISTING ML LOGIC ----
        z_score, anomaly = noise_model.update(amplitude)

        print("----- PACKET -----")
        print("Amplitude      :", round(amplitude, 2))
        print("Expected Noise :", round(noise_model.expected_noise, 2))
        print("Variance       :", round(noise_model.variance, 2))
        print("Z-Score        :", round(z_score, 2))
        print("Anomaly        :", anomaly)
        print("Frequency      :", round(dominant_freq, 2))
        print("Freq Confidence:", round(freq_confidence, 2))
        print("Mode           :", mode)
        if machine_id:
            print("Machine ID     :", machine_id)
        print("Timestamp      :", timestamp)

        # ---- DATABASE INSERT ----
        cursor.execute(
            """
            INSERT INTO raw_audio
            (timestamp, amplitude, dominant_freq, freq_confidence, machine_id, mode)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                datetime.fromtimestamp(timestamp / 1000),
                amplitude,
                dominant_freq,
                freq_confidence,
                machine_id,
                mode
            )
        )
        conn.commit()

        # ---- LIVE DETECTION (if not in calibration mode) ----
        detected_machine = None
        if mode == "live":
            detected_machine = identify_machine(dominant_freq)

        return jsonify({
            "status": "ok",
            "anomaly": anomaly,
            "z_score": round(z_score, 2),
            "detected_machine": detected_machine
        })

    except Exception as e:
        conn.rollback()
        print("❌ INGEST ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# MACHINE IDENTIFICATION (FREQUENCY MATCHING)
# =========================
def identify_machine(dominant_freq):
    """Match detected frequency to trained machine profiles"""
    if dominant_freq is None or dominant_freq <= 0:
        return None

    cursor = conn.cursor()  # ✅ NEW cursor per call
    
    try:
        cursor.execute(
            """
            SELECT machine_id, mean_freq, std_freq, min_freq, max_freq
            FROM machine_profiles
            ORDER BY machine_id
            """
        )
        profiles = cursor.fetchall()

        for profile in profiles:
            machine_id, mean_freq, std_freq, min_freq, max_freq = profile
            
            # Match if frequency within trained range
            if min_freq <= dominant_freq <= max_freq:
                return machine_id

        return None
        
    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# SAVE CALIBRATION PROFILE
# =========================
@app.route("/save_profile", methods=["POST"])
def save_profile():
    """
    Analyze calibration data and save machine profile.
    Expects: {"machine_id": "machine_1"}
    """
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        # Fetch all calibration data for this machine
        cursor.execute(
            """
            SELECT dominant_freq, freq_confidence
            FROM raw_audio
            WHERE machine_id = %s AND mode = 'calibration' AND dominant_freq > 0
            ORDER BY timestamp DESC
            LIMIT 1000
            """,
            (machine_id,)
        )
        
        rows = cursor.fetchall()

        if not rows or len(rows) == 0:
            return jsonify({"error": "No calibration data found"}), 400

        # Filter by confidence
        valid_freqs = [
            freq for freq, conf in rows 
            if conf >= CONFIDENCE_THRESHOLD and freq > 0
        ]

        if len(valid_freqs) < 10:
            return jsonify({
                "error": f"Not enough valid frames ({len(valid_freqs)} < 10)"
            }), 400

        # Calculate profile statistics
        mean_freq = sum(valid_freqs) / len(valid_freqs)
        variance = sum((f - mean_freq) ** 2 for f in valid_freqs) / len(valid_freqs)
        std_freq = math.sqrt(variance)

        # Allow ±2 standard deviations
        min_freq = max(0, mean_freq - 2 * std_freq)
        max_freq = mean_freq + 2 * std_freq

        print(f"\n=== PROFILE CREATED: {machine_id} ===")
        print(f"Frames analyzed: {len(valid_freqs)}")
        print(f"Mean frequency: {mean_freq:.2f} Hz")
        print(f"Std deviation:  {std_freq:.2f} Hz")
        print(f"Range allowed:  {min_freq:.2f} - {max_freq:.2f} Hz")

        # Save or update profile
        cursor.execute(
            """
            INSERT INTO machine_profiles (machine_id, mean_freq, std_freq, min_freq, max_freq, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (machine_id)
            DO UPDATE SET
                mean_freq = EXCLUDED.mean_freq,
                std_freq = EXCLUDED.std_freq,
                min_freq = EXCLUDED.min_freq,
                max_freq = EXCLUDED.max_freq,
                created_at = NOW()
            """,
            (machine_id, mean_freq, std_freq, min_freq, max_freq)
        )
        conn.commit()

        return jsonify({
            "status": "profile_saved",
            "machine_id": machine_id,
            "mean_freq": round(mean_freq, 2),
            "std_freq": round(std_freq, 2),
            "min_freq": round(min_freq, 2),
            "max_freq": round(max_freq, 2),
            "frames_used": len(valid_freqs)
        })

    except Exception as e:
        conn.rollback()
        print("❌ SAVE_PROFILE ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# GET ALL PROFILES
# =========================
@app.route("/profiles", methods=["GET"])
def get_profiles():
    """Return all trained machine profiles"""
    cursor = conn.cursor()  # ✅ NEW cursor per request
    
    try:
        cursor.execute(
            """
            SELECT machine_id, mean_freq, std_freq, min_freq, max_freq, created_at
            FROM machine_profiles
            ORDER BY machine_id
            """
        )
        
        rows = cursor.fetchall()
        profiles = [
            {
                "machine_id": row[0],
                "mean_freq": round(row[1], 2),
                "std_freq": round(row[2], 2),
                "min_freq": round(row[3], 2),
                "max_freq": round(row[4], 2),
                "created_at": str(row[5])
            }
            for row in rows
        ]

        return jsonify(profiles)

    except Exception as e:
        print("❌ GET_PROFILES ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# EXPORT RAW DATA
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
                "machine_id": r[4],
                "mode": r[5]
            }
            for r in rows
        ]

        return jsonify(data)

    except Exception as e:
        print("❌ EXPORT_RAW ERROR:", str(e))
        return jsonify({"error": "server error"}), 500

    finally:
        cursor.close()  # ✅ ALWAYS CLOSE

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

