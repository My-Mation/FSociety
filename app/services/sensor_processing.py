def process_vibration_data(vibration_samples):
    """
    Process vibration samples to generate summary metrics.
    
    Args:
        vibration_samples: List of integer/float vibration values
    
    Returns:
        dict: Summary of vibration stats (percent active, avg, samples) or None if no samples
    """
    if not vibration_samples or len(vibration_samples) == 0:
        return None
        
    vibration_count = sum(1 for v in vibration_samples if v == 0)
    total_samples = len(vibration_samples)
    
    vibration_percent = 0
    avg_raw = 0
    
    if total_samples > 0:
        vibration_percent = (1 - (vibration_count / total_samples)) * 100
        avg_raw = sum(vibration_samples) / total_samples
        
    return {
        "samples": total_samples,
        "vibration_detected_count": vibration_count,
        "vibration_percent": round(vibration_percent, 1),
        "avg_raw_value": round(avg_raw, 3),
        "has_vibration": vibration_percent < 50
    }

def process_gas_data(gas_samples):
    """
    Process gas samples to generate safety status metrics.
    
    Args:
        gas_samples: List of gas values (dicts or ints)
    
    Returns:
        dict: Gas safety summary or None if no samples
    """
    if not gas_samples or len(gas_samples) == 0:
        return None
        
    # Normalize input: handle list of dicts or list of values
    raw_values = [g.get("raw", 0) if isinstance(g, dict) else g for g in gas_samples]
    valid_raw_values = [v for v in raw_values if v > 0]
    
    avg_gas = 0
    max_gas = 0
    min_gas = 0
    
    if valid_raw_values:
        avg_gas = sum(valid_raw_values) / len(valid_raw_values)
        max_gas = max(valid_raw_values)
        min_gas = min(valid_raw_values)
    
    # Check gas safety status
    if avg_gas == 0: 
        gas_status = "NO_DATA"
    elif avg_gas < 800: 
        gas_status = "SAFE"
    elif avg_gas < 2000: 
        gas_status = "MODERATE"
    else: 
        gas_status = "HAZARDOUS"
    
    return {
        "samples": len(gas_samples),
        "valid_samples": len(valid_raw_values),
        "avg_raw": round(avg_gas, 1),
        "max_raw": round(max_gas, 1),
        "min_raw": round(min_gas, 1),
        "status": gas_status
    }
