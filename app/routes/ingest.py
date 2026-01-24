from flask import Blueprint, request, jsonify
import queue
import traceback
from datetime import datetime
import psycopg2.extras
from app.db import get_db
from app.services.batch_processor import BATCH_QUEUE, persist_failed_batch
from app.services.audio_processing import AMPLITUDE_THRESHOLD, noise_model, identify_machines
from app.services.stability import update_detection_history, get_stable_machines

ingest_bp = Blueprint('ingest', __name__)

@ingest_bp.route("/ingest_batch", methods=["POST"])
def ingest_batch():
    """Accept large batch payloads and enqueue for background processing.
    Returns 202 Accepted immediately so upstream proxies (nginx) won't timeout.
    """
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        print('[ERROR] ingest_batch: invalid JSON', str(e))
        return jsonify({"error": "invalid json"}), 400

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
        persist_failed_batch(payload)
        return jsonify({"error": "queue full, persisted"}), 503


@ingest_bp.route("/ingest", methods=["POST"])
def ingest():
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        data = request.get_json(force=True)
        mode = data.get("mode", "live")
        store_all = data.get("store_all", False)

        if mode == "calibration":
            frames = data.get("frames", [])
            machine_id = data.get("machine_id")
            frames_captured = data.get("frames_captured", len(frames))

            if not frames or not machine_id:
                return jsonify({"error": "frames and machine_id required"}), 400

            inserted_count = 0
            for frame in frames:
                amplitude = frame.get("amplitude")
                peaks = frame.get("peaks", [])
                timestamp = frame.get("timestamp")

                if not store_all and (amplitude < AMPLITUDE_THRESHOLD or len(peaks) == 0):
                    continue

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
            print(f"\n[OK] CALIBRATION BATCH: {machine_id} - Frames: {len(frames)}, Inserted: {inserted_count}")

            return jsonify({
                "status": "calibration_batch_saved",
                "frames_received": len(frames),
                "frames_captured": frames_captured,
                "frames_inserted": inserted_count,
                "machine_id": machine_id
            })

        elif mode == "live":
            frames = data.get("frames", [])
            frames_captured = data.get("frames_captured", len(frames))

            if not frames:
                return jsonify({"error": "frames required"}), 400

            running_machines = set()
            anomaly_machines = set()
            inserted_count = 0

            for frame in frames:
                amplitude = frame.get("amplitude")
                peaks = frame.get("peaks", [])
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
                        None,
                        mode
                    )
                )
                inserted_count += 1

                if len(peaks) > 0:
                    result = identify_machines(peaks)
                    running_machines.update(result["detected"])
                    anomaly_machines.update(result["anomaly"])

            conn.commit()

            cursor.execute("SELECT machine_id FROM machine_profiles")
            all_machines = [row[0] for row in cursor.fetchall()]
            update_detection_history(running_machines, all_machines)
            stable_machines = get_stable_machines(all_machines)

            print(f"\n[OK] LIVE BATCH: {len(frames)} frames, {inserted_count} inserted")
            print(f"   Detected (raw): {sorted(running_machines)}")
            print(f"   Stable machines: {sorted(stable_machines)}")

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
        cursor.close()

# ESP32 Routes defined in same file for logical grouping
@ingest_bp.route("/ingest_esp32", methods=["POST"])
def ingest_esp32():
    """Dedicated endpoint for ESP32 sensor data"""
    conn = get_db()
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
        print(f"[OK] ESP32 STORED: device={device_id}, vibration={vibration}")
        return jsonify({"status": "stored"}), 200
        
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] ESP32 INGEST ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()


@ingest_bp.route("/latest_esp32", methods=["GET"])
def latest_esp32():
    conn = get_db()
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

@ingest_bp.route("/esp32_data", methods=["GET"])
def get_esp32_data():
    conn = get_db()
    cursor = conn.cursor()
    try:
        limit = request.args.get("limit", default=100, type=int)
        device_id = request.args.get("device_id", default=None, type=str)
        
        if device_id:
            cursor.execute("SELECT id, device_id, timestamp, vibration, event_count, gas_raw, gas_status FROM esp32_data WHERE device_id = %s ORDER BY timestamp DESC LIMIT %s", (device_id, limit))
        else:
            cursor.execute("SELECT id, device_id, timestamp, vibration, event_count, gas_raw, gas_status FROM esp32_data ORDER BY timestamp DESC LIMIT %s", (limit,))
        
        rows = cursor.fetchall()
        data = [{"id": r[0], "device_id": r[1], "timestamp": str(r[2]), "vibration": r[3], "event_count": r[4], "gas_raw": r[5], "gas_status": r[6]} for r in rows]
        return jsonify(data)
    except Exception as e:
        print(f"[ERROR] ESP32 GET ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
