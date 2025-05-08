# MainServer.py
from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import uuid
import shutil
import os
import httpx

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 또는 ["http://localhost:3000"] 같이 특정 origin만
    allow_credentials=True,
    allow_methods=["*"],  # OPTIONS, POST, GET 등 전부 허용
    allow_headers=["*"],  # X-API-KEY 포함
)

load_dotenv()

MAIN_SERVER_IP_URL = os.getenv("MAIN_SERVER_IP_URL")

# 저장 경로
UPLOAD_FOLDER = "uploads/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# AI 서버 URL
if MAIN_SERVER_IP_URL is None:
    raise ValueError("MAIN_SERVER_IP_URL 환경변수가 설정되지 않았습니다.")

AI_SERVER_URL = f"http://{MAIN_SERVER_IP_URL}:8001/run_ai/"  # AI 서버의 run_ai API 주소
# (포트 8001 예시, 실제 포트 확인!가능)

def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-KEY")
    expected_key = os.getenv("API_KEY")

    if api_key != expected_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

@app.post("/upload_image/")
async def upload_image(file: UploadFile = File(...),
                       is_female: bool = Form(...),
                       _: None = Depends(verify_api_key)):
    # (1) 업로드된 파일을 임시로 저장
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Saved uploaded image to: {save_path}")

    # (2) 저장한 이미지를 AI 서버로 보내기
    async with httpx.AsyncClient() as client:
        with open(save_path, "rb") as f:
            form_data = httpx.MultipartData(
                files={"file": (filename, f, "image/png")},
                data={"is_female": str(is_female).lower()}
            )
            headers = {"X-API-KEY": os.getenv("API_KEY")}
            response = await client.post(AI_SERVER_URL, files=files, headers=headers)

    print(f"[+] Sent image to AI server, status code: {response.status_code}")
    return {"message": "Image received and forwarded to AI server."}

@app.post("/upload_result/")
async def upload_result(file: UploadFile = File(...),
                        _: None = Depends(verify_api_key)):
    # AI 서버에서 결과 동영상이 돌아오는 엔드포인트
    filename = f"result_{uuid.uuid4().hex}.mp4"
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Saved processed video to: {save_path}")
    return {"message": f"Result video saved as {filename}"}
