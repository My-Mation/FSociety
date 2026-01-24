from flask import Blueprint, request, jsonify
import psycopg2.extras
import traceback
from app.db import get_db
from app.services.stability import get_stable_machines, detection_history

profiles_bp = Blueprint('profiles', __name__)

FREQ_BIN_SIZE = 15
MIN_CLUSTER_SAMPLES = 15

@profiles_bp.route("/save_profile", methods=["POST"])
def save_profile():
    conn = get_db()
    cursor = conn.cursor()

    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")
        vibration_samples = data.get("vibration_samples", [])
        gas_samples = data.get("gas_samples", [])
        
        print(f"[DEBUG] save_profile received: machine_id={machine_id}")

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        # Process vibration data
        vibration_data = None
        if vibration_samples and len(vibration_samples) > 0:
            vibration_count = sum(1 for v in vibration_samples if v == 0)
            total_samples = len(vibration_samples)
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
            valid_raw_values = [v for v in raw_values if v > 0]
            
            if valid_raw_values:
                avg_gas = sum(valid_raw_values) / len(valid_raw_values)
                max_gas = max(valid_raw_values)
                min_gas = min(valid_raw_values)
            else:
                avg_gas = 0; max_gas = 0; min_gas = 0
            
            if avg_gas == 0: gas_status = "NO_DATA"
            elif avg_gas < 800: gas_status = "SAFE"
            elif avg_gas < 2000: gas_status = "MODERATE"
            else: gas_status = "HAZARDOUS"
            
            gas_data = {
                "samples": len(gas_samples),
                "valid_samples": len(valid_raw_values),
                "avg_raw": round(avg_gas, 1),
                "max_raw": round(max_gas, 1),
                "min_raw": round(min_gas, 1),
                "status": gas_status
            }

        # Fetch calibration data
        cursor.execute("SELECT peaks FROM raw_audio WHERE machine_id = %s AND mode = 'calibration' ORDER BY timestamp DESC LIMIT 3000", (machine_id,))
        rows = cursor.fetchall()

        if not rows:
            return jsonify({"error": "No calibration data found"}), 400

        all_frequencies = []
        for (peaks,) in rows:
            if peaks:
                sorted_peaks = sorted([p for p in peaks if isinstance(p, dict) and p.get("freq", 0) > 0], key=lambda x: x.get("amp", 0), reverse=True)[:5]
                for p in sorted_peaks:
                    freq = p.get("freq")
                    amp = p.get("amp", 0)
                    if freq and freq > 0 and amp >= 0.1:
                        all_frequencies.append(freq)

        if len(all_frequencies) < 20:
            return jsonify({"error": f"Not enough valid frequencies ({len(all_frequencies)} < 20)"}), 400

        clusters = {}
        for freq in all_frequencies:
            bucket = round(freq / FREQ_BIN_SIZE) * FREQ_BIN_SIZE
            clusters.setdefault(bucket, []).append(freq)

        freq_bands = []
        for bucket, freqs in sorted(clusters.items()):
            if len(freqs) < MIN_CLUSTER_SAMPLES: continue
            freqs_sorted = sorted(freqs)
            n = len(freqs_sorted)
            q1 = freqs_sorted[n // 4]
            q3 = freqs_sorted[3 * n // 4]
            center = sum(freqs_sorted) / n
            iqr = q3 - q1
            band_low = max(0, q1 - 0.5 * iqr)
            band_high = q3 + 0.5 * iqr
            freq_bands.append({"center": round(center, 2), "low": round(band_low, 2), "high": round(band_high, 2), "samples": n})

        freq_bands = sorted(freq_bands, key=lambda x: x["samples"], reverse=True)[:5]
        freq_bands = sorted(freq_bands, key=lambda x: x["center"])

        all_frequencies.sort()
        def percentile(vals, p): return vals[int(p * (len(vals) - 1))]
        median_freq = percentile(all_frequencies, 0.5)
        q1 = percentile(all_frequencies, 0.25)
        q3 = percentile(all_frequencies, 0.75)
        iqr = q3 - q1
        iqr_low = max(0, q1 - 0.5 * iqr)
        iqr_high = q3 + 0.5 * iqr

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
            (machine_id, median_freq, iqr_low, iqr_high, psycopg2.extras.Json(freq_bands),
             psycopg2.extras.Json(vibration_data) if vibration_data else None,
             psycopg2.extras.Json(gas_data) if gas_data else None)
        )
        conn.commit()

        print(f"\n=== PROFILE CREATED: {machine_id} ===")
        return jsonify({
            "status": "profile_saved",
            "machine_id": machine_id,
            "median_freq": round(median_freq, 2),
            "freq_bands": freq_bands,
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

@profiles_bp.route("/profiles", methods=["GET"])
def get_profiles():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT machine_id, median_freq, iqr_low, iqr_high, freq_bands, vibration_data, gas_data, created_at FROM machine_profiles ORDER BY machine_id")
        rows = cursor.fetchall()
        profiles = []
        for row in rows:
            machine_id, median_freq, iqr_low, iqr_high, freq_bands, vibration_data, gas_data, created_at = row
            iqr_val = round(iqr_high - iqr_low, 2) if (iqr_low is not None and iqr_high is not None) else None
            
            vibration_status_text = None
            if vibration_data and "vibration_percent" in vibration_data:
                percent = vibration_data["vibration_percent"]
                if percent >= 99.9: vibration_status_text = "No vibration detected"
                elif percent <= 0.1: vibration_status_text = "Always vibrating"
                else: vibration_status_text = f"Intermittent vibration: {100-percent:.1f}% active"

            profiles.append({
                "machine_id": machine_id,
                "median_freq": round(median_freq, 2) if median_freq else None,
                "iqr_low": round(iqr_low, 2) if iqr_low else None,
                "iqr_high": round(iqr_high, 2) if iqr_high else None,
                "iqr": iqr_val,
                "freq_bands": freq_bands or [],
                "vibration_data": vibration_data,
                "vibration_status_text": vibration_status_text,
                "gas_data": gas_data,
                "created_at": str(created_at) if created_at else None
            })
        return jsonify(profiles)
    except Exception as e:
        print("[ERROR] GET_PROFILES ERROR:", str(e))
        return jsonify([]), 200
    finally:
        cursor.close()

@profiles_bp.route("/delete_profile", methods=["POST"])
def delete_profile():
    conn = get_db()
    cursor = conn.cursor()
    try:
        data = request.get_json(force=True)
        machine_id = data.get("machine_id")
        if not machine_id: return jsonify({"error": "machine_id required"}), 400
        
        cursor.execute("DELETE FROM machine_profiles WHERE machine_id = %s", (machine_id,))
        deleted_count = cursor.rowcount
        conn.commit()
        
        if deleted_count > 0:
            return jsonify({"status": "profile_deleted", "machine_id": machine_id})
        else:
            return jsonify({"error": f"No profile found for {machine_id}"}), 404
    except Exception as e:
        conn.rollback()
        print("[ERROR] DELETE_PROFILE ERROR:", str(e))
        return jsonify({"error": "server error"}), 500
    finally:
        cursor.close()

@profiles_bp.route("/live_status", methods=["GET"])
def live_status():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT machine_id FROM machine_profiles")
        all_machines = [row[0] for row in cursor.fetchall()]
        cursor.close()
        
        stable = get_stable_machines(all_machines)
        detected = []
        for machine_id in all_machines:
            if machine_id in detection_history and detection_history[machine_id] and detection_history[machine_id][-1] == 1:
                detected.append(machine_id)
        
        return jsonify({"detected": sorted(detected), "stable": sorted(stable)})
    except Exception as e:
        print(f"[ERROR] LIVE_STATUS ERROR: {str(e)}")
        return jsonify({"detected": [], "stable": []})
