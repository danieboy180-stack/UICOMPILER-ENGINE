"""Deterministic, byte-exact bundle packaging (replaces the two LLM 'file-packaging utility' prompts).

An LLM packager can silently reformat; this cannot: bytes in == bytes out. Implements packager rules 1-6 for both
layer12_bundle.txt and layer342_bundle.txt, including the validation summary the 342 packager prompt demands.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TAGS12 = ("LAYER1_OUTPUT", "LAYER2_OUTPUT", "MANIFEST_UPDATE", "REPORT")
TAGS342 = ("LAYER3_OUTPUT", "LAYER4_1_OUTPUT", "LAYER4_2_OUTPUT", "MANIFEST_UPDATE", "REPORT")
FILENAMES = {"12": "layer12_bundle.txt", "342": "layer342_bundle.txt"}


def canonical_json(value: Any) -> str:
    """Identical to ingest._canonical_json / ingest_layer342._canonical_json (tested for equality)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def chash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_json_text(text: str) -> str:
    """Same fence-stripping the ingest scripts apply."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_tags(text: str, tags: tuple[str, ...]) -> dict[str, str]:
    """First match per tag, DOTALL - exactly how ingest.py / ingest_layer342.py parse."""
    out: dict[str, str] = {}
    for tag in tags:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if m:
            out[tag] = m.group(1)
    return out


def load_body(raw: str) -> tuple[Any, str | None]:
    try:
        return json.loads(clean_json_text(raw)), None
    except json.JSONDecodeError as e:
        return None, f"{e.msg} at line {e.lineno} col {e.colno}"


@dataclass
class PackReport:
    kind: str
    ok: bool = False
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    halt: dict[str, Any] | None = None
    sha256: str = ""
    size: int = 0
    out_path: str | None = None

    def summary(self) -> str:
        lines = [f"[pack {self.kind}] present: {', '.join(self.present) or 'none'}"]
        if self.missing:
            lines.append(f"  missing: {', '.join(self.missing)}")
        if self.halt:
            lines.append(f"  HALTED bundle (intentional partial): {self.halt}")
        lines += [f"  ERROR: {e}" for e in self.errors] + [f"  WARN: {w}" for w in self.warnings]
        lines.append(f"  verdict: {'OK' if self.ok else 'REFUSED'}  sha256={self.sha256[:16]}  bytes={self.size}")
        return "\n".join(lines)


def _status(layer_val: Any) -> str | None:
    if isinstance(layer_val, str):
        return layer_val.lower()
    if isinstance(layer_val, dict) and layer_val.get("status") is not None:
        return str(layer_val["status"]).lower()
    return None


def validate_pack(kind: str, data: bytes, allow_partial: bool = False) -> PackReport:
    """Packager rules: presence check, halt detection, inconsistent-bundle detection, garbage rejection."""
    rep = PackReport(kind=kind, sha256=sha256_bytes(data), size=len(data))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        rep.errors.append(f"not valid UTF-8 ({e.reason} at byte {e.start})")
        return rep
    tags = TAGS12 if kind == "12" else TAGS342
    found = parse_tags(text, tags)
    rep.present = [t for t in tags if t in found]
    if not found:
        rep.errors.append("no recognizable layer tags - not a UI GOD bundle")
        return rep
    for tag, raw in found.items():
        _, err = load_body(raw)
        if err:
            rep.errors.append(f"<{tag}> is not valid JSON: {err}")
    if kind == "12":
        rep.missing = [t for t in ("LAYER1_OUTPUT", "LAYER2_OUTPUT") if t not in found]
        if rep.missing and not allow_partial:
            rep.errors.append(f"incomplete pair, missing {rep.missing} (pass allow_partial only if Layer 1 halted)")
        elif rep.missing:
            rep.warnings.append(f"partial bundle allowed: missing {rep.missing}")
    else:
        rep.missing = [t for t in ("MANIFEST_UPDATE", "REPORT") if t not in found]
        for t in rep.missing:
            rep.errors.append(f"<{t}> missing: ingest_layer342.py requires it")
        if "LAYER3_OUTPUT" not in found:
            rep.errors.append("<LAYER3_OUTPUT> missing: a 342 bundle with no Layer 3 body has nothing to ingest")
        report, _ = load_body(found.get("REPORT", "")) if "REPORT" in found else (None, None)
        if isinstance(report, dict):
            layers = report.get("layers") if isinstance(report.get("layers"), dict) else {}
            s3, s41, s42 = (_status(layers.get(k)) for k in ("layer3", "layer4_1", "layer4_2"))
            if report.get("halt_reason") or report.get("halt_stage"):
                rep.halt = {k: report.get(k) for k in ("halt_reason", "halt_stage", "blocking_object")}
            if "LAYER4_1_OUTPUT" in found and s3 != "pass":
                rep.errors.append(f"inconsistent: Layer 4.1 present but Layer 3 status is {s3!r}")
            if "LAYER4_2_OUTPUT" in found and s41 != "pass":
                rep.errors.append(f"inconsistent: Layer 4.2 present but Layer 4.1 status is {s41!r}")
    rep.ok = not rep.errors
    return rep


def pack(kind: str, data: bytes, out_dir: Path, allow_partial: bool = False) -> PackReport:
    """Write layer{12,342}_bundle.txt byte-for-byte. Refuses (writes nothing) when validation fails."""
    rep = validate_pack(kind, data, allow_partial)
    if not rep.ok:
        return rep
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / FILENAMES[kind]
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    if target.read_bytes() != data:  # paranoia: lossless is the whole point
        rep.ok = False
        rep.errors.append("post-write byte comparison failed")
    rep.out_path = str(target)
    return rep
