"""Dataset export lane: archived runs -> gated, deduped, group-split, hash-verified training rows.

Fail-closed: a run that fails any gate goes to quarantine.jsonl with reasons (never silently dropped, never exported).
Core is stdlib + pydantic (already a repo dependency). Output is JSONL (diffable, streamable); Parquet is optional.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from . import UIC_VERSION, spec
from .bundle import chash, sha256_bytes
from .check import HEX64, check_ir, check_l1, check_l2, has_errors, l1_objects, verify_chains

LAYER_FILES = {"l1": "layer1_ccir.json", "l2": "layer2_csir.json", "l3": "layer3_visual_ir.json",
               "l41": "layer4_1_semantic_ir.json", "l42": "layer4_2_execution_ir.json"}
MANIFEST_KEYS = {"l1": "layer1_ccir", "l2": "layer2_csir", "l3": "layer3_visual_ir", "l41": "layer4_1_semantic_ir",
                 "l42": "layer4_2_execution_ir"}
SPLITS = ("train", "val", "test")
TASKS = {"structure": ("l1", "l2"), "design_system": ("l2", "l3"), "semantic": ("l3", "l41"), "execution": ("l41", "l42")}
SECRET_PATTERNS = [("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
                   ("phone", re.compile(r"(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)")),
                   ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"))]


class Row(BaseModel):
    """Strict row schema: unknown fields are rejected, so schema drift fails loudly instead of corrupting a dataset."""
    model_config = ConfigDict(extra="forbid")
    row_id: str
    run_id: str
    project_id: str
    group_id: str
    source_hash: str
    source_kind: str
    source_ref: str | None
    l1: dict
    l2: dict
    l3: dict
    l41: dict
    l42: dict
    quality: dict
    warnings: list[str]
    redactions: dict[str, int]
    provenance: dict
    content_hash: str


def _read(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def scrub(obj: Any, counts: dict[str, int] | None = None) -> tuple[Any, dict[str, int]]:
    counts = {} if counts is None else counts
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = v if (isinstance(k, str) and k.endswith("hash")) else scrub(v, counts)[0]
        return out, counts
    if isinstance(obj, list):
        return [scrub(v, counts)[0] for v in obj], counts
    if isinstance(obj, str) and not HEX64.match(obj):
        s = obj
        for name, rx in SECRET_PATTERNS:
            s, n = rx.subn(f"[REDACTED_{name.upper()}]", s)
            if n:
                counts[name] = counts.get(name, 0) + n
        return s, counts
    return obj, counts


def _signature(l1: dict) -> set[str]:
    sig: set[str] = set()
    for o in l1_objects(l1):
        text = ((o.get("text") or {}).get("value") or "").strip().lower()
        pos, size = o.get("position") or {}, o.get("size") or {}
        sig.add(f"{o.get('role')}|{text}")
        sig.add(f"{o.get('role')}@{int((pos.get('x') or 0) // 10)},{int((pos.get('y') or 0) // 10)}"
                f"~{int((size.get('w') or 0) // 10)},{int((size.get('h') or 0) // 10)}")
    return sig


def minhash(sig: set[str], perms: int = 64) -> tuple[int, ...]:
    return tuple(min(int.from_bytes(hashlib.blake2b(f"{i}:{s}".encode(), digest_size=8).digest(), "big") for s in sig)
                 if sig else 0 for i in range(perms))


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def split_of(group_id: str, train: int = 80, val: int = 10) -> str:
    b = int(hashlib.sha256(group_id.encode()).hexdigest()[:8], 16) % 100
    return "train" if b < train else "val" if b < train + val else "test"


def _gates(root: Path, run_dir: Path, run_id: str) -> tuple[dict | None, list[str], list[str]]:
    """Returns (layers+meta | None, blocking reasons, warnings)."""
    why: list[str] = []
    warn: list[str] = []
    art = run_dir / "Artifacts"
    mpath = run_dir / f"manifest_{run_id}.json"
    if not mpath.is_file():
        return None, ["G.MANIFEST: archived manifest missing"], warn
    man = _read(mpath)
    layers: dict[str, Any] = {}
    for k, fn in LAYER_FILES.items():
        if not (art / fn).is_file():
            why.append(f"G.LAYERS: {fn} missing")
        else:
            layers[k] = _read(art / fn)
    if why:
        return None, why, warn
    ledger = root / "Logs" / "Pipeline_Ledger.json"
    entries = [e for e in (_read(ledger) if ledger.is_file() else []) if e.get("run_id") == run_id]
    if not entries or entries[-1].get("status") != "PASS" or entries[-1].get("hash_chain_valid") is not True:
        why.append("G.PIPELINE: run has no PASS entry with a valid hash chain in Pipeline_Ledger")
    ih = man.get("image_hash")
    if not isinstance(ih, str) or not HEX64.match(ih):
        why.append(f"G.SOURCE_HASH: manifest image_hash {ih!r} is not a real sha256 (default/fabricated hash)")
    for k, name in MANIFEST_KEYS.items():
        want = (man.get("artifacts", {}).get(name) or {}).get("hash")
        if chash(layers[k]) != want:
            why.append(f"G.ARTIFACT_HASH: {name} on disk != manifest hash")
    fs = check_l1(layers["l1"]) + check_l2(layers["l1"], layers["l2"])
    ids1 = {o.get("id") for o in l1_objects(layers["l1"])}
    ids2 = {v for d in _iter_dicts(layers["l2"]) for key in spec.L2_STRUCTURAL_ID_KEYS if isinstance((v := d.get(key)), str)}
    ctx = {"l1_ids": ids1, "l2_ids": ids2, "static": layers["l1"].get("input", {}).get("type") not in ("video", "screen_recording")}
    for lay, key in (("L3", "l3"), ("L41", "l41"), ("L42", "l42")):
        fs += check_ir(lay, layers[key], ctx)
    fs += verify_chains({"L3": layers["l3"], "L41": layers["l41"], "L42": layers["l42"]}, chash(layers["l2"]))
    why += [f"G.CONTRACT: {f}" for f in fs if f.level == "error"]
    warn += [str(f) for f in fs if f.level == "warn"]
    layers["_manifest"] = man
    return layers, why, warn


def _iter_dicts(o: Any):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _iter_dicts(v)
    elif isinstance(o, list):
        for v in o:
            yield from _iter_dicts(v)


def build_row(root: Path, run_id: str) -> tuple[Row | None, list[str]]:
    run_dir = Path(root) / "Archives" / f"Run_{run_id}"
    layers, why, warn = _gates(Path(root), run_dir, run_id)
    if why or layers is None:
        return None, why
    man = layers.pop("_manifest")
    red: dict[str, int] = {}
    clean = {}
    for k in LAYER_FILES:
        clean[k], _ = scrub(layers[k], red)
    objs = l1_objects(clean["l1"])
    src_hash = man["image_hash"]
    src_ref = next((p.relative_to(root).as_posix() for d in ("Images", "Sources") for p in sorted((Path(root) / d).glob(src_hash + ".*"))), None)
    project = str(layers["l1"].get("project_id") or "default_project")
    body = {"run_id_independent": {k: clean[k] for k in LAYER_FILES}, "source_hash": src_hash}
    row = Row(
        row_id=chash(body)[:32], run_id=run_id, project_id=project, group_id=project, source_hash=src_hash,
        source_kind="image" if (src_ref or "").startswith("Images") else ("code" if src_ref else "unknown"),
        source_ref=src_ref, **clean,
        quality={"objects": len(objs), "min_confidence": min((o["confidence"] for o in objs), default=None),
                 "weak_objects": sum(1 for o in objs if o["confidence"] < 0.60)},
        warnings=warn, redactions=red,
        provenance={"uic_version": UIC_VERSION, "prompts": spec.prompt_fingerprints(),
                    "artifact_hashes": {n: man["artifacts"][n]["hash"] for n in MANIFEST_KEYS.values()},
                    "schema_version": (clean["l3"].get("VERSIONING") or {}).get("schema_version")},
        content_hash=chash(body))
    return row, []


def list_runs(root: Path) -> list[str]:
    return sorted(p.name[len("Run_"):] for p in (Path(root) / "Archives").glob("Run_*") if p.is_dir())


def _write_atomic(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return sha256_bytes(text.encode("utf-8"))


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)


def export_dataset(root: Path, out: Path, task: str | None = None, require_source: bool = False,
                   near_threshold: float = 0.9, force: bool = False) -> dict:
    root, out = Path(root), Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise FileExistsError(f"{out} exists; dataset versions are immutable (use a new directory or force=True)")
    kept: list[Row] = []
    quarantine: list[dict] = []
    seen_exact: dict[str, str] = {}
    sigs: list[tuple[str, set[str]]] = []
    buckets: dict[tuple, list[int]] = {}
    for run_id in list_runs(root):
        row, why = build_row(root, run_id)
        if row is None:
            quarantine.append({"run_id": run_id, "reasons": why})
            continue
        if require_source and not row.source_ref:
            quarantine.append({"run_id": run_id, "reasons": ["G.SOURCE_MISSING: source file not in Images/ or Sources/"]})
            continue
        if row.content_hash in seen_exact:
            quarantine.append({"run_id": run_id, "reasons": [f"DEDUP.EXACT: same content as run {seen_exact[row.content_hash]}"]})
            continue
        sig = _signature(row.l1)
        mh = minhash(sig)
        cand = {i for b in range(8) for i in buckets.get((b, mh[b * 8:(b + 1) * 8]), [])}
        dup = next((sigs[i][0] for i in sorted(cand) if jaccard(sig, sigs[i][1]) >= near_threshold), None)
        if dup:
            quarantine.append({"run_id": run_id, "reasons": [f"DEDUP.NEAR: Jaccard >= {near_threshold} with run {dup}"]})
            continue
        for b in range(8):
            buckets.setdefault((b, mh[b * 8:(b + 1) * 8]), []).append(len(sigs))
        sigs.append((run_id, sig))
        seen_exact[row.content_hash] = run_id
        kept.append(row)
    files: dict[str, str] = {}
    by_split: dict[str, list[Row]] = {s: [] for s in SPLITS}
    for r in kept:
        by_split[split_of(r.group_id)].append(r)
    for s in SPLITS:
        files[f"{s}.jsonl"] = _write_atomic(out / f"{s}.jsonl", _jsonl([r.model_dump() for r in by_split[s]]))
        if task:
            src, dst = TASKS[task]
            msgs = [{"messages": [{"role": "user", "content": f"UI GOD {task}: produce {dst} from this frozen {src}.\n" + json.dumps(getattr(r, src), sort_keys=True)},
                                  {"role": "assistant", "content": json.dumps(getattr(r, dst), sort_keys=True)}],
                     "meta": {"row_id": r.row_id, "group_id": r.group_id}} for r in by_split[s]]
            files[f"{task}_{s}.messages.jsonl"] = _write_atomic(out / f"{task}_{s}.messages.jsonl", _jsonl(msgs))
    files["quarantine.jsonl"] = _write_atomic(out / "quarantine.jsonl", _jsonl(quarantine))
    manifest = {"uic_version": UIC_VERSION, "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "counts": {**{s: len(by_split[s]) for s in SPLITS}, "quarantined": len(quarantine), "runs_seen": len(list_runs(root))},
                "task": task, "near_dup_threshold": near_threshold, "prompts": spec.prompt_fingerprints(),
                "files": files, "groups": sorted({r.group_id for r in kept})}
    _write_atomic(out / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _write_atomic(out / "DATASET_CARD.md", (
        f"# UI GOD dataset\n\nBuilt by uic {UIC_VERSION}. Rows: {sum(manifest['counts'][s] for s in SPLITS)} "
        f"({', '.join(f'{s}={manifest['counts'][s]}' for s in SPLITS)}); quarantined: {len(quarantine)}.\n\n"
        "Every row passed: pipeline PASS + valid hash chain, real source hash, artifact-hash equality, the executable "
        "prompt contract (uic.check), recomputed layer hash chains, secret/PII scrub, exact + near dedup. "
        "Splits are by group_id (project) so near-identical screens never straddle train/test.\n"))
    return manifest


def verify_dataset(out: Path) -> list[str]:
    """Independent re-verification of a dataset directory. Empty list == clean."""
    out = Path(out)
    bad: list[str] = []
    mp = out / "manifest.json"
    if not mp.is_file():
        return ["manifest.json missing"]
    man = _read(mp)
    for name, h in man["files"].items():
        p = out / name
        if not p.is_file() or sha256_bytes(p.read_bytes()) != h:
            bad.append(f"file hash mismatch: {name}")
    groups: dict[str, str] = {}
    ids: set[str] = set()
    for s in SPLITS:
        p = out / f"{s}.jsonl"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = Row.model_validate_json(line)
            except ValidationError as e:
                bad.append(f"{s}:{n} schema: {e.errors()[0]['msg']}")
                continue
            body = {"run_id_independent": {k: getattr(row, k) for k in LAYER_FILES}, "source_hash": row.source_hash}
            if chash(body) != row.content_hash:
                bad.append(f"{s}:{n} content_hash mismatch")
            if row.row_id in ids:
                bad.append(f"{s}:{n} duplicate row_id")
            ids.add(row.row_id)
            if groups.setdefault(row.group_id, s) != s:
                bad.append(f"group leakage: {row.group_id} in {groups[row.group_id]} and {s}")
            if split_of(row.group_id) != s:
                bad.append(f"{s}:{n} wrong split for group {row.group_id}")
            f = check_l1(row.l1) + check_l2(row.l1, row.l2)
            if has_errors(f):
                bad.append(f"{s}:{n} contract: {next(x for x in f if x.level == 'error')}")
            if not HEX64.match(row.source_hash):
                bad.append(f"{s}:{n} source_hash not sha256")
    return bad
