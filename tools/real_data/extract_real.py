"""Render a real HTML page with WeasyPrint and extract L1/L2 bundles + DOM ground truth for ONE screen."""
import json, random, re, sys
from weasyprint import HTML, CSS
from weasyprint.urls import URLFetcher

FETCHER = URLFetcher(allowed_protocols=["file", "data"])  # offline: external fonts/CDNs are skipped

def _num(x):
    if x is None: return 0.0
    if isinstance(x, (int, float)): return float(x)
    if hasattr(x, "value") and x.value is not None: return float(x.value)
    return 0.0

def _rgba(c):
    if c is None or isinstance(c, str): return None
    if hasattr(c, "red"): return (c.red * (255 if c.red <= 1 else 1), c.green * (255 if c.red <= 1 else 1), c.blue * (255 if c.red <= 1 else 1), c.alpha)
    co = getattr(c, "coordinates", None)
    if co and None not in co[:3]:
        return (co[0] * 255, co[1] * 255, co[2] * 255, c.alpha if c.alpha is not None else 1.0)
    return None

def _hex(c):
    v = _rgba(c)
    return None if (v is None or v[3] < 0.03) else "#%02x%02x%02x" % tuple(max(0, min(255, round(t))) for t in v[:3])

def render(path, W, H, max_pages=40):
    css = CSS(string=f"@page {{ size: {W}px {H}px; margin: 0 }}")
    doc = HTML(filename=path, url_fetcher=FETCHER, media_type="screen").render(stylesheets=[css])
    pages = []
    for page in doc.pages[:max_pages]:
        boxes = {}
        def walk(b):
            el = getattr(b, "element", None)
            if el is not None and hasattr(b, "border_box_x") and isinstance(el.tag, str):
                area = b.border_width() * b.border_height()
                if el not in boxes or area > boxes[el][1]: boxes[el] = (b, area)
            for ch in getattr(b, "children", []) or []: walk(ch)
        walk(page._page_box)
        pages.append(boxes)
    return pages

def own_text(el):
    t = (el.text or "") + "".join((c.tail or "") for c in el)
    return re.sub(r"\s+", " ", t).strip()

def build(boxes, root_el, W, H, name):
    parent_of = {c: p for p in root_el.iter() for c in p}
    nodes = {}
    for el, (b, _) in boxes.items():
        tag = el.tag.split('}')[-1].lower()
        if tag in ("html", "body", "head", "script", "style", "link", "meta", "title", "path", "defs"): continue
        st = b.style
        if st["visibility"] != "visible" or st["opacity"] == 0: continue
        x0, y0 = b.border_box_x(), b.border_box_y(); x1, y1 = x0 + b.border_width(), y0 + b.border_height()
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 2 or y1 - y0 < 2: continue
        bg = _hex(st["background_color"]); text = own_text(el)
        bw = max(_num(st["border_top_width"]), _num(st["border_left_width"]))
        has_border = bw > 0 and _rgba(st["border_top_color"]) and _rgba(st["border_top_color"])[3] > 0.03
        present = tag in ("img", "svg", "input", "button", "select", "textarea", "video", "canvas") or bg or has_border or text
        if not present: continue
        rad = _num((st["border_top_left_radius"] or (0,))[0])
        nodes[el] = dict(el=el, tag=tag, bbox=(round(x0), round(y0), round(x1 - x0), round(y1 - y0)), bg=bg, text=text, fs=round(st["font_size"]),
                         tc=_hex(st["color"]), rad=round(rad), bc=_hex(st["border_top_color"]) if has_border else None, bw=round(bw) if has_border else None,
                         disp=" ".join(map(str, st["display"])) if isinstance(st["display"], (tuple, list)) else str(st["display"]),
                         fdir=str(st["flex_direction"]), cgap=_num(st["column_gap"]), rgap=_num(st["row_gap"]), cls=el.get("class", ""), typ=el.get("type", ""))
    def near_anc(el):
        p = parent_of.get(el)
        while p is not None and p not in nodes: p = parent_of.get(p)
        return p
    # drop pure shells whose box is (within 1px) identical to a visual descendant's: geometry cannot tell them apart
    changed = True
    while changed:
        changed = False
        for el in list(nodes):
            a = near_anc(el)
            if a is not None and not nodes[a]["text"] and all(abs(p - q) <= 1 for p, q in zip(nodes[a]["bbox"], nodes[el]["bbox"])):
                del nodes[a]; changed = True; break
    order = sorted(nodes, key=lambda e: (nodes[e]["bbox"][1], nodes[e]["bbox"][0], -nodes[e]["bbox"][2] * nodes[e]["bbox"][3]))
    kids = {}
    for el in order:
        a = near_anc(el); kids.setdefault(a, []).append(el)
    prefix = {}
    ids = {}
    def role(n, el):
        t, c = n["tag"], (n["cls"] or "").lower()
        has_kids = len(kids.get(el, [])) > 0
        if t == "button" or (t == "a" and ("btn" in c or "button" in c or "cta" in c)): return "button", ("primary" if n["bg"] else "secondary")
        if t in ("input", "select", "textarea"):
            return "input", {"checkbox": "checkbox", "radio": "radio"}.get(n["typ"], "dropdown" if t == "select" else "text")
        if t in ("img", "video", "canvas"): return "image", "generic"
        if t == "svg": return "image", ("icon" if min(n["bbox"][2:]) <= 48 else "generic")
        if n["text"] and not has_kids: return "text", ("heading" if t in ("h1", "h2", "h3", "h4", "h5", "h6") else ("caption" if n["fs"] < 13 else "body"))
        if n["text"] and has_kids: return "text", ("heading" if t in ("h1", "h2", "h3", "h4", "h5", "h6") else "body")
        v = "nav_bar" if t == "nav" else ("list" if t in ("ul", "ol") else ("grid" if "grid" in n["disp"] else ("card" if (n["rad"] > 6 and (n["bg"] or n["bw"])) else None)))
        return "container", v
    rolecode = {"button": "btn", "input": "inp", "text": "txt", "image": "img", "container": "ctr"}
    l1c, l2s = [], []
    for i, el in enumerate(order):
        n = nodes[el]; r, v = role(n, el); n["role"], n["variant"] = r, v
        nid = f"{rolecode[r]}-{i + 1:03d}"; ids[el] = nid
        conf = 0.72 if min(n["bbox"][2:]) < 14 else 0.97
        c = {"id": nid, "bbox": dict(zip("xywh", n["bbox"])), "confidence": conf}
        if n["text"]: c["ocr_text"] = n["text"]
        if n["bg"]: c["dominant_color"] = n["bg"]
        if n["text"] and n["tc"]: c["text_color"] = n["tc"]
        if n["text"]: c["font_size"] = n["fs"]
        if n["rad"]: c["border_radius"] = n["rad"]
        if n["bc"]: c["border_color"] = n["bc"]; c["border_width"] = n["bw"]
        l1c.append(c); l2s.append({"id": nid, "semantic_role": r, "semantic_variant": v})
    parents = {ids[e]: (ids[near_anc(e)] if near_anc(e) is not None else None) for e in order}
    # evaluable layout truth: containers whose visible children are DIRECT DOM children (no hidden wrapper between)
    layout = {}
    for el in order:
        ch = kids.get(el, [])
        if len(ch) < 2 or any(parent_of.get(c) is not el for c in ch): continue
        n = nodes[el]
        if "flex" in n["disp"] and "grid" not in n["disp"]:
            d = "row" if n["fdir"].startswith("row") else "col"
            layout[ids[el]] = {"dir": d, "gap": round(n["cgap"] if d == "row" else n["rgap"]), "kind": "flex"}
        elif n["disp"].startswith("block") and all("inline" not in nodes[c]["disp"] for c in ch) and "grid" not in n["disp"] and "flex" not in n["disp"]:
            layout[ids[el]] = {"dir": "col", "gap": None, "kind": "block"}
    shuffled = list(zip(l1c, l2s)); random.Random(7).shuffle(shuffled)
    l1 = {"schema_version": "2.1", "project_id": f"real_{name}", "image_width": W, "image_height": H, "candidates": [a for a, _ in shuffled]}
    l2 = {"schema_version": "2.1", "semantics": [b for _, b in shuffled]}
    return l1, l2, {"parents": parents, "layout": layout, "text": {ids[e]: nodes[e]["text"] for e in order if nodes[e]["text"]}}

if __name__ == "__main__":
    path, W, H, name = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    pages = render(path, W, H)
    root_el = next(e for bx in pages for e in bx if e.tag.lower() == "html")
    for pi, boxes in enumerate(pages):
        l1, l2, truth = build(boxes, root_el, W, H, f"{name}_p{pi + 1:02d}")
        if len(l1["candidates"]) < 5: continue
        json.dump({"l1": l1, "l2": l2, "truth": truth}, open(f"real_{name}_p{pi + 1:02d}.json", "w"), separators=(",", ":"))
        roles = {}
        for sm in l2["semantics"]: roles[sm["semantic_role"]] = roles.get(sm["semantic_role"], 0) + 1
        depth = lambda i: 0 if truth["parents"][i] is None else 1 + depth(truth["parents"][i])
        print(f"{name}_p{pi + 1:02d}: nodes={len(l1['candidates']):3d} depth={max(depth(i) for i in truth['parents'])} roots={sum(1 for p in truth['parents'].values() if p is None):2d} "
              f"layouts={len(truth['layout']):2d} (flex={sum(1 for v in truth['layout'].values() if v['kind']=='flex')}) weak={sum(1 for c in l1['candidates'] if c['confidence']<0.8)} roles={roles}")
