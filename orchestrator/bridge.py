class SchemaBridgeError(Exception):
    pass

def bridge_l1_to_l2(l1_data: dict):
    if not isinstance(l1_data, dict) or (l1_data.get("candidates") is None and l1_data.get("objects") is None):
        raise SchemaBridgeError("Missing required input field 'candidates' or 'objects' for Layer 2")

def bridge_l2_to_l3(l2_data: dict):
    if not isinstance(l2_data, dict) or (l2_data.get("semantics") is None and l2_data.get("regions") is None and l2_data.get("screens") is None):
        raise SchemaBridgeError("Missing required input field 'semantics' or 'regions' for Layer 3")

def bridge_l3_to_l4_1(l3_data: dict):
    has_l3 = (
        l3_data.get("visuals") is not None
        or l3_data.get("TOKEN_REGISTRY") is not None
        or l3_data.get("COMPONENT_DNA") is not None
        or l3_data.get("token_registry") is not None
    ) if isinstance(l3_data, dict) else False
    if not has_l3:
        raise SchemaBridgeError("Missing required input field 'visuals' or 'TOKEN_REGISTRY' for Layer 4.1")

def bridge_l4_1_to_l4_2(l4_1_data: dict):
    has_l4_1 = (
        l4_1_data.get("root") is not None
        or l4_1_data.get("semantic_graph") is not None
        or l4_1_data.get("canonical_taxonomy") is not None
    ) if isinstance(l4_1_data, dict) else False
    if not has_l4_1:
        raise SchemaBridgeError("Missing required input field 'root' or 'semantic_graph' for Layer 4.2")
