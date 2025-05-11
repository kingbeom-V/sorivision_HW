import threading
import time
import requests
import random

# === 전역 설정 ===
device_id = "raspi-001"
BASE_LAT = 37.30041
BASE_LON = 127.0397
LOCATION_VARIATION = 0.002  # 약 200m 이내 변동

# === GPS 데이터 생성 ===
def generate_random_location():
    lat_variation = random.uniform(-LOCATION_VARIATION, LOCATION_VARIATION)
    lon_variation = random.uniform(-LOCATION_VARIATION, LOCATION_VARIATION)
    lat = round(BASE_LAT + lat_variation, 6)
    lon = round(BASE_LON + lon_variation, 6)
    return lat, lon

# === HTTP 전송 ===
def send_http(lat, lon):
    try:
        res = requests.post("http://your.server.com/api/v1/hw/gps", json={
            'device_id': device_id,
            'lat': lat,
            'lon': lon
        })
        print(f"📡 전송 완료: {lat}, {lon}, 상태: {res.status_code}")
    except Exception as e:
        print(f"❌ 전송 실패: {e}")

# === 메인 실행 ===
if __name__ == "__main__":
    print(f"✅ 기기 ID: {device_id}")

    try:
        while True:
            # 랜덤한 GPS 데이터 생성
            lat, lon = generate_random_location()
            # 서버로 전송
            send_http(lat, lon)
            # 5초 대기
            time.sleep(5)
    except KeyboardInterrupt:
        print("🛑 종료됨")
