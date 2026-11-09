"""Prompt-conformant sample bundles (what a good model emits), used by tests and `uic demo`.

Hash fields are filled with obviously fake 'LLM-style' values on purpose: the seal step must replace them.
"""
from __future__ import annotations

import json
from typing import Any

FAKE = "f" * 64
DEFAULT_OBJECTS = [
    dict(role="text", x=50, y=20, w=60, h=8, text="Sign in", conf=0.95),
    dict(role="input", x=50, y=40, w=80, h=10, text="Email", conf=0.93),
    dict(role="button", x=50, y=60, w=80, h=10, text="Continue", conf=0.92, bg="#2463EB"),
]


def _obj(role: str, n: int, o: dict) -> dict:
    return {
        "id": f"{role}_{n}", "role": role, "type": None, "parent": None, "children": None, "group": None, "layer": None,
        "position": {"x": o["x"], "y": o["y"]}, "size": {"w": o["w"], "h": o["h"]}, "rotation": None,
        "visibility": "visible", "shape": {"form": "rectangle", "corner_values": None},
        "style": {"bg": o.get("bg"), "color": None, "opacity": None, "blur": None, "gradient": None, "radius": None,
                  "border": None, "stroke": None, "shadow": None, "elevation": None, "mask": None, "pattern": None},
        "typography": {"font_guess": None, "size_guess": None, "weight": None, "line_height": None},
        "image": {"category": None}, "icon": {"name_guess": None},
        "motion": {"state": "none", "animation_ref": None},
        "interaction": {"state": "none", "keyboard_open": None, "cursor_visible": None},
        "relationships": {"above": None, "below": None, "left_of": None, "right_of": None, "aligned_with": None,
                          "distance_to_nearest": None, "overlaps": None, "layout": None, "reading_order": n,
                          "hierarchy_depth": 0},
        "text": {"value": o.get("text"), "truncated": False, "multiline": False, "language": "en",
                 "ocr_confidence": o["conf"]},
        "confidence": o["conf"],
        "evidence": {"shape": "Rectangular control with visible boundary.", "text": "Legible label.", "position": None,
                     "motion": None, "color": None, "relationship": None},
        "source_frame": "screen_0",
    }


def build_sample(objects: list[dict] | None = None, project: str = "proj_0") -> dict[str, Any]:
    specs = objects or DEFAULT_OBJECTS
    counters: dict[str, int] = {}
    objs = []
    for s in specs:
        n = counters.get(s["role"], 0)
        counters[s["role"]] = n + 1
        objs.append(_obj(s["role"], n, s))
    ids = [o["id"] for o in objs]
    low = min(o["confidence"] for o in objs)
    l1 = {
        "input": {"type": "image", "subtype": "mobile", "confidence": 0.99},
        "screen": {"device": "mobile", "orientation": "portrait", "viewport": None, "theme": "light"},
        "objects": objs,
        "report": {"objects_detected": len(objs), "scenes_or_screens_processed": 1, "frames_processed": None,
                   "ocr_quality": "high", "motion_detected": False, "missing_regions": [], "uncertain_regions": [],
                   "confidence_distribution": {"0.90-1.00": len(objs), "0.70-0.89": 0, "0.60-0.69": 0, "<0.60": 0},
                   "warnings": [], "recommendation": "ready as-is", "compiler_ready": True},
    }
    l2 = {
        "project": {"project_id": project, "input_type": "image"},
        "screens": [{"screen_id": "screen_0", "device": "mobile"}], "scenes": [],
        "regions": [{"region_id": "region_0", "region_type": "content", "children": ids,
                     "clustering_evidence": ["consistent horizontal centering across " + ", ".join(ids),
                                             "reading order 0..n follows top-to-bottom stacking"]}],
        "component_templates": [], "component_instances": [],
        "layout_graph": [{"node": "region_0", "layout": "vertical_stack",
                          "layout_evidence": "Objects share x=50 center and increase monotonically in y."}],
        "constraint_graph": [], "interaction_graph": {"edges": []}, "navigation_graph": None, "state_graph": None,
        "z_stack": {"order": ["content"], "evidence": "No overlap or occlusion in Layer 1."},
        "confidence": {"project": low, "screens": {"screen_0": low}, "regions": {"region_0": low}, "components": {}},
        "validation_report": {"no_cycles": True, "single_parent_enforced": True, "no_impossible_overlaps": True,
                              "duplicate_ids_removed": True, "graph_connected": True, "layout_consistent": True,
                              "failures": [], "status": "pass"},
    }
    ver = {"schema_version": "2.1.0", "compiler_version": "1.1.0", "layer_version": "1.1.0", "ir_version": 1}
    chain = {"input_hash": FAKE, "output_hash": FAKE, "parent_hash": FAKE, "pipeline_hash": FAKE, "algorithm": "sha256"}
    gate = {"gate_result": "pass", "halt_reason": None, "halt_stage": None, "blocking_object": None}
    val = {"status": "pass", "failures": [], "halt_reason": None, "halt_stage": None, "blocking_object": None,
           "compiler_ready": True}
    btn = next((o["id"] for o in objs if o["role"] == "button"), ids[0])
    l3 = {
        "VERSIONING": dict(ver),
        "VALIDATION_GATE": {"layer1_compiler_ready": True, "layer2_validation_status": "pass", **gate},
        "TOKEN_REGISTRY": {"ordering": "sort_by_id", "color_tokens": [
            {"id": "color_001", "raw": "#2463EB", "observed_usage": "button_fill", "repeated_instances": 2,
             "confidence": 0.94, "candidate_semantic_roles": ["primary_action"], "selected_role": "primary_action"}],
            "spacing_tokens": [], "radius_tokens": [], "shadow_tokens": [], "opacity_tokens": [], "border_tokens": [],
            "font_tokens": [], "motion_tokens": [], "icon_tokens": [], "size_tokens": []},
        "COLOR_RELATIONSHIPS": {"primary": "color_001", "evidence": "Only filled control uses it."},
        "TYPOGRAPHY_SYSTEM": {"scale": []}, "SPACING_RHYTHM": {"scale": []}, "ICON_REGISTRY": {"sizes": []},
        "MOTION_TOKENS": [],
        "COMPONENT_DNA": [{"component_id": "comp_button_0", "slots": ["label"], "states": ["default"],
                           "variants": ["filled"], "token_usage": ["color_001"], "evidence": "Layer 2 region_0"}],
        "COMPONENT_FAMILIES": [], "REUSE_MAP": {"ordering": "sort_by_id", "records": []},
        "INTERACTION_ROLES": {"ordering": "sort_by_id", "records": [
            {"object": btn, "candidate_semantic_roles": ["primary_action"], "confidence": 0.88,
             "evidence": "Only filled control in the content region.", "derived_from": [btn]}]},
        "RESPONSIVE_RULES": "not_observable", "ACCESSIBILITY": {"contrast": []}, "DENSITY_METRICS": {},
        "STYLE_FINGERPRINT": {}, "DESIGN_DEPENDENCY_GRAPH": {"ordering": "sort_by_id", "records": [
            {"component": "comp_button_0", "depends_on": ["color_token:color_001"]}]},
        "DESIGN_SYSTEM_QUALITY": {}, "DESIGN_HASH": {"input_fields": ["tokens", "components"], "hash_chain": dict(chain)},
        "READY_SCORE": {"compiler_ready": True, "design_system_ready": True, "token_ready": True,
                        "component_ready": True, "motion_ready": False, "responsive_ready": False},
    }
    l41 = {
        "meta": {"source_layers": ["layer1", "layer2", "layer3"], "abstraction_level": "canonical_semantic"},
        "VERSIONING": dict(ver), "HASH_CHAIN": dict(chain), "VALIDATION_GATE": {"layer3_design_system_ready": True, **gate},
        "semantic_graph": {"nodes": [{"object_id": btn, "canonical_chain": {
            "primitive": "PrimaryAction", "taxonomy": "ACTION_TRIGGER", "control_class": "INTERACTIVE_CONTROL"}}],
            "edges": []},
        "canonical_taxonomy": ["ACTION_TRIGGER"], "primitive_registry": [
            {"primitive": "PrimaryAction", "derived_from": "comp_button_0"}],
        "accessibility_graph": [], "interaction_contracts": [], "semantic_relationships": [],
        "semantic_constraints": [], "ui_grammar": {"ordering": "sort_by_reading_order", "records": []},
        "layout_model": [{"node": "region_0", "flow": "STACK", "stack_direction": "vertical",
                          "derived_from": ["region_0"]}],
        "entity_resolution": [], "confidence": [],
        "validation": {"no_cycles": True, "every_interaction_has_target": True, "every_component_has_primitive": True,
                       "every_primitive_has_taxonomy": True, "every_taxonomy_valid": True,
                       "no_duplicate_semantic_ids": True, "semantic_graph_connected": True, **val},
    }
    l42 = {
        "meta": {"source_layer": "layer_4_1", "execution_readiness": True, "runtime_complexity": "low"},
        "VERSIONING": dict(ver), "HASH_CHAIN": dict(chain), "VALIDATION_GATE": {"layer4_1_compiler_ready": True, **gate},
        "EXECUTION_CONTRACTS": {"ordering": "sort_by_id", "records": [
            {"component": btn, "canonical_type": "ACTION_TRIGGER", "allowed_states": ["default"],
             "allowed_events": [], "allowed_transitions": [], "terminal_states": [], "illegal_states": [],
             "evidence": ["Layer4.1 canonical chain"]}]},
        "EVENTS": [], "TRANSITIONS": [], "STATE_GRAPH": {"nodes": [], "edges": [], "lifecycle": []},
        "NAVIGATION_GRAPH": {"nodes": [], "edges": [], "entry_points": [], "exit_points": []},
        "RUNTIME_DEPENDENCY_GRAPH": [], "FLOW_GRAPH": {"flows": [], "behavioral_patterns": [], "flow_validation": {}},
        "SIDE_EFFECTS": [], "RUNTIME_CONSTRAINTS": [], "TIMING": [], "ERROR_GRAPH": [], "CONFIDENCE": {},
        "VALIDATION": {"no_invalid_transitions": True, "no_orphan_states": True, "no_unreachable_states": True,
                       "every_event_resolves": True, "every_transition_legal": True, "navigation_connected": True,
                       "execution_graph_connected": True, "failures": [], "status": "pass",
                       "halt_reason": None, "halt_stage": None, "blocking_object": None, "compiler_ready": True},
    }
    layer_rep = {k: {"layer_version": "1.1.0", "schema_version": "2.1.0", "compiler_version": "1.1.0", "ir_version": 1,
                     "status": "pass", "input_hash": FAKE, "output_hash": FAKE, "parent_hash": FAKE,
                     "compiler_ready": True} for k in ("layer3", "layer4_1", "layer4_2")}
    return {
        "l1": l1, "l2": l2, "l3": l3, "l41": l41, "l42": l42,
        "manifest12": {"pipeline_id": "ui_god", "run_id": "llm-made-up", "image_hash": FAKE, "layer1_hash": FAKE,
                       "layer2_hash": FAKE, "schema_version": "v2.1"},
        "report12": {"layer": "UI_GOD_LAYER1+2_FUSED", "schema_version": "v2.1", "objects_extracted": len(objs),
                     "warnings": []},
        "manifest342": {"pipeline_id": "ui_god", "run_id": "llm-made-up", "parent_run_id": "llm-made-up",
                        "pipeline_version": "1.1", "schema_version": "v2.1", "compiler_version": "1.1.0",
                        **{f"{k}_{s}": FAKE for k in ("layer3", "layer4_1", "layer4_2") for s in ("input_hash", "output_hash")},
                        "pipeline_hash": FAKE, "timestamp": "2026-11-09T00:00:00Z"},
        "report342": {"pipeline": "UI", "pipeline_id": "ui_god", "run_id": "llm-made-up", "layers": layer_rep,
                      "next_layer": "UI_GOD_LAYER4_3", "warnings": [], "halt_reason": None, "halt_stage": None,
                      "blocking_object": None},
    }


def _t(tag: str, body: Any) -> str:
    return f"<{tag}>\n{json.dumps(body, indent=2)}\n</{tag}>\n"


def text12(s: dict) -> str:
    return _t("LAYER1_OUTPUT", s["l1"]) + _t("LAYER2_OUTPUT", s["l2"]) + _t("MANIFEST_UPDATE", s["manifest12"]) + _t("REPORT", s["report12"])


def text342(s: dict) -> str:
    return (_t("LAYER3_OUTPUT", s["l3"]) + _t("LAYER4_1_OUTPUT", s["l41"]) + _t("LAYER4_2_OUTPUT", s["l42"]) +
            _t("MANIFEST_UPDATE", s["manifest342"]) + _t("REPORT", s["report342"]))


def run_sample(root, sample: dict, project: str, source: bytes, export_dir=None) -> int:
    """Drive the full lane (lint -> seal -> ingest -> pipeline [-> export]) for a sample; returns the exit code."""
    from pathlib import Path

    from .cli import run_lane
    root = Path(root)
    work = root / "_inputs"
    work.mkdir(parents=True, exist_ok=True)
    (work / "source.png").write_bytes(source)
    (work / "l12.txt").write_text(text12(sample), encoding="utf-8")
    (work / "l342.txt").write_text(text342(sample), encoding="utf-8")
    return run_lane(root, work / "source.png", work / "l12.txt", work / "l342.txt", project, export_dir)
