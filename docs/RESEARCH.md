# Research notes (saved 2026-11-11)

Paraphrased from the sources below; verify before citing. Each line says what UIC takes from it.

## Datasets and training recipes
- WebSight (Hugging Face blog and dataset card): paired HTML + screenshot data, synthetic on purpose to avoid real-page noise; v0.2 is about 1.9M rows and about 369 GB, so roughly 190 KB per row. Used for: row sizing, image + code pair shape. https://huggingface.co/blog/websight , https://huggingface.co/datasets/HuggingFaceM4/WebSight
- UICoder (arXiv 2406.07739): generate UI code with a model, filter with compiler success and a vision-model score, dedupe, fine-tune, repeat. Used for: engine-as-automated-filter design. https://arxiv.org/abs/2406.07739
- Design2Code (arXiv 2403.03163): metrics block-match, text, position, color (CIEDE2000), CLIP ViT-B/32. Used for: planned scoring harness. https://arxiv.org/abs/2403.03163
- WebUI (arXiv 2301.13280, uimodeling.github.io): per-page accessibility tree, computed style and layout, viewport and full-page shots; splits grouped by domain (70/10/20). Used for: group split rule. https://uimodeling.github.io
- PixelWeb (arXiv 2504.16419) notes annotation inaccuracies in WebUI and Rico boxes. Used for: never trusting boxes without our own validation. https://arxiv.org/pdf/2504.16419v2.pdf
- Unsloth / TRL vision fine-tuning: chat-style `messages` with text and image content. Used for: `--task` message export. https://unsloth.ai/docs/basics/vision-fine-tuning.md

## Capture and determinism
- Playwright screenshots: disable animations, hide caret, fix viewport and device scale, keep OS, browser and fonts identical. Used for: capture lane design. https://argos-ci.com/blog/fix-flaky-visual-tests , https://scrapegraphai.com/blog/playwright-screenshot
- `page.accessibility.snapshot()` is deprecated; use CDP `Accessibility.getFullAXTree`. Used for: capture lane.
- WeasyPrint runs no JavaScript and handles flexbox only for simple cases. Used for: loophole 13. https://www.useanvil.com/ask-anvil/pdfs/generate-pdfs-in-ci-without-headless-browser/

## Data hygiene
- nn-dup (PyPI): exact + MinHash/LSH near-dup + structural dedup, family-aware splits, drop report and manifest. Used for: dedup parameters (10-token shingles, 256 permutations, 0.85) as a tuning starting point; UIC currently uses 64 permutations, 8 bands, Jaccard 0.9 on layout/text signatures. https://pypi.org/project/nn-dup/2.1.4/
- DVC: hashes data, commits small pointer files to Git, syncs to remote storage. Used for: roadmap 6. https://www.casrai.org/guides/dvc-data-version-control-research-datasets-ml-pipelines
- RFC 8785 (JSON Canonicalization Scheme): deterministic JSON for hashing and signing across languages. The repo's canonical form (sorted keys, compact separators, ASCII escapes) is stable in Python but is not JCS. Used for: roadmap 7. https://datatracker.ietf.org/doc/rfc8785/

## Operations
- GitHub Free personal accounts include 2,000 Actions minutes per month for private repositories. Used for: CI budget (the whole suite runs in about a minute). https://docs.github.com/en/get-started/learning-about-github/githubs-plans
