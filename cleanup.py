import os
import json
import subprocess
from datetime import datetime, timedelta, timezone

DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

# Malaysian Timezone (UTC+8)
MYT = timezone(timedelta(hours=8))

def cleanup():
    # Sync with private repo first to get external deletions
    if os.path.exists(os.path.join(DATA_DIR, ".git")):
        try:
            subprocess.run(["git", "pull", "origin", "main"], cwd=DATA_DIR)
        except:
            pass

    if not os.path.exists(METADATA_FILE):
        return

    try:
        with open(METADATA_FILE, "r") as f:
            content = f.read()
            if not content: return
            metadata = json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading metadata: {e}")
        return

    now = datetime.now(MYT)
    new_metadata = {}
    
    # Track files physically present
    actual_files = os.listdir(UPLOAD_DIR) if os.path.exists(UPLOAD_DIR) else []
    
    for filename, info in metadata.items():
        try:
            expires_at = datetime.fromisoformat(info["expires"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=MYT)
                
            if now > expires_at:
                file_path = os.path.join(UPLOAD_DIR, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
                print(f"Deleted expired file: {filename}")
            else:
                new_metadata[filename] = info
        except Exception as e:
            print(f"Error processing {filename}: {e}")
            new_metadata[filename] = info

    # Also clean up local files that are NOT in metadata (ghost files)
    for f in actual_files:
        if f not in new_metadata:
            try:
                os.remove(os.path.join(UPLOAD_DIR, f))
                print(f"Cleaned ghost file: {f}")
            except: pass

    with open(METADATA_FILE, "w") as f:
        json.dump(new_metadata, f, indent=4)

    # Push cleanup results
    try:
        subprocess.run(["git", "add", "."], cwd=DATA_DIR)
        subprocess.run(["git", "commit", "-m", "Auto-cleanup of expired files"], cwd=DATA_DIR)
        subprocess.run(["git", "push", "origin", "main"], cwd=DATA_DIR)
    except:
        pass

if __name__ == "__main__":
    cleanup()
