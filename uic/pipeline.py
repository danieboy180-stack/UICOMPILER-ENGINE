"""StrictPipeline: the unchanged engine pipeline with its fail-open stages made fail-closed.

Same stage count, order and names as orchestrator.Pipeline (ingest_layer342 resumes by stage index), so it is a drop-in.
What changes, and why (see docs/LOOPHOLES.md):
  * Layer3/4.1/4.2 never synthesize from legacy `semantics`; they require the ingested layer342 artifact.
  * ValidateL4.2 additionally runs the executable prompt contract (uic.check) on every artifact on disk and verifies
    the hash chains; a contract failure fails the run and triggers the engine's normal rollback.
  * Test-only force_* hooks are refused at Ingest.
  * Layers 4.3/5/6 stay unimplemented, but say so (`stub: true`, `not_implemented`) instead of claiming `completed`.
"""
from __future__ import annotations

import json

from orchestrator.orchestrator import IngestStage, Pipeline, Stage, ValidateL4_2Stage

from . import spec
from .bundle import chash
from .check import _static, check_ir, check_l1, check_l2, has_errors, l1_objects, verify_chains, walk
from .export import LAYER_FILES


class GuardedIngest(IngestStage):
    def run(self, ctx) -> bool:
        bad = sorted(str(k) for k in ctx.storage if str(k).startswith("force_"))
        if bad:
            ctx.logger.log(self.name, "ERROR", f"test-only hooks are set in a strict run: {bad}")
            return False
        return super().run(ctx)


class RequireIngested(Stage):
    def __init__(self, artifact: str, failed_file: str):
        self.artifact, self.failed_file = artifact, failed_file

    def run(self, ctx) -> bool:
        from orchestrator.lock import locked_read
        root = ctx.manifest_path.parent.parent
        if ctx.manifest_path.exists():
            with locked_read(ctx.manifest_path, default={}) as m:
                if isinstance(m, dict) and m.get("artifacts", {}).get(self.artifact):
                    return True
        failed = root / "Python_Output" / "runs" / str(ctx.run_id) / self.failed_file
        why = "its gate failed during ingest" if failed.exists() else "the layer342 bundle was never ingested"
        ctx.logger.log(self.name, "ERROR", f"{self.artifact} missing: {why}. Strict mode never synthesizes layers.")
        return False


class ContractStage(ValidateL4_2Stage):
    def run(self, ctx) -> bool:
        if not super().run(ctx):
            return False
        art = ctx.manifest_path.parent.parent / "Artifacts"
        try:
            docs = {k: json.loads((art / fn).read_text(encoding="utf-8")) for k, fn in LAYER_FILES.items()}
        except (OSError, ValueError) as exc:
            ctx.logger.log(self.name, "ERROR", f"contract check cannot read artifacts: {exc}")
            return False
        l1, l2 = docs["l1"], docs["l2"]
        ids2 = {v for _, d in walk(l2) for k in spec.L2_STRUCTURAL_ID_KEYS if isinstance((v := d.get(k)), str)}
        cx = {"l1_ids": {o.get("id") for o in l1_objects(l1)}, "l2_ids": ids2, "static": _static(l1)}
        fs = check_l1(l1) + check_l2(l1, l2)
        for layer, key in (("L3", "l3"), ("L41", "l41"), ("L42", "l42")):
            fs += check_ir(layer, docs[key], cx)
        fs += verify_chains({"L3": docs["l3"], "L41": docs["l41"], "L42": docs["l42"]}, chash(l2))
        for f in fs:
            ctx.logger.log(self.name, "ERROR" if f.level == "error" else "WARNING", str(f))
        return not has_errors(fs)


class _HonestStub(Stage):
    art_key = ""
    parent_key = ""
    label: object = ""

    def run(self, ctx) -> bool:
        from orchestrator.ledger import write_atomic_artifact
        from orchestrator.lock import locked_read
        root = ctx.manifest_path.parent.parent
        parent_hash = None
        if ctx.manifest_path.exists():
            with locked_read(ctx.manifest_path) as m:
                parent_hash = m.get("artifacts", {}).get(self.parent_key, {}).get("hash")
                if self.art_key == "layer4_3":
                    parent_hash = parent_hash or m.get("layer2_hash")
        payload = {"layer": self.label, "payload": "not_implemented", "stub": True}
        try:
            write_atomic_artifact(root, self.art_key, ctx.run_id, payload, parent_hash)
            return True
        except ValueError as exc:
            ctx.logger.log(self.name, "ERROR", str(exc))
            return False


class _Stub43(_HonestStub):
    art_key, parent_key, label = "layer4_3", "layer4_2_execution_ir", "4.3"


class _Stub5(_HonestStub):
    art_key, parent_key, label = "layer5", "layer4_3", 5


class _Stub6(_HonestStub):
    art_key, parent_key, label = "layer6", "layer5", 6


class StrictPipeline(Pipeline):
    def __init__(self):
        super().__init__()

        def swap(attr: str, new: Stage) -> None:
            old = getattr(self, attr)
            new.name = old.name
            setattr(self, attr, new)
            self.stages[self.stages.index(old)] = new

        swap("ingest_stage", GuardedIngest())
        swap("layer3_stage", RequireIngested("layer3_visual_ir", "FAILED_layer3.json"))
        swap("layer4_1_stage", RequireIngested("layer4_1_semantic_ir", "FAILED_layer4_1.json"))
        swap("layer4_2_stage", RequireIngested("layer4_2_execution_ir", "FAILED_layer4_2.json"))
        swap("validate_l4_2_stage", ContractStage())
        swap("layer4_3_stage", _Stub43())
        swap("layer5_stage", _Stub5())
        swap("layer6_stage", _Stub6())
        self._stage_by_class = {type(s).__name__: s for s in self.stages}
