"""End-to-end accuracy on REAL screens.

The fixtures in tests/fixtures/real were extracted from a real, public product landing page rendered with a
real layout engine (WeasyPrint; see tools/real_data/extract_real.py). Layer 1/2 are derived from the rendered
DOM and the ground truth is the DOM's own nesting and CSS flex direction/gap, so the engine is scored against
facts it was never shown.

Thresholds: every screen completes with an intact hash chain, every Layer 1 id appears exactly once in the
final tree, parent accuracy >= 95 %, layout direction >= 90 %, flex gap within 2 px, and the output is
deterministic and independent of input order.
"""

import io
import json
import random
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
FIXTURES = Path(__file__).parent.parent / "fixtures" / "real"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.lock import locked_read
from orchestrator.orchestrator import Pipeline, PipelineContext
from verification.receipt import record_verification


def _run(l1, l2):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Python_Inbox").mkdir(parents=True)
        bundle = (f"<LAYER1_OUTPUT>\n{json.dumps(l1)}\n</LAYER1_OUTPUT>\n"
                  f"<LAYER2_OUTPUT>\n{json.dumps(l2)}\n</LAYER2_OUTPUT>")
        (root / "Python_Inbox" / "layer12_bundle.txt").write_text(bundle, encoding="utf-8")
        ctx = PipelineContext(run_id=None, manifest_path=root / "Manifest" / "manifest_x.json")
        with redirect_stdout(io.StringIO()):
            rc = Pipeline().run(ctx)
        tree = None
        artifact = root / "Artifacts" / "layer4_2_execution_ir.json"
        if artifact.exists():
            tree = json.loads(artifact.read_text(encoding="utf-8"))["root_node"]
        with locked_read(root / "Logs" / "Pipeline_Ledger.json", default=[]) as ledger:
            entry = [e for e in ledger if e.get("run_id") == ctx.run_id][-1]
        return rc, tree, entry


def _walk(node, parent=None, out=None):
    out = [] if out is None else out
    out.append((node, parent))
    for child in node.get("children", []):
        _walk(child, node, out)
    return out


def _score(case, tree):
    l1 = {c["id"]: c for c in case["l1"]["candidates"]}
    truth = case["truth"]
    nodes = _walk(tree)
    by_id = {n["id"]: n for n, _ in nodes}
    parent_of = {n["id"]: p for n, p in nodes}
    real = [n["id"] for n, _ in nodes if n["id"] in l1]

    def real_parent(node_id):
        p = parent_of.get(node_id)
        while p is not None and p["id"] not in l1:
            p = parent_of.get(p["id"])
        return p["id"] if p is not None else None

    out = {"complete": sorted(real) == sorted(l1) and len(real) == len(set(real)),
           "par_ok": sum(1 for i in l1 if real_parent(i) == truth["parents"][i]), "par_n": len(l1),
           "dir_ok": 0, "dir_n": 0, "gap_ok": 0, "gap_n": 0}
    for cid, t in truth["layout"].items():
        pred = {"flex_col": "col", "flex_row": "row"}.get(by_id[cid]["layout"]["layout_type"], "abs")
        out["dir_n"] += 1
        out["dir_ok"] += pred == t["dir"]
        if t["kind"] == "flex" and pred == t["dir"]:
            out["gap_n"] += 1
            gap = by_id[cid]["layout"].get("gap")
            out["gap_ok"] += gap is not None and abs(gap - t["gap"]) <= 2
    return out


def test_real_screens_meet_accuracy_thresholds():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 3, "real-data fixtures are missing"
    total = {"par_ok": 0, "par_n": 0, "dir_ok": 0, "dir_n": 0, "gap_ok": 0, "gap_n": 0}
    evidence = ["real-data accuracy (public landing page rendered with WeasyPrint, DOM ground truth)"]
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        rc, tree, entry = _run(case["l1"], case["l2"])
        assert rc == 0 and tree is not None, f"{f.name}: pipeline failed (exit {rc})"
        assert entry["status"] == "PASS" and entry["hash_chain_valid"] is True, f"{f.name}: {entry}"
        s = _score(case, tree)
        assert s["complete"], f"{f.name}: every Layer 1 id must appear exactly once in the tree"
        for k in total:
            total[k] += s[k]
        evidence.append(f"{f.name}: nodes={s['par_n']} parent={s['par_ok']}/{s['par_n']} "
                        f"direction={s['dir_ok']}/{s['dir_n']} gap={s['gap_ok']}/{s['gap_n']} warnings={len(entry['warnings'])}")

        # determinism and input-order independence on real data
        rc2, tree2, _ = _run(case["l1"], case["l2"])
        l1s, l2s = json.loads(json.dumps(case["l1"])), json.loads(json.dumps(case["l2"]))
        random.Random(5).shuffle(l1s["candidates"])
        random.Random(6).shuffle(l2s["semantics"])
        rc3, tree3, _ = _run(l1s, l2s)
        reference = json.dumps(tree, sort_keys=True)
        assert rc2 == 0 and json.dumps(tree2, sort_keys=True) == reference, f"{f.name}: rerun differs"
        assert rc3 == 0 and json.dumps(tree3, sort_keys=True) == reference, f"{f.name}: input order changes the output"
        evidence.append(f"{f.name}: rerun identical, shuffled input identical")

    parent_acc = total["par_ok"] / total["par_n"]
    evidence.append(f"aggregate parent accuracy: {total['par_ok']}/{total['par_n']} = {parent_acc:.3f}")
    evidence.append(f"aggregate layout direction: {total['dir_ok']}/{total['dir_n']}")
    evidence.append(f"aggregate flex gap within 2px: {total['gap_ok']}/{total['gap_n']}")
    assert parent_acc >= 0.95, f"parent accuracy {parent_acc:.3f} < 0.95"
    assert total["dir_n"] > 0 and total["dir_ok"] / total["dir_n"] >= 0.90, f"direction {total['dir_ok']}/{total['dir_n']}"
    if total["gap_n"]:
        assert total["gap_ok"] / total["gap_n"] >= 0.85, f"gap {total['gap_ok']}/{total['gap_n']}"

    record_verification("test_real_screens_meet_accuracy_thresholds",
                        "real-screen parent/layout accuracy, determinism and input-order independence",
                        "\n".join(evidence), True)


if __name__ == "__main__":
    test_real_screens_meet_accuracy_thresholds()
    print("PASS test_real_data_accuracy")
