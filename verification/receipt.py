import json
import hashlib
import time
import os
from pathlib import Path

# Handle Windows / Unix lock compatibility if needed, but since it's append mode we can just write atomically if we want
# Wait, fcntl doesn't exist on windows.
import sys
if sys.platform == "win32":
    import msvcrt
    def lock_file(f):
        # msvcrt.locking not strictly needed for append in most simple cases but let's use it
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            pass
    def unlock_file(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    def unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def record_verification(stage_id: str, claim: str, raw_output: str, passed: bool) -> None:
    if passed:
        lines = raw_output.strip().splitlines()
        if not raw_output or len(lines) < 10:
            raise ValueError(f"Insufficient verification evidence. Output must be at least 10 lines, got {len(lines)}")
            
        short_strings = ["passed", "ok", "done", "success", "true"]
        if raw_output.strip().lower() in short_strings:
            raise ValueError("Insufficient verification evidence. Cannot be just 'PASSED' or 'OK'.")
            
    lines = raw_output.strip().splitlines()
    if len(lines) > 40:
        excerpt = "\n".join(lines[:20] + ["... [truncated] ..."] + lines[-20:])
    else:
        excerpt = "\n".join(lines)
        
    out_hash = hashlib.sha256(raw_output.encode('utf-8')).hexdigest()
    
    entry = {
        "stage_id": stage_id,
        "claim": claim,
        "raw_output_hash": out_hash,
        "raw_output_excerpt": excerpt,
        "passed": passed,
        "timestamp": time.time()
    }
    
    root = Path.cwd()
    logs_dir = root / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    ledger_path = logs_dir / "Verification_Ledger.json"
    
    with open(ledger_path, "a", encoding="utf-8") as f:
        lock_file(f)
        try:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
        finally:
            unlock_file(f)
