import queue
import os
import time
import json
import threading
import traceback
from datetime import datetime
import psycopg2.extras
from app.db import get_db
from app.services.audio_processing import AMPLITUDE_THRESHOLD, noise_model, identify_machines
from app.services.stability import update_detection_history, get_stable_machines

# =========================
# BATCH QUEUE FOR ASYNC PROCESSING
# =========================
# Queue to hold incoming large payloads so the HTTP response can return quickly
BATCH_QUEUE = queue.Queue()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED_BATCH_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data', 'failed_batches') # app/../data/failed_batches
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
    conn = get_db()
    
    while True:
        batch = BATCH_QUEUE.get()
        if batch is None:
            break

        try:
            # Reuse ingest logic but in worker context (create fresh cursor)
            mode = batch.get('mode', 'live')
            user_id = batch.get('user_id') # REQUIRED

            if not user_id:
                print('[WARN] Skipping batch missing user_id')
                continue

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
                        (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode, user_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            datetime.fromtimestamp(timestamp / 1000),
                            amplitude,
                            dominant_freq,
                            freq_confidence,
                            psycopg2.extras.Json(peaks),
                            machine_id,
                            mode,
                            user_id
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
                        (timestamp, amplitude, dominant_freq, freq_confidence, peaks, machine_id, mode, user_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            datetime.fromtimestamp(timestamp / 1000),
                            amplitude,
                            dominant_freq,
                            freq_confidence,
                            psycopg2.extras.Json(peaks),
                            None,
                            mode,
                            user_id
                        )
                    )
                    inserted_count += 1

                    # TODO: Update identify_machines to take user_id
                    machines_in_frame = identify_machines(user_id, peaks)
                    running_machines.update(machines_in_frame["detected"]) 

                conn.commit()

                # Update temporal stability (fetch all machine ids for THIS user)
                cursor.execute("SELECT machine_id FROM machine_profiles WHERE user_id = %s", (user_id,))
                all_machines = [row[0] for row in cursor.fetchall()]
                
                update_detection_history(user_id, running_machines, all_machines)
                stable_machines = get_stable_machines(user_id, all_machines)

                cursor.close()
                print(f"\n[OK] (worker) LIVE BATCH: user={user_id}, {len(frames)} frames, {inserted_count} inserted")
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

def start_worker():
    worker_thread = threading.Thread(target=batch_worker, daemon=True)
    worker_thread.start()
