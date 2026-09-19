"""Deterministic resolver for the Python_Inbox ingestion boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

EXIT_OK = 0
EXIT_10 = 10
ALLOWED_FILES = {"layer12_bundle.txt"}

class Layer1CCIR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    project_id: str = "default_project"

def _model_validate(model: type[BaseModel], value: Any) -> BaseModel:
    validator = getattr(model, "model_validate", None)
    if validator is not None:
        return validator(value)
    return model.parse_obj(value)

def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()

def _model_dump(model: BaseModel) -> dict[str, Any]:
    dumper = getattr(model, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return json.loads(model.json())

def _atomic_json_write(path: Path, value: Any) -> None:
    from orchestrator.lock import locked_write
    with locked_write(path, value):
        pass

def _read_json(path: Path, default: Any) -> Any:
    from orchestrator.lock import locked_read
    with locked_read(path, default=default) as data:
        return data

def _recover_transaction(root: Path, run_id: str) -> None:
    transaction_path = root / f".ingest_transaction_{run_id}.json"
    if not transaction_path.exists():
        return
    transaction = _read_json(transaction_path, {})
    from orchestrator.lock import locked_read, locked_write, safe_unlink
    for item in transaction.get("backups", []):
        target = root / item["target"]
        backup = root / item["backup"]
        if item["existed"] and backup.exists():
            with locked_read(backup, parse_json=False) as content:
                pass
            with locked_write(target, content):
                pass
            safe_unlink(backup)
        elif not item["existed"]:
            safe_unlink(target)
    for item in transaction.get("backups", []):
        safe_unlink(root / item["backup"])
    safe_unlink(transaction_path)

def _commit_outputs(root: Path, outputs: dict[Path, Any], run_id: str) -> None:
    transaction_path = root / f".ingest_transaction_{run_id}.json"
    backups: list[dict[str, Any]] = []
    from orchestrator.lock import locked_write, safe_unlink
    try:
        for target, value in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
            existed = target.exists()
            if existed:
                from orchestrator.lock import locked_read, locked_write
                with locked_read(target, parse_json=False) as existing_content:
                    pass
                with locked_write(backup, existing_content):
                    pass
            backups.append({
                "target": str(target.relative_to(root)),
                "backup": str(backup.relative_to(root)),
                "existed": existed,
            })
            with locked_write(target, value):
                pass
        _atomic_json_write(transaction_path, {"backups": backups})
    except Exception:
        _recover_transaction(root, run_id)
        raise
    else:
        for item in backups:
            safe_unlink(root / item["backup"])
        safe_unlink(transaction_path)

def ingest(root: Path) -> int:
    root = root.resolve()
    inbox = root / "Python_Inbox"
    selected = next((inbox / name for name in sorted(ALLOWED_FILES) if (inbox / name).is_file()), None)
    if selected is None:
        return EXIT_OK

    original_name = selected.name
    processing_path = selected.with_name(f".{selected.name}.processing.{uuid.uuid4().hex}")
    try:
        os.rename(selected, processing_path)
        selected = processing_path
    except OSError:
        return EXIT_OK

    from orchestrator.lock import safe_unlink
    
    try:
        with selected.open("r", encoding="utf-8") as stream:
            raw_text = stream.read()
            
        l1_match = re.search(r'<LAYER1_OUTPUT>(.*?)</LAYER1_OUTPUT>', raw_text, re.DOTALL)
        l2_match = re.search(r'<LAYER2_OUTPUT>(.*?)</LAYER2_OUTPUT>', raw_text, re.DOTALL)
        
        if not l1_match or not l2_match:
            print("[Ingest] ERROR: Missing <LAYER1_OUTPUT> or <LAYER2_OUTPUT>")
            safe_unlink(selected)
            return EXIT_10
            
        layer1_raw = json.loads(l1_match.group(1).strip())
        layer2_raw = json.loads(l2_match.group(1).strip())
        
        # Gap 6: Schema versioning explicit check policy
        if layer1_raw.get("schema_version") != "2.1":
            print(f"[Ingest] ERROR: Unsupported schema version '{layer1_raw.get('schema_version')}'. Expected '2.1'.")
            safe_unlink(selected)
            return EXIT_10
            
        validated_l1 = _model_validate(Layer1CCIR, layer1_raw)
        
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as e:
        print(f"[Ingest] Validation failed: {e}")
        safe_unlink(selected)
        return EXIT_10

    data1 = _model_dump(validated_l1)
    # Re-merge any other fields dynamically since BaseModel might drop them if not defined,
    # or just use the raw dict if we want to keep everything. Actually let's use the raw dictionary.
    data1 = layer1_raw
    data2 = layer2_raw
    
    content_hash1 = _hash(data1)
    content_hash2 = _hash(data2)
    project_id = data1.get("project_id", "default_project")
    
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    ledger = _read_json(ledger_path, [])
    
    # Gap 3 (O12 Fix): Idempotency policy - SCOPED BY PROJECT_ID
    # We can check layer1 hash for idempotency
    for e in ledger:
        if e.get("artifact") == "layer1_ccir" and e.get("hash") == content_hash1 and e.get("status") == "written" and e.get("project_id", "default_project") == project_id:
            print(f"[Ingest] WARNING: Duplicate bundle (hash {content_hash1[:8]}) already processed in project '{project_id}' (run {e.get('run_id')}). Rejecting.")
            safe_unlink(selected)
            return EXIT_OK

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    artifact_path1 = root / "Artifacts" / "layer1_ccir.json"
    artifact_path2 = root / "Artifacts" / "layer2_csir.json"
    
    manifest_path = root / "Manifest" / f"manifest_{run_id}.json"
    manifest = _read_json(manifest_path, {"next_layer": None, "artifacts": {}})
    # layer1_ccir is the first artifact in the chain for this run — no parent
    # within this run. Cross-run provenance is not implemented; if needed later,
    # resolve it explicitly here instead of an inert lookup.
    parent_hash = None
    
    entry1 = {
        "artifact": "layer1_ccir",
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": timestamp,
        "hash": content_hash1,
        "parent_hash": parent_hash,
        "status": "written",
    }
    
    entry2 = {
        "artifact": "layer2_csir",
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": timestamp,
        "hash": content_hash2,
        "parent_hash": content_hash1, # layer2 is child of layer1
        "status": "written",
    }
    
    if not isinstance(ledger, list):
        return EXIT_10
        
    ledger.append(entry1)
    ledger.append(entry2)
    
    manifest.setdefault("artifacts", {})["layer1_ccir"] = {
        "hash": content_hash1,
        "run_id": run_id,
        "version": 1,
    }
    manifest.setdefault("artifacts", {})["layer2_csir"] = {
        "hash": content_hash2,
        "run_id": run_id,
        "version": 1,
    }
    manifest["pipeline_id"] = data1.get("pipeline_id", "default_pipeline")
    manifest["run_id"] = run_id
    manifest["image_hash"] = data1.get("image_hash", "default_image_hash")
    manifest["layer1_hash"] = content_hash1
    manifest["layer2_hash"] = content_hash2
    manifest["next_layer"] = 13
    
    destination = root / "Python_Output" / "runs" / run_id / "ingested" / original_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(selected), str(destination))
        _commit_outputs(root, {artifact_path1: data1, artifact_path2: data2, ledger_path: ledger, manifest_path: manifest}, run_id)
    except (OSError, TypeError, ValueError):
        if destination.exists() and not selected.exists():
            selected.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(selected))
        return EXIT_10
    return EXIT_OK

def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve allow-listed Python_Inbox bundles.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return ingest(parser.parse_args().root)

if __name__ == "__main__":
    sys.exit(main())
