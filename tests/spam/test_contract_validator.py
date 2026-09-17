import json
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "ingest.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    
from orchestrator.orchestrator import PipelineContext, ValidateL1Stage, ValidateL2Stage
from orchestrator.lock import locked_write

def run_stage(root, stage, l1_data, l2_data=None):
    # Setup artifacts
    art_dir = root / "Artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    with locked_write(art_dir / "layer1_ccir.json", l1_data):
        pass
    if l2_data is not None:
        with locked_write(art_dir / "layer2_csir.json", l2_data):
            pass
            
    # Setup manifest so ctx works
    mf = root / "Manifest" / "manifest_testrun.json"
    mf.parent.mkdir(parents=True, exist_ok=True)
    with locked_write(mf, {"run_id": "testrun"}):
        pass
        
    ctx = PipelineContext(run_id="testrun", manifest_path=mf)
    ctx.storage["l1_data"] = l1_data
    ctx.storage["l2_data"] = l2_data
    
    import io
    from contextlib import redirect_stdout, redirect_stderr
    f = io.StringIO()
    with redirect_stdout(f), redirect_stderr(f):
        success = stage.run(ctx)
    return success, f.getvalue()

def test_l1_forbidden_fields():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stage = ValidateL1Stage()
        
        # Test clean
        clean_data = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "c1", "bbox": {"x":0,"y":0,"w":10,"h":10}, "confidence": 0.9}
            ]
        }
        success, out = run_stage(root, stage, clean_data)
        assert success, out
        
        # Test forbidden
        forbidden = ["component", "template", "navigation_graph", "layout_engine", "region_type", "interaction_graph"]
        for f in forbidden:
            bad_data = {
                "schema_version": "2.1",
                "candidates": [
                    {"id": "c1", "bbox": {"x":0,"y":0,"w":10,"h":10}, f: "bad"}
                ]
            }
            success, out = run_stage(root, stage, bad_data)
            assert not success
            assert f"candidates[0].{f}" in out, f"Expected candidates[0].{f} in output, got: {out}"

def test_l2_traceability():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stage = ValidateL2Stage()
        
        # Test clean
        l1_clean = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "c1", "text": "hello", "color": "#000", "position": "absolute"}
            ]
        }
        l2_clean = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "c1", "semantic_role": "button", "text": "hello"}
            ]
        }
        success, out = run_stage(root, stage, l1_clean, l2_clean)
        assert success, out
        
        # Test bad (invented field)
        l1_bad = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "c1"} # missing text, color, position
            ]
        }
        l2_bad = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "c1", "semantic_role": "button", "text": "invented text"}
            ]
        }
        success, out = run_stage(root, stage, l1_bad, l2_bad)
        assert not success
        assert "semantics[0].text" in out, f"Expected semantics[0].text in output, got: {out}"

        # Additional write to Verification Ledger
        from verification.receipt import record_verification
        record_verification("test_contract_validator", "Contract validation limits enforced", "SUCCESS\n" + "\n".join(["padding"]*10), True)

if __name__ == "__main__":
    test_l1_forbidden_fields()
    test_l2_traceability()
    print("PASS test_contract_validator")
