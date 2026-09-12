"""Deterministic resolver for the Python_Inbox ingestion boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError


EXIT_OK = 0
EXIT_10 = 10
CANONICAL_NAME = "layer12_bundle"
ALLOWED_FILES = {"layer12_bundle.json"}


class Layer12Bundle(BaseModel):
    """Schema for the currently supported inbox filename."""
    schema_version: Literal["2.1"] = "2.1"
    project_id: str = "default_project"
    layer: Literal[12]
    payload: dict[str, Any]

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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)

def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)

def _recover_transaction(root: Path) -> None:
    transaction_path = root / ".ingest_transaction.json"
    if not transaction_path.exists():
        return
    transaction = _read_json(transaction_path, {})
    for item in transaction.get("backups", []):
        target = root / item["target"]
        backup = root / item["backup"]
        if item["existed"] and backup.exists():
            os.replace(backup, target)
        elif not item["existed"]:
            target.unlink(missing_ok=True)
    for item in transaction.get("backups", []):
        (root / item["backup"]).unlink(missing_ok=True)
    transaction_path.unlink(missing_ok=True)

def _commit_outputs(root: Path, outputs: dict[Path, Any]) -> None:
    transaction_path = root / ".ingest_transaction.json"
    backups: list[dict[str, Any]] = []
    try:
        for target, value in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
            existed = target.exists()
            if existed:
                shutil.copy2(target, backup)
            backups.append({
                "target": str(target.relative_to(root)),
                "backup": str(backup.relative_to(root)),
                "existed": existed,
            })
            staged = target.with_name(f".{target.name}.ingest.tmp")
            _atomic_json_write(staged, value)
        _atomic_json_write(transaction_path, {"backups": backups})
        for target in outputs:
            os.replace(target.with_name(f".{target.name}.ingest.tmp"), target)
    except Exception:
        _recover_transaction(root)
        raise
    else:
        for item in backups:
            (root / item["backup"]).unlink(missing_ok=True)
        transaction_path.unlink(missing_ok=True)

def ingest(root: Path) -> int:
    root = root.resolve()
    _recover_transaction(root)
    inbox = root / "Python_Inbox"
    selected = next((inbox / name for name in sorted(ALLOWED_FILES) if (inbox / name).is_file()), None)
    if selected is None:
        return EXIT_OK

    try:
        with selected.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
            
        # Gap 6: Schema versioning explicit check policy
        if raw.get("schema_version") != "2.1":
            print(f"[Ingest] ERROR: Unsupported schema version '{raw.get('schema_version')}'. Expected '2.1'.")
            selected.unlink(missing_ok=True)
            return EXIT_10
            
        validated = _model_validate(Layer12Bundle, raw)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as e:
        print(f"[Ingest] Validation failed: {e}")
        return EXIT_10

    data = _model_dump(validated)
    content_hash = _hash(data)
    project_id = data.get("project_id", "default_project")
    
    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    ledger = _read_json(ledger_path, [])
    
    # Gap 3 (O12 Fix): Idempotency policy - SCOPED BY PROJECT_ID
    for e in ledger:
        if e.get("artifact") == CANONICAL_NAME and e.get("hash") == content_hash and e.get("status") == "written" and e.get("project_id", "default_project") == project_id:
            print(f"[Ingest] WARNING: Duplicate bundle (hash {content_hash[:8]}) already processed in project '{project_id}' (run {e.get('run_id')}). Rejecting.")
            selected.unlink(missing_ok=True)
            return EXIT_OK # It's technically a no-op success

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    artifact_path = root / "Artifacts" / f"{CANONICAL_NAME}.json"
    manifest_path = root / "Manifest" / f"manifest_{run_id}.json"
    manifest = _read_json(manifest_path, {"next_layer": None, "artifacts": {}})
    parent_hash = manifest.get("artifacts", {}).get(CANONICAL_NAME, {}).get("hash")
    entry = {
        "artifact": CANONICAL_NAME,
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": timestamp,
        "hash": content_hash,
        "parent_hash": parent_hash,
        "status": "written",
    }
    if not isinstance(ledger, list):
        return EXIT_10
    ledger.append(entry)
    manifest.setdefault("artifacts", {})[CANONICAL_NAME] = {
        "hash": content_hash,
        "run_id": run_id,
        "version": 1,
    }
    manifest["next_layer"] = 13
    destination = root / "Python_Output" / "runs" / run_id / "ingested" / selected.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(selected), str(destination))
        _commit_outputs(root, {artifact_path: data, ledger_path: ledger, manifest_path: manifest})
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