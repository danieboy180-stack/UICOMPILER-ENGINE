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
    from .ledger import load_json_strict

    # Fail closed: a corrupt/truncated manifest or ledger must never read as "empty".
    try:
        manifest_data = load_json_strict(manifest_path)
        ledger_data = load_json_strict(ledger_path)
    except (OSError, TimeoutError, ValueError) as exc:
        logger.log("MetaVerify", "ERROR", f"Manifest or ledger unreadable (fail-closed): {exc}")
        return False, EXIT_30
    if not isinstance(manifest_data, dict) or not isinstance(ledger_data, list):
        logger.log("MetaVerify", "ERROR", "Manifest or ledger has an unexpected structure (fail-closed)")
        return False, EXIT_30

    run_entries = [e for e in ledger_data if isinstance(e, dict) and e.get("run_id") == run_id and e.get("status") == "written"]

    # Every artifact the manifest claims must be backed by a ledger entry; otherwise a wiped
    # ledger would make the checks below pass with nothing verified.
    manifest_artifacts = manifest_data.get("artifacts", {})
    if not isinstance(manifest_artifacts, dict):
        logger.log("MetaVerify", "ERROR", "Manifest 'artifacts' is not an object (fail-closed)")
        return False, EXIT_30
    unbacked = sorted(set(manifest_artifacts) - {e.get("artifact") for e in run_entries})
    if unbacked:
        logger.log("MetaVerify", "ERROR", f"Ledger has no written entry for manifest artifact(s): {', '.join(unbacked)}. Tampering detected!")
        return False, EXIT_30

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

        with locked_read(file_path, default={}) as data:
            data_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256(data_str.encode('utf-8')).hexdigest()
            computed_hashes[artifact_name] = h

        if h != entry["hash"]:
            logger.log("MetaVerify", "ERROR", f"Hash mismatch for {artifact_name}. Tampering detected!")
            return False, EXIT_30

        manifest_hash = manifest_data.get("artifacts", {}).get(artifact_name, {}).get("hash")
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

