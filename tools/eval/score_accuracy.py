"""Score pipeline output against hand-labeled Layer 4.2 JSON (L1_42_SCOPING_DOC.md, section 6).

The spec: a hand-labeled set of screenshots with the expected Layer 4.2 tree for each; CI computes the IoU of the
bounding boxes and the classification accuracy of the semantic roles, and the pipeline is not done until it reaches
90 % on that set. This is the scorer. It needs only two folders of JSON, so it does not care how the predictions
were produced (your VLM pipeline, a test double, anything):

    python tools/eval/score_accuracy.py --labels labels/ --preds preds/ [--min-iou 0.9] [--min-role 0.9]

Files are paired by name. A screen with no prediction scores zero. Row/column wrapper nodes the engine invents
(ids containing "~g") and the root are transparent: they are neither matched nor counted as extra predictions.
Where the spec is silent the definitions are: boxes match one-to-one by IoU >= 0.5 (highest IoU first); a screen's
IoU score is the mean IoU over its labeled nodes (unmatched = 0); role accuracy is correct roles over labeled nodes
(unmatched = wrong).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_ID = "root_container"
SYNTHETIC_MARK = "~g"


def _box(node):
    b = node.get("bbox") or {}
    try:
        x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    iw = min(ax + aw, bx + bw) - max(ax, bx)
    ih = min(ay + ah, by + bh) - max(ay, by)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (aw * ah + bw * bh - inter)


def flatten(tree):
    """Real nodes only: [{id, type, box, parent}], with wrappers and the root made transparent."""
    nodes = []

    def walk(node, real_parent):
        nid = str(node.get("id"))
        transparent = nid == ROOT_ID or SYNTHETIC_MARK in nid
        if not transparent and _box(node):
            nodes.append({"id": nid, "type": node.get("type"), "box": _box(node), "parent": real_parent})
        for child in node.get("children") or []:
            walk(child, real_parent if transparent else nid)

    if isinstance(tree, dict):
        walk(tree, None)
    return nodes


def score_screen(label_tree, pred_tree, iou_threshold=0.5):
    labels, preds = flatten(label_tree), flatten(pred_tree)
    pairs = sorted(((iou(l["box"], p["box"]), li, pi) for li, l in enumerate(labels) for pi, p in enumerate(preds)),
                   key=lambda t: (-t[0], t[1], t[2]))
    match, used = {}, set()
    for score, li, pi in pairs:
        if score < iou_threshold:
            break
        if li not in match and pi not in used:
            match[li] = (pi, score)
            used.add(pi)
    n_l, n_p, n_m = len(labels), len(preds), len(match)
    pred_to_label = {pi: li for li, (pi, _) in match.items()}
    role_ok = sum(1 for li, (pi, _) in match.items() if labels[li]["type"] == preds[pi]["type"])
    parent_ok = parent_n = 0
    for li, (pi, _) in match.items():
        if labels[li]["parent"] is None:
            continue
        parent_n += 1
        pred_parent = next((i for i, p in enumerate(preds) if p["id"] == preds[pi]["parent"]), None)
        parent_ok += pred_parent is not None and pred_to_label.get(pred_parent) is not None and \
            labels[pred_to_label[pred_parent]]["id"] == labels[li]["parent"]
    return {
        "labeled": n_l, "predicted": n_p, "matched": n_m,
        "mean_iou": sum(sc for _, sc in match.values()) / n_l if n_l else 0.0,
        "recall": n_m / n_l if n_l else 0.0, "precision": n_m / n_p if n_p else 0.0,
        "role_accuracy": role_ok / n_l if n_l else 0.0,
        "parent_accuracy": parent_ok / parent_n if parent_n else 1.0,
    }


def _tree(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("root_node", data) if isinstance(data, dict) else {}


def score_directories(labels_dir, preds_dir, iou_threshold=0.5):
    rows = {}
    for label_path in sorted(Path(labels_dir).glob("*.json")):
        pred_path = Path(preds_dir) / label_path.name
        pred_tree = _tree(pred_path) if pred_path.exists() else {}
        rows[label_path.stem] = score_screen(_tree(label_path), pred_tree, iou_threshold)
        rows[label_path.stem]["has_prediction"] = pred_path.exists()
    screens = list(rows.values())
    mean = lambda key: sum(r[key] for r in screens) / len(screens) if screens else 0.0
    return {"screens": rows, "count": len(screens), "mean_iou": mean("mean_iou"), "role_accuracy": mean("role_accuracy"),
            "recall": mean("recall"), "precision": mean("precision"), "parent_accuracy": mean("parent_accuracy"),
            "missing_predictions": sorted(k for k, r in rows.items() if not r["has_prediction"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--preds", required=True, type=Path)
    parser.add_argument("--iou", type=float, default=0.5, help="IoU needed to match a predicted box to a labeled box")
    parser.add_argument("--min-iou", type=float, default=0.9)
    parser.add_argument("--min-role", type=float, default=0.9)
    parser.add_argument("--json", type=Path, help="also write the full report here")
    args = parser.parse_args(argv)

    report = score_directories(args.labels, args.preds, args.iou)
    if not report["count"]:
        print(f"No labeled screens found in {args.labels}")
        return 1
    for name, r in report["screens"].items():
        print(f"{name:32s} nodes={r['labeled']:3d} iou={r['mean_iou']:.3f} role={r['role_accuracy']:.3f} "
              f"recall={r['recall']:.3f} precision={r['precision']:.3f} parent={r['parent_accuracy']:.3f}"
              + ("" if r["has_prediction"] else "  (NO PREDICTION)"))
    ok = report["mean_iou"] >= args.min_iou and report["role_accuracy"] >= args.min_role and not report["missing_predictions"]
    print(f"\n{report['count']} screens: mean IoU {report['mean_iou']:.3f} (min {args.min_iou}), role accuracy "
          f"{report['role_accuracy']:.3f} (min {args.min_role}), recall {report['recall']:.3f}, precision {report['precision']:.3f}")
    print("PASS" if ok else "FAIL")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
