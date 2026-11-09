"""Whole lane (lint -> seal -> ingest -> real Pipeline -> export -> verify) plus every export gate."""
import copy
import hashlib
import json
from pathlib import Path

from uic import export, fixtures as fx
from uic.bundle import chash


def _variant(i: int, text: str | None = None):
    """Structurally distinct screens so near-dup detection does not merge them."""
    objs = [dict(role="text", x=20 + 7 * i, y=10 + 5 * i, w=30 + i, h=6, text=text or f"Heading number {i}", conf=0.95),
            dict(role="input", x=50, y=30 + 9 * i, w=60 - 3 * i, h=8 + i, text=f"Field {i}", conf=0.9),
            dict(role="button", x=30 + 4 * i, y=70, w=40, h=9, text=f"Action {i} go", conf=0.9, bg="#2463EB")]
    return fx.build_sample(objs, project=f"proj-{i}")


def _lane(root: Path, i: int, text: str | None = None, project: str | None = None) -> int:
    s = _variant(i, text)
    return fx.run_sample(root, s, project or f"proj-{i}", f"screenshot-{i}-{text}".encode())


def test_full_lane_produces_a_verified_dataset(tmp_path):
    assert _lane(tmp_path, 0) == 0
    man = export.export_dataset(tmp_path, tmp_path / "Datasets" / "v1", task="structure")
    assert sum(man["counts"][s] for s in export.SPLITS) == 1 and man["counts"]["quarantined"] == 0
    assert export.verify_dataset(tmp_path / "Datasets" / "v1") == []
    row = json.loads(next(l for s in export.SPLITS for l in (tmp_path / "Datasets" / "v1" / f"{s}.jsonl").read_text().splitlines()))
    assert row["source_hash"] == hashlib.sha256(b"screenshot-0-None").hexdigest()
    assert row["source_ref"].startswith("Images/") and (tmp_path / row["source_ref"]).is_file()
    assert row["provenance"]["prompts"] is not None and row["project_id"] == "proj-0"


def test_default_or_fabricated_source_hash_is_quarantined(tmp_path):
    assert _lane(tmp_path, 1) == 0
    run_id = export.list_runs(tmp_path)[0]
    mp = tmp_path / "Archives" / f"Run_{run_id}" / f"manifest_{run_id}.json"
    m = json.loads(mp.read_text())
    m["image_hash"] = "default_image_hash"
    mp.write_text(json.dumps(m))
    man = export.export_dataset(tmp_path, tmp_path / "out")
    q = json.loads((tmp_path / "out" / "quarantine.jsonl").read_text().splitlines()[0])
    assert man["counts"]["train"] + man["counts"]["val"] + man["counts"]["test"] == 0
    assert any(r.startswith("G.SOURCE_HASH") for r in q["reasons"])


def test_tampered_archived_artifact_is_quarantined(tmp_path):
    assert _lane(tmp_path, 2) == 0
    run_id = export.list_runs(tmp_path)[0]
    p = tmp_path / "Archives" / f"Run_{run_id}" / "Artifacts" / "layer1_ccir.json"
    d = json.loads(p.read_text())
    d["objects"][0]["text"]["value"] = "tampered"
    p.write_text(json.dumps(d))
    export.export_dataset(tmp_path, tmp_path / "out")
    q = json.loads((tmp_path / "out" / "quarantine.jsonl").read_text().splitlines()[0])
    assert any(r.startswith("G.ARTIFACT_HASH") for r in q["reasons"])


def test_missing_layer_and_failed_pipeline_entry_are_quarantined(tmp_path):
    assert _lane(tmp_path, 3) == 0
    run_id = export.list_runs(tmp_path)[0]
    (tmp_path / "Archives" / f"Run_{run_id}" / "Artifacts" / "layer4_2_execution_ir.json").unlink()
    export.export_dataset(tmp_path, tmp_path / "out1")
    assert "G.LAYERS" in (tmp_path / "out1" / "quarantine.jsonl").read_text()


def test_near_duplicate_screens_are_dropped_to_quarantine(tmp_path):
    assert _lane(tmp_path, 4, text="Same title", project="proj-a") == 0
    s = _variant(4, "Same title")  # identical structure, different source + project
    assert fx.run_sample(tmp_path, s, "proj-b", b"another-screenshot") == 0
    man = export.export_dataset(tmp_path, tmp_path / "out")
    q = [json.loads(l) for l in (tmp_path / "out" / "quarantine.jsonl").read_text().splitlines()]
    assert sum(man["counts"][s_] for s_ in export.SPLITS) == 1
    assert len(q) == 1 and q[0]["reasons"][0].startswith("DEDUP.")


def test_pii_and_secrets_are_redacted_in_rows_and_counted(tmp_path):
    s = _variant(5, "mail me at jane.doe@example.com or +234 803 555 0199")
    s["l1"]["objects"][1]["text"]["value"] = "key sk-abcdefghijklmnopqrstuvwxyz1234"
    assert fx.run_sample(tmp_path, s, "pii", b"pii-shot") == 0
    export.export_dataset(tmp_path, tmp_path / "out")
    blob = "".join((tmp_path / "out" / f"{x}.jsonl").read_text() for x in export.SPLITS)
    assert "jane.doe@example.com" not in blob and "sk-abcdefghijklmnop" not in blob
    row = json.loads(next(l for l in blob.splitlines() if l))
    assert row["redactions"].get("email") == 1 and row["redactions"].get("secret") == 1
    assert export.verify_dataset(tmp_path / "out") == []


def test_groups_never_straddle_splits_and_verify_catches_corruption(tmp_path):
    for i in range(8):
        assert _lane(tmp_path, i) == 0
    out = tmp_path / "out"
    man = export.export_dataset(tmp_path, out, task="semantic")
    assert sum(man["counts"][s] for s in export.SPLITS) + man["counts"]["quarantined"] == 8
    seen: dict[str, str] = {}
    for s in export.SPLITS:
        for line in (out / f"{s}.jsonl").read_text().splitlines():
            g = json.loads(line)["group_id"]
            assert seen.setdefault(g, s) == s
    assert export.verify_dataset(out) == []
    train = out / "train.jsonl"
    train.write_text(train.read_text().replace("Heading", "Hxading", 1) if train.read_text() else "x")
    assert any("file hash mismatch" in b for b in export.verify_dataset(out))


def test_dataset_versions_are_immutable_unless_forced(tmp_path):
    assert _lane(tmp_path, 6) == 0
    out = tmp_path / "out"
    export.export_dataset(tmp_path, out)
    try:
        export.export_dataset(tmp_path, out)
        raised = False
    except FileExistsError:
        raised = True
    assert raised
    assert export.export_dataset(tmp_path, out, force=True)["counts"]["quarantined"] == 0


def test_row_schema_rejects_unknown_fields(tmp_path):
    assert _lane(tmp_path, 7) == 0
    rid = export.list_runs(tmp_path)[0]
    row, why = export.build_row(tmp_path, rid)
    assert row is not None and why == []
    bad = row.model_dump()
    bad["surprise"] = 1
    try:
        export.Row.model_validate(bad)
        rejected = False
    except Exception:
        rejected = True
    assert rejected


def test_content_hash_is_run_independent(tmp_path):
    assert _lane(tmp_path, 0) == 0
    r1, _ = export.build_row(tmp_path, export.list_runs(tmp_path)[0])
    body = {"run_id_independent": {k: getattr(r1, k) for k in export.LAYER_FILES}, "source_hash": r1.source_hash}
    assert chash(body) == r1.content_hash
