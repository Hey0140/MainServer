from fastapi import FastAPI, UploadFile, File, Form

app = FastAPI()

@app.post("/process/")
async def process_test(text: str = Form(...)):
    print(f"Received text: {text}")
    return {"message": f"Server received your text: {text}"}
