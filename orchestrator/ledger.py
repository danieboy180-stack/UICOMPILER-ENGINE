import json
import os
import hashlib
import datetime
from pathlib import Path
from typing import Any
from .lock import simple_file_lock

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
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        
        ledger = []
        if ledger_path.exists():
            with open(ledger_path, 'r', encoding='utf-8') as f:
                try:
                    ledger = json.load(f)
                except json.JSONDecodeError:
                    pass
                
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
        
        tmp_artifact = artifact_path.with_suffix('.tmp')
        tmp_manifest = manifest_path.with_suffix('.tmp')
        tmp_ledger = ledger_path.with_suffix('.tmp')
        
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(tmp_artifact, 'w', encoding='utf-8') as f: json.dump(payload, f)
        with open(tmp_manifest, 'w', encoding='utf-8') as f: json.dump(manifest, f, indent=2)
        with open(tmp_ledger, 'w', encoding='utf-8') as f: json.dump(ledger, f, indent=2)
        
        os.replace(tmp_artifact, artifact_path)
        os.replace(tmp_manifest, manifest_path)
        os.replace(tmp_ledger, ledger_path)

def record_failed_gate(
    root: Path,
    artifact_name: str,
    run_id: str,
    parent_hash: str = None
) -> None:
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    
    with simple_file_lock(ledger_path):
        ledger = []
        if ledger_path.exists():
            with open(ledger_path, 'r', encoding='utf-8') as f:
                try:
                    ledger = json.load(f)
                except json.JSONDecodeError:
                    pass
                
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
        tmp_ledger = ledger_path.with_suffix('.tmp')
        with open(tmp_ledger, 'w', encoding='utf-8') as f: json.dump(ledger, f, indent=2)
        os.replace(tmp_ledger, ledger_path)

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
        if pl_path.exists():
            with open(pl_path, 'r', encoding='utf-8') as f:
                try:
                    ledger = json.load(f)
                except json.JSONDecodeError:
                    pass
                
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
        
        tmp_pl = pl_path.with_suffix('.tmp')
        with open(tmp_pl, 'w', encoding='utf-8') as f: json.dump(ledger, f, indent=2)
        os.replace(tmp_pl, pl_path)
