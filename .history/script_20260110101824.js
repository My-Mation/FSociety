// ==========================================
// CONFIGURATION
// ==========================================
const BACKEND_URL = `${window.location.origin}`;
const CALIBRATION_DURATION = 60;
const AMPLITUDE_THRESHOLD = 0.5;
const CONFIDENCE_THRESHOLD = 0.08;

// Gas thresholds for display
const GAS_SAFE_LIMIT = 300;
const GAS_HAZARD_LIMIT = 700;

// ==========================================
// STATE VARIABLES
// ==========================================
let audioContext = null;
let analyser = null;
let microphone = null;

// Calibration state
let isCalibrating = false;
let calibrationData = [];
let calibrationTimer = 0;
let calibrationFrameCount = 0;
let selectedMachine = null;
let calibrationVibrationSamples = [];
let calibrationGasSamples = [];
let calibrationESP32PollTimer = null;

// Run Check state
let isRunningCheck = false;
let runCheckBatch = [];
let runCheckTimer = null;

// Live Detection state
let isLiveDetecting = false;
let liveBatch = [];
let liveBatchTimer = 0;
let liveESP32PollTimer = null;

// Canvas contexts
let calibrationCanvasCtx = null;
let liveCanvasCtx = null;

// Chart contexts
let calibrationSpectrumCtx = null;
let calibrationHistoryCtx = null;
let liveSpectrumCtx = null;
let liveConfidenceCtx = null;
let liveGasCtx = null;
let liveVibrationCtx = null;

// Historical data for charts
let amplitudeHistory = [];
let frequencyHistory = [];
let gasHistory = [];
let vibrationHistory = [];
let confidenceHistory = [];
let liveStartTime = 0;
let liveSampleCount = 0;

// Stored profiles cache
let machineProfiles = {};

// ==========================================
// INITIALIZATION
// ==========================================
window.addEventListener('load', function() {
    initializeCanvases();
    generateCalibrationButtons();
    loadProfiles();
    console.log("✅ System initialized");
});

function initializeCanvases() {
    const calibCanvas = document.getElementById('calibration-canvas');
    const liveCanvas = document.getElementById('live-canvas');
    
    if (calibCanvas) {
        calibrationCanvasCtx = calibCanvas.getContext('2d');
        calibCanvas.width = calibCanvas.offsetWidth || 800;
        calibCanvas.height = calibCanvas.offsetHeight || 150;
    }
    
    // Calibration chart canvases
    const calibSpectrum = document.getElementById('calibration-spectrum');
    if (calibSpectrum) {
        calibrationSpectrumCtx = calibSpectrum.getContext('2d');
        calibSpectrum.width = calibSpectrum.offsetWidth || 800;
        calibSpectrum.height = 150;
    }
    
    const calibHistory = document.getElementById('calibration-history');
    if (calibHistory) {
        calibrationHistoryCtx = calibHistory.getContext('2d');
        calibHistory.width = calibHistory.offsetWidth || 800;
        calibHistory.height = 120;
    }
    
    if (liveCanvas) {
        liveCanvasCtx = liveCanvas.getContext('2d');
        liveCanvas.width = liveCanvas.offsetWidth || 800;
        liveCanvas.height = liveCanvas.offsetHeight || 150;
    }
    
    // Live detection chart canvases
    const liveSpectrum = document.getElementById('live-spectrum-chart');
    if (liveSpectrum) {
        liveSpectrumCtx = liveSpectrum.getContext('2d');
        liveSpectrum.width = liveSpectrum.offsetWidth || 400;
        liveSpectrum.height = 150;
    }
    
    const liveConfidence = document.getElementById('live-confidence-chart');
    if (liveConfidence) {
        liveConfidenceCtx = liveConfidence.getContext('2d');
        liveConfidence.width = liveConfidence.offsetWidth || 400;
        liveConfidence.height = 80;
    }
    
    const liveGas = document.getElementById('live-gas-chart');
    if (liveGas) {
        liveGasCtx = liveGas.getContext('2d');
        liveGas.width = liveGas.offsetWidth || 400;
        liveGas.height = 80;
    }
    
    const liveVibration = document.getElementById('live-vibration-chart');
    if (liveVibration) {
        liveVibrationCtx = liveVibration.getContext('2d');
        liveVibration.width = liveVibration.offsetWidth || 400;
        liveVibration.height = 80;
    }
}

function generateCalibrationButtons() {
    const container = document.getElementById('calibration-machines');
    const machines = ['machine_1', 'machine_2', 'machine_3'];
    
    container.innerHTML = machines.map(m => `
        <button class="listen-btn" id="listen-${m}" onclick="startListeningForMachine('${m}')" data-machine="${m}">
            <span class="machine-name">${m.replace('_', ' ').toUpperCase()}</span>
            <span class="profile-status" id="status-${m}">No profile</span>
        </button>
    `).join('');
}

// ==========================================
// AUDIO INITIALIZATION
// ==========================================
async function initAudio() {
    try {
        // Check if already initialized and running
        if (audioContext && audioContext.state === 'running') {
            console.log("✅ Audio already initialized");
            return true;
        }
        
        // Check if getUserMedia is supported
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            updateStatus('calibration', '❌ Your browser does not support microphone access. Try Chrome or Firefox.', 'error');
            return false;
        }
        
        console.log("🎤 Requesting microphone permission...");
        updateStatus('calibration', '🎤 Requesting microphone permission...', 'info');
        
        // Request microphone FIRST before creating AudioContext
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { 
                echoCancellation: false, 
                noiseSuppression: false, 
                autoGainControl: false 
            }
        });
        
        console.log("✅ Microphone permission granted");
        
        // Create AudioContext after getting permission
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        
        // Resume AudioContext if suspended (required by some browsers)
        if (audioContext.state === 'suspended') {
            await audioContext.resume();
        }
        
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.8;
        analyser.minDecibels = -90;
        
        microphone = audioContext.createMediaStreamSource(stream);
        microphone.connect(analyser);
        
        console.log("✅ Audio fully initialized");
        return true;
    } catch (error) {
        console.error("❌ Audio init failed:", error);
        
        // Provide specific error messages
        let errorMsg = 'Microphone error: ';
        if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
            errorMsg = '❌ Microphone permission denied. Click the 🔒 icon in address bar → Allow microphone.';
        } else if (error.name === 'NotFoundError') {
            errorMsg = '❌ No microphone found. Please connect a microphone.';
        } else if (error.name === 'NotReadableError') {
            errorMsg = '❌ Microphone is in use by another app. Close other apps using the mic.';
        } else if (error.name === 'SecurityError') {
            errorMsg = '❌ Microphone blocked. Page must be served over HTTPS or localhost.';
        } else {
            errorMsg += error.message;
        }
        
        updateStatus('calibration', errorMsg, 'error');
        return false;
    }
}

// ==========================================
// CALIBRATION MODE (PER MACHINE)
// ==========================================
async function startListeningForMachine(machineId) {
    if (isCalibrating) {
        updateStatus('calibration', '⚠️ Already listening! Stop first.', 'error');
        return;
    }
    
    const ok = await initAudio();
    if (!ok) return;
    
    selectedMachine = machineId;
    isCalibrating = true;
    calibrationData = [];
    calibrationFrameCount = 0;
    calibrationTimer = 0;
    calibrationVibrationSamples = [];
    calibrationGasSamples = [];
    amplitudeHistory = [];  // Reset amplitude history
    
    // Update UI
    document.getElementById('cal-mode-badge').textContent = 'LISTENING';
    document.getElementById('cal-mode-badge').className = 'mode-badge listening';
    document.getElementById('cal-active-machine').textContent = machineId.replace('_', ' ').toUpperCase();
    document.getElementById('calibration-control-panel').style.display = 'block';
    document.getElementById('calibration-waveform-container').style.display = 'block';
    document.getElementById('calibration-save').disabled = true;
    
    // Highlight active button
    document.querySelectorAll('.listen-btn').forEach(btn => {
        btn.classList.remove('listening');
        btn.disabled = true;
    });
    document.getElementById('listen-' + machineId).classList.add('listening');
    
    updateStatus('calibration', `🎤 Listening for ${machineId.replace('_', ' ').toUpperCase()}... Make steady sounds!`);
    
    // Start ESP32 polling during calibration
    startCalibrationESP32Polling();
    
    calibrationLoop();
}

function calibrationLoop() {
    if (!isCalibrating) return;
    
    const timeDomainData = new Uint8Array(analyser.frequencyBinCount);
    const frequencyData = new Uint8Array(analyser.frequencyBinCount);
    
    analyser.getByteTimeDomainData(timeDomainData);
    analyser.getByteFrequencyData(frequencyData);
    
    let sum = 0;
    for (let i = 0; i < timeDomainData.length; i++) {
        const val = (timeDomainData[i] - 128) / 128;
        sum += val * val;
    }
    const amplitude = Math.sqrt(sum / timeDomainData.length) * 100;
    
    let maxVal = 0, maxIndex = 0;
    for (let i = 1; i < frequencyData.length; i++) {
        if (frequencyData[i] > maxVal) {
            maxVal = frequencyData[i];
            maxIndex = i;
        }
    }
    
    const nyquist = audioContext.sampleRate / 2;
    const dominantFreq = (maxIndex / frequencyData.length) * nyquist;
    const freqConfidence = maxVal / 255;
    
    const peaks = extractTopPeaks(frequencyData, nyquist, 5);
    
    calibrationFrameCount++;
    
    calibrationData.push({
        amplitude,
        peaks: peaks,
        timestamp: Date.now()
    });
    
    // Track amplitude history for charts
    amplitudeHistory.push(amplitude);
    if (amplitudeHistory.length > 60) amplitudeHistory.shift();
    
    // Calculate average amplitude
    const avgAmp = amplitudeHistory.reduce((a, b) => a + b, 0) / amplitudeHistory.length;
    
    // Update UI stats
    document.getElementById('cal-duration').textContent = `${Math.floor(calibrationTimer)}s / ${CALIBRATION_DURATION}s`;
    document.getElementById('cal-frames').textContent = calibrationFrameCount;
    document.getElementById('cal-vib-count').textContent = calibrationVibrationSamples.length;
    document.getElementById('cal-gas-count').textContent = calibrationGasSamples.length;
    document.getElementById('calibration-timer').textContent = `${Math.floor(calibrationTimer)}s`;
    document.getElementById('cal-avg-amp').textContent = avgAmp.toFixed(1);
    document.getElementById('cal-peak-freq').textContent = Math.round(dominantFreq);
    document.getElementById('cal-time-remain').textContent = Math.max(0, CALIBRATION_DURATION - Math.floor(calibrationTimer)) + 's';
    
    // Update progress bars
    const progress = (calibrationTimer / CALIBRATION_DURATION) * 100;
    document.getElementById('cal-frames-progress').style.width = progress + '%';
    document.getElementById('cal-vib-progress').style.width = progress + '%';
    document.getElementById('cal-gas-progress').style.width = progress + '%';
    
    // Draw visualizations
    drawWaveform(timeDomainData, calibrationCanvasCtx);
    if (calibrationSpectrumCtx) drawSpectrum(frequencyData, calibrationSpectrumCtx, nyquist);
    if (calibrationHistoryCtx) drawHistory(amplitudeHistory, calibrationHistoryCtx, 'Amplitude');
    
    calibrationTimer += 0.1;
    
    if (calibrationTimer >= CALIBRATION_DURATION) {
        stopCalibration();
    } else {
        setTimeout(calibrationLoop, 100);
    }
}

function startCalibrationESP32Polling() {
    if (calibrationESP32PollTimer) return;
    
    calibrationESP32PollTimer = setInterval(async () => {
        try {
            const res = await fetch(BACKEND_URL + '/latest_esp32');
            const data = await res.json();
            console.log('ESP32 poll received:', data);
            
            if (data.vibration !== undefined) {
                calibrationVibrationSamples.push(data.vibration);
                document.getElementById('cal-vib-count').textContent = calibrationVibrationSamples.length;
                console.log('Vibration samples now:', calibrationVibrationSamples.length);
            }
            if (data.gas_raw !== undefined) {
                calibrationGasSamples.push({ raw: data.gas_raw, status: data.gas_status });
                document.getElementById('cal-gas-count').textContent = calibrationGasSamples.length;
                console.log('Gas samples now:', calibrationGasSamples.length);
            }
        } catch (e) {
            console.warn('ESP32 poll error:', e);
        }
    }, 500);
}

function stopCalibrationESP32Polling() {
    if (calibrationESP32PollTimer) {
        clearInterval(calibrationESP32PollTimer);
        calibrationESP32PollTimer = null;
    }
}

async function stopCalibration() {
    isCalibrating = false;
    stopCalibrationESP32Polling();
    
    // Update UI
    document.getElementById('cal-mode-badge').textContent = 'IDLE';
    document.getElementById('cal-mode-badge').className = 'mode-badge idle';
    document.querySelectorAll('.listen-btn').forEach(btn => {
        btn.classList.remove('listening');
        btn.disabled = false;
    });
    
    if (calibrationFrameCount > 0) {
        updateStatus('calibration', `📤 Uploading ${calibrationData.length} frames...`, 'info');
        
        try {
            const response = await fetch(BACKEND_URL + '/ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    frames: calibrationData,
                    machine_id: selectedMachine,
                    mode: 'calibration',
                    store_all: true,
                    frames_captured: calibrationData.length
                })
            });
            
            const result = await response.json();
            
            if (result.status === 'calibration_batch_saved') {
                updateStatus('calibration', `✅ Uploaded! ${result.frames_inserted} frames. Click Save Profile.`, 'success');
                document.getElementById('calibration-save').disabled = false;
            } else {
                updateStatus('calibration', `Error: ${result.error || 'Unknown'}`, 'error');
            }
        } catch (error) {
            updateStatus('calibration', `Upload error: ${error.message}`, 'error');
        }
    } else {
        updateStatus('calibration', '⚠️ No frames captured. Try again.', 'error');
    }
}

async function saveProfile() {
    if (calibrationFrameCount < 10) {
        updateStatus('calibration', 'Need at least 10 frames!', 'error');
        return;
    }
    
    // Debug logging
    console.log('saveProfile called');
    console.log('calibrationVibrationSamples:', calibrationVibrationSamples);
    console.log('calibrationGasSamples:', calibrationGasSamples);
    
    try {
        const payload = { 
            machine_id: selectedMachine,
            vibration_samples: calibrationVibrationSamples,
            gas_samples: calibrationGasSamples
        };
        console.log('Sending payload:', JSON.stringify(payload));
        
        const response = await fetch(BACKEND_URL + '/save_profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const result = await response.json();
        
        if (result.status === 'profile_saved') {
            updateStatus('calibration', `✅ Profile saved! Median: ${result.median_freq} Hz, ${result.bands_count} bands`, 'success');
            document.getElementById('calibration-save').disabled = true;
            loadProfiles();
            
            // Update button status
            const statusEl = document.getElementById('status-' + selectedMachine);
            if (statusEl) {
                statusEl.textContent = '✓ Profile saved';
            }
            document.getElementById('listen-' + selectedMachine).classList.add('has-profile');
        } else {
            updateStatus('calibration', `Error: ${result.error}`, 'error');
        }
    } catch (error) {
        updateStatus('calibration', `Error: ${error.message}`, 'error');
    }
}

// ==========================================
// RUN CHECK MODE (One-time check)
// ==========================================
async function runCheck() {
    const ok = await initAudio();
    if (!ok) return;
    
    isRunningCheck = true;
    runCheckBatch = [];
    
    document.getElementById('runcheck-btn').disabled = true;
    document.getElementById('runcheck-stop').disabled = false;
    document.getElementById('runcheck-progress').style.display = 'block';
    document.getElementById('runcheck-results').style.display = 'none';
    
    updateStatus('runcheck', '🔎 Analyzing audio for 3 seconds...', 'info');
    document.getElementById('runcheck-progress-text').textContent = 'Collecting samples...';
    
    // Collect for 3 seconds
    let checkDuration = 0;
    const checkLoop = () => {
        if (!isRunningCheck || checkDuration >= 3) {
            finishRunCheck();
            return;
        }
        
        const frequencyData = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(frequencyData);
        
        const nyquist = audioContext.sampleRate / 2;
        const peaks = extractTopPeaks(frequencyData, nyquist, 5);
        
        if (peaks.length > 0) {
            runCheckBatch.push({ peaks, timestamp: Date.now() });
        }
        
        checkDuration += 0.1;
        document.getElementById('runcheck-progress-text').textContent = `Collecting... ${checkDuration.toFixed(1)}s`;
        setTimeout(checkLoop, 100);
    };
    
    checkLoop();
}

function stopRunCheck() {
    isRunningCheck = false;
}

async function finishRunCheck() {
    isRunningCheck = false;
    document.getElementById('runcheck-btn').disabled = false;
    document.getElementById('runcheck-stop').disabled = true;
    
    if (runCheckBatch.length === 0) {
        updateStatus('runcheck', '⚠️ No audio detected. Make some sound!', 'error');
        document.getElementById('runcheck-progress').style.display = 'none';
        return;
    }
    
    document.getElementById('runcheck-progress-text').textContent = 'Analyzing against profiles...';
    
    try {
        // Send batch to backend for detection
        const response = await fetch(BACKEND_URL + '/ingest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frames: runCheckBatch.map(b => ({
                    amplitude: 1,
                    peaks: b.peaks,
                    timestamp: b.timestamp
                })),
                mode: 'live'
            })
        });
        
        const result = await response.json();
        
        // Display results
        document.getElementById('runcheck-progress').style.display = 'none';
        document.getElementById('runcheck-results').style.display = 'block';
        
        const detectedRaw = result.running_machines_raw || [];
        const detectedStable = result.running_machines || [];
        const allMachines = result.all_machines || [];
        
        let html = '';
        if (allMachines.length === 0) {
            html = '<p style="color: #888;">No machine profiles found. Calibrate first!</p>';
        } else {
            allMachines.forEach(m => {
                const isMatch = detectedRaw.includes(m);
                html += `
                    <div class="check-result-item">
                        <span>${m.replace('_', ' ').toUpperCase()}</span>
                        <span class="${isMatch ? 'match' : 'no-match'}">
                            ${isMatch ? '✓ MATCH' : '✗ No match'}
                        </span>
                    </div>
                `;
            });
        }
        
        document.getElementById('runcheck-results-list').innerHTML = html;
        
        if (detectedRaw.length > 0) {
            updateStatus('runcheck', `✅ Detected: ${detectedRaw.join(', ')}`, 'success');
        } else {
            updateStatus('runcheck', 'No machines matched current audio.', 'info');
        }
        
    } catch (error) {
        updateStatus('runcheck', `Error: ${error.message}`, 'error');
        document.getElementById('runcheck-progress').style.display = 'none';
    }
}

// ==========================================
// PROFILES
// ==========================================
async function loadProfiles() {
    try {
        const response = await fetch(BACKEND_URL + '/profiles');
        const profiles = await response.json();
        
        // Update cache
        machineProfiles = {};
        profiles.forEach(p => { machineProfiles[p.machine_id] = p; });
        
        // Update calibration button statuses
        ['machine_1', 'machine_2', 'machine_3'].forEach(m => {
            const btn = document.getElementById('listen-' + m);
            const status = document.getElementById('status-' + m);
            if (btn && status) {
                if (machineProfiles[m]) {
                    status.textContent = '✓ Profile saved';
                    btn.classList.add('has-profile');
                } else {
                    status.textContent = 'No profile';
                    btn.classList.remove('has-profile');
                }
            }
        });
        
        // Update profiles list
        const container = document.getElementById('profiles-list');
        
        if (profiles.length === 0) {
            container.innerHTML = '<p style="color: #888;">No profiles trained yet.</p>';
            return;
        }
        
        container.innerHTML = profiles.map(p => `
            <div class="profile-card">
                <div class="profile-header">
                    <span>${p.machine_id.replace('_', ' ').toUpperCase()}</span>
                    <button class="delete-btn" onclick="deleteProfile('${p.machine_id}')">🗑️ Delete</button>
                </div>
                
                <!-- Audio Section -->
                <div style="margin-top: 10px; padding: 10px; background: #0f1419; border-radius: 5px; border-left: 3px solid #3b82f6;">
                    <div style="color: #3b82f6; font-weight: bold; margin-bottom: 8px; font-size: 13px;">🔊 Audio Signature</div>
                    <div class="profile-detail">
                        <span>Median Frequency:</span>
                        <strong>${p.median_freq} Hz</strong>
                    </div>
                    <div class="profile-detail">
                        <span>Overall IQR Range:</span>
                        <strong>${p.iqr_low} – ${p.iqr_high} Hz</strong>
                    </div>
                    <div class="profile-detail">
                        <span>Frequency Bands:</span>
                        <strong>${p.bands_count || 0} bands</strong>
                    </div>
                    ${p.freq_bands && p.freq_bands.length > 0 ? `
                        <div style="margin-top: 8px; padding: 8px; background: #1a2332; border-radius: 4px;">
                            <div style="color: #888; margin-bottom: 5px; font-size: 11px;">Detected Harmonic Bands:</div>
                            ${p.freq_bands.map((b, i) => `
                                <div style="font-size: 12px; color: #22c55e; padding: 2px 0;">
                                    Band ${i+1}: ${b.low.toFixed(0)} – ${b.high.toFixed(0)} Hz (${b.samples} samples)
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
                
                <!-- Vibration Section -->
                <div style="margin-top: 10px; padding: 10px; background: #0f1419; border-radius: 5px; border-left: 3px solid #f59e0b;">
                    <div style="color: #f59e0b; font-weight: bold; margin-bottom: 8px; font-size: 13px;">📳 Vibration Pattern</div>
                    ${p.vibration_data ? `
                        <div class="profile-detail">
                            <span>Samples Collected:</span>
                            <strong>${p.vibration_data.samples}</strong>
                        </div>
                        <div class="profile-detail">
                            <span>Vibration Detected:</span>
                            <strong style="color: ${p.vibration_data.has_vibration ? '#22c55e' : '#888'};">
                                ${p.vibration_data.vibration_percent}% of samples
                            </strong>
                        </div>
                        <div class="profile-detail">
                            <span>Status:</span>
                            <strong style="color: ${p.vibration_data.has_vibration ? '#22c55e' : '#888'};">
                                ${p.vibration_data.has_vibration ? '✓ Machine Vibrates' : '✗ No Vibration'}
                            </strong>
                        </div>
                    ` : `
                        <div style="color: #666; font-size: 12px;">No vibration data collected</div>
                    `}
                </div>
                
                <!-- Gas/Air Quality Section -->
                <div style="margin-top: 10px; padding: 10px; background: #0f1419; border-radius: 5px; border-left: 3px solid ${
                    p.gas_data ? (p.gas_data.status === 'SAFE' ? '#22c55e' : p.gas_data.status === 'MODERATE' ? '#f59e0b' : p.gas_data.status === 'NO_DATA' ? '#666' : '#ef4444') : '#666'
                };">
                    <div style="color: ${
                        p.gas_data ? (p.gas_data.status === 'SAFE' ? '#22c55e' : p.gas_data.status === 'MODERATE' ? '#f59e0b' : p.gas_data.status === 'NO_DATA' ? '#666' : '#ef4444') : '#666'
                    }; font-weight: bold; margin-bottom: 8px; font-size: 13px;">💨 Air Quality</div>
                    ${p.gas_data ? `
                        <div class="profile-detail">
                            <span>Samples Collected:</span>
                            <strong>${p.gas_data.valid_samples || p.gas_data.samples} / ${p.gas_data.samples}</strong>
                        </div>
                        <div class="profile-detail">
                            <span>Average Gas Level:</span>
                            <strong>${p.gas_data.avg_raw}</strong>
                        </div>
                        <div class="profile-detail">
                            <span>Range:</span>
                            <strong>${p.gas_data.min_raw} – ${p.gas_data.max_raw}</strong>
                        </div>
                        <div class="profile-detail">
                            <span>Status:</span>
                            <strong style="color: ${p.gas_data.status === 'SAFE' ? '#22c55e' : p.gas_data.status === 'MODERATE' ? '#f59e0b' : p.gas_data.status === 'NO_DATA' ? '#666' : '#ef4444'};">
                                ${p.gas_data.status === 'SAFE' ? '✓ Safe (<800)' : p.gas_data.status === 'MODERATE' ? '⚠️ Moderate (800-2000)' : p.gas_data.status === 'NO_DATA' ? '❓ No valid readings' : '🚨 Hazardous (>2000)'}
                            </strong>
                        </div>
                    ` : `
                        <div style="color: #666; font-size: 12px;">No gas data collected</div>
                    `}
                </div>
                
                <div style="margin-top: 10px; font-size: 11px; color: #666;">
                    Created: ${p.created_at || 'Unknown'}
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading profiles:', error);
    }
}

async function deleteProfile(machineId) {
    if (!confirm(`Delete profile for ${machineId.replace('_', ' ').toUpperCase()}?`)) {
        return;
    }
    
    try {
        const response = await fetch(BACKEND_URL + '/delete_profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ machine_id: machineId })
        });
        
        const result = await response.json();
        
        if (result.status === 'profile_deleted') {
            alert(`✅ Profile deleted: ${machineId}`);
            loadProfiles();  // Refresh list
        } else {
            alert(`❌ Error: ${result.error}`);
        }
    } catch (error) {
        alert(`Error deleting profile: ${error.message}`);
    }
}

// ==========================================
// HELPERS & CHART DRAWING
// ==========================================
function drawWaveform(data, ctx) {
    if (!ctx) return;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    
    // Draw gradient waveform
    const gradient = ctx.createLinearGradient(0, 0, 0, ctx.canvas.height);
    gradient.addColorStop(0, '#22c55e');
    gradient.addColorStop(0.5, '#3b82f6');
    gradient.addColorStop(1, '#a855f7');
    
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    for (let i = 0; i < data.length; i++) {
        const x = (i / data.length) * ctx.canvas.width;
        const y = ctx.canvas.height - ((data[i] / 255) * ctx.canvas.height);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
    
    // Draw center line
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, ctx.canvas.height / 2);
    ctx.lineTo(ctx.canvas.width, ctx.canvas.height / 2);
    ctx.stroke();
}

function drawSpectrum(frequencyData, ctx, nyquist) {
    if (!ctx) return;
    const width = ctx.canvas.width;
    const height = ctx.canvas.height;
    
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    
    // Draw bars with gradient
    const barWidth = width / frequencyData.length;
    const gradient = ctx.createLinearGradient(0, height, 0, 0);
    gradient.addColorStop(0, '#22c55e');
    gradient.addColorStop(0.5, '#3b82f6');
    gradient.addColorStop(1, '#ef4444');
    
    for (let i = 0; i < frequencyData.length; i++) {
        const barHeight = (frequencyData[i] / 255) * height;
        const x = i * barWidth;
        
        ctx.fillStyle = gradient;
        ctx.fillRect(x, height - barHeight, barWidth - 1, barHeight);
    }
    
    // Draw frequency labels
    ctx.fillStyle = '#666';
    ctx.font = '10px monospace';
    ctx.textAlign = 'left';
    const freqStep = Math.floor(nyquist / 4);
    for (let i = 0; i <= 4; i++) {
        const freq = i * freqStep;
        const x = (i / 4) * width;
        ctx.fillText(freq + 'Hz', x + 5, height - 5);
    }
}

function drawHistory(dataArray, ctx, label = '') {
    if (!ctx || dataArray.length === 0) return;
    const width = ctx.canvas.width;
    const height = ctx.canvas.height;
    
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    
    // Find max for scaling
    const max = Math.max(...dataArray, 1);
    
    // Draw gradient line
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, '#3b82f6');
    gradient.addColorStop(1, '#22c55e');
    
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    for (let i = 0; i < dataArray.length; i++) {
        const x = (i / (dataArray.length - 1)) * width;
        const y = height - ((dataArray[i] / max) * height * 0.9);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
    
    // Draw filled area
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    const fillGradient = ctx.createLinearGradient(0, 0, 0, height);
    fillGradient.addColorStop(0, 'rgba(34, 197, 94, 0.3)');
    fillGradient.addColorStop(1, 'rgba(34, 197, 94, 0.05)');
    ctx.fillStyle = fillGradient;
    ctx.fill();
    
    // Draw grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = (i / 4) * height;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }
    
    // Draw max value label
    ctx.fillStyle = '#666';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    ctx.fillText('Max: ' + max.toFixed(1), width - 5, 12);
}

function drawMiniLineChart(dataArray, ctx, color = '#22c55e', maxValue = null) {
    if (!ctx || dataArray.length === 0) return;
    const width = ctx.canvas.width;
    const height = ctx.canvas.height;
    
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    
    const max = maxValue || Math.max(...dataArray, 1);
    
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    for (let i = 0; i < dataArray.length; i++) {
        const x = (i / Math.max(dataArray.length - 1, 1)) * width;
        const y = height - ((dataArray[i] / max) * height * 0.9) - height * 0.05;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
}

// ==========================================
// PEAK EXTRACTION (NEW - CRITICAL FOR MULTI-MACHINE)
// ==========================================
/**
 * Extract top N peaks from frequency data.
 * Uses local maxima detection with minimum separation.
 * @param {Uint8Array} frequencyData - FFT frequency bins
 * @param {number} nyquist - Nyquist frequency (sampleRate/2)
 * @param {number} numPeaks - Number of peaks to extract (default 5)
 * @returns {Array} Array of {freq, amp} objects sorted by amplitude (descending)
 */
function extractTopPeaks(frequencyData, nyquist, numPeaks = 5) {
    const peaks = [];
    const minSeparation = 5; // Minimum bin separation between peaks
    const minAmplitude = 10; // Minimum raw FFT value to consider (0-255) - lowered to capture quieter sounds
    
    // Find all local maxima
    for (let i = 2; i < frequencyData.length - 2; i++) {
        const val = frequencyData[i];
        
        // Skip if below minimum amplitude
        if (val < minAmplitude) continue;
        
        // Check if local maximum (higher than neighbors)
        if (val > frequencyData[i-1] && 
            val > frequencyData[i+1] &&
            val >= frequencyData[i-2] && 
            val >= frequencyData[i+2]) {
            
            const freq = (i / frequencyData.length) * nyquist;
            const amp = val / 255; // Normalize to 0-1
            
            peaks.push({ freq, amp, bin: i });
        }
    }
    
    // Sort by amplitude (descending)
    peaks.sort((a, b) => b.amp - a.amp);
    
    // Select top peaks with minimum separation
    const selectedPeaks = [];
    for (const peak of peaks) {
        // Check if far enough from already selected peaks
        let tooClose = false;
        for (const selected of selectedPeaks) {
            if (Math.abs(peak.bin - selected.bin) < minSeparation) {
                tooClose = true;
                break;
            }
        }
        
        if (!tooClose) {
            selectedPeaks.push({ freq: peak.freq, amp: peak.amp });
            if (selectedPeaks.length >= numPeaks) break;
        }
    }
    
    return selectedPeaks;
}

function switchTab(event, tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tab).classList.add('active');
    event.target.classList.add('active');
    
    // Initialize canvas when switching to live detection
    if (tab === 'livedetection') {
        setTimeout(initializeCanvases, 100);
    }
}

function updateStatus(tab, message, type = 'info') {
    const el = document.getElementById(tab + '-status');
    if (el) {
        el.textContent = message;
        el.className = 'status ' + type;
    }
}

// ==========================================
// LIVE DETECTION MODE (Main Dashboard)
// ==========================================
async function startLiveDetection() {
    const ok = await initAudio();
    if (!ok) {
        updateStatus('livedetection', '❌ Microphone access required', 'error');
        return;
    }
    
    isLiveDetecting = true;
    liveBatch = [];
    liveBatchTimer = 0;
    
    // Reset tracking variables
    amplitudeHistory = [];
    frequencyHistory = [];
    gasHistory = [];
    vibrationHistory = [];
    confidenceHistory = [];
    liveStartTime = Date.now();
    liveSampleCount = 0;
    
    // Update UI
    document.getElementById('live-mode-badge').textContent = 'DETECTING';
    document.getElementById('live-mode-badge').className = 'mode-badge detecting';
    document.getElementById('live-start-btn').disabled = true;
    document.getElementById('live-stop-btn').disabled = false;
    
    updateStatus('livedetection', '🎤 Live detection running...', 'success');
    
    // Start ESP32 polling for live mode
    startLiveESP32Polling();
    
    // Start audio detection loop
    liveDetectionLoop();
}

function liveDetectionLoop() {
    if (!isLiveDetecting) return;
    
    const timeDomainData = new Uint8Array(analyser.frequencyBinCount);
    const frequencyData = new Uint8Array(analyser.frequencyBinCount);
    
    analyser.getByteTimeDomainData(timeDomainData);
    analyser.getByteFrequencyData(frequencyData);
    
    let sum = 0;
    for (let i = 0; i < timeDomainData.length; i++) {
        const val = (timeDomainData[i] - 128) / 128;
        sum += val * val;
    }
    const amplitude = Math.sqrt(sum / timeDomainData.length) * 100;
    
    let maxVal = 0, maxIndex = 0;
    for (let i = 1; i < frequencyData.length; i++) {
        if (frequencyData[i] > maxVal) {
            maxVal = frequencyData[i];
            maxIndex = i;
        }
    }
    
    const nyquist = audioContext.sampleRate / 2;
    const dominantFreq = (maxIndex / frequencyData.length) * nyquist;
    const freqConfidence = maxVal / 255;
    
    const peaks = extractTopPeaks(frequencyData, nyquist, 5);
    
    // Track history
    liveSampleCount++;
    amplitudeHistory.push(amplitude);
    if (amplitudeHistory.length > 60) amplitudeHistory.shift();
    
    frequencyHistory.push(dominantFreq);
    if (frequencyHistory.length > 60) frequencyHistory.shift();
    
    confidenceHistory.push(freqConfidence);
    if (confidenceHistory.length > 30) confidenceHistory.shift();
    
    // Calculate stats
    const peakAmp = Math.max(...amplitudeHistory);
    const avgAmp = amplitudeHistory.reduce((a, b) => a + b, 0) / amplitudeHistory.length;
    const runningTime = Math.floor((Date.now() - liveStartTime) / 1000);
    const sampleRate = Math.floor(liveSampleCount / Math.max(runningTime, 1));
    
    // Collect frame
    liveBatch.push({
        amplitude,
        peaks: peaks,
        dominant_freq: dominantFreq,
        freq_confidence: freqConfidence,
        timestamp: Date.now()
    });
    
    // Update audio UI
    document.getElementById('live-freq').textContent = dominantFreq.toFixed(1);
    document.getElementById('live-amp').textContent = amplitude.toFixed(2);
    document.getElementById('live-conf').textContent = freqConfidence.toFixed(3);
    document.getElementById('live-peak-amp').textContent = peakAmp.toFixed(1);
    document.getElementById('live-avg-amp').textContent = avgAmp.toFixed(1);
    document.getElementById('live-sample-rate').textContent = sampleRate;
    document.getElementById('live-runtime').textContent = runningTime + 's';
    
    // Draw visualizations
    if (liveCanvasCtx) drawWaveform(timeDomainData, liveCanvasCtx);
    if (liveSpectrumCtx) drawSpectrum(frequencyData, liveSpectrumCtx, nyquist);
    if (liveConfidenceCtx) drawMiniLineChart(confidenceHistory, liveConfidenceCtx, '#3b82f6', 1);
    
    // Send batch every 500ms
    liveBatchTimer += 0.1;
    if (liveBatchTimer >= 0.5) {
        if (liveBatch.length > 0) {
            sendLiveBatch();
        }
        liveBatch = [];
        liveBatchTimer = 0;
    }
    
    setTimeout(liveDetectionLoop, 100);
}

async function sendLiveBatch() {
    try {
        const response = await fetch(BACKEND_URL + '/ingest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frames: liveBatch,
                mode: 'live'
            })
        });
        
        const data = await response.json();
        
        // Update machine detection display
        const card = document.getElementById('live-machine-card');
        const nameEl = document.getElementById('live-detected-machine');
        const confEl = document.getElementById('live-detection-confidence');
        const stableEl = document.getElementById('live-stable-machines');
        
        const detected = data.running_machines_raw || [];
        const stable = data.running_machines || [];
        
        if (stable.length > 0) {
            nameEl.textContent = stable.map(m => m.replace('_', ' ').toUpperCase()).join(', ');
            confEl.textContent = 'Stable detection';
            card.className = 'detected-machine-card detected';
        } else if (detected.length > 0) {
            nameEl.textContent = detected.map(m => m.replace('_', ' ').toUpperCase()).join(', ');
            confEl.textContent = 'Detecting...';
            card.className = 'detected-machine-card';
        } else {
            nameEl.textContent = 'No machine detected';
            confEl.textContent = 'Listening...';
            card.className = 'detected-machine-card no-detection';
        }
        
        stableEl.textContent = stable.length > 0 ? stable.join(', ') : '--';
        
    } catch (err) {
        console.warn('Live batch error:', err);
    }
}

function startLiveESP32Polling() {
    if (liveESP32PollTimer) return;
    
    fetchLiveESP32Data();
    liveESP32PollTimer = setInterval(fetchLiveESP32Data, 1000);
}

function stopLiveESP32Polling() {
    if (liveESP32PollTimer) {
        clearInterval(liveESP32PollTimer);
        liveESP32PollTimer = null;
    }
}

async function fetchLiveESP32Data() {
    try {
        const res = await fetch(BACKEND_URL + '/latest_esp32');
        const data = await res.json();
        
        // Update vibration display
        const vibValue = data.vibration || 0;
        document.getElementById('live-vibration-value').textContent = vibValue > 0 ? 'ON' : 'OFF';
        
        const vibPercent = Math.min(vibValue * 100, 100);
        document.getElementById('live-vibration-bar').style.width = vibPercent + '%';
        
        const intensityEl = document.getElementById('live-vibration-intensity');
        if (vibValue > 0.7) {
            intensityEl.textContent = 'HIGH';
            intensityEl.className = 'vibration-intensity high';
        } else if (vibValue > 0.3) {
            intensityEl.textContent = 'MEDIUM';
            intensityEl.className = 'vibration-intensity medium';
        } else {
            intensityEl.textContent = 'LOW';
            intensityEl.className = 'vibration-intensity low';
        }
        
        document.getElementById('live-event-count').textContent = data.event_count || 0;
        
        // Track vibration history
        vibrationHistory.push(vibValue * 100);
        if (vibrationHistory.length > 30) vibrationHistory.shift();
        
        // Update gas display
        const gasRaw = data.gas_raw || 0;
        const gasStatus = data.gas_status || 'UNKNOWN';
        
        document.getElementById('live-gas-value').textContent = gasRaw;
        document.getElementById('live-gas-status-text').textContent = gasStatus;
        
        // Track gas history
        gasHistory.push(gasRaw);
        if (gasHistory.length > 30) gasHistory.shift();
        
        const gasIndicator = document.getElementById('live-gas-indicator');
        gasIndicator.className = 'gas-indicator';
        
        if (gasRaw > GAS_HAZARD_LIMIT || gasStatus === 'RISK' || gasStatus === 'DANGER') {
            gasIndicator.classList.add('hazardous');
        } else if (gasRaw > GAS_SAFE_LIMIT || gasStatus === 'WARNING') {
            gasIndicator.classList.add('warning');
        } else {
            gasIndicator.classList.add('safe');
        }
        
        // Update charts
        if (liveGasCtx) drawMiniLineChart(gasHistory, liveGasCtx, '#f59e0b', 1000);
        if (liveVibrationCtx) drawMiniLineChart(vibrationHistory, liveVibrationCtx, '#ef4444', 100);
        
    } catch (err) {
        console.warn('ESP32 fetch error:', err);
    }
}

function stopLiveDetection() {
    isLiveDetecting = false;
    liveBatch = [];
    liveBatchTimer = 0;
    
    stopLiveESP32Polling();
    
    document.getElementById('live-mode-badge').textContent = 'IDLE';
    document.getElementById('live-mode-badge').className = 'mode-badge idle';
    document.getElementById('live-start-btn').disabled = false;
    document.getElementById('live-stop-btn').disabled = true;
    
    updateStatus('livedetection', 'Detection stopped.', 'info');
}
