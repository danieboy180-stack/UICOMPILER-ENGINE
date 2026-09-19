"""Deterministic resolver for Layer 3, Layer 4.1, and Layer 4.2 within layer342_bundle.txt (v1.1 Fused Prompt Spec)."""

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
from typing import Any, Literal, Optional

from pydantic import BaseModel, ValidationError

EXIT_OK = 0
EXIT_10 = 10
ALLOWED_FILES = {"layer342_bundle.txt"}


class Layer3VisualIR(BaseModel):
    schema_version: Optional[str] = None
    model_config = {"extra": "allow"}


class Layer4_1SemanticIR(BaseModel):
    schema_version: Optional[str] = None
    model_config = {"extra": "allow"}


class Layer4_2ExecutionIR(BaseModel):
    schema_version: Optional[str] = None
    model_config = {"extra": "allow"}


def _get_layer4_3_stage_index() -> int:
    try:
        from orchestrator.orchestrator import Pipeline
        pipeline = Pipeline()
        for idx, stage in enumerate(pipeline.stages):
            if type(stage).__name__ == "Layer4_3Stage":
                return idx
    except Exception:
        pass
    return 12


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


def compute_pipeline_hash(layers: list[tuple[str, str, str, str]]) -> str:
    """Compute rolling pipeline hash for executed layer tuples: (layer_name, input_hash, output_hash, parent_hash)."""
    payload = [[l[0], l[1], l[2], l[3]] for l in layers]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_pipeline_hash(declared_hash: str, layers: list[tuple[str, str, str, str]]) -> tuple[bool, str]:
    """Verify declared pipeline hash against independently computed representations."""
    cand1 = compute_pipeline_hash(layers)
    if declared_hash == cand1:
        return True, cand1
    cand2 = hashlib.sha256(":".join(f"{l[0]}:{l[1]}:{l[2]}:{l[3]}" for l in layers).encode("utf-8")).hexdigest()
    if declared_hash == cand2:
        return True, cand2
    cand3 = hashlib.sha256(json.dumps(
        [{"layer": l[0], "input_hash": l[1], "output_hash": l[2], "parent_hash": l[3]} for l in layers],
        sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if declared_hash == cand3:
        return True, cand3
    return False, cand1


def _clean_json_text(text: str) -> str:
    """Strip code fences and trailing/leading whitespace from pasted block."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_layer_status(layer_val: Any) -> str | None:
    if isinstance(layer_val, str):
        return layer_val.lower()
    if isinstance(layer_val, dict):
        st = layer_val.get("status")
        return str(st).lower() if st is not None else None
    return None


def _format_side_by_side(records: list[tuple[str, str, str, bool]]) -> str:
    """Format side-by-side comparison table of declared vs recomputed hashes."""
    header = f"{'Hash Item':<22} | {'Declared (MANIFEST_UPDATE)':<34} | {'Recomputed (On-Disk / Body)':<34} | {'Status'}"
    sep = "-" * len(header)
    lines = [
        "",
        "=" * len(header),
        "             HASH VERIFICATION & PROVENANCE CHAIN (v1.1)",
        "=" * len(header),
        header,
        sep,
    ]
    for label, decl, recomp, ok in records:
        decl_disp = (decl[:31] + "...") if len(decl) > 34 else decl
        recomp_disp = (recomp[:31] + "...") if len(recomp) > 34 else recomp
        status_disp = "MATCH [PASS]" if ok else "MISMATCH [FAIL]"
        lines.append(f"{label:<22} | {decl_disp:<34} | {recomp_disp:<34} | {status_disp}")
    lines.append("=" * len(header))
    lines.append("")
    return "\n".join(lines)


def _atomic_json_write(path: Path, value: Any) -> None:
    from orchestrator.lock import locked_write
    with locked_write(path, value):
        pass


def _read_json(path: Path, default: Any = None) -> Any:
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
    from orchestrator.lock import locked_read, locked_write, safe_unlink
    try:
        # Step 1: Create backups for all targets before any modifications
        for target in outputs.keys():
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
            existed = target.exists()
            if existed:
                with locked_read(target, parse_json=False) as existing_content:
                    if existing_content is not None:
                        with locked_write(backup, existing_content):
                            pass
            backups.append({
                "target": str(target.relative_to(root)),
                "backup": str(backup.relative_to(root)),
                "existed": existed,
            })

        # Step 2: Write transaction journal BEFORE modifying targets (True WAL)
        _atomic_json_write(transaction_path, {"backups": backups})

        # Step 3: Atomically write new target values
        for target, value in outputs.items():
            with locked_write(target, value):
                pass
    except Exception:
        _recover_transaction(root, run_id)
        raise
    else:
        for item in backups:
            safe_unlink(root / item["backup"])
        safe_unlink(transaction_path)


def _handle_halt(root: Path, selected: Path, run_id: str, force_clear_halted: bool) -> None:
    """
    DECISION / FAIL-CLOSED POLICY FOR HALTED BUNDLES:
    By default, halted/failed bundles are kept in Python_Inbox/ so that developers or operators
    can directly inspect the rejected payload, diagnose gate failures or hash mismatches, and
    remediate without data loss. If --force-clear-halted is passed, the script explicitly moves
    the failed bundle into Python_Output/runs/{run_id}/halted/ to clear the inbox.
    """
    if force_clear_halted:
        halted_dir = root / "Python_Output" / "runs" / run_id / "halted"
        halted_dir.mkdir(parents=True, exist_ok=True)
        dest = halted_dir / selected.name
        try:
            shutil.move(str(selected), str(dest))
            print(f"[IngestLayer342] --force-clear-halted: Moved halted bundle to {dest}")
        except Exception as e:
            print(f"[IngestLayer342] WARNING: Failed to move halted bundle {selected} to {dest}: {e}")
    else:
        # Default policy: keep in Python_Inbox/ for inspection and debugging
        pass


def ingest(root: Path, force_clear_halted: bool = False) -> int:
    root = root.resolve()
    inbox = root / "Python_Inbox"

    # Only check allow-listed file; strictly do NOT glob-scan Python_Inbox
    selected = next((inbox / name for name in sorted(ALLOWED_FILES) if (inbox / name).is_file()), None)
    if selected is None:
        return EXIT_OK

    try:
        raw_text = selected.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[IngestLayer342] ERROR reading {selected}: {e}")
        return EXIT_10

    # CHANGE 1: Parse named tags independently with re.search (NO re.finditer)
    m_l3 = re.search(r"<LAYER3_OUTPUT>(.*?)</LAYER3_OUTPUT>", raw_text, re.DOTALL)
    m_l4_1 = re.search(r"<LAYER4_1_OUTPUT>(.*?)</LAYER4_1_OUTPUT>", raw_text, re.DOTALL)
    m_l4_2 = re.search(r"<LAYER4_2_OUTPUT>(.*?)</LAYER4_2_OUTPUT>", raw_text, re.DOTALL)

    m_manifest = re.search(r"<MANIFEST_UPDATE>(.*?)</MANIFEST_UPDATE>", raw_text, re.DOTALL)
    m_report = re.search(r"<REPORT>(.*?)</REPORT>", raw_text, re.DOTALL)

    if m_manifest is None:
        print("[IngestLayer342] ERROR: Missing required <MANIFEST_UPDATE> tag in bundle.")
        return EXIT_10

    if m_report is None:
        print("[IngestLayer342] ERROR: Missing required <REPORT> tag in bundle.")
        return EXIT_10

    # Parse single <REPORT> object once
    report_raw_text = _clean_json_text(m_report.group(1))
    report_data: dict[str, Any] = {}
    try:
        report_data = json.loads(report_raw_text)
    except json.JSONDecodeError:
        # Fallback to key-value line parsing for simple textual reports
        report_data = {"layers": {}}
        for line in report_raw_text.splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip().lower()
                v = v.strip()
                if "layer" in k:
                    if "342" in v:
                        report_data["layers"]["layer3"] = {"status": "pass"}
                        report_data["layers"]["layer4_1"] = {"status": "pass"}
                        report_data["layers"]["layer4_2"] = {"status": "pass"}
                    elif "3" in v:
                        report_data["layers"]["layer3"] = {"status": "pass"}
                    elif "4_1" in v or "4.1" in v:
                        report_data["layers"]["layer4_1"] = {"status": "pass"}
                    elif "4_2" in v or "4.2" in v:
                        report_data["layers"]["layer4_2"] = {"status": "pass"}
                elif "status" in k:
                    st_val = v.lower()
                    for l_key in ("layer3", "layer4_1", "layer4_2"):
                        if l_key in report_data["layers"]:
                            report_data["layers"][l_key]["status"] = st_val

    if not isinstance(report_data, dict):
        report_data = {}

    report_layers = report_data.get("layers", {})
    if not isinstance(report_layers, dict):
        report_layers = {}

    l3_status = _extract_layer_status(report_layers.get("layer3"))
    l4_1_status = _extract_layer_status(report_layers.get("layer4_1") or report_layers.get("layer41") or report_layers.get("layer4.1"))
    l4_2_status = _extract_layer_status(report_layers.get("layer4_2") or report_layers.get("layer42") or report_layers.get("layer4.2"))

    halt_reason = report_data.get("halt_reason")
    halt_stage = report_data.get("halt_stage")
    blocking_object = report_data.get("blocking_object")

    # Parse single <MANIFEST_UPDATE> object once
    manifest_update_text = _clean_json_text(m_manifest.group(1))
    try:
        manifest_update = json.loads(manifest_update_text)
    except json.JSONDecodeError as e:
        print(f"[IngestLayer342] ERROR: Invalid JSON in <MANIFEST_UPDATE>: {e}")
        return EXIT_10

    if not isinstance(manifest_update, dict):
        print("[IngestLayer342] ERROR: <MANIFEST_UPDATE> is not a JSON object.")
        return EXIT_10

    from orchestrator.schema import extract_schema_version, is_valid_schema_version
    schema_ver = extract_schema_version(manifest_update, report_data, raw_text=raw_text)
    if not is_valid_schema_version(schema_ver):
        print(f"[IngestLayer342] ERROR: Unsupported schema version '{schema_ver}'. Expected '2.1' or 'v2.1'.")
        return EXIT_10

    # Extract declared hashes from MANIFEST_UPDATE top level
    layer3_input_hash = manifest_update.get("layer3_input_hash")
    layer3_output_hash = manifest_update.get("layer3_output_hash")

    layer4_1_input_hash = manifest_update.get("layer4_1_input_hash") or manifest_update.get("layer41_input_hash") or manifest_update.get("layer4.1_input_hash")
    layer4_1_output_hash = manifest_update.get("layer4_1_output_hash") or manifest_update.get("layer41_output_hash") or manifest_update.get("layer4.1_output_hash")

    layer4_2_input_hash = manifest_update.get("layer4_2_input_hash") or manifest_update.get("layer42_input_hash") or manifest_update.get("layer4.2_input_hash")
    layer4_2_output_hash = manifest_update.get("layer4_2_output_hash") or manifest_update.get("layer42_output_hash") or manifest_update.get("layer4.2_output_hash")

    declared_pipeline_hash = manifest_update.get("pipeline_hash")

    # Locate target run by scanning root/Manifest/manifest_*.json
    manifest_dir = root / "Manifest"
    if not manifest_dir.exists():
        print(f"[IngestLayer342] ERROR: Manifest directory does not exist: {manifest_dir}")
        return EXIT_10

    candidates: list[tuple[Path, float, dict[str, Any]]] = []
    for mf in manifest_dir.glob("manifest_*.json"):
        if mf.name.endswith(".lock") or mf.name.endswith(".tmp") or mf.name.endswith(".bak"):
            continue
        try:
            m_data = _read_json(mf, default=None)
            if not isinstance(m_data, dict):
                continue
            artifacts = m_data.get("artifacts", {})
            if not isinstance(artifacts, dict):
                continue
            has_l2 = artifacts.get("layer2_csir") is not None
            has_l3 = artifacts.get("layer3_visual_ir") is not None
            if has_l2 and not has_l3:
                candidates.append((mf, mf.stat().st_mtime, m_data))
        except (OSError, Exception):
            continue

    if len(candidates) == 0:
        print("[IngestLayer342] ERROR: Zero candidate manifests found where artifacts.layer2_csir exists and artifacts.layer3_visual_ir does not exist.")
        return EXIT_10

    candidates.sort(key=lambda c: c[1], reverse=True)

    # Check for ambiguous match (identical mtime)
    if len(candidates) > 1 and abs(candidates[0][1] - candidates[1][1]) < 1e-4:
        print(f"[IngestLayer342] ERROR: Multiple candidate manifests match ambiguously with identical mtime ({candidates[0][0].name} and {candidates[1][0].name}). Never guessing.")
        return EXIT_10

    target_manifest_path, _, target_manifest = candidates[0]
    run_id = target_manifest.get("run_id")
    if not run_id:
        stem = target_manifest_path.stem
        run_id = stem[len("manifest_"):] if stem.startswith("manifest_") else stem

    project_id = target_manifest.get("project_id", "default_project")

    # Defense-in-depth: independently re-verify layer1_ccir.json and layer2_csir.json on disk
    artifacts_dir = root / "Artifacts"
    l1_path = artifacts_dir / "layer1_ccir.json"
    l2_path = artifacts_dir / "layer2_csir.json"

    if not l1_path.exists() or not l2_path.exists():
        print(f"[IngestLayer342] ERROR: Upstream artifact missing from disk: {l1_path} or {l2_path}")
        return EXIT_10

    l1_data = _read_json(l1_path, None)
    l2_data = _read_json(l2_path, None)
    if l1_data is None or l2_data is None:
        print("[IngestLayer342] ERROR: Failed to read layer1_ccir.json or layer2_csir.json from disk.")
        return EXIT_10

    disk_l1_hash = _hash(l1_data)
    disk_l2_hash = _hash(l2_data)

    manifest_artifacts = target_manifest.get("artifacts", {})
    manifest_l1_hash = manifest_artifacts.get("layer1_ccir", {}).get("hash")
    manifest_l2_hash = manifest_artifacts.get("layer2_csir", {}).get("hash")

    if disk_l1_hash != manifest_l1_hash:
        print(f"[IngestLayer342] ERROR: Independent re-verification failed: layer1_ccir disk hash ({disk_l1_hash}) != manifest hash ({manifest_l1_hash}). Refusing paste.")
        return EXIT_10

    if disk_l2_hash != manifest_l2_hash:
        print(f"[IngestLayer342] ERROR: Independent re-verification failed: layer2_csir disk hash ({disk_l2_hash}) != manifest hash ({manifest_l2_hash}). Refusing paste.")
        return EXIT_10

    # Strict Gate Ordering check:
    # If report.layers.layer3.status != "pass", do NOT process layer4_1 even if tag is present (malformed bundle)
    if l3_status != "pass" and m_l4_1 is not None:
        print(f"[IngestLayer342] ERROR: Malformed bundle: <LAYER4_1_OUTPUT> tag present but Layer 3 status is '{l3_status}' (not 'pass'). Refusing malformed bundle.")
        return EXIT_10

    # Same for layer4_2 if layer4_1 did not pass
    if l4_1_status != "pass" and m_l4_2 is not None:
        print(f"[IngestLayer342] ERROR: Malformed bundle: <LAYER4_2_OUTPUT> tag present but Layer 4.1 status is '{l4_1_status}' (not 'pass'). Refusing malformed bundle.")
        return EXIT_10

    ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
    ledger = _read_json(ledger_path, [])
    if not isinstance(ledger, list):
        ledger = []

    executed_layers: list[tuple[str, str, str, str]] = []
    verification_records: list[tuple[str, str, str, bool]] = []
    pending_outputs: dict[Path, Any] = {}

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # =========================================================================
    # 1. LAYER 3: Design System Reconstruction
    # =========================================================================
    if m_l3 is None and l3_status is None:
        # Layer 3 never ran in this bundle
        return EXIT_OK

    # 1.a Independently re-verify layer3_input_hash against disk layer2_csir.json
    declared_l3_in = str(layer3_input_hash) if layer3_input_hash is not None else "MISSING"
    match_l3_in = (layer3_input_hash == disk_l2_hash)
    verification_records.append(("layer3_input_hash", declared_l3_in, disk_l2_hash, match_l3_in))

    if not match_l3_in:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer3_input_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l3_in}, Recomputed from disk layer2_csir.json: {disk_l2_hash}. Refusing paste.")
        return EXIT_10

    # Parse Layer 3 JSON body if tag present
    layer3_raw: dict[str, Any] = {}
    if m_l3 is not None:
        l3_body_text = _clean_json_text(m_l3.group(1))
        try:
            layer3_raw = json.loads(l3_body_text)
        except json.JSONDecodeError as e:
            print(f"[IngestLayer342] ERROR: Invalid JSON in <LAYER3_OUTPUT>: {e}")
            return EXIT_10
        if not isinstance(layer3_raw, dict):
            print("[IngestLayer342] ERROR: <LAYER3_OUTPUT> is not a JSON object.")
            return EXIT_10

    # Check validation failure in body or report
    is_failed_3 = (l3_status == "fail")
    fail_reason_3 = halt_reason or "Layer 3 validation gate failed"

    val_failed_3 = layer3_raw.get("validation_failed")
    if val_failed_3 is True or (isinstance(val_failed_3, str) and val_failed_3.lower() == "true"):
        is_failed_3 = True
        fail_reason_3 = layer3_raw.get("halt_reason") or layer3_raw.get("error") or fail_reason_3

    vg_3 = layer3_raw.get("VALIDATION_GATE") or layer3_raw.get("validation_gate")
    if isinstance(vg_3, dict):
        if vg_3.get("gate_result") == "fail":
            is_failed_3 = True
            fail_reason_3 = vg_3.get("halt_reason") or vg_3.get("reason") or fail_reason_3
        elif vg_3.get("gate_result") == "pass" and not val_failed_3:
            is_failed_3 = False

    if is_failed_3:
        failed_file_3 = root / "Python_Output" / "runs" / run_id / "FAILED_layer3.json"
        failed_data_3 = layer3_raw if layer3_raw else {
            "validation_failed": True,
            "halt_reason": fail_reason_3,
            "halt_stage": halt_stage or "Layer3",
            "blocking_object": blocking_object,
        }
        failed_hash_3 = _hash(failed_data_3)

        ledger.append({
            "artifact": "layer3_visual_ir",
            "version": 1,
            "run_id": run_id,
            "project_id": project_id,
            "timestamp": now_iso,
            "hash": failed_hash_3,
            "parent_hash": layer3_input_hash,
            "status": "failed_gate",
        })

        _commit_outputs(root, {failed_file_3: failed_data_3, ledger_path: ledger}, run_id)
        _handle_halt(root, selected, run_id, force_clear_halted)
        print(_format_side_by_side(verification_records))
        return EXIT_10

    # Passing path for Layer 3:
    try:
        _model_validate(Layer3VisualIR, layer3_raw)
    except ValidationError as ve:
        print(f"[IngestLayer342] ERROR: Layer 3 schema validation failed: {ve}")
        return EXIT_10

    recomputed_l3_output_hash = _hash(layer3_raw)
    declared_l3_out = str(layer3_output_hash) if layer3_output_hash is not None else "MISSING"
    match_l3_out = (layer3_output_hash == recomputed_l3_output_hash)
    verification_records.append(("layer3_output_hash", declared_l3_out, recomputed_l3_output_hash, match_l3_out))

    if not match_l3_out:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer3_output_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l3_out}, Recomputed from <LAYER3_OUTPUT>: {recomputed_l3_output_hash}. Refusing to write artifact.")
        return EXIT_10

    l3_artifact_path = root / "Artifacts" / "layer3_visual_ir.json"
    pending_outputs[l3_artifact_path] = layer3_raw

    ledger.append({
        "artifact": "layer3_visual_ir",
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": now_iso,
        "hash": recomputed_l3_output_hash,
        "parent_hash": layer3_input_hash,
        "status": "written",
    })

    target_manifest.setdefault("artifacts", {})["layer3_visual_ir"] = {
        "hash": recomputed_l3_output_hash,
        "run_id": run_id,
        "version": 1,
    }
    target_manifest["layer3_hash"] = recomputed_l3_output_hash
    executed_layers.append(("layer3", layer3_input_hash, recomputed_l3_output_hash, layer3_input_hash))

    # =========================================================================
    # 2. LAYER 4.1: Canonical Semantic Engine
    # =========================================================================
    if m_l4_1 is None and l4_1_status is None:
        # Layer 4.1 never ran in this bundle (halted upstream after Layer 3)
        # Commit Layer 3 outputs and return EXIT_OK
        if declared_pipeline_hash:
            ok_pipe, recomp_pipe = verify_pipeline_hash(declared_pipeline_hash, executed_layers)
            verification_records.append(("pipeline_hash", str(declared_pipeline_hash), recomp_pipe, ok_pipe))
            print(_format_side_by_side(verification_records))
            if not ok_pipe:
                print(f"[IngestLayer342] ERROR: pipeline_hash mismatch! Declared: {declared_pipeline_hash}, Recomputed: {recomp_pipe}")
                return EXIT_10
            target_manifest["pipeline_hash"] = recomp_pipe
        else:
            print(_format_side_by_side(verification_records))

        pending_outputs[target_manifest_path] = target_manifest
        pending_outputs[ledger_path] = ledger
        _commit_outputs(root, pending_outputs, run_id)
        return EXIT_OK

    # 2.a Independently re-verify layer4_1_input_hash against disk/predecessor layer3_visual_ir.json
    declared_l4_1_in = str(layer4_1_input_hash) if layer4_1_input_hash is not None else "MISSING"
    match_l4_1_in = (layer4_1_input_hash == recomputed_l3_output_hash)
    verification_records.append(("layer4_1_input_hash", declared_l4_1_in, recomputed_l3_output_hash, match_l4_1_in))

    if not match_l4_1_in:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer4_1_input_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l4_1_in}, Recomputed from predecessor layer3_visual_ir: {recomputed_l3_output_hash}. Refusing paste.")
        return EXIT_10

    # Parse Layer 4.1 JSON body if tag present
    layer4_1_raw: dict[str, Any] = {}
    if m_l4_1 is not None:
        l4_1_body_text = _clean_json_text(m_l4_1.group(1))
        try:
            layer4_1_raw = json.loads(l4_1_body_text)
        except json.JSONDecodeError as e:
            print(f"[IngestLayer342] ERROR: Invalid JSON in <LAYER4_1_OUTPUT>: {e}")
            return EXIT_10
        if not isinstance(layer4_1_raw, dict):
            print("[IngestLayer342] ERROR: <LAYER4_1_OUTPUT> is not a JSON object.")
            return EXIT_10

    is_failed_4_1 = (l4_1_status == "fail")
    fail_reason_4_1 = halt_reason or "Layer 4.1 validation gate failed"

    val_failed_4_1 = layer4_1_raw.get("validation_failed")
    if val_failed_4_1 is True or (isinstance(val_failed_4_1, str) and val_failed_4_1.lower() == "true"):
        is_failed_4_1 = True
        fail_reason_4_1 = layer4_1_raw.get("halt_reason") or layer4_1_raw.get("error") or fail_reason_4_1

    vg_4_1 = layer4_1_raw.get("VALIDATION_GATE") or layer4_1_raw.get("validation_gate")
    if isinstance(vg_4_1, dict):
        if vg_4_1.get("gate_result") == "fail":
            is_failed_4_1 = True
            fail_reason_4_1 = vg_4_1.get("halt_reason") or vg_4_1.get("reason") or fail_reason_4_1
        elif vg_4_1.get("gate_result") == "pass" and not val_failed_4_1:
            is_failed_4_1 = False

    if is_failed_4_1:
        failed_file_4_1 = root / "Python_Output" / "runs" / run_id / "FAILED_layer4_1.json"
        failed_data_4_1 = layer4_1_raw if layer4_1_raw else {
            "validation_failed": True,
            "halt_reason": fail_reason_4_1,
            "halt_stage": halt_stage or "Layer4.1",
            "blocking_object": blocking_object,
        }
        failed_hash_4_1 = _hash(failed_data_4_1)

        ledger.append({
            "artifact": "layer4_1_semantic_ir",
            "version": 1,
            "run_id": run_id,
            "project_id": project_id,
            "timestamp": now_iso,
            "hash": failed_hash_4_1,
            "parent_hash": layer4_1_input_hash,
            "status": "failed_gate",
        })

        pending_outputs[failed_file_4_1] = failed_data_4_1
        pending_outputs[target_manifest_path] = target_manifest
        pending_outputs[ledger_path] = ledger
        _commit_outputs(root, pending_outputs, run_id)
        _handle_halt(root, selected, run_id, force_clear_halted)
        print(_format_side_by_side(verification_records))
        return EXIT_10

    # Passing path for Layer 4.1:
    try:
        _model_validate(Layer4_1SemanticIR, layer4_1_raw)
    except ValidationError as ve:
        print(f"[IngestLayer342] ERROR: Layer 4.1 schema validation failed: {ve}")
        return EXIT_10

    recomputed_l4_1_output_hash = _hash(layer4_1_raw)
    declared_l4_1_out = str(layer4_1_output_hash) if layer4_1_output_hash is not None else "MISSING"
    match_l4_1_out = (layer4_1_output_hash == recomputed_l4_1_output_hash)
    verification_records.append(("layer4_1_output_hash", declared_l4_1_out, recomputed_l4_1_output_hash, match_l4_1_out))

    if not match_l4_1_out:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer4_1_output_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l4_1_out}, Recomputed from <LAYER4_1_OUTPUT>: {recomputed_l4_1_output_hash}. Refusing to write artifact.")
        return EXIT_10

    l4_1_artifact_path = root / "Artifacts" / "layer4_1_semantic_ir.json"
    pending_outputs[l4_1_artifact_path] = layer4_1_raw

    ledger.append({
        "artifact": "layer4_1_semantic_ir",
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": now_iso,
        "hash": recomputed_l4_1_output_hash,
        "parent_hash": layer4_1_input_hash,
        "status": "written",
    })

    target_manifest.setdefault("artifacts", {})["layer4_1_semantic_ir"] = {
        "hash": recomputed_l4_1_output_hash,
        "run_id": run_id,
        "version": 1,
    }
    target_manifest["layer4_1_hash"] = recomputed_l4_1_output_hash
    executed_layers.append(("layer4_1", layer4_1_input_hash, recomputed_l4_1_output_hash, layer4_1_input_hash))

    # =========================================================================
    # 3. LAYER 4.2: Canonical Execution Engine
    # =========================================================================
    if m_l4_2 is None and l4_2_status is None:
        # Layer 4.2 never ran in this bundle (halted upstream after Layer 4.1)
        if declared_pipeline_hash:
            ok_pipe, recomp_pipe = verify_pipeline_hash(declared_pipeline_hash, executed_layers)
            verification_records.append(("pipeline_hash", str(declared_pipeline_hash), recomp_pipe, ok_pipe))
            print(_format_side_by_side(verification_records))
            if not ok_pipe:
                print(f"[IngestLayer342] ERROR: pipeline_hash mismatch! Declared: {declared_pipeline_hash}, Recomputed: {recomp_pipe}")
                return EXIT_10
            target_manifest["pipeline_hash"] = recomp_pipe
        else:
            print(_format_side_by_side(verification_records))

        pending_outputs[target_manifest_path] = target_manifest
        pending_outputs[ledger_path] = ledger
        _commit_outputs(root, pending_outputs, run_id)
        return EXIT_OK

    # 3.a Independently re-verify layer4_2_input_hash against disk/predecessor layer4_1_semantic_ir.json
    declared_l4_2_in = str(layer4_2_input_hash) if layer4_2_input_hash is not None else "MISSING"
    match_l4_2_in = (layer4_2_input_hash == recomputed_l4_1_output_hash)
    verification_records.append(("layer4_2_input_hash", declared_l4_2_in, recomputed_l4_1_output_hash, match_l4_2_in))

    if not match_l4_2_in:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer4_2_input_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l4_2_in}, Recomputed from predecessor layer4_1_semantic_ir: {recomputed_l4_1_output_hash}. Refusing paste.")
        return EXIT_10

    # Parse Layer 4.2 JSON body if tag present
    layer4_2_raw: dict[str, Any] = {}
    if m_l4_2 is not None:
        l4_2_body_text = _clean_json_text(m_l4_2.group(1))
        try:
            layer4_2_raw = json.loads(l4_2_body_text)
        except json.JSONDecodeError as e:
            print(f"[IngestLayer342] ERROR: Invalid JSON in <LAYER4_2_OUTPUT>: {e}")
            return EXIT_10
        if not isinstance(layer4_2_raw, dict):
            print("[IngestLayer342] ERROR: <LAYER4_2_OUTPUT> is not a JSON object.")
            return EXIT_10

    is_failed_4_2 = (l4_2_status == "fail")
    fail_reason_4_2 = halt_reason or "Layer 4.2 validation gate failed"

    val_failed_4_2 = layer4_2_raw.get("validation_failed")
    if val_failed_4_2 is True or (isinstance(val_failed_4_2, str) and val_failed_4_2.lower() == "true"):
        is_failed_4_2 = True
        fail_reason_4_2 = layer4_2_raw.get("halt_reason") or layer4_2_raw.get("error") or fail_reason_4_2

    vg_4_2 = layer4_2_raw.get("VALIDATION_GATE") or layer4_2_raw.get("validation_gate")
    if isinstance(vg_4_2, dict):
        if vg_4_2.get("gate_result") == "fail":
            is_failed_4_2 = True
            fail_reason_4_2 = vg_4_2.get("halt_reason") or vg_4_2.get("reason") or fail_reason_4_2
        elif vg_4_2.get("gate_result") == "pass" and not val_failed_4_2:
            is_failed_4_2 = False

    val_status_4_2 = layer4_2_raw.get("validation")
    if isinstance(val_status_4_2, dict) and val_status_4_2.get("status") == "fail":
        is_failed_4_2 = True
        fail_reason_4_2 = val_status_4_2.get("reason") or fail_reason_4_2

    if is_failed_4_2:
        failed_file_4_2 = root / "Python_Output" / "runs" / run_id / "FAILED_layer4_2.json"
        failed_data_4_2 = layer4_2_raw if layer4_2_raw else {
            "validation_failed": True,
            "halt_reason": fail_reason_4_2,
            "halt_stage": halt_stage or "Layer4.2",
            "blocking_object": blocking_object,
        }
        failed_hash_4_2 = _hash(failed_data_4_2)

        ledger.append({
            "artifact": "layer4_2_execution_ir",
            "version": 1,
            "run_id": run_id,
            "project_id": project_id,
            "timestamp": now_iso,
            "hash": failed_hash_4_2,
            "parent_hash": layer4_2_input_hash,
            "status": "failed_gate",
        })

        pending_outputs[failed_file_4_2] = failed_data_4_2
        pending_outputs[target_manifest_path] = target_manifest
        pending_outputs[ledger_path] = ledger
        _commit_outputs(root, pending_outputs, run_id)
        _handle_halt(root, selected, run_id, force_clear_halted)
        print(_format_side_by_side(verification_records))
        return EXIT_10

    # Passing path for Layer 4.2:
    try:
        _model_validate(Layer4_2ExecutionIR, layer4_2_raw)
    except ValidationError as ve:
        print(f"[IngestLayer342] ERROR: Layer 4.2 schema validation failed: {ve}")
        return EXIT_10

    recomputed_l4_2_output_hash = _hash(layer4_2_raw)
    declared_l4_2_out = str(layer4_2_output_hash) if layer4_2_output_hash is not None else "MISSING"
    match_l4_2_out = (layer4_2_output_hash == recomputed_l4_2_output_hash)
    verification_records.append(("layer4_2_output_hash", declared_l4_2_out, recomputed_l4_2_output_hash, match_l4_2_out))

    if not match_l4_2_out:
        print(_format_side_by_side(verification_records))
        print(f"[IngestLayer342] ERROR: layer4_2_output_hash mismatch! Declared in MANIFEST_UPDATE: {declared_l4_2_out}, Recomputed from <LAYER4_2_OUTPUT>: {recomputed_l4_2_output_hash}. Refusing to write artifact.")
        return EXIT_10

    l4_2_artifact_path = root / "Artifacts" / "layer4_2_execution_ir.json"
    pending_outputs[l4_2_artifact_path] = layer4_2_raw

    ledger.append({
        "artifact": "layer4_2_execution_ir",
        "version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "timestamp": now_iso,
        "hash": recomputed_l4_2_output_hash,
        "parent_hash": layer4_2_input_hash,
        "status": "written",
    })

    target_manifest.setdefault("artifacts", {})["layer4_2_execution_ir"] = {
        "hash": recomputed_l4_2_output_hash,
        "run_id": run_id,
        "version": 1,
    }
    target_manifest["layer4_2_hash"] = recomputed_l4_2_output_hash
    executed_layers.append(("layer4_2", layer4_2_input_hash, recomputed_l4_2_output_hash, layer4_2_input_hash))

    # =========================================================================
    # PIPELINE HASH VERIFICATION
    # =========================================================================
    if declared_pipeline_hash:
        ok_pipe, recomp_pipe = verify_pipeline_hash(declared_pipeline_hash, executed_layers)
        verification_records.append(("pipeline_hash", str(declared_pipeline_hash), recomp_pipe, ok_pipe))
        if not ok_pipe:
            print(_format_side_by_side(verification_records))
            print(f"[IngestLayer342] ERROR: pipeline_hash mismatch! Declared in MANIFEST_UPDATE: {declared_pipeline_hash}, Recomputed from executed layers: {recomp_pipe}. Refusing to finalize manifest.")
            return EXIT_10
        target_manifest["pipeline_hash"] = recomp_pipe
    else:
        # Compute and record pipeline_hash dynamically
        recomp_pipe = compute_pipeline_hash(executed_layers)
        target_manifest["pipeline_hash"] = recomp_pipe
        verification_records.append(("pipeline_hash", recomp_pipe, recomp_pipe, True))

    # Print side-by-side verification table
    print(_format_side_by_side(verification_records))

    # Advance manifest.next_layer dynamically to Layer4_3Stage's real index
    layer4_3_stage_idx = _get_layer4_3_stage_index()
    target_manifest["next_layer"] = layer4_3_stage_idx

    pending_outputs[target_manifest_path] = target_manifest
    pending_outputs[ledger_path] = ledger

    # Commit all outputs atomically
    _commit_outputs(root, pending_outputs, run_id)

    # Move bundle to Python_Output/runs/{run_id}/ingested/
    destination = root / "Python_Output" / "runs" / run_id / "ingested" / selected.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(selected), str(destination))
    except (OSError, Exception) as e:
        print(f"[IngestLayer342] WARNING: Failed to move {selected} to {destination}: {e}")

    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve allow-listed Layer 342 Python_Inbox bundles (v1.1 Fused Prompt Spec).")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--force-clear-halted", action="store_true", help="Explicitly move halted/failed bundles to Python_Output/runs/{run_id}/halted/ instead of leaving in Python_Inbox/ for inspection")
    args = parser.parse_args()
    return ingest(args.root, force_clear_halted=args.force_clear_halted)


if __name__ == "__main__":
    sys.exit(main())
