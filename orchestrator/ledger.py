import json
import os
import hashlib
import datetime
from pathlib import Path
from typing import Any
import uuid
from .lock import locked_read, locked_write, simple_file_lock

def write_atomic_artifact(
    root: Path,
    artifact_name: str,
    run_id: str,
    payload: Any,
    parent_hash: str = None
) -> None:
    artifact_dir = root / "Artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    artifact_path = artifact_dir / f"{artifact_name}.json"
    manifest_path = root / "Manifest" / f"manifest_{run_id}.json"
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    
    with simple_file_lock(manifest_path), simple_file_lock(ledger_path):
        data_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        data_hash = hashlib.sha256(data_str.encode('utf-8')).hexdigest()
        
        manifest = {}
        with locked_read(manifest_path, default={}) as data:
            manifest = data
        
        ledger = []
        with locked_read(ledger_path, default=[]) as data:
            ledger = data if isinstance(data, list) else []
            
        if parent_hash:
            # Check if parent_hash exists in the ledger for this run
            parent_found = False
            for e in reversed(ledger):
                if e.get("run_id") == run_id and e.get("hash") == parent_hash:
                    parent_found = True
                    break
            if not parent_found:
                raise ValueError(f"Hash chain broken: parent_hash {parent_hash} not found in ledger for run {run_id}")
                
        manifest.setdefault("artifacts", {})[artifact_name] = {
            "hash": data_hash,
            "run_id": run_id,
            "version": 1
        }
        
        ledger.append({
            "artifact": artifact_name,
            "version": 1,
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "hash": data_hash,
            "parent_hash": parent_hash,
            "status": "written",
        })
        
        uid = uuid.uuid4().hex
        tmp_artifact = artifact_path.with_suffix(f'.{uid}.tmp')
        tmp_manifest = manifest_path.with_suffix(f'.{uid}.tmp')
        tmp_ledger = ledger_path.with_suffix(f'.{uid}.tmp')
        
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        
        with locked_write(artifact_path, payload):
            pass
        with locked_write(manifest_path, manifest):
            pass
        with locked_write(ledger_path, ledger):
            pass

def record_failed_gate(
    root: Path,
    artifact_name: str,
    run_id: str,
    parent_hash: str = None
) -> None:
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    
    with simple_file_lock(ledger_path):
        ledger = []
        with locked_read(ledger_path, default=[]) as data:
            ledger = data if isinstance(data, list) else []
                
        ledger.append({
            "artifact": artifact_name,
            "version": 1,
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "hash": "failed_gate_hash",
            "parent_hash": parent_hash,
            "status": "failed_gate",
        })
        
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with locked_write(ledger_path, ledger):
            pass

def write_pipeline_ledger(
    root: Path,
    run_id: str,
    status: str,
    duration_seconds: float,
    files_produced: int,
    warnings: list,
    errors: list,
    hash_chain_valid: bool
) -> None:
    pl_path = root / "Logs" / "Pipeline_Ledger.json"
    
    with simple_file_lock(pl_path):
        pl_path.parent.mkdir(parents=True, exist_ok=True)
        
        ledger = []
        with locked_read(pl_path, default=[]) as data:
            ledger = data if isinstance(data, list) else []
                
        entry = {
            "run_id": run_id,
            "input": [],
            "output": [],
            "duration_seconds": duration_seconds,
            "compiler_version": "v2.1",
            "files_produced": files_produced,
            "status": status,
            "warnings": warnings,
            "errors": errors,
            "hash_chain_valid": hash_chain_valid
        }
        
        ledger.append(entry)
        
        with locked_write(pl_path, ledger):
            pass
