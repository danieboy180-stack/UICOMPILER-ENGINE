"""Deterministic Layer 4.1 layout inference (no AI).

Implements the Layer 4.1 decision from L1_42_SCOPING_DOC.md:

* parent/child relationships come from geometry: box B inside box A -> B is a child of A;
* v1 layouts are vertical flex stacks with nested horizontal rows (recursive XY-cut);
* anything that overlaps or does not align cleanly falls back to absolute positioning.

Everything here is a pure function of the input boxes. The result does not depend on the
order the boxes arrive in, so identical input always yields a byte-identical tree.
"""

from statistics import median
from typing import Any, Dict, List, Optional, Tuple

ROOT_ID = "root_container"

TOL = 2             # px of slack for adjacency and alignment decisions
CONTAIN_TOL = 2     # a child may stick out of its parent by this many px and still be contained
SAME_BOX_TOL = 1    # boxes within this many px on every edge are the same box: siblings, never nested
OVERLAP_FRAC = 0.2  # overlap along an axis tolerated up to this fraction of the smaller extent ...
OVERLAP_MAX = 6     # ... but never more than this many px

Box = Tuple[int, int, int, int]  # x, y, w, h
Unit = Tuple[Dict[str, Any], Box]  # (already built tree node, its bounding box)


def parse_bbox(raw: Any) -> Optional[Box]:
    """Return an integer (x, y, w, h) box, or None if ``raw`` is not a usable box."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y, w, h = (round(float(raw[k])) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def union_box(boxes: List[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _area(b: Box) -> int:
    return b[2] * b[3]


def _contains(outer: Box, inner: Box) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    if (abs(ox - ix) <= SAME_BOX_TOL and abs(oy - iy) <= SAME_BOX_TOL
            and abs(ow - iw) <= SAME_BOX_TOL and abs(oh - ih) <= SAME_BOX_TOL):
        return False
    return (ix >= ox - CONTAIN_TOL and iy >= oy - CONTAIN_TOL
            and ix + iw <= ox + ow + CONTAIN_TOL and iy + ih <= oy + oh + CONTAIN_TOL)


def _same_box(a: Box, b: Box) -> bool:
    return all(abs(p - q) <= SAME_BOX_TOL for p, q in zip(a, b))


def _mostly_inside(outer: Box, inner: Box) -> bool:
    """Overflow tolerance: the centre of ``inner`` is inside ``outer`` and at least half of it overlaps."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    if _same_box(outer, inner):
        return False
    if not (ox <= ix + iw / 2 <= ox + ow and oy <= iy + ih / 2 <= oy + oh):
        return False
    overlap_w = min(ox + ow, ix + iw) - max(ox, ix)
    overlap_h = min(oy + oh, iy + ih) - max(oy, iy)
    return overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h * 2 >= iw * ih


def fits_inside(outer: Box, inner: Box) -> bool:
    """The single definition of "child belongs in parent", shared by inference and validation:
    identical boxes, strict containment (within ``CONTAIN_TOL``), or mostly inside (overflow)."""
    return _same_box(outer, inner) or _contains(outer, inner) or _mostly_inside(outer, inner)


def assign_parents(items: List[Tuple[str, Box]]) -> Dict[str, Optional[str]]:
    """Smallest enclosing box wins. A parent always sorts before its children (larger area first),
    so the result is a forest and cannot contain cycles.

    Strict containment (spec: "Box B is inside Box A") is tried first. Real screens routinely have
    content that overflows its container by a few pixels, so when nothing strictly contains a box
    the smallest box that holds its centre and at least half of its area is used instead."""
    order = sorted(items, key=lambda it: (-_area(it[1]), it[1][1], it[1][0], it[0]))
    parents: Dict[str, Optional[str]] = {}
    for i, (nid, box) in enumerate(order):
        parent = None
        for test in (_contains, _mostly_inside):
            for j in range(i - 1, -1, -1):
                if test(order[j][1], box):
                    parent = order[j][0]
                    break
            if parent is not None:
                break
        parents[nid] = parent
    return parents


def _bands(items: List[Tuple[str, Box]], axis: int) -> List[List[Tuple[str, Box]]]:
    """Split items into bands along one axis (0: stacked top-to-bottom, 1: side by side)."""
    lo_i, len_i = (1, 3) if axis == 0 else (0, 2)
    order = sorted(items, key=lambda it: (it[1][lo_i], it[1][lo_i] + it[1][len_i], it[0]))
    first = order[0][1]
    bands = [[order[0]]]
    band_lo, band_hi = first[lo_i], first[lo_i] + first[len_i]
    for it in order[1:]:
        s = it[1][lo_i]
        e = s + it[1][len_i]
        slack = max(TOL, min(OVERLAP_MAX, OVERLAP_FRAC * min(e - s, band_hi - band_lo)))
        if band_hi - s <= slack:
            bands.append([it])
            band_lo, band_hi = s, e
        else:
            bands[-1].append(it)
            band_hi = max(band_hi, e)
    return bands


def _arrange(items: List[Tuple[str, Box]]) -> Tuple[str, Optional[List[List[Tuple[str, Box]]]]]:
    if len(items) < 2:
        return "flex_col", [items]
    for axis, kind in ((0, "flex_col"), (1, "flex_row")):
        bands = _bands(items, axis)
        if len(bands) > 1:
            return kind, bands
    return "absolute", None


def _align(kind: str, boxes: List[Box]) -> str:
    if len(boxes) < 2:
        return "stretch"
    lo_i, len_i = (0, 2) if kind == "flex_col" else (1, 3)
    starts = [b[lo_i] for b in boxes]
    ends = [b[lo_i] + b[len_i] for b in boxes]
    lo, hi = min(starts), max(ends)
    if all(abs(s - lo) <= TOL and abs(e - hi) <= TOL for s, e in zip(starts, ends)):
        return "stretch"
    if all(abs(s - lo) <= TOL for s in starts):
        return "flex-start"
    if all(abs(e - hi) <= TOL for e in ends):
        return "flex-end"
    mid = (lo + hi) / 2
    if all(abs((s + e) / 2 - mid) <= TOL for s, e in zip(starts, ends)):
        return "center"
    return "flex-start"


def _padding(owner: Box, units: List[Box]) -> Dict[str, int]:
    if not units:
        return {"top": 0, "right": 0, "bottom": 0, "left": 0}
    ux, uy, uw, uh = union_box(units)
    ox, oy, ow, oh = owner
    return {
        "top": max(0, uy - oy),
        "right": max(0, (ox + ow) - (ux + uw)),
        "bottom": max(0, (oy + oh) - (uy + uh)),
        "left": max(0, ux - ox),
    }


def _compose(owner_id: str, owner_box: Box, units: List[Unit], used: set) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Lay out ``units`` (already built sibling subtrees) inside ``owner_id``."""
    margin = {"top": 0, "right": 0, "bottom": 0, "left": 0}
    if not units:
        return ({"layout_type": "flex_col", "justify_content": "flex-start", "align_items": "stretch",
                 "gap": 0, "margin": margin, "padding": dict(margin)}, [])

    by_id = {u[0]["id"]: u for u in units}
    kind, bands = _arrange([(u[0]["id"], u[1]) for u in units])
    padding = _padding(owner_box, [u[1] for u in units])

    if bands is None:  # overlap / no clean cut -> absolute positioning, reading order
        ordered = sorted(units, key=lambda u: (u[1][1], u[1][0], u[0]["id"]))
        return ({"layout_type": "absolute", "justify_content": None, "align_items": None,
                 "gap": 0, "margin": margin, "padding": padding}, [u[0] for u in ordered])

    children: List[Dict[str, Any]] = []
    extents: List[Tuple[int, int]] = []
    lo_i, len_i = (1, 3) if kind == "flex_col" else (0, 2)
    for n, band in enumerate(bands):
        band_units = [by_id[i] for i, _ in band]
        extents.append((min(u[1][lo_i] for u in band_units), max(u[1][lo_i] + u[1][len_i] for u in band_units)))
        if len(band_units) == 1:
            children.append(band_units[0][0])
            continue
        wid = f"{owner_id}~g{n}"
        while wid in used:
            wid += "_"
        used.add(wid)
        wbox = union_box([u[1] for u in band_units])
        wlayout, wchildren = _compose(wid, wbox, band_units, used)
        children.append({"id": wid, "layout": wlayout, "children": wchildren})

    gaps = [max(0, extents[i + 1][0] - extents[i][1]) for i in range(len(extents) - 1)]
    layout = {"layout_type": kind, "justify_content": "flex-start",
              "align_items": _align(kind, [u[1] for u in units]),
              "gap": int(round(median(gaps))) if gaps else 0, "margin": margin, "padding": padding}
    return layout, children


def build_semantic_tree(visual_ids: List[str], l1_data: Any) -> Dict[str, Any]:
    """Build the Layer 4.1 ``root`` tree for the given visual ids using the Layer 1 boxes."""
    boxes: Dict[str, Box] = {}
    width = height = None
    if isinstance(l1_data, dict):
        for c in l1_data.get("candidates", []):
            if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] not in boxes:
                b = parse_bbox(c.get("bbox"))
                if b:
                    boxes[c["id"]] = b
        w, h = l1_data.get("image_width"), l1_data.get("image_height")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
            width, height = int(w), int(h)

    ids = sorted({i for i in visual_ids if isinstance(i, str)})
    placed = [(i, boxes[i]) for i in ids if i in boxes]
    unplaced = [i for i in ids if i not in boxes]
    parents = assign_parents(placed)
    kids: Dict[Optional[str], List[str]] = {}
    for nid, p in parents.items():
        kids.setdefault(p, []).append(nid)

    used = set(ids) | {ROOT_ID}

    def subtree(nid: str) -> Dict[str, Any]:
        units = [(subtree(c), boxes[c]) for c in kids.get(nid, [])]
        layout, children = _compose(nid, boxes[nid], units, used)
        return {"id": nid, "layout": layout, "children": children}

    top = [(subtree(i), boxes[i]) for i in kids.get(None, [])]
    if width is not None:
        root_box: Box = (0, 0, width, height)
    elif placed:
        root_box = union_box([b for _, b in placed])
    else:
        root_box = (0, 0, 1, 1)
    layout, children = _compose(ROOT_ID, root_box, top, used)
    children.extend({"id": i, "layout": _compose(i, root_box, [], used)[0], "children": []} for i in unplaced)
    return {"id": ROOT_ID, "layout": layout, "children": children}
