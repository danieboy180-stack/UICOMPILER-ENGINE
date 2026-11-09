"""Executable contract: the prompt rules, as code. Every finding carries a rule id traceable to the prompt text.

check_l1 / check_l2 implement 'UI GOD IR - FUSED LAYER 1+2 v1.1'; check_ir implements 'LAYERS 3 + 4.1 + 4.2 v1.1'.
Pure functions over parsed JSON, no I/O, no mutation. Errors block export; warnings are recorded in the dataset row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from . import spec
from .bundle import load_body, parse_tags
from .seal import pipeline_hash, substantive_hash, _unwrap

ID_RE = re.compile(r"^([a-z][a-z_]*)_(\d+)$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Finding:
    rule: str
    level: str  # "error" | "warn"
    path: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.rule} {self.path}: {self.msg}"


def has_errors(fs: list[Finding]) -> bool:
    return any(f.level == "error" for f in fs)


def walk(o: Any, path: str = "$") -> Iterator[tuple[str, dict]]:
    if isinstance(o, dict):
        yield path, o
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")


def strings(o: Any) -> Iterator[str]:
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def section(root: dict, *names: str) -> list[dict]:
    for n in names:
        for k in (n, n.lower(), n.upper()):
            v = root.get(k)
            if isinstance(v, dict) and isinstance(v.get("records"), list):
                return [r for r in v["records"] if isinstance(r, dict)]
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty(v: Any) -> bool:
    return bool(v) and (not isinstance(v, str) or v.strip() != "")


def _static(l1: dict) -> bool:
    t = (l1.get("input") or {}).get("type") if isinstance(l1.get("input"), dict) else None
    return t not in ("video", "screen_recording")


def l1_objects(l1: dict) -> list[dict]:
    objs = l1.get("objects", l1.get("OBJECTS", []))
    return [o for o in objs if isinstance(o, dict)] if isinstance(objs, list) else []


# ----------------------------------------------------------------------------------------------------------- Layer 1
def check_l1(l1: Any, report: Any = None) -> list[Finding]:
    f: list[Finding] = []
    E = lambda r, p, m: f.append(Finding(r, "error", p, m))  # noqa: E731
    W = lambda r, p, m: f.append(Finding(r, "warn", p, m))  # noqa: E731
    if not isinstance(l1, dict):
        return [Finding("L1.SHAPE", "error", "$", "Layer 1 must be a JSON object")]
    raw_objs = l1.get("objects", l1.get("OBJECTS"))
    if not isinstance(raw_objs, list):
        E("L1.SHAPE", "$.objects", "objects must be a list")
        return f
    inp = l1.get("input")
    if not isinstance(inp, dict) or inp.get("type") not in spec.L1_INPUT_TYPES:
        E("L1.INPUT", "$.input.type", f"must be one of {spec.L1_INPUT_TYPES}")
    sketch = isinstance(inp, dict) and inp.get("type") == "sketch"
    ids: dict[tuple, dict] = {}
    for i, o in enumerate(raw_objs):
        p = f"$.objects[{i}]"
        if not isinstance(o, dict):
            E("L1.OBJ.SHAPE", p, "object must be a dict")
            continue
        oid, role, frame = o.get("id"), o.get("role"), o.get("source_frame")
        if role not in spec.L1_ROLES:
            E("L1.OBJ.ROLE", p, f"role {role!r} not in prompt role list")
        m = ID_RE.match(oid) if isinstance(oid, str) else None
        if not m or m.group(1) != role:
            E("L1.OBJ.ID", p, f"id {oid!r} must be role + '_' + zero-based index (role={role!r})")
        if not _nonempty(frame):
            E("L1.OBJ.SOURCE_FRAME", p, "source_frame is mandatory (provenance)")
        if (frame, oid) in ids:
            E("L1.OBJ.ID", p, f"duplicate id {oid!r} in {frame!r}")
        ids[(frame, oid)] = o
        pos, size = o.get("position"), o.get("size")
        nulls = 0
        for name, d, keys in (("position", pos, "xy"), ("size", size, ("w", "h"))):
            if d is None:
                nulls += 1
                continue
            for k in keys:
                v = d.get(k) if isinstance(d, dict) else None
                if not _num(v) or not (0 <= v <= 100) or (name == "size" and v <= 0):
                    E("L1.OBJ.GEOMETRY", f"{p}.{name}.{k}", f"percent value in (0,100] required, got {v!r}")
        text = o.get("text") if isinstance(o.get("text"), dict) else {}
        if role == "text" and text.get("value") is None:
            nulls += 1
        if isinstance(text.get("value"), str) and "\u00b7" in text["value"]:
            E("L1.OBJ.TEXT_SEP", f"{p}.text.value", "the '\u00b7' separator is forbidden; use ' | '")
        conf = o.get("confidence")
        if not _num(conf) or not (0 <= conf <= 1):
            E("L1.OBJ.CONF", f"{p}.confidence", f"must be a number in [0,1], got {conf!r}")
        else:
            cap = 0.60 if sketch else (0.60 if nulls >= 2 else 0.70 if nulls == 1 else 1.0)
            if conf > cap + 1e-9:
                E("L1.OBJ.CONF_CAP", f"{p}.confidence", f"{conf} exceeds cap {cap} ({nulls} material field(s) null, sketch={sketch})")
        rel = o.get("relationships")
        if isinstance(rel, dict) and rel.get("layout") is not None:
            E("L1.OBJ.LAYOUT_NULL", f"{p}.relationships.layout", "Layer 1 must emit relationships.layout = null")
        mo = (o.get("motion") or {}).get("state", "none") if isinstance(o.get("motion"), dict) else "none"
        if mo not in spec.MOTION_STATES:
            E("L1.OBJ.MOTION", f"{p}.motion.state", f"{mo!r} invalid")
        ia = (o.get("interaction") or {}).get("state", "none") if isinstance(o.get("interaction"), dict) else "none"
        if ia not in spec.INTERACTION_STATES:
            E("L1.OBJ.INTERACTION", f"{p}.interaction.state", f"{ia!r} invalid")
    by_id = {oid: o for (_, oid), o in ids.items()}
    for (_, oid), o in ids.items():
        par, kids = o.get("parent"), o.get("children")
        if par is not None and par not in by_id:
            E("L1.CONTAIN", f"{oid}.parent", f"parent {par!r} does not exist")
        for k in kids or []:
            if k not in by_id:
                E("L1.CONTAIN", f"{oid}.children", f"child {k!r} does not exist")
            elif by_id[k].get("parent") != oid:
                W("L1.CONTAIN", f"{oid}.children", f"{k!r} listed as child but its parent is {by_id[k].get('parent')!r}")
    for path, d in walk(l1):
        for k in d:
            if k in spec.L1_FORBIDDEN_KEYS:
                E("L1.FORBIDDEN", f"{path}.{k}", "key belongs to Layer 2+; Layer 1 may not emit it")
    for i, a in enumerate(l1.get("animations") or l1.get("ANIMATIONS") or []):
        p = f"$.animations[{i}]"
        if not isinstance(a, dict) or not a.get("object_ids") or a.get("type") not in spec.ANIMATION_TYPES:
            E("L1.ANIM", p, "animation needs object_ids and a valid type; null-filled placeholders are forbidden")
        else:
            for oid in a["object_ids"]:
                if oid not in by_id:
                    E("L1.ANIM", p, f"object_ids references unknown {oid!r}")
    rep = l1.get("report", l1.get("REPORT"))
    if isinstance(rep, dict):
        low = {oid for oid, o in by_id.items() if _num(o.get("confidence")) and o["confidence"] < 0.60}
        listed = set(strings(rep.get("uncertain_regions", [])))
        for oid in sorted(low - listed):
            E("L1.REPORT.UNCERTAIN", "$.report.uncertain_regions", f"{oid} has confidence < 0.60 but is not listed")
        if rep.get("objects_detected") is not None and rep["objects_detected"] != len(raw_objs):
            E("L1.REPORT.COUNT", "$.report.objects_detected", f"{rep['objects_detected']} != {len(raw_objs)} objects")
        if not isinstance(rep.get("compiler_ready"), bool):
            E("L1.REPORT.READY", "$.report.compiler_ready", "boolean compiler_ready is required (Layer 3 gate reads it)")
    else:
        E("L1.REPORT", "$.report", "Layer 1 REPORT block is required (extraction quality report)")
    if isinstance(report, dict) and report.get("objects_extracted") not in (None, len(raw_objs)):
        E("L1.REPORT.COUNT", "REPORT.objects_extracted", f"{report['objects_extracted']} != {len(raw_objs)}")
    return f


# ----------------------------------------------------------------------------------------------------------- Layer 2
def check_l2(l1: dict, l2: Any) -> list[Finding]:
    f: list[Finding] = []
    E = lambda r, p, m: f.append(Finding(r, "error", p, m))  # noqa: E731
    W = lambda r, p, m: f.append(Finding(r, "warn", p, m))  # noqa: E731
    if not isinstance(l2, dict):
        return [Finding("L2.SHAPE", "error", "$", "Layer 2 must be a JSON object")]
    objs = l1_objects(l1)
    l1_ids = {o.get("id") for o in objs}
    defined: set[str] = set()
    for path, d in walk(l2):
        for k in spec.L2_STRUCTURAL_ID_KEYS:
            if isinstance(d.get(k), str):
                defined.add(d[k])
        if isinstance(d.get("id"), str) and d["id"] not in l1_ids:
            E("L2.FREEZE.BARE_ID", path, f"bare 'id' {d['id']!r} is not a Layer 1 id; Layer 2 must use region_id/"
                                         "component_id/template_id/instance_id for ids it defines")
        for k in d:
            if k in spec.NO_BUSINESS_KEYS:
                E("L2.NO_BIZ", f"{path}.{k}", "business/intent inference is forbidden")
    for c in sorted(defined & l1_ids):
        E("L2.FREEZE.COLLISION", "$", f"Layer 2 defines id {c!r} that already exists in Layer 1")
    seen = set(strings(l2))
    gone = sorted(i for i in l1_ids if i not in seen)
    if gone:
        E("L2.SURVIVE", "$", f"Layer 1 objects silently dropped: {gone[:10]}{'...' if len(gone) > 10 else ''}")
    ref_keys = {"children", "instances", "alignment_group", "elements", "members", "object_ids", "objects", "parent"}
    for path, d in walk(l2):
        for k in ref_keys & set(d):
            for s in strings(d[k]):
                if ID_RE.match(s) and s not in l1_ids and s not in defined:
                    E("L2.REFS", f"{path}.{k}", f"dangling reference {s!r}")
    for i, r in enumerate(l2.get("regions") or []):
        ev = r.get("clustering_evidence") if isinstance(r, dict) else None
        if not isinstance(ev, list) or not ev:
            E("L2.EVIDENCE", f"$.regions[{i}].clustering_evidence", "non-empty clustering_evidence required")
        elif len(ev) < 2:
            W("L2.EVIDENCE", f"$.regions[{i}].clustering_evidence", "boundary needs >=2 corroborating signals")
    for path, d in walk(l2):
        if isinstance(d.get("layout"), str):
            if d["layout"] not in spec.L2_LAYOUTS:
                E("L2.LAYOUT", f"{path}.layout", f"{d['layout']!r} not an allowed layout")
            if not _nonempty(d.get("layout_evidence")):
                E("L2.LAYOUT.EVIDENCE", f"{path}.layout_evidence", "every layout claim needs evidence")
        if isinstance(d.get("region_type"), str) and d["region_type"] not in spec.L2_REGION_TYPES:
            E("L2.REGION_TYPE", f"{path}.region_type", f"{d['region_type']!r} invalid")
        if d.get("pattern") is not None and "pattern" in d:
            if d["pattern"] not in spec.L2_PATTERNS:
                E("L2.PATTERN", f"{path}.pattern", f"{d['pattern']!r} invalid")
            elif not _nonempty(d.get("pattern_evidence")):
                E("L2.PATTERN.EVIDENCE", f"{path}.pattern_evidence", "pattern needs evidence")
    z = l2.get("z_stack")
    if isinstance(z, dict) and z.get("order"):
        bad = [x for x in z["order"] if x not in spec.L2_Z_LEVELS]
        if bad:
            E("L2.ZSTACK", "$.z_stack.order", f"invalid levels {bad}")
        if not _nonempty(z.get("evidence")):
            E("L2.ZSTACK.EVIDENCE", "$.z_stack.evidence", "z-stack claim needs evidence")
    conf = l2.get("confidence")
    if isinstance(conf, dict):
        low = min((o["confidence"] for o in objs if _num(o.get("confidence"))), default=None)
        proj = conf.get("project")
        if not _num(proj) or not (0 <= proj <= 1):
            E("L2.CONF", "$.confidence.project", f"must be in [0,1], got {proj!r}")
        else:
            screens = [v for v in (conf.get("screens") or {}).values() if _num(v)]
            if screens and proj > min(screens) + 1e-9:
                E("L2.CONF.MIN", "$.confidence.project", "project confidence must be the MIN of its screens")
            if low is not None and proj > low + 1e-9:
                E("L2.CONF.UPGRADE", "$.confidence.project", f"{proj} > weakest Layer 1 object {low}: silent upgrade")
    else:
        E("L2.CONF", "$.confidence", "confidence propagation block required")
    n_screens = len(l2.get("screens") or [])
    if l2.get("navigation_graph") not in (None, {}) and n_screens <= 1 and _static(l1):
        E("L2.NAV", "$.navigation_graph", "must be null/omitted for single-screen input")
    if l2.get("state_graph") is not None and _static(l1):
        E("L2.STATE", "$.state_graph", "must be null/omitted for non-video input")
    has_evidence = any((o.get("motion") or {}).get("state", "none") not in ("none", "unobservable") or
                       (o.get("interaction") or {}).get("state", "none") not in ("none", "unobservable") for o in objs) \
        or bool(l1.get("animations") or l1.get("ANIMATIONS"))
    edges = (l2.get("interaction_graph") or {}).get("edges") or []
    if edges and not has_evidence:
        E("L2.INTERACTION", "$.interaction_graph.edges", "edges present but Layer 1 holds no interaction/motion evidence")
    for i, e in enumerate(edges):
        if not _nonempty(e.get("evidence")):
            E("L2.INTERACTION.EVIDENCE", f"$.interaction_graph.edges[{i}]", "edge without evidence")
    for i, t in enumerate(l2.get("component_templates") or []):
        if not isinstance(t, dict) or not t.get("structure"):
            E("L2.TEMPLATE", f"$.component_templates[{i}]", "template needs a non-empty structure")
        elif len(t.get("instances") or []) < 2:
            W("L2.TEMPLATE", f"$.component_templates[{i}]", "template should have 2+ instances")
    vr = l2.get("validation_report")
    if not isinstance(vr, dict) or vr.get("status") not in ("pass", "fail"):
        E("L2.VALIDATION", "$.validation_report.status", "must be pass|fail")
    else:
        fails = vr.get("failures") or []
        bools = [k for k, v in vr.items() if isinstance(v, bool) and not v]
        if vr["status"] == "pass" and (fails or bools):
            E("L2.VALIDATION", "$.validation_report", f"status=pass but failures={fails} false_checks={bools}")
        if vr["status"] == "fail" and not fails:
            E("L2.VALIDATION", "$.validation_report", "status=fail but no failures listed")
    return f


# ------------------------------------------------------------------------------------------- Layers 3 / 4.1 / 4.2
LAYER_WRAPPERS = {"L3": "DESIGN_SYSTEM_IR", "L41": "CANONICAL_SEMANTIC_IR", "L42": "CANONICAL_EXECUTION_IR"}


def _halt_block(f: list, p: str, blk: dict, is_validation: bool) -> None:
    for k in spec.HALT_KEYS:
        if k not in blk:
            f.append(Finding("C.HALT", "error", p, f"missing {k} (compiler stop reason must always be present)"))
    status = blk.get("gate_result", blk.get("status"))
    if status == "pass" and any(blk.get(k) is not None for k in spec.HALT_KEYS):
        f.append(Finding("C.HALT", "error", p, "pass but halt_* fields are not null"))
    if status == "fail" and not blk.get("halt_reason"):
        f.append(Finding("C.HALT", "error", p, "fail without halt_reason"))
    if is_validation and status == "pass" and blk.get("failures"):
        f.append(Finding("C.HALT", "error", p, "status=pass but failures not empty"))
    if is_validation and status == "pass":
        bad = [k for k, v in blk.items() if isinstance(v, bool) and not v and k != "compiler_ready"]
        if bad:
            f.append(Finding("C.HALT", "error", p, f"status=pass but checks are false: {bad}"))


def check_ir(layer: str, body: Any, ctx: dict | None = None) -> list[Finding]:
    """layer in {'L3','L41','L42'}. ctx: {'l1_ids','l2_ids','static'} enables cross-layer reference checks."""
    ctx = ctx or {}
    f: list[Finding] = []
    E = lambda r, p, m: f.append(Finding(r, "error", p, m))  # noqa: E731
    W = lambda r, p, m: f.append(Finding(r, "warn", p, m))  # noqa: E731
    if not isinstance(body, dict):
        return [Finding(f"{layer}.SHAPE", "error", "$", "IR body must be a JSON object")]
    root = _unwrap(body, LAYER_WRAPPERS[layer])
    gate = root.get("VALIDATION_GATE", root.get("validation_gate"))
    halted = isinstance(gate, dict) and gate.get("gate_result") == "fail"
    if isinstance(gate, dict):
        _halt_block(f, "$.VALIDATION_GATE", gate, False)
        if gate.get("gate_result") not in ("pass", "fail"):
            E(f"{layer}.GATE", "$.VALIDATION_GATE.gate_result", "must be pass|fail")
    elif layer != "L3" or "validation_failed" not in root:
        E(f"{layer}.GATE", "$.VALIDATION_GATE", "input validation gate block required")
    ver = root.get("VERSIONING")
    if not isinstance(ver, dict) or any(k not in ver for k in spec.VERSIONING_KEYS):
        E("C.VERSIONING", "$.VERSIONING", f"must contain {spec.VERSIONING_KEYS} (null allowed only when halted)")
    elif not halted and any(ver[k] is None for k in spec.VERSIONING_KEYS):
        E("C.VERSIONING", "$.VERSIONING", "null version values only allowed when execution halted")
    if halted:
        return f
    chain = root.get("HASH_CHAIN") or root.get("hash_chain") or (root.get("DESIGN_HASH") or {}).get("hash_chain")
    if not isinstance(chain, dict) or any(k not in chain for k in spec.CHAIN_KEYS):
        E("C.HASH_CHAIN", "$.HASH_CHAIN", f"must contain {spec.CHAIN_KEYS}")
    else:
        if chain.get("algorithm") != "sha256":
            E("C.HASH_CHAIN", "$.HASH_CHAIN.algorithm", "must be sha256")
        for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
            if not isinstance(chain.get(k), str) or not HEX64.match(chain[k]):
                E("C.HASH_CHAIN", f"$.HASH_CHAIN.{k}", "must be a real 64-hex sha256 (run the seal step)")
    for path, d in walk(body):
        for k in d:
            if k == "DEPENDENCY_GRAPH":
                E("C.NAMING", f"{path}.{k}", "bare DEPENDENCY_GRAPH is forbidden: use DESIGN_/RUNTIME_DEPENDENCY_GRAPH")
            if k in spec.NO_BUSINESS_KEYS:
                E("C.NO_INTENT", f"{path}.{k}", "business/intent fields are forbidden at every layer")
        if isinstance(d.get("records"), list):
            if d.get("ordering") not in spec.ORDERINGS:
                E("C.ORDERING", path, f"collection must declare ordering in {spec.ORDERINGS}")
            elif d["ordering"] == "sort_by_id" and len(d["records"]) > 1:
                for key in ("id", "object_id", "component_id", "component", "node", "canonical_id", "entity_id",
                            "family_id", "transition_id", "guard_id", "effect_id", "flow_id", "template_id"):
                    vals = [r.get(key) for r in d["records"] if isinstance(r, dict)]
                    if len(vals) == len(d["records"]) and all(isinstance(v, str) for v in vals):
                        if vals != sorted(vals):
                            W("C.ORDERING", path, f"records not sorted by {key}")
                        break
    val = root.get("VALIDATION", root.get("validation"))
    if isinstance(val, dict):
        _halt_block(f, "$.VALIDATION", val, True)
    elif layer in ("L41", "L42"):
        E(f"{layer}.VALIDATION", "$.VALIDATION", "terminal validation block required")
    static = ctx.get("static", True)
    known = set(ctx.get("l1_ids", set())) | set(ctx.get("l2_ids", set()))
    if layer == "L3":
        reg = root.get("TOKEN_REGISTRY") or {}
        token_ids: set[str] = set()
        for key, lst in (reg.items() if isinstance(reg, dict) else []):
            for i, t in enumerate(lst if isinstance(lst, list) else []):
                if not isinstance(t, dict):
                    continue
                token_ids.add(str(t.get("id")))
                ok = (_num(t.get("repeated_instances")) and t["repeated_instances"] >= 2) or \
                     (_num(t.get("confidence")) and t["confidence"] >= 0.90)
                if not ok:
                    E("L3.TOKEN_PROMOTION", f"$.TOKEN_REGISTRY.{key}[{i}]", "needs >=2 instances or confidence >= 0.90")
        for i, c in enumerate(root.get("COMPONENT_DNA") or []):
            bad = [s for s in (c.get("states") or []) if s not in spec.CANONICAL_STATES] if isinstance(c, dict) else []
            if bad:
                E("L3.STATES", f"$.COMPONENT_DNA[{i}].states", f"non-canonical states {bad}")
        rs = root.get("READY_SCORE")
        if not isinstance(rs, dict) or any(not isinstance(rs.get(k), bool) for k in spec.READY_KEYS):
            E("L3.READY", "$.READY_SCORE", f"must hold booleans {spec.READY_KEYS}")
        if static and section(root, "MOTION_TOKENS"):
            E("L3.MOTION", "$.MOTION_TOKENS", "static input has no animation evidence; motion tokens must be empty")
        rr = root.get("RESPONSIVE_RULES")
        if static and isinstance(rr, dict) and any(rr.values()):
            E("L3.RESPONSIVE", "$.RESPONSIVE_RULES", "never infer responsive rules from a single viewport")
        for i, r in enumerate(section(root.get("DESIGN_DEPENDENCY_GRAPH") and root, "DESIGN_DEPENDENCY_GRAPH")):
            for dep in r.get("depends_on") or []:
                tid = str(dep).split(":")[-1]
                if token_ids and tid not in token_ids:
                    E("L3.DEPENDENCY", f"$.DESIGN_DEPENDENCY_GRAPH[{i}]", f"unknown token {tid!r}")
    if layer == "L41":
        for path, d in walk(root):
            if isinstance(d.get("taxonomy"), str) and d["taxonomy"] not in spec.TAXONOMY:
                E("L41.TAXONOMY", f"{path}.taxonomy", f"{d['taxonomy']!r} not in the 19 canonical values")
            cc = d.get("canonical_chain")
            if isinstance(cc, dict):
                if not _nonempty(cc.get("primitive")) or cc.get("taxonomy") not in spec.TAXONOMY \
                        or cc.get("control_class") not in spec.CONTROL_CLASSES:
                    E("L41.CHAIN", f"{path}.canonical_chain", "needs primitive + taxonomy(19) + control_class")
        for i, c in enumerate(section(root, "INTERACTION_CONTRACTS")):
            if c.get("action") not in spec.INTERACTION_ACTIONS:
                E("L41.ACTION", f"$.interaction_contracts[{i}].action", f"{c.get('action')!r} not an allowed action")
            if not _nonempty(c.get("evidence")):
                E("L41.EVIDENCE", f"$.interaction_contracts[{i}]", "contract without evidence")
        for i, r in enumerate(section(root, "SEMANTIC_RELATIONSHIPS")):
            if r.get("type") not in spec.REL_TYPES or not _nonempty(r.get("evidence")):
                E("L41.REL", f"$.semantic_relationships[{i}]", "valid type + evidence required")
        for i, r in enumerate(section(root, "LAYOUT_MODEL")):
            if r.get("flow") not in spec.LAYOUT_FLOWS:
                E("L41.LAYOUT", f"$.layout_model[{i}].flow", f"{r.get('flow')!r} invalid")
        for i, r in enumerate(section(root, "primitive_registry", "SIGNATURE")):
            if not _nonempty(r.get("derived_from")):
                E("L41.PROVENANCE", f"$.primitive_registry[{i}]", "derived_from is mandatory")
        if isinstance(val, dict) and val.get("every_taxonomy_valid") is True:
            bad = [d["taxonomy"] for _, d in walk(root) if isinstance(d.get("taxonomy"), str) and d["taxonomy"] not in spec.TAXONOMY]
            if bad:
                E("L41.VALIDATION", "$.VALIDATION.every_taxonomy_valid", f"claims true but found {bad}")
    if layer == "L42":
        ev_ids: set[str] = set()
        for i, e in enumerate(section(root, "EVENTS")):
            eid = e.get("id")
            if eid in ev_ids:
                E("L42.EVENT", f"$.EVENTS[{i}]", f"duplicate event id {eid!r}")
            ev_ids.add(eid)
            if e.get("type") not in spec.EVENT_TYPES:
                E("L42.EVENT", f"$.EVENTS[{i}].type", f"{e.get('type')!r} not in controlled vocabulary")
            if not _nonempty(e.get("evidence")):
                E("L42.EVENT.EVIDENCE", f"$.EVENTS[{i}]", "event without upstream evidence")
            if known and e.get("source") not in known:
                E("L42.EVENT.SOURCE", f"$.EVENTS[{i}].source", f"unknown source {e.get('source')!r}")
        for i, t in enumerate(section(root, "TRANSITIONS")):
            if t.get("trigger") not in ev_ids:
                E("L42.TRANSITION", f"$.TRANSITIONS[{i}].trigger", f"trigger {t.get('trigger')!r} is not an event (orphan)")
            for k in ("from", "to"):
                if t.get(k) not in spec.CANONICAL_STATES:
                    E("L42.STATES", f"$.TRANSITIONS[{i}].{k}", f"{t.get(k)!r} is not a canonical state")
        for path, d in walk(root):
            for k in ("allowed_states", "terminal_states"):
                bad = [s for s in d.get(k, []) if s not in spec.CANONICAL_STATES] if isinstance(d.get(k), list) else []
                if bad:
                    E("L42.STATES", f"{path}.{k}", f"non-canonical states {bad}")
        for i, r in enumerate(section(root.get("FLOW_GRAPH") or {}, "flows")):
            if r.get("type") not in spec.FLOW_TYPES:
                E("L42.FLOW", f"$.FLOW_GRAPH.flows[{i}].type", f"{r.get('type')!r} invalid")
            for s in r.get("steps") or []:
                if s not in ev_ids:
                    E("L42.FLOW", f"$.FLOW_GRAPH.flows[{i}].steps", f"unknown event {s!r}")
        for i, r in enumerate(section(root, "RUNTIME_CONSTRAINTS", "CONSTRAINT")):
            if r.get("type") not in spec.CONSTRAINT_TYPES:
                E("L42.CONSTRAINT", f"$.RUNTIME_CONSTRAINTS[{i}].type", f"{r.get('type')!r} invalid")
        if static and (section(root, "TIMING") or section(root, "ERROR_GRAPH")):
            E("L42.STATIC", "$.TIMING|ERROR_GRAPH", "static input: TIMING and ERROR_GRAPH must be empty (no observed evidence)")
        nav = root.get("NAVIGATION_GRAPH") or {}
        if static and isinstance(nav, dict) and nav.get("edges"):
            E("L42.NAV", "$.NAVIGATION_GRAPH.edges", "static single-screen input cannot carry navigation edges")
    return f


# ------------------------------------------------------------------------------------------------ bundle-level
def check_bundle12(text: str) -> tuple[list[Finding], dict]:
    tags = parse_tags(text, ("LAYER1_OUTPUT", "LAYER2_OUTPUT", "MANIFEST_UPDATE", "REPORT"))
    f: list[Finding] = []
    l1, e1 = load_body(tags.get("LAYER1_OUTPUT", ""))
    l2, e2 = load_body(tags.get("LAYER2_OUTPUT", ""))
    man, _ = load_body(tags.get("MANIFEST_UPDATE", "")) if "MANIFEST_UPDATE" in tags else (None, None)
    rep, _ = load_body(tags.get("REPORT", "")) if "REPORT" in tags else (None, None)
    if l1 is None or l2 is None:
        return [Finding("B12.PARSE", "error", "$", f"L1 error={e1} L2 error={e2}")], {}
    f += check_l1(l1, rep)
    f += check_l2(l1, l2)
    if not isinstance(man, dict):
        f.append(Finding("B12.MANIFEST", "error", "MANIFEST_UPDATE", "required JSON object missing"))
    else:
        for k in spec.MANIFEST12_KEYS:
            if k not in man:
                f.append(Finding("B12.MANIFEST", "error", "MANIFEST_UPDATE", f"missing required key {k}"))
    return f, {"l1": l1, "l2": l2, "manifest": man, "report": rep}


def verify_chains(bodies: dict[str, dict], l2_disk_hash: str) -> list[Finding]:
    """Recompute every chain value; independent of what the model (or the seal) claimed."""
    f: list[Finding] = []
    in_hash, parent, tuples = l2_disk_hash, l2_disk_hash, []
    names = {"L3": "layer3", "L41": "layer4_1", "L42": "layer4_2"}
    from .bundle import chash
    for layer in ("L3", "L41", "L42"):
        b = bodies.get(layer)
        if b is None:
            continue
        root = _unwrap(b, LAYER_WRAPPERS[layer])
        chain = root.get("HASH_CHAIN") or root.get("hash_chain") or (root.get("DESIGN_HASH") or {}).get("hash_chain") or {}
        out_h = substantive_hash(b)
        tuples.append([names[layer], in_hash, out_h, parent])
        want = {"input_hash": in_hash, "output_hash": out_h, "parent_hash": parent, "pipeline_hash": pipeline_hash(tuples)}
        for k, v in want.items():
            if chain.get(k) != v:
                f.append(Finding("C.CHAIN.VERIFY", "error", f"{names[layer]}.HASH_CHAIN.{k}", f"declared {chain.get(k)!r} != recomputed {v}"))
        parent, in_hash = out_h, chash(b)
    return f
