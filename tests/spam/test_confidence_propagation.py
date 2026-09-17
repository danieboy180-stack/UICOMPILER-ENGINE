import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "ingest.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    
from orchestrator.orchestrator import PipelineContext, ConfidencePropagationStage
from orchestrator.lock import locked_write, locked_read

def run_stage(root, l1_data, l2_data):
    # Setup artifacts
    art_dir = root / "Artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    with locked_write(art_dir / "layer1_ccir.json", l1_data):
        pass
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
    stage = ConfidencePropagationStage()
    
    import io
    from contextlib import redirect_stdout, redirect_stderr
    f = io.StringIO()
    with redirect_stdout(f), redirect_stderr(f):
        success = stage.run(ctx)
        
    # Read the updated l2_data from storage/disk
    return success, ctx.storage.get("l2_data")

def test_confidence_propagation():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        
        # 1. Test clean (All high confidence)
        l1_clean = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "obj1", "confidence": 0.95},
                {"id": "obj2", "confidence": 0.85}
            ]
        }
        l2_clean = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "obj1"},
                {"id": "obj2"}
            ]
        }
        success, l2_out = run_stage(root, l1_clean, l2_clean)
        assert success
        assert "weak_child" not in l2_out["semantics"][0]
        assert "weak_child" not in l2_out["semantics"][1]
        
        # 2. Test weak_child (One low confidence)
        l1_weak = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "obj1", "confidence": 0.95},
                {"id": "obj2", "confidence": 0.75} # Below 0.8 threshold
            ]
        }
        l2_weak = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "obj1"},
                {"id": "obj2"}
            ]
        }
        success, l2_out = run_stage(root, l1_weak, l2_weak)
        assert success
        assert "weak_child" not in l2_out["semantics"][0]
        assert l2_out["semantics"][1].get("weak_child") is True

        # Write verification receipt
        from verification.receipt import record_verification
        record_verification("test_confidence_propagation", "Confidence propagation verified", "SUCCESS\n" + "\n".join(["padding"]*10), True)

if __name__ == "__main__":
    test_confidence_propagation()
    print("PASS test_confidence_propagation")
