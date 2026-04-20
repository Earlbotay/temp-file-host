from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from threading import Thread

app = FastAPI(title="Earl Store", description="Temporary file host with 7-day retention.")

@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": str(e), "traceback": traceback.format_exc()})

# Persistence Configuration
DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
PRIVATE_REPO_URL = os.getenv("PRIVATE_REPO_URL") # To be set in GH Secrets

os.makedirs(UPLOAD_DIR, exist_ok=True)
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def startup_event():
    """Ensure data repo is ready on startup."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    print(f"Current working directory: {os.getcwd()}")
    if os.path.exists("templates/index.html"):
        print("Template index.html found.")
    else:
        print("CRITICAL: templates/index.html MISSING!")
    
    if not os.path.exists(os.path.join(DATA_DIR, ".git")):
        try:
            # Re-clone if data folder is empty/invalid
            if os.getenv("PRIVATE_REPO_URL"):
                subprocess.run(["git", "clone", os.getenv("PRIVATE_REPO_URL"), DATA_DIR])
        except Exception as e:
            print(f"Startup clone error: {e}")

def git_sync():
    """Sync changes to private repo."""
    try:
        subprocess.run(["git", "add", "."], cwd=DATA_DIR)
        subprocess.run(["git", "commit", "-m", f"Sync: {datetime.now().isoformat()}"], cwd=DATA_DIR)
        subprocess.run(["git", "push", "origin", "main"], cwd=DATA_DIR)
    except Exception as e:
        print(f"Git sync error: {e}")

def load_metadata():
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_metadata(data):
    with open(METADATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    timestamp = int(datetime.now().timestamp())
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    metadata = load_metadata()
    metadata[filename] = {
        "name": file.filename,
        "ip": request.client.host,
        "time": datetime.now().isoformat(),
        "expires": (datetime.now() + timedelta(days=7)).isoformat(),
        "size": os.path.getsize(file_path)
    }
    save_metadata(metadata)
    
    # Sync in background
    Thread(target=git_sync).start()
    
    host = request.headers.get("host", "localhost:8080")
    protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
    return {"url": f"{protocol}://{host}/d/{filename}"}

@app.get("/api/list")
def list_files():
    return load_metadata()

@app.get("/d/{filename}")
def download_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename=filename, content_disposition_type="attachment")
    raise HTTPException(status_code=404, detail="File expired or not found.")

@app.get("/doc", response_class=HTMLResponse)
async def documentation(request: Request):
    doc_content = """
    <html>
    <head><title>API Documentation - Earl Store</title><style>body{font-family:sans-serif;padding:2rem;line-height:1.6;}</style></head>
    <body>
    <h1>Earl Store API Documentation</h1>
    <h3>1. Upload File</h3>
    <p><b>Endpoint:</b> <code>POST /api/upload</code></p>
    <p><b>Body:</b> multipart/form-data with <code>file</code> field.</p>
    
    <h3>2. List Files</h3>
    <p><b>Endpoint:</b> <code>GET /api/list</code></p>
    
    <h3>3. Download File</h3>
    <p><b>Endpoint:</b> <code>GET /d/{filename}</code></p>
    
    <hr>
    <p>All files are deleted after 7 days. Supports all formats (APK, ZIP, MP4, etc).</p>
    <p>Created by <b>Earl Store</b></p>
    </body>
    </html>
    """
    return HTMLResponse(content=doc_content)
