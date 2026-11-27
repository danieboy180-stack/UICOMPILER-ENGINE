# tools/author

Authoring tool for `bundles/ui-samples/`. It does not call a model and it is not part of the strict pipeline; it
turns hand-authored object boxes into the raw Layer 1+2 and Layer 3/4.1/4.2 bundles that `uic run` ingests.

- `spec_<name>.py` — one screen: which original upload, the crop box of one phone screen, and every object with its pixel box, role, text and parent.
- `gen.py` — compiles a spec into the bundles. Geometry is percent of the crop, colors are sampled from pixels, contrast is computed, Layer 2 regions and layouts are derived from the measured boxes. Output is deterministic.
- `build.py` — runs every spec: crops the screen to `Images/<name>.png` (gitignored) and writes `bundles/ui-samples/<crop sha256[:12]>/layer12_bundle.raw.txt` and `layer342_bundle.raw.txt`.

```
pip install -r tools/author/requirements.txt
python tools/author/build.py --src-dir _inbox      # originals live in _inbox/ (branch inbox-001), not on main
python -m uic lint --kind 12  bundles/ui-samples/<id>/layer12_bundle.raw.txt
python -m uic lint --kind 342 bundles/ui-samples/<id>/layer342_bundle.raw.txt
```

The crop PNG is only byte-identical (same sha256, same folder id) with the same Pillow version. The Layer 3/4.1/4.2 skeleton is read from `bundles/ui-samples/c4e33e193aab/`.
