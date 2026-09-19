"""Comprehensive tests for ingest_layer342.py (v1.1 Fused Prompt Spec)."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ingest_layer342
from ingest_layer342 import _canonical_json, _hash, compute_pipeline_hash, EXIT_OK, EXIT_10
from orchestrator.lock import locked_write


def _setup_run(root: Path, run_id: str = "run_test_1") -> tuple[Path, dict, str, str]:
    manifest_dir = root / "Manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"manifest_{run_id}.json"

    artifacts_dir = root / "Artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    l1_data = {
        "schema_version": "2.1",
        "project_id": "test_proj",
        "candidates": [{"id": "btn_1", "bbox": {"x": 10, "y": 20, "w": 80, "h": 30}}]
    }
    l2_data = {
        "schema_version": "2.1",
        "semantics": [{"id": "btn_1", "semantic_role": "button"}]
    }

    with locked_write(artifacts_dir / "layer1_ccir.json", l1_data):
        pass
    with locked_write(artifacts_dir / "layer2_csir.json", l2_data):
        pass

    l1_hash = _hash(l1_data)
    l2_hash = _hash(l2_data)

    manifest_data = {
        "run_id": run_id,
        "project_id": "test_proj",
        "pipeline_id": "test_pipeline",
        "next_layer": 13,
        "artifacts": {
            "layer1_ccir": {"hash": l1_hash, "run_id": run_id, "version": 1},
            "layer2_csir": {"hash": l2_hash, "run_id": run_id, "version": 1}
        }
    }
    with locked_write(manifest_path, manifest_data):
        pass

    ledger_path = artifacts_dir / "Artifact_Ledger.json"
    ledger_data = [
        {"artifact": "layer1_ccir", "run_id": run_id, "hash": l1_hash, "parent_hash": None, "status": "written"},
        {"artifact": "layer2_csir", "run_id": run_id, "hash": l2_hash, "parent_hash": l1_hash, "status": "written"}
    ]
    with locked_write(ledger_path, ledger_data):
        pass

    return manifest_path, manifest_data, l1_hash, l2_hash


def _write_inbox(root: Path, filename: str, content: str) -> Path:
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    bundle_path = inbox / filename
    bundle_path.write_text(content, encoding="utf-8")
    return bundle_path


def test_valid_layer3_pass():
    """Verify passing Layer 3-only paste writes artifact and ledger correctly in v1.1 format."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_pass_1")

        l3_body = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn_1",
                    "style": {"background_color": "#00FF00"},
                    "raw_text": "Submit"
                }
            ],
            "VALIDATION_GATE": {
                "gate_result": "pass"
            }
        }
        l3_hash = _hash(l3_body)

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": l3_hash,
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"}
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(l3_body, indent=2)}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_OK, f"Expected EXIT_OK, got {code}"

        l3_path = root / "Artifacts" / "layer3_visual_ir.json"
        assert l3_path.exists(), "layer3_visual_ir.json must be written to disk"
        l3_content = json.loads(l3_path.read_text(encoding="utf-8"))
        expected_l3_hash = _hash(l3_content)
        assert expected_l3_hash == l3_hash

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        l3_entry = next((e for e in reversed(ledger) if e.get("artifact") == "layer3_visual_ir"), None)
        assert l3_entry is not None
        assert l3_entry["status"] == "written"
        assert l3_entry["parent_hash"] == l2_hash
        assert l3_entry["hash"] == expected_l3_hash

        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest["artifacts"]["layer3_visual_ir"]["hash"] == expected_l3_hash
        assert updated_manifest["next_layer"] == 13
        assert (root / "Python_Inbox" / "layer342_bundle.txt").exists()


def test_layer3_failed_gate():
    """Verify failed Layer 3 gate writes FAILED_layer3.json and records failed_gate in ledger."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_fail_1")

        l3_body = {
            "schema_version": "2.1",
            "validation_failed": True,
            "VALIDATION_GATE": {
                "gate_result": "fail",
                "halt_reason": "Confidence threshold violation"
            }
        }
        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": _hash(l3_body)
        }
        report = {
            "layers": {
                "layer3": {"status": "fail"}
            },
            "halt_reason": "Confidence threshold violation",
            "halt_stage": "Layer3"
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(l3_body, indent=2)}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10, got {code}"

        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists()

        failed_file = root / "Python_Output" / "runs" / "run_fail_1" / "FAILED_layer3.json"
        assert failed_file.exists(), "FAILED_layer3.json must exist"
        failed_data = json.loads(failed_file.read_text(encoding="utf-8"))
        assert failed_data["validation_failed"] is True

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        l3_entry = next((e for e in reversed(ledger) if e.get("artifact") == "layer3_visual_ir"), None)
        assert l3_entry is not None
        assert l3_entry["status"] == "failed_gate"
        assert l3_entry["parent_hash"] == l2_hash
        assert l3_entry["hash"] == _hash(failed_data)


def test_valid_layer3_and_4_1_pass():
    """Verify full valid layer3+4.1 paste (2 named tags, both pass) writes both artifacts and chains hashes."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_l3_l41_pass")

        l3_body = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn_1",
                    "style": {"background_color": "#00FF00"},
                    "raw_text": "Submit"
                }
            ],
            "VALIDATION_GATE": {
                "gate_result": "pass"
            }
        }
        l3_hash = _hash(l3_body)

        l4_1_body = {
            "schema_version": "2.1",
            "root": {
                "id": "root_container",
                "layout": {
                    "layout_type": "flex_col",
                    "gap": 8
                },
                "children": [
                    {
                        "id": "btn_1",
                        "layout": {"layout_type": "flex_row"},
                        "children": []
                    }
                ]
            },
            "VALIDATION_GATE": {
                "gate_result": "pass"
            }
        }
        l4_1_hash = _hash(l4_1_body)

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": l3_hash,
            "layer4_1_input_hash": l3_hash,
            "layer4_1_output_hash": l4_1_hash,
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "pass"}
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(l3_body, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(l4_1_body, indent=2)}
</LAYER4_1_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_OK, f"Expected EXIT_OK, got {code}"

        l3_path = root / "Artifacts" / "layer3_visual_ir.json"
        assert l3_path.exists()
        l4_1_path = root / "Artifacts" / "layer4_1_semantic_ir.json"
        assert l4_1_path.exists()

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

        l3_entry = next((e for e in ledger if e.get("artifact") == "layer3_visual_ir" and e.get("run_id") == "run_l3_l41_pass"), None)
        assert l3_entry is not None
        assert l3_entry["status"] == "written"
        assert l3_entry["hash"] == l3_hash
        assert l3_entry["parent_hash"] == l2_hash

        l4_1_entry = next((e for e in ledger if e.get("artifact") == "layer4_1_semantic_ir" and e.get("run_id") == "run_l3_l41_pass"), None)
        assert l4_1_entry is not None
        assert l4_1_entry["status"] == "written"
        assert l4_1_entry["hash"] == l4_1_hash
        assert l4_1_entry["parent_hash"] == l3_hash

        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest["artifacts"]["layer3_visual_ir"]["hash"] == l3_hash
        assert updated_manifest["artifacts"]["layer4_1_semantic_ir"]["hash"] == l4_1_hash
        assert updated_manifest["next_layer"] == 13


def test_layer3_pass_layer4_1_fail():
    """Verify paste where Layer 3 passed but Layer 4.1 failed: layer3 artifact written, FAILED_layer4_1.json written, EXIT_10."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_l3_pass_l41_fail")

        l3_body = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn_1",
                    "style": {"background_color": "#00FF00"},
                    "raw_text": "Submit"
                }
            ],
            "VALIDATION_GATE": {
                "gate_result": "pass"
            }
        }
        l3_hash = _hash(l3_body)

        l4_1_body = {
            "schema_version": "2.1",
            "validation_failed": True,
            "VALIDATION_GATE": {
                "gate_result": "fail",
                "halt_reason": "Overlapping layout bounds detected"
            }
        }
        l4_1_hash = _hash(l4_1_body)

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": l3_hash,
            "layer4_1_input_hash": l3_hash,
            "layer4_1_output_hash": l4_1_hash,
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "fail"}
            },
            "halt_reason": "Overlapping layout bounds detected",
            "halt_stage": "Layer4.1"
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(l3_body, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(l4_1_body, indent=2)}
</LAYER4_1_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10, got {code}"

        # Layer 3 artifact exists
        l3_path = root / "Artifacts" / "layer3_visual_ir.json"
        assert l3_path.exists()

        # Layer 4.1 artifact does NOT exist
        l4_1_path = root / "Artifacts" / "layer4_1_semantic_ir.json"
        assert not l4_1_path.exists()

        # FAILED_layer4_1.json exists under Python_Output/runs/{run_id}/
        failed_file = root / "Python_Output" / "runs" / "run_l3_pass_l41_fail" / "FAILED_layer4_1.json"
        assert failed_file.exists()
        failed_data = json.loads(failed_file.read_text(encoding="utf-8"))
        assert failed_data["validation_failed"] is True

        # Ledger entries
        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text(encoding="utf-8"))
        l3_entry = next((e for e in ledger if e.get("artifact") == "layer3_visual_ir" and e.get("run_id") == "run_l3_pass_l41_fail"), None)
        assert l3_entry is not None
        assert l3_entry["status"] == "written"

        l4_1_entry = next((e for e in ledger if e.get("artifact") == "layer4_1_semantic_ir" and e.get("run_id") == "run_l3_pass_l41_fail"), None)
        assert l4_1_entry is not None
        assert l4_1_entry["status"] == "failed_gate"
        assert l4_1_entry["parent_hash"] == l3_hash


def test_tampered_layer1_ccir():
    """Verify tamper detection: if layer1_ccir disk hash differs from manifest, refuse paste."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_tamper_1")

        tampered_l1 = {"schema_version": "2.1", "tampered": True}
        (root / "Artifacts" / "layer1_ccir.json").write_text(json.dumps(tampered_l1), encoding="utf-8")

        bundle_content = """<LAYER3_OUTPUT>
{
  "schema_version": "2.1",
  "visuals": []
}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{}
</MANIFEST_UPDATE>
<REPORT>
{"layers": {"layer3": {"status": "pass"}}}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10 on tampered disk artifact, got {code}"
        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists()


def test_strict_gate_ordering_malformed_bundle():
    """Verify strict gate ordering: if layer3 failed, bundle with LAYER4_1_OUTPUT tag is rejected as malformed."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_malformed_order")

        bundle_content = f"""<LAYER3_OUTPUT>
{{"schema_version": "2.1", "validation_failed": true}}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{{"schema_version": "2.1", "root": {{"id": "r", "layout": {{"layout_type": "flex_col"}}, "children": []}}}}
</LAYER4_1_OUTPUT>
<MANIFEST_UPDATE>
{{"layer3_input_hash": "{l2_hash}"}}
</MANIFEST_UPDATE>
<REPORT>
{{"layers": {{"layer3": {{"status": "fail"}}, "layer4_1": {{"status": "pass"}}}}}}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)
        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10 on malformed gate order, got {code}"
        # Neither artifact should be written
        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists()
        assert not (root / "Artifacts" / "layer4_1_semantic_ir.json").exists()


def test_ambiguous_manifests():
    """Verify that identical modification timestamp among candidate manifests triggers EXIT_10."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m1, _, _, _ = _setup_run(root, "run_a")
        m2_path = root / "Manifest" / "manifest_run_b.json"
        m1_content = m1.read_text(encoding="utf-8")
        m2_path.write_text(m1_content.replace("run_a", "run_b"), encoding="utf-8")

        same_mtime = 1700000000.0
        os.utime(m1, (same_mtime, same_mtime))
        os.utime(m2_path, (same_mtime, same_mtime))

        bundle_content = """<LAYER3_OUTPUT>
{"schema_version": "2.1", "visuals": []}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{}
</MANIFEST_UPDATE>
<REPORT>
{"layers": {"layer3": {"status": "pass"}}}
</REPORT>"""
        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_10


def test_full_3_triple_pass_and_pipeline_run():
    """Verify full valid layer3+4.1+4.2 paste (all 3 pass) in v1.1 format:
    - Artifacts/layer3_visual_ir.json, layer4_1_semantic_ir.json, layer4_2_execution_ir.json written.
    - Hash chain L2 -> L3 -> L4.1 -> L4.2 in Artifact_Ledger.json.
    - Manifest updated with all 3 artifact hashes.
    - Manifest.next_layer set dynamically to Layer4_3Stage index (12).
    - Bundle layer342_bundle.txt moved to Python_Output/runs/{run_id}/ingested/.
    - Running Pipeline().run(ctx) exercises ValidateL3, ValidateL4.1, ValidateL4.2 from disk.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_3pass")

        t0 = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn_1",
                    "style": {"background_color": "#00FF00"},
                    "raw_text": "Submit"
                }
            ],
            "VALIDATION_GATE": {"gate_result": "pass"}
        }
        t1 = {
            "schema_version": "2.1",
            "root": {
                "id": "root_container",
                "layout": {"layout_type": "flex_col", "gap": 8},
                "children": [
                    {
                        "id": "btn_1",
                        "layout": {"layout_type": "flex_row"},
                        "children": []
                    }
                ]
            },
            "VALIDATION_GATE": {"gate_result": "pass"}
        }
        t2 = {
            "schema_version": "2.1",
            "root_node": {
                "id": "root_container",
                "type": "container",
                "children": [
                    {
                        "id": "btn_1",
                        "type": "button",
                        "children": []
                    }
                ]
            },
            "validation": {"status": "pass"},
            "VALIDATION_GATE": {"gate_result": "pass"}
        }

        h0 = _hash(t0)
        h1 = _hash(t1)
        h2 = _hash(t2)

        pipe_hash = compute_pipeline_hash([
            ("layer3", l2_hash, h0, l2_hash),
            ("layer4_1", h0, h1, h0),
            ("layer4_2", h1, h2, h1),
        ])

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": h0,
            "layer4_1_input_hash": h0,
            "layer4_1_output_hash": h1,
            "layer4_2_input_hash": h1,
            "layer4_2_output_hash": h2,
            "pipeline_hash": pipe_hash,
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "pass"},
                "layer4_2": {"status": "pass"}
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(t0, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(t1, indent=2)}
</LAYER4_1_OUTPUT>
<LAYER4_2_OUTPUT>
{json.dumps(t2, indent=2)}
</LAYER4_2_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        assert code == EXIT_OK, f"Expected EXIT_OK, got {code}"

        # 1. Verify artifacts exist on disk
        l3_path = root / "Artifacts" / "layer3_visual_ir.json"
        l4_1_path = root / "Artifacts" / "layer4_1_semantic_ir.json"
        l4_2_path = root / "Artifacts" / "layer4_2_execution_ir.json"
        assert l3_path.exists()
        assert l4_1_path.exists()
        assert l4_2_path.exists()

        # 2. Check ledger hash chain
        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

        l3_entry = next((e for e in ledger if e.get("artifact") == "layer3_visual_ir" and e.get("run_id") == "run_3pass"), None)
        assert l3_entry is not None and l3_entry["status"] == "written"
        assert l3_entry["hash"] == h0 and l3_entry["parent_hash"] == l2_hash

        l4_1_entry = next((e for e in ledger if e.get("artifact") == "layer4_1_semantic_ir" and e.get("run_id") == "run_3pass"), None)
        assert l4_1_entry is not None and l4_1_entry["status"] == "written"
        assert l4_1_entry["hash"] == h1 and l4_1_entry["parent_hash"] == h0

        l4_2_entry = next((e for e in ledger if e.get("artifact") == "layer4_2_execution_ir" and e.get("run_id") == "run_3pass"), None)
        assert l4_2_entry is not None and l4_2_entry["status"] == "written"
        assert l4_2_entry["hash"] == h2 and l4_2_entry["parent_hash"] == h1

        # 3. Check manifest updated and next_layer is 12 (Layer4_3Stage)
        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest["artifacts"]["layer3_visual_ir"]["hash"] == h0
        assert updated_manifest["artifacts"]["layer4_1_semantic_ir"]["hash"] == h1
        assert updated_manifest["artifacts"]["layer4_2_execution_ir"]["hash"] == h2
        assert updated_manifest["pipeline_hash"] == pipe_hash
        assert updated_manifest["next_layer"] == 12

        # 4. Check bundle moved to ingested/
        assert not (root / "Python_Inbox" / "layer342_bundle.txt").exists()
        ingested_file = root / "Python_Output" / "runs" / "run_3pass" / "ingested" / "layer342_bundle.txt"
        assert ingested_file.exists()

        # 5. Run pipeline and verify Validate stages load from disk
        from orchestrator.orchestrator import Pipeline, PipelineContext
        ctx = PipelineContext(run_id="run_3pass", manifest_path=manifest_path)
        p_res = Pipeline().run(ctx)
        assert p_res == EXIT_OK, f"Expected pipeline EXIT_OK, got {p_res}"

        log_file = root / "Logs" / "Run_run_3pass.jsonl"
        assert log_file.exists()
        log_lines = log_file.read_text(encoding="utf-8").splitlines()
        log_messages = [json.loads(line).get("message", "") for line in log_lines]

        assert any("Loaded and verified layer3_visual_ir from disk" in msg for msg in log_messages)
        assert any("Loaded and verified layer4_1_semantic_ir from disk" in msg for msg in log_messages)
        assert any("Loaded and verified layer4_2_execution_ir from disk" in msg for msg in log_messages)


def test_tamper_design_system_ir_catches_output_hash_mismatch():
    """Acceptance Criteria 4: Hand-edit a single character in DESIGN_SYSTEM_IR before ingest,
    confirm ingest_layer342.py catches the output_hash mismatch and refuses to write
    layer3_visual_ir.json rather than trusting MANIFEST_UPDATE's claimed hash."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_tamper_ds_ir")

        original_t0 = {
            "schema_version": "2.1",
            "visuals": [
                {
                    "id": "btn_submit",
                    "style": {"background_color": "#0088FF", "text_color": "#FFFFFF", "border_radius": 4},
                    "raw_text": "Submit Form"
                }
            ],
            "VALIDATION_GATE": {"gate_result": "pass"}
        }
        original_hash = _hash(original_t0)

        # Hand-edit a single character in the body (change #0088FF to #0088FE)
        tampered_t0 = json.loads(json.dumps(original_t0))
        tampered_t0["visuals"][0]["style"]["background_color"] = "#0088FE"

        # MANIFEST_UPDATE declares the original (unmodified) hash
        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": original_hash,  # Claims original, but body is tampered
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"}
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(tampered_t0, indent=2)}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)

        code = ingest_layer342.ingest(root)
        # MUST return EXIT_10 and REFUSE to write layer3_visual_ir.json
        assert code == EXIT_10, f"Expected EXIT_10 on output hash mismatch, got {code}"
        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists(), (
            "Artifact layer3_visual_ir.json MUST NOT be written when output_hash does not match body!"
        )


def test_pipeline_hash_mismatch_fails():
    """Verify that a mismatch in pipeline_hash causes hard failure EXIT_10 and does not advance manifest."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_pipe_mismatch")

        t0 = {"schema_version": "2.1", "visuals": [], "VALIDATION_GATE": {"gate_result": "pass"}}
        t1 = {"schema_version": "2.1", "root": {"id": "r", "layout": {"layout_type": "flex_col"}, "children": []}, "VALIDATION_GATE": {"gate_result": "pass"}}
        t2 = {"schema_version": "2.1", "root_node": {"id": "r", "type": "container", "children": []}, "validation": {"status": "pass"}, "VALIDATION_GATE": {"gate_result": "pass"}}

        h0 = _hash(t0)
        h1 = _hash(t1)
        h2 = _hash(t2)

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": h0,
            "layer4_1_input_hash": h0,
            "layer4_1_output_hash": h1,
            "layer4_2_input_hash": h1,
            "layer4_2_output_hash": h2,
            "pipeline_hash": "deadbeef" * 8,  # Completely invalid pipeline hash
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "pass"},
                "layer4_2": {"status": "pass"}
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(t0, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(t1, indent=2)}
</LAYER4_1_OUTPUT>
<LAYER4_2_OUTPUT>
{json.dumps(t2, indent=2)}
</LAYER4_2_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)
        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10 on pipeline_hash mismatch, got {code}"

        # Manifest next_layer must NOT be advanced to 12
        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest.get("next_layer") != 12

        # Original bundle must NOT be moved
        assert (root / "Python_Inbox" / "layer342_bundle.txt").exists()


def test_layer4_2_validation_fail():
    """Verify paste where Layer 3 and Layer 4.1 pass, but Layer 4.2 validation fails:
    - Layer 3 and 4.1 artifacts written to disk.
    - Layer 4.2 artifact NOT written to disk.
    - Python_Output/runs/{run_id}/FAILED_layer4_2.json written.
    - Artifact_Ledger records failed_gate for layer4_2_execution_ir.
    - Manifest next_layer remains unchanged (13).
    - Original bundle in Python_Inbox is NOT moved.
    - Ingest returns EXIT_10.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_hash, l2_hash = _setup_run(root, "run_l42_fail")

        t0 = {"schema_version": "2.1", "visuals": [], "VALIDATION_GATE": {"gate_result": "pass"}}
        t1 = {"schema_version": "2.1", "root": {"id": "r", "layout": {"layout_type": "flex_col"}, "children": []}, "VALIDATION_GATE": {"gate_result": "pass"}}
        t2 = {
            "schema_version": "2.1",
            "validation_failed": True,
            "validation": {"status": "fail", "reason": "Circular component dependency"},
            "VALIDATION_GATE": {"gate_result": "fail"}
        }

        h0 = _hash(t0)
        h1 = _hash(t1)

        manifest_update = {
            "layer3_input_hash": l2_hash,
            "layer3_output_hash": h0,
            "layer4_1_input_hash": h0,
            "layer4_1_output_hash": h1,
            "layer4_2_input_hash": h1,
        }
        report = {
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "pass"},
                "layer4_2": {"status": "fail"}
            },
            "halt_reason": "Circular component dependency",
            "halt_stage": "Layer4.2"
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(t0, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(t1, indent=2)}
</LAYER4_1_OUTPUT>
<LAYER4_2_OUTPUT>
{json.dumps(t2, indent=2)}
</LAYER4_2_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)
        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10, got {code}"

        assert (root / "Artifacts" / "layer3_visual_ir.json").exists()
        assert (root / "Artifacts" / "layer4_1_semantic_ir.json").exists()
        assert not (root / "Artifacts" / "layer4_2_execution_ir.json").exists()

        failed_file = root / "Python_Output" / "runs" / "run_l42_fail" / "FAILED_layer4_2.json"
        assert failed_file.exists()
        failed_data = json.loads(failed_file.read_text(encoding="utf-8"))
        assert failed_data.get("validation_failed") is True

        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text(encoding="utf-8"))
        l4_2_entry = next((e for e in ledger if e.get("artifact") == "layer4_2_execution_ir" and e.get("run_id") == "run_l42_fail"), None)
        assert l4_2_entry is not None
        assert l4_2_entry["status"] == "failed_gate"
        assert l4_2_entry["parent_hash"] == h1

        updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert updated_manifest["next_layer"] == 13
        assert "layer4_2_execution_ir" not in updated_manifest.get("artifacts", {})
        assert (root / "Python_Inbox" / "layer342_bundle.txt").exists()


def test_real_fused_v2_1_schema_version() -> None:
    """Real Layer 3+4.1+4.2 v1.1 bundle with schema_version 'v2.1' only in MANIFEST_UPDATE/REPORT."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_h, l2_h = _setup_run(root, "run_v21_real")

        # Layer bodies legitimately have NO schema_version in real fused output
        l3_body = {
            "visuals": [{"id": "v1", "style": {"color": "#111"}}],
            "VALIDATION_GATE": {"gate_result": "pass"}
        }
        l4_1_body = {
            "root": {"id": "r1", "layout": {"layout_type": "flex_col"}, "children": []},
            "VALIDATION_GATE": {"gate_result": "pass"}
        }
        l4_2_body = {
            "root_node": {"id": "r1", "type": "container", "children": []},
            "validation": {"status": "pass"},
            "VALIDATION_GATE": {"gate_result": "pass"}
        }

        h3 = _hash(l3_body)
        h4_1 = _hash(l4_1_body)
        h4_2 = _hash(l4_2_body)

        pipe_hash = compute_pipeline_hash([
            ("layer3", l2_h, h3, l2_h),
            ("layer4_1", h3, h4_1, h3),
            ("layer4_2", h4_1, h4_2, h4_1),
        ])

        manifest_update = {
            "schema_version": "v2.1",
            "layer3_input_hash": l2_h,
            "layer3_output_hash": h3,
            "layer4_1_input_hash": h3,
            "layer4_1_output_hash": h4_1,
            "layer4_2_input_hash": h4_1,
            "layer4_2_output_hash": h4_2,
            "pipeline_hash": pipe_hash,
        }

        report = {
            "schema_version": "v2.1",
            "layers": {
                "layer3": {"status": "pass"},
                "layer4_1": {"status": "pass"},
                "layer4_2": {"status": "pass"},
            }
        }

        bundle_content = f"""<LAYER3_OUTPUT>
{json.dumps(l3_body, indent=2)}
</LAYER3_OUTPUT>
<LAYER4_1_OUTPUT>
{json.dumps(l4_1_body, indent=2)}
</LAYER4_1_OUTPUT>
<LAYER4_2_OUTPUT>
{json.dumps(l4_2_body, indent=2)}
</LAYER4_2_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)
        code = ingest_layer342.ingest(root)
        assert code == EXIT_OK, f"Expected EXIT_OK, got {code}"
        assert (root / "Artifacts" / "layer3_visual_ir.json").exists()
        assert (root / "Artifacts" / "layer4_1_semantic_ir.json").exists()
        assert (root / "Artifacts" / "layer4_2_execution_ir.json").exists()


def test_unsupported_schema_version_rejected() -> None:
    """Bundle with unsupported schema_version ('v1.0') is rejected with EXIT_10."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest_path, manifest_data, l1_h, l2_h = _setup_run(root, "run_bad_ver")

        manifest_update = {
            "schema_version": "v1.0",
            "layer3_input_hash": l2_h,
            "layer3_output_hash": "dummy",
        }
        report = {"schema_version": "v1.0"}

        bundle_content = f"""<LAYER3_OUTPUT>
{{}}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""

        _write_inbox(root, "layer342_bundle.txt", bundle_content)
        code = ingest_layer342.ingest(root)
        assert code == EXIT_10, f"Expected EXIT_10, got {code}"
        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists()


if __name__ == "__main__":
    test_valid_layer3_pass()
    print("PASS: test_valid_layer3_pass")
    test_layer3_failed_gate()
    print("PASS: test_layer3_failed_gate")
    test_valid_layer3_and_4_1_pass()
    print("PASS: test_valid_layer3_and_4_1_pass")
    test_layer3_pass_layer4_1_fail()
    print("PASS: test_layer3_pass_layer4_1_fail")
    test_tampered_layer1_ccir()
    print("PASS: test_tampered_layer1_ccir")
    test_strict_gate_ordering_malformed_bundle()
    print("PASS: test_strict_gate_ordering_malformed_bundle")
    test_ambiguous_manifests()
    print("PASS: test_ambiguous_manifests")
    test_full_3_triple_pass_and_pipeline_run()
    print("PASS: test_full_3_triple_pass_and_pipeline_run")
    test_tamper_design_system_ir_catches_output_hash_mismatch()
    print("PASS: test_tamper_design_system_ir_catches_output_hash_mismatch")
    test_pipeline_hash_mismatch_fails()
    print("PASS: test_pipeline_hash_mismatch_fails")
    test_layer4_2_validation_fail()
    print("PASS: test_layer4_2_validation_fail")
    test_real_fused_v2_1_schema_version()
    print("PASS: test_real_fused_v2_1_schema_version")
    test_unsupported_schema_version_rejected()
    print("PASS: test_unsupported_schema_version_rejected")
    print("ALL TESTS PASSED!")
