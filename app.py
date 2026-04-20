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
        # Get original filename if available, else use current filename
        original_name = metadata.get(filename, {}).get("name", filename)
        return FileResponse(path=file_path, filename=original_name, content_disposition_type="attachment")
    raise HTTPException(status_code=404, detail="File expired or not found.")

@app.get("/doc", response_class=HTMLResponse)
async def documentation(request: Request):
    doc_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>API Documentation - Earl Store</title>
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root { --bg: #ffffff; --text: #000000; --muted: #666666; --border: #e5e5e5; --code-bg: #f5f5f5; }
            @media (prefers-color-scheme: dark) { :root { --bg: #000000; --text: #ffffff; --muted: #888888; --border: #333333; --code-bg: #111111; } }
            body { font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; max-width: 900px; margin: 0 auto; }
            h1 { font-size: 2.5rem; margin-bottom: 1rem; }
            h2 { font-size: 1.5rem; margin-top: 2rem; border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; }
            code { background: var(--code-bg); padding: 0.2rem 0.4rem; border-radius: 4px; font-family: monospace; }
            pre { background: var(--code-bg); padding: 1rem; border-radius: 8px; overflow-x: auto; border: 1px solid var(--border); margin: 1rem 0; }
            .example-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; }
            @media (max-width: 768px) { .example-grid { grid-template-columns: 1fr; } }
            .back-link { display: inline-block; margin-bottom: 2rem; text-decoration: none; color: var(--text); font-weight: 700; }
        </style>
    </head>
    <body>
        <a href="/" class="back-link">← BACK TO HOME</a>
        <h1>API Documentation</h1>
        <p>Earl Store provides a simple REST API to upload files programmatically. All files are kept for 7 days.</p>

        <h2>1. Upload Endpoint</h2>
        <p><code>POST https://temp.earlstore.online/api/upload</code></p>
        <p>The body must be <code>multipart/form-data</code> with a <code>file</code> field.</p>

        <h2>2. CURL Examples (All Formats)</h2>
        <div class="example-grid">
            <div>
                <p><b>Images (PNG, JPG, GIF)</b></p>
                <pre>curl -F "file=@photo.png" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
            <div>
                <p><b>Videos (MP4, MKV, MOV)</b></p>
                <pre>curl -F "file=@video.mp4" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
            <div>
                <p><b>Apps & Packages (APK, IPA, EXE)</b></p>
                <pre>curl -F "file=@app.apk" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
            <div>
                <p><b>Archives (ZIP, RAR, 7Z)</b></p>
                <pre>curl -F "file=@data.zip" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
            <div>
                <p><b>Documents (PDF, DOCX, TXT)</b></p>
                <pre>curl -F "file=@info.pdf" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
            <div>
                <p><b>Scripts (PY, JS, PHP)</b></p>
                <pre>curl -F "file=@script.py" \\
https://temp.earlstore.online/api/upload</pre>
            </div>
        </div>

        <h2>3. Response Format</h2>
        <pre>{
  "url": "https://temp.earlstore.online/d/123456789_file.ext"
}</pre>

        <footer style="margin-top: 4rem; color: var(--muted); font-size: 0.8rem;">
            &copy; 2026 Earl Store. Security & Speed.
        </footer>
    </body>
    </html>
    """
    return HTMLResponse(content=doc_content)
