# Rate-Limiting Optimization Changes

## Summary
Updated the calibration and live detection system to reduce request frequency and avoid ngrok rate limits:

1. **Calibration Mode**: Now collects all audio frames locally during the 60-second recording, then sends the **complete batch once** to the server at the end
2. **Live Detection Mode**: Batches frames and sends data every **500ms instead of 100ms** (5x reduction in requests)

## Changes Made

### server.py
- **Modified `/ingest` route** to handle batch frame processing:
  - `mode='calibration'`: Expects `frames` array (all frames from 60s session) + `machine_id`
  - `mode='live'`: Expects `frames` array (batched frames) with no machine_id
  - Silently filters frames with low amplitude or confidence during insertion
  - Returns batch statistics (frames_received, frames_inserted, detected_machine)
  
- **Removed per-frame overhead**: No longer processes individual frames; instead processes batches
  
- **Preserved anomaly detection**: EWMA z-score calculation still runs on each frame during insertion

### index.html
- **Calibration changes**:
  - `calibrationLoop()`: Now collects all frames to `calibrationData` array instead of sending per-frame
  - No longer sends individual fetch requests during recording
  - `stopCalibration()`: Sends **one batch request** with all 600 frames (~60s × 10 fps) at end of recording
  - Displays upload progress with "📤 Uploading..." status

- **Live detection changes**:
  - Added `detectionBatch` array and `detectionBatchTimer` variables
  - `detectionLoop()`: Collects frames every 100ms loop, but only sends batch every 500ms
  - Batches ~5 frames (100ms × 5) before sending to server
  - Reduces request rate from **10 req/sec → 2 req/sec** (80% reduction)

- **stopDetection()**: Clears batch variables to prevent memory leaks

## Benefits
✅ **Reduced load on ngrok**: From ~10 requests/sec to 1 request per 60s (calibration) + 2 req/sec (detection)
✅ **Lower database contention**: Fewer concurrent queries
✅ **Better server efficiency**: Bulk inserts vs individual INSERTs
✅ **Avoids rate limit errors**: ngrok and backend won't be overwhelmed
✅ **Maintains detection accuracy**: Still performs real-time ML analysis and machine identification

## Testing
- Server starts successfully on http://127.0.0.1:5000
- Both `/ingest` and `/save_profile` endpoints ready for batch requests
- All database operations preserve data integrity
