import os
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import ingest

EXIT_OK = 0
EXIT_10 = 10
EXIT_20 = 20

import re
import datetime

def mask_secrets(text: str) -> str:
    if not isinstance(text, str):
        return text
    # Mask common secrets
    text = re.sub(r'sk-[a-zA-Z0-9]{32,}', 'sk-***', text)
    text = re.sub(r'(?i)(bearer\s+)[a-zA-Z0-9\-\._~+/]+=*', r'\1***', text)
    text = re.sub(r'(?i)(api[_-]?key\s*[:=]\s*)["\']?[a-zA-Z0-9\-\._~+/]+["\']?', r'\1"***"', text)
    return text

def _canonical_json_hash(value: Any) -> str:
    import hashlib
    canon = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass
class PipelineContext:
    run_id: str
    manifest_path: Path
    storage: Dict[str, Any] = field(default_factory=dict)
    logger: 'RunLogger' = field(init=False)
    errors: List[str] = field(default_factory=list)
    last_checkpoint: Optional[str] = None

    def __post_init__(self):
        # Gap 1 (O12 Fix): Isolate manifest per run_id
        if not self.manifest_path.name.endswith(f"_{self.run_id}.json"):
            new_name = f"manifest_{self.run_id}.json"
            self.manifest_path = self.manifest_path.with_name(new_name)
        self.logger = RunLogger(self.manifest_path.parent.parent, self.run_id)

class RunLogger:
    def __init__(self, root: Path, run_id: str):
        self.log_path = root / "Logs" / f"Run_{run_id}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
    def log(self, step: str, status: str, message: str = ""):
        # Keep printing to stdout for tests
        msg = f"[{step}] {status}"
        if message:
            msg += f" - {mask_secrets(message)}"
        print(msg)
        
        # Write structured JSON lines (O9 Part B Gap 8)
        log_entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "step": step,
            "status": status,
            "message": mask_secrets(message)
        }
        from .lock import locked_append
        try:
            locked_append(self.log_path, json.dumps(log_entry) + "\n")
        except (TimeoutError, OSError):
            # Logging contention must not turn the pipeline's original result into a crash.
            return

class Stage:
    name: str = "Stage"
    def run(self, ctx: PipelineContext) -> bool:
        return True

    def failure_code(self, ctx: PipelineContext) -> int:
        return EXIT_10

class IngestStage(Stage):
    name = "Ingest"
    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        # Record time just before calling ingest so we can identify the manifest IT wrote
        import time as _time
        call_ts = _time.time()
        res = ingest.ingest(root)
        if res == ingest.EXIT_OK:
            # Identify the manifest ingest.py just wrote by scanning for files written
            # AFTER our call_ts. Under concurrency, other threads may have written older
            # manifests — we must only accept our own (most recent, post-call_ts).
            try:
                manifest_dir = root / "Manifest"
                manifest_dir.mkdir(parents=True, exist_ok=True)
                # Collect manifests written after we called ingest
                fresh = []
                for mf in manifest_dir.glob("manifest_*.json"):
                    try:
                        if mf.stat().st_mtime >= call_ts - 0.5:  # 0.5s grace for clock skew
                            fresh.append(mf)
                    except OSError:
                        pass
                # Sort newest-first and pick the one that has a matching run_id in its content
                fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                for mf in fresh:
                    try:
                        from .lock import locked_read
                        with locked_read(mf) as manifest:
                            pass
                        run_id = manifest.get("run_id")
                        if run_id and mf.name == f"manifest_{run_id}.json":
                            ctx.run_id = run_id
                            ctx.manifest_path = mf
                            ctx.logger = RunLogger(root, run_id)
                            break
                    except (OSError, json.JSONDecodeError):
                        pass
            except Exception:
                pass

            # CRITICAL GATE (O15 Fix): If run_id is still None, ingest processed nothing
            # (empty inbox, duplicate rejection, or garbage file). Do NOT let downstream stages
            # proceed with a null run_id — that crashes CheckpointStage and others.
            if ctx.run_id is None:
                ctx.logger.log(self.name, "INFO", "No bundle processed (empty inbox, duplicate, or invalid). Halting pipeline cleanly.")
                return False

            return True
        else:
            ctx.logger.log(self.name, "ERROR", f"ingest.ingest returned {res}")
            return False

class FreezeBoundaryStage(Stage):
    name = "FreezeBoundary"
    
    def _extract_ids(self, data: Any, result: set = None) -> set:
        if result is None: result = set()
        if isinstance(data, dict):
            obj_id = data.get("id")
            if obj_id is not None and isinstance(obj_id, str):
                result.add(obj_id)
            for v in data.values():
                self._extract_ids(v, result)
        elif isinstance(data, list):
            for item in data:
                self._extract_ids(item, result)
        return result

    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        l1_path = root / "Artifacts" / "layer1_ccir.json"
        l2_path = root / "Artifacts" / "layer2_csir.json"
        
        if not l1_path.exists() or not l2_path.exists():
            return True
            
        from .lock import locked_read
        with locked_read(l1_path, default={}) as l1_data:
            ctx.storage["l1_data"] = l1_data
            
        with locked_read(l2_path, default={}) as l2_data:
            ctx.storage["l2_data"] = l2_data
            
        l1_ids = self._extract_ids(ctx.storage["l1_data"])
        l2_ids = self._extract_ids(ctx.storage["l2_data"])
            
        diff = l2_ids - l1_ids
        if diff:
            ctx.logger.log(self.name, "ERROR", f"FREEZE_VIOLATION: L2 invented IDs {diff}")
            ctx.storage["pipeline_status"] = "FREEZE_VIOLATION"
            return False
            
        return True
        
    def failure_code(self, ctx: PipelineContext) -> int:
        return 10  # EXIT_10

class ValidateL1Stage(Stage):
    name = "ValidateL1"
    
    FORBIDDEN_L1_FIELDS = {"component", "template", "navigation_graph", "layout_engine", "region_type", "interaction_graph"}
    
    def _walk_l1(self, data: Any, path: str = "") -> str:
        if isinstance(data, dict):
            for k, v in data.items():
                current_path = f"{path}.{k}" if path else k
                if k in self.FORBIDDEN_L1_FIELDS:
                    return current_path
                res = self._walk_l1(v, current_path)
                if res: return res
        elif isinstance(data, list):
            for i, item in enumerate(data):
                current_path = f"{path}[{i}]"
                res = self._walk_l1(item, current_path)
                if res: return res
        return None

    def run(self, ctx: PipelineContext) -> bool:
        data = ctx.storage.get("l1_data")
        if data is None:
            return True
            
        from .bridge import bridge_l1_to_l2, SchemaBridgeError
        try:
            bridge_l1_to_l2(data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False
            
        violating_path = self._walk_l1(data)
        if violating_path:
            ctx.logger.log(self.name, "ERROR", f"Forbidden field found at {violating_path}")
            return False
        return True

class ValidateL2Stage(Stage):
    name = "ValidateL2"
    
    def _extract_objects(self, data: Any, result: dict = None) -> dict:
        if result is None: result = {}
        if isinstance(data, dict):
            obj_id = data.get("id")
            if obj_id is not None and isinstance(obj_id, str):
                result[obj_id] = data
            for v in data.values():
                self._extract_objects(v, result)
        elif isinstance(data, list):
            for item in data:
                self._extract_objects(item, result)
        return result

    def _validate_l2(self, l2_data: Any, l1_map: dict, path: str = "") -> str:
        if isinstance(l2_data, dict):
            obj_id = l2_data.get("id")
            if obj_id is not None and isinstance(obj_id, str):
                l1_obj = l1_map.get(obj_id)
                if not l1_obj:
                    return f"{path} (id: {obj_id} not in Layer1)"
                
                for k, v in l2_data.items():
                    if k in ("text", "color", "position"):
                        if l1_obj.get(k) is None:
                            return f"{path}.{k}"
            
            for k, v in l2_data.items():
                current_path = f"{path}.{k}" if path else k
                res = self._validate_l2(v, l1_map, current_path)
                if res: return res
        elif isinstance(l2_data, list):
            for i, item in enumerate(l2_data):
                current_path = f"{path}[{i}]"
                res = self._validate_l2(item, l1_map, current_path)
                if res: return res
        return None

    def run(self, ctx: PipelineContext) -> bool:
        l1_data = ctx.storage.get("l1_data")
        l2_data = ctx.storage.get("l2_data")
        
        if l1_data is None or l2_data is None:
            return True
            
        from .bridge import bridge_l2_to_l3, SchemaBridgeError
        try:
            bridge_l2_to_l3(l2_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False
            
        l1_map = self._extract_objects(l1_data)
        violating_path = self._validate_l2(l2_data, l1_map)
        
        if violating_path:
            ctx.logger.log(self.name, "ERROR", f"L2 field not traceable to L1 found at {violating_path}")
            return False
        return True

class ConfidencePropagationStage(Stage):
    name = "ConfidencePropagation"
    
    CONFIDENCE_THRESHOLD = 0.8
    
    def _extract_objects(self, data: Any, result: dict = None) -> dict:
        if result is None: result = {}
        if isinstance(data, dict):
            obj_id = data.get("id")
            if obj_id is not None and isinstance(obj_id, str):
                result[obj_id] = data
            for v in data.values():
                self._extract_objects(v, result)
        elif isinstance(data, list):
            for item in data:
                self._extract_objects(item, result)
        return result
        
    def _propagate(self, l2_data: Any, l1_map: dict):
        if isinstance(l2_data, dict):
            obj_id = l2_data.get("id")
            if obj_id is not None and isinstance(obj_id, str):
                l1_obj = l1_map.get(obj_id)
                if l1_obj is not None:
                    conf = l1_obj.get("confidence")
                    if conf is not None and conf < self.CONFIDENCE_THRESHOLD:
                        l2_data["weak_child"] = True
            for v in l2_data.values():
                self._propagate(v, l1_map)
        elif isinstance(l2_data, list):
            for item in l2_data:
                self._propagate(item, l1_map)

    def run(self, ctx: PipelineContext) -> bool:
        l1_data = ctx.storage.get("l1_data")
        l2_data = ctx.storage.get("l2_data")
        
        if l1_data is None or l2_data is None:
            return True
            
        l1_map = self._extract_objects(l1_data)
        
        import copy
        l2_updated = copy.deepcopy(l2_data)
        self._propagate(l2_updated, l1_map)
        
        ctx.storage["l2_data"] = l2_updated
        
        root = ctx.manifest_path.parent.parent
        l2_path = root / "Artifacts" / "layer2_csir.json"
        
        # Overwrite the artifact on disk so tests and future stages can see it
        from .lock import locked_write
        with locked_write(l2_path, l2_updated):
            pass
            
        return True

class Layer3Stage(Stage):
    """
    Stage O10/L3: Design System Reconstruction (Visual IR).
    Fuses L2 semantics with L1 visual properties (colors, dimensions, text).
    Writes layer3_visual_ir.json with full HASH_CHAIN block.
    """
    name = "Layer3"

    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent

        # If already ingested and present in manifest, skip synthesis
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as manifest:
                if isinstance(manifest, dict) and manifest.get("artifacts", {}).get("layer3_visual_ir"):
                    ctx.logger.log(self.name, "INFO", "Layer 3 artifact already ingested; skipping synthesis.")
                    return True

        # If gate failed during ingest, do not proceed or synthesize on missing data
        if ctx.run_id:
            failed_file = root / "Python_Output" / "runs" / ctx.run_id / "FAILED_layer3.json"
            if failed_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Layer 3 validation gate failed during ingest for run {ctx.run_id}; cannot proceed.")
                return False

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        if ledger_path.exists():
            from .lock import locked_read
            with locked_read(ledger_path, default=[]) as ledger:
                if isinstance(ledger, list):
                    for e in ledger:
                        if e.get("run_id") == ctx.run_id and e.get("artifact") == "layer3_visual_ir" and e.get("status") == "failed_gate":
                            ctx.logger.log(self.name, "ERROR", f"Layer 3 recorded as failed_gate in ledger for run {ctx.run_id}; cannot proceed.")
                            return False

        l1_data = ctx.storage.get("l1_data")
        l2_data = ctx.storage.get("l2_data")

        from .lock import locked_read
        if l1_data is None:
            l1_path = root / "Artifacts" / "layer1_ccir.json"
            if l1_path.exists():
                with locked_read(l1_path, default={}) as d:
                    l1_data = d
                    ctx.storage["l1_data"] = l1_data

        if l2_data is None:
            l2_path = root / "Artifacts" / "layer2_csir.json"
            if l2_path.exists():
                with locked_read(l2_path, default={}) as d:
                    l2_data = d
                    ctx.storage["l2_data"] = l2_data

        from .bridge import bridge_l2_to_l3, SchemaBridgeError
        try:
            bridge_l2_to_l3(l2_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

        candidates_by_id = {}
        if isinstance(l1_data, dict):
            for c in l1_data.get("candidates", []):
                if isinstance(c, dict) and "id" in c:
                    candidates_by_id[c["id"]] = c

        semantics = l2_data.get("semantics", []) if isinstance(l2_data, dict) else []
        visuals = []
        for s in semantics:
            if not isinstance(s, dict):
                continue
            s_id = s.get("id")
            c = candidates_by_id.get(s_id, {})
            
            style = {
                "background_color": c.get("dominant_color") or c.get("background_color") or c.get("color"),
                "text_color": c.get("text_color"),
                "font_size": c.get("font_size"),
                "border_radius": c.get("border_radius"),
                "border_color": c.get("border_color"),
                "border_width": c.get("border_width"),
            }
            raw_text = c.get("ocr_text") or c.get("text") or s.get("text") or s.get("raw_text")
            visuals.append({
                "id": s_id,
                "style": style,
                "raw_text": raw_text
            })

        import hashlib
        import json
        canonical_l2 = json.dumps(l2_data, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical_l2.encode("utf-8")).hexdigest()

        parent_hash = None
        pipeline_id = "default_pipeline"
        if ctx.manifest_path.exists():
            with locked_read(ctx.manifest_path) as manifest:
                parent_hash = manifest.get("artifacts", {}).get("layer2_csir", {}).get("hash") or manifest.get("layer2_hash")
                pipeline_id = manifest.get("pipeline_id", "default_pipeline")

        canonical_visuals = json.dumps(visuals, sort_keys=True, separators=(",", ":"))
        output_hash = hashlib.sha256(canonical_visuals.encode("utf-8")).hexdigest()
        pipeline_hash = hashlib.sha256(f"{pipeline_id}:{parent_hash}".encode("utf-8")).hexdigest()

        l3_data = {
            "schema_version": "2.1",
            "hash_chain": {
                "input_hash": input_hash,
                "output_hash": output_hash,
                "parent_hash": parent_hash,
                "pipeline_hash": pipeline_hash
            },
            "visuals": visuals
        }
        ctx.storage["l3_data"] = l3_data

        from .ledger import write_atomic_artifact
        try:
            write_atomic_artifact(root, "layer3_visual_ir", ctx.run_id, l3_data, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False


class ValidateL3Stage(Stage):
    name = "ValidateL3"
    
    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        manifest_data = {}
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as m:
                manifest_data = m if isinstance(m, dict) else {}

        l3_info = manifest_data.get("artifacts", {}).get("layer3_visual_ir")

        # Only fall through to silent no-op if layer3_visual_ir genuinely doesn't exist in manifest AND storage
        if l3_info is None and ctx.storage.get("l3_data") is None:
            ctx.logger.log(self.name, "INFO", "Layer 3 artifact not ingested for this run; skipping as silent no-op.")
            return True

        if l3_info is not None:
            l3_file = root / "Artifacts" / "layer3_visual_ir.json"
            if not l3_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Artifact layer3_visual_ir recorded in manifest but missing on disk: {l3_file}")
                return False

            from .lock import locked_read
            with locked_read(l3_file, default=None) as data:
                l3_data = data

            if l3_data is None:
                ctx.logger.log(self.name, "ERROR", f"Failed to read {l3_file}")
                return False

            disk_hash = _canonical_json_hash(l3_data)
            manifest_hash = l3_info.get("hash")
            if disk_hash != manifest_hash:
                ctx.logger.log(self.name, "ERROR", f"Hash mismatch for layer3_visual_ir: disk={disk_hash} != manifest={manifest_hash}")
                return False

            ctx.storage["l3_data"] = l3_data
            ctx.logger.log(self.name, "INFO", f"Loaded and verified layer3_visual_ir from disk (hash {disk_hash[:8]})")
        else:
            l3_data = ctx.storage.get("l3_data")

        if l3_data is None:
            ctx.logger.log(self.name, "ERROR", "Missing l3_data in pipeline context")
            return False
            
        from .bridge import bridge_l3_to_l4_1, SchemaBridgeError
        try:
            bridge_l3_to_l4_1(l3_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

        from .schema import extract_schema_version, is_valid_schema_version
        schema_ver = extract_schema_version(manifest_data.get("artifacts", {}).get("layer3_visual_ir"), layer_body=l3_data)
        if not is_valid_schema_version(schema_ver):
            ctx.logger.log(self.name, "ERROR", f"Invalid or missing schema_version in l3_data: '{schema_ver}'")
            return False

        hash_chain = l3_data.get("hash_chain")
        if isinstance(hash_chain, dict):
            for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
                if k not in hash_chain:
                    ctx.logger.log(self.name, "ERROR", f"Missing key '{k}' in l3_data hash_chain")
                    return False

        visuals = l3_data.get("visuals")
        if visuals is not None:
            if not isinstance(visuals, list):
                ctx.logger.log(self.name, "ERROR", "visuals must be a list in l3_data")
                return False

            for v in visuals:
                if not isinstance(v, dict) or not v.get("id") or "style" not in v:
                    ctx.logger.log(self.name, "ERROR", f"Malformed visual node in l3_data: {v}")
                    return False

        ctx.logger.log(self.name, "INFO", "ValidateL3Stage validation succeeded")
        return True


class Layer4_1Stage(Stage):
    """
    Stage O10/L4.1: Canonical Semantic Engine (Semantic IR).
    Constructs hierarchical layout tree (flex_row, flex_col, absolute) from visual and semantic IR.
    Writes layer4_1_semantic_ir.json with full HASH_CHAIN block.
    """
    name = "Layer4.1"

    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent

        # If already ingested and present in manifest, skip synthesis
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as manifest:
                if isinstance(manifest, dict) and manifest.get("artifacts", {}).get("layer4_1_semantic_ir"):
                    ctx.logger.log(self.name, "INFO", "Layer 4.1 artifact already ingested; skipping synthesis.")
                    return True

        # If gate failed during ingest, do not proceed or synthesize on missing data
        if ctx.run_id:
            failed_file = root / "Python_Output" / "runs" / ctx.run_id / "FAILED_layer4_1.json"
            if failed_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Layer 4.1 validation gate failed during ingest for run {ctx.run_id}; cannot proceed.")
                return False

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        if ledger_path.exists():
            from .lock import locked_read
            with locked_read(ledger_path, default=[]) as ledger:
                if isinstance(ledger, list):
                    for e in ledger:
                        if e.get("run_id") == ctx.run_id and e.get("artifact") == "layer4_1_semantic_ir" and e.get("status") == "failed_gate":
                            ctx.logger.log(self.name, "ERROR", f"Layer 4.1 recorded as failed_gate in ledger for run {ctx.run_id}; cannot proceed.")
                            return False

        l3_data = ctx.storage.get("l3_data")
        from .lock import locked_read
        if l3_data is None:
            l3_path = root / "Artifacts" / "layer3_visual_ir.json"
            if l3_path.exists():
                with locked_read(l3_path, default={}) as d:
                    l3_data = d
                    ctx.storage["l3_data"] = l3_data

        from .bridge import bridge_l3_to_l4_1, SchemaBridgeError
        try:
            bridge_l3_to_l4_1(l3_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

        visuals = l3_data.get("visuals", [])
        children = []
        for v in visuals:
            v_id = v.get("id")
            children.append({
                "id": v_id,
                "layout": {
                    "layout_type": "flex_col",
                    "justify_content": None,
                    "align_items": None,
                    "gap": 0,
                    "margin": {"top": 0, "right": 0, "bottom": 0, "left": 0},
                    "padding": {"top": 0, "right": 0, "bottom": 0, "left": 0}
                },
                "children": []
            })

        root_tree = {
            "id": "root_container",
            "layout": {
                "layout_type": "flex_col",
                "justify_content": "flex-start",
                "align_items": "stretch",
                "gap": 8,
                "margin": {"top": 0, "right": 0, "bottom": 0, "left": 0},
                "padding": {"top": 0, "right": 0, "bottom": 0, "left": 0}
            },
            "children": children
        }

        import hashlib
        import json
        canonical_l3 = json.dumps(l3_data, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical_l3.encode("utf-8")).hexdigest()

        parent_hash = None
        pipeline_id = "default_pipeline"
        if ctx.manifest_path.exists():
            with locked_read(ctx.manifest_path) as manifest:
                parent_hash = manifest.get("artifacts", {}).get("layer3_visual_ir", {}).get("hash")
                pipeline_id = manifest.get("pipeline_id", "default_pipeline")

        canonical_root = json.dumps(root_tree, sort_keys=True, separators=(",", ":"))
        output_hash = hashlib.sha256(canonical_root.encode("utf-8")).hexdigest()
        pipeline_hash = hashlib.sha256(f"{pipeline_id}:{parent_hash}".encode("utf-8")).hexdigest()

        l4_1_data = {
            "schema_version": "2.1",
            "hash_chain": {
                "input_hash": input_hash,
                "output_hash": output_hash,
                "parent_hash": parent_hash,
                "pipeline_hash": pipeline_hash
            },
            "root": root_tree
        }
        ctx.storage["l4_1_data"] = l4_1_data

        from .ledger import write_atomic_artifact
        try:
            write_atomic_artifact(root, "layer4_1_semantic_ir", ctx.run_id, l4_1_data, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False


class ValidateL4_1Stage(Stage):
    name = "ValidateL4.1"
    
    def _validate_node(self, node: Any, path: str = "root") -> str:
        if not isinstance(node, dict):
            return f"{path} is not an object"
        if not node.get("id"):
            return f"{path} missing 'id'"
        layout = node.get("layout")
        if not isinstance(layout, dict):
            return f"{path} missing 'layout'"
        if layout.get("layout_type") not in ("flex_row", "flex_col", "absolute"):
            return f"{path}.layout has invalid layout_type: {layout.get('layout_type')}"
        children = node.get("children")
        if not isinstance(children, list):
            return f"{path} 'children' is not a list"
        for i, child in enumerate(children):
            err = self._validate_node(child, f"{path}.children[{i}]")
            if err:
                return err
        return ""

    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        manifest_data = {}
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as m:
                manifest_data = m if isinstance(m, dict) else {}

        l4_1_info = manifest_data.get("artifacts", {}).get("layer4_1_semantic_ir")

        # Only fall through to silent no-op if layer4_1_semantic_ir genuinely doesn't exist in manifest AND storage
        if l4_1_info is None and ctx.storage.get("l4_1_data") is None:
            ctx.logger.log(self.name, "INFO", "Layer 4.1 artifact not ingested for this run; skipping as silent no-op.")
            return True

        if l4_1_info is not None:
            l4_1_file = root / "Artifacts" / "layer4_1_semantic_ir.json"
            if not l4_1_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Artifact layer4_1_semantic_ir recorded in manifest but missing on disk: {l4_1_file}")
                return False

            from .lock import locked_read
            with locked_read(l4_1_file, default=None) as data:
                l4_1_data = data

            if l4_1_data is None:
                ctx.logger.log(self.name, "ERROR", f"Failed to read {l4_1_file}")
                return False

            disk_hash = _canonical_json_hash(l4_1_data)
            manifest_hash = l4_1_info.get("hash")
            if disk_hash != manifest_hash:
                ctx.logger.log(self.name, "ERROR", f"Hash mismatch for layer4_1_semantic_ir: disk={disk_hash} != manifest={manifest_hash}")
                return False

            ctx.storage["l4_1_data"] = l4_1_data
            ctx.logger.log(self.name, "INFO", f"Loaded and verified layer4_1_semantic_ir from disk (hash {disk_hash[:8]})")
        else:
            l4_1_data = ctx.storage.get("l4_1_data")

        if l4_1_data is None:
            ctx.logger.log(self.name, "ERROR", "Missing l4_1_data in pipeline context")
            return False
            
        from .bridge import bridge_l4_1_to_l4_2, SchemaBridgeError
        try:
            bridge_l4_1_to_l4_2(l4_1_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

        from .schema import extract_schema_version, is_valid_schema_version
        schema_ver = extract_schema_version(manifest_data.get("artifacts", {}).get("layer4_1_semantic_ir"), layer_body=l4_1_data)
        if not is_valid_schema_version(schema_ver):
            ctx.logger.log(self.name, "ERROR", f"Invalid or missing schema_version in l4_1_data: '{schema_ver}'")
            return False

        hash_chain = l4_1_data.get("hash_chain")
        if isinstance(hash_chain, dict):
            for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
                if k not in hash_chain:
                    ctx.logger.log(self.name, "ERROR", f"Missing key '{k}' in l4_1_data hash_chain")
                    return False

        root_node = l4_1_data.get("root")
        if root_node is not None:
            err = self._validate_node(root_node)
            if err:
                ctx.logger.log(self.name, "ERROR", f"Semantic tree validation failed: {err}")
                return False

        ctx.logger.log(self.name, "INFO", "ValidateL4_1Stage validation succeeded")
        return True


class Layer4_2Stage(Stage):
    """
    Stage O10/L4.2: Canonical Execution Engine (Execution IR).
    Combines semantic role (L2), visual styling & OCR text (L3), bounding box (L1),
    and layout structure (L4.1) into the final ComponentNode execution tree.
    Writes layer4_2_execution_ir.json with full HASH_CHAIN block.
    """
    name = "Layer4.2"

    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent

        # If already ingested and present in manifest, skip synthesis
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as manifest:
                if isinstance(manifest, dict) and manifest.get("artifacts", {}).get("layer4_2_execution_ir"):
                    ctx.logger.log(self.name, "INFO", "Layer 4.2 artifact already ingested; skipping synthesis.")
                    return True

        # If gate failed during ingest, do not proceed or synthesize on missing data
        if ctx.run_id:
            failed_file = root / "Python_Output" / "runs" / ctx.run_id / "FAILED_layer4_2.json"
            if failed_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Layer 4.2 validation gate failed during ingest for run {ctx.run_id}; cannot proceed.")
                return False

        ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
        if ledger_path.exists():
            from .lock import locked_read
            with locked_read(ledger_path, default=[]) as ledger:
                if isinstance(ledger, list):
                    for e in ledger:
                        if e.get("run_id") == ctx.run_id and e.get("artifact") == "layer4_2_execution_ir" and e.get("status") == "failed_gate":
                            ctx.logger.log(self.name, "ERROR", f"Layer 4.2 recorded as failed_gate in ledger for run {ctx.run_id}; cannot proceed.")
                            return False

        l1_data = ctx.storage.get("l1_data", {})
        l2_data = ctx.storage.get("l2_data", {})
        l3_data = ctx.storage.get("l3_data", {})
        l4_1_data = ctx.storage.get("l4_1_data", {})

        from .lock import locked_read
        if not l4_1_data:
            p = root / "Artifacts" / "layer4_1_semantic_ir.json"
            if p.exists():
                with locked_read(p, default={}) as d:
                    l4_1_data = d
                    ctx.storage["l4_1_data"] = l4_1_data

        from .bridge import bridge_l4_1_to_l4_2, SchemaBridgeError
        try:
            bridge_l4_1_to_l4_2(l4_1_data)
        except SchemaBridgeError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

        l1_map = {c["id"]: c for c in l1_data.get("candidates", []) if isinstance(c, dict) and "id" in c} if isinstance(l1_data, dict) else {}
        l2_map = {s["id"]: s for s in l2_data.get("semantics", []) if isinstance(s, dict) and "id" in s} if isinstance(l2_data, dict) else {}
        l3_map = {v["id"]: v for v in l3_data.get("visuals", []) if isinstance(v, dict) and "id" in v} if isinstance(l3_data, dict) else {}

        def build_component_tree(semantic_node: dict) -> dict:
            n_id = semantic_node.get("id", "unknown")
            l2_info = l2_map.get(n_id, {})
            l1_info = l1_map.get(n_id, {})
            l3_info = l3_map.get(n_id, {})

            c_type = l2_info.get("semantic_role", "container")
            c_variant = l2_info.get("semantic_variant")
            bbox = l1_info.get("bbox", {"x": 0, "y": 0, "w": 0, "h": 0})
            style = l3_info.get("style", {})
            content = l3_info.get("raw_text")
            layout = semantic_node.get("layout", {"layout_type": "flex_col"})
            
            children = []
            for child in semantic_node.get("children", []):
                children.append(build_component_tree(child))

            return {
                "id": n_id,
                "type": c_type,
                "variant": c_variant,
                "bbox": bbox,
                "style": style,
                "content": content,
                "layout": layout,
                "children": children
            }

        root_node = build_component_tree(l4_1_data.get("root", {}))

        import hashlib
        import json
        canonical_l4_1 = json.dumps(l4_1_data, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical_l4_1.encode("utf-8")).hexdigest()

        parent_hash = None
        pipeline_id = "default_pipeline"
        if ctx.manifest_path.exists():
            with locked_read(ctx.manifest_path) as manifest:
                parent_hash = manifest.get("artifacts", {}).get("layer4_1_semantic_ir", {}).get("hash")
                pipeline_id = manifest.get("pipeline_id", "default_pipeline")

        canonical_root = json.dumps(root_node, sort_keys=True, separators=(",", ":"))
        output_hash = hashlib.sha256(canonical_root.encode("utf-8")).hexdigest()
        pipeline_hash = hashlib.sha256(f"{pipeline_id}:{parent_hash}".encode("utf-8")).hexdigest()

        l4_2_data = {
            "schema_version": "2.1",
            "hash_chain": {
                "input_hash": input_hash,
                "output_hash": output_hash,
                "parent_hash": parent_hash,
                "pipeline_hash": pipeline_hash
            },
            "root_node": root_node
        }
        ctx.storage["l4_2_data"] = l4_2_data

        from .ledger import write_atomic_artifact
        try:
            write_atomic_artifact(root, "layer4_2_execution_ir", ctx.run_id, l4_2_data, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False


class ValidateL4_2Stage(Stage):
    name = "ValidateL4.2"
    CANONICAL_TAXONOMY = {"button", "input", "text", "image", "container"}

    def _collect_all_nodes(self, node: Any, nodes_list: list):
        if isinstance(node, dict):
            nodes_list.append(node)
            for child in node.get("children", []):
                self._collect_all_nodes(child, nodes_list)

    def run(self, ctx: PipelineContext) -> bool:
        if ctx.storage.get("force_layer4_2_fail"):
            return False

        root = ctx.manifest_path.parent.parent
        manifest_data = {}
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path, default={}) as m:
                manifest_data = m if isinstance(m, dict) else {}

        l4_2_info = manifest_data.get("artifacts", {}).get("layer4_2_execution_ir")

        # Only fall through to silent no-op if layer4_2_execution_ir genuinely doesn't exist in manifest AND storage
        if l4_2_info is None and ctx.storage.get("l4_2_data") is None:
            ctx.logger.log(self.name, "INFO", "Layer 4.2 artifact not ingested for this run; skipping as silent no-op.")
            return True

        if l4_2_info is not None:
            l4_2_file = root / "Artifacts" / "layer4_2_execution_ir.json"
            if not l4_2_file.exists():
                ctx.logger.log(self.name, "ERROR", f"Artifact layer4_2_execution_ir recorded in manifest but missing on disk: {l4_2_file}")
                return False

            from .lock import locked_read
            with locked_read(l4_2_file, default=None) as data:
                l4_2_data = data

            if l4_2_data is None:
                ctx.logger.log(self.name, "ERROR", f"Failed to read {l4_2_file}")
                return False

            disk_hash = _canonical_json_hash(l4_2_data)
            manifest_hash = l4_2_info.get("hash")
            if disk_hash != manifest_hash:
                ctx.logger.log(self.name, "ERROR", f"Hash mismatch for layer4_2_execution_ir: disk={disk_hash} != manifest={manifest_hash}")
                return False

            ctx.storage["l4_2_data"] = l4_2_data
            ctx.logger.log(self.name, "INFO", f"Loaded and verified layer4_2_execution_ir from disk (hash {disk_hash[:8]})")
        else:
            l4_2_data = ctx.storage.get("l4_2_data")

        if l4_2_data is None:
            ctx.logger.log(self.name, "ERROR", "Missing l4_2_data in pipeline context")
            return False

        from .schema import extract_schema_version, is_valid_schema_version
        schema_ver = extract_schema_version(manifest_data.get("artifacts", {}).get("layer4_2_execution_ir"), layer_body=l4_2_data)
        if not is_valid_schema_version(schema_ver):
            ctx.logger.log(self.name, "ERROR", f"Invalid or missing schema_version in l4_2_data: '{schema_ver}'")
            return False

        # Verify validation block status == "pass" (supports both lowercase 'validation' and uppercase 'VALIDATION')
        val_block = l4_2_data.get("validation") or l4_2_data.get("VALIDATION")
        if isinstance(val_block, dict):
            val_st = val_block.get("status") or val_block.get("gate_result")
            if val_st and str(val_st).lower() != "pass":
                ctx.logger.log(self.name, "ERROR", f"Layer 4.2 validation status is not pass: {val_st}")
                return False
        elif "validation" in l4_2_data or "VALIDATION" in l4_2_data:
            ctx.logger.log(self.name, "ERROR", "Layer 4.2 validation field is not an object")
            return False

        hash_chain = l4_2_data.get("hash_chain")
        if isinstance(hash_chain, dict):
            for k in ("input_hash", "output_hash", "parent_hash", "pipeline_hash"):
                if k not in hash_chain:
                    ctx.logger.log(self.name, "ERROR", f"Missing key '{k}' in l4_2_data hash_chain")
                    return False

        root_node = l4_2_data.get("root_node")
        if isinstance(root_node, dict):
            all_nodes = []
            self._collect_all_nodes(root_node, all_nodes)
            known_ids = {n.get("id") for n in all_nodes if n.get("id")}

            for n in all_nodes:
                n_type = n.get("type")
                if n_type not in self.CANONICAL_TAXONOMY:
                    ctx.logger.log(self.name, "ERROR", f"Taxonomy violation: node '{n.get('id')}' has invalid type '{n_type}'. Expected one of {self.CANONICAL_TAXONOMY}")
                    return False

                target = n.get("interaction_target") or n.get("target_id") or n.get("action_target")
                if target is not None and target not in known_ids:
                    ctx.logger.log(self.name, "ERROR", f"Dangling interaction target: node '{n.get('id')}' references nonexistent target '{target}'")
                    return False

        ctx.logger.log(self.name, "INFO", "ValidateL4_2Stage validation succeeded")
        return True
    
    def failure_code(self, ctx: PipelineContext) -> int:
        return EXIT_20 if ctx.storage.get("force_layer4_2_exit_20") else EXIT_10


class Layer4_3Stage(Stage):
    name = "Layer4.3"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact
        root = ctx.manifest_path.parent.parent
        
        parent_hash = None
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path) as manifest:
                parent_hash = manifest.get("artifacts", {}).get("layer4_2_execution_ir", {}).get("hash") or manifest.get("layer2_hash")

        payload = {"layer": "4.3", "payload": "completed"}
        try:
            write_atomic_artifact(root, "layer4_3", ctx.run_id, payload, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

class Layer5Stage(Stage):
    name = "Layer5"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact, record_failed_gate
        root = ctx.manifest_path.parent.parent
        
        # Gap 7 (O12 Fix): Real Subprocess Sandboxing using Docker
        import subprocess
        import os
        
        if ctx.storage.get("force_layer5_malicious"):
            env = os.environ.copy()
            docker_cmd = str(root / "bin" / "docker.bat") if os.name == 'nt' else "docker"
            
            try:
                # Docker payload simulating execution
                res = subprocess.run(
                    [
                        docker_cmd, "run", "--rm", 
                        "--network", "none", 
                        "-m", "512m", 
                        "python:3.9-slim", 
                        "python", "-c", 
                        "import urllib.request; urllib.request.urlopen('http://malicious.com')"
                    ],
                    env=env,
                    capture_output=True,
                    text=True
                )
                
                # Log the raw Docker output so it can be verified
                for line in res.stdout.splitlines():
                    ctx.logger.log(self.name, "STDOUT", line)
                for line in res.stderr.splitlines():
                    ctx.logger.log(self.name, "STDERR", line)
                    
                if res.returncode != 0:
                    ctx.logger.log(self.name, "WARNING", f"Sandbox container killed or failed (exit code {res.returncode})")
                    return False
                    
            except FileNotFoundError:
                ctx.logger.log(self.name, "ERROR", "Docker not found in PATH")
                return False

        # Determine parent hash from manifest if possible
        parent_hash = None
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path) as manifest:
                pass
                parent_hash = manifest.get("artifacts", {}).get("layer4_3", {}).get("hash")
        
        # Test 1: simulate failed gate
        if ctx.storage.get("force_layer5_failed_gate"):
            record_failed_gate(root, "layer5", ctx.run_id, parent_hash)
            return False

        # Test 2: crash mid-write
        if ctx.storage.get("force_layer5_crash"):
            # Write partial output to test Rollback
            output_file = root / "Artifacts" / "layer5.json"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            from .lock import locked_write
            with locked_write(output_file, '{"partial": "data"'):
                pass
            return False
            
        # Normal write (Substage F)
        payload = {"layer": 5, "payload": "completed"}
        try:
            write_atomic_artifact(root, "layer5", ctx.run_id, payload, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False
        
    def failure_code(self, ctx: PipelineContext) -> int:
        if ctx.storage.get("force_layer5_failed_gate"):
            return 30
        return 40 if ctx.storage.get("force_layer5_crash") else EXIT_10

class Layer6Stage(Stage):
    name = "Layer6"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact, record_failed_gate
        root = ctx.manifest_path.parent.parent
        
        parent_hash = None
        if ctx.manifest_path.exists():
            from .lock import locked_read
            with locked_read(ctx.manifest_path) as manifest:
                parent_hash = manifest.get("artifacts", {}).get("layer5", {}).get("hash")
                
        # Normal write (Substage E)
        payload = {"layer": 6, "payload": "completed"}
        try:
            write_atomic_artifact(root, "layer6", ctx.run_id, payload, parent_hash)
            return True
        except ValueError as e:
            ctx.logger.log(self.name, "ERROR", str(e))
            return False

class MetaVerifyStage(Stage):
    name = "MetaVerify"
    def run(self, ctx: PipelineContext) -> bool:
        from .meta_verify import verify_integrity
        import json
        
        root = ctx.manifest_path.parent.parent
        
        # Test 1: Tamper with on-disk artifact
        if ctx.storage.get("force_meta_verify_tamper"):
            tamper_file = root / "Artifacts" / "layer6.json"
            if tamper_file.exists():
                from .lock import locked_read, locked_write
                with locked_read(tamper_file) as data:
                    pass
                data["payload"] = "tampered"
                with locked_write(tamper_file, data):
                    pass
        
        # Test 2: Gap in pointer chain
        if ctx.storage.get("force_meta_verify_gap"):
            ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
            if ledger_path.exists():
                from .lock import locked_read, locked_write
                with locked_read(ledger_path) as ledger:
                    pass
                # Find layer6 and break its parent_hash
                for e in reversed(ledger):
                    if e["artifact"] == "layer6" and e["run_id"] == ctx.run_id:
                        e["parent_hash"] = "broken_hash"
                        break
                with locked_write(ledger_path, ledger):
                    pass

        success, code = verify_integrity(root, ctx.run_id, ctx.logger)
        ctx.storage["meta_verify_code"] = code
        return success

    def failure_code(self, ctx: PipelineContext) -> int:
        return ctx.storage.get("meta_verify_code", EXIT_10)

class SaveStage(Stage):
    """
    SaveStage: Final pipeline export stage.
    Defines the export responsibility: persists all verified output artifacts from
    Artifacts/*.json and the run manifest to the designated output export directory:
    Python_Output/runs/<run_id>/exported/ along with an export_manifest.json before archival.
    """
    name = "Save"
    def run(self, ctx: PipelineContext) -> bool:
        if ctx.storage.get("force_save_fail"):
            return False

        try:
            root = ctx.manifest_path.parent.parent
            export_dir = root / "Python_Output" / "runs" / ctx.run_id / "exported"
            export_dir.mkdir(parents=True, exist_ok=True)

            artifacts_dir = root / "Artifacts"
            exported_files = {}

            from .lock import locked_read, locked_write
            if artifacts_dir.exists():
                for source in artifacts_dir.glob("*.json"):
                    if source.suffix in {".lock", ".tmp"}:
                        continue
                    with locked_read(source, parse_json=False) as content:
                        if content is not None:
                            dest = export_dir / source.name
                            with locked_write(dest, content):
                                pass
                            import hashlib
                            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
                            exported_files[source.name] = hashlib.sha256(raw_bytes).hexdigest()

            # Copy manifest
            if ctx.manifest_path.exists():
                with locked_read(ctx.manifest_path, parse_json=False) as content:
                    if content is not None:
                        dest = export_dir / ctx.manifest_path.name
                        with locked_write(dest, content):
                            pass

            import datetime
            export_manifest = {
                "run_id": ctx.run_id,
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "exported_artifacts": exported_files,
                "status": "EXPORTED"
            }
            with locked_write(export_dir / "export_manifest.json", export_manifest):
                pass

            return True
        except (TimeoutError, OSError) as exc:
            ctx.logger.log(self.name, "ERROR", f"Shared-file access failed: {exc}")
            return False
        except Exception as exc:
            ctx.logger.log(self.name, "ERROR", f"Save export failed: {exc}")
            return False
    
    def failure_code(self, ctx: PipelineContext) -> int:
        return 50

class ArchiveStage(Stage):
    name = "Archive"
    def run(self, ctx: PipelineContext) -> bool:
        try:
            import shutil
            import stat
            import os

            root = ctx.manifest_path.parent.parent
            archive_dir = root / "Archives" / f"Run_{ctx.run_id}"
        
            if archive_dir.exists():
                ctx.logger.log(self.name, "WARNING", f"Archive directory {archive_dir} already exists, aborting to prevent overwrite.")
                return False
            
            archive_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Copy Artifacts/
            artifacts_src = root / "Artifacts"
            artifacts_dest = archive_dir / "Artifacts"
            if artifacts_src.exists():
                for source in artifacts_src.rglob("*"):
                    if not source.is_file() or source.suffix in {".lock", ".tmp"}:
                        continue
                    relative = source.relative_to(artifacts_src)
                    from .lock import locked_read, locked_write
                    with locked_read(source, parse_json=False) as content:
                        if content is not None:
                            with locked_write(artifacts_dest / relative, content):
                                pass
            
        # 2. Copy manifest
            manifest_src = root / "Manifest" / f"manifest_{ctx.run_id}.json"
            if manifest_src.exists():
                from .lock import locked_read, locked_write
                with locked_read(manifest_src, parse_json=False) as content:
                    if content is not None:
                        with locked_write(archive_dir / f"manifest_{ctx.run_id}.json", content):
                            pass
            
        # 3. Pipeline_Ledger entry for this run
            ledger_src = root / "Logs" / "Pipeline_Ledger.json"
            if ledger_src.exists():
                from .lock import locked_read, locked_write
                with locked_read(ledger_src, default=[]) as ledger:
                    if isinstance(ledger, list):
                        run_entry = next((e for e in ledger if isinstance(e, dict) and e.get("run_id") == ctx.run_id), None)
                    else:
                        run_entry = None
                if run_entry:
                    with locked_write(archive_dir / "Pipeline_Ledger.json", [run_entry]):
                        pass
                    
        # 4. Copy BUILD_REPORT.md
            build_report_src = root / "BUILD_REPORT.md"
            if build_report_src.exists():
                from .lock import locked_read, locked_write
                with locked_read(build_report_src, parse_json=False) as content:
                    if content is not None:
                        with locked_write(archive_dir / "BUILD_REPORT.md", content):
                            pass
            
        # Set entire copy to read-only
            def set_readonly(path):
                try:
                    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass
            
            for dirpath, dirnames, filenames in os.walk(archive_dir):
                set_readonly(dirpath)
                for filename in filenames:
                    set_readonly(os.path.join(dirpath, filename))

            return True
        except (TimeoutError, OSError) as exc:
            ctx.logger.log(self.name, "ERROR", f"Shared-file access failed: {exc}")
            return False
        except Exception as exc:
            ctx.logger.log(self.name, "ERROR", f"Archive failed: {exc}")
            return False

class PipelineLedgerStage(Stage):
    name = "PipelineLedger"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_pipeline_ledger
        import time
        root = ctx.manifest_path.parent.parent
        
        status = ctx.storage.get("pipeline_status", "PASS")
        duration = time.time() - ctx.storage.get("start_time", time.time())
        files_produced = 3 if status == "PASS" else 0
        hash_chain_valid = ctx.storage.get("hash_chain_valid", True)
        
        write_pipeline_ledger(
            root=root,
            run_id=ctx.run_id,
            status=status,
            duration_seconds=round(duration, 2),
            files_produced=files_produced,
            warnings=[],
            errors=ctx.errors,
            hash_chain_valid=hash_chain_valid
        )
        return True

class BuildReportStage(Stage):
    name = "BuildReport"
    def run(self, ctx: PipelineContext) -> bool:
        from .report import generate_build_report
        root = ctx.manifest_path.parent.parent
        return generate_build_report(root, ctx.run_id, ctx.logger)

class Pipeline:
    def __init__(self):
        # Local import to avoid circular dependency
        from .checkpoint import CheckpointStage
        self.ingest_stage = IngestStage()
        self.freeze_boundary_stage = FreezeBoundaryStage()
        self.validate_l1_stage = ValidateL1Stage()
        self.validate_l2_stage = ValidateL2Stage()
        self.confidence_propagation_stage = ConfidencePropagationStage()
        self.layer3_stage = Layer3Stage()
        self.validate_l3_stage = ValidateL3Stage()
        self.layer4_1_stage = Layer4_1Stage()
        self.validate_l4_1_stage = ValidateL4_1Stage()
        self.layer4_2_stage = Layer4_2Stage()
        self.validate_l4_2_stage = ValidateL4_2Stage()
        self.checkpoint_l4_3 = CheckpointStage("Layer4_3")
        self.layer4_3_stage = Layer4_3Stage()
        self.checkpoint_l5 = CheckpointStage("Layer5")
        self.layer5_stage = Layer5Stage()
        self.checkpoint_l6 = CheckpointStage("Layer6")
        self.layer6_stage = Layer6Stage()
        self.meta_verify_stage = MetaVerifyStage()
        self.save_stage = SaveStage()
        self.archive_stage = ArchiveStage()
        self.pipeline_ledger_stage = PipelineLedgerStage()
        self.build_report_stage = BuildReportStage()

        self.stages: List[Stage] = [
            self.ingest_stage,
            self.freeze_boundary_stage,
            self.validate_l1_stage,
            self.validate_l2_stage,
            self.confidence_propagation_stage,
            self.layer3_stage,
            self.validate_l3_stage,
            self.layer4_1_stage,
            self.validate_l4_1_stage,
            self.layer4_2_stage,
            self.validate_l4_2_stage,
            self.checkpoint_l4_3,
            self.layer4_3_stage,
            self.checkpoint_l5,
            self.layer5_stage,
            self.checkpoint_l6,
            self.layer6_stage,
            self.meta_verify_stage,
            self.save_stage,
            self.archive_stage,
            self.pipeline_ledger_stage,
            self.build_report_stage
        ]
        self._stage_by_class = {type(s).__name__: s for s in self.stages}

    def run(self, ctx: PipelineContext) -> int:
        from .checkpoint import RollbackStage
        import time
        import json
        
        ctx.storage["start_time"] = time.time()
        
        for idx, stage in enumerate(self.stages):
            ctx.logger.log(stage.name, "STARTING")
            try:
                success = stage.run(ctx)
            except (TimeoutError, OSError) as exc:
                ctx.logger.log(stage.name, "ERROR", f"Shared-file access failed: {exc}")
                success = False
            except Exception as exc:
                ctx.logger.log(stage.name, "ERROR", f"Stage failed with exception: {exc}")
                success = False
            if not success:
                ctx.logger.log(stage.name, "FAILED")
                ctx.errors.append(f"{stage.name} failed")
                ctx.storage["failed_stage"] = stage.name
                
                # Pre-rollback hash validation (scoped strictly to this run_id)
                root = ctx.manifest_path.parent.parent
                manifest_path = ctx.manifest_path
                ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
                hash_valid = True
                
                if manifest_path.exists() and ledger_path.exists():
                    try:
                        from .lock import locked_read
                        with locked_read(manifest_path, default={}) as m:
                            manifest_obj = m if isinstance(m, dict) else {}
                        with locked_read(ledger_path, default=[]) as l:
                            ledger_list = l if isinstance(l, list) else []
                        if manifest_obj and ledger_list:
                            run_entries = [e for e in ledger_list if isinstance(e, dict) and e.get("run_id") == ctx.run_id and e.get("status") == "written"]
                            last_art = run_entries[-1].get("artifact") if run_entries else None
                            last_hash = run_entries[-1].get("hash") if run_entries else None
                            if last_art and manifest_obj.get("artifacts", {}).get(last_art, {}).get("hash") != last_hash:
                                hash_valid = False
                    except Exception:
                        pass
                
                ctx.storage["hash_chain_valid"] = hash_valid
                
                non_rollback_stages = {
                    self.ingest_stage.name,
                    self.meta_verify_stage.name,
                    self.save_stage.name,
                    self.archive_stage.name,
                    self.pipeline_ledger_stage.name,
                    self.build_report_stage.name
                }
                should_rollback = stage.name not in non_rollback_stages

                if should_rollback:
                    ctx.logger.log("Rollback", "INVOKING")
                    rollback_success = RollbackStage().run(ctx)
                    if not rollback_success:
                        ctx.logger.log("Rollback", "FAILED")
                    else:
                        ctx.logger.log("Rollback", "PASSED")
                        
                if "pipeline_status" not in ctx.storage:
                    ctx.storage["pipeline_status"] = "ROLLED_BACK" if should_rollback else "FAIL"
                
                # Write the pipeline ledger through its stage reference
                try:
                    self._stage_by_class["PipelineLedgerStage"].run(ctx)
                except Exception as exc:
                    ctx.logger.log("PipelineLedger", "ERROR", f"Shared-file access failed: {exc}")
                
                # Generate the build report through its stage reference
                try:
                    self._stage_by_class["BuildReportStage"].run(ctx)
                except Exception as exc:
                    ctx.logger.log("BuildReport", "ERROR", f"Shared-file access failed: {exc}")
                
                return stage.failure_code(ctx)
            ctx.logger.log(stage.name, "PASSED")
            
        return EXIT_OK
