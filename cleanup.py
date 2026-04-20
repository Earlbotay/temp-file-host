import os
import json
from datetime import datetime, timedelta, timezone

DATA_DIR = "data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

# Malaysian Timezone (UTC+8)
MYT = timezone(timedelta(hours=8))

def cleanup():
    if not os.path.exists(METADATA_FILE):
        return

    try:
        with open(METADATA_FILE, "r") as f:
            content = f.read()
            if not content:
                return
            metadata = json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading metadata: {e}")
        return

    now = datetime.now(MYT)
    
    new_metadata = {}
    for filename, info in metadata.items():
        try:
            expires_at = datetime.fromisoformat(info["expires"])
            # Ensure expires_at is aware if it's not
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
            # Keep it if error, to be safe
            new_metadata[filename] = info

    with open(METADATA_FILE, "w") as f:
        json.dump(new_metadata, f, indent=4)

if __name__ == "__main__":
    cleanup()
