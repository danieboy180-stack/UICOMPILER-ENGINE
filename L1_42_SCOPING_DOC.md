# STAGE O10 — LAYERS 1 THROUGH 4.2 SCOPING DOCUMENT

## DECISIONS

### 1. LAYER 1 — CCIR (Component Candidate IR)
- **Approach Decision**: **Hybrid (Option C).** We will use classical CV (OpenCV edge/contour detection + connected component analysis) to establish deterministic, pixel-perfect bounding boxes, followed by a fast VLM (Vision-Language Model) pass to prune noise. 
- **Why**: As a solo developer with zero budget for a massive labeling operation and fine-tuning pipeline, a purely trained YOLO model is out of scope. Conversely, purely prompting a VLM for spatial bounding boxes is prone to hallucination and poor pixel alignment. Classical CV is free, instant, and guarantees tight bounds around high-contrast UI elements.
- **Output Contract**: An array of `CandidateNode` containing a unique `id`, `bbox` (x, y, w, h), and a `confidence_score` (from the VLM pruning). No deep semantic labels yet.

### 2. LAYER 2 — CSIR (Component Semantic IR)
- **Approach Decision**: **VLM-based classification.** We will pass the original screenshot overlaid with numbered bounding boxes (from L1) to a structured-output VLM (e.g., Gemini 1.5 Flash / GPT-4o-mini). The prompt will instruct the model to map each ID to a strict semantic taxonomy.
- **Taxonomy (v1 Strict List)**:
  - `button` (primary, secondary, text, icon)
  - `input` (text, dropdown, checkbox, radio, toggle)
  - `text` (heading, body, caption)
  - `image` (avatar, hero, icon, generic)
  - `container` (card, list, grid, nav_bar)

### 3. LAYER 3 — VISUAL_IR
- **Approach Decision**: **Deterministic (ai_used: false).** 
- **Why**: We already have the bounding boxes and semantic roles. We can use OpenCV pixel sampling (k-means clustering) to extract dominant background/foreground colors. Tesseract OCR (or a cheap cloud OCR API) can extract precise text strings and estimate font-height. OpenCV heuristics can detect border radii and stroke widths. LLMs are terrible at guessing hex codes; math does it better and cheaper.

### 4. LAYER 4.1 — SEMANTIC_IR
- **Approach Decision**: **Algorithmic spatial sorting (ai_used: false).**
- **Why**: LLMs struggle with deeply nested DOM tree logic. We will use a geometric sweeping-line algorithm to infer parent-child relationships (e.g., Box B is 100% inside Box A -> B is a child of A). 
- **Minimum Viable Layout (v1)**: We will exclusively support **single-column flex layouts** (vertical stacking) with nested horizontal flex rows. Elements that overlap or don't align cleanly will fallback to absolute positioning. Grid inference is strictly deferred to v2.

### 5. LAYER 4.2 — EXECUTION_IR
- **Approach Decision**: The output must exactly serialize to the `Layer4.2Schema` expected by Layer 4.3 (Optimization Compiler). This is a rigid recursive tree where every node has a `type` (from the L2 taxonomy), `style` (from L3), `content` (from L3 OCR), and `children`.

### 6. GROUND TRUTH / ACCURACY MEASUREMENT
- **Decision**: Before writing L1 logic, I will build a hand-labeled test set of **50 high-quality screenshots**. I will manually author the expected `Layer4.2Schema` JSON for these 50 screens. 
- CI/CD will run the pipeline against these 50 screens and calculate **IoU (Intersection over Union)** for bounding boxes and **classification accuracy %** for semantic roles. The pipeline is not "done" until it hits 90% accuracy on this set.

### 7. COST MODEL
- **L1 (CV)**: $0.00
- **L2 (VLM)**: ~$0.001 per screen using Gemini 1.5 Flash or GPT-4o-mini (approx. 2000 input tokens with image, 500 output tokens).
- **L3 (CV + OCR)**: $0.00 (local Tesseract/heuristics).
- **L4.1/4.2 (Algorithmic)**: $0.00.
- **Total Cost**: **< $0.005 per screenshot**. This ensures UI_GOD is highly scalable and can be priced sustainably as a SaaS or run locally for pennies.

### 8. DATA COLLECTION PLAN
- **Sources**: Public, permissively licensed open-source UI kits (e.g., Tailwind UI templates, Figma Community open-source files). 
- **Format**: High-fidelity PNGs (2x retina resolution, e.g., 1170x2532). Strictly single-screen (no scrolling composites or long web pages for v1).

---

## PYDANTIC SCHEMA SKETCHES

```python
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel

# ---------------------------------------------------------
# LAYER 1: CCIR (Component Candidate IR)
# ---------------------------------------------------------
class BoundingBox(BaseModel):
    x: int
    y: int
    w: int
    h: int

class CandidateNode(BaseModel):
    id: str
    bbox: BoundingBox
    confidence: float

class Layer1_CCIR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    candidates: List[CandidateNode]
    image_width: int
    image_height: int

# ---------------------------------------------------------
# LAYER 2: CSIR (Component Semantic IR)
# ---------------------------------------------------------
class SemanticNode(BaseModel):
    id: str
    semantic_role: Literal["button", "input", "text", "image", "container"]
    semantic_variant: Optional[str] # e.g., "primary", "checkbox", "heading"

class Layer2_CSIR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    semantics: List[SemanticNode]

# ---------------------------------------------------------
# LAYER 3: VISUAL_IR
# ---------------------------------------------------------
class StyleAttributes(BaseModel):
    background_color: Optional[str] # hex
    text_color: Optional[str] # hex
    font_size: Optional[int] # px
    border_radius: Optional[int] # px
    border_color: Optional[str] # hex
    border_width: Optional[int] # px

class VisualNode(BaseModel):
    id: str
    style: StyleAttributes
    raw_text: Optional[str] # From OCR

class Layer3_VISUAL_IR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    visuals: List[VisualNode]

# ---------------------------------------------------------
# LAYER 4.1: SEMANTIC_IR
# ---------------------------------------------------------
class LayoutAttributes(BaseModel):
    layout_type: Literal["flex_row", "flex_col", "absolute"]
    justify_content: Optional[str]
    align_items: Optional[str]
    gap: Optional[int]
    margin: Optional[Dict[str, int]]
    padding: Optional[Dict[str, int]]

class SemanticTree(BaseModel):
    id: str
    layout: LayoutAttributes
    children: List['SemanticTree']

class Layer4_1_SEMANTIC_IR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    root: SemanticTree

# ---------------------------------------------------------
# LAYER 4.2: EXECUTION_IR (Matches existing 4.3 contract)
# ---------------------------------------------------------
class ComponentNode(BaseModel):
    id: str
    type: Literal["button", "input", "text", "image", "container"]
    variant: Optional[str]
    bbox: BoundingBox
    style: StyleAttributes
    content: Optional[str]
    layout: LayoutAttributes
    children: List['ComponentNode']

class Layer4_2_EXECUTION_IR(BaseModel):
    schema_version: Literal["2.1"] = "2.1"
    root_node: ComponentNode
```

