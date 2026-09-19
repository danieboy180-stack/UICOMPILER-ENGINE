"""
Test suite covering audit gap fixes:
- Task 1: Named stage reference invocation on failure path (not SaveStage/ArchiveStage).
- Task 4: run_full_verification.py fails when pytest fails, top line FAILED, no 10/10 scorecard.
"""

import sys
import tempfile
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.orchestrator import Pipeline, PipelineContext
from orchestrator.lock import locked_write, locked_read
from verification.receipt import record_verification


def test_stage_index_failure_path_executes_ledger_and_report():
    """
    Task 1: Forces a mid-pipeline failure and asserts PipelineLedgerStage AND
    BuildReportStage actually executed on that failure path — not SaveStage/ArchiveStage.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run_id = "test_mid_pipeline_fail"
        manifest_dir = root / "Manifest"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"manifest_{run_id}.json"
        with locked_write(manifest_path, {"run_id": run_id, "artifacts": {}}):
            pass

        ctx = PipelineContext(run_id=run_id, manifest_path=manifest_path)
        # Force a mid-pipeline failure at ValidateL4.2
        ctx.storage["force_layer4_2_fail"] = True

        pipeline = Pipeline()

        # Track which stages executed
        executed_stages = []

        orig_save = pipeline.save_stage.run
        def tracked_save(c):
            executed_stages.append("SaveStage")
            return orig_save(c)
        pipeline.save_stage.run = tracked_save

        orig_archive = pipeline.archive_stage.run
        def tracked_archive(c):
            executed_stages.append("ArchiveStage")
            return orig_archive(c)
        pipeline.archive_stage.run = tracked_archive

        orig_ledger = pipeline.pipeline_ledger_stage.run
        def tracked_ledger(c):
            executed_stages.append("PipelineLedgerStage")
            return orig_ledger(c)
        pipeline.pipeline_ledger_stage.run = tracked_ledger

        orig_report = pipeline.build_report_stage.run
        def tracked_report(c):
            executed_stages.append("BuildReportStage")
            return orig_report(c)
        pipeline.build_report_stage.run = tracked_report

        # Run pipeline (will fail at ValidateL4.2)
        res = pipeline.run(ctx)

        # Assertions
        assert res != 0, "Pipeline must exit nonzero on failure"
        assert "PipelineLedgerStage" in executed_stages, "PipelineLedgerStage MUST execute on failure path"
        assert "BuildReportStage" in executed_stages, "BuildReportStage MUST execute on failure path"
        assert "SaveStage" not in executed_stages, "SaveStage MUST NOT execute on failure path"
        assert "ArchiveStage" not in executed_stages, "ArchiveStage MUST NOT execute on failure path"

        # Check observable outputs
        ledger_path = root / "Logs" / "Pipeline_Ledger.json"
        assert ledger_path.exists(), "Pipeline_Ledger.json must be written on failure"
        with locked_read(ledger_path) as entries:
            last_entry = entries[-1] if isinstance(entries, list) else None
        assert last_entry is not None, "Ledger entry must exist"
        assert last_entry.get("run_id") == run_id
        assert last_entry.get("status") in ("FAIL", "ROLLED_BACK")

        build_report_path = root / "Reports" / f"Run_{run_id}" / "BUILD_REPORT.md"
        assert build_report_path.exists(), "BUILD_REPORT.md must be generated on failure"

        archive_dir = root / "Archives" / f"Run_{run_id}"
        assert not archive_dir.exists(), "Archives must NOT be created on failure path"

        evidence = [
            f"Exit code: {res}",
            f"Executed stages: {executed_stages}",
            f"Ledger written: {ledger_path.exists()}",
            f"Ledger status: {last_entry.get('status')}",
            f"Build report generated: {build_report_path.exists()}",
            f"Archive created: {archive_dir.exists()} (expected False)",
            "PipelineLedgerStage executed: True",
            "BuildReportStage executed: True",
            "SaveStage executed: False",
            "ArchiveStage executed: False"
        ]
        record_verification("test_stage_index_fix", "Failure path executes Ledger and Report not Save/Archive", "\n".join(evidence), True)


def test_run_full_verification_pytest_failure_reporting():
    """
    Task 4: Monkeypatched pytest failure -> run_full_verification.py must report failure,
    top line of generated report says FAILED, none of the 5 scorecard dimensions may show 10/10.
    """
    import run_full_verification

    # Create dummy ledger entries with pristine 10/10 values
    dummy_entries = [
        {
            "stage_id": "O14-Spam-Test",
            "verification_session_id": "session_dummy",
            "claim": "Test Claim",
            "raw_output_excerpt": "clean run",
            "passed": True,
            "timestamp": 1234567.0
        }
    ]

    # When pytest_ok is False, report must say FAILED at top line and cap scorecard
    report_failed = run_full_verification.build_report(dummy_entries, "session_dummy", pytest_ok=False)

    report_lines = report_failed.strip().splitlines()
    assert report_lines[0] == "FAILED", f"Top line of report must be 'FAILED', got '{report_lines[0]}'"

    # Assert NONE of the 5 scorecard dimensions show 10/10
    dimensions = ["Production-grade", "Scalability", "Solo-dev maintainability", "Durability", "Security posture"]
    for dim in dimensions:
        assert f"| **{dim}** | 10/10" not in report_failed, f"Dimension '{dim}' must NOT show 10/10 when pytest fails"

    # Also test subprocess behavior when pytest is forced to fail
    test_script = ROOT / "scratch_test_pytest_fail.py"
    try:
        test_script.write_text(
            "import sys\n"
            "import run_full_verification\n"
            "# Monkeypatch run_pytest to return False\n"
            "run_full_verification.run_pytest = lambda: False\n"
            "# Monkeypatch run_tests to return True\n"
            "run_full_verification.run_tests = lambda sid: True\n"
            "run_full_verification.main()\n",
            encoding="utf-8"
        )
        proc = subprocess.run([sys.executable, str(test_script)], cwd=str(ROOT), capture_output=True, text=True)
        assert proc.returncode != 0, f"run_full_verification must exit nonzero on pytest failure, got exit code {proc.returncode}"
    finally:
        if test_script.exists():
            test_script.unlink()

    evidence = [
        f"Top line: {report_lines[0]}",
        "Asserted no 10/10 in all 5 scorecard dimensions: True",
        f"Subprocess exit code on simulated pytest failure: {proc.returncode}",
        "Script exits nonzero when pytest fails: True",
        "Report correctly documents FAILED status: True",
        "Dimension Production-grade < 10/10: True",
        "Dimension Scalability < 10/10: True",
        "Dimension Solo-dev maintainability < 10/10: True",
        "Dimension Durability < 10/10: True",
        "Dimension Security posture < 10/10: True"
    ]
    record_verification("test_pytest_integration", "Pytest failure correctly reports FAILED and caps scorecard", "\n".join(evidence), True)


if __name__ == "__main__":
    test_stage_index_failure_path_executes_ledger_and_report()
    test_run_full_verification_pytest_failure_reporting()
    print("PASS test_audit_gap_fixes")
