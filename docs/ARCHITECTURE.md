# UIC architecture: the data factory around the UI GOD pipeline

Status: v0.1, additive. No existing engine file was modified. New code lives in `uic/`, `prompts/`, `docs/` and `test_uic_*.py`.

## Problem
The pipeline turns a screenshot or code into five verified layers (L1, L2, L3, L4.1, L4.2). Before UIC:
1. the prompts' rules existed only as prose;
2. the LLM was asked for SHA-256 values it cannot compute, and ingest rejected any mismatch;
3. real bundles silently ingested with `default_image_hash` and `default_project`;
4. LLM packagers could reformat bytes;
5. the engine's validate stages silently passed LLM-shaped layers, and unimplemented layers claimed `completed`;
6. nothing turned passed runs into training data.

## Data flow
```
source (png/code) --sha256-----------------------------+
raw L1+2 bundle  -> lint -> seal12  -> pack -> ingest.py          -> Artifacts L1, L2
raw L3+4.x bundle-> lint -> seal342 -> pack -> ingest_layer342.py -> Artifacts L3, L4.1, L4.2
                    StrictPipeline (engine stages + contract stage) -> Archives/Run_<id>/ + Logs/Pipeline_Ledger.json
Archives -> gates -> scrub -> dedup -> group split -> Datasets/<version>/ -> verify
```
Raw bundles are kept byte-exact in `Raw/`. The sealed copy is a derived artifact; the seal report stores what the model declared next to what the engine computed.

## Modules
| module | job |
|---|---|
| `uic/spec.py` | every enum from the prompts (19 taxonomy, 10 states, 12 actions, 13 events, ...), prompt fingerprints |
| `uic/check.py` | the prompt rules as code; every finding has a rule id (`L2.SURVIVE`, `C.HALT`, ...) |
| `uic/bundle.py` | byte-exact pack + the packager prompts' validation rules |
| `uic/seal.py` | recomputes hashes, ids, provenance; engine owns them, never the model |
| `uic/pipeline.py` | `StrictPipeline`: same stages as the engine, fail-closed (no legacy synthesis, contract stage, force_* refused, honest stubs) |
| `uic/export.py` | gates, scrub, exact + near dedup, group split, strict row schema, verify |
| `uic/cli.py` | `prompts lint pack run export verify demo` |
| `uic/fixtures.py` | prompt-conformant sample bundles used by tests and `demo` |

## Decisions (ADR summary)
1. **Wrap, do not rewrite.** The engine has 82 passing tests and real audit history. UIC adds a layer around it; nothing existing changes.
2. **The engine computes hashes.** An LLM cannot compute SHA-256. The seal replaces every hash, records declared vs computed, and the unchanged ingest scripts accept the result. Two hash definitions exist (prompt: output_hash omits HASH_CHAIN; ingest: hashes the whole body); the seal satisfies both.
3. **Deterministic packaging.** `uic pack` is lossless by construction and tested byte-for-byte (CRLF, trailing spaces, unicode).
4. **Executable contract.** Rules live in code with ids, so prompt drift or model drift fails a test, not a dataset. 61 mutation tests, one per rule family.
5. **Fail closed, quarantine loudly.** A run failing any gate goes to `quarantine.jsonl` with reasons. Never exported, never silently dropped.
6. **Immutable dataset versions.** Export refuses an existing non-empty directory. `verify` re-hashes every file and row.
7. **Split by group (project), not by row.** Near-identical screens never straddle train and test. `verify` checks for leakage.
8. **JSONL core.** Zero new dependencies (pydantic only). Parquet export is a later, optional step.
9. **Sources live outside git.** Images and code sources go to a content-addressed store (`Images/<sha256>.<ext>`, `Sources/`). The GitHub tool used to write this branch is text-only; binaries stay local or go to DVC.
10. **Strict mode is a subclass, not an edit.** `StrictPipeline` swaps stage objects in place (same count, order, names), so `ingest_layer342`'s resume-by-index logic and the legacy tests are unaffected.

## Row schema (strict, extra fields rejected)
`row_id, run_id, project_id, group_id, source_hash, source_kind, source_ref, l1, l2, l3, l41, l42, quality, warnings, redactions, provenance, content_hash`. `provenance` carries uic version, SHA-256 of every prompt file, per-artifact hashes. `content_hash` is run-independent: the same content gives the same hash in any run.

## Failure model
| failure | where caught | result |
|---|---|---|
| model breaks a prompt rule | `uic lint`, `ContractStage`, export gate | run refused / rolled back / quarantined |
| fabricated or default hash | seal replaces; gate `G.SOURCE_HASH` | cannot reach a dataset |
| layer342 bundle never ingested | `RequireIngested` | run fails and rolls back; nothing synthesized |
| test hooks set in a real run | `GuardedIngest` | run refused |
| tampered archive | gate `G.ARTIFACT_HASH` | quarantined |
| missing layer, failed pipeline | gates `G.LAYERS`, `G.PIPELINE` | quarantined |
| duplicate / near-duplicate screen | exact hash + MinHash/LSH | quarantined |
| PII or secrets in text | scrub (email, phone, key patterns) | redacted, counted in the row |
| dataset edited after export | `uic verify` | file hash / row hash / leakage report |

## Roadmap (not built yet)
1. Capture lane: Playwright + Chromium in a pinned Docker image for exact DOM ground truth (replaces WeasyPrint, which has no JS). Needs a machine that can download Chromium.
2. Extractor adapters: call a vision model through an API (or a local Qwen VL) so no manual copy and paste. Interface: `extract(source) -> (l12_text, l342_text)`. Needs a provider choice and keys.
3. Real Layers 4.3, 5, 6. They are honest stubs today; implementing them needs a spec for what each emits.
4. Activate CI: copy `tools/ci/uic.yml` to `.github/workflows/uic.yml` (the tool used to write this branch cannot create workflow files).
5. DVC for `Images/` and `Datasets/`; Parquet + HF datasets export; Design2Code-style scoring harness for the teacher-student loop.
6. JCS (RFC 8785) canonicalization if a non-Python consumer must verify hashes.
