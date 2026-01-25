# FSociety: Multi-Machine Sound Detection System

**FSociety** is a browser-based machine sound monitoring system that simultaneously detects 3+ machines running together by analyzing their unique frequency signatures using FFT (Fast Fourier Transform).

## 🎯 Overview

The system allows you to:
1. **Calibrate** individual machines to learn their sound signatures.
2. **Detect** which machines are running in real-time, even when multiple are operating simultaneously.
3. **Monitor** history and performance via a dashboard.

### Technical System Overview
The system operates as a distributed sensor network where the browser acts as an intelligent edge device.

**End-to-End Data Flow:**
1.  **Microphone Input**: Browser captures raw audio at 44.1/48kHz.
2.  **Web Audio API**: Performs real-time FFT (2048 bins) to convert time-domain signals to frequency-domain.
3.  **Feature Extraction**: JavaScript frontend extracts dominant peaks (Frequency, Amplitude) and filters noise.
4.  **Batch Ingestion**: Compressed feature vectors are sent to the Python backend in batches (every ~500ms).
5.  **Pattern Matching**: Backend algorithms match incoming peaks against statistical machine profiles stored in PostgreSQL.
6.  **Temporal Filtering**: A stability layer filters transient errors before updating the live UI.

---

## 📸 System Screenshots

<p align="center">
  <img src="images/viso1.jpeg" width="45%" alt="Dashboard Overview">
  <img src="images/viso2.jpeg" width="45%" alt="Live Detection">
</p>
<p align="center">
  <img src="images/viso3.jpeg" width="45%" alt="Calibration">
  <img src="images/viso4.jpeg" width="45%" alt="Login">
</p>

---

## � Sensor & Input Details

### Primary Sensor: Microphone
We utilize the **browser-based microphone** via the `navigator.mediaDevices.getUserMedia` API. This choice was driven by accessibility—eliminating the need for specialized hardware sensors (like piezoelectric accelerometers) for initial deployment.

-   **Sampling Rate**: 44.1kHz or 48kHz (Hardware dependent, typically Nyquist frequency ~22kHz).
-   **FFT Configuration**: 
    -   **Window Size**: 2048 samples (providing ~21.5Hz frequency resolution per bin).
    -   **Smoothing**: `smoothingTimeConstant = 0.8` to reduce jitter in the FFT output.
    -   **Min Decibels**: -90 dB (Noise floor cutoff).

### Why Audio?
While vibration sensors are standard in industry, **audio signatures** often correlate strongly with mechanical rotation and vibration harmonics. Audio allows for **non-contact monitoring**, meaning a single device can monitor multiple machines in a room without physical attachment.

**Limitations**: 
-   Susceptible to background conversation (filtered out via amplitude thresholds).
-   Browser auto-gain control (AGC) can sometimes distort absolute amplitude readings (mitigated by relative peak analysis).

---

## 📊 Signal Processing Pipeline

The core logic transforms raw sound waves into actionable machine IDs.

### 1. Fast Fourier Transform (FFT)
We use the Web Audio API's `AnalyserNode` to perform a real-time FFT.
-   **Binning**: The 0-24kHz spectrum is divided into 1024 bins.
-   **Resolution**: Each bin represents roughly 21.5 Hz.

### 2. Feature Extraction (Edge Processing)
Instead of streaming raw audio (which would require massive bandwidth), the **frontend JavaScript** processes frames locally:
1.  **Scan Spectrum**: Iterates through frequency bins.
2.  **Peak Detection**: Identifies local maxima above a dynamic noise floor (`noiseFloor`).
3.  **Subsonic filtering**: Ignores frequencies below 50Hz (often ambient rumble).
4.  **Payload Generation**: Sends only the mathematical description of the peaks (`[{freq: 250, amp: 0.8}, ...]`) to the server.

### 3. Harmonic Handling
Machines rarely produce a single tone. They produce a **fundamental frequency** and a series of **harmonics**. Our system captures up to 5 dominant peaks per frame, ensuring that unique harmonic "fingerprints" are preserved for matching.

---

## 🧠 Machine Profiling Logic

### Statistical Calibration
During the 60-second calibration phase, the system builds a statistical model of the machine's "normal" operation. It's not just a snapshot; it's a distribution.

1.  **Data Collection**: Backend aggregates ~600 frames of peak data.
2.  **Cluster Analysis**: The system identifies the most consistent frequency bands.
3.  **IQR Profiling**: For the dominant frequency, we calculate the **Interquartile Range (IQR)**:
    -   **Median (Q2)**: The center of the frequency distribution.
    -   **Spread (Q3 - Q1)**: Defines the stability of the machine.
    -   **Tolerance Calculation**: We define the valid range as `[Q1 - 0.5*IQR, Q3 + 0.5*IQR]`.

**Why IQR?**
Standard deviation assumes a normal distribution, but machine noise often has outliers (transient clicks/pops). IQR is robust against outliers, creating a tighter, more accurate profile that rejects noise.

**Drift Handling**: Machines speed up and slow down under load. Our profile stores valid frequency **bands** rather than exact integers, allowing for ±10-20Hz drift (or more, depending on the machine's variance).

---

## � Multi-Machine Detection Algorithm

How do we know "Machine A" and "Machine B" are running simultaneously?

### 1. Multi-Band Matching
When live data arrives, the algorithm checks each peak against **all** active machine profiles in the database.
-   **Rule**: A machine is considered "potentially active" if **≥2 of its harmonic bands** match the current signal's peaks.
-   This "multi-key" verification drastically reduces false positives compared to single-frequency matching.

### 2. Confidence Scoring & Thresholding
-   **Amplitude Check**: Signals below `MIN_PEAK_AMP` (0.15) are ignored as background noise.
-   **Band Overlap**: If simple frequency matching is ambiguous, the system checks for harmonic alignment.

### 3. Temporal Stability Filter (Hysteresis)
A single "bad frame" shouldn't toggle the status. We implement a sliding window filter:
-   **Window Size**: Last 15 batches (~7.5 seconds).
-   **Threshold**: A machine must be detected in **≥60%** of the window to be displayed as "RUNNING".
-   **Effect**: This prevents flickering UI states when momentary interference occurs.

### 4. Edge Case Handling
-   **Frequency Collision**: If Machine A (200Hz) and Machine B (200Hz) overlap perfectly, the system looks for their *secondary* harmonics (e.g., 400Hz vs 600Hz) to distinguish them.

---

## 🏗️ Backend & Architecture

Most prototype audio/ML apps crash under load. FSociety is architected for **concurrency and persistence**.

### Tech Stack
-   **Runtime**: Python 3.9+ (Flask)
-   **Database**: PostgreSQL 13+ (Relational data + JSONB for flexible profile storage).
-   **Task Queue**: `queue.Queue` + Background Worker Threads.

### Data Model
-   **`users`**: Authentication and isolation.
-   **`machine_profiles`**: Stores the statistical fingerprints (JSONB `freq_bands`, `stats`).
-   **`raw_audio`**: Time-series log of all spectral data for historical replay and debugging.

### Scalability Design
-   **Decoupled Ingestion**: The `/ingest` endpoint simply pushes data to an in-memory queue and returns `200 OK` immediately. The heavy processing (DB writes, matching algorithms) happens in a separate **worker thread**.
-   **Batch Processing**: The frontend aggregates ~500ms of audio frames before sending a single HTTP request, reducing network overhead by 20x compared to per-frame streaming.

---

## � Quick Start

### 1. Prerequisites
- **Python 3.8+**
- **PostgreSQL** installed and running locally.
- **Microphone** access for the browser.

### 2. Setup Database
Ensure PostgreSQL is running. The system requires a database named `soundml`.
Update your `.env` or `app/db.py` with your credentials.

### 3. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 4. Start Server
```powershell
python run.py
```
→ Opens on http://127.0.0.1:5000

---

## 📈 Performance Characteristics

| Metric | Value | Explanation |
| :--- | :--- | :--- |
| **End-to-End Latency** | ~500 ms | Dominated by the batching window (intentional buffering for stability). |
| **Backend Processing** | < 10 ms | Time to match peaks against 5+ profiles in Python. |
| **CPU Load** | < 5% | Efficient FFT is offloaded to the client (browser); Python only does math on extracted features. |
| **Network Payload** | ~2 KB/sec | Highly compressed feature vectors (peaks only), not raw audio. |

---

## � Known Limitations & Engineering Trade-offs

### 1. Frequency Overlap Constraint
**Limitation**: Machines operating within **<40Hz** of each other are difficult to distinguish.
**Trade-off**: We accepted this to simplify the algorithm. Solving this would require complex source separation (ICA) which is computationally heavy for a real-time web app.

### 2. Environmental Sensitivity
**Limitation**: Loud background noise (talking, music) creates false peaks.
**Trade-off**: We use a dynamic `noiseFloor` and `AMPLITUDE_THRESHOLD`, but extreme noise still pollutes the spectrum. Industrial environments usually have consistent noise floors that can be calibrated out.

### 3. Browser Hardware Variance
**Limitation**: Different microphones have different frequency responses.
**Trade-off**: Calibration is device-specific. A profile detection model trained on a laptop mic might not work perfectly on a phone mic without recalibration.

---

## 🔮 Future Improvements

1.  **Sensor Fusion**: Combine the audio analysis with an **ESP32 vibration sensor** (accelerometer) for a 2-factor authentication of machine state.
2.  **Machine Learning Classification**: Replace the statistical (IQR) matcher with a lightweight **CNN (Convolutional Neural Network)** trained on the spectrogram images for higher accuracy in noisy environments.
3.  **Edge FFT**: Move the FFT processing to an MCU (like the ESP32) to make the sensor strictly IoT, removing the need for a browser session.

---

## 📂 Project Structure

```text
FSociety/
├── app/
│   ├── routes/           # REST API endpoints
│   ├── services/         # Core Logic: signal processing, matching, DB
│   ├── static/js/        # Frontend: Web Audio API, Chart.js visualization
│   └── templates/        # UI Views
├── data/                 # Local data storage
├── docs/                 # Detailed documentation
├── images/               # Assets
├── run.py                # Entry point
└── requirements.txt      # Dependencies
```

---

## 📜 License
MIT License. Built for the Hackathon 2026.
