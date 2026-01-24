# =========================
# TEMPORAL STABILITY TRACKING (for multi-machine detection)
# =========================
# Track last N detections per machine for stability filtering
STABILITY_WINDOW = 15  # Track last 15 batches
STABILITY_THRESHOLD = 0.6  # Require 60% detection rate

# =========================
# TEMPORAL STABILITY TRACKING (for multi-machine detection)
# =========================
# Track last N detections per machine for stability filtering
STABILITY_WINDOW = 15  # Track last 15 batches
STABILITY_THRESHOLD = 0.6  # Require 60% detection rate

# user_id -> { machine_id -> [list of history] }
detection_history = {}

def update_detection_history(user_id, running_machines, all_machines_in_profile):
    """Update detection history for temporal stability filtering per user"""
    if user_id not in detection_history:
        detection_history[user_id] = {}
        
    user_history = detection_history[user_id]
        
    for machine_id in all_machines_in_profile:
        if machine_id not in user_history:
            user_history[machine_id] = []
        
        detected = 1 if machine_id in running_machines else 0
        user_history[machine_id].append(detected)
        
        # Keep only last STABILITY_WINDOW entries
        if len(user_history[machine_id]) > STABILITY_WINDOW:
            user_history[machine_id].pop(0)

def get_stable_machines(user_id, all_machines_in_profile):
    """Return only machines detected in ≥60% of last STABILITY_WINDOW batches for this user"""
    if user_id not in detection_history:
        return []
        
    user_history = detection_history[user_id]
    stable = []
    
    for machine_id in all_machines_in_profile:
        if machine_id not in user_history or len(user_history[machine_id]) < 5:
            continue  # Need at least 5 observations
        
        detection_rate = sum(user_history[machine_id]) / len(user_history[machine_id])
        if detection_rate >= STABILITY_THRESHOLD:
            stable.append(machine_id)
    
    return stable
