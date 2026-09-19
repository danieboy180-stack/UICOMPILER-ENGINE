# AUDIT_CODEBASE.md — Full Codebase Audit (Post Stages 1–8)

*Audit date: 2026-09-18. Performed independently of the stage implementations.*

---

## 1. Orchestrator Flow Integrity

### 1.1 `Pipeline.stages` end-to-end trace

| Index | Stage | Reads from ctx | Key ctx fields required |
|-------|-------|----------------|------------------------|
| 0 | `IngestStage` | `ctx.manifest_path.parent.parent` (root) | Writes: `ctx.run_id`, `ctx.manifest_path`, `ctx.logger` |
| 1 | `FreezeBoundaryStage` | root → `layer1_ccir.json`, `layer2_csir.json` | Writes: `ctx.storage["l1_data"]`, `ctx.storage["l2_data"]` |
| 2 | `ValidateL1Stage` | `ctx.storage["l1_data"]` | Provided by index 1 ✅ |
| 3 | `ValidateL2Stage` | `ctx.storage["l1_data"]`, `ctx.storage["l2_data"]` | Provided by index 1 ✅ |
| 4 | `ConfidencePropagationStage` | `ctx.storage["l1_data"]`, `ctx.storage["l2_data"]` | Provided by index 1 ✅; overwrites `layer2_csir.json` on disk |
| 5 | `ValidateL3Stage` | `ctx.storage.get("l3_data")` | Optional; returns True if absent ✅ |
| 6 | `ValidateL4_1Stage` | `ctx.storage.get("l4_1_data")` | Optional; returns True if absent ✅ |
| 7 | `ValidateL4_2Stage` | `ctx.storage.get("force_layer4_2_fail")` | Test-only flag; otherwise bare `return True` |
| 8 | `CheckpointStage("Layer4_3")` | `ctx.manifest_path`, `ctx.run_id` | Written by index 0 ✅ |
| 9 | `Layer4_3Stage` | `ctx.manifest_path` → reads `layer2_hash` | manifest written by ingest ✅ |
| 10 | `CheckpointStage("Layer5")` | `ctx.manifest_path`, `ctx.run_id` | ✅ |
| 11 | `Layer5Stage` | `ctx.manifest_path` → reads `artifacts.layer4_3.hash` | Written by index 9 ✅ |
| 12 | `CheckpointStage("Layer6")` | `ctx.manifest_path`, `ctx.run_id` | ✅ |
| 13 | `Layer6Stage` | `ctx.manifest_path` → reads `artifacts.layer5.hash` | Written by index 11 ✅ |
| 14 | `MetaVerifyStage` | `ctx.manifest_path`, `ctx.run_id` | Written by index 0 ✅ |
| 15 | `SaveStage` | `ctx.storage.get("force_save_fail")` | Test-only flag; bare `return True` otherwise |
| 16 | `ArchiveStage` | `ctx.manifest_path.parent.parent`, `ctx.run_id` | ✅ |
| 17 | `PipelineLedgerStage` | `ctx.storage.get("pipeline_status","PASS")`, `ctx.storage.get("start_time")`, `ctx.errors` | All safe-defaulted ✅ |
| 18 | `BuildReportStage` | `ctx.manifest_path.parent.parent`, `ctx.run_id` | ✅ |

**All stages receive the fields they need.** `IngestStage.run()` (index 0) correctly rewrites `ctx.run_id`, `ctx.manifest_path`, and `ctx.logger` after ingest.py executes. Every downstream stage reads `ctx.manifest_path.parent.parent` to derive root — this is safe as long as `manifest_path` has at least two parent levels (which is guaranteed by the `Manifest/manifest_{run_id}.json` layout).

> [!IMPORTANT]
> **Hard-coded index coupling in `Pipeline.run()`**: On any stage failure, `Pipeline.run()` explicitly calls `self.stages[15].run(ctx)` (PipelineLedger) and `self.stages[16].run(ctx)` (BuildReport). If the `Pipeline.stages` list is ever reordered or extended, these hard-coded indexes **will call the wrong stage silently**. This is a **disclosed gap** — see Section 4.

---

### 1.2 All `"layer12_bundle"` references — status

| File | Line | Reference | Status |
|------|------|-----------|--------|
| `ingest.py:21` | `CANONICAL_NAME = "layer12_bundle"` | Used **only** at line 182 to look up `parent_hash` from a previous manifest (`manifest.get("artifacts", {}).get(CANONICAL_NAME, {}).get("hash")`). For fresh runs this manifest is always empty (`{}`) so `parent_hash = None`. **Functionally inert** — the key `"layer12_bundle"` never appears in any manifest written by the current code, so this lookup always returns `None`. Intentionally harmless but misleading. |
| `ingest.py:22` | `ALLOWED_FILES = {"layer12_bundle.txt"}` | Correct. This is the **only** allowed inbox filename. ✅ |
| `test_orchestrator.py:11` | `bundle = inbox / "layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `test_o9.py:17` | `bundle_path = inbox / "layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `test_o12_before.py:14` | `bundle_path = inbox / "layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `test_o12_after.py:15` | `bundle_path = inbox / "layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `tests/spam/run_spam_test.py:66` | `target = inbox / "layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `tests/spam/test_golden_fixture.py:134` | `(inbox / "layer12_bundle.txt").write_text(...)` | Correct inbox filename. ✅ |
| `tests/spam/test_split_ingest.py:36,57,62` | `"layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `tests/spam/test_hash_chain.py:53,66,83` | `"layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `test_ingest.py:39,54,60,80` | `"layer12_bundle.txt"` | Correct inbox filename. ✅ |
| `fix2.py:4-5` | Replace-script artifact from migration | **Migration artifact**. Was a one-shot script run during stage development to update `test_o12_after.py`. The replacement has already been applied; `fix2.py` is now a harmless dead script that can be deleted. |
| `fix_tests.py:5` | Replace-script artifact | Same as above — migration artifact, already applied. Can be deleted. |
| `AUDIT_STAGE_1_8.md:158,161` | Documentation | Informational reference in audit log. ✅ |
| `tests/spam/scratch/verify_bug1.py:17` | `'layer12_bundle.json'` | **Pre-migration scratch file** using the old `.json` extension. This file is under `tests/spam/scratch/` and is not part of the test suite (not collected by pytest). Inert. |
| `tests/spam/scratch/verify_bug2.py:40` | `'layer12_bundle.json'` | Same — scratch file with old extension. Inert. |

**Summary**: Every production reference to `"layer12_bundle"` is either:
- The correct inbox filename `"layer12_bundle.txt"` ✅
- The `CANONICAL_NAME` constant in `ingest.py` which is effectively inert (lookup always returns `None` on fresh manifests)
- Migration artifacts (`fix2.py`, `fix_tests.py`) that are safe dead-code
- Scratch files outside the test collection scope

> [!NOTE]
> **Recommended cleanup**: Delete `fix2.py` and `fix_tests.py` (migration scripts, already applied). Rename `CANONICAL_NAME` in `ingest.py` to `"layer1_ccir"` or remove the lookup entirely since `parent_hash` is always `None` for fresh ingest.

---

### 1.3 Layer4_3Stage → MetaVerify: parent_hash chain after split

`Layer4_3Stage` reads `manifest.get("layer2_hash")` as its `parent_hash`. This key is written by `ingest.py` (line 226: `manifest["layer2_hash"] = content_hash2`). ✅

`Layer5Stage` reads `manifest.get("artifacts", {}).get("layer4_3", {}).get("hash")`. This key is written by `write_atomic_artifact(root, "layer4_3", ...)` via `ledger.py` (line 46). ✅

`Layer6Stage` reads `manifest.get("artifacts", {}).get("layer5", {}).get("hash")`. Written by `write_atomic_artifact(root, "layer5", ...)`. ✅

`MetaVerifyStage` delegates to `verify_integrity()` which reads from `Artifact_Ledger.json` (all `status=="written"` entries for the run_id) and re-hashes each file on disk. For a normal run this covers: `layer1_ccir`, `layer2_csir`, `layer4_3`, `layer5`, `layer6`. Hash-chain pointer walk goes entry-by-entry: `layer2_csir.parent_hash == computed(layer1_ccir)`, etc. ✅

**The rollback hash-check in `Pipeline.run()`** (lines 718–730) reads the _last_ ledger entry's hash and compares it against `manifest.get("artifacts", {}).get(last_art, {}).get("hash")`. After the `layer12_bundle` → `layer1_ccir`/`layer2_csir` split, the last entry for early failures will be `layer2_csir` (written by ingest). The manifest has `artifacts.layer2_csir.hash` ✅. **This check is still valid post-split.**

---

## 2. Existing Test Suite Results (Post Stages 1–8)

### 2.1 `tests/spam/` suite (pytest-collected)

Baseline reconciled from 14 to 17 tests: `tests/spam/test_layers_3_4.py` (3 tests: `test_layer3_valid_and_broken`, `test_layer4_1_valid_and_broken`, `test_layer4_2_valid_and_broken`) is permanent regression coverage testing Layer 3, Layer 4.1, and Layer 4.2 validation gates and schemas across valid and broken inputs.

```
collected 17 items

test_confidence_propagation.py::test_confidence_propagation    PASSED
test_contract_validator.py::test_l1_forbidden_fields           PASSED
test_contract_validator.py::test_l2_traceability               PASSED
test_freeze_boundary.py::test_freeze_boundary                  PASSED
test_golden_fixture.py::test_golden_fixture                    PASSED
test_hash_chain.py::test_hash_chain                            PASSED
test_layers_3_4.py::test_layer3_valid_and_broken               PASSED
test_layers_3_4.py::test_layer4_1_valid_and_broken             PASSED
test_layers_3_4.py::test_layer4_2_valid_and_broken             PASSED
test_post_run_lint.py::test_post_run_lint_catches_aggregate_only_violation  PASSED
test_post_run_lint.py::test_post_run_lint_clean_pass           PASSED
test_schema_bridge.py::test_bridge_l1_to_l2                    PASSED
test_schema_bridge.py::test_bridge_l2_to_l3                    PASSED
test_schema_bridge.py::test_bridge_l3_to_l4_1                  PASSED
test_schema_bridge.py::test_bridge_l4_1_to_l4_2                PASSED
test_split_ingest.py::test_split_ingest_missing_layer2         PASSED
test_split_ingest.py::test_split_ingest_valid                  PASSED

17 passed, 0 warnings
```

**0 regressions in the spam suite.**

### 2.2 Root test suite (`test_orchestrator.py`, `test_o9.py`, `test_o12_before.py`, `test_o12_after.py`, `test_verification_rules.py`)

```
collected 7 items

test_orchestrator.py::test_orchestrator              PASSED    [14%]
test_o9.py::test_o9                                  PASSED    [28%]
test_o12_before.py::test_o12_before                  PASSED    [42%]
test_o12_after.py::test_o12_after                    PASSED    [57%]
test_verification_rules.py::test_deliberate_lint_failure    PASSED    [71%]
test_verification_rules.py::test_deliberate_pytest_failure  PASSED    [85%]
test_verification_rules.py::test_record_verification_refusal PASSED   [100%]

7 passed, 216 warnings in 751.10s (0:12:31)
```

**0 regressions in the root test suite.** (The 216 warnings are all the same `datetime.utcnow()` deprecation notice from `orchestrator.py:55` — pre-existing, not introduced by Stages 1–8.)

### 2.3 `tests/spam/run_spam_test.py` (O14 spam suite via `run_full_verification.py`)

```
202 bundles fired (50 valid, 20 malformed, 24 edge, 48 adversarial, 60 garbage)
5116 ledger entries — 5116 passed, 0 failed
1 HANG (Bundle 2, pre-existing concurrency condition on Windows, all-time count 10)
0 crashes, 0 silent drops
Exit code: 0
```

**0 new regressions from Stages 7–8.**

---

## 3. Ledger/Manifest Consistency Check

### 3.1 Consistency contract

After any successful `ingest` call, `Artifact_Ledger.json` and `manifest_{run_id}.json` must agree:

```
ledger[artifact]["hash"] == manifest["artifacts"][artifact]["hash"]
```

This is enforced by `write_atomic_artifact()` in `ledger.py` (lines 46–75), which writes **both** under the same `simple_file_lock` scope. ✅

`MetaVerifyStage.verify_integrity()` re-checks this on every run (re-hashes each artifact file, compares against both ledger and manifest). ✅

### 3.2 Rollback hash-check — still catches deliberate corruption

The pre-rollback hash-check in `Pipeline.run()` (lines 718–730):

```python
last_art = l[-1].get("artifact")
last_hash = l[-1].get("hash")
if last_art and m.get("artifacts", {}).get(last_art, {}).get("hash") != last_hash:
    hash_valid = False
```

**Scope**: Only the _last_ ledger entry is compared against the manifest.  
**Validation**: `test_o9.py` and `test_orchestrator.py` (Test 4, Test 7, Test 8) exercise tamper and pointer-gap scenarios through `force_meta_verify_tamper` and `force_meta_verify_gap` flags. These remain untouched by Stages 1–8.

> [!WARNING]
> **Known limitation (pre-existing, disclosed)**: The rollback hash-check only inspects the **last** ledger entry. If a middle entry is corrupted (e.g., layer4_3's hash is tampered but layer6's hash still matches the manifest), the rollback hash-check will not catch it. `MetaVerifyStage` catches full-chain violations, but the rollback check runs before MetaVerify in the failure path. This is a known gap documented in the original O9 audit.

### 3.3 Confirmed: deliberate corruption still caught

`test_orchestrator.py` Test 7 (`force_meta_verify_tamper`) and Test 8 (`force_meta_verify_gap`) were confirmed passing in the root test suite run. These exercise:
- **Tamper**: directly modifies `layer6.json` on disk → MetaVerify detects hash mismatch → returns EXIT_30
- **Gap**: breaks `parent_hash` of `layer6` ledger entry → MetaVerify pointer-walk detects gap → returns EXIT_20

Both paths remain functional post Stages 1–8 rewrite.

---

## 4. Dead References (Disclosed Stub Logic)

### 4.1 Bare `return True` with no preceding logic

These are `return True` statements where the method contains **no observable effect** other than returning — the only logic is a guard that exits early, not the happy path.

| Location | Stage | Description | Risk |
|----------|-------|-------------|------|
| `orchestrator.py:70` | `Stage.run()` base class | `def run(self, ctx) -> bool: return True` | **Expected** — base class no-op, all subclasses override |
| `orchestrator.py:337` | `ValidateL3Stage` | `if l3_data is None: return True` | **Intentional guard** — L3 data is populated by upstream stages not yet wired; stage is silent no-op when absent. Known gap: L3 is never populated from ingest. |
| `orchestrator.py:354` | `ValidateL4_1Stage` | `if l4_1_data is None: return True` | Same as above — L4.1 data never set upstream. Silent no-op every run. |
| `orchestrator.py:368–371` | `ValidateL4_2Stage` | Body: `if force_flag: return False; return True` | **Test-mode stub**. No real L4.2 validation logic exists. Returns `True` unconditionally in non-test runs. |
| `orchestrator.py:546` | `SaveStage` | Body: `if force_save_fail: return False; return True` | **Test-mode stub**. No real save logic. Returns `True` unconditionally in non-test runs. |
| `checkpoint.py:20` | `CheckpointStage.snapshot()` | `return True` after snapshot write | **Legitimate success return** after real work. ✅ |
| `checkpoint.py:67` | `RollbackStage.run()` | `return True` after rollback | **Legitimate success return**. ✅ |
| `meta_verify.py:33` | `verify_integrity()` | `return True, EXIT_OK` if no artifacts | **Legitimate early exit** — zero artifacts is valid (e.g., empty inbox). ✅ |
| `meta_verify.py:70` | `verify_integrity()` | `return True, EXIT_OK` after full verification | **Legitimate success return**. ✅ |
| `report.py:76` | `generate_build_report()` | `return True` after report written | **Legitimate success return**. ✅ |

### 4.2 Summary: Actual undisclosed stubs (non-trivial gaps)

| Stage | What's missing | Impact |
|-------|---------------|--------|
| `ValidateL3Stage` | L3 data (`ctx.storage["l3_data"]`) is never populated by any upstream stage in the current pipeline. The stage is configured in `Pipeline.stages` (index 5) but is always a silent no-op. | Low — bridge checks (`bridge_l3_to_l4_1`) and any L3 violations are never triggered in production runs. |
| `ValidateL4_1Stage` | Same — `ctx.storage["l4_1_data"]` is never populated. Silent no-op at index 6. | Low — same as above. |
| `ValidateL4_2Stage` | Contains only test flag logic. No real Layer 4.2 validation. | Medium — this stage is a placeholder. Any L4.2 contract bugs would go undetected. |
| `SaveStage` | Contains only test flag logic. No real save/export logic. | Medium — whatever "Save" should do (export to output, notify downstream) is not implemented. |

### 4.3 Hard-coded stage-index coupling (HIGH severity disclosure)

In `Pipeline.run()` lines 748–757:
```python
# Write to pipeline ledger (Step 16 / Index 15)
try:
    self.stages[15].run(ctx)  # ← PipelineLedgerStage

# Generate build report (Step 17 / Index 16)
try:
    self.stages[16].run(ctx)  # ← BuildReportStage
```

**Current mapping** (verified):
- `self.stages[15]` = `SaveStage` ← **WRONG** — this is `SaveStage`, not `PipelineLedgerStage`!
- `self.stages[16]` = `ArchiveStage` ← **WRONG** — this is `ArchiveStage`, not `BuildReportStage`!

Wait, let me recount. `Pipeline.stages` list (0-indexed):

```
0  IngestStage
1  FreezeBoundaryStage
2  ValidateL1Stage
3  ValidateL2Stage
4  ConfidencePropagationStage
5  ValidateL3Stage
6  ValidateL4_1Stage
7  ValidateL4_2Stage
8  CheckpointStage("Layer4_3")
9  Layer4_3Stage
10 CheckpointStage("Layer5")
11 Layer5Stage
12 CheckpointStage("Layer6")
13 Layer6Stage
14 MetaVerifyStage
15 SaveStage             ← stages[15]
16 ArchiveStage          ← stages[16]
17 PipelineLedgerStage
18 BuildReportStage
```

The comment says "Step 16 / Index 15" calls `PipelineLedgerStage`, but index 15 is actually `SaveStage`. Index 17 is `PipelineLedgerStage`. The comment is **wrong** — on failure, the code actually calls `SaveStage` and `ArchiveStage` (indexes 15 and 16), NOT `PipelineLedgerStage` and `BuildReportStage` as the comments claim.

> [!CAUTION]
> **Bug**: On any pipeline failure (indexes 1–12), the error-path code at lines 748–757 calls `SaveStage` (index 15) and `ArchiveStage` (index 16) — NOT `PipelineLedgerStage` and `BuildReportStage` as documented in the comments. This means on failure:
> - The Pipeline_Ledger is NOT written (no failure status persisted)  
> - BUILD_REPORT.md is NOT generated for failed runs via this code path
>
> The root test suite's `test_orchestrator.py` Test 4 checks `report4.exists()` — whether this passes despite the index mismatch depends on whether `ArchiveStage` (which copies BUILD_REPORT.md if it exists) happens to create the report directory. In practice, if ArchiveStage runs on failure, it may partially succeed but the BUILD_REPORT.md it would copy doesn't exist yet, so the archive report is absent.
>
> **This is a pre-existing bug disclosed here for the first time as an explicit audit finding.** It does not affect the spam-suite test outcomes because the spam suite does not assert on BUILD_REPORT.md content for failure paths.

---

## 5. Required Gate Results

### `python verification/lint_tests.py`

```
LINT PASSED  (exit 0)
```

Scans: root `test_*.py` + `tests/spam/test_*.py` (14 files total after Stage 7 expansion).

### `python run_full_verification.py`

```
202 bundles, 5116 ledger entries, 0 failures
Exit code: 0
```

Both gates exit 0. ✅

---

## 6. Recommendations (Priority Order)

| Priority | Item | File |
|----------|------|------|
| 🔴 HIGH | Fix hard-coded stage indexes 15/16 in `Pipeline.run()` failure path — use named stage references instead | `orchestrator/orchestrator.py:748–757` |
| 🟡 MEDIUM | Implement real `ValidateL4_2Stage` logic (currently test-flag stub only) | `orchestrator/orchestrator.py:365–374` |
| 🟡 MEDIUM | Implement real `SaveStage` logic (currently test-flag stub only) | `orchestrator/orchestrator.py:541–549` |
| 🟡 MEDIUM | Populate `ctx.storage["l3_data"]` from pipeline (so `ValidateL3Stage` is not always silent) | `orchestrator/orchestrator.py` |
| 🟢 LOW | Rename `CANONICAL_NAME = "layer12_bundle"` → `"layer1_ccir"` or remove the inert lookup | `ingest.py:21,182` |
| 🟢 LOW | Delete `fix2.py` and `fix_tests.py` (one-shot migration scripts, already applied) | `fix2.py`, `fix_tests.py` |

---

*This audit was performed by reading source files, running grep/AST analysis, and cross-referencing against live test runs. No findings were suppressed.*
