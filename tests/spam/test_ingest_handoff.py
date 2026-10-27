"""Ingest hands the run id back to the pipeline; the pipeline no longer guesses it from file timestamps.

Before: IngestStage scanned Manifest/ for any manifest written in the last 0.5 s and adopted it. A pipeline that found
an empty inbox right after another pipeline ingested therefore adopted THAT run, and a duplicate bundle with a
caller-supplied run_id carried on over stale artifacts.
"""

import io
import json
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
FIXTURES = Path(__file__).parent.parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ingest
from orchestrator.lock import locked_read
from orchestrator.orchestrator import IngestStage, Pipeline, PipelineContext
from verification.receipt import record_verification


def _bundle_text(project_id="handoff"):
    l1 = json.loads((FIXTURES / "golden_l1_ccir.json").read_text(encoding="utf-8"))
    l2 = json.loads((FIXTURES / "golden_l2_csir.json").read_text(encoding="utf-8"))
    l1["project_id"] = project_id
    return f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>"


def _stage(root: Path, text=None):
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "layer12_bundle.txt").write_text(text if text is not None else _bundle_text(), encoding="utf-8")


def _ctx(root: Path, run_id=None):
    return PipelineContext(run_id=run_id, manifest_path=root / "Manifest" / "manifest_x.json")


def _quiet(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args)
    return result, buf.getvalue()


def _written(root: Path):
    with locked_read(root / "Artifacts" / "Artifact_Ledger.json", default=[]) as ledger:
        return [e for e in ledger if e.get("status") == "written"]


def test_ingest_stage_does_not_adopt_a_run_it_did_not_create():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _stage(root)
        (code, run_id, outcome), _ = _quiet(ingest.ingest_bundle, root)
        assert (code, outcome) == (0, "ingested") and run_id

        # A second pipeline starts right after, finds the inbox empty and must halt, not borrow run_id's manifest.
        other = _ctx(root)
        ok, out = _quiet(IngestStage().run, other)
        assert ok is False, "an empty inbox is not a reason to adopt someone else's run"
        assert other.run_id is None

        record_verification(
            "test_ingest_stage_does_not_adopt_a_run_it_did_not_create",
            "IngestStage halts on an empty inbox instead of adopting a manifest written moments earlier",
            "\n".join([
                f"first ingest: code={code} outcome={outcome} run_id={run_id}",
                f"manifests on disk: {sorted(p.name for p in (root / 'Manifest').glob('manifest_*.json'))}",
                f"second pipeline: IngestStage.run -> {ok}",
                f"second pipeline run_id after ingest: {other.run_id}",
                f"inbox after: {sorted(p.name for p in (root / 'Python_Inbox').iterdir())}",
                f"written ledger entries: {[e['artifact'] for e in _written(root)]}",
                f"stage output: {out.strip() or '(none)'}",
                f"first run manifest exists: {(root / 'Manifest' / f'manifest_{run_id}.json').exists()}",
                f"second pipeline manifest_path: {other.manifest_path.name}",
                "adopted foreign run: False",
                "contract: ingest returns the run id it created; nobody guesses from file timestamps",
            ]),
            True,
        )


def test_ingest_bundle_reports_every_outcome():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        evidence = []

        r, _ = _quiet(ingest.ingest_bundle, root)
        evidence.append(f"empty inbox: {r}")
        assert r == (0, None, "empty")

        _stage(root)
        (code, run_id, outcome), _ = _quiet(ingest.ingest_bundle, root)
        evidence.append(f"first bundle: code={code} run_id={run_id} outcome={outcome}")
        assert (code, outcome) == (0, "ingested")
        assert (root / "Manifest" / f"manifest_{run_id}.json").exists(), "run_id must name the manifest that was written"

        _stage(root)
        r, _ = _quiet(ingest.ingest_bundle, root)
        evidence.append(f"same bundle again: {r}")
        assert r == (0, None, "duplicate")
        assert not (root / "Python_Inbox" / "layer12_bundle.txt").exists(), "a rejected duplicate leaves nothing behind"

        _stage(root, "<LAYER1_OUTPUT>{}</LAYER1_OUTPUT>")  # no LAYER2 section
        r, _ = _quiet(ingest.ingest_bundle, root)
        evidence.append(f"malformed bundle: {r}")
        assert r == (10, None, "invalid")
        assert (root / "Python_Inbox" / "layer12_bundle.txt").exists(), "an invalid bundle is put back so it can be fixed"

        evidence.append(f"inbox after malformed bundle: {sorted(p.name for p in (root / 'Python_Inbox').iterdir())}")
        evidence.append(f"legacy wrapper ingest.ingest() returns only the code: {_quiet(ingest.ingest, root)[0]}")
        evidence.append(f"manifests: {sorted(p.name for p in (root / 'Manifest').glob('manifest_*.json'))}")
        evidence.append(f"written artifacts: {[e['artifact'] for e in _written(root)]}")
        for entry in _written(root):
            evidence.append(f"ledger: {entry['artifact']} run={entry['run_id']} hash={entry['hash'][:12]}")
        assert len(set(e["run_id"] for e in _written(root))) == 1
        record_verification("test_ingest_bundle_reports_every_outcome",
                            "ingest_bundle distinguishes empty, ingested, duplicate and invalid", "\n".join(evidence), True)


def test_duplicate_bundle_halts_pipeline_even_with_a_preset_run_id():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _stage(root)
        first = _ctx(root)
        rc1, _ = _quiet(Pipeline().run, first)
        assert rc1 == 0
        before = [(e["artifact"], e["run_id"], e["hash"]) for e in _written(root)]

        _stage(root)  # identical bundle again, this time with a caller-supplied run_id
        second = _ctx(root, run_id="caller_supplied")
        rc2, out = _quiet(Pipeline().run, second)
        after = [(e["artifact"], e["run_id"], e["hash"]) for e in _written(root)]

        assert rc2 == 10 and second.storage.get("failed_stage") == "Ingest", (rc2, second.storage.get("failed_stage"))
        assert after == before, "a duplicate must not write anything under the caller's run id"
        assert not [e for e in after if e[1] == "caller_supplied"]

        record_verification(
            "test_duplicate_bundle_halts_pipeline_even_with_a_preset_run_id",
            "a duplicate bundle halts at Ingest instead of running over stale artifacts",
            "\n".join([
                f"first run exit code: {rc1}", f"first run id: {first.run_id}",
                f"second run exit code: {rc2}", f"second run failed stage: {second.storage.get('failed_stage')}",
                f"ledger entries before: {len(before)}", f"ledger entries after: {len(after)}",
                f"entries under caller_supplied: {len([e for e in after if e[1] == 'caller_supplied'])}",
                f"artifacts: {sorted(set(a for a, _, _ in after))}",
            ] + [f"second run log: {line}" for line in out.strip().splitlines()[:6]]),
            True,
        )


def test_racing_pipelines_never_share_a_run():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _stage(root)
        results = {}
        barrier = threading.Barrier(2)

        def worker(name):
            ctx = _ctx(root)
            barrier.wait()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = Pipeline().run(ctx)
            results[name] = (rc, ctx.run_id, ctx.storage.get("failed_stage"))

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        codes = sorted(r[0] for r in results.values())
        winners = [r for r in results.values() if r[0] == 0]
        losers = [r for r in results.values() if r[0] != 0]
        assert codes == [0, 10], results
        assert losers[0][1] is None and losers[0][2] == "Ingest", "the loser halts at Ingest with no run id"
        run_ids = {e["run_id"] for e in _written(root)}
        assert run_ids == {winners[0][1]}, f"only the winner's run may appear in the ledger: {run_ids}"

        record_verification(
            "test_racing_pipelines_never_share_a_run",
            "two pipelines racing for one bundle: exactly one runs, the other halts at Ingest",
            "\n".join([
                f"pipeline a: {results['a']}", f"pipeline b: {results['b']}", f"exit codes: {codes}",
                f"winner run id: {winners[0][1]}", f"loser run id: {losers[0][1]}",
                f"loser failed stage: {losers[0][2]}", f"run ids in ledger: {sorted(run_ids)}",
                f"written artifacts: {sorted({e['artifact'] for e in _written(root)})}",
                f"manifests: {sorted(p.name for p in (root / 'Manifest').glob('manifest_*.json'))}",
                f"inbox after: {sorted(p.name for p in (root / 'Python_Inbox').iterdir())}",
            ]),
            True,
        )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("PASS test_ingest_handoff")
