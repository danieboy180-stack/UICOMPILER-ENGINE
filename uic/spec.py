"""Machine-readable constants lifted from the UI GOD fused prompts (L1+2 v1.1, L3+4.1+4.2 v1.1).

Single source of truth for every enum the prompts define. Checkers import from here; nothing is re-typed elsewhere.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# ---- Layer 1 -------------------------------------------------------------------------------------------------------
L1_ROLES = ("button input text image icon card nav tab list table divider modal dialog drawer menu dropdown tooltip "
            "badge chart form skeleton avatar progress toast other").split()
L1_INPUT_TYPES = "image video screen_recording sketch wireframe figma_export pdf photo desktop".split()
L1_DEVICES = "mobile tablet desktop unknown".split()
MOTION_STATES = "none transition sliding fading scaling bouncing loading_indicator skeleton_state unobservable".split()
INTERACTION_STATES = "none pressed focused hovered expanded dragged selected disabled unobservable".split()
ANIMATION_TYPES = "fade scale slide morph rotation bounce loading skeleton transition navigation parallax".split()
# keys that belong to Layer 2+; Layer 1 must never emit them (CONTRACT - LAYER 1 MAY NOT)
L1_FORBIDDEN_KEYS = {"component_templates", "component_instances", "interaction_graph", "navigation_graph",
                     "state_graph", "layout_graph", "constraint_graph", "region_type", "regions", "intent",
                     "business_intent", "primary_goal", "secondary_goal"}

# ---- Layer 2 -------------------------------------------------------------------------------------------------------
L2_LAYOUTS = ("vertical_stack horizontal_stack grid masonry absolute responsive_container split_view sidebar_layout "
              "docked_footer freeform").split()
L2_PATTERNS = ("navigation_rail app_bar tab_bar settings_list dashboard_grid checkout_flow_layout form_layout "
               "chat_layout gallery_layout").split()
L2_REGION_TYPES = ("header footer sidebar content overlay floating_action_area modal_layer background_layer "
                   "unclassified").split()
L2_Z_LEVELS = "background_layer content floating_action_area toast modal_layer tooltip".split()
L2_STRUCTURAL_ID_KEYS = ("project_id", "screen_id", "scene_id", "region_id", "component_id", "template_id",
                         "instance_id", "state_id", "animation_id")
NO_BUSINESS_KEYS = {"intent", "primary_goal", "secondary_goal", "business_intent", "product_purpose",
                    "app_category", "industry", "target_audience", "business_model"}

# ---- Layers 3 / 4.1 / 4.2 (shared conventions) ---------------------------------------------------------------------
TAXONOMY = ("SCREEN NAVIGATION_HEADER STATUS_BAR ACTION_TRIGGER CONTENT_SECTION SURFACE_CONTAINER FILTER_SELECTOR "
            "INPUT_TRIGGER PROFILE_ENTITY METRIC_DISPLAY TAB_CONTROLLER TEXT_CONTENT HERO_SECTION LIST_CONTAINER "
            "CTA_ZONE MODAL_TRIGGER STATEFUL_SELECTOR SCROLL_CONTAINER FEEDBACK_INDICATOR").split()
CANONICAL_STATES = ("default active pressed focused selected inactive expanded collapsed loading disabled").split()
INTERACTION_ACTIONS = ("navigate open_modal toggle_state expand_content switch_tab submit_selection open_chat "
                       "activate_filter reveal_content start_flow complete_flow validate_input").split()
EVENT_TYPES = ("activate release input_change focus blur select submit scroll drag drop expand collapse open "
               "close").split()
ORDERINGS = "sort_by_id sort_by_reading_order sort_alphabetical".split()
CONTROL_CLASSES = "INTERACTIVE_CONTROL DISPLAY_CONTROL CONTAINER_CONTROL".split()
REL_TYPES = "controls owns references describes labels contains adjacent_to paired_with".split()
LAYOUT_FLOWS = "FLOW STACK GRID ANCHOR".split()
FLOW_TYPES = "required optional interruptible restartable terminal parallel".split()
BEHAVIORAL_PATTERNS = "progressive_disclosure multi_step_flow branching_flow single_action_flow".split()
CONSTRAINT_TYPES = "must_complete_before cannot_execute_with depends_on blocks".split()
VERSIONING_KEYS = ("schema_version", "compiler_version", "layer_version", "ir_version")
CHAIN_KEYS = ("input_hash", "output_hash", "parent_hash", "pipeline_hash", "algorithm")
HALT_KEYS = ("halt_reason", "halt_stage", "blocking_object")
READY_KEYS = ("compiler_ready", "design_system_ready", "token_ready", "component_ready", "motion_ready",
              "responsive_ready")

MANIFEST12_KEYS = ("pipeline_id", "run_id", "image_hash", "layer1_hash", "layer2_hash", "schema_version")
MANIFEST342_KEYS = ("pipeline_id", "run_id", "parent_run_id", "pipeline_version", "schema_version",
                    "compiler_version", "layer3_input_hash", "layer3_output_hash", "layer4_1_input_hash",
                    "layer4_1_output_hash", "layer4_2_input_hash", "layer4_2_output_hash", "pipeline_hash",
                    "timestamp")

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def prompt_fingerprints(prompt_dir: Path | None = None) -> dict[str, str]:
    """sha256 of every prompt file (provenance: which instructions produced a given bundle)."""
    d = Path(prompt_dir) if prompt_dir else PROMPT_DIR
    if not d.is_dir():
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*.md")) if p.is_file()}
