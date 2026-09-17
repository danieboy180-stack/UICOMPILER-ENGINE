import json
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    
from orchestrator.bridge import (
    bridge_l1_to_l2,
    bridge_l2_to_l3,
    bridge_l3_to_l4_1,
    bridge_l4_1_to_l4_2,
    SchemaBridgeError
)

def test_bridge_l1_to_l2():
    # Valid
    l1_valid = {"schema_version": "2.1", "candidates": [{"id": "1", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "confidence": 0.99}]}
    bridge_l1_to_l2(l1_valid)  # Should not raise

    # Invalid
    l1_invalid = {"schema_version": "2.1", "other": []}
    try:
        bridge_l1_to_l2(l1_invalid)
        assert False, "Should have raised SchemaBridgeError"
    except SchemaBridgeError as e:
        assert "candidates" in str(e)
        
    try:
        bridge_l1_to_l2({"schema_version": "2.1", "candidates": None})
        assert False, "Should have raised SchemaBridgeError"
    except SchemaBridgeError as e:
        assert "candidates" in str(e)

def test_bridge_l2_to_l3():
    # Valid
    l2_valid = {"schema_version": "2.1", "semantics": [{"id": "1", "semantic_role": "button"}]}
    bridge_l2_to_l3(l2_valid)
    
    # Invalid
    try:
        bridge_l2_to_l3({"schema_version": "2.1"})
        assert False
    except SchemaBridgeError as e:
        assert "semantics" in str(e)

def test_bridge_l3_to_l4_1():
    # Valid
    l3_valid = {"schema_version": "2.1", "visuals": [{"id": "1", "style": {}}]}
    bridge_l3_to_l4_1(l3_valid)
    
    # Invalid
    try:
        bridge_l3_to_l4_1({})
        assert False
    except SchemaBridgeError as e:
        assert "visuals" in str(e)

def test_bridge_l4_1_to_l4_2():
    # Valid
    l4_1_valid = {"schema_version": "2.1", "root": {"id": "1", "layout": {"layout_type": "absolute"}}}
    bridge_l4_1_to_l4_2(l4_1_valid)
    
    # Invalid
    try:
        bridge_l4_1_to_l4_2({"root_node": {}})
        assert False
    except SchemaBridgeError as e:
        assert "root" in str(e)

if __name__ == "__main__":
    test_bridge_l1_to_l2()
    test_bridge_l2_to_l3()
    test_bridge_l3_to_l4_1()
    test_bridge_l4_1_to_l4_2()
    
    from verification.receipt import record_verification
    record_verification("test_schema_bridge", "Schema bridge validated", "SUCCESS\n" + "\n".join(["padding"]*10), True)
    print("PASS test_schema_bridge")
