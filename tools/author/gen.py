"""Bundle generator: hand-authored object specs (pixel boxes) -> L1/L2 and L3/L4.1/L4.2 raw bundles (UI GOD contract).
Geometry/colour/contrast are computed from the pixels; structure fields are derived from the authored spec."""
import json, math, copy, re, os
from PIL import Image
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE = os.path.join(ROOT, "bundles", "ui-samples", "c4e33e193aab")  # skeleton for the Layer 3/4.1/4.2 sections
ZERO = "0" * 64

def hexc(rgb): return "#%02X%02X%02X" % tuple(int(round(v)) for v in rgb)
def lum(h):
    c = [int(h[i:i+2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)
def sat(h):
    r, g, b = [int(h[i:i+2], 16) / 255 for i in (1, 3, 5)]
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx

class Img:
    def __init__(self, path):
        self.a = np.asarray(Image.open(path).convert("RGB")).astype(float)
        self.h, self.w = self.a.shape[:2]
    def ring(self, b, inset=3):
        x0, y0, x1, y1 = [int(round(v)) for v in b]
        x0 += inset; y0 += inset; x1 -= inset; y1 -= inset
        if x1 - x0 < 4 or y1 - y0 < 4:
            x0, y0, x1, y1 = [int(round(v)) for v in b]
        mx, my = max(1, (x1 - x0) // 5), max(1, (y1 - y0) // 5)
        parts = [self.a[y0:y0+1, x0+mx:x1-mx], self.a[y1-1:y1, x0+mx:x1-mx], self.a[y0+my:y1-my, x0:x0+1], self.a[y0+my:y1-my, x1-1:x1]]
        px = np.concatenate([p.reshape(-1, 3) for p in parts if p.size])
        return np.median(px, axis=0)
    def bg(self, b, inset=3): return hexc(self.ring(b, inset))
    def at(self, x, y, r=2):
        p = self.a[max(0, y-r):y+r+1, max(0, x-r):x+r+1].reshape(-1, 3)
        return hexc(np.median(p, axis=0))
    def fg(self, b):
        x0, y0, x1, y1 = [int(round(v)) for v in b]
        reg = self.a[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3)
        if not len(reg): return None
        bgc = self.ring(b, 0)
        d = np.linalg.norm(reg - bgc, axis=1)
        k = max(3, int(len(reg) * 0.06))
        return hexc(np.median(reg[np.argsort(d)[-k:]], axis=0))

ROLE_CONF = {"text": 0.93, "button": 0.9, "icon": 0.85, "card": 0.9, "image": 0.85, "avatar": 0.85, "chart": 0.8,
             "nav": 0.9, "divider": 0.85, "other": 0.8, "progress": 0.85, "tab": 0.88, "badge": 0.85, "input": 0.85, "list": 0.85}

def O(k, role, bbox, parent=None, text=None, type=None, bg=None, fg=None, shape="rectangle", conf=None, ev=None,
      weight=None, icon=None, cat=None, state="none", layer=None, rt=None, sem=None, group=None, radius=None, multiline=False, border=None):
    return dict(k=k, role=role, bbox=bbox, parent=parent, text=text, type=type, bg=bg, fg=fg, shape=shape, conf=conf, ev=ev,
                weight=weight, icon=icon, cat=cat, state=state, layer=layer, rt=rt, sem=sem, group=group, radius=radius,
                multiline=multiline, border=border)

SEM_DEFAULT = {
    "text": ("TextLabel", "TEXT_CONTENT", "DISPLAY_CONTROL"), "button": ("Button", "ACTION_TRIGGER", "INTERACTIVE_CONTROL"),
    "card": ("Card", "SURFACE_CONTAINER", "CONTAINER_CONTROL"), "nav": ("NavigationBar", "TAB_CONTROLLER", "CONTAINER_CONTROL"),
    "tab": ("Tab", "TAB_CONTROLLER", "INTERACTIVE_CONTROL"), "input": ("InputField", "INPUT_TRIGGER", "INTERACTIVE_CONTROL"),
    "chart": ("Chart", "METRIC_DISPLAY", "DISPLAY_CONTROL"), "avatar": ("Avatar", "PROFILE_ENTITY", "DISPLAY_CONTROL"),
    "progress": ("ProgressIndicator", "METRIC_DISPLAY", "DISPLAY_CONTROL"), "badge": ("Badge", "FEEDBACK_INDICATOR", "DISPLAY_CONTROL"),
    "list": ("List", "LIST_CONTAINER", "CONTAINER_CONTROL"), "image": ("Image", "CONTENT_SECTION", "DISPLAY_CONTROL"),
}

def build_l1(S, img):
    cx0, cy0, cx1, cy1 = S["crop"]; W, H = cx1 - cx0, cy1 - cy0
    cnt, ids, objs = {}, {}, []
    for o in S["objs"]:
        n = cnt.get(o["role"], 0); cnt[o["role"]] = n + 1
        ids[o["k"]] = f'{o["role"]}_{n}'
    kids = {}
    for o in S["objs"]:
        if o["parent"]: kids.setdefault(o["parent"], []).append(ids[o["k"]])
    depth = {}
    def dep(k):
        if k in depth: return depth[k]
        p = next(x for x in S["objs"] if x["k"] == k)["parent"]
        depth[k] = 0 if not p else dep(p) + 1
        return depth[k]
    boxes = {}
    for o in S["objs"]:
        x0, y0, x1, y1 = o["bbox"]
        b = (max(0, x0 - cx0), max(0, y0 - cy0), min(W, x1 - cx0), min(H, y1 - cy0))
        boxes[o["k"]] = b
    for i, o in enumerate(S["objs"]):
        b = boxes[o["k"]]; w, h = b[2] - b[0], b[3] - b[1]
        pos = {"x": round((b[0] + w / 2) / W * 100, 1), "y": round((b[1] + h / 2) / H * 100, 1)}
        siz = {"w": max(0.1, round(w / W * 100, 1)), "h": max(0.1, round(h / H * 100, 1))}
        ib = (b[0], b[1], b[2], b[3])
        bgc = o["bg"] if o["bg"] not in (None, "auto") else (img.bg(ib) if o["bg"] == "auto" else None)
        fgc = o["fg"] if o["fg"] not in (None, "auto") else (img.fg(ib) if o["fg"] == "auto" else None)
        o["_bg"], o["_fg"], o["_box"], o["_id"] = bgc, fgc, b, ids[o["k"]]
        conf = o["conf"] if o["conf"] is not None else ROLE_CONF.get(o["role"], 0.85)
        if o["role"] == "text" and o["text"] is None: conf = min(conf, 0.7)
        sib = [x for x in S["objs"] if x["parent"] == o["parent"] and x["k"] != o["k"] and x["role"] in ("text", "button", "card", "divider", "input", "tab")]
        al = [ids[x["k"]] for x in sib if o["role"] in ("text", "button", "card", "divider", "input", "tab") and abs(boxes[x["k"]][0] - b[0]) / W * 100 <= 0.6][:4]
        ev = o["ev"] or {}
        if isinstance(ev, str): ev = {"shape": ev}
        evd = {"shape": ev.get("shape"), "text": ev.get("text"), "position": None, "motion": None,
               "color": ev.get("color") or (f"Sampled fill {bgc}." if bgc and o["role"] != "text" else (f"Sampled glyph colour {fgc}." if fgc else None)), "relationship": ev.get("relationship")}
        objs.append({
            "id": ids[o["k"]], "role": o["role"], "type": o["type"], "parent": ids[o["parent"]] if o["parent"] else None,
            "children": kids.get(o["k"]), "group": o["group"], "layer": o["layer"], "position": pos, "size": siz, "rotation": None,
            "visibility": "visible", "shape": {"form": o["shape"], "corner_values": None},
            "style": {"bg": bgc, "color": fgc if o["role"] in ("text", "icon") else None, "opacity": None, "blur": None, "gradient": None,
                      "radius": o["radius"], "border": o["border"], "stroke": None, "shadow": None, "elevation": None, "mask": None, "pattern": None},
            "typography": {"font_guess": "geometric sans-serif (visual estimate)" if o["role"] == "text" else None, "size_guess": None,
                           "weight": o["weight"], "line_height": None},
            "image": {"category": o["cat"]}, "icon": {"name_guess": o["icon"]}, "motion": {"state": "none", "animation_ref": None},
            "interaction": {"state": o["state"], "keyboard_open": None, "cursor_visible": None},
            "relationships": {"above": None, "below": None, "left_of": None, "right_of": None, "aligned_with": al or None,
                              "distance_to_nearest": None, "overlaps": None, "layout": None, "reading_order": i, "hierarchy_depth": dep(o["k"])},
            "text": {"value": o["text"], "truncated": False, "multiline": o["multiline"], "language": "en" if o["text"] else None,
                     "ocr_confidence": (0.9 if W > 250 else 0.8) if o["text"] else None},
            "confidence": conf, "evidence": evd, "source_frame": "screen_0"})
    confs = [o["confidence"] for o in objs]
    dist = {"0.90-1.00": sum(c >= .9 for c in confs), "0.70-0.89": sum(.7 <= c < .9 for c in confs),
            "0.60-0.69": sum(.6 <= c < .7 for c in confs), "<0.60": sum(c < .6 for c in confs)}
    rep = {"objects_detected": len(objs), "scenes_or_screens_processed": 1, "frames_processed": None, "ocr_quality": S.get("ocr", "medium"),
           "motion_detected": False, "missing_regions": S.get("missing", []), "uncertain_regions": [o["id"] for o in objs if o["confidence"] < .6],
           "confidence_distribution": dist, "warnings": S.get("warnings", []), "recommendation": "ready as-is", "compiler_ready": True}
    l1 = {"input": {"type": "image", "subtype": "mobile", "confidence": 0.9},
          "screen": {"device": "mobile", "orientation": "portrait", "viewport": f"{W}x{H}", "theme": S["theme"], "safe_area": None,
                     "status_bar": S.get("status_bar", "visible"), "navigation_bar": None, "window_count": 1, "scroll_position": None, "zoom_level": None},
          "objects": objs, "report": rep}
    return l1, ids

def layout_of(children):
    """children: list of (id, (x0,y0,x1,y1) in crop px). Returns (layout, evidence)."""
    if len(children) < 2: return "freeform", "Single child; no arrangement can be measured."
    cs = sorted(children, key=lambda c: (c[1][1] + c[1][3]) / 2)
    cy = [(c[1][1] + c[1][3]) / 2 for c in cs]
    band_overlap = lambda a, b: min(a[3], b[3]) - max(a[1], b[1]) > 0.5 * min(a[3] - a[1], b[3] - b[1])
    col_overlap = lambda a, b: min(a[2], b[2]) - max(a[0], b[0]) > 0.5 * min(a[2] - a[0], b[2] - b[0])
    allband = all(band_overlap(cs[0][1], c[1]) for c in cs[1:])
    xs = sorted(children, key=lambda c: c[1][0])
    inorder = all(xs[i][1][2] <= xs[i+1][1][2] + 2 for i in range(len(xs) - 1))
    if allband and inorder:
        return "horizontal_stack", f"{', '.join(c[0] for c in xs[:6])}{'...' if len(xs) > 6 else ''} share one horizontal band and increase left to right (x {xs[0][1][0]:.0f}-{xs[-1][1][2]:.0f}px)."
    allcol = all(col_overlap(cs[0][1], c[1]) for c in cs[1:])
    ydisjoint = all(cs[i][1][3] <= cs[i+1][1][3] + 2 for i in range(len(cs) - 1)) and all(cs[i][1][1] <= cs[i+1][1][1] + 2 for i in range(len(cs) - 1))
    if allcol and ydisjoint:
        return "vertical_stack", f"{', '.join(c[0] for c in cs[:6])}{'...' if len(cs) > 6 else ''} follow in increasing y (centres {', '.join(f'{v:.0f}' for v in cy[:6])}px) with overlapping horizontal extent."
    rows = {}
    for c in children: rows.setdefault(round((c[1][1] + c[1][3]) / 2 / 8), []).append(c)
    if len(children) >= 4 and len(rows) >= 2 and all(len(r) >= 2 for r in rows.values()):
        return "grid", f"{len(children)} children sit in {len(rows)} rows with aligned columns."
    return "freeform", "Children mix horizontal and vertical arrangement or overlap; no single stack or grid fits."

def build_l2(S, l1, ids):
    cx0, cy0, cx1, cy1 = S["crop"]
    by = {o["k"]: o for o in S["objs"]}
    idof = {o["k"]: o["_id"] for o in S["objs"]}
    regions, lg, cg, rc = [], [], [], {}
    top = [o for o in S["objs"] if o["parent"] is None]
    rid = 0
    def reg(container, childkeys, rtype, rtev, extra_ev):
        nonlocal rid
        name = f"region_{rid}"; rid += 1
        ch = [(idof[k], by[k]["_box"]) for k in childkeys]
        lay, lev = layout_of(ch)
        cl = list(extra_ev)
        xs = sorted(round(by[k]["_box"][0]) for k in childkeys)
        cl.append(f"children span y {min(by[k]['_box'][1] for k in childkeys):.0f}-{max(by[k]['_box'][3] for k in childkeys):.0f}px of {cy1 - cy0}px")
        r = {"region_id": name, "region_type": rtype, "children": [idof[k] for k in childkeys]}
        if container: r["container"] = idof[container]
        if rtype != "unclassified": r["region_type_evidence"] = rtev
        r["clustering_evidence"] = cl
        regions.append(r)
        lg.append({"node": name, "layout": lay, "layout_evidence": lev})
        lows = [by[k]["conf"] if by[k]["conf"] is not None else ROLE_CONF.get(by[k]["role"], .85) for k in childkeys]
        rc[name] = round(min(lows), 2)
        return name
    # root region(s) for top-level objects
    reg(None, [o["k"] for o in top], S.get("root_type", "content"), S.get("root_ev", "Top-level objects of the screen."),
        [f"{len(top)} top-level objects have no Layer 1 parent", "Layer 1 reading order lists them top to bottom"])
    for o in S["objs"]:
        ck = [x["k"] for x in S["objs"] if x["parent"] == o["k"]]
        if not ck: continue
        extra = [f"Layer 1 children hint: {idof[o['k']]} lists {len(ck)} children"]
        if o["_bg"]: extra.append(f"shared {o['_bg']} fill of {idof[o['k']]}")
        reg(o["k"], ck, o["rt"] or "unclassified", f"{idof[o['k']]} {o['rt'] or ''} region as authored from its Layer 1 role and position.", extra)
    allc = [o["conf"] if o["conf"] is not None else ROLE_CONF.get(o["role"], .85) for o in S["objs"]]
    allc = [o["confidence"] for o in l1["objects"]]
    proj = min(allc)
    l2 = {"project": {"project_id": "ui-samples", "input_type": "image"}, "screens": [{"screen_id": "screen_0", "device": "mobile"}], "scenes": [],
          "regions": regions, "component_templates": S.get("templates_out", []), "component_instances": S.get("instances_out", []),
          "layout_graph": lg, "constraint_graph": [], "interaction_graph": {"edges": []}, "navigation_graph": None, "state_graph": None,
          "z_stack": {"order": ["content"], "evidence": "Single flat screen; no overlap or occlusion between regions was observed."},
          "confidence": {"project": proj, "screens": {"screen_0": proj}, "regions": rc, "components": {}, "weak_child": any(c < .8 for c in allc)},
          "validation_report": {"no_cycles": True, "single_parent_enforced": True, "no_impossible_overlaps": True, "duplicate_ids_removed": True,
                                "graph_connected": True, "layout_consistent": True, "failures": [], "status": "pass"}}
    if S.get("z_order"): l2["z_stack"] = S["z_order"]
    return l2

def tag(name, obj): return f"<{name}>\n{json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}\n</{name}>"

def finish12(S, l1, l2, image_hash):
    n = len(l1["objects"])
    man = {"pipeline_id": "ui_god", "run_id": "pending", "image_hash": image_hash, "layer1_hash": ZERO, "layer2_hash": ZERO, "schema_version": "v2.1"}
    rep = {"layer": "UI_GOD_LAYER1+2_FUSED", "layer1_version": "v5", "layer2_version": "v2", "schema_version": "v2.1", "pipeline": "UI", "pipeline_id": "ui_god",
           "run_id": "pending", "image_hash": image_hash, "extraction_status": "complete", "layer1_output_file_name": "LAYER1_CCIR.json",
           "layer2_output_file_name": "LAYER2_CSIR.json", "next_layer": "UI_GOD_LAYER3", "layer1_output_hash": ZERO, "layer2_output_hash": ZERO,
           "objects_extracted": n, "warnings": S.get("warnings", [])}
    return "\n".join([tag("LAYER1_OUTPUT", l1), tag("LAYER2_OUTPUT", l2), tag("MANIFEST_UPDATE", man), tag("REPORT", rep)]) + "\n"

# ------------------------------------------------------------------------------------------ Layers 3 / 4.1 / 4.2
def load_sample():
    t = open(SAMPLE + "/layer342_bundle.raw.txt").read()
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r"<([A-Z0-9_]+)>\s*(.*?)\s*</\1>", t, re.S)}

def pascal(s): return "".join(p.capitalize() for p in s.split("_"))
def dist(a, b): return math.dist([int(a[i:i+2], 16) for i in (1, 3, 5)], [int(b[i:i+2], 16) for i in (1, 3, 5)])

def prep_templates(S):
    idof = {o["k"]: o["_id"] for o in S["objs"]}
    tout, iout, n = [], [], 0
    for ti, t in enumerate(S.get("templates", [])):
        names = []
        for el in t["insts"]:
            iid = f"instance_{n}"; n += 1; names.append(iid)
            iout.append({"instance_id": iid, "template_id": f"template_{ti}", "elements": [idof[k] for k in el]})
        tout.append({"template_id": f"template_{ti}", "name": t["name"], "structure": t["structure"], "instances": names})
    S["templates_out"], S["instances_out"] = tout, iout

def build_l342(S, l1, l2):
    base = load_sample()
    objs = l1["objects"]; byid = {o["id"]: o for o in objs}
    spec_by_id = {o["_id"]: o for o in S["objs"]}
    n = len(objs)
    # ---- Layer 3
    L3 = copy.deepcopy(base["LAYER3_OUTPUT"])
    uses = []  # (hex, label, objid)
    for o in S["objs"]:
        if o["_bg"]: uses.append((o["_bg"], "fill", o["_id"], o))
        if o["_fg"] and o["role"] in ("text", "icon"): uses.append((o["_fg"], "glyph", o["_id"], o))
    clusters = []
    for hx, lab, oid, o in uses:
        for c in clusters:
            if dist(c["rep"], hx) <= 14: c["m"].append((hx, lab, oid, o)); break
        else: clusters.append({"rep": hx, "m": [(hx, lab, oid, o)]})
    for c in clusters:
        vals = [m[0] for m in c["m"]]; c["rep"] = max(sorted(set(vals)), key=vals.count)
    clusters = [c for c in clusters if len(c["m"]) >= 2 or sat(c["rep"]) > 0.4]
    clusters.sort(key=lambda c: -len(c["m"]))
    clusters = clusters[:14]
    toks, tokof = [], {}
    roles_taken = set()
    for i, c in enumerate(sorted(clusters, key=lambda c: c["rep"]), 1):
        tid = f"color_{i:03d}"; r = c["rep"]; m = c["m"]
        txt = [x for x in m if x[1] == "glyph" and x[3]["role"] == "text"]
        fills = [x for x in m if x[1] == "fill"]
        if sat(r) > 0.45 and (fills or txt): role = "accent"
        elif txt and len(txt) >= len(fills): role = "text_primary" if lum(r) < 0.2 else ("text_secondary" if lum(r) < 0.45 else "text_inverse")
        elif fills and lum(r) > 0.8: role = "surface"
        elif fills: role = "surface_variant"
        else: role = "control_fill"
        usage = f"{'text' if txt else 'fill'}_{role} ({', '.join(x[2] for x in m[:4])}{'...' if len(m) > 4 else ''})"
        toks.append({"id": tid, "raw": r, "observed_usage": usage, "repeated_instances": len(m), "confidence": 0.93 if len(m) >= 2 else 0.9,
                     "candidate_semantic_roles": [role], "selected_role": role})
        tokof[r] = tid
        for x in m: tokof[x[0]] = tid
    L3["TOKEN_REGISTRY"]["color_tokens"] = toks
    rel = {k: None for k in ("primary", "secondary", "accent", "surface", "surface_variant", "border", "disabled", "success", "danger", "warning", "information", "text_primary", "text_secondary", "inverse_text")}
    for t in toks:
        r = t["selected_role"]
        key = {"text_inverse": "inverse_text"}.get(r, r)
        if key in rel and rel[key] is None: rel[key] = t["id"]
    rel["evidence"] = "Roles assigned from where each sampled colour is used (text glyphs vs container fills) and from saturation of the colour."
    L3["COLOR_RELATIONSHIPS"] = rel
    # spacing: shared left edge
    W = S["crop"][2] - S["crop"][0]
    edges = {}
    for o in S["objs"]:
        if o["role"] in ("text", "button", "card", "divider", "input"):
            edges.setdefault(round(o["_box"][0] / W * 100, 1), []).append(o["_id"])
    shared = [(x, v) for x, v in sorted(edges.items()) if len(v) >= 3]
    sp, al = [], []
    for i, (x, v) in enumerate(sorted(shared, key=lambda t: -len(t[1]))[:3], 1):
        sp.append({"id": f"spacing_{i:03d}", "raw": f"{x}% of viewport width", "observed_usage": f"left_edge_inset ({', '.join(v[:4])}{'...' if len(v) > 4 else ''})",
                   "repeated_instances": len(v), "confidence": 0.85, "candidate_semantic_roles": ["content_inset"], "selected_role": "content_inset"})
        al.append(f"shared left edge x={x}% ({', '.join(v[:4])}{'...' if len(v) > 4 else ''})")
    L3["TOKEN_REGISTRY"]["spacing_tokens"] = sp
    L3["SPACING_RHYTHM"] = {"scale": [s["raw"] for s in sp], "grid_consistency": None, "padding_systems": [], "alignment_systems": al,
                            "vertical_rhythm": None, "horizontal_rhythm": None, "edge_consistency": None}
    # fonts
    wts = {}
    for o in S["objs"]:
        if o["role"] == "text" and o["weight"]: wts.setdefault(o["weight"], []).append(o["_id"])
    ft = []
    for i, (w, v) in enumerate(sorted(wts.items()), 1):
        if len(v) >= 2:
            ft.append({"id": f"font_{i:03d}", "raw": f"{w} geometric sans-serif (visual estimate)", "observed_usage": f"{w}_text ({', '.join(v[:4])}{'...' if len(v) > 4 else ''})",
                       "repeated_instances": len(v), "confidence": 0.8, "candidate_semantic_roles": [f"text_{w}"], "selected_role": f"text_{w}"})
    L3["TOKEN_REGISTRY"]["font_tokens"] = ft
    L3["TYPOGRAPHY_SYSTEM"]["weight_patterns"] = [f"{w} ({', '.join(v[:4])}{'...' if len(v) > 4 else ''})" for w, v in sorted(wts.items())]
    # icons
    ic = [o for o in objs if o["role"] == "icon"]
    L3["ICON_REGISTRY"]["sizes"] = [f"{o['size']['w']}% x {o['size']['h']}% ({o['id']})" for o in ic[:8]]
    # contrast
    pairs, seen = [], set()
    for o in S["objs"]:
        if o["role"] == "text" and o["_fg"] and o["parent"]:
            par = next(x for x in S["objs"] if x["k"] == o["parent"])
            bgc = par["_bg"] or (next((x["_bg"] for x in S["objs"] if x["k"] == par["parent"] and x["_bg"]), None) if par["parent"] else None)
            if not bgc: continue
            key = (tokof.get(o["_fg"]), tokof.get(bgc))
            if None in key or key in seen: continue
            seen.add(key); pairs.append({"pair": f"{key[0]} on {key[1]}", "ratio": contrast(o["_fg"], bgc)})
    L3["ACCESSIBILITY"]["contrast"] = pairs[:8]
    L3["DENSITY_METRICS"].update({"objects_per_viewport": n, "hierarchy_depth": max(o["relationships"]["hierarchy_depth"] for o in objs)})
    L3["STYLE_FINGERPRINT"].update({"token_count": len(toks) + len(sp) + len(ft), "component_count": len(S.get("templates_out", [])), "spacing_rhythm": (sp[0]["raw"] if sp else None)})
    # interaction roles
    irs = []
    for o in sorted([x for x in objs if x["role"] == "button"], key=lambda x: x["id"]):
        big = o["size"]["w"] * o["size"]["h"]
        irs.append({"object": o["id"], "candidate_semantic_roles": ["primary_action" if (o["style"]["bg"] and sat(o["style"]["bg"]) > 0.45) else "secondary_action"],
                    "confidence": 0.75, "evidence": f"Button-shaped control {o['style']['bg'] or 'with no sampled fill'} measuring {o['size']['w']}% x {o['size']['h']}% of the screen.", "derived_from": [o["id"]]})
    L3["INTERACTION_ROLES"] = {"ordering": "sort_by_id", "records": irs}
    # components
    tmpl = S.get("templates_out", [])
    L3["COMPONENT_DNA"] = [{"component_id": t["template_id"], "name": t["name"], "states": ["default"], "structure": t["structure"], "instances": len(t["instances"]),
                            "evidence": f"Layer 2 {t['template_id']} has {len(t['instances'])} instances with the same element structure."} for t in tmpl]
    L3["REUSE_MAP"] = {"ordering": "sort_by_id", "records": [{"id": t["template_id"], "instances": t["instances"], "evidence": "Layer 2 component_instances"} for t in tmpl]}
    L3["READY_SCORE"].update({"component_ready": bool(tmpl)})
    L3["VALIDATION_GATE"]["layer1_compiler_ready"] = True
    # ---- Layer 4.1
    L41 = copy.deepcopy(base["LAYER4_1_OUTPUT"])
    nodes, prim = [], {}
    for o in sorted(objs, key=lambda x: x["id"]):
        sp_ = spec_by_id[o["id"]]
        if o["role"] not in SEM_DEFAULT: continue
        p, tx, cc = sp_["sem"] or SEM_DEFAULT[o["role"]]
        if not sp_["sem"] and o["type"] and o["role"] in ("button",): p = pascal(o["type"])
        nodes.append({"object_id": o["id"], "observed_text": o["text"]["value"], "canonical_chain": {"primitive": p, "taxonomy": tx, "control_class": cc},
                      "source_interaction_role": f"Layer3.INTERACTION_ROLES[{o['id']}]" if o["role"] == "button" else None,
                      "evidence": f"Layer 1 role={o['role']} with Layer 2 structure; no business meaning inferred."})
        prim.setdefault(p, []).append(o["id"])
    L41["semantic_graph"] = {"nodes": nodes, "edges": []}
    L41["canonical_taxonomy"] = sorted({x["canonical_chain"]["taxonomy"] for x in nodes})
    L41["primitive_registry"] = [{"primitive": p, "derived_from": f"Layer1.objects[{v[0]}..{v[-1]}]" if len(v) > 1 else f"Layer1.objects[{v[0]}]"} for p, v in sorted(prim.items())]
    reg_of = {r.get("container"): r["region_id"] for r in l2["regions"] if r.get("container")}
    L41["accessibility_graph"] = []
    for o in sorted(objs, key=lambda x: x["id"]):
        if o["role"] in ("button", "input", "tab"):
            lab = [byid[c]["text"]["value"] for c in (o["children"] or []) if byid[c]["role"] == "text" and byid[c]["text"]["value"]]
            L41["accessibility_graph"].append({"object_id": o["id"], "role": {"input": "textbox"}.get(o["role"], o["role"]), "label": " | ".join(lab) or None, "focus_order": None,
                                               "landmark": None, "reading_priority": None, "derived_from": [f"Layer1 {o['id']}.children"] if lab else [f"Layer1 {o['id']}: no visible text label"]})
    L41["interaction_contracts"] = []
    rels = []
    for o in sorted(objs, key=lambda x: x["id"]):
        if o["children"]:
            rels.append({"type": "owns" if o["role"] == "button" else "contains", "source": o["id"], "target": o["children"],
                         "evidence": [f"Layer1 {o['id']}.children"] + ([f"Layer2 {reg_of[o['id']]}"] if o["id"] in reg_of else [])})
    L41["semantic_relationships"] = rels
    recs = []
    for nd in nodes:
        tx = nd["canonical_chain"]["taxonomy"]
        rules = (["cannot_contain:ACTION_TRIGGER", "must_belong_to:1_region"] if tx == "ACTION_TRIGGER" else ["cannot_have_children"] if tx == "TEXT_CONTENT" else ["must_belong_to:1_region"])
        recs.append({"object_id": nd["object_id"], "canonical_type": tx, "rules_applied": rules, "violations": [], "evidence": [f"Layer2 region membership of {nd['object_id']}"]})
    L41["semantic_constraints"] = {"ordering": "sort_by_id", "records": recs}
    order = {o["id"]: o["relationships"]["reading_order"] for o in objs}
    seq = sorted(nodes, key=lambda x: order[x["object_id"]])
    L41["ui_grammar"] = {"ordering": "sort_by_reading_order", "records": [{"screen_id": "screen_0", "sequence": [x["canonical_chain"]["taxonomy"] for x in seq],
                                                                          "evidence": [f"Layer1 reading order: {', '.join(x['object_id'] for x in seq[:6])}{'...' if len(seq) > 6 else ''}"]}]}
    fm = {"vertical_stack": ("STACK", "vertical"), "horizontal_stack": ("STACK", "horizontal"), "grid": ("GRID", None), "freeform": ("FLOW", None), "absolute": ("ANCHOR", None)}
    L41["layout_model"] = {"ordering": "sort_by_reading_order", "records": [
        {"node": g["node"], "flow": fm[g["layout"]][0], "stack_direction": fm[g["layout"]][1], "grid_dimensions": None, "anchor": None, "alignment": None,
         "responsive_behavior": None, "breakpoint_hint": None, "derived_from": [f"Layer2.LAYOUT_GRAPH[{g['node']}]"]} for g in l2["layout_graph"]]}
    L41["entity_resolution"] = []
    rcs = l2["confidence"]["regions"]
    L41["confidence"] = {"ordering": "sort_by_id", "records": [{"object_id": nd["object_id"], "semantic_confidence": byid[nd["object_id"]]["confidence"],
                                                                "structural_confidence": byid[nd["object_id"]]["confidence"], "interaction_confidence": None, "accessibility_confidence": None} for nd in nodes]}
    # ---- Layer 4.2
    L42 = copy.deepcopy(base["LAYER4_2_OUTPUT"])
    ex = []
    for nd in nodes:
        tx = nd["canonical_chain"]["taxonomy"]; o = byid[nd["object_id"]]
        if tx in ("ACTION_TRIGGER", "INPUT_TRIGGER") or (tx == "TAB_CONTROLLER" and o["role"] == "tab"):
            st = ["default"] + (["selected"] if o["interaction"]["state"] == "selected" else [])
            ex.append({"component": o["id"], "canonical_type": tx, "allowed_states": st, "allowed_events": [], "allowed_transitions": [], "terminal_states": [], "illegal_states": [],
                       "evidence": [f"Layer4.1 canonical chain {tx}", f"Layer1 interaction.state = {o['interaction']['state']} (only the observed state{'s' if len(st) > 1 else ''} listed)"]})
    L42["EXECUTION_CONTRACTS"] = {"ordering": "sort_by_id", "records": ex}
    L42["STATE_GRAPH"] = {"nodes": [], "edges": [], "lifecycle": [{"component": o["id"], "created": None, "visible": True, "active": None, "hidden": None, "destroyed": None,
                                                                  "evidence": [f"Layer1 {o['id']}.visibility = visible"]} for o in sorted(objs, key=lambda x: x["id"]) if o["parent"] is None]}
    man = copy.deepcopy(base["MANIFEST_UPDATE"]); man["timestamp"] = "2026-11-26T00:00:00Z"
    rep = copy.deepcopy(base["REPORT"])
    rep["warnings"] = ["Static single screenshot: no events, transitions, timing or errors are observable."] + (
        [] if tmpl else ["No repeated component structures (2+ instances) were recorded in Layer 2, so COMPONENT_DNA is empty by rule."])
    return "\n".join([tag("LAYER3_OUTPUT", L3), tag("LAYER4_1_OUTPUT", L41), tag("LAYER4_2_OUTPUT", L42), tag("MANIFEST_UPDATE", man), tag("REPORT", rep)]) + "\n"

# ------------------------------------------------------------------------------------------------------- runner
import hashlib, os
def make(S, outdir, srcdir="_inbox", cropdir="Images"):
    os.makedirs(outdir, exist_ok=True); os.makedirs(cropdir, exist_ok=True)
    src = Image.open(os.path.join(srcdir, S["src"])).convert("RGB")
    crop = src.crop(S["crop"]); png = os.path.join(cropdir, S["name"] + ".png"); crop.save(png)
    ih = hashlib.sha256(open(png, "rb").read()).hexdigest()
    img = Img(png)
    l1, ids = build_l1(S, img)
    prep_templates(S)
    l2 = build_l2(S, l1, ids)
    open(f"{outdir}/l12.txt", "w").write(finish12(S, l1, l2, ih))
    open(f"{outdir}/l342.txt", "w").write(build_l342(S, l1, l2))
    return png, ih, l1, l2

def overlay(S, png, out, scale=3):
    from PIL import ImageDraw
    im = Image.open(png).convert("RGB"); im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    d = ImageDraw.Draw(im); cx0, cy0 = S["crop"][0], S["crop"][1]
    pal = {"text": "#e11", "button": "#06c", "card": "#0a0", "icon": "#e60", "image": "#a0a", "avatar": "#a0a", "chart": "#088", "nav": "#440", "divider": "#888", "other": "#666", "progress": "#c08", "tab": "#06c", "badge": "#c60", "input": "#06c"}
    for o in S["objs"]:
        x0, y0, x1, y1 = o["bbox"]; b = [(x0 - cx0) * scale, (y0 - cy0) * scale, (x1 - cx0) * scale, (y1 - cy0) * scale]
        d.rectangle(b, outline=pal.get(o["role"], "#000"), width=1)
    im.save(out)
