import requests
import sys

# Configuration
BASE_URL = "http://localhost:5000"
ENDPOINT = "/ingest_esp32"

def test_auth(api_key=None):
    url = f"{BASE_URL}{ENDPOINT}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        print(f"Testing WITH key: {api_key[:5]}...")
    else:
        print("Testing WITHOUT key...")

    payload = {
        "device_id": "TEST_DEVICE_001",
        "vibration": 12.5,
        "event_count": 1,
        "gas_raw": 450,
        "gas_status": "OK"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if api_key and response.status_code == 200:
            print("✅ SUCCESS: Auth working.")
        elif not api_key and response.status_code == 401:
            print("✅ SUCCESS: Unauthorized access blocked.")
        else:
            print("❌ FAILURE: Unexpected result.")
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_auth(sys.argv[1])
    else:
        # Test 1: No Key (Should Fail)
        test_auth(None)
        
        print("\nTo test success, run: python test_esp32_auth.py <YOUR_API_KEY>")
