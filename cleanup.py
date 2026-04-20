import os
import json
from datetime import datetime

DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

def cleanup():
    if not os.path.exists(METADATA_FILE):
        return

    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)

    now = datetime.now()
    to_delete = []
    
    new_metadata = {}
    for filename, info in metadata.items():
        expires_at = datetime.fromisoformat(info["expires"])
        if now > expires_at:
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(file_path):
                os.remove(file_path)
            print(f"Deleted expired file: {filename}")
        else:
            new_metadata[filename] = info

    with open(METADATA_FILE, "w") as f:
        json.dump(new_metadata, f, indent=4)

if __name__ == "__main__":
    cleanup()
