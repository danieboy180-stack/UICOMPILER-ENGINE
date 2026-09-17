from pathlib import Path

for p in ['test_o12_after.py', 'test_o12_before.py', 'test_o9.py']:
    text = Path(p).read_text(encoding='utf-8')
    text = text.replace('bundle_path = inbox / "layer12_bundle.json"', 'bundle_path = inbox / "layer12_bundle.txt"')
    text = text.replace('with locked_write(bundle_path, {"layer": 12, "payload": {}}):', 
'''content = """<LAYER1_OUTPUT>
{"schema_version": "2.1", "project_id": "test_proj", "layer": 12, "payload": {}}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{}
</LAYER2_OUTPUT>"""
    with locked_write(bundle_path, content):''')
    Path(p).write_text(text, encoding='utf-8')
