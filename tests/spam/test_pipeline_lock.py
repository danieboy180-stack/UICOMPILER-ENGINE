"""Pipeline runs are serialized per project root.

Every run writes into the shared Artifacts/ directory, so concurrent runs used to overwrite each other's files and
fail MetaVerify. A run now waits for the project's run lock; if it cannot get it in time it fails cleanly.
"""

import io
import json
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
FIXTURES = Path(__file__).parent.parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.lock import locked_read, simple_file_lock
from orchestrator.orchestrator import Pipeline, PipelineContext
from verification.receipt import record_verification


def _bundle(seed):
    l1 = json.loads((FIXTURES / "golden_l1_ccir.json").read_text(encoding="utf-8"))
    l2 = json.loads((FIXTURES / "golden_l2_csir.json").read_text(encoding="utf-8"))
    l1["project_id"] = f"lock_{seed}"
    l1["candidates"][0]["ocr_text"] = f"unique text {seed}"
    return f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>"


def _ctx(root):
    return PipelineContext(run_id=None, manifest_path=root / "Manifest" / "manifest_x.json")


def test_a_run_that_cannot_get_the_lock_fails_cleanly_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Python_Inbox").mkdir()
        (root / "Python_Inbox" / "layer12_bundle.txt").write_text(_bundle("held"), encoding="utf-8")
        ctx = _ctx(root)
        pipeline = Pipeline()
        pipeline.RUN_LOCK_TIMEOUT = 0.5
        buf = io.StringIO()
        held, release = threading.Event(), threading.Event()

        def competing_run():
            # The lock is re-entrant per thread, so a competing run has to be a different thread.
            with simple_file_lock(root / ".pipeline_run", timeout=5):
                held.set()
                release.wait(15)

        holder = threading.Thread(target=competing_run)
        holder.start()
        assert held.wait(5), "the competing run never took the lock"
        started = time.monotonic()
        try:
            with redirect_stdout(buf):
                rc = pipeline.run(ctx)
        finally:
            release.set()
            holder.join(timeout=15)
        waited = time.monotonic() - started

        assert rc == 10, rc
        assert "run lock" in buf.getvalue()
        assert ctx.errors == ["Pipeline run lock timeout"]
        assert (root / "Python_Inbox" / "layer12_bundle.txt").exists(), "the bundle must not be consumed without the lock"
        assert not (root / "Artifacts").exists() and not (root / "Manifest").exists(), "no state may be written"

        record_verification(
            "test_a_run_that_cannot_get_the_lock_fails_cleanly",
            "a run that cannot get the project lock fails cleanly without consuming the bundle or writing state",
            "\n".join([
                f"exit code: {rc}", f"errors: {ctx.errors}", f"waited seconds: {waited:.2f}",
                f"lock timeout configured: {pipeline.RUN_LOCK_TIMEOUT}",
                f"inbox after: {sorted(p.name for p in (root / 'Python_Inbox').iterdir())}",
                f"Artifacts exists: {(root / 'Artifacts').exists()}", f"Manifest exists: {(root / 'Manifest').exists()}",
                f"Logs: {sorted(p.name for p in (root / 'Logs').glob('*'))}",
                "log excerpt: " + (buf.getvalue().strip().splitlines() or [''])[-1],
                "stage logs written: " + str("STARTING" in buf.getvalue()),
                "contract: runs on one project root are serialized by .pipeline_run.lock",
            ]),
            True,
        )


def test_concurrent_runs_on_one_project_all_succeed_with_distinct_run_ids():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        inbox = root / "Python_Inbox"
        inbox.mkdir()
        target = inbox / "layer12_bundle.txt"
        write_lock = threading.Lock()
        results = {}

        def worker(seed):
            # Several clients drop bundles into the one inbox; each waits until the previous bundle was consumed.
            with write_lock:
                while target.exists():
                    time.sleep(0.005)
                target.write_text(_bundle(seed), encoding="utf-8")
            ctx = _ctx(root)
            with redirect_stdout(io.StringIO()):
                rc = Pipeline().run(ctx)
            results[seed] = (rc, ctx.run_id, ctx.storage.get("failed_stage"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)

        assert len(results) == 6, results
        assert all(rc == 0 for rc, _, _ in results.values()), results
        run_ids = [rid for _, rid, _ in results.values()]
        assert len(set(run_ids)) == 6, f"every run owns its id: {run_ids}"
        for rid in run_ids:
            assert (root / "Archives" / f"Run_{rid}").is_dir(), f"run {rid} was not archived"
        with locked_read(root / "Logs" / "Pipeline_Ledger.json", default=[]) as ledger:
            statuses = [(e["run_id"], e["status"], e["hash_chain_valid"]) for e in ledger]
        assert sorted(r for r, _, _ in statuses) == sorted(run_ids) and all(s == "PASS" and ok for _, s, ok in statuses)

        record_verification(
            "test_concurrent_runs_on_one_project",
            "six pipelines racing on one project root all complete with their own run ids and intact hash chains",
            "\n".join([f"run {seed}: exit={rc} run_id={rid} failed_stage={stage}" for seed, (rc, rid, stage) in sorted(results.items())]
                      + [f"distinct run ids: {len(set(run_ids))}", f"archives: {len(list((root / 'Archives').glob('Run_*')))}"]
                      + [f"ledger: {r} {s} hash_chain_valid={ok}" for r, s, ok in statuses]),
            True,
        )


if __name__ == "__main__":
    test_a_run_that_cannot_get_the_lock_fails_cleanly_and_writes_nothing()
    test_concurrent_runs_on_one_project_all_succeed_with_distinct_run_ids()
    print("PASS test_pipeline_lock")
