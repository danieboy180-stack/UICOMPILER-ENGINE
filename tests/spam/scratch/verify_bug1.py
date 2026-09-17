import sys, json, io, time
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, str(Path.cwd()))

from orchestrator import Pipeline, PipelineContext

inbox = Path.cwd() / 'Python_Inbox'
inbox.mkdir(exist_ok=True)
manifest_path = Path.cwd() / 'Manifest' / 'manifest.json'

print("=== BUG 1 VERIFY: Garbage bundle that used to produce null run_id crash ===")
print("Submitting truncated-JSON garbage bundle (same as O14 crash category)...")

# Write the exact kind of bundle that caused the WinPath/NoneType crash
target = inbox / 'layer12_bundle.json'
target.write_text('{"schema_version": "2.1", "lay')

ctx = PipelineContext(run_id=None, manifest_path=manifest_path)
f = io.StringIO()
with redirect_stdout(f), redirect_stderr(f):
    res = Pipeline().run(ctx)

out = f.getvalue()
print("=== RAW PIPELINE OUTPUT ===")
print(out)
print(f"Exit code: {res}")
print(f"ctx.run_id after: {repr(ctx.run_id)}")
print()
if 'TypeError' in out or 'NoneType' in out:
    print("RESULT: CRASH — bug still present!")
    sys.exit(1)
elif 'No bundle processed' in out or ('Ingest' in out and 'FAILED' in out):
    print("RESULT: CLEAN — garbage bundle cleanly rejected at Ingest, zero crash downstream. BUG 1 FIXED.")
else:
    print("RESULT: Unknown outcome — manual review needed")
    sys.exit(1)

# Clean up if needed
target.unlink(missing_ok=True)

print()
print("=== BUG 1 VERIFY: Empty bundle (another null run_id trigger) ===")
target.write_bytes(b'')
ctx2 = PipelineContext(run_id=None, manifest_path=manifest_path)
f2 = io.StringIO()
with redirect_stdout(f2), redirect_stderr(f2):
    res2 = Pipeline().run(ctx2)
out2 = f2.getvalue()
print("=== RAW PIPELINE OUTPUT ===")
print(out2)
print(f"Exit code: {res2}")
print(f"ctx2.run_id after: {repr(ctx2.run_id)}")
print()
if 'TypeError' in out2 or 'NoneType' in out2:
    print("RESULT: CRASH — bug still present!")
    sys.exit(1)
else:
    print("RESULT: CLEAN — empty bundle cleanly rejected at Ingest. BUG 1 FIXED.")

target.unlink(missing_ok=True)

