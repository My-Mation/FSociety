#include <WiFi.h>
#include <HTTPClient.h>

/* ===== PINS ===== */
#define VIB_PIN 27
#define GAS_PIN 35

/* ===== LAPTOP HOTSPOT WIFI ===== */
const char* WIFI_SSID = "LAPTOP-20AQM9CG 1278";
const char* WIFI_PASS = "23456789";

/* ===== BACKEND ===== */
/* Use laptop hotspot gateway IP (usually 192.168.137.1 on Windows) */
const char* BACKEND_URL = "http://192.168.137.1:5000/ingest_esp32";
const char* DEVICE_ID   = "esp32_001";

/* ===== API KEY (REQUIRED FOR AUTH) ===== */
const char* API_KEY = "_v13iKLTqgwxUe3SWta8x7PGvqjAYhkAWw63dhA6Nec";

/* ===== TIMING ===== */
unsigned long lastReadTime = 0;
unsigned long lastSendTime = 0;

const unsigned long READ_INTERVAL = 30;     // ms
const unsigned long SEND_INTERVAL = 200;   // ms

/* ===== DATA ===== */
int eventCount = 0;
int lastReading = 0;
int currentReading = 0;

int gasRaw = 0;
String gasStatus = "MEDIUM";

/* ===== SEND TO BACKEND ===== */
void sendToBackend() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected");
    return;
  }

  HTTPClient http;
  http.begin(BACKEND_URL);
  
  // [FIX] Add Headers
  http.addHeader("Content-Type", "application/json");
  // [FIX] Add Authorization Header with API Key
  http.addHeader("Authorization", "Bearer " + String(API_KEY));

  String payload = "{";
  payload += "\"device_id\":\"" + String(DEVICE_ID) + "\",";
  payload += "\"vibration\":" + String(currentReading) + ",";
  payload += "\"event_count\":" + String(eventCount) + ",";
  payload += "\"gas_raw\":" + String(gasRaw) + ",";
  payload += "\"gas_status\":\"" + gasStatus + "\"";
  payload += "}";

  int httpCode = http.POST(payload);

  Serial.print("[ESP32 → BACKEND] HTTP ");
  Serial.println(httpCode);
  
  if (httpCode > 0) {
      String response = http.getString();
      Serial.println(response);
  }

  http.end();
}

/* ===== SETUP ===== */
void setup() {
  Serial.begin(115200);

  pinMode(VIB_PIN, INPUT_PULLUP);
  pinMode(GAS_PIN, INPUT);

  /* ---- CONNECT TO LAPTOP HOTSPOT ---- */
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi connected");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

/* ===== LOOP ===== */
void loop() {
  unsigned long now = millis();

  /* ---- SENSOR READ ---- */
  if (now - lastReadTime >= READ_INTERVAL) {
    lastReadTime = now;

    int reading = !digitalRead(VIB_PIN);
    gasRaw = analogRead(GAS_PIN);

    currentReading = reading;
    if (reading == 1 && lastReading == 0) eventCount++;
    lastReading = reading;

    if (gasRaw >= 4000) gasStatus = "RISK";
    else if (gasRaw >= 3500) gasStatus = "WARNING";
    else gasStatus = "MEDIUM";
  }

  /* ---- SEND TO BACKEND ---- */
  if (now - lastSendTime >= SEND_INTERVAL) {
    lastSendTime = now;
    sendToBackend();
  }
}
