UI GOD IR — FUSED LAYER 1+2 v1.1

EXTRACTION + STRUCTURAL SYNTHESIS, SINGLE PASS

---

FUSION NOTE

(Why this exists. Not part of model-facing execution logic.)

Layer 2 was originally specified to consume Layer 1's completed output, never the raw image. Its INPUT section defines the full Layer 1 object schema as the only required primary input; the image is optional/supporting at most.

Therefore Layer 1 and Layer 2 do not require two separate model calls or two separate image transmissions. Layer 2 is pure JSON→JSON structural reasoning that can execute in the same context window immediately after Layer 1 produces its output, with no information loss.

This document fuses both stages into one prompt, one image transmission, and one response.

Nothing in the intended stage order, extraction logic, hierarchy model, or structural reasoning has been removed. Only the call boundary is fused.

Pipeline

image | video | sketch | wireframe | figma_export | pdf | photo | desktop_capture
        ↓
input classification
        ↓
scene/canvas segmentation
        ↓
object extraction
        ↓
timeline, if motion exists
        ↓
LAYER 1 — CCIR
        ↓
FREEZE
        ↓
clustering
        ↓
hierarchy
        ↓
components
        ↓
layout
        ↓
constraints
        ↓
interaction/state/navigation graphs
        ↓
validation
        ↓
LAYER 2 — CSIR
        ↓
output JSON

---

EXECUTION ORDER — STRICT

Internal processing MUST follow this exact sequence.

IMAGE / SOURCE
     ↓
[internal visual processing]
     ↓
LAYER 1 — EXTRACTION
     ↓
[freeze finalized Layer 1 object graph]
     ↓
LAYER 2 — STRUCTURAL SYNTHESIS
     ↓
OUTPUT JSON

1. Run Layer 1 completely

Run the entire Layer 1 process defined in Part A, end to end, against the supplied source.

2. FREEZE Layer 1

The moment Layer 1's:

- INPUT
- SCREEN
- SCENE/CANVAS
- OBJECTS
- ANIMATIONS, when applicable
- REPORT

are complete, freeze them.

Freezing means:

«Layer 1 is no longer treated as the model's current visual interpretation. It is treated as a completed, immutable JSON artifact that happens to exist in context.»

After freezing:

- the raw image is out of scope for Layer 2 reasoning;
- Layer 2 MUST NOT visually inspect or re-read the source;
- Layer 2 MUST NOT perform additional OCR;
- Layer 2 MUST NOT detect new objects;
- Layer 2 MUST NOT recalculate positions, dimensions, colours, text, or visual properties;
- Layer 2 MUST NOT use visual memory of the raw image to fill a gap.

If something appears underspecified, wrong, or missing in the frozen graph, that is a Layer 1 extraction-quality issue.

Layer 2 must preserve that uncertainty and propagate it.

3. Layer 2 reads only frozen Layer 1

Layer 2 may reference only fields present in the finalized Layer 1 JSON.

No field may be reconstructed from the original pixels.

4. Run Layer 2 completely

Run all applicable Layer 2 stages against the frozen Layer 1 JSON.

5. Emit only the required tagged sections

Do not emit commentary, explanations, analysis, Markdown outside the tagged sections, or additional sections.

---

CONTRACT — HARD BOUNDARY

LAYER 1 MAY

✓ Classify the input
✓ Detect scenes/canvases
✓ Detect visible objects
✓ OCR visible text
✓ Detect visible motion/animation from supplied frames
✓ Record directly observable parent/child hints
✓ Record directly observable grouping hints
✓ Record raw spatial relationships
✓ Estimate visual properties
✓ Estimate confidence per object/field
✓ Record evidence explaining non-obvious observations

LAYER 1 MAY NOT

✗ Discover reusable components or templates
✗ Infer business meaning or intent
✗ Build interaction graphs
✗ Build navigation graphs
✗ Infer layout engines
✗ Assign semantic region types such as header/footer/sidebar
✗ Add missing objects merely to complete a visual pattern
✗ Convert uncertain observations into confident facts
✗ Guess unreadable text, colours, icons, interactions, or motion

Layer 1 layout restriction

"relationships.layout" is reserved for Layer 2 structural synthesis.

Layer 1 MUST NOT classify:

- grid
- stack
- flow
- freeform
- responsive containers
- sidebar layouts
- split views
- other layout engines

Layer 1 may record the raw evidence from which Layer 2 can later derive layout:

- alignment
- bounding-box distances
- overlap
- ordering
- repeated spacing
- parent hints
- children
- group hints

Therefore:

"relationships": {
  "layout": null
}

MUST be emitted by Layer 1.

---

LAYER 2 MAY

✓ Cluster objects into regions/components
✓ Use multiple corroborating spatial signals
✓ Normalize repeated elements
✓ Discover reusable component templates
✓ Build PROJECT → SCREEN → SCENE → REGION → COMPONENT → ELEMENT hierarchy
✓ Synthesize layout
✓ Synthesize constraints
✓ Synthesize z-order
✓ Build evidence-gated interaction graphs
✓ Build video/prototype state graphs
✓ Build multi-screen navigation graphs
✓ Propagate confidence
✓ Validate structural consistency

LAYER 2 MAY NOT

✗ Detect new objects not present in the frozen Layer 1 graph
✗ Create or correct OCR text
✗ Invent text
✗ Invent colours
✗ Invent positions or sizes
✗ Invent missing icons, buttons, cards, panels, etc.
✗ Re-read the raw image
✗ Use remembered visual details not present in Layer 1
✗ Infer business meaning or product intent
✗ Create interaction/navigation/state edges without Layer 1 evidence

Any field violating these boundaries is a contract violation.

When a required structural claim cannot be supported by Layer 1 evidence:

- do not invent it;
- use "null", "unclassified", "freeform", or omission according to the applicable schema;
- preserve the uncertainty;
- surface it through confidence/evidence/warnings where applicable.

---

OUTPUT COMPLIANCE — REQUIRED

Return exactly these four tagged sections:

<LAYER1_OUTPUT>
valid JSON object containing the complete Layer 1 output
</LAYER1_OUTPUT>

<LAYER2_OUTPUT>
valid JSON object containing the complete Layer 2 output
</LAYER2_OUTPUT>

<MANIFEST_UPDATE>
valid JSON object containing only manifest additions/modifications required for this execution
</MANIFEST_UPDATE>

<REPORT>
valid JSON object containing the execution report
</REPORT>

No explanatory prose may appear outside these four tags.

Inside each tagged section, emit valid machine-readable JSON.

Do not emit YAML disguised as JSON.

Do not use comments inside JSON.

Do not add trailing commas.

---

REQUIRED MANIFEST KEYS

"MANIFEST_UPDATE" MUST include at minimum:

{
  "pipeline_id": "...",
  "run_id": "...",
  "image_hash": "...",
  "layer1_hash": "...",
  "layer2_hash": "...",
  "schema_version": "v2.1"
}

These are mandatory join keys for large-batch deduplication, audit, provenance, and recovery.

---

REPORT FORMAT

{
  "layer": "UI_GOD_LAYER1+2_FUSED",
  "layer1_version": "v5",
  "layer2_version": "v2",
  "schema_version": "v2.1",
  "pipeline": "UI",
  "pipeline_id": "...",
  "run_id": "...",
  "image_hash": "...",
  "extraction_status": "...",
  "layer1_output_file_name": "LAYER1_CCIR.json",
  "layer2_output_file_name": "LAYER2_CSIR.json",
  "next_layer": "UI_GOD_LAYER3",
  "layer1_output_hash": "...",
  "layer2_output_hash": "...",
  "objects_extracted": 0,
  "warnings": []
}

---

REQUIRED INPUTS

Primary Input

Raw UI source:

Screenshot
UI image
Design export
Video
Screen recording
Sketch
Wireframe
Figma export
PDF
Photo
Desktop capture

Supporting Inputs

None for Layer 1.

For Layer 2:

Only the frozen Layer 1 output produced during this execution is authoritative.

Any supporting metadata may be used only for provenance validation or recovery of already-extracted metadata and MUST NOT contradict, supplement, or replace Layer 1 visual extraction.

---

PRODUCES

LAYER1_CCIR.json
LAYER2_CSIR.json

NEXT LAYER

UI_GOD_LAYER3

EXPECTED PRIMARY INPUT FOR NEXT LAYER

LAYER2_CSIR.json

---

PART A — LAYER 1

UI EXTRACTION ENGINE v2

---

STAGE 0 — INPUT CLASSIFICATION

Run first.

Extraction behaviour depends on source type.

{
  "input": {
    "type": "image | video | screen_recording | sketch | wireframe | figma_export | pdf | photo | desktop",
    "subtype": "mobile | tablet | desktop | web | native_app | unknown",
    "confidence": 0.00
  }
}

Rules

- Select the closest directly observable input type.
- Do not infer platform-specific details that are not visible.
- "confidence" is confidence in the classification itself.

---

STAGE 1 — SCREEN METADATA

{
  "screen": {
    "device": "mobile | tablet | desktop | unknown",
    "orientation": "portrait | landscape | null",
    "viewport": "[WxH] | null",
    "theme": "light | dark | mixed | null",
    "safe_area": "present | absent | null",
    "status_bar": "visible | hidden | null",
    "navigation_bar": "visible | hidden | null",
    "window_count": 1,
    "scroll_position": 0.00,
    "zoom_level": null
  }
}

"scroll_position" may be "null" when it cannot be determined.

"zoom_level" may be "null" when unavailable.

If:

device = desktop

append:

{
  "monitors": 1,
  "active_window": "...",
  "browser": "...",
  "sidebar_present": true
}

Values may be "null" where not observable.

Do not infer hidden browser/application metadata.

---

STAGE 2 — SCENE / CANVAS SEGMENTATION

Two independent mechanisms exist.

Both may apply.

---

A. Scene Detection

Applicable to:

video
screen_recording

Split by meaningful visual state change, never by fixed time interval.

{
  "scene_id": "scene_0",
  "frame_range": "0-80",
  "label": "Login"
}

Every detected scene receives an independent object extraction pass.

Each extracted object must reference its source scene/frame.

Do not create a new scene merely because of a small animation or transient pixel change unless the visible UI state materially changes.

---

B. Multi-Canvas Segmentation

Applicable to:

- Figma boards
- infinite canvases
- scrolling screenshots
- dashboards wider than viewport
- whiteboards
- multi-page PDFs
- multi-screen design exports

Segment automatically.

Never require the user to pre-split the source.

{
  "screen_id": "screen_0",
  "bounds": "[x1,y1,x2,y2]",
  "label": "Dashboard",
  "linked_from": null,
  "linked_to": null
}

Coordinates in "bounds" are percentages of the full source canvas.

"linked_from" / "linked_to" may only be populated when a visible connector, arrow, or directly observable trigger indicates the relationship.

Never infer navigation from:

- proximity
- order
- visual similarity
- alignment
- page sequence alone

Every screen/canvas/scene receives its own extraction pass.

---

STAGE 3 — OBJECT EXTRACTION

Process:

top → bottom
left → right

within each scene/screen.

Every extracted object receives a unique ID scoped to its scene/screen.

---

OBJECT SCHEMA

{
  "id": "role_0",
  "role": "button | input | text | image | icon | card | nav | tab | list | table | divider | modal | dialog | drawer | menu | dropdown | tooltip | badge | chart | form | skeleton | avatar | progress | toast | other",
  "type": "specific subtype or null",
  "parent": null,
  "children": null,
  "group": null,
  "layer": null,
  "position": {
    "x": 50,
    "y": 50
  },
  "size": {
    "w": 20,
    "h": 10
  },
  "rotation": null,
  "visibility": "visible",
  "shape": {
    "form": "rectangle | circle | pill | freeform | null",
    "corner_values": null
  },
  "style": {
    "bg": null,
    "color": null,
    "opacity": null,
    "blur": null,
    "gradient": null,
    "radius": null,
    "border": null,
    "stroke": null,
    "shadow": null,
    "elevation": null,
    "mask": null,
    "pattern": null
  },
  "typography": {
    "font_guess": null,
    "size_guess": null,
    "weight": null,
    "line_height": null
  },
  "image": {
    "category": null
  },
  "icon": {
    "name_guess": null
  },
  "motion": {
    "state": "none",
    "animation_ref": null
  },
  "interaction": {
    "state": "none",
    "keyboard_open": null,
    "cursor_visible": null
  },
  "relationships": {
    "above": null,
    "below": null,
    "left_of": null,
    "right_of": null,
    "aligned_with": null,
    "distance_to_nearest": null,
    "overlaps": null,
    "layout": null,
    "reading_order": null,
    "hierarchy_depth": null
  },
  "text": {
    "value": null,
    "truncated": false,
    "multiline": false,
    "language": null,
    "ocr_confidence": null
  },
  "confidence": 0.00,
  "evidence": {
    "shape": null,
    "text": null,
    "position": null,
    "motion": null,
    "color": null,
    "relationship": null
  },
  "source_frame": "screen_0"
}

---

OBJECT RULES

1. Identity

id = role + "_" + zero-based index

IDs are scoped per scene/screen.

Example:

button_0
button_1
text_0
image_0

---

2. Text

Copy visible text exactly.

Do not paraphrase.

Do not normalize wording.

If visibly truncated, preserve the visible ending and use "~" where required.

Sibling labels belonging to one clearly bounded component may be combined into one "text.value" using:

Label A | Label B | Label C

Never use "·" as a separator.

If exact text cannot be recovered reliably:

"value": null

and preserve the OCR confidence/evidence.

---

3. Position

"position.x" and "position.y" represent the object's center point as a percentage of its own scene/screen dimensions.

---

4. Size

"size.w" and "size.h" represent the object's bounding-box dimensions as percentages of its own scene/screen dimensions.

---

5. Containment

Populate "parent" / "children" only when:

- the object is fully enclosed by a clearly observable boundary; or
- a clear card/modal/list-row/container boundary exists.

Never infer containment from proximity or alignment alone.

---

6. Group

"group" may only describe a directly observable visual grouping.

Valid support includes:

- shared visible container
- repeated visual block
- explicit section boundary
- clear common background
- directly observable grouping cue

Do not create semantic groups solely from proximity.

Example:

"hero"
"impact_stats"
"story_grid"

is valid only when the visual grouping is actually observable.

---

7. Layer

Estimate z-order only when visually supported by:

- overlap
- occlusion
- visible stacking
- explicit foreground/background relationship

Otherwise use "null".

---

8. Shape

Only record a shape when directly observable.

Do not infer hidden geometry.

---

9. Colour

Use hexadecimal values only when the colour is clearly readable.

Otherwise:

"bg": null

Never approximate a colour merely because it appears visually similar.

---

10. Typography

"font_guess" is a visual estimate only.

Never claim an exact font family when the screenshot does not support that certainty.

Typography estimates may be:

null

when not reliably observable.

---

11. Image

Classify image category only when visually supported:

photo
illustration
icon_set
screenshot
null

---

12. Icon

"name_guess" may identify a recognizable icon when visual evidence is sufficient.

Do not invent an icon name merely because its function seems likely.

---

13. Motion

Only record motion directly observable across supplied frames.

Valid states:

none
transition
sliding
fading
scaling
bouncing
loading_indicator
skeleton_state
unobservable

Static input defaults to:

none

Undeterminable motion:

unobservable

Never infer animation from UI conventions.

---

14. Interaction

Only record states directly observable:

none
pressed
focused
hovered
expanded
dragged
selected
disabled
unobservable

Do not infer interaction state from the object type alone.

---

15. Relationships

Record raw spatial relationships only.

Valid examples:

- above
- below
- left_of
- right_of
- aligned_with
- overlaps
- distance_to_nearest
- reading_order
- hierarchy_depth

Layer 1 MUST NOT classify "relationships.layout".

Therefore:

"layout": null

always.

Layer 2 owns layout classification.

---

16. Evidence

Every non-obvious extracted attribute should have a concrete evidence statement.

Bad:

"Looks like a button."

Good:

"White pill-shaped control with dark text positioned at the far right of the top navigation."

Evidence must describe observable support, not semantic intention.

---

17. Source provenance

Every object must contain:

source_frame

Referencing the frame number, scene ID, or screen ID from which the object was extracted.

---

18. Sketch uncertainty

Only when:

INPUT.type = sketch

include:

{
  "sketch": {
    "source": "hand_drawn | digital_wireframe",
    "uncertain": true
  }
}

Sketch-derived confidence is capped according to the confidence rules below.

---

19. Extraction order

Always process:

top → bottom
left → right

within each scene/screen.

---

CONFIDENCE RULES

Confidence describes confidence in the object's extraction quality, not confidence in what the UI "probably means."

Important null semantics

"null" does NOT automatically mean low confidence.

A field may be "null" because:

- the attribute is not visible;
- the attribute is not applicable;
- the attribute cannot be determined from the source;
- the field is intentionally reserved for another layer.

A naturally unobservable/non-applicable field does not reduce confidence by itself.

Confidence penalties apply primarily to materially missing or uncertain attributes such as:

- object identity
- geometry
- text
- visually important styling
- structural relationships
- motion when motion is relevant

Caps

If one material field required for reliable interpretation is null/unobservable:

confidence ≤ 0.70

If multiple material fields are null/unobservable:

confidence ≤ 0.60

Confidence above "0.80" requires that the object's:

- identity
- position/size
- primary visible characteristics

are sufficiently clear.

Sketch-derived extraction:

confidence ≤ 0.60

unless line work and object boundaries are unambiguous.

Confidence "< 0.60" must be listed in:

REPORT.uncertain_regions

Do not mechanically penalize an object simply because ordinary non-applicable fields are null.

---

STAGE 4 — MOTION / TEMPORAL INTELLIGENCE

Applicable only to:

video
screen_recording

Processing:

Scene Detection
    ↓
Frame Sampling
    ↓
Object Tracking
    ↓
Motion Tracking
    ↓
Timeline JSON
    ↓
Canonical Raw Extraction

Tracked objects keep stable IDs across frames wherever identity continuity is visually supported.

Motion is stored as separate linked records.

---

ANIMATION SCHEMA

{
  "animation_id": "anim_0",
  "object_ids": ["button_0"],
  "type": "fade | scale | slide | morph | rotation | bounce | loading | skeleton | transition | navigation | parallax",
  "start_frame": 10,
  "end_frame": 24,
  "duration": null,
  "direction": null,
  "curve_estimate": null,
  "repeat": null,
  "loop": null,
  "evidence": "Frame-to-frame position/opacity/size change observed."
}

Only populate properties actually supported by the supplied frames.

If no observable motion exists:

Do not emit an ANIMATION block.

Do not emit a null-filled placeholder animation.

---

STAGE 5 — EXTRACTION QUALITY REPORT

Layer 1 REPORT must contain:

{
  "objects_detected": 0,
  "scenes_or_screens_processed": 0,
  "frames_processed": null,
  "ocr_quality": "high | medium | low | null",
  "motion_detected": false,
  "missing_regions": [],
  "uncertain_regions": [],
  "confidence_distribution": {
    "0.90-1.00": 0,
    "0.70-0.89": 0,
    "0.60-0.69": 0,
    "<0.60": 0
  },
  "warnings": [],
  "recommendation": "ready as-is",
  "compiler_ready": true
}

compiler_ready

Set:

false

when:

- uncertain regions are structurally load-bearing; or
- input is a sketch and mean confidence is below "0.55".

Otherwise:

true

---

LAYER 1 COMPLETION BOUNDARY

At this point Layer 1 is complete.

Its complete output consists of:

INPUT
SCREEN
SCENES/CANVASES
OBJECTS
ANIMATIONS, if applicable
REPORT

This complete JSON becomes the sole authoritative input to Part B.

Do not re-read the source below this line.

---

PART B — LAYER 2

STRUCTURAL SYNTHESIS ENGINE v2

OBJECTIVE

Convert the complete Layer 1 extraction into a deterministic structural UI architecture graph.

Layer 2 does not perform visual extraction.

Layer 2 performs:

clustering
hierarchy
component discovery
layout synthesis
constraint synthesis
pattern detection
region typing
z-stack reconstruction
interaction graph synthesis
state graph synthesis
navigation graph synthesis
confidence propagation
validation

Layer 2 must derive all claims from Layer 1.

---

INPUT

Layer 2 consumes the frozen Layer 1 output:

id
role
type
parent
children
group
layer
position
size
rotation
visibility
shape
style
typography
image
icon
motion
interaction
relationships
text
confidence
evidence
source_frame
sketch

Plus:

SCENE / CANVAS
REPORT
ANIMATIONS

Nothing extracted by Layer 1 may be silently discarded.

If Layer 1 contains:

- group
- parent
- children
- relationship
- animation_ref
- linked_to
- linked_from

Layer 2 must consider and use that signal where applicable.

Discarding actionable extracted signal is a failure state.

---

HIERARCHY MODEL

The Layer 2 tree is:

PROJECT
  └── SCREEN
        └── SCENE
              └── REGION
                    └── COMPONENT
                          └── ELEMENT

Static image

Use:

PROJECT
└── SCREEN
    └── implicit SCENE

Video / screen recording

PROJECT
└── SCREEN
    ├── SCENE
    ├── SCENE
    └── SCENE

Figma / whiteboard / multi-page PDF

Multiple "SCREEN" nodes may exist.

Connections between screens must come from Layer 1 observed connector data.

---

CORE LAYER 2 INVARIANT

Every extracted Layer 1 object must survive into the structural graph.

It may be:

- an ELEMENT;
- included within a COMPONENT;
- associated with a COMPONENT_INSTANCE;

but it must never be silently deleted.

Low-confidence objects are preserved.

---

STAGE 1 — SPATIAL CLUSTERING

Never cluster on proximity alone.

A region/component boundary requires at least two corroborating signals.

Possible signals:

- distance between bounding boxes
- overlap
- alignment
- shared background fill
- shared border/stroke
- shared elevation/shadow
- repeated spacing
- reading order
- Layer 1 parent hint
- Layer 1 children hint
- Layer 1 group label
- repeated sibling pattern

A Layer 1 group label is a strong prior but is not sufficient proof by itself.

Repeated pattern evidence is useful when multiple candidates exhibit consistent:

- shape
- size
- spacing
- structure

Output requirement

Every REGION and COMPONENT generated by clustering must contain:

{
  "clustering_evidence": [
    "alignment between ...",
    "consistent spacing between ...",
    "Layer 1 parent hint ..."
  ]
}

Evidence must reference actual Layer 1 object IDs/fields wherever practical.

---

STAGE 2 — LAYOUT ENGINE

For every REGION and COMPONENT, classify direct-child arrangement.

Allowed values:

vertical_stack
horizontal_stack
grid
masonry
absolute
responsive_container
split_view
sidebar_layout
docked_footer
freeform

Rules

Only assign a specific layout when supported by measured spatial evidence.

Use:

freeform

when arrangement is genuinely ambiguous.

Never choose a layout merely because it is common for the object type.

Output:

{
  "layout": "grid",
  "layout_evidence": "Three equal-width objects share vertical alignment and repeated horizontal spacing."
}

---

STAGE 3 — CONSTRAINT SYNTHESIS

Infer constraints strictly from Layer 1 geometry.

{
  "equal_spacing": true,
  "alignment_group": ["card_0", "card_1", "card_2"],
  "consistent_padding": null,
  "margin": null,
  "anchor": "top",
  "stretch_behavior": "fill",
  "evidence": "Measured center/edge relationships across the direct children."
}

Valid anchor values:

top
bottom
left
right
center
null

Valid stretch behaviour:

fixed
fill
hug
null

Do not invent pixel values when Layer 1 only contains percentages.

---

STAGE 4 — HIERARCHY CONSTRUCTION

Build:

PROJECT
→ SCREEN
→ SCENE
→ REGION
→ COMPONENT
→ ELEMENT

Every ELEMENT must have exactly one parent.

Rules:

1. Prefer Layer 1 explicit containment.
2. Use Layer 2 clustering only when supported by at least two signals.
3. If no enclosing boundary exists, attach the element directly to its SCENE.
4. Never discard an extracted element.
5. Never leave an element floating outside the hierarchy.

---

STAGE 5 — COMPONENT NORMALIZATION & DISCOVERY

Merge duplicate UI structures only when supported by repeated structural evidence.

A repeated UI pattern should generally have:

2+ structurally corresponding instances

and consistent role/style/geometry/spacing characteristics.

Do not merge unrelated objects solely because they look similar.

---

COMPONENT TEMPLATE

{
  "template_id": "tmpl_0",
  "structure": [
    "image",
    "title",
    "price",
    "badge",
    "button"
  ],
  "instances": [
    "card_0",
    "card_1",
    "card_2"
  ],
  "variance": "Badge presence and text values differ between instances."
}

Repeated patterns may include:

- cards
- nav bars
- map/background layers
- list rows
- repeated controls

Do not invent absent child elements merely to complete a template.

A template describes repeated extracted structure; it must not manufacture missing objects.

---

STAGE 6 — DESIGN PATTERN DETECTION

Recognize structural patterns only.

Allowed:

navigation_rail
app_bar
tab_bar
settings_list
dashboard_grid
checkout_flow_layout
form_layout
chat_layout
gallery_layout
null

Output:

{
  "pattern": "gallery_layout",
  "pattern_evidence": "Repeated image cards arranged in a consistent multi-column structure."
}

Pattern names describe structural signatures only.

They do NOT assert business meaning.

For example:

checkout_flow_layout

means a structural arrangement resembling:

steps + form + summary

not proof that the UI is actually a commerce checkout.

---

STAGE 7 — REGION TYPING

Classify each REGION as:

header
footer
sidebar
content
overlay
floating_action_area
modal_layer
background_layer
unclassified

Classification must be supported by structural evidence.

Do not assign a semantic region type solely because of the object's apparent business role.

---

STAGE 8 — Z-LAYER SYNTHESIS

Reconstruct stacking from:

- Layer 1 "layer"
- overlap
- visibility
- occlusion
- region structure

Possible levels:

background_layer
content
floating_action_area
toast
modal_layer
tooltip

Output only levels actually present.

{
  "order": [
    "background_layer",
    "content",
    "floating_action_area"
  ],
  "evidence": "Layer 1 overlap/layer estimates support this ordering."
}

Region-type priors may assist, but must not override contradictory Layer 1 evidence.

---

STAGE 9 — INTERACTION GRAPH

Build interaction relationships only when directly supported by Layer 1 interaction or motion evidence.

Valid action types include:

opens
expands
switches
slides

Example:

{
  "edges": [
    {
      "source": "button_0",
      "action": "opens",
      "target": "modal_0",
      "evidence": "Layer 1 interaction/motion state shows an observable opening transition."
    }
  ]
}

Valid interaction evidence includes:

- visible pressed state
- expanded state
- observable opened state
- observable transition
- linked animation
- explicit Layer 1 state relationship

No evidence = no interaction edge.

Never infer navigation merely because an object appears clickable.

---

STAGE 10 — STATE GRAPH

Applicable only to:

video
screen_recording
prototype inputs with observed state transitions

Build from Layer 1 scenes and animations.

{
  "states": [
    {
      "state_id": "state_A",
      "scene_ref": "scene_0"
    }
  ],
  "transitions": [
    {
      "from": "state_A",
      "to": "state_B",
      "trigger": "button_0",
      "evidence": "Layer 1 animation/interaction evidence."
    }
  ]
}

Do not output STATE_GRAPH for a single static image.

---

STAGE 11 — NAVIGATION / FLOW GRAPH

Applicable only to multi-screen inputs.

Use Layer 1:

linked_from
linked_to

to construct navigation.

{
  "nodes": [
    "screen_0",
    "screen_1"
  ],
  "edges": [
    {
      "from": "screen_0",
      "to": "screen_1",
      "evidence": "Visible connector/arrow observed by Layer 1."
    }
  ]
}

Never infer navigation from:

- screen order
- spatial adjacency
- visual similarity
- naming
- expected product behaviour

No Layer 1 connector/trigger = no navigation edge.

---

STAGE 12 — CONFIDENCE PROPAGATION

Confidence must propagate numerically upward.

Layer 1 object confidence remains unchanged.

For deterministic behaviour:

component confidence = minimum confidence among its member elements
region confidence    = minimum confidence among its descendants
screen confidence    = minimum confidence among its regions
project confidence   = minimum confidence among its screens

This deliberately preserves the weakest structurally relevant evidence instead of silently upgrading confidence.

Output:

{
  "project": 0.84,
  "screens": {
    "screen_0": 0.84
  },
  "regions": {
    "region_0": 0.91,
    "region_1": 0.79
  },
  "components": {
    "card_0": 0.85
  },
  "weak_child": true
}

"weak_child" remains for backward compatibility.

It does not replace numeric confidence.

Low confidence never authorizes:

- deletion
- hallucination
- semantic completion
- pattern completion

---

STAGE 13 — STRUCTURAL VALIDATION

Run before final Layer 2 output.

{
  "no_cycles": true,
  "single_parent_enforced": true,
  "no_impossible_overlaps": true,
  "duplicate_ids_removed": true,
  "graph_connected": true,
  "layout_consistent": true,
  "failures": [],
  "status": "pass"
}

Validation requirements

Check for:

- hierarchy cycles
- multiple parents
- floating nodes
- duplicate IDs
- broken references
- impossible structural relationships
- disconnected hierarchy
- inconsistent layout assignments
- references to nonexistent objects
- template instances referencing nonexistent elements
- graph edges referencing nonexistent nodes

Failure gate

If:

status = fail

then:

1. emit the "VALIDATION_REPORT";
2. list every failure;
3. do not claim the graph is valid;
4. do not fabricate corrections;
5. do not hide the failed condition.

The Layer 2 output must truthfully surface the invalid state.

---

SEMANTIC STABILITY RULE

If an element has low confidence:

do not delete it
do not invent meaning
do not complete missing fields
do not upgrade confidence without evidence

Set:

weak_child = true

where applicable and preserve the numeric confidence trail.

---

LAYER 2 OUTPUT FORMAT

The body inside "<LAYER2_OUTPUT>" must be one valid JSON object following this structure:

{
  "project": {
    "project_id": "proj_0",
    "input_type": "image"
  },
  "screens": [
    {
      "screen_id": "screen_0",
      "device": "mobile"
    }
  ],
  "scenes": [],
  "regions": [],
  "component_templates": [],
  "component_instances": [],
  "layout_graph": [],
  "constraint_graph": [],
  "interaction_graph": {
    "edges": []
  },
  "navigation_graph": null,
  "state_graph": null,
  "z_stack": {
    "order": [],
    "evidence": ""
  },
  "confidence": {
    "project": 0.00,
    "screens": {},
    "regions": {},
    "components": {}
  },
  "validation_report": {
    "no_cycles": true,
    "single_parent_enforced": true,
    "no_impossible_overlaps": true,
    "duplicate_ids_removed": true,
    "graph_connected": true,
    "layout_consistent": true,
    "failures": [],
    "status": "pass"
  }
}

Conditional sections:

- "scenes" may be an empty array for static images.
- "navigation_graph" must be "null" or omitted for single-screen inputs.
- "state_graph" must be "null" or omitted for non-video/non-prototype inputs.

Do not fabricate empty semantic structures when a conditional stage is not applicable.

---

QUALITY RULES

Rule 1 — No orphan nodes

Every extracted object must resolve to exactly one place in:

PROJECT
→ SCREEN
→ SCENE
→ REGION
→ COMPONENT
→ ELEMENT

---

Rule 2 — No silent deletion

Layer 2 may normalize representation, but it must never silently discard a Layer 1 object.

---

Rule 3 — No duplicate component inflation

Repeated structures should be normalized into templates + instances where structural evidence supports reuse.

Do not manufacture templates from weak similarity.

---

Rule 4 — Spatial clustering requires ≥2 signals

Never create a structural boundary from proximity alone.

---

Rule 5 — Evidence traceability

Every synthesized:

- layout
- constraint
- region classification
- component boundary
- interaction edge
- navigation edge
- state transition
- z-stack claim

must cite the relevant Layer 1 evidence in its "evidence" field.

Evidence should identify the supporting object IDs/relationships/fields whenever practical.

---

Rule 6 — No visual re-read after Layer 1

Once the freeze occurs:

raw image = out of scope

Layer 2 cannot use visual memory to fill missing information.

---

Rule 7 — No hallucinated completion

If a repeated structure contains:

image + title + button

do not invent:

price + badge

merely because similar UI patterns usually contain them.

Templates represent what was extracted, not what "should" exist.

---

Rule 8 — No business-semantic inference

Do not infer:

- product purpose
- target audience
- business model
- business intent
- user intent
- application category

unless such information is directly part of Layer 1's extracted text or structure.

Structural labels must remain structural.

---

Rule 9 — Nulls are not errors

A "null" field is valid when information is:

- not observable
- not applicable
- reserved for another layer
- insufficiently reliable

Do not replace uncertainty with guesses merely to reduce null counts.

---

Rule 10 — Conditional structures

When a stage does not apply, omit the stage's output or represent it with the explicitly permitted empty/null form.

Do not create fake records simply to satisfy schema shape.

---

Rule 11 — Determinism

Given the same frozen Layer 1 JSON, Layer 2 should produce the same structural interpretation.

Do not use hidden visual inspection to break ties.

When evidence is insufficient, choose the conservative representation:

freeform
unclassified
null
omission

according to the schema.

---

Rule 12 — Validation precedes validity claims

Never output a structurally valid-looking graph while the validation report says the graph is invalid.

---

FINAL EXECUTION INVARIANT

The system must behave as though these were two separate calls:

CALL 1
IMAGE
→ LAYER 1
→ LAYER1_CCIR.json
→ freeze

CALL 2
LAYER1_CCIR.json
→ LAYER 2
→ LAYER2_CSIR.json

The fused prompt is merely an execution optimization.

It must NOT allow Layer 2 to regain access to the raw visual source.

The authoritative information boundary is:

RAW SOURCE
    ↓
LAYER 1
    ↓
FREEZE
    ↓
LAYER 2

If Layer 1 misses something:

Layer 2 preserves the gap.

If Layer 1 is uncertain:

Layer 2 preserves the uncertainty.

If Layer 1 contains a low-confidence object:

Layer 2 preserves the object and propagates the uncertainty.

If Layer 2 cannot structurally justify a claim:

Layer 2 does not invent it.

The system's priority order is:

OBSERVABILITY
    >
TRACEABILITY
    >
STRUCTURAL CONSISTENCY
    >
COMPLETENESS

Never sacrifice factual extraction integrity for apparent completeness.
