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
# Global Buffer for GitHub Sync to prevent race conditions and improve efficiency
upload_buffer = {}
buffer_lock = Lock()

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

# Fix: Move CHUNK_DIR out of DATA_DIR so it's not synced to Git
CHUNK_DIR = "temp_chunks"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHUNK_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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

def github_data_api_batch_push(items: dict):
    """
    Menolak banyak fail sekaligus dalam satu commit (Batch Push).
    Mendukung sehingga 100MB per fail melalui Blob API.
    """
    try:
        token, owner, repo_name = get_repo_info()
        if not token or not items: return

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        tree_payload = []
        
        with httpx.Client(timeout=120.0) as client:
            # 1. Cipta Blobs untuk setiap fail unik dalam buffer
            for target_file, content_bytes in items.items():
                try:
                    if content_bytes is None:
                        local_path = os.path.join(DATA_DIR, target_file)
                        if not os.path.exists(local_path): continue
                        with open(local_path, "rb") as f:
                            content_bytes = f.read()

                    content_base64 = base64.b64encode(content_bytes).decode("utf-8")
                    blob_resp = client.post(
                        f"https://api.github.com/repos/{owner}/{repo_name}/git/blobs",
                        headers=headers,
                        json={"content": content_base64, "encoding": "base64"}
                    )
                    sha = blob_resp.json().get("sha")
                    if sha:
                        tree_payload.append({"path": target_file, "mode": "100644", "type": "blob", "sha": sha})
                except Exception as blob_err:
                    print(f"Blob Error [{target_file}]: {blob_err}")

            if not tree_payload: return

            # 2. Ambil Commit Terakhir & Cipta Tree/Commit Baru
            for attempt in range(5):
                try:
                    ref_resp = client.get(f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/heads/main", headers=headers)
                    last_commit_sha = ref_resp.json()["object"]["sha"]

                    tree_resp = client.post(
                        f"https://api.github.com/repos/{owner}/{repo_name}/git/trees",
                        headers=headers,
                        json={"base_tree": last_commit_sha, "tree": tree_payload}
                    )
                    new_tree_sha = tree_resp.json()["sha"]

                    commit_resp = client.post(
                        f"https://api.github.com/repos/{owner}/{repo_name}/git/commits",
                        headers=headers,
                        json={
                            "message": f"Batch Sync: {len(tree_payload)} files at {get_now_myt().isoformat()}",
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
                        print(f"Batch Sync Success: {len(tree_payload)} files pushed.")
                        break
                    else:
                        time.sleep(2)
                except Exception as commit_err:
                    print(f"Commit Attempt {attempt+1} Error: {commit_err}")
                    time.sleep(2)

    except Exception as e:
        print(f"Batch Sync Critical Error: {e}")

def flush_upload_buffer():
    """Mengosongkan buffer RAM dan menolak data ke GitHub."""
    global upload_buffer
    with buffer_lock:
        if not upload_buffer: return
        items_to_send = dict(upload_buffer)
        upload_buffer.clear()
    
    github_data_api_batch_push(items_to_send)

def sync_worker_loop():
    """Background loop untuk sync automatik setiap 30 saat."""
    while True:
        time.sleep(30)
        flush_upload_buffer()

def git_sync(target_file: str, content_bytes: bytes = None):
    """Menambah fail ke dalam buffer RAM untuk batch push."""
    with buffer_lock:
        upload_buffer[target_file] = content_bytes

@app.on_event("startup")
async def startup_event():
    """Memastikan data repo sentiasa selari (sync) semasa mula."""
    os.makedirs("static", exist_ok=True)
    os.makedirs(CHUNK_DIR, exist_ok=True)
    
    # Memulakan background sync worker
    Thread(target=sync_worker_loop, daemon=True).start()

    if os.getenv("PRIVATE_REPO_URL"):
        if not os.path.exists(os.path.join(DATA_DIR, ".git")):
            if os.path.exists(DATA_DIR):
                try: shutil.rmtree(DATA_DIR)
                except: pass
            try:
                print("Cloning data repository...")
                subprocess.run(["git", "clone", os.getenv("PRIVATE_REPO_URL"), DATA_DIR], check=True)
            except Exception as e:
                print(f"Startup clone error: {e}")
        else:
            try:
                print("Updating data repository (git pull)...")
                subprocess.run(["git", "pull", "origin", "main"], cwd=DATA_DIR, check=True)
            except Exception as e:
                print(f"Pull error: {e}")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "w") as f:
            json.dump({}, f)

@app.on_event("shutdown")
async def shutdown_event():
    """Memastikan semua data dalam RAM ditolak ke GitHub sebelum aplikasi mati."""
    print("Shutdown detected. Flushing upload buffer to GitHub...")
    flush_upload_buffer()

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

@app.post("/api/upload")
async def upload_file(
    request: Request, 
    file: UploadFile = File(...),
    chunk_index: int = Form(None),
    total_chunks: int = Form(None),
    upload_id: str = Form(None)
):
    try:
        now = get_now_myt()
        timestamp = int(now.timestamp())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._- "]).strip()
        if not safe_name: safe_name = "file"
        
        final_content = None

        if chunk_index is not None and upload_id is not None:
            upload_temp_dir = os.path.join(CHUNK_DIR, upload_id)
            os.makedirs(upload_temp_dir, exist_ok=True)
            chunk_path = os.path.join(upload_temp_dir, f"chunk_{chunk_index}")
            with open(chunk_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            if chunk_index < total_chunks - 1:
                return {"status": "chunk_received", "chunk_index": chunk_index}
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
        else:
            filename_to_save = f"{timestamp}_{safe_name}"
            file_path = os.path.join(UPLOAD_DIR, filename_to_save)
            final_content = await file.read()
            size = len(final_content)
            with open(file_path, "wb") as f:
                f.write(final_content)
            original_name = file.filename

        metadata = load_metadata()
        metadata[filename_to_save] = {
            "name": original_name,
            "ip": request.client.host,
            "time": now.isoformat(),
            "expires": (now + timedelta(days=7)).isoformat(),
            "size": size
        }
        save_metadata(metadata)
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
            .back {{ text-decoration: none; color: var(--muted); font-size: 0.9rem; display: block; margin-bottom: 1rem; font-weight: 700; }}
            .badge {{ background: var(--speed); color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700; vertical-align: middle; }}
        </style>
    </head>
    <body>
        <a href="/" class="back">← HOME</a>
        <h1>API DOC</h1>
        <div class="box">Endpoint: <code>POST {base_url}/api/upload</code><br>Field Name: <code>file</code></div>
        
        <div class="box" style="border-left: 4px solid var(--speed);">
            <h3><span class="badge">NEW</span> SPEED / RAM UPLOAD</h3>
            <p style="font-size: 0.9rem; color: var(--muted);">Directly syncs to Data API from RAM. Best for high concurrency. <b>Limit: 100MB</b></p>
            <div class="row"><b>CURL (RAM Sync)</b> <button class="copy-btn" onclick="copy('r1')">COPY</button></div>
            <pre id="r1">curl -F "file=@photo.jpg" {base_url}/api/upload</pre>

            <div class="row" style="margin-top:1rem;"><b>PYTHON (RAM Sync)</b> <button class="copy-btn" onclick="copy('r2')">COPY</button></div>
            <pre id="r2" style="font-size:0.8rem;">import requests
files = {{"file": ("test.jpg", open("test.jpg", "rb").read())}}
resp = requests.post("{base_url}/api/upload", files=files)
print(resp.json()["url"])</pre>

            <div class="row" style="margin-top:1rem;"><b>JAVASCRIPT (RAM Sync)</b> <button class="copy-btn" onclick="copy('r3')">COPY</button></div>
            <pre id="r3" style="font-size:0.8rem;">const formData = new FormData();
formData.append('file', fileInput.files[0]);
const resp = await fetch('{base_url}/api/upload', {{ method: 'POST', body: formData }});
const result = await resp.json();
console.log(result.url);</pre>
        </div>

        <div class="box">
            <div class="row"><b>IMAGE</b> <button class="copy-btn" onclick="copy('c1')">COPY</button></div>
            <pre id="c1">curl -F "file=@p.png" {base_url}/api/upload</pre>
        </div>
        <div class="box">
            <div class="row"><b>VIDEO</b> <button class="copy-btn" onclick="copy('c2')">COPY</button></div>
            <pre id="c2">curl -F "file=@v.mp4" {base_url}/api/upload</pre>
        </div>
        <div class="box">
            <div class="row"><b>FILE (APK/ZIP/PY)</b> <button class="copy-btn" onclick="copy('c3')">COPY</button></div>
            <pre id="c3">curl -F "file=@a.apk" {base_url}/api/upload</pre>
        </div>

        <div class="box">
            <h3>REGULAR UPLOAD (< 100MB)</h3>
            <div class="row"><b>PYTHON</b> <button class="copy-btn" onclick="copy('c-reg-py')">COPY</button></div>
            <pre id="c-reg-py" style="font-size: 0.8rem;">import requests
resp = requests.post("{base_url}/api/upload", files={{"file": open("file.png", "rb")}})
print(resp.json()["url"])</pre>

            <div class="row" style="margin-top: 1rem;"><b>JAVASCRIPT</b> <button class="copy-btn" onclick="copy('c-reg-js')">COPY</button></div>
            <pre id="c-reg-js" style="font-size: 0.8rem;">const formData = new FormData();
formData.append('file', fileInput.files[0]);
const resp = await fetch('{base_url}/api/upload', {{ method: 'POST', body: formData }});
const result = await resp.json();
console.log(result.url);</pre>
        </div>

        <div class="box" style="border-left: 4px solid var(--accent);">
            <h3>CHUNKED UPLOAD (> 100MB)</h3>
            <p style="font-size: 0.9rem; color: var(--muted);">Bypass 100MB limit by splitting file. Use <code>chunk_index</code>, <code>total_chunks</code>, and <code>upload_id</code>.</p>
            
            <div class="row"><b>CURL (Chunk 1)</b> <button class="copy-btn" onclick="copy('c-ch-c1')">COPY</button></div>
            <pre id="c-ch-c1">curl -F "file=@part1" -F "chunk_index=0" -F "total_chunks=2" -F "upload_id=uid123" {base_url}/api/upload</pre>
            
            <div class="row" style="margin-top:0.5rem;"><b>CURL (Chunk 2)</b> <button class="copy-btn" onclick="copy('c-ch-c2')">COPY</button></div>
            <pre id="c-ch-c2">curl -F "file=@part2" -F "chunk_index=1" -F "total_chunks=2" -F "upload_id=uid123" {base_url}/api/upload</pre>

            <div class="row" style="margin-top: 1rem;"><b>PYTHON</b> <button class="copy-btn" onclick="copy('c-chunk-py')">COPY</button></div>
            <pre id="c-chunk-py" style="font-size: 0.8rem;">
import requests, math, uuid, os
file_path = "large_file.zip"
url = "{base_url}/api/upload"
chunk_size = 5 * 1024 * 1024
file_size = os.path.getsize(file_path)
total_chunks = math.ceil(file_size / chunk_size)
upload_id = str(uuid.uuid4())

with open(file_path, "rb") as f:
    for i in range(total_chunks):
        chunk = f.read(chunk_size)
        payload = {{"chunk_index": i, "total_chunks": total_chunks, "upload_id": upload_id}}
        files = {{"file": (os.path.basename(file_path), chunk)}}
        resp = requests.post(url, data=payload, files=files)
        if i == total_chunks - 1: print("URL:", resp.json()["url"])</pre>

            <div class="row" style="margin-top: 1rem;"><b>JAVASCRIPT</b> <button class="copy-btn" onclick="copy('c-chunk-js')">COPY</button></div>
            <pre id="c-chunk-js" style="font-size: 0.8rem;">
const CHUNK_SIZE = 5 * 1024 * 1024;
const file = fileInput.files[0];
const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
const uploadId = crypto.randomUUID();

for (let i = 0; i < totalChunks; i++) {{
    const chunk = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
    const formData = new FormData();
    formData.append('file', chunk, file.name);
    formData.append('chunk_index', i);
    formData.append('total_chunks', totalChunks);
    formData.append('upload_id', uploadId);
    const resp = await fetch('{base_url}/api/upload', {{ method: 'POST', body: formData }});
    const result = await resp.json();
    if (result.url) console.log("Final URL:", result.url);
}}</pre>
        </div>

        <div class="box" style="border-color: #333;">
            <div class="row"><b style="color: var(--muted);">SUCCESS RESPONSE (JSON)</b></div>
            <pre style="color: #00ff00; background: #050505;">{{
  "url": "{base_url}/d/123456789_file.ext"
}}</pre>
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
    if data.get("password") == os.getenv("ADMIN_PASSWORD"):
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/admin/data")
async def admin_data(password: str):
    if password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401)
    
    metadata = load_metadata()
    total_files = len(metadata)
    total_size = sum(info.get("size", 0) for info in metadata.values())
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if total_size < 1024:
            size_str = f"{total_size:.2f} {unit}"
            break
        total_size /= 1024
    else:
        size_str = f"{total_size:.2f} TB"

    return {
        "total_files": f"{total_files:,}",
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
    git_sync("metadata.json")
    return {"status": "success", "deleted": deleted}
