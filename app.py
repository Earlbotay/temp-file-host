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

# Persistence Configuration
DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
PRIVATE_REPO_URL = os.getenv("PRIVATE_REPO_URL") # To be set in GH Secrets

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def startup_event():
    """Ensure data repo is ready on startup."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs("static", exist_ok=True)
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
    return templates.TemplateResponse(request=request, name="index.html")

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
        metadata = load_metadata()
        file_info = metadata.get(filename, {})
        original_name = file_info.get("name", filename)
        
        # Update expiration time based on last access (Slide 7 days forward)
        new_expiry = (datetime.now() + timedelta(days=7)).isoformat()
        metadata[filename]["expires"] = new_expiry
        save_metadata(metadata)
        # Background sync
        Thread(target=git_sync).start()

        # Determine MIME type to decide between Inline or Attachment
        ext = os.path.splitext(original_name)[1].lower()
        image_exts = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"]
        video_exts = [".mp4", ".webm", ".ogg", ".mov", ".mkv"]
        
        if ext in image_exts or ext in video_exts:
            # Display in browser (Inline)
            return FileResponse(path=file_path, filename=original_name, content_disposition_type="inline")
        else:
            # Force download for other files (APK, ZIP, etc)
            return FileResponse(path=file_path, filename=original_name, content_disposition_type="attachment")
            
    raise HTTPException(status_code=404, detail="File expired or not found.")

@app.get("/doc", response_class=HTMLResponse)
async def documentation(request: Request):
    host = request.headers.get("host", "temp.earlstore.online")
    protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{protocol}://{host}"

    doc_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Docs - Earl Store</title>
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
        <script src="https://unpkg.com/lucide@latest"></script>
        <style>
            :root {{ --bg: #0b0b0b; --text: #f0f0f0; --muted: #888888; --border: #222222; --accent: #ff3e00; }}
            body {{ font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 2rem; margin-bottom: 2rem; }}
            .box {{ border: 1px solid var(--border); padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }}
            pre {{ background: #111; padding: 0.8rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; color: var(--accent); margin: 0.5rem 0; }}
            .row {{ display: flex; justify-content: space-between; align-items: center; }}
            .copy-btn {{ background: var(--text); color: var(--bg); border: none; padding: 0.3rem 0.6rem; border-radius: 4px; cursor: pointer; font-size: 0.7rem; font-weight: 700; }}
            .back {{ text-decoration: none; color: var(--muted); font-size: 0.9rem; display: block; margin-bottom: 1rem; }}
        </style>
    </head>
    <body>
        <a href="/" class="back">← HOME</a>
        <h1>API Usage</h1>
        <div class="box">Endpoint: <code>POST {base_url}/api/upload</code> (field: <code>file</code>)</div>
        
        <div class="box">
            <div class="row"><b>IMAGE</b> <button class="copy-btn" onclick="copy('c1')">COPY</button></div>
            <pre id="c1">curl -F "file=@p.png" {base_url}/api/upload</pre>
        </div>
        <div class="box">
            <div class="row"><b>VIDEO</b> <button class="copy-btn" onclick="copy('c2')">COPY</button></div>
            <pre id="c2">curl -F "file=@v.mp4" {base_url}/api/upload</pre>
        </div>
        <div class="box">
            <div class="row"><b>FILE (APK/ZIP)</b> <button class="copy-btn" onclick="copy('c3')">COPY</button></div>
            <pre id="c3">curl -F "file=@a.apk" {base_url}/api/upload</pre>
        </div>

        <script>
            function copy(id) {{
                navigator.clipboard.writeText(document.getElementById(id).innerText);
                event.target.innerText = 'COPIED';
                setTimeout(() => event.target.innerText = 'COPY', 2000);
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=doc_content)
