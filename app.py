from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import os
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from threading import Thread

app = FastAPI(title="Earl File", description="Temporary file host with 7-day retention.")

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

def git_sync():
    """Sync changes to private repo."""
    try:
        if not os.path.exists(os.path.join(DATA_DIR, ".git")): return
        subprocess.run(["git", "pull", "origin", "main"], cwd=DATA_DIR)
        subprocess.run(["git", "add", "."], cwd=DATA_DIR)
        subprocess.run(["git", "commit", "-m", f"Sync: {get_now_myt().strftime('%Y-%m-%d %H:%M:%S')}"], cwd=DATA_DIR)
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
            # Parse strictly
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
async def upload_file(request: Request, file: UploadFile = File(...)):
    try:
        now = get_now_myt()
        timestamp = int(now.timestamp())
        safe_name = "".join([c for c in file.filename if c.isalnum() or c in "._- "]).strip()
        if not safe_name: safe_name = "file"
        
        filename = f"{timestamp}_{safe_name}"
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
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Upload failed: {str(e)}"})

@app.post("/api/upload/chunk")
async def upload_chunk(
    request: Request,
    file: UploadFile = File(...),
    chunk_index: int = 0,
    upload_id: str = "",
    filename: str = ""
):
    try:
        if not upload_id or not filename:
            raise HTTPException(status_code=400, detail="Missing upload_id or filename")
        
        # Create a unique directory for this specific upload
        upload_temp_dir = os.path.join(CHUNK_DIR, upload_id)
        os.makedirs(upload_temp_dir, exist_ok=True)
        
        chunk_path = os.path.join(upload_temp_dir, f"chunk_{chunk_index}")
        with open(chunk_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        return {"status": "success", "chunk_index": chunk_index}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Chunk upload failed: {str(e)}"})

@app.post("/api/upload/complete")
async def complete_upload(request: Request, data: dict):
    try:
        upload_id = data.get("upload_id")
        filename = data.get("filename")
        total_chunks = data.get("total_chunks")
        
        if not all([upload_id, filename, total_chunks]):
            raise HTTPException(status_code=400, detail="Missing required completion data")
            
        upload_temp_dir = os.path.join(CHUNK_DIR, upload_id)
        if not os.path.exists(upload_temp_dir):
            raise HTTPException(status_code=404, detail="Upload session not found")
            
        now = get_now_myt()
        timestamp = int(now.timestamp())
        safe_name = "".join([c for c in filename if c.isalnum() or c in "._- "]).strip()
        if not safe_name: safe_name = "file"
        
        final_filename = f"{timestamp}_{safe_name}"
        file_path = os.path.join(UPLOAD_DIR, final_filename)
        
        # Assemble chunks
        with open(file_path, "wb") as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(upload_temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                    raise HTTPException(status_code=400, detail=f"Chunk {i} missing")
                with open(chunk_path, "rb") as f:
                    final_file.write(f.read())
        
        # Cleanup chunks
        shutil.rmtree(upload_temp_dir)
        
        metadata = load_metadata()
        metadata[final_filename] = {
            "name": filename,
            "ip": request.client.host,
            "time": now.isoformat(),
            "expires": (now + timedelta(days=7)).isoformat(),
            "size": os.path.getsize(file_path)
        }
        save_metadata(metadata)
        Thread(target=git_sync).start()
        
        host = request.headers.get("host", "temp.earlstore.online")
        protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
        return {"url": f"{protocol}://{host}/d/{final_filename}"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Upload completion failed: {str(e)}"})

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
        
        # Update BOTH time and expires in MYT (Sliding Expiry)
        now = get_now_myt()
        metadata[filename]["time"] = now.isoformat()
        metadata[filename]["expires"] = (now + timedelta(days=7)).isoformat()
        
        # Recalculate human-readable dates explicitly before saving
        t = now
        e = now + timedelta(days=7)
        metadata[filename]["time_human"] = t.strftime("%b %d, %Y, %I:%M %p")
        metadata[filename]["expires_human"] = e.strftime("%b %d, %Y, %I:%M %p")
        
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
        <title>API DOC - Earl File</title>
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {{ --bg: #ffffff; --text: #000000; --muted: #666666; --border: #eeeeee; --accent: #ff3e00; }}
            body {{ font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; max-width: 800px; margin: 0 auto; }}
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
            <div class="row"><b>FILE (APK/ZIP/PY)</b> <button class="copy-btn" onclick="copy('c3')">COPY</button></div>
            <pre id="c3">curl -F "file=@a.apk" {base_url}/api/upload</pre>
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
    password = data.get("password")
    env_pass = os.getenv("ADMIN_PASSWORD")
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
    Thread(target=git_sync).start()
    return {"status": "success", "deleted": deleted}
