import json
import os
import uuid
from pathlib import Path

def generate_bundles():
    root = Path.cwd()
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    
    # clear inbox first
    for f in inbox.iterdir():
        if f.is_file():
            f.unlink()

    counts = {'a': 0, 'b': 0, 'c': 0, 'd': 0, 'e': 0}

    def write_bundle(name, content, is_json=True):
        path = inbox / name
        if is_json and isinstance(content, (dict, list)):
            text = f"<LAYER1_OUTPUT>\n{json.dumps(content)}\n</LAYER1_OUTPUT>\n<LAYER2_OUTPUT>\n{{}}\n</LAYER2_OUTPUT>\n<MANIFEST_UPDATE>\n{{}}\n</MANIFEST_UPDATE>\n<REPORT>\n</REPORT>"
            if path.suffix == '.json':
                path = path.with_suffix('.txt')
            path.write_text(text, encoding='utf-8')
        elif isinstance(content, str):
            if path.suffix == '.json':
                path = path.with_suffix('.txt')
            path.write_text(content, encoding='utf-8', errors='ignore')
        else:
            if path.suffix == '.json':
                path = path.with_suffix('.txt')
            path.write_bytes(content)

    # a. Valid bundles (20+)
    for i in range(40):
        write_bundle(f"valid_{i}.json", {
            "schema_version": "2.1",
            "project_id": f"proj_A" if i < 10 else f"proj_B", # To test idempotency duplicate hashes
            "layer": 12,
            "payload": {"view_hierarchy": {"type": "window", "id": str(uuid.uuid4())}, "index": i, "_cat": "a"}
        })
        counts['a'] += 1
        
    # Idempotency checks: Exact duplicates
    # Same content, same project -> should drop
    for i in range(5):
        write_bundle(f"valid_dup_same_proj_{i}.json", {
            "schema_version": "2.1",
            "project_id": "proj_A",
            "layer": 12,
            "payload": {"view_hierarchy": {"type": "window", "id": "fixed_id_123"}, "index": "fixed"}
        })
        counts['a'] += 1
        
    # Same content, different project -> should pass
    for i in range(5):
        write_bundle(f"valid_dup_diff_proj_{i}.json", {
            "schema_version": "2.1",
            "project_id": f"proj_dup_{i}",
            "layer": 12,
            "payload": {"view_hierarchy": {"type": "window", "id": "fixed_id_123"}, "index": "fixed"}
        })
        counts['a'] += 1

    # b. Malformed schema bundles
    malformed = [
        {}, # empty
        {"schema_version": "1.0", "layer": 12, "payload": {}}, # wrong version
        {"schema_version": "2.1", "layer": "twelve", "payload": {}}, # wrong type
        {"schema_version": "2.1", "project_id": "proj", "layer": 12}, # missing payload
        {"schema_version": "2.1", "project_id": 123, "layer": 12, "payload": {}} # wrong type for project_id
    ]
    for i, m in enumerate(malformed):
        for j in range(4): # 20 total
            write_bundle(f"malformed_{i}_{j}.json", m)
            counts['b'] += 1

    # c. Boundary/edge bundles
    edge = [
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"arr": [], "_cat": "c"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"arr": [1]*5000, "_cat": "c"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"deep": {"deep": {"deep": {"deep": {"val": 1}}}}, "_cat": "c"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"text": "👍💯 " * 1000, "_cat": "c"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"nulls": [None]*1000, "_cat": "c"}}
    ]
    # create a deeply nested one (50+ levels)
    deep = {"_cat": "c"}
    curr = deep
    for _ in range(60):
        curr["child"] = {}
        curr = curr["child"]
    edge.append({"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": deep})

    for i, m in enumerate(edge):
        for j in range(4): # 24 total
            write_bundle(f"edge_{i}_{j}.json", m)
            counts['c'] += 1

    # d. Adversarial bundles
    adv = [
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"run_id": "malicious_override", "_cat": "d"}},
        {"schema_version": "2.1", "project_id": "../../../malicious", "layer": 12, "payload": {"_cat": "d"}},
        {"schema_version": "2.1", "project_id": "p\x00nullbyte", "layer": 12, "payload": {"_cat": "d"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"hash": "fake_hash", "_cat": "d"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"TAXONOMY": "action_trigger", "_cat": "d"}},
        {"schema_version": "2.1", "project_id": "p", "layer": 12, "payload": {"taxonomy": "fake_taxonomy_not_in_19", "_cat": "d"}}
    ]
    for i, m in enumerate(adv):
        for j in range(8): # 48 total
            write_bundle(f"adv_{i}_{j}.json", m)
            counts['d'] += 1

    # e. Byte-level garbage
    for i in range(15):
        write_bundle(f"garbage_trunc_{i}.json", '{"schema_version": "2.1", "lay', is_json=False)
        write_bundle(f"garbage_bin_{i}.json", os.urandom(1024), is_json=False)
        write_bundle(f"garbage_empty_{i}.json", b'', is_json=False)
        write_bundle(f"garbage_wrongext_{i}.txt", '{"schema_version": "2.1", "layer": 12, "payload": {}}', is_json=False)
        counts['e'] += 4

    print(f"Generated mock bundles: {counts}")
    return counts

if __name__ == "__main__":
    generate_bundles()
