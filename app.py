from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from threading import Thread

app = FastAPI(title="Earl Store", description="Temporary file host with 7-day retention.")

# Malaysian Timezone (UTC+8)
MYT = timezone(timedelta(hours=8))

def get_now_myt():
    return datetime.now(MYT)

@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": str(e)})

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
    if not os.path.exists(os.path.join(DATA_DIR, ".git")):
        try:
            if os.getenv("PRIVATE_REPO_URL"):
                subprocess.run(["git", "clone", os.getenv("PRIVATE_REPO_URL"), DATA_DIR])
        except Exception as e:
            print(f"Startup clone error: {e}")

def git_sync():
    """Sync changes to private repo."""
    try:
        subprocess.run(["git", "add", "."], cwd=DATA_DIR)
        subprocess.run(["git", "commit", "-m", f"Sync: {get_now_myt().isoformat()}"], cwd=DATA_DIR)
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
    # Add human readable dates in MYT before saving
    for code in data:
        try:
            t = datetime.fromisoformat(data[code]["time"])
            e = datetime.fromisoformat(data[code]["expires"])
            data[code]["time_human"] = t.strftime("%b %d, %Y, %I:%M %p")
            data[code]["expires_human"] = e.strftime("%b %d, %Y, %I:%M %p")
        except:
            pass
    with open(METADATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    now = get_now_myt()
    timestamp = int(now.timestamp())
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    metadata = load_metadata()
    metadata[filename] = {
        "name": file.filename,
        "ip": request.client.host,
        "time": now.isoformat(),
        "expires": (now + timedelta(days=7)).isoformat(),
        "size": os.path.getsize(file_path)
    }
    save_metadata(metadata)
    
    Thread(target=git_sync).start()
    
    host = request.headers.get("host", "temp.earlstore.online")
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
        
        # Update expiration in MYT
        new_expiry = (get_now_myt() + timedelta(days=7)).isoformat()
        metadata[filename]["expires"] = new_expiry
        save_metadata(metadata)
        Thread(target=git_sync).start()

        ext = os.path.splitext(original_name)[1].lower()
        image_exts = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"]
        video_exts = [".mp4", ".webm", ".ogg", ".mov", ".mkv"]
        
        if ext in image_exts or ext in video_exts:
            return FileResponse(path=file_path, filename=original_name, content_disposition_type="inline")
        else:
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
            :root {{ --bg: #ffffff; --text: #000000; --muted: #666666; --border: #eeeeee; --accent: #ff3e00; }}
            @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0b0b0b; --text: #f0f0f0; --muted: #888888; --border: #222222; --accent: #ff3e00; }} }}
            body {{ font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 2.5rem; font-weight: 700; margin-bottom: 2rem; }}
            .box {{ border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 1.5rem; background: #fafafa; }}
            pre {{ background: #000; padding: 1rem; border-radius: 8px; overflow-x: auto; font-size: 0.9rem; color: #fff; margin: 0.5rem 0; }}
            .row {{ display: flex; justify-content: space-between; align-items: center; }}
            .copy-btn {{ background: var(--text); color: var(--bg); border: none; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; }}
            .back {{ text-decoration: none; color: var(--muted); font-size: 0.9rem; display: block; margin-bottom: 1rem; font-weight: 700; }}
        </style>
    </head>
    <body>
        <a href="/" class="back">← HOME</a>
        <h1>API DOC</h1>
        <div class="box">Endpoint: <code>POST {base_url}/api/upload</code><br>Field Name: <code>file</code></div>
        
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
                const btn = event.target;
                btn.innerText = 'COPIED';
                setTimeout(() => btn.innerText = 'COPY', 2000);
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=doc_content)

@app.post("/admin/login")
async def admin_login(data: dict):
    password = data.get("password")
    env_pass = os.getenv("ADMIN_PASSWORD")
    if not env_pass:
        raise HTTPException(status_code=500, detail="Admin password not set in server.")
    if password == env_pass:
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/admin/data")
async def admin_data(password: str):
    if password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)
    
    metadata = load_metadata()
    total_files = len(metadata)
    total_size = sum(info.get("size", 0) for info in metadata.values())
    
    # Format size
    for unit in ['B', 'KB', 'MB', 'GB']:
        if total_size < 1024:
            size_str = f"{total_size:.2f} {unit}"
            break
        total_size /= 1024
    else:
        size_str = f"{total_size:.2f} TB"

    return {
        "total_files": total_files,
        "total_size": size_str,
        "files": metadata
    }

@app.post("/admin/delete")
async def admin_delete(data: dict):
    if data.get("password") != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)
    
    filenames = data.get("filenames", [])
    metadata = load_metadata()
    deleted = []
    
    for filename in filenames:
        if filename in metadata:
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(file_path):
                os.remove(file_path)
            del metadata[filename]
            deleted.append(filename)
    
    save_metadata(metadata)
    Thread(target=git_sync).start()
    return {"status": "success", "deleted": deleted}
