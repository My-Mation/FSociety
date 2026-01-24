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
let logTimer = null; // New Event Log Timer

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
window.addEventListener('load', function () {
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
        calibSpectrum.height = calibSpectrum.offsetHeight || 150;
    }

    const calibHistory = document.getElementById('calibration-history'); // Kept for safety if ID exists, but we moved to new charts

    // NEW Calibration Graphs
    CalibrationAudioGraph.init();
    CalibrationVibrationGraph.init();



    if (liveCanvas) {
        liveCanvasCtx = liveCanvas.getContext('2d');
        liveCanvas.width = liveCanvas.offsetWidth || 800;
        liveCanvas.height = liveCanvas.offsetHeight || 150;
    }

    // Live detection chart canvases
    const liveSpectrum = document.getElementById('live-spectrum-chart');
    if (liveSpectrum) {
        liveSpectrumCtx = liveSpectrum.getContext('2d');
        resizeCanvas(liveSpectrum);
    }

    const liveConfidence = document.getElementById('live-confidence-chart');
    if (liveConfidence) {
        liveConfidenceCtx = liveConfidence.getContext('2d');
        resizeCanvas(liveConfidence);
    }

    const liveGas = document.getElementById('live-gas-chart');
    if (liveGas) {
        liveGasCtx = liveGas.getContext('2d');
        resizeCanvas(liveGas);
    }

    const liveVibration = document.getElementById('live-vibration-chart');
    if (liveVibration) {
        liveVibrationCtx = liveVibration.getContext('2d');
        resizeCanvas(liveVibration);
    }

    // NEW: Gas Speedometer (Chart.js)
    initGasGauge();

    // Auto-start Environmental Monitoring (Gas/Vibration) immediately
    startLiveESP32Polling();

    // Resize listener
    window.addEventListener('resize', () => {
        resizeCanvas(document.getElementById('live-canvas'));
        resizeCanvas(liveSpectrum);
        resizeCanvas(liveConfidence);
        resizeCanvas(liveGas);
        resizeCanvas(liveVibration);
    });
}

function resizeCanvas(canvas) {
    if (!canvas) return;
    const parent = canvas.parentElement;
    canvas.width = parent.offsetWidth;
    canvas.height = parent.offsetHeight;
}

// Gas Gauge Chart Instance (Global)
let gasGaugeChart = null;

// Gas Gauge Needle Plugin (Enhanced for Industrial Look)
// Gas Gauge Needle Plugin (Enhanced for Industrial Look)
const gaugeNeedle = {
    id: 'gaugeNeedle',
    afterDraw(chart, args, plugins) {
        const { ctx, data } = chart;
        const xCenter = chart.getDatasetMeta(0).data[0].x;
        const yCenter = chart.getDatasetMeta(0).data[0].y;
        const outerRadius = chart.getDatasetMeta(0).data[0].outerRadius;
        const innerRadius = chart.getDatasetMeta(0).data[0].innerRadius;
        const widthSlice = (outerRadius - innerRadius) / 2;
        const radius = 5; // Smaller hub

        // Needle Angle Calculation (Total 4200 = 180 degrees)
        // Chart Rotation -90 deg (Top Arch)
        // 0 val = Left (-90 deg from Top?) -> Wait.
        // Chart.js: 0 deg = East (Right).
        // -90 deg = North (Top).
        // -180 deg = West (Left).
        // So Arc starts at -180 (Left) and goes to 0 (Right).
        // Needle at 0 should be -PI.
        // Needle at Max should be 0.

        const value = data.datasets[0].needleValue || 0;
        const max = 4200;
        const percentage = Math.min(Math.max(value / max, 0), 1);

        // Angle: Start at -PI (Left), End at 0 (Right).
        const angle = -Math.PI + (percentage * Math.PI);

        ctx.save();
        ctx.translate(xCenter, yCenter);
        ctx.rotate(angle);

        // Draw Needle
        ctx.beginPath();
        ctx.moveTo(0 - radius, 0); // Center Hub Left
        ctx.lineTo(0, 0 - outerRadius + 10); // Tip
        ctx.lineTo(0 + radius, 0); // Center Hub Right
        ctx.fillStyle = '#e0e0e0';
        ctx.fill();
        ctx.restore();

        // Draw Center Dot
        ctx.save();
        ctx.beginPath();
        ctx.arc(xCenter, yCenter, radius + 2, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.restore();

        // Draw Industrial Ticks & Labels
        ctx.save();
        ctx.font = 'bold 9px Inter';
        ctx.fillStyle = '#888';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        const ticks = [0, 500, 1000, 2000, 3000, 4200];

        ticks.forEach(tickVal => {
            const tickPct = tickVal / 4200;
            // Ticks match needle angle: -PI to 0
            const tickAngle = -Math.PI + (tickPct * Math.PI);

            // Text Position (Inside inner radius)
            const textRadius = innerRadius - 15;
            const tx = xCenter + Math.cos(tickAngle) * textRadius;
            const ty = yCenter + Math.sin(tickAngle) * textRadius;

            ctx.fillText(tickVal.toString(), tx, ty);

            // Tick Mark
            const markStart = innerRadius - 5;
            const markEnd = innerRadius;
            const mx1 = xCenter + Math.cos(tickAngle) * markStart;
            const my1 = yCenter + Math.sin(tickAngle) * markStart;
            const mx2 = xCenter + Math.cos(tickAngle) * markEnd;
            const my2 = yCenter + Math.sin(tickAngle) * markEnd;

            ctx.beginPath();
            ctx.moveTo(mx1, my1);
            ctx.lineTo(mx2, my2);
            ctx.strokeStyle = '#666';
            ctx.lineWidth = 1;
            ctx.stroke();
        });
        ctx.restore();
    }
};

function initGasGauge() {
    const ctx = document.getElementById('live-gas-gauge');
    if (!ctx) return;

    if (gasGaugeChart) {
        gasGaugeChart.destroy();
    }

    // Industrial Zones: Safe (0-300), Warn(300-700), Hazard(700-4200)
    // 4200 Total. 
    // Seg 1: 300
    // Seg 2: 400 (700-300)
    // Seg 3: 3500 (4200-700)

    gasGaugeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Safe', 'Warning', 'Hazard'],
            datasets: [{
                data: [300, 400, 3500],
                needleValue: 0,
                backgroundColor: [
                    '#15803d', // Dark Green (Safe)
                    '#a16207', // Dark Yellow (Warn)
                    '#991b1b'  // Dark Red (Haz)
                ],
                borderWidth: 1,
                borderColor: '#111',
                hoverOffset: 0,
                circumference: 180,
                rotation: -90, // Start at Left (West) - Chart.js 3/4 standard for semi-circle
                cutout: '75%'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false }
            },
            layout: { padding: 0 },
            animation: false
        },
        plugins: [gaugeNeedle]
    });
}

function updateGasGauge(value) {
    if (!gasGaugeChart) return;

    // Just update needle, zones stay static
    gasGaugeChart.data.datasets[0].needleValue = value;
    gasGaugeChart.update('none'); // No animation for prompt feeling
}

// ==========================================
// GAS HISTORY GRAPH (NEW MONDATORY)
// ==========================================


// ==========================================
// CALIBRATION GRAPHS (NEW)
// ==========================================
const CalibrationAudioGraph = {
    chart: null,
    data: [],
    labels: [],

    init() {
        const ctx = document.getElementById('calibration-audio-chart');
        if (!ctx) return;

        this.data = [];
        this.labels = [];

        if (this.chart) this.chart.destroy();

        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: this.labels,
                datasets: [{
                    label: 'Amplitude',
                    data: this.data,
                    borderColor: '#3b82f6',
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0,
                    fill: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        title: { display: true, text: 'Session Time (s)', color: '#666', font: { size: 10 } },
                        ticks: { color: '#666', maxTicksLimit: 10 },
                        grid: { color: '#333' }
                    },
                    y: {
                        min: 0, max: 20, // Match Live view scale logic
                        ticks: { color: '#666' },
                        grid: { color: '#333' }
                    }
                }
            }
        });
    },

    reset() {
        this.data = [];
        this.labels = [];
        if (this.chart) {
            this.chart.data.labels = this.labels;
            this.chart.data.datasets[0].data = this.data;
            this.chart.update();
        }
    },

    update(time, amplitude) {
        if (!this.chart) return;
        this.labels.push(time.toFixed(1));
        this.data.push(amplitude);
        this.chart.update('none');
    }
};

const CalibrationVibrationGraph = {
    chart: null,
    data: [],
    labels: [],

    init() {
        const ctx = document.getElementById('calibration-vibration-chart');
        if (!ctx) return;

        this.data = [];
        this.labels = [];

        if (this.chart) this.chart.destroy();

        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: this.labels,
                datasets: [{
                    label: 'Vibration (RMS)',
                    data: this.data,
                    borderColor: '#eab308', // Warning Yellow for Vib
                    backgroundColor: 'rgba(234, 179, 8, 0.1)',
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.1,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        title: { display: true, text: 'Session Time (s)', color: '#666', font: { size: 10 } },
                        ticks: { color: '#666', maxTicksLimit: 10 },
                        grid: { color: '#333' }
                    },
                    y: {
                        min: 0, max: 1.0, // Assumed G range
                        ticks: { color: '#666' },
                        grid: { color: '#333' }
                    }
                }
            }
        });
    },

    reset() {
        this.data = [];
        this.labels = [];
        if (this.chart) {
            this.chart.data.labels = this.labels;
            this.chart.data.datasets[0].data = this.data;
            this.chart.update();
        }
    },

    update(time, vibValue) {
        if (!this.chart) return;
        this.labels.push(time.toFixed(1));
        this.data.push(vibValue);
        this.chart.update('none');
    }
};

// ==========================================
// AUDIO LEVEL HISTORY MONITOR (CLEAN IMPLEMENTATION)
// ==========================================
const AudioHistoryGraph = {
    chart: null,
    data: [],
    labels: [],
    startTime: 0,

    init() {
        const ctx = document.getElementById('all-time-audio-chart');
        if (!ctx) return;

        // Reset
        this.data = [];
        this.labels = [];
        this.startTime = 0;

        if (this.chart) {
            this.chart.destroy();
        }

        // Configure Chart
        // Configure Chart
        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: this.labels,
                datasets: [{
                    label: 'Audio Level',
                    data: this.data,
                    borderColor: '#3b82f6',
                    borderWidth: 2,
                    pointRadius: 0, // Keep points hidden but allow hover
                    pointHitRadius: 10, // Easier to hover
                    tension: 0, // Straight lines for performance
                    fill: false,
                    spanGaps: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                layout: {
                    padding: { left: 10, right: 10, bottom: 10 }
                },
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    title: { display: false },
                    tooltip: {
                        enabled: true,
                        backgroundColor: 'rgba(0, 0, 0, 0.9)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        callbacks: {
                            label: function (context) {
                                return `Level: ${context.parsed.y.toFixed(1)}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        display: true,
                        title: { display: true, text: 'Time (s)', color: '#aaa', font: { size: 10 } },
                        grid: {
                            color: '#333',
                            drawTicks: true
                        },
                        ticks: {
                            color: '#aaa',
                            autoSkip: true,
                            maxTicksLimit: 10,
                            maxRotation: 0,
                            font: { size: 10 }
                        }
                    },
                    y: {
                        display: true,
                        min: 0,
                        max: 20,
                        beginAtZero: true,
                        grid: { color: '#333' },
                        title: { display: true, text: 'Level', color: '#aaa', font: { size: 10 } },
                        ticks: {
                            color: '#aaa',
                            font: { size: 10 },
                            stepSize: 4 // Force 0, 4, 8, 12, 16, 20
                        }
                    }
                }
            }
        });
    },

    start() {
        this.startTime = Date.now();
        this.data = [];
        this.labels = [];
        if (this.chart) {
            this.chart.data.labels = this.labels;
            this.chart.data.datasets[0].data = this.data;
            this.chart.update();
        }
    },

    update(level) {
        if (!this.chart || this.startTime === 0) return;

        // Determine X-axis (Seconds relative to start)
        const seconds = ((Date.now() - this.startTime) / 1000).toFixed(1);

        // Push Data
        this.labels.push(seconds);
        this.data.push(Math.max(0.5, level)); // Minimum 0.5 visibility

        // Batch Update logic could be added here, but direct update 'none' is usually fine for <10Hz
        this.chart.update('none');
    }
};

// Hook into existing init
/* 
    Requires: 
    1. AudioHistoryGraph.init() on load/resize 
    2. AudioHistoryGraph.start() on detection start
    3. AudioHistoryGraph.update(val) in loop
*/


// Replaced by AudioHistoryGraph.update()


// ... (Existing Functions) ...

// Updated SendLiveBatch with Semantic logic
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

        const nameEl = document.getElementById('live-detected-machine');
        const confEl = document.getElementById('live-detection-confidence');
        const stableEl = document.getElementById('live-stable-machines');

        const detected = data.running_machines_raw || [];
        const stable = data.running_machines || [];

        const dominantFreq = frequencyHistory[frequencyHistory.length - 1] || 0;
        const fallback = getClosestMachine(dominantFreq);

        if (stable.length > 0) {
            nameEl.textContent = stable.map(m => m.replace('_', ' ').toUpperCase()).join(', ');
            confEl.textContent = 'LOCKED (99%)';
            updateStateHistory('LOCKED', '99%', 'AI_MATCH');

        } else if (detected.length > 0) {
            nameEl.textContent = detected.map(m => m.replace('_', ' ').toUpperCase()).join(', ');
            confEl.textContent = 'DETECTING...';
            updateStateHistory('DETECTING', '60%', 'THRESHOLD');

        } else if (fallback) {
            // SEMANTIC FIX: "Machine 1?" -> "Machine 1 (Slightly Different)"
            const machineName = fallback.machine.replace('_', ' ').toUpperCase();
            nameEl.textContent = `${machineName} (SLIGHTLY DIFFERENT)`;
            nameEl.style.color = '#eab308'; // Warning Yellow

            const confPercent = (fallback.confidence * 100).toFixed(0);
            confEl.textContent = `${confPercent}%`; // Just percentage, no text

            updateStateHistory('UNCERTAIN', `${confPercent}%`, 'HEURISTIC');

        } else {
            nameEl.textContent = 'NO SIGNAL MATCH';
            confEl.textContent = 'IDLE';
            nameEl.style.color = '#666';
        }

        stableEl.textContent = stable.length > 0 ? stable.length : '0';

    } catch (err) {
        console.warn('Live batch error:', err);
    }
}

// ...


// Duplicate liveDetectionLoop removed


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
    calibrationGasSamples = [];
    amplitudeHistory = [];  // Reset amplitude history

    // Reset Graphs
    CalibrationAudioGraph.reset();
    CalibrationVibrationGraph.reset();

    // Update UI
    document.getElementById('cal-mode-badge').textContent = 'LISTENING';
    document.getElementById('cal-mode-badge').className = 'mode-badge listening';
    document.getElementById('cal-active-machine').textContent = machineId.replace('_', ' ').toUpperCase();
    document.getElementById('calibration-control-panel').style.display = 'block';
    document.getElementById('calibration-waveform-container').style.display = 'flex';
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
    document.getElementById('cal-current-amp').textContent = amplitude.toFixed(2);
    document.getElementById('cal-current-freq').textContent = dominantFreq.toFixed(2) + ' Hz';
    document.getElementById('cal-time-remain').textContent = Math.max(0, CALIBRATION_DURATION - Math.floor(calibrationTimer)) + 's';

    // Update progress bars
    const progress = (calibrationTimer / CALIBRATION_DURATION) * 100;
    document.getElementById('cal-frames-progress').style.width = progress + '%';
    document.getElementById('cal-vib-progress').style.width = progress + '%';
    document.getElementById('cal-gas-progress').style.width = progress + '%';

    // Draw visualizations
    drawWaveform(timeDomainData, calibrationCanvasCtx);
    if (calibrationSpectrumCtx) drawSpectrum(frequencyData, calibrationSpectrumCtx, nyquist);
    // Draw visualizations
    drawWaveform(timeDomainData, calibrationCanvasCtx);
    if (calibrationSpectrumCtx) drawSpectrum(frequencyData, calibrationSpectrumCtx, nyquist);

    // Update New Graphs
    CalibrationAudioGraph.update(calibrationTimer, amplitude);

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

                // Update Graph
                // Use calibrationTimer which is roughly synced
                CalibrationVibrationGraph.update(calibrationTimer, data.vibration);
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
            container.innerHTML = '<p style="color: #666; text-align: center; padding: 40px; font-style: italic;">No profiles trained yet. Go to Calibration Tab to add machines.</p>';
            return;
        }

        container.innerHTML = profiles.map(p => {
            // Calculate extra metrics for display
            const vibPercent = p.vibration_data ? p.vibration_data.vibration_percent : 0;
            const vibColor = vibPercent > 50 ? '#dc2626' : vibPercent > 20 ? '#eab308' : '#3b82f6';

            const gasStatus = p.gas_data ? p.gas_data.status : 'NO_DATA';
            const gasColorClass = gasStatus === 'SAFE' ? 'status-safe' :
                gasStatus === 'MODERATE' ? 'status-moderate' :
                    gasStatus === 'HAZARDOUS' ? 'status-hazard' : 'status-nodata';

            const gasLabel = gasStatus === 'SAFE' ? 'Safe' :
                gasStatus === 'MODERATE' ? 'Moderate' :
                    gasStatus === 'HAZARDOUS' ? 'Hazardous' : 'No Data';

            return `
            <div class="industrial-card">
                <!-- Section A: Header -->
                <div class="profile-main-header">
                    <div class="profile-name">
                        ${p.machine_id.replace('_', ' ').toUpperCase()}
                        <span class="profile-status-badge">ACTIVE</span>
                    </div>
                     <button class="delete-btn" onclick="deleteProfile('${p.machine_id}')">
                        <span style="font-size: 14px; margin-right: 5px;">🗑️</span> DELETE
                    </button>
                </div>

                <div class="profile-grid">
                    <!-- Left Column: Audio Data (70%) -->
                    <div class="profile-col-left">
                        <!-- Section B: Audio Signature -->
                        <div class="sub-card">
                            <div class="sub-card-header">Audio Signature Analysis</div>
                            <div class="sub-card-body">
                                <table class="metric-table">
                                    <tr>
                                        <td class="metric-label">Median Frequency</td>
                                        <td class="metric-value">${p.median_freq} <span class="metric-unit">Hz</span></td>
                                    </tr>
                                    <tr>
                                        <td class="metric-label">IQR Range (Spread)</td>
                                        <td class="metric-value">${p.iqr_low} – ${p.iqr_high} <span class="metric-unit">Hz</span></td>
                                    </tr>
                                    <tr>
                                        <td class="metric-label">Detected Bands</td>
                                        <td class="metric-value">${p.bands_count || 0} <span class="metric-unit">bands</span></td>
                                    </tr>
                                </table>

                                ${p.freq_bands && p.freq_bands.length > 0 ? `
                                    <div class="bands-container">
                                        <div class="bands-title">Harmonic Frequency Bands</div>
                                        <div class="bands-grid">
                                            ${p.freq_bands.map((b, i) => `
                                                <div class="band-chip">
                                                    <span>Band ${i + 1}</span>
                                                    ${b.low.toFixed(0)}-${b.high.toFixed(0)} Hz
                                                </div>
                                            `).join('')}
                                        </div>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    </div>

                    <!-- Right Column: Environmental (30%) -->
                    <div class="profile-col-right">
                        
                        <!-- Section C: Vibration Pattern -->
                        <div class="sub-card">
                            <div class="sub-card-header">Vibration Pattern</div>
                            <div class="sub-card-body">
                                ${p.vibration_data ? `
                                    <div class="compact-stat">
                                        <span class="compact-label">Samples</span>
                                        <span class="compact-value">${p.vibration_data.samples}</span>
                                    </div>
                                    <div class="compact-stat">
                                        <span class="compact-label">Intensity</span>
                                        <span class="compact-value stat-highlight" style="color: ${vibColor}">${vibPercent.toFixed(1)}%</span>
                                    </div>
                                    <div class="vib-bar-bg">
                                        <div class="vib-bar-fill" style="width: ${Math.min(vibPercent, 100)}%; background-color: ${vibColor}"></div>
                                    </div>
                                ` : `
                                    <div style="color: #666; font-size: 12px; font-style: italic;">No vibration data</div>
                                `}
                            </div>
                        </div>

                        <!-- Section D: Air Quality -->
                        <div class="sub-card">
                            <div class="sub-card-header">Air Quality</div>
                            <div class="sub-card-body">
                                ${p.gas_data ? `
                                    <div class="compact-stat">
                                        <span class="compact-label">Samples</span>
                                        <span class="compact-value">${p.gas_data.valid_samples || p.gas_data.samples}</span>
                                    </div>
                                    <div class="compact-stat">
                                        <span class="compact-label">Avg Level</span>
                                        <span class="compact-value">${p.gas_data.avg_raw}</span>
                                    </div>
                                    <div class="compact-stat" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #333">
                                        <span class="compact-label">Status</span>
                                        <span class="compact-value ${gasColorClass}">${gasLabel}</span>
                                    </div>
                                ` : `
                                    <div style="color: #666; font-size: 12px; font-style: italic;">No gas data</div>
                                `}
                            </div>
                        </div>

                    </div>
                </div>
            </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Error loading profiles:', error);
        document.getElementById('profiles-list').innerHTML = '<p style="color: #dc2626; text-align: center;">Error loading profiles. Check console.</p>';
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

    // Draw simple waveform
    ctx.strokeStyle = '#3b82f6';
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

    // Draw bars
    const barWidth = width / frequencyData.length;

    for (let i = 0; i < frequencyData.length; i++) {
        const barHeight = (frequencyData[i] / 255) * height;
        const x = i * barWidth;

        ctx.fillStyle = '#3b82f6';
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

    // Draw line
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 2;
    ctx.beginPath();

    for (let i = 0; i < dataArray.length; i++) {
        const x = (i / (dataArray.length - 1)) * width;
        const y = height - ((dataArray[i] / max) * height * 0.9);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

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

function drawMiniLineChart(dataArray, ctx, color = '#3b82f6', maxValue = null, chartType = null) {
    if (!ctx || dataArray.length === 0) return;

    // Ensure canvas has proper dimensions
    const canvas = ctx.canvas;
    if (canvas.width === 0 || canvas.height === 0) {
        canvas.width = Math.max(canvas.offsetWidth, canvas.parentElement?.offsetWidth || 400);
        canvas.height = Math.max(canvas.offsetHeight, 80);
    }

    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);

    const max = maxValue || Math.max(...dataArray, 1);
    const leftPadding = 35; // Space for Y-axis labels
    const rightPadding = 5;
    const topPadding = 5;
    const bottomPadding = 15; // Space for X-axis label
    const graphWidth = width - leftPadding - rightPadding;
    const graphHeight = height - topPadding - bottomPadding;

    // Draw gas danger zones if this is a gas chart
    if (chartType === 'gas') {
        // Red zone (hazardous > 700)
        const hazardY = topPadding + graphHeight - ((GAS_HAZARD_LIMIT / max) * graphHeight);
        ctx.fillStyle = 'rgba(239, 68, 68, 0.15)';
        ctx.fillRect(leftPadding, topPadding, graphWidth, hazardY - topPadding);

        // Yellow zone (warning 300-700)
        const safeY = topPadding + graphHeight - ((GAS_SAFE_LIMIT / max) * graphHeight);
        ctx.fillStyle = 'rgba(234, 179, 8, 0.15)';
        ctx.fillRect(leftPadding, hazardY, graphWidth, safeY - hazardY);

        // Green zone (safe < 300)
        ctx.fillStyle = 'rgba(34, 197, 94, 0.1)';
        ctx.fillRect(leftPadding, safeY, graphWidth, topPadding + graphHeight - safeY);
    }

    // Draw grid lines for reference
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
        const y = topPadding + (graphHeight / 4) * i;
        ctx.beginPath();
        ctx.moveTo(leftPadding, y);
        ctx.lineTo(width - rightPadding, y);
        ctx.stroke();
    }

    // Draw filled area first
    ctx.beginPath();
    for (let i = 0; i < dataArray.length; i++) {
        const x = leftPadding + (i / Math.max(dataArray.length - 1, 1)) * graphWidth;
        const y = topPadding + graphHeight - ((dataArray[i] / max) * graphHeight);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    // Complete the fill path
    ctx.lineTo(leftPadding + graphWidth, topPadding + graphHeight);
    ctx.lineTo(leftPadding, topPadding + graphHeight);
    ctx.closePath();
    ctx.fillStyle = color + '33'; // 20% opacity
    ctx.fill();

    // Draw the line on top
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < dataArray.length; i++) {
        const x = leftPadding + (i / Math.max(dataArray.length - 1, 1)) * graphWidth;
        const y = topPadding + graphHeight - ((dataArray[i] / max) * graphHeight);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // ---- Y-axis labels ----
    ctx.fillStyle = '#888';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';

    // Top (max)
    ctx.fillText(Math.round(max).toString(), leftPadding - 4, topPadding + 10);

    // Middle
    ctx.fillText(Math.round(max / 2).toString(), leftPadding - 4, topPadding + graphHeight / 2 + 3);

    // Bottom (zero)
    ctx.fillText('0', leftPadding - 4, topPadding + graphHeight - 2);

    // ---- X-axis label ----
    ctx.fillStyle = '#555';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Time →', leftPadding + graphWidth / 2, height - 2);

    // ---- Current value label (top right) ----
    if (dataArray.length > 0) {
        const lastValue = dataArray[dataArray.length - 1];
        ctx.fillStyle = color;
        ctx.font = 'bold 10px monospace';
        ctx.textAlign = 'right';
        ctx.fillText(Math.round(lastValue).toString(), width - rightPadding, topPadding + 10);
    }

    // ---- Chart type specific labels ----
    if (chartType === 'confidence') {
        ctx.fillStyle = '#888';
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';
        ctx.fillText('(0-1)', width - rightPadding, height - 2);
    }
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
        if (val > frequencyData[i - 1] &&
            val > frequencyData[i + 1] &&
            val >= frequencyData[i - 2] &&
            val >= frequencyData[i + 2]) {

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

function getClosestMachine(dominantFreq) {
    let closest = null;
    let smallestDistance = Infinity;

    Object.values(machineProfiles).forEach(profile => {
        const median = profile.median_freq;
        const distance = Math.abs(dominantFreq - median);

        if (distance < smallestDistance) {
            smallestDistance = distance;
            closest = profile.machine_id;
        }
    });

    if (!closest) return null;

    // Convert distance → pseudo confidence (heuristic)
    const confidence = Math.max(0, 1 - (smallestDistance / 1000));

    return {
        machine: closest,
        confidence: confidence.toFixed(2)
    };
}

function switchTab(event, tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

    document.getElementById(tab).classList.add('active');
    event.target.classList.add('active');

    // Initialize canvas when switching to live detection
    if (tab === 'livedetection') {
        setTimeout(() => {
            initializeCanvases();
            AudioHistoryGraph.init(); // Init Graph
            // Re-initialize gas chart specifically to ensure it's properly sized
            const liveGas = document.getElementById('live-gas-chart');
            if (liveGas && liveGas.offsetWidth > 0) {
                liveGasCtx = liveGas.getContext('2d');
                liveGas.width = Math.max(liveGas.offsetWidth, 400);
                liveGas.height = Math.max(liveGas.offsetHeight, 80);
            }
        }, 150);
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
// ==========================================
// STRICT CHART.JS FIX: LIVE SIGNAL
// ==========================================

let liveSignalChart = null;

// liveSignalChart logic removed as per requirements (Fixed Scale History Graph now used)
function initLiveSignalChart() {
    const ctx = document.getElementById('live-canvas');
    if (!ctx) return;

    if (liveSignalChart) {
        liveSignalChart.destroy();
    }

    // Generate initial X-labels (0 to 20ms approx)
    // Assuming ~48kHz sample rate, 1024 samples is ~21ms
    const labels = Array.from({ length: 1024 }, (_, i) => ((i / 1024) * 21).toFixed(1));

    liveSignalChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Normalized Amplitude',
                data: new Array(1024).fill(0.5),
                borderColor: '#3b82f6',
                borderWidth: 1.5,
                pointRadius: 0,
                tension: 0.1, // Slight curve for waveform look
                fill: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false, // Performance Critical
            interaction: {
                mode: 'nearest',
                intersect: false,
                axis: 'x'
            },
            plugins: {
                legend: { display: false },
                title: {
                    display: true,
                    text: 'Live Signal Waveform',
                    color: '#e0e0e0',
                    font: { size: 14, family: 'Roboto' },
                    padding: { bottom: 5 }
                },
                subtitle: {
                    display: true,
                    text: 'Real-time microphone input (short window)',
                    color: '#888',
                    font: { size: 10, family: 'Roboto' },
                    padding: { bottom: 10 }
                },
                tooltip: { enabled: false } // Disable tooltips for performance
            },
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Time (ms)',
                        color: '#999',
                        font: { size: 10 }
                    },
                    ticks: {
                        color: '#666',
                        maxTicksLimit: 10,
                        callback: function (val, index) {
                            return this.getLabelForValue(val) + ' ms';
                        }
                    },
                    grid: { color: '#222' }
                },
                y: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Audio Amplitude',
                        color: '#999',
                        font: { size: 10 }
                    },
                    ticks: { color: '#666' },
                    grid: { color: '#222' },
                    min: 0,
                    max: 1 // Normalized
                }
            }
        }
    });
}

function updateLiveSignalChart(timeData) {
    if (!liveSignalChart) {
        initLiveSignalChart();
        if (!liveSignalChart) return;
    }

    // Normalize Uint8 (0-255) to 0-1 float
    // Note: Creating a new array every frame is costly, but required for Chart.js
    // Optimization: Pre-allocate if possible, but Chart.js usually wants a fresh array or mutation
    const normalizedData = [];
    for (let i = 0; i < timeData.length; i++) {
        normalizedData.push(timeData[i] / 255);
    }

    liveSignalChart.data.datasets[0].data = normalizedData;
    liveSignalChart.update('none'); // Efficient update mode
}

async function startLiveDetection() {
    const ok = await initAudio();
    if (!ok) {
        updateStatus('livedetection', '❌ Microphone access required', 'error');
        return;
    }

    isLiveDetecting = true;
    liveBatch = [];
    liveBatchTimer = 0;

    // Record session start time for Gemini
    window.liveSessionStartTime = new Date().toISOString().slice(0, 19);
    window.liveSessionStopTime = null;

    // Hide Gemini button when starting new session
    document.getElementById('gemini-analysis-btn').style.display = 'none';

    // Reset tracking variables
    amplitudeHistory = [];
    frequencyHistory = [];
    gasHistory = [];
    vibrationHistory = [];
    confidenceHistory = [];

    // Start History Graph
    // Start History Graph
    AudioHistoryGraph.start();
    AudioHistoryGraph.start();



    liveStartTime = Date.now();
    liveSampleCount = 0;

    // Update UI
    document.getElementById('live-mode-badge').textContent = 'DETECTING';
    document.getElementById('live-mode-badge').className = 'mode-badge detecting';
    document.getElementById('live-start-btn').disabled = true;
    document.getElementById('live-stop-btn').disabled = false;

    updateStatus('livedetection', '🎤 Live detection running...', 'success');

    // Live ESP32 polling is now auto-started at init, but verify here
    startLiveESP32Polling();

    // Start audio detection loop
    liveDetectionLoop();

    // Start 5-second Event Log Timer
    logTimer = setInterval(logSystemSnapshot, 5000);
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

    // Central UI Map for Audio
    const ui = {
        freq: document.getElementById('live-freq'),
        amp: document.getElementById('live-amp'),
        conf: document.getElementById('live-conf'),
        confPercent: document.getElementById('live-detection-confidence'),

        // Compatibility / Hidden elements
        peak: document.getElementById('live-peak-amp'),
        avg: document.getElementById('live-avg-amp'),
        sampleRate: document.getElementById('live-sample-rate'),
        runtime: document.getElementById('live-runtime')
    };

    // Update audio UI with Guards
    if (ui.freq) ui.freq.textContent = dominantFreq.toFixed(1);
    if (ui.amp) ui.amp.textContent = amplitude.toFixed(2);
    if (ui.conf) ui.conf.textContent = freqConfidence.toFixed(3);
    if (ui.confPercent) ui.confPercent.textContent = (freqConfidence * 100).toFixed(0) + '%';

    // Hidden Elements (Guarded)
    if (ui.peak) ui.peak.textContent = peakAmp.toFixed(1);
    if (ui.avg) ui.avg.textContent = avgAmp.toFixed(1);
    if (ui.sampleRate) ui.sampleRate.textContent = sampleRate;
    if (ui.runtime) ui.runtime.textContent = runningTime + 's';

    // Draw visualizations (Contexts checked at init)
    updateLiveSignalChart(timeDomainData);
    AudioHistoryGraph.update(amplitude);

    if (liveSpectrumCtx) drawSpectrum(frequencyData, liveSpectrumCtx, nyquist);

    // Mini-charts (Guarded)
    if (liveConfidenceCtx && confidenceHistory.length > 0) {
        drawMiniLineChart(confidenceHistory, liveConfidenceCtx, '#3b82f6', 1, 'confidence');
    }

    // Send batch every 500ms
    liveBatchTimer += 0.1;
    if (liveBatchTimer >= 0.5) {
        if (liveBatch.length > 0) {
            sendLiveBatch();
            // NEW: Update Audio Table
            updateAudioFramesTable(liveBatch);
        }
        liveBatch = [];
        liveBatchTimer = 0;
    }

    setTimeout(liveDetectionLoop, 100);
}


// ==========================================
// NEW TABLE LOGIC (High Density)
// ==========================================

function updateAudioFramesTable(batch) {
    const tbody = document.getElementById('table-audio-frames');
    if (!tbody) return;

    // Prepend new rows
    const rows = batch.map(b => `
        <tr>
            <td>${new Date(b.timestamp).toLocaleTimeString().split(' ')[0]}</td>
            <td>${Math.round(b.dominant_freq)}</td>
            <td>${b.amplitude.toFixed(1)}</td>
            <td><span class="${b.freq_confidence > 0.5 ? 'status-cell-green' : 'status-cell-yellow'}">${b.freq_confidence.toFixed(2)}</span></td>
        </tr>
    `).join('');

    tbody.innerHTML = rows + tbody.innerHTML;

    // Limit rows
    while (tbody.children.length > 10) {
        tbody.removeChild(tbody.lastChild);
    }
}

// NEW: 5-Second System Snapshot Log
function logSystemSnapshot() {
    const tbody = document.getElementById('table-state-history');
    if (!tbody) return;

    // 1. Gather Data
    const time = new Date().toLocaleTimeString();

    // Machine Name
    const machineNameEl = document.getElementById('live-detected-machine');
    let machineName = machineNameEl ? machineNameEl.textContent : '--';
    if (machineName.includes('MATCH')) machineName = machineName.replace(' (POSSIBLE MATCH)', '').replace(' (SLIGHTLY DIFFERENT)', '');

    // Values (Get from UI to match user view)
    const freq = document.getElementById('live-freq') ? document.getElementById('live-freq').textContent : '--';
    const amp = document.getElementById('live-amp') ? document.getElementById('live-amp').textContent : '--';
    const vib = document.getElementById('live-vibration-value') ? document.getElementById('live-vibration-value').textContent : '0.00';
    const gas = document.getElementById('live-gas-value') ? document.getElementById('live-gas-value').textContent : '--';

    // 2. Determine Row Color based on Gas/Vib limits
    let rowClass = ''; // Default white/gray text
    const gasVal = parseInt(gas) || 0;
    const vibVal = parseFloat(vib) || 0;

    if (gasVal > 700 || vibVal > 0.5) rowClass = 'status-cell-red';
    else if (gasVal > 300) rowClass = 'status-cell-yellow';

    // 3. Construct Row
    const row = `
        <tr>
            <td>${time}</td>
            <td style="font-weight:bold; color:#fff;">${machineName}</td>
            <td>${freq}</td>
            <td>${amp}</td>
            <td><span class="${vibVal > 0.5 ? 'status-cell-red' : ''}">${vib}</span></td>
            <td><span class="${gasVal > 300 ? 'status-cell-yellow' : ''}">${gas}</span></td>
        </tr>
    `;

    // 4. Prepend
    tbody.innerHTML = row + tbody.innerHTML;

    // 5. Prune (Keep last 50 entries)
    while (tbody.children.length > 50) {
        tbody.removeChild(tbody.lastChild);
    }
}

function updateSensorSnapshotTable(gasRaw, gasStatus, vibVal) {
    const tbody = document.getElementById('table-sensor-snapshot');
    if (!tbody) return;

    const time = new Date().toLocaleTimeString();

    // Status Logic
    const gasColor = gasStatus === 'SAFE' ? 'status-cell-green' : 'status-cell-red';
    const vibColor = vibVal > 0.5 ? 'status-cell-red' : 'status-cell-green';
    const micColor = 'status-cell-green'; // Always active

    tbody.innerHTML = `
        <tr><td>MIC_ARR_01</td><td>ACTIVE</td><td>dB</td><td>${time}</td><td><span class="${micColor}">OK</span></td></tr>
        <tr><td>GAS_MQX_02</td><td>${gasRaw}</td><td>PPM</td><td>${time}</td><td><span class="${gasColor}">${gasStatus}</span></td></tr>
        <tr><td>ACCEL_XYZ</td><td>${vibVal.toFixed(2)}</td><td>G</td><td>${time}</td><td><span class="${vibColor}">${vibVal > 0 ? 'MOTION' : 'STILL'}</span></td></tr>
    `;
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

        // 1. Central UI Map for ESP32
        const ui = {
            vibVal: document.getElementById('live-vibration-value'),
            vibIntensity: document.getElementById('live-vibration-intensity'),
            // gasVal uses different ID in some layouts? Check safely.
            gasVal: document.getElementById('live-gas-value'),
            gasStatus: document.getElementById('live-gas-status-text'),

            // Charts Canvas
            gasCanvas: document.getElementById('live-gas-chart'),
            vibCanvas: document.getElementById('live-vibration-chart'),

            // Non-existent in V6 (but kept for safety if someone adds back)
            vibBar: document.getElementById('live-vibration-bar'),
            eventCount: document.getElementById('live-event-count')
        };

        // 2. Parse Data Safe Defaults
        const vibValue = data.vibration || 0;
        // Fix: Allow 0 as valid gas reading. Only use 2123 if undefined.
        const gasRaw = (data.gas_raw !== undefined && data.gas_raw !== null) ? data.gas_raw : 2123;
        const gasStatus = data.gas_status || 'STANDBY';

        // 3. Update UI with Guards
        if (ui.vibVal) ui.vibVal.textContent = vibValue.toFixed(2);

        // Intensity Label Logic
        if (ui.vibIntensity) {
            if (vibValue > 0.7) {
                ui.vibIntensity.textContent = 'HIGH';
                ui.vibIntensity.className = 'vibration-intensity high';
            } else if (vibValue > 0.3) {
                ui.vibIntensity.textContent = 'MEDIUM';
                ui.vibIntensity.className = 'vibration-intensity medium';
            } else {
                ui.vibIntensity.textContent = 'LOW';
                ui.vibIntensity.className = 'vibration-intensity low';
            }
        }

        if (ui.eventCount) ui.eventCount.textContent = data.event_count || 0;

        if (ui.gasVal) ui.gasVal.textContent = gasRaw;

        // Color Logic for Gas Value
        if (ui.gasVal) {
            if (gasRaw > GAS_HAZARD_LIMIT || gasStatus === 'RISK' || gasStatus === 'DANGER') {
                ui.gasVal.style.color = '#dc2626';
            } else if (gasRaw > GAS_SAFE_LIMIT || gasStatus === 'WARNING') {
                ui.gasVal.style.color = '#eab308';
            } else {
                ui.gasVal.style.color = '#22c55e';
            }
        }

        if (ui.gasStatus) ui.gasStatus.textContent = gasStatus;

        // 4. Update Charts & Tables (Functions handle their own null checks internally usually, but let's be safe)
        updateGasGauge(gasRaw);
        updateSensorSnapshotTable(gasRaw, gasStatus, vibValue);

        // 5. Track History
        vibrationHistory.push(Math.min(vibValue * 100, 100)); // Normalize for chart
        if (vibrationHistory.length > 30) vibrationHistory.shift();

        gasHistory.push(gasRaw);
        if (gasHistory.length > 30) gasHistory.shift();

        // 6. Draw Mini Charts (Guarded)
        if (ui.gasCanvas && gasHistory.length > 0) {
            // Re-init context if lost
            if (!liveGasCtx || ui.gasCanvas.width === 0) {
                // Removed legacy mini-chart draw to prevent conflict with GasHistoryGraph
            }
        }

        if (ui.vibCanvas && vibrationHistory.length > 0) {
            if (!liveVibrationCtx || ui.vibCanvas.width === 0) {
                liveVibrationCtx = ui.vibCanvas.getContext('2d');
                resizeCanvas(ui.vibCanvas);
            }
            if (liveVibrationCtx) drawMiniLineChart(vibrationHistory, liveVibrationCtx, '#dc2626', 100, 'vibration');
        }

    } catch (err) {
        console.warn('ESP32 fetch loop error (handled):', err);
    }
}

function stopLiveDetection() {
    isLiveDetecting = false;
    liveBatch = [];
    liveBatch = [];
    liveBatchTimer = 0;

    if (logTimer) {
        clearInterval(logTimer);
        logTimer = null;
    }

    // Do NOT stop environment polling (Gas/Vib) when audio detection stops
    // stopLiveESP32Polling();

    // Record session end time
    window.liveSessionStopTime = new Date().toISOString().slice(0, 19);

    document.getElementById('live-mode-badge').textContent = 'IDLE';
    document.getElementById('live-mode-badge').className = 'mode-badge idle';
    document.getElementById('live-start-btn').disabled = false;
    document.getElementById('live-stop-btn').disabled = true;

    // Show Gemini analysis button
    document.getElementById('gemini-analysis-btn').style.display = 'inline-block';

    updateStatus('livedetection', 'Detection stopped. Click "Send Session to Gemini AI" to analyze this session.', 'info');
}

// Session tracking for Gemini
window.liveSessionStartTime = null;
window.liveSessionStopTime = null;

function openGeminiAnalysis() {
    if (!window.liveSessionStartTime || !window.liveSessionStopTime) {
        alert('No session data available. Please run a detection session first.');
        return;
    }

    // Open the Gemini analysis page with session parameters
    const params = new URLSearchParams({
        start: window.liveSessionStartTime,
        stop: window.liveSessionStopTime,
        autoload: 'true'
    });

    window.open('/gemini-analysis?' + params.toString(), '_blank');
}
