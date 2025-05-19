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
            print("❌ GPS 데이터가 유효하지 않음")
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

# === 4. GPS 읽기 스레드 ===
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

# === 공통 기능 ===
def capture_image_bytes():
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    if ret:
        success, buffer = cv2.imencode('.jpg', frame)
        if success:
            return io.BytesIO(buffer.tobytes())
    return None

def play_mp3_binary(mp3_bytes):
    try:
        audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        play(audio)
    except Exception as e:
        print(f"❌ MP3 바이너리 재생 실패: {e}")



# === 5. IR 핸들러 ===
def ir_handler():
    press_count = 0
    last_press_time = 0
    timeout = 0.8
    
    while True:
        # 버튼이 눌린 경우
        if GPIO.input(IR_PIN) == 0:
            now = time.time()
            
            # 새로운 입력 시작
            if now - last_press_time > timeout:
                press_count = 1
            else:
                # 연속 입력
                press_count += 1
            
            # 마지막 입력 시간 갱신
            last_press_time = now
        
        # 타임아웃 체크
        elif press_count > 0 and time.time() - last_press_time > timeout:
            # 🔴 최종 패턴 처리
            handle_key_pattern(press_count)
            press_count = 0
        
        time.sleep(0.05)



def describe_landscape():
    img_bytes = capture_image_bytes()
    with location_lock:
        lat, lon = latest_location['lat'], latest_location['lon']
    
    if not img_bytes:
        print("⚠️ 이미지 캡처 실패")
        return
     
    if lat is None or lon is None:
        print("⚠️ GPS 좌표 없음")
        return
     
    try:
        files = {'image': ('capture.jpg', img_bytes, 'image/jpeg')}
        data = {'device_id': DEVICE_ID}
        res = requests.post(f"{SERVER_BASE_URL}/auto_describe", files=files, data=data)
        if res.status_code == 200:
            play_mp3_binary(res.content)
        else:
            print(f"⚠️ 서버 오류: {res.status_code}")
    except Exception as e:
        print(f"❌ 요청 실패: {e}")


# === 프롬프트 응답 ===
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

# === 프롬프트 처리 ===
def respond_to_prompt():
    img_bytes = capture_image_bytes()
    if not img_bytes:
        print("⚠️ 이미지 캡처 실패")
        return
     
    prompt_text = recognize_speech()
    if not prompt_text:
        print("⚠️ 프롬프트가 비어 있어 전송하지 않음")
        return
     
    try:
        files = {'image': ('capture.jpg', img_bytes, 'image/jpeg')}
        data = {'device_id': DEVICE_ID, 'prompt': prompt_text}
        res = requests.post(f"{SERVER_BASE_URL}/user_qa", files=files, data=data)
        if res.status_code == 200:
            play_mp3_binary(res.content)
        else:
            print(f"⚠️ 서버 오류: {res.status_code}")
    except Exception as e:
        print(f"❌ 요청 실패: {e}")


# === 긴급 ID 요청 및 이미지 전송 ===
def handle_emergency():
    emergency_id = get_emergency_id()
    if emergency_id:
        burst_send_images(emergency_id)
    else:
        print("❌ 긴급 ID 요청 실패")


# === 6. 키 패턴 처리 ===
def handle_key_pattern(press_count):
    print(f"🔑 버튼 {press_count}회")
    if press_count == 1:
        describe_landscape()
    elif press_count == 2:
        respond_to_prompt()
    elif press_count == 5:
        handle_emergency()
    else:
        print(f"❓ 미정의 입력: {press_count}회")



# === 8. HTTP 통신 ===
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
        res = requests.post(f"{SERVER_BASE_URL}/get_emergency_id", json={'device_id': DEVICE_ID})
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


# === 9. 메인 실행부 ===
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
