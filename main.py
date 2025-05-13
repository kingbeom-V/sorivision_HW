
from dotenv import load_dotenv
import os
import serial
import threading
import time
import requests
import RPi.GPIO as GPIO
import cv2
from pydub import AudioSegment
from pydub.playback import play
import io
import speech_recognition as sr
import random

# === 1. 환경 변수 로드 ===
load_dotenv()

SERVER_BASE_URL = os.getenv("SERVER_BASE_URL")
DEVICE_ID = os.getenv("DEVICE_ID")
LOCAL_MP3_PATH = os.getenv("LOCAL_MP3_PATH")

latest_location = {'lat': None, 'lon': None}
location_lock = threading.Lock()

# === 2. GPIO 설정 ===
IR_PIN = 19
GPIO.setmode(GPIO.BCM)
GPIO.setup(IR_PIN, GPIO.IN)

# === 3. GPS 데이터 처리 ===
def parse_gprmc(sentence):
    try:
        parts = sentence.split(',')
        if parts[2] != 'A':
            return None, None
        lat = convert_to_decimal(parts[3], parts[4])
        lon = convert_to_decimal(parts[5], parts[6])
        return lat, lon
    except Exception as e:
        print(f"❌ GPS 파싱 오류: {e}")
        return None, None

def convert_to_decimal(raw, direction):
    if not raw:
        return None
    d, m = (float(raw[:2]), float(raw[2:])) if direction in ['N', 'S'] else (float(raw[:3]), float(raw[3:]))
    return -1 * (d + m / 60.0) if direction in ['S', 'W'] else round(d + m / 60.0, 6)

def gps_reader(ser):
    global latest_location
    while True:
        if ser.in_waiting:
            line = ser.readline().decode('ascii', errors='replace').strip()
            if line.startswith('$GPRMC'):
                lat, lon = parse_gprmc(line)
                if lat and lon:
                    with location_lock:
                        latest_location['lat'], latest_location['lon'] = lat, lon
        time.sleep(1)

# === 4. IR 핸들러 ===
def ir_handler():
    press_time = None
    press_count = 0
    last_press = 0
    while True:
        if GPIO.input(IR_PIN) == 0:
            now = time.time()
            if press_time is None:
                press_time = now
                last_press = now
                press_count = 1
            elif now - last_press <= 0.8:
                press_count += 1
                last_press = now
            else:
                handle_key_pattern(press_count, last_press - press_time)
                press_time = now
                last_press = now
                press_count = 1

        # 길게 눌림 처리
        if press_time and time.time() - last_press > 1.0:
            duration = last_press - press_time
            handle_key_pattern(press_count, duration)
            press_time = None
            press_count = 0

        time.sleep(0.05)

# === 5. HTTP 통신 ===
def send_http(lat, lon):
    try:
        res = requests.post(f"{SERVER_BASE_URL}/gps", json={
            'device_id': DEVICE_ID,
            'lat': lat,
            'lon': lon
        })
        print(f"📡 전송 완료: {lat}, {lon}, 상태: {res.status_code}")
    except Exception as e:
        print(f"❌ 전송 실패: {e}")

def burst_send_images(emergency_id):
    for i in range(10):
        img_bytes = capture_image_bytes()
        if img_bytes:
            try:
                files = {'image': (f'frame_{i}.jpg', img_bytes, 'image/jpeg')}
                data = {'device_id': DEVICE_ID, 'emergency_id': emergency_id}
                res = requests.post(f"{SERVER_BASE_URL}/emergency_img", files=files, data=data)
                print(f"📸 이미지 전송 {i+1}/10, 상태: {res.status_code}")
            except Exception as e:
                print(f"❌ 이미지 전송 실패: {e}")
        else:
            print(f"⚠️ 이미지 캡처 실패 ({i+1}/10)")
        time.sleep(1)

def get_emergency_id():
    try:
        res = requests.post(f"{SERVER_BASE_URL}/get_emergency_id", json={
            'device_id': DEVICE_ID,
        })
        if res.status_code == 200:
            data = res.json()
            emergency_id = data.get("emergency_id")
            if emergency_id:
                print(f"✅ Emergency ID: {emergency_id}")
                return emergency_id
            else:
                print("⚠️ 응답에 'emergency_id' 없음")
        else:
            print(f"❌ 상태 코드 오류: {res.status_code}")
    except Exception as e:
        print(f"❌ 전송 실패: {e}")
    return None

# === 6. 오디오 처리 ===
def set_audio_output_to_jack():
    os.system("amixer cset numid=3 1")

def play_local_mp3(filepath):
    try:
        set_audio_output_to_jack()
        audio = AudioSegment.from_file(filepath, format="mp3")
        play(audio)
    except Exception as e:
        print(f"❌ 로컬 MP3 재생 실패: {e}")

def play_mp3_binary(mp3_bytes):
    set_audio_output_to_jack()
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    play(audio)

# === 7. 이미지 처리 ===
def capture_image_bytes():
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    if ret:
        success, buffer = cv2.imencode('.jpg', frame)
        if success:
            return io.BytesIO(buffer.tobytes())
    return None

# === 8. 프롬프트 및 음성 인식 ===
def recognize_speech():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("🎙️ 말해주세요...")
        audio = recognizer.listen(source, timeout=5, phrase_time_limit=7)
    try:
        text = recognizer.recognize_google(audio, language='ko-KR')
        print(f"📝 인식된 텍스트: {text}")
        return text
    except sr.UnknownValueError:
        print("❌ 음성을 인식하지 못했습니다.")
    except sr.RequestError as e:
        print(f"❌ 음성 인식 API 오류: {e}")
    return None

# === 10. 메인 실행부 ===
if __name__ == "__main__":
    try:
        ser = serial.Serial('/dev/serial0', 9600, timeout=1)
    except Exception as e:
        print(f"❌ 시리얼 포트 오류: {e}")
        exit()

    threading.Thread(target=gps_reader, args=(ser,), daemon=True).start()
    threading.Thread(target=ir_handler, daemon=True).start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ser.close()
        GPIO.cleanup()
        print("🛑 종료됨")
