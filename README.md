# UICOMPILER-ENGINE

Deterministic orchestration and verification spine for a layered screenshot → UI-code compiler (UI_GOD).

It does **not** generate layers. Layer outputs (produced by an LLM) are dropped into `Python_Inbox/` as tagged text
bundles. The engine ingests them, validates every layer contract, anchors each artifact in a SHA-256 hash chain,
and fails closed when anything does not line up.

## Pipeline

22 stages, run in order by `orchestrator.Pipeline`:

`Ingest → FreezeBoundary → ValidateL1 → ValidateL2 → ConfidencePropagation → Layer3 → ValidateL3 → Layer4.1 →
ValidateL4.1 → Layer4.2 → ValidateL4.2 → Checkpoint → Layer4.3 → Checkpoint → Layer5 → Checkpoint → Layer6 →
MetaVerify → Save → Archive → PipelineLedger → BuildReport`

Invariants:

- **Freeze boundary** – Layer 2 may not introduce IDs that Layer 1 did not define, and a frozen layer artifact is never rewritten.
- **Hash chain** – every artifact records its parent hash in `Artifacts/Artifact_Ledger.json` and the run manifest; `MetaVerify` recomputes every hash from disk.
- **Fail closed** – a corrupt or emptied ledger/manifest is a failure, never an empty pass.
- **Confidence propagation** – candidates below 0.8 confidence are flagged `weak_child` in memory and reported as run warnings.
- **Layout inference (Layer 4.1)** – `orchestrator/layout.py` builds the tree from Layer 1 boxes: smallest enclosing box is the parent (content that overflows its container by a few px still belongs to it), vertical flex stacks with nested rows via recursive row/column cuts, and absolute positioning wherever boxes overlap. Pure and deterministic: the result does not depend on input order.
- **Geometry gate** – ValidateL4.1/4.2 reject duplicate ids and children that do not fit inside their parent box (same predicate the inference uses).
- **One run at a time per project** – runs share `Artifacts/`, so they are serialized by `.pipeline_run.lock` (a second run waits its turn, or fails cleanly after `Pipeline.RUN_LOCK_TIMEOUT`). `ingest.ingest_bundle` hands the run id back to `IngestStage`, so a pipeline never adopts someone else's run, a duplicate bundle halts at Ingest, and a finished (archived) run cannot be re-run.
- **Rollback** – a failing stage restores the last checkpointed manifest and moves its output to `Python_Output/runs/<run_id>/FAILED_*.json`.

## Quick start

Python 3.12+ and `pip install -r requirements.txt`.

```bash
# put layer12_bundle.txt (<LAYER1_OUTPUT>/<LAYER2_OUTPUT>) or layer342_bundle.txt in Python_Inbox/
python ingest.py --root .            # layers 1-2
python ingest_layer342.py --root .   # layers 3 / 4.1 / 4.2 (fused prompt v1.1)
```

```python
from pathlib import Path
from orchestrator import Pipeline, PipelineContext

ctx = PipelineContext(run_id=None, manifest_path=Path("Manifest/manifest.json"))
exit_code = Pipeline().run(ctx)
```

| Exit code | Meaning |
|-----------|---------|
| 0  | success |
| 10 | stage failure (default) |
| 20 | hash-chain pointer gap (MetaVerify) |
| 30 | integrity failure: tampered/missing artifact or unreadable ledger (MetaVerify), Layer 5 failed gate |
| 40 | Layer 5 crash |
| 50 | Save/export failure |

Each run writes `Logs/Run_<id>.jsonl`, `Logs/Pipeline_Ledger.json` and `Reports/Run_<id>/BUILD_REPORT.md`.
Runtime directories are git-ignored.

## Tests

```bash
python ci_gate.py
```

Runs the static test linter (`verification/lint_tests.py`), the full pytest suite, and checks that
verification receipts were recorded. Every passing test must contain a real assertion or a receipt, and receipts must carry real captured evidence: placeholder or repeated lines are rejected by `record_verification`.
A ready-made GitHub Actions workflow for the same gate is in `tools/ci/ci.yml` (copy it to `.github/workflows/ci.yml` to enable CI).

## Real-data check

`tests/fixtures/real/` holds three screens extracted from a real public landing page rendered with WeasyPrint
(`tools/real_data/extract_real.py page.html 390 844 name`, needs `pip install weasyprint`). Layer 1/2 come from the
rendered DOM; ground truth is the DOM's own nesting and CSS flex direction/gap. `test_real_data_accuracy.py` enforces
the thresholds below in CI. Measured on 34 real single-screen renders (632 elements):

| | before | after |
|---|---|---|
| Pipeline completes (hash chain intact) | 23.5 % (8/34) | 100 % (34/34) |
| Parent accuracy | 45.2 % | 99.2 % (627/632) |
| Layout direction / flex gap (scoreable containers) | not produced | 18/18, 3/3 |
| Same output for shuffled input | 0 % | 100 % |

The few remaining parent misses are DOM siblings whose boxes overlap, which geometry alone cannot separate.
Only 3 flex containers were scoreable (most flex rows wrap invisible elements), so treat direction/gap as a small sample.

## Accuracy harness

The scoping doc's success criterion (IoU of boxes and role accuracy against hand-labeled Layer 4.2 JSON, 90 % gate) is measured by
`tools/eval/score_accuracy.py`:

```bash
python tools/eval/score_accuracy.py --labels labels/ --preds preds/ [--min-iou 0.9] [--min-role 0.9]
```

Both folders hold one Layer 4.2 JSON per screen, paired by file name; a screen without a prediction scores zero and fails the gate.
Row/column wrapper nodes the engine invents, and the root, are transparent. It needs your labeled screens and your pipeline's output.

## Status and limits

- Layers 1-2 arrive as LLM output; Layer 3 carries their style/text fields; Layers 4.1 and 4.2 are inferred deterministically from the Layer 1 boxes (or ingested via the layer-342 bundle).
- There is no CV/VLM code in this repo yet, so the 50-screenshot / 90 % target in `L1_42_SCOPING_DOC.md` is not measured here (the scorer above is ready for your labeled screens). The real-data check above scores Layers 3-4.2 on DOM-derived boxes, not on pixels.
- `AUDIT_*.md` are point-in-time snapshots; some findings in them (stubbed stages, hard-coded stage indexes) were fixed afterwards.
