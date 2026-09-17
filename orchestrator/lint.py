"""
orchestrator/lint.py — Stage 7: Contract-Violation Lint Gate (post-run)

Second-pass aggregate lint that runs AFTER the full pipeline has completed.
Reads all written layer artifacts together (not per-stage) to catch violations
that are only visible when comparing layers against each other.

Violation format returned: "LayerN: <field-path>: <description>"
  e.g. "Layer2: semantics[0].text: OCR text 'X' not found verbatim in Layer 1"

This format satisfies the `layer: field: violation` contract in BUILD_REPORT.md.
"""

from pathlib import Path
from typing import Any, List, Set
import json


def _get_all_strings(data: Any) -> Set[str]:
    """Recursively collect every string value in a JSON-parsed data structure."""
    strs: Set[str] = set()
    if isinstance(data, dict):
        for v in data.values():
            strs.update(_get_all_strings(v))
    elif isinstance(data, list):
        for item in data:
            strs.update(_get_all_strings(item))
    elif isinstance(data, str):
        strs.add(data)
    return strs


def _check_fields(
    data: Any,
    target_fields: Set[str],
    allowed_values: Set[str],
    path: str = "",
) -> List[tuple]:
    """
    Walk *data* and collect (field_path, value) pairs where a key in
    *target_fields* has a string value NOT present in *allowed_values*.
    """
    violations: List[tuple] = []
    if isinstance(data, dict):
        for k, v in data.items():
            current_path = f"{path}.{k}" if path else k
            if k in target_fields and isinstance(v, str):
                if v not in allowed_values:
                    violations.append((current_path, v))
            violations.extend(_check_fields(v, target_fields, allowed_values, current_path))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            current_path = f"{path}[{i}]"
            violations.extend(_check_fields(item, target_fields, allowed_values, current_path))
    return violations


def _load_json(path: Path) -> Any:
    """Load a JSON file, returning {} on any error (missing file, parse error)."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def post_run_lint(archive_dir: Path) -> List[str]:
    """
    Aggregate cross-layer lint pass.

    Reads layer1_ccir.json, layer2_csir.json, and layer3_visual_ir.json from
    *archive_dir*/Artifacts/ all at once, then checks for contract violations
    that require comparing multiple layers simultaneously:

      • Layer 2 OCR/text fields must exist verbatim in Layer 1.
      • Layer 3 color fields must be derivable (appear verbatim) in Layer 1 or 2.

    Returns a list of violation strings in the format:
        "LayerN: <field-path>: <description>"

    An empty list means all artifacts are mutually consistent.
    """
    artifacts_dir = archive_dir / "Artifacts"

    l1_data = _load_json(artifacts_dir / "layer1_ccir.json")
    l2_data = _load_json(artifacts_dir / "layer2_csir.json")
    l3_data = _load_json(artifacts_dir / "layer3_visual_ir.json")

    l1_strings = _get_all_strings(l1_data)
    l2_strings = _get_all_strings(l2_data)

    violations: List[str] = []

    # ------------------------------------------------------------------
    # Rule 1: Every text / raw_text value in Layer 2 must appear verbatim
    # in Layer 1.  This catches OCR hallucinations invisible per-stage.
    # ------------------------------------------------------------------
    if l2_data and l1_strings:
        l2_text_violations = _check_fields(l2_data, {"text", "raw_text"}, l1_strings)
        for field_path, val in l2_text_violations:
            violations.append(
                f"Layer2: {field_path}: OCR text '{val}' not found verbatim in Layer 1"
            )

    # ------------------------------------------------------------------
    # Rule 2: Every color value in Layer 3 must be derivable from Layer 1
    # or Layer 2 (i.e., the string appears somewhere in those layers).
    # This catches color values invented by the visual stage with no source.
    # ------------------------------------------------------------------
    if l3_data and (l1_strings or l2_strings):
        l12_strings = l1_strings | l2_strings
        l3_color_violations = _check_fields(
            l3_data, {"color", "background_color", "text_color"}, l12_strings
        )
        for field_path, val in l3_color_violations:
            violations.append(
                f"Layer3: {field_path}: Color '{val}' not derivable from Layer 1/2"
            )

    return violations
