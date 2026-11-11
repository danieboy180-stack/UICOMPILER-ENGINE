"""uic.pipeline.StrictPipeline: fail-closed behavior, contract stage, force_* guard, honest stubs."""
import hashlib
import json
from pathlib import Path

import ingest
from orchestrator.orchestrator import Pipeline, PipelineContext
from uic import export, fixtures as fx, seal
from uic.cli import EXIT_PIPELINE, run_lane
from uic.pipeline import StrictPipeline


def _ingest_l12(root: Path, s: dict) -> str:
    (root / "Python_Inbox").mkdir(parents=True, exist_ok=True)
    sealed, _ = seal.seal12(fx.text12(s), hashlib.sha256(b"src").hexdigest(), "proj")
    (root / "Python_Inbox" / "layer12_bundle.txt").write_text(sealed, encoding="utf-8")
    code, run_id, outcome = ingest.ingest_bundle(root)
    assert (code, outcome) == (0, "ingested")
    return run_id


def _ctx(root: Path, run_id: str) -> PipelineContext:
    return PipelineContext(run_id=run_id, manifest_path=root / "Manifest" / f"manifest_{run_id}.json")


def test_strict_pipeline_keeps_stage_count_order_and_names():
    base, strict = Pipeline(), StrictPipeline()
    assert [s.name for s in strict.stages] == [s.name for s in base.stages]
    assert len(strict.stages) == len(base.stages)
    assert strict.ingest_stage is strict.stages[0] and strict.layer3_stage in strict.stages


def test_strict_refuses_to_synthesize_layers_when_layer342_was_never_ingested(tmp_path):
    run_id = _ingest_l12(tmp_path, fx.build_sample())
    ctx = _ctx(tmp_path, run_id)
    rc = StrictPipeline().run(ctx)
    assert rc != 0 and ctx.storage["failed_stage"] == "Layer3"
    assert ctx.storage["pipeline_status"] == "ROLLED_BACK"
    assert not (tmp_path / "Archives").exists() or not list((tmp_path / "Archives").glob("Run_*"))


def test_force_hooks_are_refused_in_strict_runs(tmp_path):
    run_id = _ingest_l12(tmp_path, fx.build_sample())
    ctx = _ctx(tmp_path, run_id)
    ctx.storage["force_layer5_crash"] = True
    rc = StrictPipeline().run(ctx)
    assert rc != 0 and ctx.storage["failed_stage"] == "Ingest"


def test_unimplemented_layers_say_so_instead_of_claiming_completed(tmp_path):
    assert fx.run_sample(tmp_path, fx.build_sample(), "proj", b"shot") == 0
    run_dir = next((tmp_path / "Archives").glob("Run_*"))
    for name in ("layer4_3", "layer5", "layer6"):
        payload = json.loads((run_dir / "Artifacts" / f"{name}.json").read_text())
        assert payload.get("stub") is True and payload["payload"] == "not_implemented"
    assert "completed" not in json.dumps([json.loads(p.read_text()) for p in (run_dir / "Artifacts").glob("layer[456]*.json")])


def test_contract_stage_fails_a_hash_valid_but_rule_breaking_run_and_nothing_is_archived(tmp_path):
    s = fx.build_sample()
    s["l2"]["regions"][0]["children"].pop()  # Layer 2 silently drops a Layer 1 object (L2.SURVIVE)
    work = tmp_path / "_in"
    work.mkdir()
    (work / "src.png").write_bytes(b"bad-run")
    (work / "l12.txt").write_text(fx.text12(s), encoding="utf-8")
    (work / "l342.txt").write_text(fx.text342(s), encoding="utf-8")
    rc = run_lane(tmp_path, work / "src.png", work / "l12.txt", work / "l342.txt", "proj", None, force_lint=True)
    assert rc == EXIT_PIPELINE
    assert export.list_runs(tmp_path) == []
    log = "".join(p.read_text(errors="ignore") for p in (tmp_path / "Logs").glob("*"))
    assert "L2.SURVIVE" in log


def test_contract_stage_passes_the_conformant_sample_and_archives_it(tmp_path):
    assert fx.run_sample(tmp_path, fx.build_sample(), "proj", b"good-run") == 0
    assert len(export.list_runs(tmp_path)) == 1


def test_export_source_ref_is_posix_and_run_export_refuses_existing_dataset_dir(tmp_path):
    assert fx.run_sample(tmp_path, fx.build_sample(), "proj", b"posix-run") == 0
    row, why = export.build_row(tmp_path, export.list_runs(tmp_path)[0])
    assert why == [] and "\\" not in row.source_ref and row.source_ref.startswith("Images/")
    out = tmp_path / "Datasets" / "taken"
    out.mkdir(parents=True)
    (out / "x").write_text("occupied")
    work = tmp_path / "_in2"
    work.mkdir()
    s = fx.build_sample([dict(role="button", x=40, y=40, w=30, h=9, text="Other", conf=0.9)], project="other")
    (work / "src.png").write_bytes(b"second-run")
    (work / "l12.txt").write_text(fx.text12(s), encoding="utf-8")
    (work / "l342.txt").write_text(fx.text342(s), encoding="utf-8")
    assert run_lane(tmp_path, work / "src.png", work / "l12.txt", work / "l342.txt", "other", out) != 0
    assert (out / "x").read_text() == "occupied"
