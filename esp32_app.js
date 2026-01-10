/**
 * ========================================
 * ESP32 INDUSTRIAL MONITOR - APPLICATION
 * Vanilla JavaScript for sensor dashboard
 * ========================================
 */

(function() {
    'use strict';

    // ========================================
    // CONFIGURATION
    // ========================================
    const CONFIG = {
        API_BASE: window.location.origin,
        POLL_INTERVAL: 2000,        // Poll every 2 seconds
        MAX_HISTORY_POINTS: 100,    // Max data points to store
        MAX_TABLE_ROWS: 20,         // Max rows in data table
        GAS_MAX_VALUE: 4000,        // Max gas PPM for scale
        CHART_COLORS: {
            gas: '#22c55e',
            gasWarning: '#eab308',
            gasRisk: '#ef4444',
            vibration: '#3b82f6',
            grid: '#2d3748',
            text: '#8b949e'
        }
    };

    // ========================================
    // STATE MANAGEMENT
    // ========================================
    const state = {
        connected: false,
        lastUpdate: null,
        currentData: null,
        history: {
            gas: [],
            vibration: [],
            timestamps: []
        },
        vibrationPeak: 0,
        totalEvents: 0,
        pollTimer: null
    };

    // ========================================
    // DOM ELEMENT REFERENCES
    // ========================================
    const elements = {
        // Connection status
        connectionStatus: document.getElementById('connection-status'),
        lastUpdate: document.getElementById('last-update'),
        
        // Device info
        deviceId: document.getElementById('device-id'),
        lastTimestamp: document.getElementById('last-timestamp'),
        eventCount: document.getElementById('event-count'),
        
        // Gas sensor
        gasValue: document.getElementById('gas-value'),
        gasStatusBadge: document.getElementById('gas-status-badge'),
        gasBar: document.getElementById('gas-bar'),
        gasChart: document.getElementById('gas-chart'),
        
        // Vibration sensor
        vibrationValue: document.getElementById('vibration-value'),
        vibrationIndicator: document.getElementById('vibration-indicator'),
        vibrationEvents: document.getElementById('vibration-events'),
        vibrationPeak: document.getElementById('vibration-peak'),
        vibrationChart: document.getElementById('vibration-chart'),
        
        // History
        historyChart: document.getElementById('history-chart'),
        dataPointsCount: document.getElementById('data-points-count'),
        clearHistoryBtn: document.getElementById('btn-clear-history'),
        
        // Table
        dataTableBody: document.getElementById('data-table-body')
    };

    // ========================================
    // API FUNCTIONS
    // ========================================
    
    /**
     * Fetch ESP32 data from backend
     * @param {number} limit - Number of records to fetch
     * @returns {Promise<Array>} Array of sensor readings
     */
    async function fetchESP32Data(limit = 50) {
        try {
            const response = await fetch(`${CONFIG.API_BASE}/esp32_data?limit=${limit}`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            return data;
            
        } catch (error) {
            console.error('[ESP32] Fetch error:', error.message);
            throw error;
        }
    }

    // ========================================
    // DATA PROCESSING
    // ========================================
    
    /**
     * Process and update state with new data
     * @param {Array} data - Raw data from API
     */
    function processData(data) {
        if (!data || data.length === 0) {
            return;
        }

        // Get most recent reading
        const latest = data[0];
        state.currentData = latest;
        state.lastUpdate = new Date();

        // Update history (add new points, maintain max size)
        data.slice().reverse().forEach(reading => {
            const timestamp = reading.timestamp;
            
            // Avoid duplicates
            if (state.history.timestamps.includes(timestamp)) {
                return;
            }

            state.history.gas.push(reading.gas_raw || 0);
            state.history.vibration.push(reading.vibration || 0);
            state.history.timestamps.push(timestamp);

            // Trim to max size
            if (state.history.gas.length > CONFIG.MAX_HISTORY_POINTS) {
                state.history.gas.shift();
                state.history.vibration.shift();
                state.history.timestamps.shift();
            }
        });

        // Update peak vibration
        const currentVibration = latest.vibration || 0;
        if (currentVibration > state.vibrationPeak) {
            state.vibrationPeak = currentVibration;
        }

        // Update total events
        state.totalEvents = latest.event_count || 0;
    }

    /**
     * Get status class based on gas status string
     * @param {string} status - Gas status from API
     * @returns {string} CSS class name
     */
    function getGasStatusClass(status) {
        if (!status) return '';
        
        const normalized = status.toUpperCase();
        if (normalized === 'RISK' || normalized === 'DANGER') return 'risk';
        if (normalized === 'WARNING') return 'warning';
        return 'safe';
    }

    /**
     * Format timestamp for display
     * @param {string} timestamp - ISO timestamp
     * @returns {string} Formatted time string
     */
    function formatTimestamp(timestamp) {
        if (!timestamp) return '--:--:--';
        
        try {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('en-US', { 
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        } catch {
            return '--:--:--';
        }
    }

    /**
     * Format short timestamp for table
     * @param {string} timestamp - ISO timestamp
     * @returns {string} Formatted datetime string
     */
    function formatShortTimestamp(timestamp) {
        if (!timestamp) return '--';
        
        try {
            const date = new Date(timestamp);
            return date.toLocaleString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            });
        } catch {
            return '--';
        }
    }

    // ========================================
    // UI UPDATE FUNCTIONS
    // ========================================
    
    /**
     * Update connection status indicator
     * @param {boolean} connected - Connection state
     */
    function updateConnectionStatus(connected) {
        state.connected = connected;
        
        elements.connectionStatus.classList.remove('connected', 'disconnected');
        elements.connectionStatus.classList.add(connected ? 'connected' : 'disconnected');
        
        const statusText = elements.connectionStatus.querySelector('.status-text');
        statusText.textContent = connected ? 'Connected' : 'Disconnected';
    }

    /**
     * Update last update timestamp display
     */
    function updateLastUpdateTime() {
        if (state.lastUpdate) {
            elements.lastUpdate.textContent = formatTimestamp(state.lastUpdate.toISOString());
        }
    }

    /**
     * Update all UI elements with current data
     */
    function updateUI() {
        const data = state.currentData;
        
        if (!data) {
            return;
        }

        // Device info
        elements.deviceId.textContent = data.device_id || '--';
        elements.lastTimestamp.textContent = formatShortTimestamp(data.timestamp);
        elements.eventCount.textContent = state.totalEvents;

        // Gas sensor
        const gasValue = data.gas_raw || 0;
        elements.gasValue.textContent = gasValue;
        
        // Gas status badge
        const statusClass = getGasStatusClass(data.gas_status);
        elements.gasStatusBadge.textContent = data.gas_status || '--';
        elements.gasStatusBadge.className = 'status-badge ' + statusClass;
        
        // Gas bar (percentage of max)
        const gasPercent = Math.min(100, (gasValue / CONFIG.GAS_MAX_VALUE) * 100);
        elements.gasBar.style.width = `${100 - gasPercent}%`;

        // Vibration sensor
        const vibration = data.vibration || 0;
        elements.vibrationValue.textContent = vibration;
        
        // Vibration indicator (active if value > 0)
        if (vibration > 0) {
            elements.vibrationIndicator.classList.add('active');
            elements.vibrationIndicator.querySelector('.indicator-text').textContent = 'ON';
        } else {
            elements.vibrationIndicator.classList.remove('active');
            elements.vibrationIndicator.querySelector('.indicator-text').textContent = 'OFF';
        }
        
        // Vibration stats
        elements.vibrationEvents.textContent = state.totalEvents;
        elements.vibrationPeak.textContent = state.vibrationPeak;

        // Data points count
        elements.dataPointsCount.textContent = `${state.history.gas.length} points`;

        // Update last update time
        updateLastUpdateTime();
    }

    /**
     * Update data table with recent readings
     * @param {Array} data - Array of readings
     */
    function updateTable(data) {
        if (!data || data.length === 0) {
            elements.dataTableBody.innerHTML = `
                <tr>
                    <td colspan="6" class="no-data">No data available</td>
                </tr>
            `;
            return;
        }

        const rows = data.slice(0, CONFIG.MAX_TABLE_ROWS).map(reading => {
            const statusClass = getGasStatusClass(reading.gas_status);
            return `
                <tr>
                    <td>${formatShortTimestamp(reading.timestamp)}</td>
                    <td>${reading.device_id || '--'}</td>
                    <td>${reading.gas_raw || 0}</td>
                    <td class="status-${statusClass}">${reading.gas_status || '--'}</td>
                    <td>${reading.vibration || 0}</td>
                    <td>${reading.event_count || 0}</td>
                </tr>
            `;
        }).join('');

        elements.dataTableBody.innerHTML = rows;
    }

    // ========================================
    // CANVAS CHART FUNCTIONS
    // ========================================
    
    /**
     * Draw a simple line chart on canvas
     * @param {HTMLCanvasElement} canvas - Target canvas
     * @param {Array} data - Data points
     * @param {string} color - Line color
     * @param {number} maxValue - Max Y value for scaling
     */
    function drawLineChart(canvas, data, color, maxValue = null) {
        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        const padding = 10;

        // Clear canvas
        ctx.fillStyle = '#111820';
        ctx.fillRect(0, 0, width, height);

        if (!data || data.length < 2) {
            // Draw "no data" message
            ctx.fillStyle = CONFIG.CHART_COLORS.text;
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Waiting for data...', width / 2, height / 2);
            return;
        }

        // Calculate scale
        const max = maxValue || Math.max(...data) || 1;
        const min = 0;
        const range = max - min;

        const chartWidth = width - padding * 2;
        const chartHeight = height - padding * 2;

        // Draw grid lines
        ctx.strokeStyle = CONFIG.CHART_COLORS.grid;
        ctx.lineWidth = 0.5;
        
        for (let i = 0; i <= 4; i++) {
            const y = padding + (chartHeight / 4) * i;
            ctx.beginPath();
            ctx.moveTo(padding, y);
            ctx.lineTo(width - padding, y);
            ctx.stroke();
        }

        // Draw data line
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.beginPath();

        data.forEach((value, index) => {
            const x = padding + (index / (data.length - 1)) * chartWidth;
            const y = padding + chartHeight - ((value - min) / range) * chartHeight;
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });

        ctx.stroke();

        // Draw fill gradient
        ctx.lineTo(padding + chartWidth, padding + chartHeight);
        ctx.lineTo(padding, padding + chartHeight);
        ctx.closePath();
        
        const gradient = ctx.createLinearGradient(0, padding, 0, height - padding);
        gradient.addColorStop(0, color + '40');
        gradient.addColorStop(1, color + '05');
        ctx.fillStyle = gradient;
        ctx.fill();

        // Draw current value label
        if (data.length > 0) {
            const lastValue = data[data.length - 1];
            ctx.fillStyle = color;
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'right';
            ctx.fillText(lastValue.toFixed(0), width - padding, padding + 12);
        }
    }

    /**
     * Draw combined history chart
     * @param {HTMLCanvasElement} canvas - Target canvas
     */
    function drawHistoryChart(canvas) {
        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        const padding = { top: 20, right: 60, bottom: 30, left: 50 };

        // Clear canvas
        ctx.fillStyle = '#111820';
        ctx.fillRect(0, 0, width, height);

        const gasData = state.history.gas;
        const vibrationData = state.history.vibration;

        if (gasData.length < 2) {
            ctx.fillStyle = CONFIG.CHART_COLORS.text;
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Collecting data...', width / 2, height / 2);
            return;
        }

        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // Calculate scales
        const gasMax = Math.max(...gasData, 1000);
        const vibMax = Math.max(...vibrationData, 10);

        // Draw grid
        ctx.strokeStyle = CONFIG.CHART_COLORS.grid;
        ctx.lineWidth = 0.5;
        
        for (let i = 0; i <= 5; i++) {
            const y = padding.top + (chartHeight / 5) * i;
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(width - padding.right, y);
            ctx.stroke();
            
            // Y-axis labels (gas)
            const gasLabel = Math.round(gasMax - (gasMax / 5) * i);
            ctx.fillStyle = CONFIG.CHART_COLORS.gas;
            ctx.font = '10px monospace';
            ctx.textAlign = 'right';
            ctx.fillText(gasLabel.toString(), padding.left - 5, y + 3);
        }

        // Draw gas line
        ctx.strokeStyle = CONFIG.CHART_COLORS.gas;
        ctx.lineWidth = 2;
        ctx.beginPath();

        gasData.forEach((value, index) => {
            const x = padding.left + (index / (gasData.length - 1)) * chartWidth;
            const y = padding.top + chartHeight - (value / gasMax) * chartHeight;
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();

        // Draw vibration line (different scale)
        ctx.strokeStyle = CONFIG.CHART_COLORS.vibration;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();

        vibrationData.forEach((value, index) => {
            const x = padding.left + (index / (vibrationData.length - 1)) * chartWidth;
            const y = padding.top + chartHeight - (value / vibMax) * chartHeight;
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw legend
        ctx.font = '11px sans-serif';
        
        // Gas legend
        ctx.fillStyle = CONFIG.CHART_COLORS.gas;
        ctx.fillRect(padding.left, height - 15, 15, 3);
        ctx.fillText('Gas (PPM)', padding.left + 20, height - 10);
        
        // Vibration legend
        ctx.fillStyle = CONFIG.CHART_COLORS.vibration;
        ctx.fillRect(padding.left + 120, height - 15, 15, 3);
        ctx.fillText('Vibration', padding.left + 140, height - 10);

        // Right Y-axis labels (vibration)
        ctx.textAlign = 'left';
        for (let i = 0; i <= 5; i++) {
            const y = padding.top + (chartHeight / 5) * i;
            const vibLabel = Math.round(vibMax - (vibMax / 5) * i);
            ctx.fillStyle = CONFIG.CHART_COLORS.vibration;
            ctx.fillText(vibLabel.toString(), width - padding.right + 5, y + 3);
        }
    }

    /**
     * Update all charts
     */
    function updateCharts() {
        // Gas mini chart
        drawLineChart(
            elements.gasChart,
            state.history.gas.slice(-30),
            CONFIG.CHART_COLORS.gas,
            CONFIG.GAS_MAX_VALUE
        );

        // Vibration mini chart
        drawLineChart(
            elements.vibrationChart,
            state.history.vibration.slice(-30),
            CONFIG.CHART_COLORS.vibration,
            Math.max(...state.history.vibration, 10)
        );

        // Combined history chart
        drawHistoryChart(elements.historyChart);
    }

    // ========================================
    // POLLING & MAIN LOOP
    // ========================================
    
    /**
     * Main polling function - fetches and updates data
     */
    async function poll() {
        try {
            const data = await fetchESP32Data(50);
            
            updateConnectionStatus(true);
            processData(data);
            updateUI();
            updateTable(data);
            updateCharts();
            
        } catch (error) {
            console.error('[ESP32] Poll error:', error);
            updateConnectionStatus(false);
        }
    }

    /**
     * Start polling loop
     */
    function startPolling() {
        // Initial poll
        poll();
        
        // Set up interval
        state.pollTimer = setInterval(poll, CONFIG.POLL_INTERVAL);
        
        console.log(`[ESP32] Polling started (${CONFIG.POLL_INTERVAL}ms interval)`);
    }

    /**
     * Stop polling loop
     */
    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
            console.log('[ESP32] Polling stopped');
        }
    }

    // ========================================
    // EVENT HANDLERS
    // ========================================
    
    /**
     * Clear history data
     */
    function clearHistory() {
        state.history.gas = [];
        state.history.vibration = [];
        state.history.timestamps = [];
        state.vibrationPeak = 0;
        
        updateCharts();
        elements.dataPointsCount.textContent = '0 points';
        
        console.log('[ESP32] History cleared');
    }

    /**
     * Handle canvas resize
     */
    function handleResize() {
        // Update canvas dimensions based on container
        const charts = [
            elements.gasChart,
            elements.vibrationChart,
            elements.historyChart
        ];

        charts.forEach(canvas => {
            const container = canvas.parentElement;
            canvas.width = container.clientWidth;
        });

        updateCharts();
    }

    // ========================================
    // INITIALIZATION
    // ========================================
    
    /**
     * Initialize the application
     */
    function init() {
        console.log('[ESP32] Initializing dashboard...');

        // Set up event listeners
        elements.clearHistoryBtn.addEventListener('click', clearHistory);
        window.addEventListener('resize', handleResize);

        // Handle visibility change (pause polling when tab hidden)
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                stopPolling();
            } else {
                startPolling();
            }
        });

        // Initial resize
        handleResize();

        // Start polling
        startPolling();

        console.log('[ESP32] Dashboard initialized');
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
