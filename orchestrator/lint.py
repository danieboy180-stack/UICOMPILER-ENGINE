from pathlib import Path
from typing import Any, List, Set
import json

def _get_all_strings(data: Any) -> Set[str]:
    strs = set()
    if isinstance(data, dict):
        for v in data.values():
            strs.update(_get_all_strings(v))
    elif isinstance(data, list):
        for item in data:
            strs.update(_get_all_strings(item))
    elif isinstance(data, str):
        strs.add(data)
    return strs

def _check_fields(data: Any, target_fields: Set[str], allowed_values: Set[str], path: str = "") -> List[tuple]:
    violations = []
    if isinstance(data, dict):
        for k, v in data.items():
            current_path = f"{path}.{k}" if path else k
            if k in target_fields and isinstance(v, str):
                if v not in allowed_values:
                    violations.append((current_path, v))
            violations.extend(_check_fields(v, target_fields, allowed_values, current_path))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            current_path = f"{path}[{i}]"
            violations.extend(_check_fields(item, target_fields, allowed_values, current_path))
    return violations

def post_run_lint(archive_dir: Path) -> List[str]:
    violations = []
    
    # Load artifacts if they exist
    l1_data = {}
    l2_data = {}
    l3_data = {}
    
    l1_path = archive_dir / "Artifacts" / "layer1_ccir.json"
    if l1_path.exists():
        with open(l1_path, 'r', encoding='utf-8') as f:
            try: l1_data = json.load(f)
            except: pass
            
    l2_path = archive_dir / "Artifacts" / "layer2_csir.json"
    if l2_path.exists():
        with open(l2_path, 'r', encoding='utf-8') as f:
            try: l2_data = json.load(f)
            except: pass
            
    l3_path = archive_dir / "Artifacts" / "layer3_visual_ir.json"
    if l3_path.exists():
        with open(l3_path, 'r', encoding='utf-8') as f:
            try: l3_data = json.load(f)
            except: pass
            
    l1_strings = _get_all_strings(l1_data)
    l2_strings = _get_all_strings(l2_data)
    
    if l2_data and l1_strings:
        # Check text in L2
        l2_text_violations = _check_fields(l2_data, {"text", "raw_text"}, l1_strings, "")
        for path, val in l2_text_violations:
            violations.append(f"Layer2: {path}: OCR text '{val}' not found verbatim in Layer 1")
            
    if l3_data and (l1_strings or l2_strings):
        l12_strings = l1_strings | l2_strings
        l3_color_violations = _check_fields(l3_data, {"color", "background_color", "text_color"}, l12_strings, "")
        for path, val in l3_color_violations:
            violations.append(f"Layer3: {path}: Color '{val}' not derivable from Layer 1/2")
            
    return violations
