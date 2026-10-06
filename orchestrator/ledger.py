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


def load_json_strict(path: Path) -> Any:
    """Read JSON under the shared file lock and FAIL CLOSED.

    ``locked_read`` silently returns its default when the file is not valid JSON,
    which turns a truncated or tampered ledger into an empty one and lets integrity
    checks pass vacuously. Integrity code must use this helper instead: a missing
    file raises ``FileNotFoundError`` and corrupt JSON raises ``ValueError``.
    """
    with locked_read(path, parse_json=False) as raw:
        if raw is None:
            raise FileNotFoundError(str(path))
        return json.loads(raw)


def verify_manifest_ledger_consistency(
    manifest_path: Path, ledger_path: Path, run_id: str
) -> "tuple[bool, list[str]]":
    """Check that the manifest and Artifact_Ledger agree for EVERY artifact of a run.

    The latest 'written' ledger entry of each artifact must carry the same hash the
    manifest records, and every manifest artifact must have a ledger entry. Unreadable
    or structurally invalid state is reported as inconsistent (fail-closed). If neither
    file exists yet there is nothing to compare and the result is consistent.
    """
    if not manifest_path.exists() or not ledger_path.exists():
        return True, []
    try:
        manifest = load_json_strict(manifest_path)
        ledger = load_json_strict(ledger_path)
    except (OSError, TimeoutError, ValueError) as exc:
        return False, [f"unreadable manifest/ledger: {exc}"]
    if not isinstance(manifest, dict) or not isinstance(ledger, list):
        return False, ["manifest/ledger has an unexpected structure"]

    latest = {}
    for entry in ledger:
        if isinstance(entry, dict) and entry.get("run_id") == run_id and entry.get("status") == "written":
            latest[entry.get("artifact")] = entry.get("hash")

    recorded = manifest.get("artifacts", {})
    if not isinstance(recorded, dict):
        return False, ["manifest 'artifacts' is not an object"]

    problems = []
    for name, ledger_hash in latest.items():
        info = recorded.get(name)
        manifest_hash = info.get("hash") if isinstance(info, dict) else None
        if manifest_hash != ledger_hash:
            problems.append(f"{name}: ledger hash {ledger_hash} != manifest hash {manifest_hash}")
    for name in recorded:
        if name not in latest:
            problems.append(f"{name}: recorded in manifest but has no 'written' ledger entry")
    return (not problems), problems


def count_written_artifacts(ledger_path: Path, run_id: str) -> int:
    """Number of distinct artifacts written for ``run_id`` (0 if the ledger is unreadable)."""
    try:
        ledger = load_json_strict(ledger_path)
    except (OSError, TimeoutError, ValueError):
        return 0
    if not isinstance(ledger, list):
        return 0
    return len({
        e.get("artifact")
        for e in ledger
        if isinstance(e, dict) and e.get("run_id") == run_id and e.get("status") == "written"
    })
