"""The Seal step: hashes, ids and provenance are computed by code, never by an LLM.

Why: the fused prompts ask the model to emit SHA-256 values it cannot compute, and ingest*.py refuses any mismatch;
ingest.py also reads image_hash/project_id/pipeline_id from the Layer 1 *body* while the prompt puts them in
MANIFEST_UPDATE (so real runs silently get 'default_image_hash'). The seal recomputes everything, stores the model's
declared values next to the computed ones (seal report), and emits a bundle the unchanged ingest scripts accept.
The raw bundle is kept untouched by the caller; the sealed copy is a derived artifact.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import chash, load_body, parse_tags

STRIP_KEYS = {"HASH_CHAIN", "hash_chain", "timestamp", "execution_metadata"}


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in STRIP_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def substantive_hash(body: Any) -> str:
    """Prompt rule: output_hash = sha256(canonical IR with HASH_CHAIN + runtime timestamps omitted)."""
    return chash(_strip(body))


def pipeline_hash(tuples: list[list[str]]) -> str:
    """Prompt rule: rolling hash of ordered [layer_name, input_hash, output_hash, parent_hash] tuples."""
    return chash(tuples)


def _unwrap(body: dict, wrapper: str) -> dict:
    inner = body.get(wrapper)
    return inner if isinstance(inner, dict) and len(body) == 1 else body


def _chain_slot(layer: str, body: dict) -> dict:
    """Return the dict that holds (or must hold) the layer's hash chain, creating it only where the prompt says."""
    root = _unwrap(body, {"L3": "DESIGN_SYSTEM_IR", "L41": "CANONICAL_SEMANTIC_IR", "L42": "CANONICAL_EXECUTION_IR"}[layer])
    if layer == "L3":
        dh = root.get("DESIGN_HASH")
        if not isinstance(dh, dict):
            dh = root["DESIGN_HASH"] = {}
        slot = dh.get("hash_chain")
        if not isinstance(slot, dict):
            slot = dh["hash_chain"] = {}
        return slot
    for key in ("HASH_CHAIN", "hash_chain"):
        if isinstance(root.get(key), dict):
            return root[key]
    root["HASH_CHAIN"] = {}
    return root["HASH_CHAIN"]


def _record(rep: list, field: str, declared: Any, computed: Any) -> None:
    rep.append({"field": field, "declared": declared, "computed": computed, "changed": declared != computed})


def seal12(text: str, source_sha256: str, project_id: str, pipeline_id: str = "ui_god") -> tuple[str, dict]:
    """Seal a layer12 bundle: inject engine-owned provenance where ingest.py actually reads it."""
    tags = parse_tags(text, ("LAYER1_OUTPUT", "LAYER2_OUTPUT", "MANIFEST_UPDATE", "REPORT"))
    l1, e1 = load_body(tags.get("LAYER1_OUTPUT", ""))
    l2, e2 = load_body(tags.get("LAYER2_OUTPUT", ""))
    if not isinstance(l1, dict) or not isinstance(l2, dict):
        raise ValueError(f"cannot seal: L1 error={e1} L2 error={e2}")
    manifest, _ = load_body(tags.get("MANIFEST_UPDATE", "{}"))
    report, _ = load_body(tags.get("REPORT", "{}"))
    manifest = manifest if isinstance(manifest, dict) else {}
    report = report if isinstance(report, dict) else {}
    rep: list[dict] = []
    l1 = copy.deepcopy(l1)
    for key, val in (("project_id", project_id), ("pipeline_id", pipeline_id), ("image_hash", source_sha256)):
        _record(rep, f"layer1.{key}", l1.get(key, manifest.get(key)), val)
        l1[key] = val
    h1, h2 = chash(l1), chash(l2)
    new_manifest = dict(manifest)
    for key, val in (("pipeline_id", pipeline_id), ("image_hash", source_sha256), ("layer1_hash", h1),
                     ("layer2_hash", h2), ("schema_version", manifest.get("schema_version") or "v2.1")):
        _record(rep, f"manifest.{key}", manifest.get(key), val)
        new_manifest[key] = val
    new_report = dict(report)
    for key, val in (("image_hash", source_sha256), ("layer1_output_hash", h1), ("layer2_output_hash", h2)):
        _record(rep, f"report.{key}", report.get(key), val)
        new_report[key] = val
    out = (f"<LAYER1_OUTPUT>\n{_dump(l1)}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{_dump(l2)}\n</LAYER2_OUTPUT>\n"
           f"<MANIFEST_UPDATE>\n{_dump(new_manifest)}\n</MANIFEST_UPDATE>\n<REPORT>\n{_dump(new_report)}\n</REPORT>\n")
    return out, {"kind": "12", "changes": rep, "layer1_hash": h1, "layer2_hash": h2}


def seal342(text: str, l2_disk_hash: str, run_id: str) -> tuple[str, dict]:
    """Seal a layer342 bundle against the Layer 2 artifact already on disk."""
    names = {"L3": "LAYER3_OUTPUT", "L41": "LAYER4_1_OUTPUT", "L42": "LAYER4_2_OUTPUT"}
    tags = parse_tags(text, tuple(names.values()) + ("MANIFEST_UPDATE", "REPORT"))
    bodies: dict[str, dict] = {}
    for layer, tag in names.items():
        if tag in tags:
            b, err = load_body(tags[tag])
            if not isinstance(b, dict):
                raise ValueError(f"cannot seal: <{tag}> invalid ({err})")
            bodies[layer] = copy.deepcopy(b)
    manifest, _ = load_body(tags.get("MANIFEST_UPDATE", "{}"))
    report, _ = load_body(tags.get("REPORT", "{}"))
    manifest = dict(manifest) if isinstance(manifest, dict) else {}
    report = dict(report) if isinstance(report, dict) else {}
    layers_rep = report.get("layers") if isinstance(report.get("layers"), dict) else {}
    rep: list[dict] = []
    in_hash, parent = l2_disk_hash, l2_disk_hash
    tuples: list[list[str]] = []
    artifact_hash: dict[str, str] = {}
    layer_in: dict[str, str] = {}
    keymap = {"L3": "layer3", "L41": "layer4_1", "L42": "layer4_2"}
    for layer in ("L3", "L41", "L42"):
        if layer not in bodies:
            continue
        body = bodies[layer]
        passed = str((layers_rep.get(keymap[layer]) or {}).get("status", "pass")).lower() != "fail"
        layer_in[layer] = in_hash
        if passed:  # never invent a chain for a failed/halted layer
            out_h = substantive_hash(body)
            tuples.append([keymap[layer], in_hash, out_h, parent])
            slot = _chain_slot(layer, body)
            for key, val in (("input_hash", in_hash), ("output_hash", out_h), ("parent_hash", parent),
                             ("pipeline_hash", pipeline_hash(tuples)), ("algorithm", "sha256")):
                _record(rep, f"{keymap[layer]}.chain.{key}", slot.get(key), val)
                slot[key] = val
            parent = out_h
        artifact_hash[layer] = chash(body)
        in_hash = artifact_hash[layer]
        lr = layers_rep.get(keymap[layer])
        if isinstance(lr, dict) and passed:
            for key, val in (("input_hash", layer_in[layer]), ("output_hash", artifact_hash[layer])):
                _record(rep, f"report.layers.{keymap[layer]}.{key}", lr.get(key), val)
                lr[key] = val
    new_manifest = dict(manifest)
    new_manifest.pop("pipeline_hash", None)  # ingest_layer342 computes + records it when absent
    for layer, key in (("L3", "layer3"), ("L41", "layer4_1"), ("L42", "layer4_2")):
        if layer in bodies:
            for suffix, val in (("input_hash", layer_in[layer]), ("output_hash", artifact_hash[layer])):
                _record(rep, f"manifest.{key}_{suffix}", manifest.get(f"{key}_{suffix}"), val)
                new_manifest[f"{key}_{suffix}"] = val
    for key, val in (("run_id", run_id), ("parent_run_id", run_id),
                     ("schema_version", manifest.get("schema_version") or "v2.1"),
                     ("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))):
        _record(rep, f"manifest.{key}", manifest.get(key), val)
        new_manifest[key] = val
    if isinstance(report.get("layers"), dict):
        report["layers"] = layers_rep
    parts = []
    for layer, tag in names.items():
        if layer in bodies:
            parts.append(f"<{tag}>\n{_dump(bodies[layer])}\n</{tag}>\n")
    parts.append(f"<MANIFEST_UPDATE>\n{_dump(new_manifest)}\n</MANIFEST_UPDATE>\n")
    parts.append(f"<REPORT>\n{_dump(report)}\n</REPORT>\n")
    return "".join(parts), {"kind": "342", "changes": rep, "artifact_hash": artifact_hash}


def l2_hash_from_disk(root: Path) -> str:
    return chash(json.loads((Path(root) / "Artifacts" / "layer2_csir.json").read_text(encoding="utf-8")))
