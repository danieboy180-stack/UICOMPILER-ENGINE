"""
Tests for Layer 3, Layer 4.1, and Layer 4.2 stages.
Each stage has a valid-input case and a broken-input case.
Follows the record_verification(...) pattern.
"""

import json
import sys
import tempfile
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.orchestrator import (
    PipelineContext,
    Layer3Stage,
    ValidateL3Stage,
    Layer4_1Stage,
    ValidateL4_1Stage,
    Layer4_2Stage,
    ValidateL4_2Stage,
)
from orchestrator.lock import locked_write
from verification.receipt import record_verification


def _setup_test_context(root: Path, run_id: str = "test_run") -> PipelineContext:
    manifest_dir = root / "Manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"manifest_{run_id}.json"
    manifest_data = {
        "run_id": run_id,
        "pipeline_id": "test_pipeline_123",
        "image_hash": "hash_img_abc",
        "artifacts": {
            "layer1_ccir": {"hash": "hash_l1_test"},
            "layer2_csir": {"hash": "hash_l2_test"}
        }
    }
    with locked_write(manifest_path, manifest_data):
        pass

    art_dir = root / "Artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = art_dir / "Artifact_Ledger.json"
    ledger_data = [
        {
            "artifact": "layer1_ccir",
            "run_id": run_id,
            "hash": "hash_l1_test",
            "parent_hash": None,
            "status": "written"
        },
        {
            "artifact": "layer2_csir",
            "run_id": run_id,
            "hash": "hash_l2_test",
            "parent_hash": "hash_l1_test",
            "status": "written"
        }
    ]
    with locked_write(ledger_path, ledger_data):
        pass

    ctx = PipelineContext(run_id=run_id, manifest_path=manifest_path)
    return ctx


def test_layer3_valid_and_broken():
    """Test Layer3Stage and ValidateL3Stage with valid and broken inputs."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = _setup_test_context(root, "l3_run")

        l1_valid = {
            "schema_version": "2.1",
            "candidates": [
                {
                    "id": "btn_1",
                    "bbox": {"x": 10, "y": 20, "w": 100, "h": 40},
                    "confidence": 0.95,
                    "dominant_color": "#FF0000",
                    "ocr_text": "Submit"
                }
            ]
        }
        l2_valid = {
            "schema_version": "2.1",
            "semantics": [
                {
                    "id": "btn_1",
                    "semantic_role": "button",
                    "semantic_variant": "primary"
                }
            ]
        }

        ctx.storage["l1_data"] = l1_valid
        ctx.storage["l2_data"] = l2_valid

        # Valid Case
        l3_stage = Layer3Stage()
        ok = l3_stage.run(ctx)
        assert ok is True, "Layer3Stage should succeed with valid inputs"
        assert "l3_data" in ctx.storage, "Layer3Stage must populate ctx.storage['l3_data']"

        l3_data = ctx.storage["l3_data"]
        assert "hash_chain" in l3_data, "l3_data must contain hash_chain block"
        for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
            assert k in l3_data["hash_chain"], f"Missing {k} in hash_chain"
        assert l3_data["visuals"][0]["id"] == "btn_1"
        assert l3_data["visuals"][0]["raw_text"] == "Submit"
        assert l3_data["visuals"][0]["style"]["background_color"] == "#FF0000"

        # ValidateL3Stage passes on valid data
        v3_stage = ValidateL3Stage()
        val_ok = v3_stage.run(ctx)
        assert val_ok is True, "ValidateL3Stage should validate real l3_data successfully"

        # Assert artifact written to disk
        l3_file = root / "Artifacts" / "layer3_visual_ir.json"
        assert l3_file.exists(), "layer3_visual_ir.json must be written to disk"

        # Broken Case 1: missing required semantics in L2
        ctx_broken = _setup_test_context(root, "l3_broken")
        ctx_broken.storage["l1_data"] = l1_valid
        ctx_broken.storage["l2_data"] = {"schema_version": "2.1"}  # missing semantics
        broken_ok = l3_stage.run(ctx_broken)
        assert broken_ok is False, "Layer3Stage should fail when L2 semantics is missing"

        # Broken Case 2: ValidateL3 fails when l3_data is recorded in manifest but missing on disk
        ctx_none = _setup_test_context(root, "l3_none")
        with locked_write(ctx_none.manifest_path, {"run_id": "l3_none", "artifacts": {"layer3_visual_ir": {"hash": "dummy"}}}):
            pass
        none_ok = v3_stage.run(ctx_none)
        assert none_ok is False, "ValidateL3Stage must fail when l3_data is recorded in manifest but missing on disk"


        # Broken Case 3: ValidateL3 fails on malformed hash_chain
        ctx_bad_hash = _setup_test_context(root, "l3_bad_hash")
        ctx_bad_hash.storage["l3_data"] = {
            "schema_version": "2.1",
            "hash_chain": {},  # missing required keys
            "visuals": []
        }
        bad_hash_ok = v3_stage.run(ctx_bad_hash)
        assert bad_hash_ok is False, "ValidateL3Stage must fail on missing hash_chain keys"

        evidence = [
            f"Layer3Stage valid run success: {ok}",
            f"l3_data keys: {list(l3_data.keys())}",
            f"hash_chain keys: {list(l3_data['hash_chain'].keys())}",
            f"visuals count: {len(l3_data['visuals'])}",
            f"ValidateL3Stage valid run: {val_ok}",
            f"Broken L2 run: {broken_ok}",
            f"ValidateL3Stage on None: {none_ok}",
            f"ValidateL3Stage on bad hash: {bad_hash_ok}",
            "Artifact exists on disk: True",
            "All Layer 3 contracts verified successfully."
        ]
        record_verification("test_layer3", "Layer3Stage valid and broken input contracts", "\n".join(evidence), True)


def test_layer4_1_valid_and_broken():
    """Test Layer4_1Stage and ValidateL4_1Stage with valid and broken inputs."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = _setup_test_context(root, "l4_1_run")

        l3_valid = {
            "schema_version": "2.1",
            "hash_chain": {
                "input_hash": "ih_test",
                "output_hash": "oh_test",
                "parent_hash": "ph_test",
                "pipeline_hash": "plh_test"
            },
            "visuals": [
                {
                    "id": "node_v1",
                    "style": {"background_color": "#0000FF"},
                    "raw_text": "Login"
                }
            ]
        }
        ctx.storage["l3_data"] = l3_valid

        # Valid Case
        l4_1_stage = Layer4_1Stage()
        ok = l4_1_stage.run(ctx)
        assert ok is True, "Layer4_1Stage should succeed with valid inputs"
        assert "l4_1_data" in ctx.storage, "Layer4_1Stage must populate ctx.storage['l4_1_data']"

        l4_1_data = ctx.storage["l4_1_data"]
        assert "hash_chain" in l4_1_data, "l4_1_data must contain hash_chain block"
        for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
            assert k in l4_1_data["hash_chain"], f"Missing {k} in hash_chain"
        assert "root" in l4_1_data, "l4_1_data must have root tree"
        assert l4_1_data["root"]["layout"]["layout_type"] in ("flex_row", "flex_col", "absolute")

        # ValidateL4_1Stage passes on valid data
        v4_1_stage = ValidateL4_1Stage()
        val_ok = v4_1_stage.run(ctx)
        assert val_ok is True, "ValidateL4_1Stage should validate real l4_1_data successfully"

        # Assert artifact written to disk
        l4_1_file = root / "Artifacts" / "layer4_1_semantic_ir.json"
        assert l4_1_file.exists(), "layer4_1_semantic_ir.json must be written to disk"

        # Broken Case 1: missing required visuals in L3
        ctx_broken = _setup_test_context(root, "l4_1_broken")
        ctx_broken.storage["l3_data"] = {"schema_version": "2.1"}  # missing visuals
        broken_ok = l4_1_stage.run(ctx_broken)
        assert broken_ok is False, "Layer4_1Stage should fail when L3 visuals is missing"

        # Broken Case 2: ValidateL4_1 fails when l4_1_data is recorded in manifest but missing on disk
        ctx_none = _setup_test_context(root, "l4_1_none")
        with locked_write(ctx_none.manifest_path, {"run_id": "l4_1_none", "artifacts": {"layer4_1_semantic_ir": {"hash": "dummy"}}}):
            pass
        none_ok = v4_1_stage.run(ctx_none)
        assert none_ok is False, "ValidateL4_1Stage must fail when l4_1_data is recorded in manifest but missing on disk"


        # Broken Case 3: Invalid layout type fails validation
        ctx_bad_layout = _setup_test_context(root, "l4_1_bad_layout")
        ctx_bad_layout.storage["l4_1_data"] = {
            "schema_version": "2.1",
            "hash_chain": {
                "input_hash": "ih", "output_hash": "oh",
                "parent_hash": "ph", "pipeline_hash": "plh"
            },
            "root": {
                "id": "root_node",
                "layout": {"layout_type": "invalid_grid_layout"},
                "children": []
            }
        }
        bad_layout_ok = v4_1_stage.run(ctx_bad_layout)
        assert bad_layout_ok is False, "ValidateL4_1Stage must reject invalid layout types"

        evidence = [
            f"Layer4_1Stage valid run success: {ok}",
            f"l4_1_data keys: {list(l4_1_data.keys())}",
            f"hash_chain keys: {list(l4_1_data['hash_chain'].keys())}",
            f"root node id: {l4_1_data['root']['id']}",
            f"ValidateL4_1Stage valid run: {val_ok}",
            f"Broken L3 run: {broken_ok}",
            f"ValidateL4_1Stage on None: {none_ok}",
            f"ValidateL4_1Stage on invalid layout: {bad_layout_ok}",
            "Artifact exists on disk: True",
            "All Layer 4.1 contracts verified successfully."
        ]
        record_verification("test_layer4_1", "Layer4_1Stage valid and broken input contracts", "\n".join(evidence), True)


def test_layer4_2_valid_and_broken():
    """Test Layer4_2Stage and ValidateL4_2Stage with valid and broken inputs."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctx = _setup_test_context(root, "l4_2_run")

        l1_data = {
            "schema_version": "2.1",
            "candidates": [{"id": "btn_submit", "bbox": {"x": 5, "y": 10, "w": 80, "h": 30}}]
        }
        l2_data = {
            "schema_version": "2.1",
            "semantics": [{"id": "btn_submit", "semantic_role": "button", "semantic_variant": "primary"}]
        }
        l3_data = {
            "schema_version": "2.1",
            "visuals": [{"id": "btn_submit", "style": {"background_color": "#00FF00"}, "raw_text": "Send"}]
        }
        l4_1_data = {
            "schema_version": "2.1",
            "root": {
                "id": "btn_submit",
                "layout": {"layout_type": "flex_col"},
                "children": []
            }
        }

        ctx.storage["l1_data"] = l1_data
        ctx.storage["l2_data"] = l2_data
        ctx.storage["l3_data"] = l3_data
        ctx.storage["l4_1_data"] = l4_1_data

        # Valid Case
        l4_2_stage = Layer4_2Stage()
        ok = l4_2_stage.run(ctx)
        assert ok is True, "Layer4_2Stage should succeed with valid inputs"
        assert "l4_2_data" in ctx.storage, "Layer4_2Stage must populate ctx.storage['l4_2_data']"

        l4_2_data = ctx.storage["l4_2_data"]
        assert "hash_chain" in l4_2_data, "l4_2_data must contain hash_chain block"
        for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
            assert k in l4_2_data["hash_chain"], f"Missing {k} in hash_chain"
        assert "root_node" in l4_2_data, "l4_2_data must have root_node"
        assert l4_2_data["root_node"]["type"] == "button"
        assert l4_2_data["root_node"]["content"] == "Send"

        # ValidateL4_2Stage passes on valid data
        v4_2_stage = ValidateL4_2Stage()
        val_ok = v4_2_stage.run(ctx)
        assert val_ok is True, "ValidateL4_2Stage should validate real l4_2_data successfully"

        # Assert artifact written to disk
        l4_2_file = root / "Artifacts" / "layer4_2_execution_ir.json"
        assert l4_2_file.exists(), "layer4_2_execution_ir.json must be written to disk"

        # Broken Case 1: Taxonomy violation (invalid type)
        ctx_bad_tax = _setup_test_context(root, "l4_2_bad_tax")
        ctx_bad_tax.storage["l4_2_data"] = {
            "schema_version": "2.1",
            "hash_chain": {"input_hash": "ih", "output_hash": "oh", "parent_hash": "ph", "pipeline_hash": "plh"},
            "root_node": {
                "id": "bad_node",
                "type": "unregistered_custom_type",  # outside canonical taxonomy
                "children": []
            }
        }
        bad_tax_ok = v4_2_stage.run(ctx_bad_tax)
        assert bad_tax_ok is False, "ValidateL4_2Stage must reject nodes with invalid taxonomy types"

        # Broken Case 2: Dangling interaction target
        ctx_dangling = _setup_test_context(root, "l4_2_dangling")
        ctx_dangling.storage["l4_2_data"] = {
            "schema_version": "2.1",
            "hash_chain": {"input_hash": "ih", "output_hash": "oh", "parent_hash": "ph", "pipeline_hash": "plh"},
            "root_node": {
                "id": "node_clicker",
                "type": "button",
                "interaction_target": "nonexistent_target_modal",  # Dangling!
                "children": []
            }
        }
        dangling_ok = v4_2_stage.run(ctx_dangling)
        assert dangling_ok is False, "ValidateL4_2Stage must reject dangling interaction targets"

        # Broken Case 3: Test flag force_layer4_2_fail
        ctx_flag = _setup_test_context(root, "l4_2_flag")
        ctx_flag.storage["force_layer4_2_fail"] = True
        flag_ok = v4_2_stage.run(ctx_flag)
        assert flag_ok is False, "ValidateL4_2Stage must respect force_layer4_2_fail flag"

        evidence = [
            f"Layer4_2Stage valid run success: {ok}",
            f"l4_2_data keys: {list(l4_2_data.keys())}",
            f"hash_chain keys: {list(l4_2_data['hash_chain'].keys())}",
            f"root_node type: {l4_2_data['root_node']['type']}",
            f"ValidateL4_2Stage valid run: {val_ok}",
            f"ValidateL4_2Stage taxonomy violation check: {bad_tax_ok}",
            f"ValidateL4_2Stage dangling target check: {dangling_ok}",
            f"ValidateL4_2Stage test flag check: {flag_ok}",
            "Artifact exists on disk: True",
            "All Layer 4.2 contracts verified successfully."
        ]
        record_verification("test_layer4_2", "Layer4_2Stage valid and broken input contracts", "\n".join(evidence), True)


if __name__ == "__main__":
    test_layer3_valid_and_broken()
    test_layer4_1_valid_and_broken()
    test_layer4_2_valid_and_broken()
    print("PASS test_layers_3_4")
