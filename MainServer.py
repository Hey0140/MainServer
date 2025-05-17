from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from google.cloud.storage import Client, transfer_manager
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

bucket_name = os.getenv("BUCKET_NAME")

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
                       _: None = Depends(verify_api_key)):
    global image_path, shared_index, gender_value, session_id

    session_id = uuid.uuid4().hex[:6]
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    filename = f"{uuid.uuid4().hex[:3]}_{file.filename}"
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
    save_path = os.path.join(UPLOAD_FOLDER, session_id, result_filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"[+] Video name: {result_filename}")
    print(f"[+] Saved processed video to: {save_path}")
    print(f"[+] check the session_id : {session_id}")

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

def upload_many_blobs_with_transfer_manager(
    bucket_name, filenames, source_directory="", workers=8
):
    """Upload every file in a list to a bucket, concurrently in a process pool.

    Each blob name is derived from the filename, not including the
    `source_directory` parameter. For complete control of the blob name for each
    file (and other aspects of individual blob metadata), use
    transfer_manager.upload_many() instead.
    """

    # The ID of your GCS bucket
    #bucket_name = "your-bucket-name"

    # A list (or other iterable) of filenames to upload.
    #filenames = ["file_1.txt", "file_2.txt"]

    # The directory on your computer that is the root of all of the files in the
    # list of filenames. This string is prepended (with os.path.join()) to each
    # filename to get the full path to the file. Relative paths and absolute
    # paths are both accepted. This string is not included in the name of the
    # uploaded blob; it is only used to find the source files. An empty string
    # means "the current working directory". Note that this parameter allows
    # directory traversal (e.g. "/", "../") and is not intended for unsanitized
    # end user input.
    #source_directory=""

    # The maximum number of processes to use for the operation. The performance
    # impact of this value depends on the use case, but smaller files usually
    # benefit from a higher number of processes. Each additional process occupies
    # some CPU and memory resources until finished. Threads can be used instead
    # of processes by passing `worker_type=transfer_manager.THREAD`.
    #workers=8



    storage_client = Client()
    bucket = storage_client.bucket(bucket_name)

    results = transfer_manager.upload_many_from_filenames(
        bucket, filenames, source_directoy=source_directory, max_workers=workers
    )

    for name, result in zip(filenames, results):
        # The results list is either `None` or an exception for each filename in
        # the input list, in order.

        if isinstance(result, Exception):
            print("Failed to upload {} due to exception: {}".format(name, result))
        else:
            print("Uploaded {} to {}.".format(name, bucket.name))