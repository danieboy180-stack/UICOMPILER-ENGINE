# ui-samples / c4e33e193aab (first end-to-end run, 2026-11-12)

The screenshot itself is NOT stored in git (the GitHub tool is text-only). Keep the original file; its hash below identifies it.

| field | value |
|---|---|
| source file | `Screenshot_20261112_123256.jpg` |
| source sha256 | `c4e33e193aab77ef04cfccbeb248c8a7cb728cebb4bb9d348d7482e4e2921953` |
| size | 1437 x 1576 px, 193,030 bytes |
| project (group for splits) | `ui-samples` |
| content | dark mobile bottom sheet titled `Choose a delivery address`: drag handle, close button, `Use current location` row with `Recommended` caption, divider, green `Add a new address` pill |
| objects extracted | 13 (lowest confidence 0.45: `image_0`, an 18 px clipped sliver) |

## How it was extracted
By Claude, following `prompts/ui_god_l12_fused_v1_1.md`, `prompts/ui_god_l342_fused_v1_1.md` and `prompts/ADDENDUM_v1_2.md`. Geometry is percent of the image from exact pixel bounding boxes; colors are sampled from pixels (JPEG, so within a couple of levels); contrast ratios are computed. Bundles here are minified JSON, saved exactly as run.

## Result
- `uic lint`: 0 errors, 0 warnings on both bundles.
- Strict pipeline: pass, all layer hashes MATCH.
- Dataset export (`--task structure --require-source`): 1 row, train split, quarantine empty, `uic verify` clean.
- A second run in a fresh folder reproduced identical layer hashes and the same row `content_hash`.
- Measured, not claimed as a pass/fail: contrast of the green label on the green pill is 2.84:1 (white on sheet 17.01:1, caption on sheet 8.13:1).

## Reproduce
```
python -m uic run --root . --source <the screenshot> --project ui-samples --l12 bundles/ui-samples/c4e33e193aab/layer12_bundle.raw.txt
python -m uic run --root . --l342 bundles/ui-samples/c4e33e193aab/layer342_bundle.raw.txt
python -m uic export --root . --out Datasets/v1 --task structure --require-source
python -m uic verify Datasets/v1
```
