"""
Stage 7 — Contract-Violation Lint Gate (post-run)

Tests that the aggregate, cross-layer lint pass catches violations that are
ONLY visible when reading all layer artifacts together (not per-stage).

An "aggregate-only violation" is one that cannot be detected by inspecting any
single layer in isolation — the checker must read multiple layers simultaneously
and compare their contents.

Examples:
  - OCR text in Layer 2 that does not exist verbatim anywhere in Layer 1
    (per-stage check of L2 alone cannot know this is hallucinated)
  - A color value in Layer 3 not derivable from any Layer 1 or Layer 2 field
    (per-stage check of L3 alone cannot know the color has no source)
"""

import pytest
import sys
import tempfile
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.lint import post_run_lint
from orchestrator.report import generate_build_report


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_archive(tmp_root: Path, run_id: str, l1: dict, l2: dict, l3: dict) -> Path:
    """Write L1/L2/L3 artifacts into the expected archive layout and return the archive dir."""
    archive_dir = tmp_root / "Archives" / f"Run_{run_id}"
    art_dir = archive_dir / "Artifacts"
    art_dir.mkdir(parents=True)
    with open(art_dir / "layer1_ccir.json", "w", encoding="utf-8") as f:
        json.dump(l1, f)
    with open(art_dir / "layer2_csir.json", "w", encoding="utf-8") as f:
        json.dump(l2, f)
    with open(art_dir / "layer3_visual_ir.json", "w", encoding="utf-8") as f:
        json.dump(l3, f)
    return archive_dir


# ---------------------------------------------------------------------------
# Core Stage-7 test: ONE planted aggregate-only violation
# ---------------------------------------------------------------------------

def test_post_run_lint_catches_aggregate_only_violation():
    """
    Full-artifact-set test with exactly one aggregate-only violation planted.

    The violation: Layer 2 carries OCR text "GHOST_TEXT" which does NOT appear
    anywhere in Layer 1.  This is an aggregate-only violation — inspecting L2
    in isolation gives no indication that the text is fabricated; only comparing
    L2 against L1 together reveals the discrepancy.

    Layer 3 is clean (its color value "#CCCCCC" appears verbatim in L1).

    Asserts:
      1. post_run_lint() returns exactly one violation.
      2. The violation string mentions "Layer2", the field path, and the
         offending value "GHOST_TEXT".
      3. generate_build_report() writes the violation into BUILD_REPORT.md in
         the expected `layer: field: violation` format.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        run_id = "agg_lint_test"

        # --- Layer 1 (raw image candidates) ---------------------------------
        # Contains the string "#CCCCCC" so that L3 has a valid derivable color.
        # Does NOT contain "GHOST_TEXT".
        l1_data = {
            "schema_version": "2.1",
            "candidates": [
                {
                    "id": "btn-1",
                    "type": "button",
                    "ocr_text": None,
                    "dominant_color": "#CCCCCC",   # <- L3 will reference this legitimately
                    "confidence": 0.95,
                }
            ],
        }

        # --- Layer 2 (semantic IR) ------------------------------------------
        # Plants ONE aggregate-only violation:
        #   field `text` = "GHOST_TEXT" is not found verbatim anywhere in L1.
        # This is invisible when checking L2 alone; only aggregate comparison catches it.
        l2_data = {
            "schema_version": "2.1",
            "semantics": [
                {
                    "id": "btn-1",
                    "semantic_role": "button",
                    "text": "GHOST_TEXT",          # <- VIOLATION: not in L1
                }
            ],
        }

        # --- Layer 3 (visual IR) --------------------------------------------
        # Fully valid: background_color "#CCCCCC" IS present in L1.
        l3_data = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn-1",
                    "background_color": "#CCCCCC",  # <- valid, derivable from L1
                }
            ],
        }

        archive_dir = _make_archive(tmp, run_id, l1_data, l2_data, l3_data)

        # ------------------------------------------------------------------
        # 1. Direct lint engine check — must find exactly one violation
        # ------------------------------------------------------------------
        violations = post_run_lint(archive_dir)

        assert len(violations) == 1, (
            f"Expected exactly 1 aggregate-only violation, got {len(violations)}: {violations}"
        )

        violation_str = violations[0]

        # Must identify the layer
        assert "Layer2" in violation_str, (
            f"Violation should reference 'Layer2', got: {violation_str!r}"
        )
        # Must identify the offending value
        assert "GHOST_TEXT" in violation_str, (
            f"Violation should name the offending value 'GHOST_TEXT', got: {violation_str!r}"
        )
        # Must follow `layer: field: violation` structure (colon-separated)
        parts = violation_str.split(":", 2)
        assert len(parts) == 3, (
            f"Violation string must be in 'layer: field: violation' format, got: {violation_str!r}"
        )

        # ------------------------------------------------------------------
        # 2. generate_build_report() — violation must appear in BUILD_REPORT.md
        # ------------------------------------------------------------------
        success = generate_build_report(tmp, run_id)
        assert success, "generate_build_report() must return True"

        report_path = tmp / "Reports" / f"Run_{run_id}" / "BUILD_REPORT.md"
        assert report_path.exists(), f"BUILD_REPORT.md not created at {report_path}"

        content = report_path.read_text(encoding="utf-8")
        lint_content = content

        # The report must include the aggregate lint section
        assert "aggregate_lint_violations" in content, (
            "BUILD_REPORT.md must contain 'aggregate_lint_violations' section"
        )
        # The planted violation must appear verbatim
        assert "Layer2" in content, "BUILD_REPORT.md must mention 'Layer2'"
        assert "GHOST_TEXT" in content, (
            "BUILD_REPORT.md must include the offending value 'GHOST_TEXT'"
        )
        # compiler_integrity must be FAIL when there are lint violations
        assert "compiler_integrity**: FAIL" in content or "compiler_integrity: FAIL" in content, (
            "compiler_integrity must be FAIL when aggregate lint violations are present"
        )

        import json
        from verification.receipt import record_verification
        evidence = ["planted violation: Layer2 text 'GHOST_TEXT' does not exist in Layer1",
                    f"violations found: {len(violations)}", f"violation: {violations[0]}",
                    f"layer1: {json.dumps(l1_data, sort_keys=True)}", f"layer2: {json.dumps(l2_data, sort_keys=True)}",
                    f"layer3: {json.dumps(l3_data, sort_keys=True)}"]
        evidence += [f"BUILD_REPORT.md: {line}" for line in lint_content.splitlines() if line.strip()]
        record_verification("Stage7-AggLint", "One planted violation caught and written to BUILD_REPORT.md",
                            "\n".join(evidence), True)


# ---------------------------------------------------------------------------
# Complementary: clean artifact set must produce zero violations
# ---------------------------------------------------------------------------

def test_post_run_lint_clean_pass():
    """
    When all layer artifacts are consistent with each other, the aggregate lint
    must return zero violations — proving it doesn't generate false positives.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        run_id = "clean_lint_test"

        l1_data = {
            "candidates": [
                {"id": "x1", "ocr_text": "Submit", "dominant_color": "#0000FF"},
            ]
        }
        # L2 text "Submit" IS in L1 (ocr_text field)
        l2_data = {
            "semantics": [
                {"id": "x1", "text": "Submit"}
            ]
        }
        # L3 color "#0000FF" IS in L1
        l3_data = {
            "visuals": [
                {"id": "x1", "background_color": "#0000FF"}
            ]
        }

        archive_dir = _make_archive(tmp, run_id, l1_data, l2_data, l3_data)
        violations = post_run_lint(archive_dir)

        assert violations == [], (
            f"Expected zero violations for clean artifacts, got: {violations}"
        )

        import json
        from verification.receipt import record_verification
        evidence = ["clean artifact set: the aggregate lint must find zero violations",
                    f"layer1: {json.dumps(l1_data, sort_keys=True)}", f"layer2: {json.dumps(l2_data, sort_keys=True)}",
                    f"layer3: {json.dumps(l3_data, sort_keys=True)}", f"violations returned: {violations}",
                    f"violation count: {len(violations)}",
                    "L2 text 'Submit' is traceable to L1 ocr_text", "L3 color '#0000FF' is traceable to L1 dominant_color"]
        evidence += [f"archived file: {p.relative_to(archive_dir)} ({p.stat().st_size} bytes)"
                     for p in sorted(archive_dir.rglob("*")) if p.is_file()]
        record_verification("Stage7-AggLint-clean", "Clean artifact set yields zero violations (no false positives)",
                            "\n".join(evidence), True)


if __name__ == "__main__":
    test_post_run_lint_catches_aggregate_only_violation()
    test_post_run_lint_clean_pass()
    print("PASS test_post_run_lint")
