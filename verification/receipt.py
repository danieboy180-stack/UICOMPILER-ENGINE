import json
import hashlib
import time
import os
from pathlib import Path

# Lines that exist only to reach the line count. They are not evidence of anything.
PLACEHOLDER_LINES = {"padding", "filler", "placeholder", "lorem ipsum", "todo", "xxx", "...", "n/a", "none"}


def _evidence_lines(raw_output: str) -> list:
    return [line.strip() for line in (raw_output or "").strip().splitlines() if line.strip()]


def record_verification(stage_id: str, claim: str, raw_output: str, passed: bool) -> None:
    if passed:
        lines = _evidence_lines(raw_output)
        if len(lines) < 10:
            raise ValueError(f"Insufficient verification evidence. Output must be at least 10 non-empty lines, got {len(lines)}")

        short_strings = ["passed", "ok", "done", "success", "true"]
        if raw_output.strip().lower() in short_strings:
            raise ValueError("Insufficient verification evidence. Cannot be just 'PASSED' or 'OK'.")

        placeholders = sorted({line for line in lines if line.lower() in PLACEHOLDER_LINES})
        if placeholders:
            raise ValueError(f"Insufficient verification evidence. Placeholder lines are not evidence: {placeholders}")

        distinct = len(set(lines))
        if distinct < max(6, len(lines) // 2):
            raise ValueError(
                f"Insufficient verification evidence. Only {distinct} of {len(lines)} lines are distinct; "
                "repeating lines to reach the minimum is not evidence."
            )

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


def ingest_evidence(root, completed) -> str:
    """Observable facts about one ingest run (a finished subprocess): what it printed and what it left on disk."""
    from orchestrator.lock import locked_read
    root = Path(root)
    lines = [f"exit code: {completed.returncode}"]
    lines += [f"stdout: {line}" for line in str(completed.stdout).splitlines() if line.strip()]
    lines += [f"stderr: {line}" for line in str(completed.stderr).splitlines() if line.strip()]
    inbox, artifacts = root / "Python_Inbox", root / "Artifacts"
    lines.append(f"inbox after ingest: {sorted(p.name for p in inbox.iterdir()) if inbox.exists() else []}")
    lines.append(f"artifacts: {sorted(p.name for p in artifacts.iterdir()) if artifacts.exists() else []}")
    ledger_path = artifacts / "Artifact_Ledger.json"
    if ledger_path.exists():
        with locked_read(ledger_path, default=[]) as ledger:
            for e in ledger:
                lines.append(f"ledger: {e['artifact']} status={e['status']} hash={e['hash'][:12]} parent={(e.get('parent_hash') or 'none')[:12]}")
    for manifest_path in sorted((root / "Manifest").glob("manifest_*.json")):
        with locked_read(manifest_path, default={}) as manifest:
            lines += [f"manifest {k}: {str(v)[:60]}" for k, v in sorted(manifest.items()) if not isinstance(v, dict)]
            lines += [f"manifest artifact {k}: {str(v.get('hash'))[:12]}" for k, v in sorted(manifest.get("artifacts", {}).items())]
    out = root / "Python_Output"
    lines.append(f"moved bundle: {sorted(str(p.relative_to(root)) for p in out.rglob('*') if p.is_file()) if out.exists() else []}")
    return "\n".join(lines)
