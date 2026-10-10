"""Orchestrator scenarios: every failure path must end where it should and leave the state it should.

Each scenario runs the full Pipeline in its own temp project root with its own bundle (built from the golden
fixtures), so nothing leaks between scenarios. Failures are injected with the pipeline's force_* storage flags.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent
FIXTURES = ROOT / "tests" / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import Pipeline, PipelineContext
from orchestrator.lock import locked_read
from verification.receipt import record_verification

ALL_LAYERS = ["layer1_ccir", "layer2_csir", "layer3_visual_ir", "layer4_1_semantic_ir",
              "layer4_2_execution_ir", "layer4_3", "layer5", "layer6"]


def _bundle() -> str:
    l1 = json.loads((FIXTURES / "golden_l1_ccir.json").read_text(encoding="utf-8"))
    l2 = json.loads((FIXTURES / "golden_l2_csir.json").read_text(encoding="utf-8"))
    return f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>"


def _force_rmtree(path: Path) -> None:
    # Archives are read-only on purpose; give the owner write access back so the temp dir can be deleted.
    for dirpath, _dirnames, filenames in os.walk(path):
        os.chmod(dirpath, stat.S_IRWXU)
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), stat.S_IRUSR | stat.S_IWUSR)
    shutil.rmtree(path, ignore_errors=True)


class Run:
    def __init__(self, root: Path, ctx: PipelineContext, rc: int):
        self.root, self.ctx, self.rc = root, ctx, rc

    def rerun(self) -> int:
        with redirect_stdout(io.StringIO()):
            self.rc = Pipeline().run(self.ctx)
        return self.rc

    @property
    def run_id(self) -> str:
        return self.ctx.run_id

    @property
    def failed_stage(self):
        return self.ctx.storage.get("failed_stage")

    def pipeline_entry(self) -> dict:
        with locked_read(self.root / "Logs" / "Pipeline_Ledger.json", default=[]) as ledger:
            return [e for e in ledger if e.get("run_id") == self.run_id][-1]

    def ledger(self) -> list:
        with locked_read(self.root / "Artifacts" / "Artifact_Ledger.json", default=[]) as ledger:
            return [e for e in ledger if e.get("run_id") == self.run_id]

    def written(self) -> list:
        return [e["artifact"] for e in self.ledger() if e["status"] == "written"]

    def manifest(self) -> dict:
        with locked_read(self.ctx.manifest_path, default={}) as manifest:
            return manifest

    def report(self) -> str:
        return (self.root / "Reports" / f"Run_{self.run_id}" / "BUILD_REPORT.md").read_text(encoding="utf-8")

    def archive(self) -> Path:
        return self.root / "Archives" / f"Run_{self.run_id}"

    def archive_hashes(self) -> dict:
        return {str(p.relative_to(self.archive())): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.archive().rglob("*")) if p.is_file()}

    def failed_files(self) -> list:
        return sorted(p.name for p in (self.root / "Python_Output" / "runs" / self.run_id).glob("FAILED_*"))

    def evidence(self, *extra: str) -> str:
        entry = self.pipeline_entry()
        lines = [f"exit code: {self.rc}", f"failed stage: {self.failed_stage}", f"pipeline status: {entry['status']}",
                 f"hash_chain_valid: {entry['hash_chain_valid']}", f"files_produced: {entry['files_produced']}",
                 f"errors: {entry['errors']}"]
        lines += [f"ledger: {e['artifact']} {e['status']}" for e in self.ledger()]
        lines += list(extra)
        lines += [f"report: {line}" for line in self.report().splitlines() if line.strip()]
        return "\n".join(lines)


@contextmanager
def scenario(flags=None):
    root = Path(tempfile.mkdtemp())
    try:
        (root / "Python_Inbox").mkdir()
        (root / "Python_Inbox" / "layer12_bundle.txt").write_text(_bundle(), encoding="utf-8")
        ctx = PipelineContext(run_id=None, manifest_path=root / "Manifest" / "manifest_x.json", storage=dict(flags or {}))
        with redirect_stdout(io.StringIO()):
            rc = Pipeline().run(ctx)
        yield Run(root, ctx, rc)
    finally:
        _force_rmtree(root)


def _canonical_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def test_clean_run_archives_reports_and_passes():
    with scenario() as r:
        assert r.rc == 0 and r.failed_stage is None
        entry = r.pipeline_entry()
        assert entry["status"] == "PASS" and entry["hash_chain_valid"] is True
        assert entry["files_produced"] == len(ALL_LAYERS) and entry["errors"] == []
        assert r.written() == ALL_LAYERS

        assert r.archive().is_dir(), "a passing run is archived"
        for source in sorted((r.root / "Artifacts").glob("*.json")):
            copy = r.archive() / "Artifacts" / source.name
            assert copy.read_bytes() == source.read_bytes(), f"{source.name}: archive differs from Artifacts"
            assert stat.S_IMODE(copy.stat().st_mode) & 0o222 == 0, f"{copy.name} must be read-only"
        assert stat.S_IMODE(r.archive().stat().st_mode) & 0o100, "archive directories must stay traversable"

        report = r.report()
        assert "**compiler_integrity**: PASS" in report and "**errors**: None" in report
        assert f"**artifacts_produced**: {len(ALL_LAYERS)}" in report
        assert "100%" not in report, "fidelity is not measured, so the report must not claim a percentage"
        record_verification("orchestrator-clean-run", "clean run: PASS, archived read-only, report written",
                            r.evidence(f"archive: {sorted(p.name for p in r.archive().iterdir())}"), True)


def test_rerunning_a_finished_run_is_refused_and_archive_untouched():
    with scenario() as r:
        assert r.rc == 0
        ledger_before, archive_before = r.ledger(), r.archive_hashes()
        r.ctx.storage = {}
        assert r.rerun() == 10
        assert r.failed_stage == "Ingest", "a finished run is refused up front, before any stage writes"
        assert r.ledger() == ledger_before, "no duplicate artifact entries may be appended to the finished run"
        assert r.archive_hashes() == archive_before, "the archive is immutable"
        assert r.pipeline_entry()["status"] == "FAIL"
        record_verification("orchestrator-rerun-refused", "re-running a finished run is refused, archive untouched",
                            r.evidence(f"archive files unchanged: {len(archive_before)}"), True)


def test_validate_l42_failure_exits_20_and_rolls_back():
    with scenario({"force_layer4_2_fail": True, "force_layer4_2_exit_20": True}) as r:
        assert r.rc == 20 and r.failed_stage == "ValidateL4.2"
        entry = r.pipeline_entry()
        assert entry["status"] == "ROLLED_BACK" and entry["files_produced"] == 0
        assert r.failed_files() == ["FAILED_layer4_2_execution_ir.json"]
        assert not r.archive().exists(), "a failed run is never archived"
        assert "**errors**: ValidateL4.2 failed" in r.report()
        record_verification("orchestrator-l42-fail", "ValidateL4.2 failure: exit 20, rolled back, not archived",
                            r.evidence(f"failed files: {r.failed_files()}"), True)


def test_layer5_crash_rolls_back_to_the_checkpoint():
    with scenario({"force_layer5_crash": True}) as r:
        assert r.rc == 40 and r.failed_stage == "Layer5"
        entry = r.pipeline_entry()
        assert entry["status"] == "ROLLED_BACK" and entry["hash_chain_valid"] is True
        assert r.failed_files() == ["FAILED_layer5.json"], "the partial output is quarantined"
        assert not (r.root / "Artifacts" / "layer5.json").exists(), "the partial output is gone from Artifacts"
        checkpoint = r.root / "Checkpoints" / r.run_id / "pre_layer5" / f"manifest_{r.run_id}.json"
        assert r.ctx.manifest_path.read_bytes() == checkpoint.read_bytes(), "the manifest is restored from the checkpoint"
        assert r.written() == ALL_LAYERS[:6], "the ledger keeps only what was written before the checkpoint"
        assert "layer5" not in r.manifest().get("artifacts", {})
        assert "**errors**: Layer5 failed" in r.report()
        record_verification("orchestrator-l5-crash", "Layer5 crash: exit 40, quarantined, manifest restored",
                            r.evidence(f"failed files: {r.failed_files()}", f"checkpoint: {checkpoint.relative_to(r.root)}"), True)


def test_layer5_ledger_append_is_atomic_and_consistent():
    with scenario() as r:
        entries = [e for e in r.ledger() if e["artifact"] == "layer5"]
        assert len(entries) == 1 and entries[0]["status"] == "written"
        with locked_read(r.root / "Artifacts" / "layer5.json") as payload:
            on_disk = _canonical_hash(payload)
        manifest_hash = r.manifest()["artifacts"]["layer5"]["hash"]
        assert entries[0]["hash"] == manifest_hash == on_disk, "ledger, manifest and the file on disk agree"
        parent = [e for e in r.ledger() if e["artifact"] == "layer4_3"][0]["hash"]
        assert entries[0]["parent_hash"] == parent, "layer5 chains to layer4_3"
        record_verification("orchestrator-l5-atomic-ledger", "layer5 ledger entry written once and consistent",
                            r.evidence(f"layer5 hash: {entries[0]['hash']}", f"layer5 parent: {entries[0]['parent_hash']}"), True)


def test_layer5_failed_gate_is_recorded_and_exits_30():
    with scenario({"force_layer5_failed_gate": True}) as r:
        assert r.rc == 30 and r.failed_stage == "Layer5"
        last = r.ledger()[-1]
        assert (last["artifact"], last["status"]) == ("layer5", "failed_gate")
        assert "layer5" not in r.manifest().get("artifacts", {}), "a failed gate never reaches the manifest"
        assert r.pipeline_entry()["status"] == "ROLLED_BACK"
        assert not r.archive().exists()
        record_verification("orchestrator-l5-failed-gate", "Layer5 failed gate: recorded, exit 30, rolled back",
                            r.evidence(f"last ledger entry: {last['artifact']} {last['status']}"), True)


def test_metaverify_detects_a_tampered_artifact():
    with scenario({"force_meta_verify_tamper": True}) as r:
        assert r.rc == 30 and r.failed_stage == "MetaVerify"
        with locked_read(r.root / "Artifacts" / "layer6.json") as payload:
            assert payload["payload"] == "tampered", "the scenario really tampered with layer6"
        entry = r.pipeline_entry()
        assert entry["status"] == "FAIL" and entry["hash_chain_valid"] is False
        assert "**compiler_integrity**: FAIL" in r.report()
        assert not r.archive().exists(), "tampered runs are never archived"
        record_verification("orchestrator-metaverify-tamper", "tampered artifact: exit 30, integrity FAIL, not archived",
                            r.evidence(f"layer6 payload on disk: {payload['payload']}"), True)


def test_metaverify_detects_a_pointer_chain_gap():
    with scenario({"force_meta_verify_gap": True}) as r:
        assert r.rc == 20 and r.failed_stage == "MetaVerify"
        entry = r.pipeline_entry()
        assert entry["status"] == "FAIL" and entry["hash_chain_valid"] is False
        broken = [e for e in r.ledger() if e["artifact"] == "layer6"][-1]
        assert broken["parent_hash"] == "broken_hash"
        assert not r.archive().exists()
        record_verification("orchestrator-metaverify-gap", "pointer chain gap: exit 20, integrity FAIL, not archived",
                            r.evidence(f"layer6 parent_hash in ledger: {broken['parent_hash']}"), True)


def test_save_failure_exits_50_without_archiving():
    with scenario({"force_save_fail": True}) as r:
        assert r.rc == 50 and r.failed_stage == "Save"
        entry = r.pipeline_entry()
        assert entry["status"] == "FAIL" and entry["hash_chain_valid"] is True
        assert not r.archive().exists(), "nothing is archived when Save failed"
        assert not (r.root / "Python_Output" / "runs" / r.run_id / "exported").exists()
        assert r.written() == ALL_LAYERS, "every layer was written; only the export failed"
        record_verification("orchestrator-save-fail", "Save failure: exit 50, nothing archived or exported",
                            r.evidence(f"exported dir exists: {(r.root / 'Python_Output' / 'runs' / r.run_id / 'exported').exists()}"), True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("PASS test_orchestrator")
