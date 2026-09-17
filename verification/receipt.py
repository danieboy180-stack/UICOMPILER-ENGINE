import json
import hashlib
import time
import os
from pathlib import Path

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
        "verification_session_id": os.environ.get("VERIFICATION_SESSION_ID"),
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
    
    from orchestrator.lock import locked_append
    locked_append(ledger_path, json.dumps(entry, sort_keys=True) + "\n")
