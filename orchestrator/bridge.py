class SchemaBridgeError(Exception):
    pass

def bridge_l1_to_l2(l1_data: dict):
    if not isinstance(l1_data, dict) or l1_data.get("candidates") is None:
        raise SchemaBridgeError("Missing required input field 'candidates' for Layer 2")

def bridge_l2_to_l3(l2_data: dict):
    if not isinstance(l2_data, dict) or l2_data.get("semantics") is None:
        raise SchemaBridgeError("Missing required input field 'semantics' for Layer 3")

def bridge_l3_to_l4_1(l3_data: dict):
    if not isinstance(l3_data, dict) or l3_data.get("visuals") is None:
        raise SchemaBridgeError("Missing required input field 'visuals' for Layer 4.1")

def bridge_l4_1_to_l4_2(l4_1_data: dict):
    if not isinstance(l4_1_data, dict) or l4_1_data.get("root") is None:
        raise SchemaBridgeError("Missing required input field 'root' for Layer 4.2")
