# Runbook

## One-time check
```
pip install -r requirements.txt pytest
python -m pytest -q            # engine + uic
python ci_gate.py
python -m uic demo --root /tmp/uic_demo   # prints DEMO PASSED
python -m uic prompts          # fingerprints of the stored prompts
```
Run everything from the repo root (the engine modules import by top-level name).

## Per screenshot or code file
1. **Extract Layer 1+2.** Give a model `prompts/ui_god_l12_fused_v1_1.md` + `prompts/ADDENDUM_v1_2.md` (sections A to D) + your image or code. Save its raw reply to `l12.txt`. No packager prompt is needed.
2. **Ingest it.**
   ```
   python -m uic run --root . --source shot.png --project stackra-web --l12 l12.txt
   ```
   This lints (refuses on contract errors, with rule ids), keeps the raw reply in `Raw/`, seals hashes and ids, stores the source by hash in `Images/` or `Sources/`, packs byte-exact, ingests.
3. **Extract Layer 3+4.1+4.2.** Give a model `prompts/ui_god_l342_fused_v1_1.md` + addendum (C to E) + `Artifacts/layer2_csir.json` + `Artifacts/layer1_ccir.json`. Save the raw reply to `l342.txt`.
4. **Finish the run.**
   ```
   python -m uic run --root . --l342 l342.txt
   ```
   Lints, seals against the Layer 2 on disk, ingests, runs the unchanged Pipeline, archives.
5. **Build a dataset version** (immutable; pick a new directory each time):
   ```
   python -m uic export --root . --out Datasets/v1 --task structure
   python -m uic verify Datasets/v1
   ```
   Tasks: `structure` (L1 to L2), `design_system` (L2 to L3), `semantic` (L3 to L4.1), `execution` (L4.1 to L4.2). Add `--require-source` to drop rows whose image is not in the store.

`run --export DIR` exists for smoke tests; it overwrites DIR. Use `export` for real versions.

## Exit codes
0 ok; 2 contract/lint refusal; 3 ingest refusal; 4 pipeline or verify failure.

## Reading refusals
| message | meaning | fix |
|---|---|---|
| `L2.FREEZE.BARE_ID` | Layer 2 used `id` for its own record | use `region_id` etc. (addendum B) |
| `L2.SURVIVE` | a Layer 1 object is missing from Layer 2 | re-run Layer 2; never drop objects |
| `L1.OBJ.CONF_CAP` | confidence too high for a null material field | lower it or fill the field |
| `C.HALT` | gate/validation block lacks halt fields | add the three null fields |
| `G.SOURCE_HASH` (export) | run used a default or fake source hash | re-run through `uic run` |
| `DEDUP.NEAR` (export) | too similar to an earlier screen | expected; check `quarantine.jsonl` |

## What lives where
`Raw/` model replies, byte-exact. `Images/`, `Sources/` inputs by sha256. `Archives/Run_<id>/` verified runs. `Datasets/<version>/` train/val/test JSONL, `quarantine.jsonl`, `manifest.json`, `DATASET_CARD.md`. All are gitignored runtime folders except `Datasets/`, which you decide to commit or put under DVC.

## Working with Claude on this
Send a screenshot or code; Claude produces the two bundles per the prompts, runs the lane in its sandbox against a copy of the repo, and reports the lint/seal/pipeline/export result. Bundles can be committed as text. Images cannot be written through the GitHub tool, so keep them in your local `Images/` store.
