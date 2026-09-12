import json
import shutil
from pathlib import Path
from typing import Any
from .orchestrator import PipelineContext, Stage

def snapshot(ctx: PipelineContext, stage_name: str) -> bool:
    root = ctx.manifest_path.parent.parent
    checkpoint_dir = root / "Checkpoints" / ctx.run_id / f"pre_{stage_name.lower()}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    manifest_src = root / "Manifest" / f"manifest_{ctx.run_id}.json"
    if manifest_src.exists():
        shutil.copy2(manifest_src, checkpoint_dir / f"manifest_{ctx.run_id}.json")
        
    ctx.last_checkpoint = stage_name
    return True

class CheckpointStage(Stage):
    def __init__(self, target_stage: str):
        self.target_stage = target_stage
        self.name = f"Checkpoint(pre_{target_stage.lower()})"

    def run(self, ctx: PipelineContext) -> bool:
        return snapshot(ctx, self.target_stage)

class RollbackStage(Stage):
    name = "Rollback"
    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        failed_stage = ctx.storage.get("failed_stage")
        
        # 1. Restore from latest checkpoint if any
        if hasattr(ctx, "last_checkpoint") and ctx.last_checkpoint:
            checkpoint_dir = root / "Checkpoints" / ctx.run_id / f"pre_{ctx.last_checkpoint.lower()}"
            if checkpoint_dir.exists():
                manifest_backup = checkpoint_dir / f"manifest_{ctx.run_id}.json"
                if manifest_backup.exists():
                    shutil.copy2(manifest_backup, root / "Manifest" / f"manifest_{ctx.run_id}.json")
                
                ctx.logger.log(self.name, "INFO", f"Restored from pre_{ctx.last_checkpoint.lower()} checkpoint.")
        
        # 2. Move failing layer's output (if any)
        if failed_stage:
            failed_stage_lower = failed_stage.lower()
            output_file = root / "Artifacts" / f"{failed_stage_lower}.json"
            if output_file.exists():
                dest_dir = root / "Python_Output" / "runs" / ctx.run_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / f"FAILED_{failed_stage_lower}.json"
                shutil.move(str(output_file), str(dest_file))
                ctx.logger.log(self.name, "INFO", f"Moved failing output to {dest_file}")
                
        ctx.logger.log(self.name, "INFO", "Rollback complete. Pipeline halted.")
        return True
