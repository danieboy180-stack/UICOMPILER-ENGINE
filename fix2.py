from pathlib import Path
p = 'test_o12_after.py'
t = Path(p).read_text(encoding='utf-8')
t = t.replace('artifacts": {"layer12_bundle":', 'artifacts": {"layer1_ccir":')
t = t.replace('write_atomic_artifact(root, "layer12_bundle",', 'write_atomic_artifact(root, "layer1_ccir",')
Path(p).write_text(t, encoding='utf-8')
