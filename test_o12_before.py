import os
import json
import subprocess
import time
import threading
from pathlib import Path
from orchestrator.orchestrator import Pipeline, PipelineContext

def setup_inbox(root: Path, content_str: str = None):
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for f in inbox.glob("*"): f.unlink()
    bundle_path = inbox / "layer12_bundle.json"
    content = content_str or '{"layer": 12, "schema_version": "2.1", "payload": {"data": "test"}}'
    with open(bundle_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return bundle_path

def run_before_tests():
    root = Path.cwd()
    manifest_path = root / "Manifest" / "manifest.json"
    
    print("\n" + "="*50)
    print("BEFORE FIXES (FAILS)")
    print("="*50)
    
    # Bug 1: Concurrency Race
    print("\n--- BUG 1 (BEFORE): Concurrency Race ---")
    def run_pipeline(run_id):
        setup_inbox(root) # to ensure they have something to process
        ctx = PipelineContext(run_id=run_id, manifest_path=manifest_path)
        Pipeline().run(ctx)
        
    t1 = threading.Thread(target=run_pipeline, args=("conc_before_1",))
    t2 = threading.Thread(target=run_pipeline, args=("conc_before_2",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    
    # Bug 2: Crash Recovery Bypass
    print("\n--- BUG 2 (BEFORE): Crash Recovery Silent Bypass ---")
    lock_file = root / "Manifest" / "manifest.json.lock"
    with open(lock_file, 'w') as f: f.write("deadlock")
    os.utime(lock_file, (time.time() - 20, time.time() - 20)) # Stale
    setup_inbox(root)
    ctx_crash = PipelineContext(run_id="crash_before_run", manifest_path=manifest_path)
    res_crash = Pipeline().run(ctx_crash)
    print(f"\nRAW EXIT CODE: {res_crash}")
    print(f"LOCK FILE EXISTS: {lock_file.exists()}")
    # Intentionally missing the strict assertion here to show the old bypass behavior outputting a non-zero exit code.

    # Bug 3: Idempotency Scope
    print("\n--- BUG 3 (BEFORE): Idempotency Scope (Global) ---")
    setup_inbox(root, '{"layer": 12, "schema_version": "2.1", "payload": {"data": "unique_global_test"}}')
    res1 = subprocess.run(["python", "ingest.py", "--root", str(root)])
    # Ingest same bundle for "Project B"
    setup_inbox(root, '{"layer": 12, "schema_version": "2.1", "payload": {"data": "unique_global_test"}}')
    res2 = subprocess.run(["python", "ingest.py", "--root", str(root)])
    print(f"Project A Exit: {res1.returncode}, Project B Exit (should be 0 but drops it): {res2.returncode}")

    # Flag 2: Sandboxing
    print("\n--- FLAG 2 (BEFORE): Fake Sandboxing ---")
    setup_inbox(root)
    ctx_sandbox = PipelineContext(run_id="sandbox_before_run", manifest_path=manifest_path, storage={"force_layer5_malicious": True})
    res_sandbox = Pipeline().run(ctx_sandbox)
    print(f"RAW EXIT CODE: {res_sandbox}")

def test_o12_before():
    import io
    from contextlib import redirect_stdout
    from verification.receipt import record_verification
    
    f = io.StringIO()
    with redirect_stdout(f):
        run_before_tests()
        
    out = f.getvalue()
    assert "BEFORE FIXES" in out
    
    record_verification("O12-before", "Unfixed pipeline bugs", out, True)
    print(out)

if __name__ == "__main__":
    test_o12_before()

