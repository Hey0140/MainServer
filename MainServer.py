from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from google.cloud.storage import Client, transfer_manager
from google.cloud import storage
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
import uuid
import shutil
import os
import httpx
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()

AI_SERVER_IPS = {
    0: os.getenv("AI_SERVER_0"),
    1: os.getenv("AI_SERVER_1"),
    2: os.getenv("AI_SERVER_2"),
}

UPLOAD_FOLDER = "uploads/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# 전역 상태 저장
image_path = ""
gender_value = 0
MAX_INDEX = 8
session_id = 0 #전역변수

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
GCS_CREDENTIAL_JSON = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")  # json 경로

shared_index = 0
shared_index_lock = asyncio.Lock()

def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-KEY")
    expected_key = os.getenv("API_KEY")
    if api_key != expected_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

@app.post("/upload_image/")
async def upload_image(file: UploadFile = File(...),
                        gender : int = Form(...),
                        sid: int = Form(...),
                       _: None = Depends(verify_api_key)):
    global image_path, shared_index, gender_value, session_id

    session_id = str(sid)
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    filename = f"{uuid.uuid4().hex[:4]}_picture.png"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    image_path = save_path
    gender_value = gender
    shared_index = 0

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Saved uploaded image to: {save_path}")
    print(f"[+] gender: {gender_value}  => 1 : man, 2 : woman ")
    print(f"[+] session_id : {session_id}")

    # 초기 작업: AI 서버마다 하나씩 보내기
    tasks = []
    async with shared_index_lock:
        for server_id in AI_SERVER_IPS:
            if shared_index >= MAX_INDEX:
                break
            tasks.append(send_task_to_ai_server(server_id))


    await asyncio.gather(*tasks)

    return {"session_id" : session_id, "message": "Initial jobs sent to AI servers."}

@app.post("/upload_result/")
async def upload_result(file: UploadFile = File(...),
                        request: Request = None,
                        _: None = Depends(verify_api_key)):
    global session_id
    result_filename = f"result_{file.filename}"
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    save_path = os.path.join(session_folder, result_filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Video name: {result_filename}")
    print(f"[+] Saved processed video to: {save_path}")
    print(f"[+] check the session_id : {session_id}")

    file_list = os.listdir(session_folder)
    result_files = [f for f in file_list if f.endswith(".mp4")]
    print(f"[📁] Current processed videos: {len(result_files)} / {MAX_INDEX}")

    if len(result_files) == MAX_INDEX:
        print(f"[🚀] All {MAX_INDEX} videos received. Uploading to GCS...")

        local_session_id = session_id
        local_session_folder = os.path.join(UPLOAD_FOLDER, local_session_id)
        local_result_files = list(result_files)

        async def upload_all_to_gcs():
            for f in local_result_files:
                local_path = os.path.join(local_session_folder, f)
                gcs_path = f"{local_session_id}/{f}"
                upload_to_gcs(local_path, gcs_path)
                print(f"[☁️] Uploaded {f} to GCS: {gcs_path}")

        asyncio.create_task(upload_all_to_gcs())

    # 서버 ID 추정 후 다음 작업 전달
    client_ip = request.client.host
    server_id = None
    for sid, ip in AI_SERVER_IPS.items():
        if client_ip == ip:
            server_id = sid
            print(f"[🔄] Request came from AI server {server_id} ({client_ip})")
            break
    else:
        print(f"[⚠️] Unknown AI server IP: {client_ip}")

    reponse_message = {"message": f"Result video saved as {result_filename}"}
    if server_id is not None:
        asyncio.create_task(send_task_to_ai_server(server_id))

    return reponse_message

async def send_task_to_ai_server(server_id):
    global shared_index, gender_value

    async with shared_index_lock:
        if shared_index >= MAX_INDEX:
            print(f"[🚫] No more jobs. Sending DONE signal to AI server {server_id}")
            await send_done_signal_to_ai_server(server_id)
            return

        index = shared_index
        shared_index += 1

    async with httpx.AsyncClient(timeout=240.0) as client:
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "image/png")}
            data = {"index": str(index), "gender": str(gender_value)}
            headers = {"X-API-KEY": os.getenv("API_KEY")}
            url = f"http://{AI_SERVER_IPS[server_id]}:8001/run_ai/"
            response = await client.post(url, files=files, data=data, headers=headers)

    print(f"[+] Sent index {index} to AI server {server_id}, status: {response.status_code}")

async def send_done_signal_to_ai_server(server_id):
    async with httpx.AsyncClient() as client:
        headers = {"X-API-KEY": os.getenv("API_KEY")}
        data = {"index": "-1", "gender": str(gender_value)}  # 끝났음을 의미
        url = f"http://{AI_SERVER_IPS[server_id]}:8001/run_ai/"
        response = await client.post(url, data=data, headers=headers)
        print(f"[📴] Sent DONE signal to AI server {server_id}, status: {response.status_code}")

def upload_to_gcs(local_file_path: str, gcs_path: str) -> str:
    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(local_file_path)

    # URL 공개 설정 (필요 시)
    public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{gcs_path}"
    return public_url

