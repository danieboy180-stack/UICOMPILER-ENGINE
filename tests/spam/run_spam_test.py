import os
import sys
import threading
import time
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError, Future

# Make orchestrator importable
root = Path.cwd()
sys.path.append(str(root))

from orchestrator import Pipeline, PipelineContext
from tests.spam.generate_mock_bundles import generate_bundles
from verification.receipt import record_verification


class _ThreadRoutedStream:
    """Send each thread's prints to its own buffer. contextlib.redirect_stdout swaps sys.stdout for the whole
    process, so concurrent pipelines captured each other's output (or nothing), which looked like 'silent drops'."""

    def __init__(self, fallback):
        self.fallback = fallback
        self.buffers = {}

    def write(self, text):
        return self.buffers.get(threading.get_ident(), self.fallback).write(text)

    def flush(self):
        self.fallback.flush()

    def isatty(self):
        return False


_ROUTER = _ThreadRoutedStream(sys.stdout)

def run_single_bundle(manifest_path, idx):
    try:
        ctx = PipelineContext(run_id=None, manifest_path=manifest_path)
        import io
        f = io.StringIO()
        _ROUTER.buffers[threading.get_ident()] = f
        try:
            res = Pipeline().run(ctx)
        finally:
            _ROUTER.buffers.pop(threading.get_ident(), None)

        out = f.getvalue()
        if "[Ingest] STARTING" not in out and res == 0:
            return None # empty inbox

        return {
            "idx": idx,
            "run_id": getattr(ctx, 'run_id', 'Unknown'),
            "exit_code": res,
            "output": out
        }
    except Exception as e:
        import traceback
        return {
            "idx": idx,
            "run_id": "CRASH",
            "exit_code": -1,
            "output": traceback.format_exc()
        }

def run_spam():
    real_stdout, real_stderr = sys.stdout, sys.stderr
    _ROUTER.fallback = real_stdout
    sys.stdout = sys.stderr = _ROUTER
    try:
        return _run_spam()
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr


def _run_spam():
    print("Generating bundles in memory...")
    counts = generate_bundles() # This writes them to inbox, we will read them back

    inbox = root / "Python_Inbox"
    bundles_to_process = list(inbox.glob("*.json")) + list(inbox.glob("*.txt"))
    total_bundles = len(bundles_to_process)
    print(f"Total generated bundles: {total_bundles}")

    # Read all bundles into memory and clear inbox
    bundle_data = []
    for f in bundles_to_process:
        bundle_data.append((f.name, f.read_bytes()))
        f.unlink()

    manifest_path = root / "Manifest" / "manifest.json"
    results = []

    print("Firing bundles (workers queue on the shared inbox; concurrent runs are covered by test_pipeline_lock.py)...")

    inbox_lock = threading.Lock()

    def process_bundle(idx, b_name, b_bytes):
        # One bundle owns the shared inbox from write to cleanup. Without this, a finishing worker's cleanup deleted
        # the NEXT worker's freshly written bundle (the run then halted on an "empty inbox" and counted as a failure).
        # The engine serializes pipeline runs per project anyway; concurrency is covered by test_pipeline_lock.py.
        with inbox_lock:
            return _process_bundle_locked(idx, b_name, b_bytes)

    def _process_bundle_locked(idx, b_name, b_bytes):
        target = inbox / "layer12_bundle.txt"

        while target.exists():
            time.sleep(0.01)
        # O15 Fix (PermissionError 13): on Windows, shutil.move() inside ingest may hold a
        # transient kernel handle on the file for a few ms after the previous pipeline call
        # returned. Retry write_bytes with backoff to avoid the race.
        for _attempt in range(15):
            try:
                target.write_bytes(b_bytes)
                break
            except PermissionError:
                time.sleep(0.05)
        else:
            target.write_bytes(b_bytes)  # final attempt — let it raise if still locked
        # O15 Fix (WinError 32): yield the OS file handle before ingest opens the file.
        # On Windows, write_bytes holds a kernel handle briefly after close(); a tiny sleep
        # lets it drain so shutil.move() inside ingest doesn't race with our own open handle.
        time.sleep(0.05)

        res = run_single_bundle(manifest_path, idx)

        # O15 Fix (WinError 32): cleanup unlink uses retry-with-backoff instead of bare
        # unlink, because ingest's shutil.move() may still hold a transient lock on the file
        # for a few ms after our pipeline call returns.
        if target.exists():
            for _attempt in range(10):
                try:
                    target.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.05)

        if res is not None:
            res['original_name'] = b_name
        return res

    worker_count = int(os.environ.get("SPAM_WORKERS", "100"))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = []
        for i, (b_name, b_bytes) in enumerate(bundle_data):
            futures.append(executor.submit(process_bundle, i, b_name, b_bytes))
            # Give a tiny pause so they don't immediately clash on writing
            time.sleep(0.02)

        for i, f in enumerate(futures):
            try:
                res = f.result(timeout=300)
                if res is not None:
                    results.append(res)
            except TimeoutError:
                results.append({
                    "idx": i,
                    "run_id": "HANG",
                    "exit_code": -2,
                    "output": "Pipeline run timed out after 300 seconds. (HANG)",
                    "original_name": b_name
                })
            except Exception as e:
                import traceback
                results.append({
                    "idx": i,
                    "run_id": "CRASH",
                    "exit_code": -1,
                    "output": traceback.format_exc(),
                    "original_name": b_name
                })

    print(f"Completed {len(results)} pipeline runs.")

    # Evaluate outcomes per category
    outcomes = {
        'a': {'pass': 0, 'fail': 0, 'crash': 0, 'hang': 0, 'silent_drop': 0},
        'b': {'pass': 0, 'fail': 0, 'crash': 0, 'hang': 0, 'silent_drop': 0},
        'c': {'pass': 0, 'fail': 0, 'crash': 0, 'hang': 0, 'silent_drop': 0},
        'd': {'pass': 0, 'fail': 0, 'crash': 0, 'hang': 0, 'silent_drop': 0},
        'e': {'pass': 0, 'fail': 0, 'crash': 0, 'hang': 0, 'silent_drop': 0}
    }
    critical_findings = []

    # We will record them all
    for res in results:
        code = res['exit_code']
        out = res['output']
        run_id = res['run_id']

        # True categorization based on filename
        b_name = res.get('original_name', 'valid_')
        if b_name.startswith('valid'):
            cat = 'a'
        elif b_name.startswith('malformed'):
            cat = 'b'
        elif b_name.startswith('edge'):
            cat = 'c'
        elif b_name.startswith('adv'):
            cat = 'd'
        elif b_name.startswith('garbage'):
            cat = 'e'
        else:
            cat = 'a'

        status_label = "PASS"
        if code == -2:
            outcomes[cat]['hang'] += 1
            status_label = "HANG"
            critical_findings.append((run_id, status_label, out))
        elif code == -1:
            outcomes[cat]['crash'] += 1
            status_label = "CRASH"
            critical_findings.append((run_id, status_label, out))
        elif code == 0 or "Duplicate bundle" in out:
            if "Duplicate bundle" in out:
                outcomes[cat]['pass'] += 1
            elif "[Ingest] FAILED" in out:
                outcomes[cat]['fail'] += 1
            else:
                outcomes[cat]['pass'] += 1
        else:
            outcomes[cat]['fail'] += 1
            status_label = "FAIL"
            if not out.strip():
                outcomes[cat]['silent_drop'] += 1
                status_label = "SILENT_DROP"
                critical_findings.append((run_id, status_label, "No output produced on failure."))

        claim = f"Spam Bundle {res['idx']} (Run {run_id}, Exit {code})"

        # The receipt must say what actually happened: a crash, hang or silent drop is NOT a passing outcome.
        acceptable = status_label not in ("HANG", "CRASH", "SILENT_DROP")
        category_names = {"a": "valid", "b": "malformed schema", "c": "boundary/edge", "d": "adversarial", "e": "byte-level garbage"}
        evidence = [f"bundle index: {res['idx']}", f"bundle name: {b_name}", f"category: {cat} ({category_names[cat]})",
                    f"exit code: {code}", f"run id: {run_id}", f"classification: {status_label}",
                    f"output bytes: {len(out.encode('utf-8'))}", f"output lines: {len(out.splitlines())}",
                    f"outcome counters for category {cat}: {dict(outcomes[cat])}",
                    f"acceptable outcome: {acceptable}"]
        evidence += [f"output: {line}" for line in out.splitlines() if line.strip()]
        record_verification("O14-Spam", claim, "\n".join(evidence), acceptable)

    print("Writing Scorecard...")

    def format_cat(c):
        return (f"Accepted (or duplicate rejected): {outcomes[c]['pass']}, Rejected: {outcomes[c]['fail']}, "
                f"Crash: {outcomes[c]['crash']}, Hang: {outcomes[c]['hang']}, Silent Drop: {outcomes[c]['silent_drop']}")

    scorecard = f"""# SPAM TEST SCORECARD

## Execution Summary
- **Total Bundles Fired**: {total_bundles}
- **Categories Generated**:
  - Valid: {counts['a']}
  - Malformed Schema: {counts['b']}
  - Boundary/Edge: {counts['c']}
  - Adversarial: {counts['d']}
  - Byte-level Garbage: {counts['e']}

## Outcomes by Category
- **a. Valid**: {format_cat('a')}
- **b. Malformed Schema**: {format_cat('b')}
- **c. Boundary/Edge**: {format_cat('c')}
- **d. Adversarial**: {format_cat('d')}
- **e. Byte-level Garbage**: {format_cat('e')}

## Critical Findings
"""
    if critical_findings:
        for run_id, label, out in critical_findings:
            scorecard += f"### CRITICAL: {label} (Run: {run_id})\n```\n{out}\n```\n"
    else:
        scorecard += "No critical findings (crashes, hangs, silent drops) occurred.\n\n"

    # Expectations are COMPUTED from what happened in this run; nothing below is asserted by hand.
    def named(prefix):
        return [r for r in results if str(r.get("original_name", "")).startswith(prefix)]

    def accepted_run(r):
        return r["exit_code"] == 0 and "Duplicate bundle" not in r["output"]

    def category_of(name):
        for prefix, cat in (("valid", "a"), ("malformed", "b"), ("edge", "c"), ("adv", "d"), ("garbage", "e")):
            if str(name).startswith(prefix):
                return cat
        return "a"

    accepted = [r for r in results if accepted_run(r)]
    accepted_new = {c: [r for r in accepted if category_of(r.get("original_name")) == c] for c in "abcde"}
    same_proj, diff_proj = named("valid_dup_same_proj_"), named("valid_dup_diff_proj_")
    valid_total = sum(outcomes['a'].values())
    crash_count = sum(c['crash'] for c in outcomes.values())
    hang_count = sum(c['hang'] for c in outcomes.values())
    silent_drop_count = sum(c['silent_drop'] for c in outcomes.values())
    rejected_same = sum(1 for r in same_proj if "Duplicate bundle" in r["output"])
    expectations = [
        ("No crash, hang or silent drop in any category", crash_count + hang_count + silent_drop_count == 0,
         f"crashes={crash_count} hangs={hang_count} silent drops={silent_drop_count}"),
        ("Invalid bundles (malformed, edge, adversarial, garbage) are never accepted as new runs",
         all(len(accepted_new[c]) == 0 for c in "bcde"), ", ".join(f"{c}: {len(accepted_new[c])}" for c in "bcde")),
        ("Every valid bundle is accepted (or rejected as a duplicate)", valid_total > 0 and outcomes['a']['pass'] == valid_total,
         f"{outcomes['a']['pass']}/{valid_total}"),
        ("Same content, same project: first accepted, the rest rejected as duplicates",
         len(same_proj) > 1 and sum(1 for r in same_proj if accepted_run(r)) == 1 and rejected_same == len(same_proj) - 1,
         f"{len(same_proj)} bundles, {sum(1 for r in same_proj if accepted_run(r))} accepted, {rejected_same} rejected"),
        ("Same content, different projects: all accepted", len(diff_proj) > 0 and all(accepted_run(r) for r in diff_proj),
         f"{sum(1 for r in diff_proj if accepted_run(r))}/{len(diff_proj)} accepted"),
    ]
    scorecard += "## Expectations (computed from this run)\n\n| Expectation | Result | Observed |\n| :--- | :--- | :--- |\n"
    for text, ok, observed in expectations:
        scorecard += f"| {text} | {'PASS' if ok else 'FAIL'} | {observed} |\n"
    scorecard += "\n## Accepted as a NEW run, by category\n\n"
    scorecard += "| Category | Accepted as a new run |\n| :--- | :--- |\n"
    for c, label in (("a", "Valid"), ("b", "Malformed Schema"), ("c", "Boundary/Edge"), ("d", "Adversarial"), ("e", "Byte-level Garbage")):
        scorecard += f"| {label} | {len(accepted_new[c])} of {sum(outcomes[c].values())} |\n"
    scorecard += "\nThis scorecard reports what was measured. It deliberately assigns no quality scores.\n"

    (root / "tests" / "spam" / "SPAM_SCORECARD.md").write_text(scorecard, encoding='utf-8')
    print("Done! Scorecard written.")
    failed = [text for text, ok, _ in expectations if not ok]
    for text in failed:
        print(f"EXPECTATION FAILED: {text}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(run_spam())
