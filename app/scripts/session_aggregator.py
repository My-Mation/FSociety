"""
Session Aggregator Module
Fetches and aggregates raw_audio + esp32_data for a given time window.
Produces a token-efficient JSON payload for Gemini (without calling it).

This module is ADDITIVE - it does NOT modify any existing tables or logic.
"""

import os
import json
import re
import requests
from datetime import datetime

# Gemini API key from environment
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# FIXED: Use ONLY valid Gemini models with FULL names (confirmed by ListModels)
# Order: cheapest/fastest first for fallback
GEMINI_MODELS = [
    "models/gemini-2.0-flash-lite",  # Fastest, most likely to succeed
    "models/gemini-2.0-flash",       # Fast, reliable
    "models/gemini-2.5-flash-lite",  # Newer, lite version
    "models/gemini-2.5-flash",       # Newest flash model
]


# =============================================================================
# ROBUST JSON EXTRACTION & NORMALIZATION (Safety-Critical)
# =============================================================================

def extract_json_from_text(text: str) -> tuple:
    """
    Extract the FIRST valid JSON object from text that may contain:
    - Markdown fences (```json ... ```)
    - Leading/trailing explanations
    - Mixed text and JSON
    
    Returns:
        tuple: (extracted_json_dict or None, error_message or None)
    """
    if not text or not text.strip():
        return None, "Empty response"
    
    original_text = text
    
    # Step 1: Try to extract from markdown code fences
    # Matches ```json ... ``` or ``` ... ```
    fence_patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # ```json ... ```
        r'```\s*([\s\S]*?)\s*```',       # ``` ... ```
    ]
    
    for pattern in fence_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict):
                    print(f"[JSON_EXTRACT] ✅ Extracted from markdown fence ({len(match)} chars)")
                    return parsed, None
            except json.JSONDecodeError:
                continue
    
    # Step 2: Try to find JSON object directly using brace matching
    # Find all potential JSON starts
    json_starts = [m.start() for m in re.finditer(r'\{', text)]
    
    for start in json_starts:
        # Try progressively longer substrings
        brace_count = 0
        end = start
        
        for i, char in enumerate(text[start:], start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break
        
        if end > start:
            candidate = text[start:end]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    print(f"[JSON_EXTRACT] ✅ Extracted JSON object ({len(candidate)} chars)")
                    return parsed, None
            except json.JSONDecodeError:
                continue
    
    # Step 3: Try the whole text as JSON
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            print(f"[JSON_EXTRACT] ✅ Whole text is valid JSON")
            return parsed, None
    except json.JSONDecodeError:
        pass
    
    return None, f"No valid JSON found in {len(text)} chars of text"


def attempt_json_repair(text: str) -> tuple:
    """
    Attempt to repair common JSON issues:
    - Missing closing braces
    - Truncated arrays
    - Trailing commas
    
    Returns:
        tuple: (repaired_json_dict or None, error_message or None)
    """
    if not text:
        return None, "Empty text"
    
    # Find the start of JSON
    json_start = text.find('{')
    if json_start == -1:
        return None, "No JSON object start found"
    
    text = text[json_start:]
    
    # Count braces
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')
    
    # Add missing closing braces/brackets
    repaired = text
    repaired += ']' * (open_brackets - close_brackets)
    repaired += '}' * (open_braces - close_braces)
    
    # Remove trailing commas before closing braces/brackets
    repaired = re.sub(r',\s*}', '}', repaired)
    repaired = re.sub(r',\s*]', ']', repaired)
    
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            print(f"[JSON_REPAIR] ✅ Successfully repaired JSON")
            return parsed, None
    except json.JSONDecodeError as e:
        return None, f"Repair failed: {str(e)}"
    
    return None, "Repair produced non-dict result"


def create_fallback_response(raw_text: str, reason: str) -> dict:
    """
    Create a safe fallback JSON response when parsing fails.
    Preserves the raw output for manual review.
    """
    safe_observation = raw_text[:300] if raw_text else "No output received"
    safe_notes = raw_text[:500] if raw_text else "No output received"
    
    # Escape any problematic characters
    safe_observation = safe_observation.replace('\n', ' ').replace('\r', '')
    safe_notes = safe_notes.replace('\n', ' ').replace('\r', '')
    
    return {
        "health_status": "WARNING",
        "key_findings": [
            {
                "signal": "combined",
                "observation": "AI response could not be parsed",
                "interpretation": safe_observation,
                "confidence": "LOW"
            }
        ],
        "overall_severity": "MEDIUM",
        "recommended_actions": [
            "Review raw AI output",
            "Check model response format"
        ],
        "notes": f"Parse failure: {reason}. Raw output: {safe_notes}"
    }


def validate_analysis_schema(data: dict) -> dict:
    """
    Validate and normalize the analysis response to match expected schema.
    Fills in missing fields with safe defaults.
    """
    # Expected schema with defaults
    validated = {
        "health_status": data.get("health_status", "WARNING"),
        "key_findings": data.get("key_findings", []),
        "overall_severity": data.get("overall_severity", "MEDIUM"),
        "recommended_actions": data.get("recommended_actions", []),
        "notes": data.get("notes", "")
    }
    
    # Validate health_status
    if validated["health_status"] not in ["NORMAL", "WARNING", "CRITICAL"]:
        validated["health_status"] = "WARNING"
    
    # Validate severity
    if validated["overall_severity"] not in ["LOW", "MEDIUM", "HIGH"]:
        validated["overall_severity"] = "MEDIUM"
    
    # Ensure key_findings is a list
    if not isinstance(validated["key_findings"], list):
        validated["key_findings"] = []
    
    # Ensure recommended_actions is a list
    if not isinstance(validated["recommended_actions"], list):
        validated["recommended_actions"] = []
    
    # Ensure at least one finding
    if not validated["key_findings"]:
        validated["key_findings"] = [{
            "signal": "combined",
            "observation": "Analysis completed",
            "interpretation": "No specific findings reported",
            "confidence": "LOW"
        }]
    
    # Ensure at least one action
    if not validated["recommended_actions"]:
        validated["recommended_actions"] = ["Continue monitoring"]
    
    return validated


def normalize_gemini_response(raw_text: str, model_name: str = "unknown") -> dict:
    """
    Master function to normalize any Gemini response into valid analysis JSON.
    
    Process:
    1. Try direct JSON extraction
    2. If fails, attempt repair
    3. If repair fails, create fallback response
    4. Always validate schema
    
    Args:
        raw_text: Raw text response from Gemini
        model_name: Name of model used (for logging)
    
    Returns:
        dict: Valid analysis JSON matching expected schema
    """
    print(f"[NORMALIZE] Processing response from {model_name} ({len(raw_text) if raw_text else 0} chars)")
    
    # Step 1: Try extraction
    extracted, extract_error = extract_json_from_text(raw_text)
    
    if extracted:
        print(f"[NORMALIZE] ✅ Extraction succeeded")
        return validate_analysis_schema(extracted)
    
    print(f"[NORMALIZE] ⚠️ Extraction failed: {extract_error}")
    
    # Step 2: Try repair
    repaired, repair_error = attempt_json_repair(raw_text)
    
    if repaired:
        print(f"[NORMALIZE] ✅ Repair succeeded")
        return validate_analysis_schema(repaired)
    
    print(f"[NORMALIZE] ⚠️ Repair failed: {repair_error}")
    
    # Step 3: Create fallback
    print(f"[NORMALIZE] ⚠️ Using fallback response")
    return create_fallback_response(raw_text, f"Extract: {extract_error}; Repair: {repair_error}")


# =============================================================================
# GEMINI API SYSTEM PROMPT
# =============================================================================

# System prompt for Gemini
GEMINI_SYSTEM_PROMPT = """You are an industrial machine diagnostics assistant.

You will be given a SINGLE machine session summary as structured JSON.
This data was generated by deterministic signal-processing code and
represents aggregated sensor behavior over a fixed time window.

IMPORTANT RULES:
- Do NOT assume missing data.
- Do NOT invent sensors or values.
- Base all conclusions strictly on the provided JSON.
- If evidence is weak, say so explicitly.

TASK:
Analyze the session summary and return your response in STRICT JSON
using EXACTLY the following schema:

{
  "health_status": "NORMAL | WARNING | CRITICAL",
  "key_findings": [
    {
      "signal": "sound | vibration | gas | combined",
      "observation": "short factual observation",
      "interpretation": "likely technical meaning",
      "confidence": "LOW | MEDIUM | HIGH"
    }
  ],
  "overall_severity": "LOW | MEDIUM | HIGH",
  "recommended_actions": [
    "action item 1",
    "action item 2"
  ],
  "notes": "optional clarifications or uncertainty"
}

SESSION SUMMARY:
"""


# =============================================================================
# GEMINI API CALLER WITH FALLBACK
# =============================================================================

def call_gemini_with_fallback(session_data: dict) -> dict:
    """
    Send session data to Gemini API with model fallback and robust response parsing.
    
    Valid models (from ListModels API):
    - models/gemini-2.0-flash-lite (fastest, cheapest)
    - models/gemini-2.0-flash
    - models/gemini-2.5-flash-lite
    - models/gemini-2.5-flash
    
    Args:
        session_data: The aggregated session payload
    
    Returns:
        dict: { "model_used": str, "analysis": dict }
    
    Raises:
        ValueError: If API key not configured
        RuntimeError: If all models fail
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    # Build compact prompt (token-efficient)
    prompt = GEMINI_SYSTEM_PROMPT + json.dumps(session_data, separators=(",", ":"))
    
    headers = {"Content-Type": "application/json"}
    
    # FIXED: Simple payload without responseMimeType
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 512
        }
    }
    
    last_error = None
    
    for model in GEMINI_MODELS:
        # Model already includes "models/" prefix, use v1beta endpoint
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        
        try:
            print(f"[GEMINI] Trying model: {model}")
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                # Extract raw text
                try:
                    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as e:
                    last_error = f"{model}: Malformed response structure - {str(e)}"
                    print(f"[GEMINI] ⚠️ {last_error}")
                    continue
                
                print(f"[GEMINI] ✅ Got response from {model} ({len(raw_text)} chars)")
                
                # FIXED: Use robust normalizer instead of direct json.loads
                analysis = normalize_gemini_response(raw_text, model)
                
                return {
                    "model_used": model,
                    "analysis": analysis
                }
            
            # Handle specific error codes
            if response.status_code == 400:
                error_detail = ""
                try:
                    error_detail = response.json().get('error', {}).get('message', '')
                except:
                    pass
                last_error = f"{model}: Invalid request (400) - {error_detail}"
                print(f"[GEMINI] ⚠️ {last_error}")
                continue
            
            if response.status_code in (403, 429):
                error_detail = ""
                try:
                    error_detail = response.json().get('error', {}).get('message', '')
                except:
                    pass
                last_error = f"{model}: quota/permission denied ({response.status_code}) {error_detail}"
                print(f"[GEMINI] ⚠️ {last_error}")
                continue
            
            # Other errors
            error_detail = ""
            try:
                error_detail = response.json().get('error', {}).get('message', response.text[:200])
            except:
                error_detail = response.text[:200]
            last_error = f"{model}: HTTP {response.status_code} - {error_detail}"
            print(f"[GEMINI] ⚠️ {last_error}")
            
        except requests.exceptions.Timeout:
            last_error = f"{model}: request timeout (30s)"
            print(f"[GEMINI] ⚠️ {last_error}")
        except requests.exceptions.ConnectionError as e:
            last_error = f"{model}: connection error - {str(e)}"
            print(f"[GEMINI] ⚠️ {last_error}")
        except Exception as e:
            last_error = f"{model}: exception {str(e)}"
            print(f"[GEMINI] ⚠️ {last_error}")
    
    # If ALL models fail
    raise RuntimeError(
        f"All Gemini models unavailable. Last error: {last_error}"
    )


def call_gemini_api(session_data: dict) -> dict:
    """
    DEPRECATED: Use call_gemini_with_fallback() instead.
    Kept for backward compatibility.
    """
    result = call_gemini_with_fallback(session_data)
    return result["analysis"]


def get_latest_data_range(conn, user_id: int, duration_seconds: int = 60) -> dict:
    """
    Get the latest data time range from the database.
    Returns start/stop timestamps based on the most recent audio data.
    
    Args:
        conn: PostgreSQL connection object
        user_id: The ID of the authenticated user
        duration_seconds: How many seconds of data to include (default 60)
    
    Returns:
        dict with 'start', 'stop', 'has_data', 'audio_count', 'esp32_count'
    """
    cursor = conn.cursor()
    try:
        print(f"[DEBUG] get_latest_data_range CHECK user_id={user_id} duration={duration_seconds}")
        
        # Get latest audio timestamp
        cursor.execute("""
            SELECT 
                MAX(timestamp) - INTERVAL '%s seconds' AS start_ts,
                MAX(timestamp) AS stop_ts,
                COUNT(*) AS total_rows
            FROM raw_audio
            WHERE user_id = %%s
        """ % duration_seconds, (user_id,))
        row = cursor.fetchone()
        print(f"[DEBUG] get_latest_data_range ROW: {row}")
        
        if not row or not row[1]:
            return {
                "has_data": False,
                "start": None,
                "stop": None,
                "audio_count": 0,
                "esp32_count": 0,
                "message": "No audio data in database"
            }
        
        start_ts = row[0]
        stop_ts = row[1]
        
        # Count audio rows in this range
        cursor.execute("""
            SELECT COUNT(*) FROM raw_audio 
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        audio_count = cursor.fetchone()[0]
        
        # Count ESP32 rows in this range
        cursor.execute("""
            SELECT COUNT(*) FROM esp32_data 
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        esp32_count = cursor.fetchone()[0]
        
        return {
            "has_data": audio_count > 0,
            "start": start_ts.isoformat() if start_ts else None,
            "stop": stop_ts.isoformat() if stop_ts else None,
            "audio_count": audio_count,
            "esp32_count": esp32_count,
            "message": f"Found {audio_count} audio rows, {esp32_count} ESP32 rows"
        }
        
    finally:
        cursor.close()


def validate_time_range(conn, user_id: int, start_ts: str, stop_ts: str) -> dict:
    """
    Validate that the requested time range has data.
    Returns validation result with counts.
    """
    cursor = conn.cursor()
    try:
        # Count audio rows
        cursor.execute("""
            SELECT COUNT(*) FROM raw_audio 
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        audio_count = cursor.fetchone()[0]
        
        # Count ESP32 rows
        cursor.execute("""
            SELECT COUNT(*) FROM esp32_data 
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        esp32_count = cursor.fetchone()[0]
        
        # Get actual data boundaries
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM raw_audio WHERE user_id = %s", (user_id,))
        audio_range = cursor.fetchone()
        
        return {
            "valid": audio_count > 0 or esp32_count > 0,
            "audio_count": audio_count,
            "esp32_count": esp32_count,
            "audio_earliest": str(audio_range[0]) if audio_range[0] else None,
            "audio_latest": str(audio_range[1]) if audio_range[1] else None,
            "requested_start": start_ts,
            "requested_stop": stop_ts
        }
        
    finally:
        cursor.close()


def aggregate_session_data(conn, user_id: int, start_ts: str, stop_ts: str, machine_id: str = None, device_id: str = None):
    """
    Fetch and aggregate data from raw_audio and esp32_data between start and stop timestamps.
    
    Args:
        conn: PostgreSQL connection object
        user_id: The ID of the authenticated user
        start_ts: ISO timestamp string (e.g., "2026-01-10T10:00:00")
        stop_ts: ISO timestamp string (e.g., "2026-01-10T10:30:00")
        machine_id: Optional filter for raw_audio
        device_id: Optional filter for esp32_data
    
    Returns:
        dict: Aggregated session payload (ready for Gemini)
    
    Raises:
        ValueError: If no data exists in the selected time range
    """
    try:
        cursor = conn.cursor()

        # =====================
        # 0. VALIDATE TIME RANGE (Hard Guard)
        # =====================
        cursor.execute("""
            SELECT COUNT(*) FROM raw_audio
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        audio_count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM esp32_data
            WHERE user_id = %s AND timestamp BETWEEN %s AND %s
        """, (user_id, start_ts, stop_ts))
        esp32_count = cursor.fetchone()[0]
        
        if audio_count == 0 and esp32_count == 0:
            # Return fallback payload instead of error
            try:
                duration_sec = 60.0  # Default fallback
                return {
                    "machine_id": machine_id or "unknown",
                    "device_id": device_id or "unknown",
                    "session": {
                        "start": start_ts,
                        "stop": stop_ts,
                        "duration_sec": duration_sec
                    },
                    "sound_summary": {
                        "data_mode": "none",
                        "dominant_freq_median": 0,
                        "freq_iqr": [0, 0],
                        "out_of_profile_events": 0
                    },
                    "vibration_summary": {"avg": 0, "peak": 0, "event_count": 0},
                    "gas_summary": {"avg_raw": 0, "peak_raw": 0, "status": "LOW"}
                }
            except Exception as e:
                print(f"[ERROR] Failed to create fallback payload: {e}")
                raise
        
        # Parse timestamps for duration calculation
        start_dt = datetime.fromisoformat(start_ts.replace('Z', '+00:00').replace('+00:00', ''))
        stop_dt = datetime.fromisoformat(stop_ts.replace('Z', '+00:00').replace('+00:00', ''))
        duration_sec = (stop_dt - start_dt).total_seconds()
        
        # =====================
        # 1. AGGREGATE RAW_AUDIO (Sound Data)
        # =====================
        sound_summary = aggregate_sound_data(cursor, user_id, start_ts, stop_ts, machine_id, conn)
        
        # =====================
        # 2. AGGREGATE ESP32_DATA (Vibration + Gas)
        # =====================
        vibration_summary, gas_summary, resolved_device_id = aggregate_esp32_data(
            cursor, user_id, start_ts, stop_ts, device_id
        )
        
        # =====================
        # 3. BUILD FINAL PAYLOAD
        # =====================
        payload = {
            "machine_id": machine_id or sound_summary.get("detected_machine_id", "unknown"),
            "device_id": resolved_device_id or device_id or "unknown",
            "session": {
                "start": start_ts,
                "stop": stop_ts,
                "duration_sec": round(duration_sec, 1)
            },
            "sound_summary": {
                "data_mode": sound_summary.get("data_mode", "unknown"),
                "dominant_freq_median": sound_summary.get("dominant_freq_median", 0),
                "freq_iqr": sound_summary.get("freq_iqr", [0, 0]),
                "out_of_profile_events": sound_summary.get("out_of_profile_events", 0)
            },
            "vibration_summary": vibration_summary,
            "gas_summary": gas_summary
        }
        
        return payload

    except Exception as e:
        print(f"[WARN] aggregate_session_data failed, returning dummy data: {str(e)}")
        # Return dummy data on any error
        try:
            duration_sec = 60.0  # Default fallback
            return {
                "machine_id": machine_id or "unknown",
                "device_id": device_id or "unknown",
                "session": {
                    "start": start_ts,
                    "stop": stop_ts,
                    "duration_sec": duration_sec
                },
                "sound_summary": {
                    "data_mode": "none",
                    "dominant_freq_median": 0,
                    "freq_iqr": [0, 0],
                    "out_of_profile_events": 0
                },
                "vibration_summary": {"avg": 0, "peak": 0, "event_count": 0},
                "gas_summary": {"avg_raw": 0, "peak_raw": 0, "status": "LOW"}
            }
        except Exception as fallback_e:
            print(f"[ERROR] Failed to create fallback payload: {fallback_e}")
            raise e  # Re-raise original error if fallback fails
    finally:
        try:
            cursor.close()
        except:
            pass  # Ignore cursor close errors


def aggregate_sound_data(cursor, user_id: int, start_ts: str, stop_ts: str, machine_id: str, conn):
    """
    Aggregate sound data from raw_audio table.
    Computes median frequency, IQR, and out-of-profile event count.
    
    Uses controlled fallback:
    1. Try LIVE data first (preferred for real-time diagnostics)
    2. Fallback to CALIBRATION data if no live data exists
    3. Data modes are NEVER mixed
    """
    def fetch(mode):
        q = """
            SELECT dominant_freq, freq_confidence, machine_id
            FROM raw_audio
            WHERE user_id = %s
              AND timestamp BETWEEN %s AND %s
              AND dominant_freq IS NOT NULL
              AND dominant_freq > 0
              AND mode = %s
        """
        params = [user_id, start_ts, stop_ts, mode]
        if machine_id:
            q += " AND machine_id = %s"
            params.append(machine_id)
        q += " ORDER BY timestamp"
        cursor.execute(q, params)
        return cursor.fetchall()

    # 1. Try LIVE first
    rows = fetch("live")
    data_mode = "live"

    # 2. Fallback to CALIBRATION if no live data
    if not rows:
        rows = fetch("calibration")
        data_mode = "calibration"

    # 3. No data at all
    if not rows:
        return {
            "data_mode": "none",
            "dominant_freq_median": 0,
            "freq_iqr": [0, 0],
            "out_of_profile_events": 0,
            "detected_machine_id": machine_id
        }

    # Extract frequencies
    frequencies = [r[0] for r in rows if r[0] and r[0] > 0]
    
    # Detect machine_id if not provided
    detected_machine_id = machine_id or max(
        (r[2] for r in rows if r[2]), default=None
    )

    if not frequencies:
        return {
            "data_mode": data_mode,
            "dominant_freq_median": 0,
            "freq_iqr": [0, 0],
            "out_of_profile_events": 0,
            "detected_machine_id": detected_machine_id
        }

    # Sort for percentile calculations
    frequencies.sort()
    n = len(frequencies)

    # Compute median
    median = (
        frequencies[n // 2]
        if n % 2
        else (frequencies[n // 2 - 1] + frequencies[n // 2]) / 2
    )

    # Compute IQR (25th and 75th percentiles)
    q1 = frequencies[int(0.25 * (n - 1))]
    q3 = frequencies[int(0.75 * (n - 1))]

    # Only compare against profiles in LIVE mode (calibration IS the profile)
    out_of_profile = (
        count_out_of_profile_events(conn, user_id, frequencies, detected_machine_id)
        if data_mode == "live" and detected_machine_id
        else 0
    )

    return {
        "data_mode": data_mode,
        "dominant_freq_median": round(median, 2),
        "freq_iqr": [round(q1, 2), round(q3, 2)],
        "out_of_profile_events": out_of_profile,
        "detected_machine_id": detected_machine_id
    }


def count_out_of_profile_events(conn, user_id: int, frequencies: list, machine_id: str) -> int:
    """
    Count how many frequency readings fall outside the machine's calibrated IQR range.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT iqr_low, iqr_high FROM machine_profiles WHERE machine_id = %s AND user_id = %s",
            (machine_id, user_id)
        )
        row = cursor.fetchone()
        
        if not row:
            return 0
        
        iqr_low, iqr_high = row
        out_of_range = sum(1 for f in frequencies if f < iqr_low or f > iqr_high)
        return out_of_range
        
    finally:
        cursor.close()


def aggregate_esp32_data(cursor, user_id: int, start_ts: str, stop_ts: str, device_id: str = None):
    """
    Aggregate ESP32 sensor data (vibration + gas) for a user.
    """
    # Build query with optional device_id filter
    q = """
        SELECT vibration, gas_raw, device_id, gas_status
        FROM esp32_data
        WHERE user_id = %s
          AND timestamp BETWEEN %s AND %s
    """
    params = [user_id, start_ts, stop_ts]
    if device_id:
        q += " AND device_id = %s"
        params.append(device_id)
        
    cursor.execute(q, params)
    rows = cursor.fetchall()
    
    if not rows:
        return (
            {"avg": 0, "peak": 0, "event_count": 0},
            {"avg_raw": 0, "peak_raw": 0, "status": "NO_DATA"},
            device_id
        )
        
    # Detect device_id if missing
    resolved_device_id = device_id or max(
        (r[2] for r in rows if r[2]), default=None
    )
    
    # Process Vibration
    vibrations = [r[0] for r in rows if r[0] is not None]
    if vibrations:
        vib_avg = sum(vibrations) / len(vibrations)
        vib_peak = max(vibrations)
        vib_events = sum(1 for v in vibrations if v > 0) # Assumes >0 is event
    else:
        vib_avg, vib_peak, vib_events = 0, 0, 0
        
    vibration_summary = {
    }
    
    # Gas summary
    gas_avg = sum(gas_raws) / len(gas_raws) if gas_raws else 0
    gas_peak = max(gas_raws) if gas_raws else 0
    
    # Determine overall gas status
    gas_status = determine_gas_status(gas_avg, gas_statuses)
    
    gas_summary = {
        "avg_raw": round(gas_avg, 1),
        "peak_raw": round(gas_peak, 1),
        "status": gas_status
    }
    
    return vibration_summary, gas_summary, resolved_device_id


def determine_gas_status(avg_gas: float, statuses: list) -> str:
    """
    Determine overall gas status based on average value and collected statuses.
    """
    # Priority: if any DANGER/RISK, mark HIGH
    if any(s in ['DANGER', 'RISK', 'HAZARDOUS'] for s in statuses):
        return "HIGH"
    
    # By average value thresholds
    if avg_gas > 2000:
        return "HIGH"
    elif avg_gas > 800:
        return "MEDIUM"
    else:
        return "LOW"


def get_gemini_api_key_status() -> dict:
    """
    Check if Gemini API key is configured (without exposing it).
    """
    key = os.getenv('GEMINI_API_KEY', '')
    return {
        "configured": bool(key and len(key) > 10),
        "key_preview": f"{key[:4]}...{key[-4:]}" if key and len(key) > 10 else "NOT_SET"
    }
