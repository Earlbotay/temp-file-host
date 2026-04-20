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
        # Get original filename if available, else use current filename
        original_name = metadata.get(filename, {}).get("name", filename)
        return FileResponse(path=file_path, filename=original_name, content_disposition_type="attachment")
    raise HTTPException(status_code=404, detail="File expired or not found.")

@app.get("/doc", response_class=HTMLResponse)
async def documentation(request: Request):
    # Dynamic domain based on request headers (Cloudflare domain)
    host = request.headers.get("host", "temp.earlstore.online")
    protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{protocol}://{host}"

    doc_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>API Documentation - Earl Store</title>
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <script src="https://unpkg.com/lucide@latest"></script>
        <style>
            :root {{ --bg: #ffffff; --text: #000000; --muted: #666666; --border: #e5e5e5; --code-bg: #f9f9f9; --accent: #ff3e00; }}
            @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0b0b0b; --text: #f0f0f0; --muted: #888888; --border: #222222; --code-bg: #111111; }} }}
            body {{ font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; max-width: 1000px; margin: 0 auto; }}
            h1 {{ font-size: 3rem; font-weight: 700; margin-bottom: 2rem; text-align: center; }}
            h2 {{ font-size: 1.25rem; margin-top: 3rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); }}
            .container {{ display: grid; grid-template-columns: 1fr; gap: 2rem; }}
            .box {{ background: var(--bg); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; transition: border-color 0.2s; }}
            .box:hover {{ border-color: var(--text); }}
            .code-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
            .format-tag {{ font-weight: 700; color: var(--accent); font-size: 0.9rem; }}
            pre {{ background: var(--code-bg); padding: 1rem; border-radius: 8px; overflow-x: auto; margin: 0; font-family: monospace; font-size: 0.95rem; position: relative; }}
            .copy-btn {{ background: var(--text); color: var(--bg); border: none; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; display: flex; align-items: center; gap: 0.4rem; }}
            .copy-btn:hover {{ opacity: 0.9; }}
            .back-link {{ display: inline-flex; align-items: center; gap: 0.5rem; text-decoration: none; color: var(--text); font-weight: 700; margin-bottom: 2rem; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(450px, 1fr)); gap: 1.5rem; }}
            @media (max-width: 600px) {{ .grid {{ grid-template-columns: 1fr; }} }}
        </style>
    </head>
    <body>
        <a href="/" class="back-link"><i data-lucide="arrow-left" size="18"></i> HOME</a>
        <h1>API Docs</h1>
        
        <div class="container">
            <div class="box" style="text-align: center;">
                <p>Endpoint: <code>POST {base_url}/api/upload</code></p>
                <p>Form-Data field: <code>file</code></p>
            </div>

            <div class="grid">
                <!-- Image Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">IMAGES</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@photo.png" {base_url}/api/upload</pre>
                </div>

                <!-- Video Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">VIDEO</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@video.mp4" {base_url}/api/upload</pre>
                </div>

                <!-- APK Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">APPS (APK/IPA)</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@app.apk" {base_url}/api/upload</pre>
                </div>

                <!-- ZIP Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">ARCHIVE (ZIP/RAR)</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@data.zip" {base_url}/api/upload</pre>
                </div>

                <!-- Document Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">DOCUMENTS</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@file.pdf" {base_url}/api/upload</pre>
                </div>

                <!-- Script Example -->
                <div class="box">
                    <div class="code-header">
                        <span class="format-tag">SCRIPTS</span>
                        <button class="copy-btn" onclick="copyCode(this)">COPY CURL</button>
                    </div>
                    <pre>curl -F "file=@script.sh" {base_url}/api/upload</pre>
                </div>
            </div>
        </div>

        <script>
            lucide.createIcons();
            function copyCode(btn) {{
                const pre = btn.parentElement.nextElementSibling;
                navigator.clipboard.writeText(pre.innerText).then(() => {{
                    const originalText = btn.innerHTML;
                    btn.innerHTML = 'COPIED!';
                    setTimeout(() => btn.innerHTML = originalText, 2000);
                }});
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=doc_content)
