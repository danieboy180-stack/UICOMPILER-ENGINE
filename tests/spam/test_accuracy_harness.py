"""The accuracy scorer behaves as the spec's 90 % gate needs it to (L1_42_SCOPING_DOC.md, section 6)."""

import copy
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
FIXTURE = Path(__file__).parent.parent / "fixtures" / "real" / "stackra_mobile_p12.json"
for extra in (ROOT, ROOT / "tools" / "eval"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import score_accuracy as sa
from orchestrator.orchestrator import Pipeline, PipelineContext
from verification.receipt import record_verification


def _real_tree():
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Python_Inbox").mkdir()
        (root / "Python_Inbox" / "layer12_bundle.txt").write_text(
            f"<LAYER1_OUTPUT>\n{json.dumps(case['l1'])}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{json.dumps(case['l2'])}\n</LAYER2_OUTPUT>",
            encoding="utf-8")
        ctx = PipelineContext(run_id=None, manifest_path=root / "Manifest" / "manifest_x.json")
        with redirect_stdout(io.StringIO()):
            assert Pipeline().run(ctx) == 0
        return json.loads((root / "Artifacts" / "layer4_2_execution_ir.json").read_text(encoding="utf-8"))["root_node"]


def _nodes(tree):
    out, stack = [], [tree]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(n.get("children", []))
    return out


def test_identical_prediction_scores_perfectly():
    tree = _real_tree()
    r = sa.score_screen(tree, copy.deepcopy(tree))
    assert (r["mean_iou"], r["role_accuracy"], r["recall"], r["precision"], r["parent_accuracy"]) == (1.0, 1.0, 1.0, 1.0, 1.0)
    assert r["labeled"] == 17, "17 real elements; the root and the row/column wrappers are transparent"


def test_jitter_keeps_matches_but_lowers_iou():
    tree = _real_tree()
    jittered = copy.deepcopy(tree)
    for n in _nodes(jittered):
        n["bbox"]["x"] += 1  # 1 px: the fixture has three near-identical overlapping avatar boxes, where a 2 px shift
        n["bbox"]["y"] -= 1  # makes a neighbour the better IoU match (inherent to IoU matching, not a scorer bug)
    r = sa.score_screen(tree, jittered)
    assert r["recall"] == 1.0 and r["role_accuracy"] == 1.0
    assert 0.7 < r["mean_iou"] < 1.0, r["mean_iou"]


def test_missing_nodes_and_wrong_roles_are_counted_exactly():
    tree = _real_tree()
    broken = copy.deepcopy(tree)
    real = [n for n in _nodes(broken) if n["id"] != sa.ROOT_ID and sa.SYNTHETIC_MARK not in n["id"] and not n.get("children")]
    dropped, flipped = real[:2], real[2:5]
    drop_ids = {n["id"] for n in dropped}
    for n in _nodes(broken):
        n["children"] = [c for c in n.get("children", []) if c["id"] not in drop_ids]
    for n in flipped:
        n["type"] = "image" if n["type"] != "image" else "text"
    r = sa.score_screen(tree, broken)
    assert r["labeled"] == 17 and r["matched"] == 15
    assert r["recall"] == 15 / 17 and r["precision"] == 1.0
    assert r["role_accuracy"] == (15 - 3) / 17, "unmatched nodes count as wrong roles"


def test_engine_wrappers_are_transparent_to_the_scorer():
    tree = _real_tree()
    wrapped = copy.deepcopy(tree)
    top = wrapped["children"]
    assert len(top) >= 2
    wrapper = {"id": "extra~g9", "type": "container", "bbox": dict(top[0]["bbox"]), "children": [top[0], top[1]]}
    wrapped["children"] = [wrapper] + top[2:]
    r = sa.score_screen(tree, wrapped)
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["mean_iou"] == 1.0, r


def test_directory_gate_passes_perfect_fails_degraded_and_flags_missing():
    tree = _real_tree()
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        for name in ("labels", "good", "bad", "partial"):
            (base / name).mkdir()
        for i in range(4):
            (base / "labels" / f"screen_{i}.json").write_text(json.dumps({"root_node": tree}), encoding="utf-8")
            (base / "good" / f"screen_{i}.json").write_text(json.dumps({"root_node": tree}), encoding="utf-8")
            degraded = copy.deepcopy(tree)
            for n in [n for n in _nodes(degraded) if n.get("type") == "text"][:5]:
                n["type"] = "image"
            (base / "bad" / f"screen_{i}.json").write_text(json.dumps({"root_node": degraded}), encoding="utf-8")
            if i < 3:
                (base / "partial" / f"screen_{i}.json").write_text(json.dumps({"root_node": tree}), encoding="utf-8")

        def run(preds):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sa.main(["--labels", str(base / "labels"), "--preds", str(base / preds)])
            return rc, buf.getvalue()

        good_rc, good_out = run("good")
        bad_rc, bad_out = run("bad")
        partial_rc, partial_out = run("partial")
        assert good_rc == 0 and "PASS" in good_out
        assert bad_rc == 1 and "FAIL" in bad_out, "5 of 17 wrong roles is far below the 90 % gate"
        assert partial_rc == 1 and "NO PREDICTION" in partial_out, "a screen with no prediction cannot pass"
        assert sa.main(["--labels", str(base / "nothing"), "--preds", str(base / "good")]) == 1

        record_verification(
            "test_directory_gate", "the 90 % gate passes a perfect set, fails a degraded set and flags missing predictions",
            "\n".join([f"good set: exit {good_rc}", f"bad set: exit {bad_rc}", f"partial set: exit {partial_rc}"]
                      + [f"good: {line}" for line in good_out.strip().splitlines()[-3:]]
                      + [f"bad: {line}" for line in bad_out.strip().splitlines()[-3:]]
                      + [f"partial: {line}" for line in partial_out.strip().splitlines()[-3:]]
                      + ["gate: mean IoU >= 0.9 and role accuracy >= 0.9 and no missing predictions"]),
            True,
        )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("PASS test_accuracy_harness")
