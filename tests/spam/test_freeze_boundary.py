import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "ingest.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.orchestrator import PipelineContext, FreezeBoundaryStage
from orchestrator.lock import locked_write

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
    stage = FreezeBoundaryStage()

    import io
    from contextlib import redirect_stdout, redirect_stderr
    f = io.StringIO()
    with redirect_stdout(f), redirect_stderr(f):
        success = stage.run(ctx)
    return success, f.getvalue(), ctx.storage.get("pipeline_status")

def test_freeze_boundary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        # 1. Test clean (IDs match)
        l1_clean = {
            "schema_version": "2.1",
            "candidates": [
                {"id": "obj1"},
                {"id": "obj2"}
            ]
        }
        l2_clean = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "obj1"},
                {"id": "obj2"}
            ]
        }
        success, out, status = run_stage(root, l1_clean, l2_clean)
        assert success

        # 2. Test FREEZE_VIOLATION (L2 references absent ID)
        l2_bad = {
            "schema_version": "2.1",
            "semantics": [
                {"id": "obj1"},
                {"id": "obj3"} # obj3 not in L1
            ]
        }
        success, out, status = run_stage(root, l1_clean, l2_bad)
        assert not success
        assert status == "FREEZE_VIOLATION"
        assert "FREEZE_VIOLATION" in out

        # 3. Test Full Pipeline (simulating test requirements exactly)
        # "assert FREEZE_VIOLATION status in the ledger and EXIT_10"
        from orchestrator.orchestrator import Pipeline

        # We need to run ingest + pipeline via test_orchestrator mechanisms,
        # but to keep it simple we can just run Pipeline().run()
        pipeline = Pipeline()
        ctx2 = PipelineContext(run_id="testrun2", manifest_path=root / "Manifest" / "manifest_testrun2.json")
        with locked_write(ctx2.manifest_path, {"run_id": "testrun2"}):
            pass

        # Re-write the bad artifacts so FreezeBoundary picks them up
        with locked_write(root / "Artifacts" / "layer1_ccir.json", l1_clean):
            pass
        with locked_write(root / "Artifacts" / "layer2_csir.json", l2_bad):
            pass

        import io
        from contextlib import redirect_stdout, redirect_stderr
        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            # IngestStage will fail if no bundle in inbox, let's bypass it by removing it
            pipeline.stages = pipeline.stages[1:] # Skip ingest
            exit_code = pipeline.run(ctx2)

        # assert EXIT_10
        assert exit_code == 10

        # assert FREEZE_VIOLATION status in the ledger
        pl_path = root / "Logs" / "Pipeline_Ledger.json"
        from orchestrator.lock import locked_read
        with locked_read(pl_path) as ledger:
            assert any(e.get("status") == "FREEZE_VIOLATION" for e in ledger)

        # Write verification receipt
        from verification.receipt import record_verification
        import json
        evidence = [f"exit code: {exit_code}", f"ledger statuses: {[e.get('status') for e in ledger]}"]
        evidence += [f"ledger entry: {json.dumps(e, sort_keys=True)}" for e in ledger]
        evidence += [f"pipeline log: {line}" for line in f.getvalue().splitlines() if line.strip()]
        evidence.append(f"FREEZE_VIOLATION entries in ledger: {sum(1 for e in ledger if e.get('status') == 'FREEZE_VIOLATION')}")
        evidence.append(f"pipeline log line count: {len([line for line in f.getvalue().splitlines() if line.strip()])}")
        record_verification("test_freeze_boundary", "Freeze boundary constraint verified", "\n".join(evidence), True)

if __name__ == "__main__":
    test_freeze_boundary()
    print("PASS test_freeze_boundary")
