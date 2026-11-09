"""uic.check: every prompt rule has a positive baseline and at least one mutation that must trip it."""
import copy

import pytest

from uic import check, fixtures as fx


def _ctx(s):
    return {"l1_ids": {o["id"] for o in s["l1"]["objects"]}, "l2_ids": {"region_0", "screen_0"}, "static": True}


def _run(s, kind):
    if kind == "L1":
        return check.check_l1(s["l1"], s["report12"])
    if kind == "L2":
        return check.check_l2(s["l1"], s["l2"])
    return check.check_ir(kind, s[{"L3": "l3", "L41": "l41", "L42": "l42"}[kind]], _ctx(s))


def test_conformant_sample_has_zero_findings_at_every_layer():
    s = fx.build_sample()
    for kind in ("L1", "L2", "L3", "L41", "L42"):
        assert _run(s, kind) == [], kind


def _o(s, i=0):
    return s["l1"]["objects"][i]


def _set(d, path, val):
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = val


MUTATIONS = [
    # (rule, kind, mutator)
    ("L1.OBJ.ID", "L1", lambda s: _o(s).update(id="btn_0")),
    ("L1.OBJ.ROLE", "L1", lambda s: _o(s).update(role="widget")),
    ("L1.OBJ.LAYOUT_NULL", "L1", lambda s: _o(s)["relationships"].update(layout="grid")),
    ("L1.OBJ.CONF_CAP", "L1", lambda s: _o(s).update(position=None)),
    ("L1.OBJ.TEXT_SEP", "L1", lambda s: _o(s)["text"].update(value="A \u00b7 B")),
    ("L1.OBJ.SOURCE_FRAME", "L1", lambda s: _o(s).pop("source_frame")),
    ("L1.OBJ.GEOMETRY", "L1", lambda s: _o(s)["size"].update(w=140)),
    ("L1.OBJ.MOTION", "L1", lambda s: _o(s)["motion"].update(state="wiggling")),
    ("L1.FORBIDDEN", "L1", lambda s: s["l1"].update(regions=[])),
    ("L1.REPORT.UNCERTAIN", "L1", lambda s: _o(s).update(confidence=0.5)),
    ("L1.REPORT", "L1", lambda s: s["l1"].pop("report")),
    ("L1.CONTAIN", "L1", lambda s: _o(s).update(parent="card_7")),
    ("L1.ANIM", "L1", lambda s: s["l1"].update(animations=[{"animation_id": "anim_0", "object_ids": [], "type": "fade"}])),
    ("L2.FREEZE.BARE_ID", "L2", lambda s: s["l2"]["regions"][0].update(id="region_0")),
    ("L2.FREEZE.COLLISION", "L2", lambda s: s["l2"]["regions"][0].update(region_id="text_0")),
    ("L2.SURVIVE", "L2", lambda s: s["l2"]["regions"][0]["children"].pop()),
    ("L2.REFS", "L2", lambda s: s["l2"]["regions"][0]["children"].append("button_9")),
    ("L2.CONF.UPGRADE", "L2", lambda s: s["l2"]["confidence"].update(project=0.99)),
    ("L2.CONF.MIN", "L2", lambda s: s["l2"]["confidence"].update(screens={"screen_0": 0.5})),
    ("L2.INTERACTION", "L2", lambda s: s["l2"]["interaction_graph"].update(edges=[{"source": "button_0", "action": "opens", "evidence": "x"}])),
    ("L2.LAYOUT", "L2", lambda s: s["l2"]["layout_graph"][0].update(layout="flexbox")),
    ("L2.LAYOUT.EVIDENCE", "L2", lambda s: s["l2"]["layout_graph"][0].update(layout_evidence="")),
    ("L2.NAV", "L2", lambda s: s["l2"].update(navigation_graph={"edges": [1]})),
    ("L2.STATE", "L2", lambda s: s["l2"].update(state_graph={"states": []})),
    ("L2.NO_BIZ", "L2", lambda s: s["l2"]["project"].update(industry="fintech")),
    ("L2.VALIDATION", "L2", lambda s: s["l2"]["validation_report"].update(failures=["cycle"])),
    ("L2.EVIDENCE", "L2", lambda s: s["l2"]["regions"][0].update(clustering_evidence=[])),
    ("L2.REGION_TYPE", "L2", lambda s: s["l2"]["regions"][0].update(region_type="hero")),
    ("L2.ZSTACK", "L2", lambda s: s["l2"]["z_stack"].update(order=["sky"])),
    ("C.VERSIONING", "L3", lambda s: s["l3"]["VERSIONING"].pop("ir_version")),
    ("C.HASH_CHAIN", "L3", lambda s: s["l3"]["DESIGN_HASH"]["hash_chain"].update(algorithm="md5")),
    ("C.HASH_CHAIN", "L41", lambda s: s["l41"]["HASH_CHAIN"].update(output_hash="abc")),
    ("C.NAMING", "L42", lambda s: s["l42"].update(DEPENDENCY_GRAPH=[])),
    ("C.NO_INTENT", "L41", lambda s: s["l41"]["meta"].update(intent="sell things")),
    ("C.ORDERING", "L3", lambda s: s["l3"]["REUSE_MAP"].pop("ordering")),
    ("C.HALT", "L42", lambda s: s["l42"]["VALIDATION"].pop("halt_stage")),
    ("C.HALT", "L41", lambda s: s["l41"]["VALIDATION_GATE"].update(halt_reason="boom")),
    ("L3.TOKEN_PROMOTION", "L3", lambda s: s["l3"]["TOKEN_REGISTRY"]["color_tokens"][0].update(repeated_instances=1, confidence=0.5)),
    ("L3.STATES", "L3", lambda s: s["l3"]["COMPONENT_DNA"][0].update(states=["default", "hovering"])),
    ("L3.READY", "L3", lambda s: s["l3"]["READY_SCORE"].pop("token_ready")),
    ("L3.RESPONSIVE", "L3", lambda s: s["l3"].update(RESPONSIVE_RULES={"breakpoints": [768]})),
    ("L3.MOTION", "L3", lambda s: s["l3"].update(MOTION_TOKENS=[{"id": "motion_001"}])),
    ("L3.DEPENDENCY", "L3", lambda s: s["l3"]["DESIGN_DEPENDENCY_GRAPH"]["records"][0].update(depends_on=["color_token:color_999"])),
    ("L41.TAXONOMY", "L41", lambda s: s["l41"]["semantic_graph"]["nodes"][0]["canonical_chain"].update(taxonomy="CART_BUTTON")),
    ("L41.CHAIN", "L41", lambda s: s["l41"]["semantic_graph"]["nodes"][0]["canonical_chain"].update(control_class="BUTTONISH")),
    ("L41.ACTION", "L41", lambda s: s["l41"].update(interaction_contracts=[{"trigger": "button_0", "action": "buy_now", "evidence": ["x"]}])),
    ("L41.REL", "L41", lambda s: s["l41"].update(semantic_relationships=[{"type": "likes", "source": "a", "target": "b", "evidence": ["x"]}])),
    ("L41.LAYOUT", "L41", lambda s: s["l41"]["layout_model"][0].update(flow="FLEX")),
    ("L41.PROVENANCE", "L41", lambda s: s["l41"]["primitive_registry"][0].pop("derived_from")),
    ("L42.EVENT", "L42", lambda s: s["l42"].update(EVENTS=[{"id": "event_0", "type": "hover", "source": "button_0", "evidence": ["x"]}])),
    ("L42.EVENT.EVIDENCE", "L42", lambda s: s["l42"].update(EVENTS=[{"id": "event_0", "type": "activate", "source": "button_0"}])),
    ("L42.TRANSITION", "L42", lambda s: s["l42"].update(TRANSITIONS=[{"transition_id": "trans_0", "from": "default", "to": "pressed", "trigger": "event_9"}])),
    ("L42.STATES", "L42", lambda s: s["l42"]["EXECUTION_CONTRACTS"]["records"][0].update(allowed_states=["default", "hovering"])),
    ("L42.STATIC", "L42", lambda s: s["l42"].update(TIMING=[{"transition_ref": "trans_0", "duration": 300}])),
    ("L42.NAV", "L42", lambda s: s["l42"]["NAVIGATION_GRAPH"].update(edges=[{"from": "screen_0", "to": "screen_1"}])),
    ("L42.FLOW", "L42", lambda s: s["l42"]["FLOW_GRAPH"].update(flows=[{"flow_id": "flow_0", "type": "required", "steps": ["event_9"]}])),
    ("L42.CONSTRAINT", "L42", lambda s: s["l42"].update(RUNTIME_CONSTRAINTS=[{"type": "hates", "source": "a", "target": "b"}])),
]


@pytest.mark.parametrize("rule,kind,mutator", MUTATIONS, ids=[f"{r}-{k}-{i}" for i, (r, k, _) in enumerate(MUTATIONS)])
def test_each_prompt_rule_trips_on_its_violation(rule, kind, mutator):
    s = copy.deepcopy(fx.build_sample())
    mutator(s)
    hits = [f for f in _run(s, kind) if f.rule == rule]
    assert hits, f"{rule} did not fire; got {[str(f) for f in _run(s, kind)]}"
    assert any(f.level == "error" for f in hits)


def test_sketch_input_caps_confidence_at_060():
    s = copy.deepcopy(fx.build_sample())
    s["l1"]["input"]["type"] = "sketch"
    assert any(f.rule == "L1.OBJ.CONF_CAP" for f in check.check_l1(s["l1"], s["report12"]))


def test_halted_layer_skips_body_checks_but_still_demands_halt_fields():
    halted = {"VALIDATION_GATE": {"gate_result": "fail", "halt_reason": "L1 not ready", "halt_stage": "Layer3.Stage0",
                                  "blocking_object": None},
              "VERSIONING": {"schema_version": None, "compiler_version": None, "layer_version": None, "ir_version": None}}
    assert check.check_ir("L3", halted) == []
    del halted["VALIDATION_GATE"]["halt_stage"]
    assert [f.rule for f in check.check_ir("L3", halted)] == ["C.HALT"]


def test_check_functions_do_not_mutate_their_input():
    s = fx.build_sample()
    before = copy.deepcopy(s)
    for kind in ("L1", "L2", "L3", "L41", "L42"):
        _run(s, kind)
    assert s == before
