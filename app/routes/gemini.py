from flask import Blueprint, request, jsonify, session
import sys
import os
import traceback
from app.auth import login_required

# Ensure we can import from scripts
# sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

# Better: import as package (assuming __init__.py exists or namespace package)
# If session_aggregator is in app/scripts/session_aggregator.py
try:
    from app.scripts.session_aggregator import aggregate_session_data, get_gemini_api_key_status, call_gemini_with_fallback, get_latest_data_range, validate_time_range
except ImportError as e:
    print(f"[WARN] Could not import session_aggregator: {e}")
    # Mocking for robust startup if script is missing
    def aggregate_session_data(*args, **kwargs): raise NotImplementedError("session_aggregator missing")
    def get_gemini_api_key_status(): return {"configured": False, "error": "Module missing"}
    def call_gemini_with_fallback(*args): raise NotImplementedError("session_aggregator missing")
    def get_latest_data_range(*args, **kwargs): return {"has_data": False, "error": "Module missing"}
    def validate_time_range(*args): return {"valid": False, "error": "Module missing"}

from app.db import get_db

gemini_bp = Blueprint('gemini', __name__)

@gemini_bp.route("/latest-data-range", methods=["GET"])
@login_required
def latest_data_range_route():
    try:
        duration = request.args.get('duration', default=60, type=int)
        conn = get_db()
        user_id = session['user_id']
        result = get_latest_data_range(conn, user_id=user_id, duration_seconds=duration)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] LATEST_DATA_RANGE ERROR: {str(e)}")
        return jsonify({"error": str(e), "has_data": False}), 500

@gemini_bp.route("/validate-time-range", methods=["GET"])
@login_required
def validate_range():
    start_ts = request.args.get('start')
    stop_ts = request.args.get('stop')
    user_id = session['user_id']
    if not start_ts or not stop_ts:
        return jsonify({"error": "start and stop are required", "valid": False}), 400
    try:
        conn = get_db()
        result = validate_time_range(conn, user_id, start_ts, stop_ts)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] VALIDATE_TIME_RANGE ERROR: {str(e)}")
        return jsonify({"error": str(e), "valid": False}), 500

@gemini_bp.route("/session-preview", methods=["GET"])
@login_required
def get_session_preview():
    start_ts = request.args.get('start')
    stop_ts = request.args.get('stop')
    machine_id = request.args.get('machine_id')
    device_id = request.args.get('device_id')
    user_id = session['user_id']

    if start_ts: start_ts = start_ts.replace('%3A', ':')
    if stop_ts: stop_ts = stop_ts.replace('%3A', ':')

    if not start_ts or not stop_ts:
        return jsonify({"error": "start and stop timestamps are required"}), 400
    
    try:
        conn = get_db()
        payload = aggregate_session_data(
            conn=conn,
            user_id=user_id,
            start_ts=start_ts,
            stop_ts=stop_ts,
            machine_id=machine_id or None,
            device_id=device_id or None
        )
        print(f"[OK] SESSION PREVIEW: {start_ts} to {stop_ts}")
        return jsonify(payload)
    except Exception as e:
        print(f"[ERROR] SESSION_PREVIEW ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@gemini_bp.route("/api-key-status", methods=["GET"])
@login_required
def api_key_status():
    status = get_gemini_api_key_status()
    return jsonify(status)

@gemini_bp.route("/debug-db", methods=["GET"])
@login_required
def debug_db():
    conn = get_db()
    cursor = conn.cursor()
    user_id = session['user_id']
    try:
        result = {}
        cursor.execute("SELECT COUNT(*) FROM raw_audio WHERE user_id = %s", (user_id,))
        result['raw_audio_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM raw_audio WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        result['raw_audio_earliest'] = str(row[0]) if row[0] else None
        result['raw_audio_latest'] = str(row[1]) if row[1] else None
        
        cursor.execute("SELECT COUNT(*) FROM esp32_data WHERE user_id = %s", (user_id,))
        result['esp32_data_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM esp32_data WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        result['esp32_earliest'] = str(row[0]) if row[0] else None
        result['esp32_latest'] = str(row[1]) if row[1] else None
        
        cursor.execute("SELECT COUNT(*) FROM machine_profiles WHERE user_id = %s", (user_id,))
        result['profiles_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT timestamp, amplitude, dominant_freq, machine_id, mode FROM raw_audio WHERE user_id = %s ORDER BY timestamp DESC LIMIT 5", (user_id,))
        result['recent_raw_audio'] = [{'timestamp': str(r[0]), 'amplitude': r[1], 'dominant_freq': r[2], 'machine_id': r[3], 'mode': r[4]} for r in cursor.fetchall()]
        
        cursor.execute("SELECT timestamp, device_id, vibration, gas_raw, gas_status FROM esp32_data WHERE user_id = %s ORDER BY timestamp DESC LIMIT 5", (user_id,))
        result['recent_esp32_data'] = [{'timestamp': str(r[0]), 'device_id': r[1], 'vibration': r[2], 'gas_raw': r[3], 'gas_status': r[4]} for r in cursor.fetchall()]
        
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] DEBUG_DB ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

def generate_rule_based_analysis(session_data: dict) -> dict:
    findings = []
    sound = session_data.get("sound_summary", {})
    freq = sound.get("dominant_freq_median", 0)
    out_of_profile = sound.get("out_of_profile_events", 0)
    
    if freq > 0:
        findings.append({"signal": "sound", "observation": f"Dominant frequency: {freq:.1f} Hz", "interpretation": "Frequency detected within operational range", "confidence": "MEDIUM"})
    if out_of_profile > 10:
        findings.append({"signal": "sound", "observation": f"{out_of_profile} out-of-profile frequency events", "interpretation": "Possible mechanical anomaly", "confidence": "MEDIUM"})
        
    return {"health_status": "NORMAL" if out_of_profile < 50 else "WARNING", "findings": findings, "recommendations": ["Monitor machine closely"] if out_of_profile > 10 else []}

@gemini_bp.route("/gemini-analyze", methods=["POST"])
@login_required
def gemini_analyze():
    try:
        data = request.get_json(force=True)
        session_data = data.get('session_data')
        if not session_data: return jsonify({"error": "session_data is required"}), 400
        
        print(f"[OK] GEMINI ANALYZE: Sending session to Gemini API...")
        try:
            result = call_gemini_with_fallback(session_data)
            return jsonify({"status": "success", "ai_used": True, "model": result["model_used"], "analysis": result["analysis"]})
        except RuntimeError as e:
            print(f"[WARN] GEMINI FALLBACK: {str(e)}")
            return jsonify({"status": "fallback", "ai_used": False, "reason": str(e), "fallback": "Rule-based diagnostics only", "analysis": generate_rule_based_analysis(session_data)})
    except Exception as e:
        print(f"[ERROR] GEMINI_ANALYZE ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
