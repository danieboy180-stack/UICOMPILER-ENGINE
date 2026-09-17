import json
import hashlib
import time
from pathlib import Path
from typing import Tuple
from .orchestrator import RunLogger

EXIT_OK = 0
EXIT_20 = 20
EXIT_30 = 30

def verify_integrity(root: Path, run_id: str, logger: RunLogger) -> Tuple[bool, int]:
    logger.log("MetaVerify", "INFO", "Starting fast hash-recompute + pointer-walk (no subprocesses)")
    start_time = time.time()
    
    manifest_path = root / "Manifest" / f"manifest_{run_id}.json"
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    
    if not manifest_path.exists() or not ledger_path.exists():
        logger.log("MetaVerify", "ERROR", "Missing manifest or ledger")
        return False, EXIT_30
        
    from .lock import locked_read
    with locked_read(manifest_path) as manifest:
        pass
    with locked_read(ledger_path) as ledger:
        pass
        
    run_entries = [e for e in ledger if e.get("run_id") == run_id and e.get("status") == "written"]
    
    if not run_entries:
        logger.log("MetaVerify", "INFO", "No artifacts written in this run.")
        return True, EXIT_OK
    
    computed_hashes = {}
    for entry in run_entries:
        artifact_name = entry["artifact"]
        
        file_path = root / "Artifacts" / f"{artifact_name}.json"
        if not file_path.exists():
            logger.log("MetaVerify", "ERROR", f"Artifact missing on disk: {artifact_name}")
            return False, EXIT_30
            
        with locked_read(file_path) as data:
            pass
            data_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256(data_str.encode('utf-8')).hexdigest()
            computed_hashes[artifact_name] = h
            
        if h != entry["hash"]:
            logger.log("MetaVerify", "ERROR", f"Hash mismatch for {artifact_name}. Tampering detected!")
            return False, EXIT_30
            
        manifest_hash = manifest.get("artifacts", {}).get(artifact_name, {}).get("hash")
        if h != manifest_hash:
            logger.log("MetaVerify", "ERROR", f"Manifest hash mismatch for {artifact_name}. Tampering detected!")
            return False, EXIT_30

    # Pointer walk: check parent_hash sequentially for the run's artifacts
    for i in range(1, len(run_entries)):
        current = run_entries[i]
        prev = run_entries[i-1]
        
        if current.get("parent_hash") != computed_hashes.get(prev["artifact"]):
            logger.log("MetaVerify", "ERROR", f"Pointer chain gap detected at {current['artifact']}")
            return False, EXIT_20
            
    elapsed = time.time() - start_time
    logger.log("MetaVerify", "INFO", f"compiler_integrity == PASS verified in {elapsed:.3f}s (No subprocess builds invoked)")
    return True, EXIT_OK

