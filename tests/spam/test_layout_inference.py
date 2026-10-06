"""Layer 4.1 layout inference: geometry in, deterministic tree out."""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.layout import ROOT_ID, assign_parents, build_semantic_tree, fits_inside
from orchestrator.orchestrator import _duplicate_ids, _geometry_violations


def _l1(*boxes, width=400, height=300):
    return {
        "image_width": width, "image_height": height,
        "candidates": [{"id": i, "bbox": dict(zip("xywh", b))} for i, b in boxes],
    }


def _find(tree, node_id):
    stack = [tree]
    while stack:
        node = stack.pop()
        if node["id"] == node_id:
            return node
        stack.extend(node["children"])
    raise AssertionError(f"{node_id} not in tree")


def test_card_with_rows_infers_nesting_gap_and_padding():
    l1 = _l1(("card", (10, 10, 380, 200)), ("title", (20, 20, 200, 24)), ("badge", (300, 20, 60, 24)),
             ("b1", (20, 160, 100, 40)), ("b2", (132, 160, 100, 40)))
    tree = build_semantic_tree(["card", "title", "badge", "b1", "b2"], l1)

    card = _find(tree, "card")
    assert card["layout"]["layout_type"] == "flex_col"
    # card spans x 10..390; its widest child (badge) ends at x=360 -> right padding is 30, not 10
    assert card["layout"]["padding"] == {"top": 10, "right": 30, "bottom": 10, "left": 10}
    assert [c["id"] for c in tree["children"]] == ["card"], "everything else lives inside the card"

    top_row, button_row = card["children"]
    assert top_row["layout"]["layout_type"] == "flex_row" and [c["id"] for c in top_row["children"]] == ["title", "badge"]
    assert button_row["layout"]["layout_type"] == "flex_row" and button_row["layout"]["gap"] == 12
    assert [c["id"] for c in button_row["children"]] == ["b1", "b2"]
    assert tree["layout"]["padding"]["left"] == 10 and tree["layout"]["padding"]["top"] == 10


def test_overlap_without_a_clean_cut_falls_back_to_absolute():
    l1 = _l1(("a", (10, 10, 100, 100)), ("b", (60, 60, 100, 100)))
    tree = build_semantic_tree(["a", "b"], l1)
    assert tree["layout"]["layout_type"] == "absolute"
    assert [c["id"] for c in tree["children"]] == ["a", "b"]


def test_parenting_same_box_siblings_overflow_and_strangers():
    items = [("outer", (0, 0, 100, 100)), ("inner", (10, 10, 20, 20)),
             ("twin_a", (50, 50, 20, 20)), ("twin_b", (50, 50, 20, 20)),
             ("overflow", (85, 40, 30, 20)), ("far", (300, 300, 10, 10))]
    parents = assign_parents(items)
    assert parents["inner"] == "outer"
    assert parents["twin_a"] == "outer" and parents["twin_b"] == "outer", "identical boxes are siblings, never nested"
    assert parents["overflow"] == "outer", "content overflowing its container by a few px still belongs to it"
    assert parents["far"] is None
    assert fits_inside((0, 0, 100, 100), (10, 10, 20, 20)) and not fits_inside((0, 0, 100, 100), (300, 300, 10, 10))


def test_result_does_not_depend_on_input_order():
    boxes = [("c", (0, 0, 300, 200)), ("t", (10, 10, 100, 20)), ("p", (10, 40, 200, 60)),
             ("x", (10, 120, 80, 30)), ("y", (100, 120, 80, 30)), ("z", (190, 120, 80, 30))]
    reference = json.dumps(build_semantic_tree([i for i, _ in boxes], _l1(*boxes)), sort_keys=True)
    for seed in range(8):
        shuffled = boxes[:]
        random.Random(seed).shuffle(shuffled)
        ids = [i for i, _ in shuffled]
        assert json.dumps(build_semantic_tree(ids, _l1(*shuffled)), sort_keys=True) == reference


def test_every_visual_appears_exactly_once_even_without_boxes():
    l1 = _l1(("a", (0, 0, 50, 50)))
    tree = build_semantic_tree(["a", "ghost"], l1)
    ids = []
    stack = [tree]
    while stack:
        node = stack.pop()
        ids.append(node["id"])
        stack.extend(node["children"])
    assert sorted(ids) == sorted([ROOT_ID, "a", "ghost"])
    flat = build_semantic_tree(["a", "b"], {})
    assert [c["id"] for c in flat["children"]] == ["a", "b"], "no boxes at all degrades to a flat deterministic list"


def test_validators_reject_duplicate_ids_and_bad_geometry():
    good = {"id": "r", "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
            "children": [{"id": "c", "bbox": {"x": 10, "y": 10, "w": 20, "h": 20}, "children": []}]}
    assert _duplicate_ids(good) == [] and _geometry_violations(good) == []

    dup = {"id": "r", "children": [{"id": "c", "children": []}, {"id": "c", "children": []}]}
    assert _duplicate_ids(dup) == ["c"]

    bad = {"id": "r", "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
           "children": [{"id": "far", "bbox": {"x": 500, "y": 500, "w": 20, "h": 20}, "children": []}]}
    assert _geometry_violations(bad) == ["far not inside r"]

    zero_parent = {"id": "r", "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                   "children": [{"id": "c", "bbox": {"x": 5, "y": 5, "w": 5, "h": 5}, "children": []}]}
    assert _geometry_violations(zero_parent) == [], "unknown (zero) parent boxes are skipped, not failed"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("PASS test_layout_inference")
