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
