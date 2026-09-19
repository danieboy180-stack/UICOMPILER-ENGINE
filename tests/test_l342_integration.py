"""Integration tests for Layer 1+2 Ingest -> Layer 342 Ingest -> Pipeline execution.

Verifies the complete end-to-end chain without mocking hashing or file I/O:
1. Scenario 1: Layer 1+2 ingest produces layer1_ccir/layer2_csir and manifest.
2. Scenario 2: Layer 342 ingest (3 triples) writes all 3 artifacts; hash chain recomputed from disk.
3. Scenario 3: Pipeline.run() completes Layer 4.3 -> Layer 5 -> Layer 6; RunLogger proves Validate stages loaded from disk.
4. Scenario 4: Layer 3 gate failure halts ingest, writes FAILED_layer3.json, and Pipeline.run() halts on missing data.
5. Scenario 5: Layer 4.2 gate failure halts ingest, keeps Layer 3 & 4.1 artifacts, writes FAILED_layer4_2.json.
6. Scenario 6: Disk tampering of layer4_1_semantic_ir causes ValidateL4.1 to fail and triggers rollback.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ingest
import ingest_layer342
from orchestrator.orchestrator import Pipeline, PipelineContext, EXIT_OK, EXIT_10


# ---------------------------------------------------------------------------
# Fixture and Bundle Helpers
# ---------------------------------------------------------------------------

def _write_inbox(root: Path, filename: str, content: str) -> Path:
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    file_path = inbox / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path


def _rehash_file(path: Path) -> str:
    """Independently recompute the canonical JSON SHA-256 hash of a file on disk."""
    data = json.loads(path.read_text(encoding="utf-8"))
    canon = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _make_layer12_bundle(project_id: str = "proj_integration") -> str:
    l1 = {
        "schema_version": "2.1",
        "project_id": project_id,
        "layer": 12,
        "candidates": [
            {
                "id": "btn_submit",
                "bbox": {"x": 10, "y": 20, "w": 120, "h": 40},
                "confidence": 0.95,
                "dominant_color": "#0088FF",
                "ocr_text": "Submit Form"
            }
        ]
    }
    l2 = {
        "schema_version": "2.1",
        "semantics": [
            {
                "id": "btn_submit",
                "semantic_role": "button",
                "semantic_variant": "primary"
            }
        ]
    }
    return (
        f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n"
        f"<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>"
    )


def _make_layer342_bundle_pass(root: Path | None = None, l2_hash: str | None = None) -> str:
    if root is not None and l2_hash is None:
        l2_file = root / "Artifacts" / "layer2_csir.json"
        if l2_file.exists():
            l2_hash = _rehash_file(l2_file)
    if l2_hash is None:
        l2_hash = "0" * 64

    t0 = {
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
    t1 = {
        "schema_version": "2.1",
        "root": {
            "id": "root_container",
            "layout": {"layout_type": "flex_col", "gap": 8},
            "children": [
                {
                    "id": "btn_submit",
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
                    "id": "btn_submit",
                    "type": "button",
                    "children": []
                }
            ]
        },
        "validation": {"status": "pass"},
        "VALIDATION_GATE": {"gate_result": "pass"}
    }

    from ingest_layer342 import _hash, compute_pipeline_hash
    h_out0 = _hash(t0)
    h_out1 = _hash(t1)
    h_out2 = _hash(t2)

    executed = [
        ("layer3", l2_hash, h_out0, l2_hash),
        ("layer4_1", h_out0, h_out1, h_out0),
        ("layer4_2", h_out1, h_out2, h_out1),
    ]
    pipe_hash = compute_pipeline_hash(executed)

    manifest_update = {
        "layer3_input_hash": l2_hash,
        "layer3_output_hash": h_out0,
        "layer4_1_input_hash": h_out0,
        "layer4_1_output_hash": h_out1,
        "layer4_2_input_hash": h_out1,
        "layer4_2_output_hash": h_out2,
        "pipeline_hash": pipe_hash,
    }

    report = {
        "layers": {
            "layer3": {"status": "pass"},
            "layer4_1": {"status": "pass"},
            "layer4_2": {"status": "pass"},
        }
    }

    return f"""<LAYER3_OUTPUT>
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


def _make_layer342_bundle_l3_fail(root: Path | None = None, l2_hash: str | None = None) -> str:
    if root is not None and l2_hash is None:
        l2_file = root / "Artifacts" / "layer2_csir.json"
        if l2_file.exists():
            l2_hash = _rehash_file(l2_file)
    if l2_hash is None:
        l2_hash = "0" * 64

    t0 = {
        "schema_version": "2.1",
        "validation_failed": True,
        "VALIDATION_GATE": {"gate_result": "fail", "halt_reason": "Low visual contrast violation"}
    }
    manifest_update = {
        "layer3_input_hash": l2_hash,
    }
    report = {
        "layers": {
            "layer3": {"status": "fail"}
        },
        "halt_reason": "Low visual contrast violation",
        "halt_stage": "Layer3",
    }
    return f"""<LAYER3_OUTPUT>
{json.dumps(t0, indent=2)}
</LAYER3_OUTPUT>
<MANIFEST_UPDATE>
{json.dumps(manifest_update, indent=2)}
</MANIFEST_UPDATE>
<REPORT>
{json.dumps(report, indent=2)}
</REPORT>"""


def _make_layer342_bundle_l4_2_fail(root: Path | None = None, l2_hash: str | None = None) -> str:
    if root is not None and l2_hash is None:
        l2_file = root / "Artifacts" / "layer2_csir.json"
        if l2_file.exists():
            l2_hash = _rehash_file(l2_file)
    if l2_hash is None:
        l2_hash = "0" * 64

    t0 = {
        "schema_version": "2.1",
        "visuals": [
            {
                "id": "btn_submit",
                "style": {"background_color": "#0088FF"},
                "raw_text": "Submit Form"
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
                    "id": "btn_submit",
                    "layout": {"layout_type": "flex_row"},
                    "children": []
                }
            ]
        },
        "VALIDATION_GATE": {"gate_result": "pass"}
    }
    t2 = {
        "schema_version": "2.1",
        "validation_failed": True,
        "validation": {"status": "fail", "reason": "Component cycle detected"},
        "VALIDATION_GATE": {"gate_result": "fail"}
    }

    from ingest_layer342 import _hash
    h_out0 = _hash(t0)
    h_out1 = _hash(t1)

    manifest_update = {
        "layer3_input_hash": l2_hash,
        "layer3_output_hash": h_out0,
        "layer4_1_input_hash": h_out0,
        "layer4_1_output_hash": h_out1,
        "layer4_2_input_hash": h_out1,
    }

    report = {
        "layers": {
            "layer3": {"status": "pass"},
            "layer4_1": {"status": "pass"},
            "layer4_2": {"status": "fail"}
        },
        "halt_reason": "Component cycle detected",
        "halt_stage": "Layer4.2"
    }

    return f"""<LAYER3_OUTPUT>
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


# ---------------------------------------------------------------------------
# Scenarios 1 to 6
# ---------------------------------------------------------------------------

def test_scenario_1_layer12_ingest_produces_artifacts_and_run_id():
    """Scenario 1: Drop layer12_bundle.txt, run ingest.py -> confirm layer1_ccir/layer2_csir written, capture run_id."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen1"))

        res = ingest.ingest(root)
        assert res == EXIT_OK, f"ingest.py failed with exit code {res}"

        # Confirm artifacts written to disk
        l1_path = root / "Artifacts" / "layer1_ccir.json"
        l2_path = root / "Artifacts" / "layer2_csir.json"
        assert l1_path.exists(), "layer1_ccir.json must exist"
        assert l2_path.exists(), "layer2_csir.json must exist"

        # Locate manifest and capture run_id
        manifest_dir = root / "Manifest"
        manifests = list(manifest_dir.glob("manifest_*.json"))
        assert len(manifests) == 1, f"Expected 1 manifest, found {len(manifests)}"
        manifest_data = json.loads(manifests[0].read_text(encoding="utf-8"))

        run_id = manifest_data.get("run_id")
        assert run_id, "Manifest must have a non-empty run_id"
        assert manifests[0].name == f"manifest_{run_id}.json"

        # Check ledger
        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        assert ledger_path.exists()
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        artifacts_in_ledger = [e["artifact"] for e in ledger if e.get("run_id") == run_id]
        assert "layer1_ccir" in artifacts_in_ledger
        assert "layer2_csir" in artifacts_in_ledger

        # Confirm inbox file was moved to ingested/
        assert not (root / "Python_Inbox" / "layer12_bundle.txt").exists()
        assert (root / "Python_Output" / "runs" / run_id / "ingested" / "layer12_bundle.txt").exists()


def test_scenario_2_layer342_ingest_and_disk_recomputed_hash_chain():
    """Scenario 2: Drop layer342_bundle.txt (all 3 pass) -> confirm all 3 artifacts written, verify hash chain by independently re-hashing disk files."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Step 1: Ingest Layer 1+2
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen2"))
        assert ingest.ingest(root) == EXIT_OK

        manifest_dir = root / "Manifest"
        manifest_file = next(manifest_dir.glob("manifest_*.json"))
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        run_id = manifest_data["run_id"]

        # Step 2: Ingest Layer 342
        _write_inbox(root, "layer342_bundle.txt", _make_layer342_bundle_pass(root=root))
        res_342 = ingest_layer342.ingest(root)
        assert res_342 == EXIT_OK, f"ingest_layer342.py failed with code {res_342}"

        # Confirm all 3 artifacts written to disk
        l3_path = root / "Artifacts" / "layer3_visual_ir.json"
        l4_1_path = root / "Artifacts" / "layer4_1_semantic_ir.json"
        l4_2_path = root / "Artifacts" / "layer4_2_execution_ir.json"
        assert l3_path.exists(), "layer3_visual_ir.json must exist"
        assert l4_1_path.exists(), "layer4_1_semantic_ir.json must exist"
        assert l4_2_path.exists(), "layer4_2_execution_ir.json must exist"

        # Independently recompute disk hashes
        h_l1 = _rehash_file(root / "Artifacts" / "layer1_ccir.json")
        h_l2 = _rehash_file(root / "Artifacts" / "layer2_csir.json")
        h_l3 = _rehash_file(l3_path)
        h_l4_1 = _rehash_file(l4_1_path)
        h_l4_2 = _rehash_file(l4_2_path)

        # Verify against updated manifest
        updated_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert updated_manifest["artifacts"]["layer1_ccir"]["hash"] == h_l1
        assert updated_manifest["artifacts"]["layer2_csir"]["hash"] == h_l2
        assert updated_manifest["artifacts"]["layer3_visual_ir"]["hash"] == h_l3
        assert updated_manifest["artifacts"]["layer4_1_semantic_ir"]["hash"] == h_l4_1
        assert updated_manifest["artifacts"]["layer4_2_execution_ir"]["hash"] == h_l4_2
        assert updated_manifest["next_layer"] == 12

        # Verify hash chain in Artifact_Ledger.json
        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text(encoding="utf-8"))
        run_ledger = {e["artifact"]: e for e in ledger if e.get("run_id") == run_id}

        assert run_ledger["layer1_ccir"]["hash"] == h_l1
        assert run_ledger["layer2_csir"]["hash"] == h_l2
        assert run_ledger["layer2_csir"]["parent_hash"] == h_l1

        assert run_ledger["layer3_visual_ir"]["hash"] == h_l3
        assert run_ledger["layer3_visual_ir"]["parent_hash"] == h_l2

        assert run_ledger["layer4_1_semantic_ir"]["hash"] == h_l4_1
        assert run_ledger["layer4_1_semantic_ir"]["parent_hash"] == h_l3

        assert run_ledger["layer4_2_execution_ir"]["hash"] == h_l4_2
        assert run_ledger["layer4_2_execution_ir"]["parent_hash"] == h_l4_1

        # Confirm layer342_bundle moved to ingested/
        assert not (root / "Python_Inbox" / "layer342_bundle.txt").exists()
        assert (root / "Python_Output" / "runs" / run_id / "ingested" / "layer342_bundle.txt").exists()


def test_scenario_3_pipeline_full_run_and_validate_runlogger_evidence():
    """Scenario 3: Run Pipeline.run() -> reaches Layer4_3/5/6 stages, confirm ValidateL3/L4_1/L4_2 each logged reading data."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Ingest Layer 1+2
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen3"))
        assert ingest.ingest(root) == EXIT_OK

        manifest_file = next((root / "Manifest").glob("manifest_*.json"))
        run_id = json.loads(manifest_file.read_text(encoding="utf-8"))["run_id"]

        # Ingest Layer 342
        _write_inbox(root, "layer342_bundle.txt", _make_layer342_bundle_pass(root=root))
        assert ingest_layer342.ingest(root) == EXIT_OK

        # Run Pipeline
        ctx = PipelineContext(run_id=run_id, manifest_path=manifest_file)
        pipeline_exit = Pipeline().run(ctx)
        assert pipeline_exit == EXIT_OK, f"Pipeline.run() failed with exit code {pipeline_exit}"

        # Inspect RunLogger JSONL output
        log_file = root / "Logs" / f"Run_{run_id}.jsonl"
        assert log_file.exists(), f"Log file {log_file} must exist"

        log_entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        # 1. Assert Validate stages logged actually reading and verifying data from disk (not silent no-op)
        validate_messages = [e.get("message", "") for e in log_entries]

        assert any("Loaded and verified layer3_visual_ir from disk" in msg for msg in validate_messages), (
            "ValidateL3Stage did not log reading and verifying layer3_visual_ir from disk"
        )
        assert any("Loaded and verified layer4_1_semantic_ir from disk" in msg for msg in validate_messages), (
            "ValidateL4_1Stage did not log reading and verifying layer4_1_semantic_ir from disk"
        )
        assert any("Loaded and verified layer4_2_execution_ir from disk" in msg for msg in validate_messages), (
            "ValidateL4_2Stage did not log reading and verifying layer4_2_execution_ir from disk"
        )

        # 2. Assert stages reached Layer4_3, Layer5, Layer6 and passed
        passed_stages = {e.get("step") for e in log_entries if e.get("status") == "PASSED"}
        for expected_stage in ("ValidateL3", "ValidateL4.1", "ValidateL4.2", "Layer4.3", "Layer5", "Layer6", "MetaVerify", "Save", "Archive", "PipelineLedger", "BuildReport"):
            assert expected_stage in passed_stages, f"Stage {expected_stage} was not reached and passed"

        # 3. Confirm ctx.storage has populated IR objects
        assert ctx.storage.get("l3_data"), "ctx.storage['l3_data'] must be populated"
        assert ctx.storage.get("l4_1_data"), "ctx.storage['l4_1_data'] must be populated"
        assert ctx.storage.get("l4_2_data"), "ctx.storage['l4_2_data'] must be populated"


def test_scenario_4_layer3_gate_failure_halts_ingest_and_pipeline():
    """Scenario 4: Layer 3 gate fails -> ingest halts, no layer4_1/4_2 artifacts, FAILED_layer3.json exists, Pipeline.run() halts."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Ingest Layer 1+2
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen4"))
        assert ingest.ingest(root) == EXIT_OK

        manifest_file = next((root / "Manifest").glob("manifest_*.json"))
        run_id = json.loads(manifest_file.read_text(encoding="utf-8"))["run_id"]

        # Drop Layer 342 bundle with failing Layer 3 gate
        _write_inbox(root, "layer342_bundle.txt", _make_layer342_bundle_l3_fail(root=root))
        res_342 = ingest_layer342.ingest(root)
        assert res_342 == EXIT_10, f"Expected EXIT_10 on Layer 3 gate failure, got {res_342}"

        # Confirm Layer 3 failed gate behavior
        assert not (root / "Artifacts" / "layer3_visual_ir.json").exists()
        assert not (root / "Artifacts" / "layer4_1_semantic_ir.json").exists()
        assert not (root / "Artifacts" / "layer4_2_execution_ir.json").exists()

        failed_file = root / "Python_Output" / "runs" / run_id / "FAILED_layer3.json"
        assert failed_file.exists(), "FAILED_layer3.json must exist"
        failed_data = json.loads(failed_file.read_text(encoding="utf-8"))
        assert failed_data.get("validation_failed") is True

        # Confirm ledger entry is failed_gate
        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text(encoding="utf-8"))
        l3_entries = [e for e in ledger if e.get("artifact") == "layer3_visual_ir" and e.get("run_id") == run_id]
        assert len(l3_entries) == 1
        assert l3_entries[0]["status"] == "failed_gate"

        # Confirm bundle NOT moved from Python_Inbox
        assert (root / "Python_Inbox" / "layer342_bundle.txt").exists()

        # If Pipeline.run() is invoked anyway, confirm it halts cleanly rather than proceeding on missing data
        ctx = PipelineContext(run_id=run_id, manifest_path=manifest_file)
        p_res = Pipeline().run(ctx)
        assert p_res != EXIT_OK, f"Pipeline should have halted on missing/failed Layer 3 data, returned {p_res}"


def test_scenario_5_layer4_2_failure_persists_prior_artifacts():
    """Scenario 5: Layer 3 & 4.1 pass, Layer 4.2 fails -> Layer 3 & 4.1 persist, layer4_2 does not, FAILED_layer4_2.json exists."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Ingest Layer 1+2
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen5"))
        assert ingest.ingest(root) == EXIT_OK

        manifest_file = next((root / "Manifest").glob("manifest_*.json"))
        run_id = json.loads(manifest_file.read_text(encoding="utf-8"))["run_id"]

        # Drop Layer 342 bundle where L3 and L4.1 pass, but L4.2 fails
        _write_inbox(root, "layer342_bundle.txt", _make_layer342_bundle_l4_2_fail(root=root))
        res_342 = ingest_layer342.ingest(root)
        assert res_342 == EXIT_10, f"Expected EXIT_10 on Layer 4.2 failure, got {res_342}"

        # Confirm Layer 3 and Layer 4.1 artifacts exist
        assert (root / "Artifacts" / "layer3_visual_ir.json").exists()
        assert (root / "Artifacts" / "layer4_1_semantic_ir.json").exists()

        # Confirm Layer 4.2 artifact was NOT written
        assert not (root / "Artifacts" / "layer4_2_execution_ir.json").exists()

        # Confirm FAILED_layer4_2.json exists
        failed_file = root / "Python_Output" / "runs" / run_id / "FAILED_layer4_2.json"
        assert failed_file.exists(), "FAILED_layer4_2.json must exist"
        failed_data = json.loads(failed_file.read_text(encoding="utf-8"))
        assert failed_data.get("validation_failed") is True

        # Confirm ledger records written for L3, L4.1 and failed_gate for L4.2
        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text(encoding="utf-8"))
        l3_e = next(e for e in ledger if e.get("artifact") == "layer3_visual_ir" and e.get("run_id") == run_id)
        l4_1_e = next(e for e in ledger if e.get("artifact") == "layer4_1_semantic_ir" and e.get("run_id") == run_id)
        l4_2_e = next(e for e in ledger if e.get("artifact") == "layer4_2_execution_ir" and e.get("run_id") == run_id)

        assert l3_e["status"] == "written"
        assert l4_1_e["status"] == "written"
        assert l4_2_e["status"] == "failed_gate"

        # Confirm bundle NOT moved from Python_Inbox
        assert (root / "Python_Inbox" / "layer342_bundle.txt").exists()


def test_scenario_6_tamper_layer4_1_disk_fails_and_rolls_back_pipeline():
    """Scenario 6: Tamper test: edit layer4_1_semantic_ir on disk after successful ingest -> ValidateL4.1 catches mismatch and rolls back."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Ingest Layer 1+2
        _write_inbox(root, "layer12_bundle.txt", _make_layer12_bundle("proj_scen6"))
        assert ingest.ingest(root) == EXIT_OK

        manifest_file = next((root / "Manifest").glob("manifest_*.json"))
        run_id = json.loads(manifest_file.read_text(encoding="utf-8"))["run_id"]

        # Ingest Layer 342 (full pass)
        _write_inbox(root, "layer342_bundle.txt", _make_layer342_bundle_pass(root=root))
        assert ingest_layer342.ingest(root) == EXIT_OK

        # Tamper with Artifacts/layer4_1_semantic_ir.json on disk
        l4_1_file = root / "Artifacts" / "layer4_1_semantic_ir.json"
        l4_1_data = json.loads(l4_1_file.read_text(encoding="utf-8"))
        l4_1_data["root"]["layout"]["gap"] = 9999
        l4_1_file.write_text(json.dumps(l4_1_data), encoding="utf-8")

        # Run Pipeline
        ctx = PipelineContext(run_id=run_id, manifest_path=manifest_file)
        pipeline_exit = Pipeline().run(ctx)

        # Confirm failure and rollback
        assert pipeline_exit != EXIT_OK, "Pipeline should have failed due to tampered disk artifact"
        assert ctx.storage.get("failed_stage") == "ValidateL4.1"

        # Check log file confirms hash mismatch
        log_file = root / "Logs" / f"Run_{run_id}.jsonl"
        assert log_file.exists()
        log_entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        mismatch_logged = any(
            e.get("step") == "ValidateL4.1" and "Hash mismatch for layer4_1_semantic_ir" in e.get("message", "")
            for e in log_entries
        )
        assert mismatch_logged, "ValidateL4.1 did not log hash mismatch error"

        rollback_logged = any(e.get("step") == "Rollback" and e.get("status") == "PASSED" for e in log_entries)
        assert rollback_logged, "RollbackStage was not invoked following ValidateL4.1 failure"


if __name__ == "__main__":
    test_scenario_1_layer12_ingest_produces_artifacts_and_run_id()
    print("PASS: Scenario 1")
    test_scenario_2_layer342_ingest_and_disk_recomputed_hash_chain()
    print("PASS: Scenario 2")
    test_scenario_3_pipeline_full_run_and_validate_runlogger_evidence()
    print("PASS: Scenario 3")
    test_scenario_4_layer3_gate_failure_halts_ingest_and_pipeline()
    print("PASS: Scenario 4")
    test_scenario_5_layer4_2_failure_persists_prior_artifacts()
    print("PASS: Scenario 5")
    test_scenario_6_tamper_layer4_1_disk_fails_and_rolls_back_pipeline()
    print("PASS: Scenario 6")
    print("ALL 6 INTEGRATION SCENARIOS PASSED!")

