# MainServer.py
from fastapi import FastAPI, UploadFile, File
from dotenv import load_dotenv
import uuid
import shutil
import os
import httpx

app = FastAPI()

MAIN_SERVER_IP_URL = os.getenv("MAIN_SERVER_IP_URL")

# 저장 경로
UPLOAD_FOLDER = "uploads/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# AI 서버 URL
if MAIN_SERVER_IP_URL is None:
    raise ValueError("MAIN_SERVER_IP_URL 환경변수가 설정되지 않았습니다.")

AI_SERVER_URL = "http://"+MAIN_SERVER_IP_URL+":8001/run_ai/"  # AI 서버의 run_ai API 주소
# (포트 8001 예시, 실제 포트 확인!)

@app.post("/upload_image/")
async def upload_image(file: UploadFile = File(...)):
    # (1) 업로드된 파일을 임시로 저장
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Saved uploaded image to: {save_path}")

    # (2) 저장한 이미지를 AI 서버로 보내기
    async with httpx.AsyncClient() as client:
        with open(save_path, "rb") as f:
            files = {'file': (filename, f, 'image/png')}
            response = await client.post(AI_SERVER_URL, files=files)

    print(f"[+] Sent image to AI server, status code: {response.status_code}")
    return {"message": "Image received and forwarded to AI server."}

@app.post("/upload_result/")
async def upload_result(file: UploadFile = File(...)):
    # AI 서버에서 결과 동영상이 돌아오는 엔드포인트
    filename = f"result_{uuid.uuid4().hex}.mp4"
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Saved processed video to: {save_path}")
    return {"message": f"Result video saved as {filename}"}
