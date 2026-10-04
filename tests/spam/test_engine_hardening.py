"""
Engine-hardening regression tests.

Each test pins a defect that was reproduced end-to-end before it was fixed:

  1. weak_child false tamper: ConfidencePropagation rewrote the frozen Layer 2 artifact on
     disk, so MetaVerify rejected (exit 30) every run containing a candidate with
     confidence < 0.8.
  2. MetaVerify failed open: a truncated or emptied Artifact_Ledger.json read as "no artifacts"
     and the integrity check passed vacuously.
  3. Pre-rollback hash validation only compared the LAST ledger entry with the manifest.
  4. A MetaVerify failure (tampered artifact) still reported hash_chain_valid=True and
     compiler_integrity: PASS.
  5. Re-ingesting an already processed bundle raised NameError (safe_unlink was never imported
     on the duplicate-rejection path) and leaked a .processing temp file in Python_Inbox.
"""

import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
FIXTURES = Path(__file__).parent.parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.lock import locked_read
from orchestrator.orchestrator import MetaVerifyStage, Pipeline, PipelineContext
from verification.receipt import record_verification

WEAK_ID = "lbl-003"


def _load_fixtures(weak_confidence=None):
    l1 = json.loads((FIXTURES / "golden_l1_ccir.json").read_text(encoding="utf-8"))
    l2 = json.loads((FIXTURES / "golden_l2_csir.json").read_text(encoding="utf-8"))
    if weak_confidence is not None:
        for cand in l1["candidates"]:
            if cand["id"] == WEAK_ID:
                cand["confidence"] = weak_confidence
    return l1, l2


def _stage_inbox(root: Path, weak_confidence=None) -> None:
    l1, l2 = _load_fixtures(weak_confidence)
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    bundle = (
        f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n"
        f"<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>"
    )
    (inbox / "layer12_bundle.txt").write_text(bundle, encoding="utf-8")


def _run_pipeline(root: Path, storage=None):
    ctx = PipelineContext(
        run_id=None,
        manifest_path=root / "Manifest" / "manifest_x.json",
        storage=dict(storage or {}),
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = Pipeline().run(ctx)
    return rc, ctx, buf.getvalue()


def _pipeline_ledger_entry(root: Path, run_id: str) -> dict:
    with locked_read(root / "Logs" / "Pipeline_Ledger.json", default=[]) as ledger:
        entries = [e for e in ledger if isinstance(e, dict) and e.get("run_id") == run_id]
    assert entries, f"no Pipeline_Ledger entry for run {run_id}"
    return entries[-1]


def _ingest_only(root: Path) -> PipelineContext:
    _stage_inbox(root)
    res = subprocess.run(
        [sys.executable, str(ROOT / "ingest.py"), "--root", str(root)],
        text=True, capture_output=True, check=False,
    )
    assert res.returncode == 0, f"ingest failed ({res.returncode}): {res.stdout}\n{res.stderr}"
    manifests = sorted((root / "Manifest").glob("manifest_*.json"))
    assert len(manifests) == 1, f"expected exactly one manifest, got {manifests}"
    with locked_read(manifests[0]) as manifest:
        run_id = manifest["run_id"]
    return PipelineContext(run_id=run_id, manifest_path=manifests[0])


def _meta_verify(ctx: PipelineContext):
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = MetaVerifyStage().run(ctx)
    return ok, ctx.storage.get("meta_verify_code"), buf.getvalue()


def test_weak_child_run_passes_and_layer2_stays_frozen():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _stage_inbox(root, weak_confidence=0.5)
        rc, ctx, out = _run_pipeline(root)

        assert rc == 0, (
            f"a low-confidence candidate must not fail the pipeline "
            f"(exit {rc}, failed_stage={ctx.storage.get('failed_stage')})"
        )

        with locked_read(root / "Artifacts" / "layer2_csir.json") as l2_disk:
            assert "weak_child" not in json.dumps(l2_disk), (
                "the frozen Layer 2 artifact must never be rewritten with annotations"
            )

        assert ctx.storage.get("weak_children") == [WEAK_ID]
        flagged = [s for s in ctx.storage["l2_data"]["semantics"] if s["id"] == WEAK_ID]
        assert flagged and flagged[0].get("weak_child") is True

        entry = _pipeline_ledger_entry(root, ctx.run_id)
        assert entry["status"] == "PASS"
        assert entry["hash_chain_valid"] is True
        assert any(WEAK_ID in w for w in entry["warnings"]), "weak_child must surface as a warning"

        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            written = {
                e["artifact"] for e in ledger
                if e["run_id"] == ctx.run_id and e["status"] == "written"
            }
        assert entry["files_produced"] == len(written) > 3, (
            f"files_produced={entry['files_produced']} but ledger shows {len(written)} written artifacts"
        )

        evidence = (
            f"exit code: {rc}\nweak children: {ctx.storage['weak_children']}\n"
            f"warnings: {entry['warnings']}\nartifacts written: {sorted(written)}\n" + out
        )
        record_verification(
            "test_weak_child_run_passes_and_layer2_stays_frozen",
            "weak_child no longer trips MetaVerify; Layer 2 artifact stays byte-frozen",
            evidence, True,
        )


def test_meta_verify_fails_closed_on_unreadable_or_emptied_ledger():
    cases = [
        ("control: untouched ledger", None, True, 0),
        ("ledger truncated to invalid JSON", "{garbage", False, 30),
        ("ledger wiped to an empty list", "[]", False, 30),
    ]
    evidence = [
        "MetaVerify fail-closed regression",
        f"cases: {len(cases)}",
        "contract: unreadable or emptied ledger => failure code 30, never a vacuous pass",
    ]
    for label, replacement, expect_ok, expect_code in cases:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ctx = _ingest_only(root)
            if replacement is not None:
                (root / "Artifacts" / "Artifact_Ledger.json").write_text(replacement, encoding="utf-8")
            ok, code, out = _meta_verify(ctx)
            evidence.append(f"{label}: ok={ok} code={code}")
            evidence.extend(f"    {line}" for line in out.strip().splitlines())
            assert ok is expect_ok and code == expect_code, (
                f"{label}: expected ok={expect_ok} code={expect_code}, got ok={ok} code={code}"
            )
    record_verification(
        "test_meta_verify_fails_closed",
        "MetaVerify fails closed on a corrupt or emptied ledger",
        "\n".join(evidence), True,
    )


def test_prerollback_hash_check_covers_every_artifact():
    from orchestrator.ledger import (
        count_written_artifacts,
        verify_manifest_ledger_consistency,
        write_atomic_artifact,
    )

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        run_id = "hardening_run"
        manifest_path = root / "Manifest" / f"manifest_{run_id}.json"
        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"

        parent = None
        for name, payload in (("alpha", {"n": 1}), ("beta", {"n": 2}), ("gamma", {"n": 3})):
            write_atomic_artifact(root, name, run_id, payload, parent)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            parent = manifest["artifacts"][name]["hash"]

        evidence = ["pre-rollback hash validation regression"]
        for entry in json.loads(ledger_path.read_text(encoding="utf-8")):
            evidence.append(
                f"ledger: {entry['artifact']} hash={entry['hash'][:12]} "
                f"parent={(entry['parent_hash'] or 'none')[:12]} status={entry['status']}"
            )

        ok, problems = verify_manifest_ledger_consistency(manifest_path, ledger_path, run_id)
        evidence.append(f"consistent state: ok={ok} problems={problems}")
        assert ok and problems == []
        written = count_written_artifacts(ledger_path, run_id)
        evidence.append(f"distinct written artifacts: {written}")
        assert written == 3

        # Tamper with the FIRST artifact only. The old check looked at the last entry, so it
        # reported this state as valid.
        good_manifest = manifest_path.read_text(encoding="utf-8")
        tampered = json.loads(good_manifest)
        tampered["artifacts"]["alpha"]["hash"] = "0" * 64
        manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
        ok, problems = verify_manifest_ledger_consistency(manifest_path, ledger_path, run_id)
        evidence.append(f"first artifact tampered: ok={ok} problems={problems}")
        assert not ok and any("alpha" in p for p in problems)

        # An artifact present in the manifest but missing from the ledger is also a break.
        orphaned = json.loads(good_manifest)
        orphaned["artifacts"]["delta"] = {"hash": "1" * 64, "run_id": run_id, "version": 1}
        manifest_path.write_text(json.dumps(orphaned), encoding="utf-8")
        ok, problems = verify_manifest_ledger_consistency(manifest_path, ledger_path, run_id)
        evidence.append(f"manifest-only artifact: ok={ok} problems={problems}")
        assert not ok and any("delta" in p for p in problems)

        # A corrupt ledger must be reported as inconsistent, not as empty-and-fine.
        manifest_path.write_text(good_manifest, encoding="utf-8")
        ledger_path.write_text("{garbage", encoding="utf-8")
        ok, problems = verify_manifest_ledger_consistency(manifest_path, ledger_path, run_id)
        evidence.append(f"corrupt ledger: ok={ok} problems={problems}")
        assert not ok
        written_after = count_written_artifacts(ledger_path, run_id)
        evidence.append(f"written artifacts counted from corrupt ledger: {written_after}")
        assert written_after == 0

        # Nothing on disk yet means nothing to compare.
        ok, problems = verify_manifest_ledger_consistency(root / "none_a.json", root / "none_b.json", run_id)
        evidence.append(f"no files yet: ok={ok} problems={problems}")
        assert ok

        record_verification(
            "test_prerollback_hash_check_covers_every_artifact",
            "manifest/ledger consistency is checked for every artifact and fails closed",
            "\n".join(evidence), True,
        )


def test_meta_verify_failure_marks_hash_chain_invalid():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _stage_inbox(root)
        rc, ctx, out = _run_pipeline(root, {"force_meta_verify_tamper": True})

        assert rc == 30, f"tampered artifact must exit 30, got {rc}"
        assert ctx.storage.get("failed_stage") == "MetaVerify"

        entry = _pipeline_ledger_entry(root, ctx.run_id)
        assert entry["status"] == "FAIL"
        assert entry["hash_chain_valid"] is False

        report = (root / "BUILD_REPORT.md").read_text(encoding="utf-8")
        assert "compiler_integrity**: FAIL" in report, "BUILD_REPORT must not claim integrity PASS"

        record_verification(
            "test_meta_verify_failure_marks_hash_chain_invalid",
            "a MetaVerify failure marks the hash chain invalid and the report FAIL",
            f"exit code: {rc}\nledger entry: {entry}\n" + out, True,
        )


def test_duplicate_bundle_is_rejected_cleanly_without_leaking_temp_files():
    import ingest

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        evidence = ["duplicate-bundle ingest regression"]

        _stage_inbox(root)
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            rc1 = ingest.ingest(root)
        evidence.append(f"first ingest: rc={rc1}")
        evidence.extend(f"    {line}" for line in buf1.getvalue().strip().splitlines())
        assert rc1 == ingest.EXIT_OK
        manifests_before = sorted(p.name for p in (root / "Manifest").glob("manifest_*.json"))
        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            entries_before = len(ledger)
        evidence.append(f"manifests after first ingest: {manifests_before}")
        evidence.append(f"ledger entries after first ingest: {entries_before}")
        evidence.append(f"inbox contents after first ingest: {sorted(p.name for p in (root / 'Python_Inbox').iterdir())}")
        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            for entry in ledger:
                evidence.append(
                    f"ledger: {entry['artifact']} status={entry['status']} hash={entry['hash'][:12]}"
                )
        assert len(manifests_before) == 1 and entries_before >= 2

        _stage_inbox(root)  # the very same bundle again
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = ingest.ingest(root)
        evidence.append(f"second ingest (duplicate): rc={rc2}")
        evidence.extend(f"    {line}" for line in buf2.getvalue().strip().splitlines())
        assert rc2 == ingest.EXIT_OK, "a duplicate bundle is rejected, not treated as an error"
        assert "Duplicate bundle" in buf2.getvalue()

        leftovers = sorted(p.name for p in (root / "Python_Inbox").iterdir())
        evidence.append(f"inbox contents after duplicate: {leftovers}")
        assert leftovers == [], f"a rejected duplicate must not leave files behind: {leftovers}"

        manifests_after = sorted(p.name for p in (root / "Manifest").glob("manifest_*.json"))
        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            entries_after = len(ledger)
        evidence.append(f"manifests after duplicate: {manifests_after}")
        evidence.append(f"ledger entries after duplicate: {entries_after}")
        assert manifests_after == manifests_before and entries_after == entries_before

        record_verification(
            "test_duplicate_bundle_is_rejected_cleanly",
            "duplicate bundles are rejected without a crash, a new run, or a leaked temp file",
            "\n".join(evidence), True,
        )


if __name__ == "__main__":
    test_weak_child_run_passes_and_layer2_stays_frozen()
    test_meta_verify_fails_closed_on_unreadable_or_emptied_ledger()
    test_prerollback_hash_check_covers_every_artifact()
    test_meta_verify_failure_marks_hash_chain_invalid()
    test_duplicate_bundle_is_rejected_cleanly_without_leaking_temp_files()
    print("PASS test_engine_hardening")
