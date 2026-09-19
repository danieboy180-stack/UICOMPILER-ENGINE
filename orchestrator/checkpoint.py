from pathlib import Path
from typing import Any
from .orchestrator import PipelineContext, Stage
from .lock import locked_read, locked_write, simple_file_lock, safe_replace

def snapshot(ctx: PipelineContext, stage_name: str) -> bool:
    try:
        root = ctx.manifest_path.parent.parent
        checkpoint_dir = root / "Checkpoints" / ctx.run_id / f"pre_{stage_name.lower()}"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        manifest_src = root / "Manifest" / f"manifest_{ctx.run_id}.json"
        if manifest_src.exists():
            with locked_read(manifest_src, parse_json=False) as content:
                if content is not None:
                    with locked_write(checkpoint_dir / f"manifest_{ctx.run_id}.json", content):
                        pass
            
        ctx.last_checkpoint = stage_name
        return True
    except (TimeoutError, OSError) as exc:
        ctx.logger.log(f"Checkpoint(pre_{stage_name.lower()})", "ERROR", f"Shared-file access failed: {exc}")
        return False
    except Exception as exc:
        ctx.logger.log(f"Checkpoint(pre_{stage_name.lower()})", "ERROR", f"Checkpoint failed: {exc}")
        return False

class CheckpointStage(Stage):
    def __init__(self, target_stage: str):
        self.target_stage = target_stage
        self.name = f"Checkpoint(pre_{target_stage.lower()})"

    def run(self, ctx: PipelineContext) -> bool:
        return snapshot(ctx, self.target_stage)

class RollbackStage(Stage):
    name = "Rollback"
    def run(self, ctx: PipelineContext) -> bool:
        try:
            root = ctx.manifest_path.parent.parent
            failed_stage = ctx.storage.get("failed_stage")

            if hasattr(ctx, "last_checkpoint") and ctx.last_checkpoint:
                checkpoint_dir = root / "Checkpoints" / ctx.run_id / f"pre_{ctx.last_checkpoint.lower()}"
                if checkpoint_dir.exists():
                    manifest_backup = checkpoint_dir / f"manifest_{ctx.run_id}.json"
                    if manifest_backup.exists():
                        with locked_read(manifest_backup, parse_json=False) as content:
                            if content is not None:
                                with locked_write(root / "Manifest" / f"manifest_{ctx.run_id}.json", content):
                                    pass

                    ctx.logger.log(self.name, "INFO", f"Restored from pre_{ctx.last_checkpoint.lower()} checkpoint.")

            if failed_stage:
                failed_stage_lower = failed_stage.lower()
                # Map stage name to real artifact filenames
                stage_artifact_map = {
                    "layer1": "layer1_ccir.json",
                    "validatel1": "layer1_ccir.json",
                    "layer2": "layer2_csir.json",
                    "validatel2": "layer2_csir.json",
                    "layer3": "layer3_visual_ir.json",
                    "validatel3": "layer3_visual_ir.json",
                    "layer4_1": "layer4_1_semantic_ir.json",
                    "layer4.1": "layer4_1_semantic_ir.json",
                    "validatel4_1": "layer4_1_semantic_ir.json",
                    "validatel4.1": "layer4_1_semantic_ir.json",
                    "layer4_2": "layer4_2_execution_ir.json",
                    "layer4.2": "layer4_2_execution_ir.json",
                    "validatel4_2": "layer4_2_execution_ir.json",
                    "validatel4.2": "layer4_2_execution_ir.json",
                    "layer4_3": "layer4_3.json",
                    "layer4.3": "layer4_3.json",
                    "layer5": "layer5.json",
                    "layer6": "layer6.json",
                }
                candidates = [root / "Artifacts" / f"{failed_stage_lower}.json"]
                art_name = stage_artifact_map.get(failed_stage_lower)
                if art_name:
                    candidates.insert(0, root / "Artifacts" / art_name)

                for output_file in candidates:
                    if output_file.exists():
                        dest_dir = root / "Python_Output" / "runs" / ctx.run_id
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        dest_file = dest_dir / f"FAILED_{output_file.stem}.json"
                        with simple_file_lock(output_file):
                            safe_replace(output_file, dest_file)
                        ctx.logger.log(self.name, "INFO", f"Moved failing output to {dest_file}")
                        break

            ctx.logger.log(self.name, "INFO", "Rollback complete. Pipeline halted.")
            return True
        except (TimeoutError, OSError) as exc:
            ctx.logger.log(self.name, "ERROR", f"Shared-file access failed: {exc}")
            return False
        except Exception as exc:
            ctx.logger.log(self.name, "ERROR", f"Rollback failed: {exc}")
            return False
