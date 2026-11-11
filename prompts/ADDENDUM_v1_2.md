# UI GOD prompt addendum v1.2 (engine contract)

Append this to the fused Layer 1+2 prompt (sections A, B, C, D) and to the fused Layer 3+4.1+4.2 prompt (sections C, D, E).
It changes no v1.1 rule. It pins down places where v1.1 is silent and the engine needs one fixed answer.
Code source of truth: `uic/spec.py` and `uic/check.py`; rule ids are in brackets.

## A. Layer 1 body shape
1. Top-level keys: `input`, `screen`, `scenes` (video only), `objects` (list), `animations` (only if motion), `report`. [L1.SHAPE]
2. The Stage 5 extraction report lives inside the Layer 1 JSON as `report`; `compiler_ready` must be a boolean. [L1.REPORT]
3. Every object carries `source_frame`. [L1.OBJ.SOURCE_FRAME]
4. Every object with confidence below 0.60 is listed in `report.uncertain_regions`. [L1.REPORT.UNCERTAIN]

## B. Layer 2 id namespace
1. Layer 1 object ids (`role_N`) are frozen. Layer 2 never uses a bare `id` key for anything it defines. Use `region_id`, `component_id`, `template_id`, `instance_id`, `screen_id`, `scene_id`, `state_id`. [L2.FREEZE.BARE_ID]
2. Layer 2 ids never equal a Layer 1 id. [L2.FREEZE.COLLISION]
3. Every Layer 1 object id appears somewhere in Layer 2 (children or instances lists). [L2.SURVIVE]
4. `confidence.project` is at most the lowest Layer 1 object confidence. [L2.CONF.UPGRADE]

## C. Things the model must not compute
Hashes, `run_id`, `image_hash`, `project_id` and `pipeline_id` are owned by the engine. Emit any 64-character hex placeholder for hash fields; the seal step recomputes every one and records what you declared next to what it computed. Do not spend effort on them and never present them as real.

## D. Static inputs (screenshot, image, code)
No TIMING records, no ERROR_GRAPH records, no navigation edges, `MOTION_TOKENS: []`, `RESPONSIVE_RULES: not_observable`. [L42.STATIC, L3.MOTION, L3.RESPONSIVE]

## E. Layer 3 / 4.1 / 4.2 shape
1. Every collection of records is written as `{ordering, records}` with ordering in sort_by_id, sort_by_reading_order, sort_alphabetical. [C.ORDERING]
2. Every validation gate and terminal validation block carries `halt_reason`, `halt_stage`, `blocking_object` (all null on pass). [C.HALT]
3. `VERSIONING` has all four keys. `HASH_CHAIN` has five keys on 4.1 and 4.2; on Layer 3 the chain sits at `DESIGN_HASH.hash_chain`. [C.VERSIONING, C.HASH_CHAIN]
4. Layer 3 `TOKEN_REGISTRY` lists only promoted tokens (2+ instances or confidence 0.90+). [L3.TOKEN_PROMOTION]
5. Layer 4.2 events use only the 13-value vocabulary, each with evidence; every transition names a real event. [L42.EVENT, L42.TRANSITION]
