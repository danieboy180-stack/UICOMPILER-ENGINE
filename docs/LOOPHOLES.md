# Loopholes found in the repo and their status

Evidence is from reading `ingest.py`, `ingest_layer342.py`, `orchestrator/*`, and running the real pipeline on prompt-conformant bundles. Status: CLOSED = fixed and tested; MITIGATED = safe through `uic`, direct use still exposed; OPEN = not fixed.

| # | loophole | status | how |
|---|---|---|---|
| 1 | Prompts make the LLM write SHA-256 values; `ingest_layer342` rejects any mismatch (exit 10) | CLOSED | `uic.seal.seal342`; test `test_unsealed_llm_hashes_are_rejected...` |
| 2 | `ingest.py` reads `image_hash`, `project_id`, `pipeline_id` from the Layer 1 body; the prompt puts them in MANIFEST_UPDATE, so real runs get `default_image_hash` / `default_project` | CLOSED via uic | `seal12` injects real values; export gate `G.SOURCE_HASH` |
| 3 | Prompt says output_hash omits HASH_CHAIN; ingest hashes the whole body | CLOSED | seal computes both; `verify_chains` re-checks |
| 4 | Layer 1/2 models validate almost nothing (extra fields allowed) | CLOSED at lint/export | `uic.check` L1.*, L2.* rules |
| 5 | Engine freeze check treats any `id` key in L2 as an L1 id; the L2 prompt creates new structural ids | CLOSED by contract | addendum section B + `L2.FREEZE.*`; engine stage untouched |
| 6 | Engine validate stages silently no-op on LLM IR (no `root_node`) | MITIGATED | `uic.check` validates the real prompt schema; export requires it. Engine stages still no-op: OPEN (roadmap 4) |
| 7 | Layers 3, 4.1, 4.2 synthesize from legacy `semantics` and pass with junk on fused L2 | MITIGATED | `uic run` never runs the pipeline without a layer342 bundle; direct `Pipeline()` use still synthesizes: OPEN |
| 8 | Layers 4.3, 5, 6 are stubs writing `payload: completed` | MITIGATED | export ignores them. Replacement: OPEN (roadmap 3) |
| 9 | `mask_secrets` covers logs only | CLOSED for datasets | export scrub (email, phone, key patterns) |
| 10 | `force_*` test hooks live in production stages | OPEN | `uic` never sets them; the archive cannot prove they were unset |
| 11 | LLM packagers can silently reformat | CLOSED | `uic pack`, byte-exact tests |
| 12 | Duplicate bundle is deleted from the inbox by ingest | MITIGATED | raw bundles are saved first in `Raw/` |
| 13 | Real-data ground truth uses WeasyPrint (no JS, limited flex) | OPEN | capture lane (roadmap 1) |
| 14 | Legacy engine taxonomy is 5 types, prompts define 19 | CLOSED in export path | export uses prompt taxonomy; legacy stages untouched |
| 15 | Source image or code was never stored; only a hash existed | CLOSED | `Images/`, `Sources/` content-addressed store |
| 16 | Nothing prevented train/test leakage | CLOSED | group split + `verify` leakage check |

Test evidence: 165 tests pass (82 engine + 83 uic) and `ci_gate.py` passes, run on a sandbox copy of `main` plus this branch.
