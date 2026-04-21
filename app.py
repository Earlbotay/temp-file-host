from fastapi import FastAPI, File, UploadFile, Request, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import os
import json
import shutil
import subprocess
import base64
import httpx
import time
from datetime import datetime, timedelta, timezone
from threading import Thread, Lock

app = FastAPI(title="Earl File", description="Temporary file host with 7-day retention.")

# Global Lock for metadata updates to prevent local race conditions
metadata_lock = Lock()

# Malaysian Timezone (UTC+8)
MYT = timezone(timedelta(hours=8))

def get_now_myt():
    return datetime.now(MYT)

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)

# Persistence Configuration
DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
PRIVATE_REPO_URL = os.getenv("PRIVATE_REPO_URL")

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
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "w") as f:
            json.dump({}, f)

def get_repo_info():
    if not PRIVATE_REPO_URL: return None, None, None
    try:
        url_part = PRIVATE_REPO_URL.replace("https://", "").split("@")
        token = url_part[0]
        repo_full = url_part[1].replace("github.com/", "").replace(".git", "")
        owner, repo_name = repo_full.split("/")
        return token, owner, repo_name
    except:
        return None, None, None

def github_data_api_push(target_file: str, content_bytes: bytes = None):
    """
    Advanced Git Data API - Supports up to 100MB.
    Bypasses the 25MB limit of the standard REST API.
    Supports RAM content directly.
    """
    try:
        token, owner, repo_name = get_repo_info()
        if not token: return

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # If content_bytes is not provided, read from local file
        if content_bytes is None:
            local_path = os.path.join(DATA_DIR, target_file)
            if not os.path.exists(local_path): return
            with open(local_path, "rb") as f:
                content_bytes = f.read()

        content_base64 = base64.b64encode(content_bytes).decode("utf-8")

        with httpx.Client(timeout=120.0) as client:
            # 1. Create Blob (Supports up to 100MB)
            blob_resp = client.post(
                f"https://api.github.com/repos/{owner}/{repo_name}/git/blobs",
                headers=headers,
                json={"content": content_base64, "encoding": "base64"}
            )
            blob_data = blob_resp.json()
            blob_sha = blob_data.get("sha")
            if not blob_sha:
                print(f"Blob Error: {blob_data}")
                return

            # Retry loop for HEAD conflict (if multiple people push at once)
            for attempt in range(5):
                ref_resp = client.get(f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/heads/main", headers=headers)
                last_commit_sha = ref_resp.json()["object"]["sha"]

                tree_resp = client.post(
                    f"https://api.github.com/repos/{owner}/{repo_name}/git/trees",
                    headers=headers,
                    json={
                        "base_tree": last_commit_sha,
                        "tree": [{"path": target_file, "mode": "100644", "type": "blob", "sha": blob_sha}]
                    }
                )
                new_tree_sha = tree_resp.json()["sha"]

                commit_resp = client.post(
                    f"https://api.github.com/repos/{owner}/{repo_name}/git/commits",
                    headers=headers,
                    json={
                        "message": f"Sync {target_file}: {get_now_myt().isoformat()}",
                        "tree": new_tree_sha,
                        "parents": [last_commit_sha]
                    }
                )
                new_commit_sha = commit_resp.json()["sha"]

                patch_resp = client.patch(
                    f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/heads/main",
                    headers=headers,
                    json={"sha": new_commit_sha}
                )
                
                if patch_resp.status_code == 200:
                    break
                else:
                    time.sleep(1)

    except Exception as e:
        print(f"Git Data API Error [{target_file}]: {e}")

def git_sync(target_file: str, content_bytes: bytes = None):
    """Async wrapper for sync using GitHub Data API."""
    Thread(target=github_data_api_push, args=(target_file, content_bytes)).start()

def load_metadata():
    with metadata_lock:
        if os.path.exists(METADATA_FILE):
            try:
                with open(METADATA_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

def save_metadata(data):
    with metadata_lock:
        for code in data:
            try:
                t_str = data[code]["time"]
                e_str = data[code]["expires"]
                t = datetime.fromisoformat(t_str.split('+')[0])
                e = datetime.fromisoformat(e_str.split('+')[0])
                data[code]["time_human"] = t.strftime("%b %d, %Y, %I:%M %p")
                data[code]["expires_human"] = e.strftime("%b %d, %Y, %I:%M %p")
            except:
                pass
        with open(METADATA_FILE, "w") as f:
            json.dump(data, f, indent=4)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

CHUNK_DIR = os.path.join(DATA_DIR, "chunks")
os.makedirs(CHUNK_DIR, exist_ok=True)

@app.post("/api/upload")
async def upload_file(
    request: Request, 
    file: UploadFile = File(...),
    chunk_index: int = Form(None),
    total_chunks: int = Form(None),
    upload_id: str = Form(None)
):
    """
    Universal Upload Endpoint.
    Supports: Regular Upload (RAM-Optimized), Chunked Upload, and Bypass Mode.
    """
    try:
        now = get_now_myt()
        timestamp = int(now.timestamp())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._- "]).strip()
        if not safe_name: safe_name = "file"
        
        final_content = None

        # A. CHUNKED UPLOAD
        if chunk_index is not None and upload_id is not None:
            upload_temp_dir = os.path.join(CHUNK_DIR, upload_id)
            os.makedirs(upload_temp_dir, exist_ok=True)
            
            chunk_path = os.path.join(upload_temp_dir, f"chunk_{chunk_index}")
            with open(chunk_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            if chunk_index < total_chunks - 1:
                return {"status": "chunk_received", "chunk_index": chunk_index}
            
            # Assembly
            filename_to_save = f"{timestamp}_{safe_name}"
            file_path = os.path.join(UPLOAD_DIR, filename_to_save)
            
            with open(file_path, "wb") as final_file:
                for i in range(total_chunks):
                    cp = os.path.join(upload_temp_dir, f"chunk_{i}")
                    with open(cp, "rb") as f:
                        final_file.write(f.read())
            
            shutil.rmtree(upload_temp_dir)
            original_name = file.filename
            size = os.path.getsize(file_path)

        # B. REGULAR / SPEED UPLOAD (RAM-Optimized)
        else:
            filename_to_save = f"{timestamp}_{safe_name}"
            file_path = os.path.join(UPLOAD_DIR, filename_to_save)
            
            # Read directly to memory for speed sync
            final_content = await file.read()
            size = len(final_content)
            
            with open(file_path, "wb") as f:
                f.write(final_content)
            
            original_name = file.filename

        # Save Metadata and Sync
        metadata = load_metadata()
        metadata[filename_to_save] = {
            "name": original_name,
            "ip": request.client.host,
            "time": now.isoformat(),
            "expires": (now + timedelta(days=7)).isoformat(),
            "size": size
        }
        save_metadata(metadata)
        
        # Trigger GitHub Sync via Data API (Fastest)
        git_sync(f"uploads/{filename_to_save}", final_content)
        git_sync("metadata.json")
        
        host = request.headers.get("host", "temp.earlstore.online")
        protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
        return {"url": f"{protocol}://{host}/d/{filename_to_save}"}
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Upload failed: {str(e)}"})

@app.get("/d/{filename}")
def download_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    metadata = load_metadata()
    
    if filename not in metadata:
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass
        raise HTTPException(status_code=404, detail="File expired or not found.")
    
    if os.path.exists(file_path):
        file_info = metadata.get(filename)
        original_name = file_info.get("name", filename)
        
        # Sliding Expiry
        now = get_now_myt()
        metadata[filename]["time"] = now.isoformat()
        metadata[filename]["expires"] = (now + timedelta(days=7)).isoformat()
        save_metadata(metadata)
        git_sync("metadata.json")

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
        <title>API DOC - Earl File</title>
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {{ --bg: #ffffff; --text: #000000; --muted: #666666; --border: #eeeeee; --accent: #ff3e00; --speed: #00ff00; }}
            body {{ font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 2.5rem; font-weight: 700; margin-bottom: 2rem; }}
            .box {{ border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 1.5rem; background: #fafafa; }}
            pre {{ background: #000; padding: 1rem; border-radius: 8px; overflow-x: auto; font-size: 0.9rem; color: #fff; margin: 0.5rem 0; }}
            .row {{ display: flex; justify-content: space-between; align-items: center; }}
            .copy-btn {{ background: var(--text); color: var(--bg); border: none; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; }}
            .badge {{ background: var(--speed); color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700; vertical-align: middle; }}
        </style>
    </head>
    <body>
        <a href="/" style="text-decoration:none; color:var(--muted); font-weight:700;">← HOME</a>
        <h1>API DOC</h1>

        <div class="box" style="border-left: 4px solid var(--speed);">
            <h3>UNIVERSAL ENDPOINT <span class="badge">SPEED MODE ENABLED</span></h3>
            <p style="font-size: 0.9rem; color: var(--muted);">This endpoint automatically detects upload type and uses GitHub Data API for 100MB RAM-optimized sync.</p>
            <code>POST {base_url}/api/upload</code><br><br>

            <div class="row"><b>1. SPEED Muat Naik (RAM)</b> <button class="copy-btn" onclick="copy('c1')">COPY</button></div>
            <pre id="c1">curl -F "file=@photo.jpg" {base_url}/api/upload</pre>

            <div class="row" style="margin-top:1rem;"><b>2. PYTHON (RAM Optimization)</b> <button class="copy-btn" onclick="copy('c2')">COPY</button></div>
            <pre id="c2" style="font-size:0.8rem;">import requests
# File is pushed to GitHub Data API directly from RAM
files = {{"file": ("test.jpg", open("test.jpg", "rb").read())}}
resp = requests.post("{base_url}/api/upload", files=files)
print(resp.json()["url"])</pre>

            <div class="row" style="margin-top:1rem;"><b>3. JAVASCRIPT (Fetch API)</b> <button class="copy-btn" onclick="copy('c3')">COPY</button></div>
            <pre id="c3" style="font-size:0.8rem;">const fd = new FormData();
fd.append('file', fileInput.files[0]);
const res = await fetch('{base_url}/api/upload', {{method: 'POST', body: fd}});
const data = await res.json();
console.log(data.url);</pre>
        </div>

        <div class="box">
            <h3>CHUNKED UPLOAD (Limit Bypass)</h3>
            <p style="font-size: 0.9rem; color: var(--muted);">Bypass 100MB Cloudflare/Tunnel limit by splitting files. Use <code>chunk_index</code>, <code>total_chunks</code>, and <code>upload_id</code> fields.</p>
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

@app.post("/admin/login")
async def admin_login(data: dict):
    if data.get("password") == os.getenv("ADMIN_PASSWORD"):
        return {"status": "success"}
    raise HTTPException(status_code=401)

@app.get("/admin/data")
async def admin_data(password: str):
    if password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)
    metadata = load_metadata()
    total_size = sum(i.get("size", 0) for i in metadata.values())
    return {{"total_files": len(metadata), "total_size": f"{{total_size/1024/1024:.2f}} MB", "files": metadata}}

@app.post("/admin/delete")
async def admin_delete(data: dict):
    if data.get("password") != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)
    filenames = data.get("filenames", [])
    metadata = load_metadata()
    for f in filenames:
        if f in metadata:
            try: os.remove(os.path.join(UPLOAD_DIR, f))
            except: pass
            del metadata[f]
    save_metadata(metadata)
    git_sync("metadata.json")
    return {"status": "success"}
