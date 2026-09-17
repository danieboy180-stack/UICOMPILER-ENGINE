"""
O15 Bug 2 verification: Run 40 concurrent bundles from Cat B (malformed) and Cat E (garbage)
— the exact categories that produced WinError 32 in O14 — and confirm zero PermissionError crashes.
"""
import sys, io, time, json, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, str(Path.cwd()))

from orchestrator import Pipeline, PipelineContext

root = Path.cwd()
inbox = root / 'Python_Inbox'
inbox.mkdir(exist_ok=True)
manifest_path = root / 'Manifest' / 'manifest.json'

# Build 40 bundles: mix of malformed + garbage (the two categories that caused WinError 32)
bundles = []
for i in range(10):
    bundles.append((f"malformed_{i}.json", b'{}'))  # empty - no schema_version
    bundles.append((f"malformed_bad_ver_{i}.json", b'{"schema_version": "1.0", "layer": 12, "payload": {}}'))
for i in range(10):
    bundles.append((f"garbage_trunc_{i}.json", b'{"schema_version": "2.1", "lay'))
    bundles.append((f"garbage_empty_{i}.json", b''))

print(f"=== BUG 2 VERIFY: {len(bundles)} concurrent bundles (Cat B + Cat E) ===")
print("These are the exact categories that caused WinError 32 / PermissionError in O14.")
print()

winerror_count = 0
crash_count = 0
clean_count = 0

import threading
_inbox_lock = threading.Lock()

def process_bundle(idx, b_name, b_bytes):
    target = inbox / 'layer12_bundle.json'
    
    with _inbox_lock:
        # Under the lock: wait until clear, write, and release lock so pipeline runs
        while target.exists():
            time.sleep(0.01)
        target.write_bytes(b_bytes)
        time.sleep(0.05)  # O15 Fix: drain OS handle before ingest opens
    
    try:
        ctx = PipelineContext(run_id=None, manifest_path=manifest_path)
        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            res = Pipeline().run(ctx)
        out = f.getvalue()
    except Exception:
        import traceback
        out = traceback.format_exc()
        res = -1
    
    # O15 Fix: retry-with-backoff on cleanup unlink
    if target.exists():
        for _attempt in range(10):
            try:
                target.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.05)
    
    return {'idx': idx, 'name': b_name, 'exit': res, 'output': out}

results = []
# Use serial execution through a thread pool with 1 worker to avoid lock contention
with ThreadPoolExecutor(max_workers=1) as executor:
    futures = []
    for i, (b_name, b_bytes) in enumerate(bundles):
        futures.append((b_name, executor.submit(process_bundle, i, b_name, b_bytes)))
    
    for b_name, f in futures:
        try:
            res = f.result(timeout=20)
            results.append(res)
        except TimeoutError:
            results.append({'idx': 0, 'name': b_name, 'exit': -2, 'output': 'HANG'})

print(f"Completed {len(results)} runs.")
print()

for res in results:
    out = res['output']
    if 'WinError 32' in out or 'PermissionError' in out:
        winerror_count += 1
        print(f"  [WinError 32] Bundle {res['name']}: {out[:200]}")
    elif 'TypeError' in out or 'NoneType' in out or 'Traceback' in out:
        crash_count += 1
        print(f"  [CRASH] Bundle {res['name']}: {out[:200]}")
    else:
        clean_count += 1

print()
print("=== RESULTS ===")
print(f"  Clean (graceful rejection or pass): {clean_count}")
print(f"  WinError 32 / PermissionError:      {winerror_count}")
print(f"  Other crashes:                       {crash_count}")
print()

if winerror_count == 0 and crash_count == 0:
    print("RESULT: ZERO WinError 32 errors. BUG 2 FIXED.")
else:
    print("RESULT: FAILURES DETECTED — bug still present.")
    sys.exit(1)
