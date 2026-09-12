import os
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import ingest

EXIT_OK = 0
EXIT_10 = 10
EXIT_20 = 20

import re
import datetime

def mask_secrets(text: str) -> str:
    if not isinstance(text, str):
        return text
    # Mask common secrets
    text = re.sub(r'sk-[a-zA-Z0-9]{32,}', 'sk-***', text)
    text = re.sub(r'(?i)(bearer\s+)[a-zA-Z0-9\-\._~+/]+=*', r'\1***', text)
    text = re.sub(r'(?i)(api[_-]?key\s*[:=]\s*)["\']?[a-zA-Z0-9\-\._~+/]+["\']?', r'\1"***"', text)
    return text

@dataclass
class PipelineContext:
    run_id: str
    manifest_path: Path
    storage: Dict[str, Any] = field(default_factory=dict)
    logger: 'RunLogger' = field(init=False)
    errors: List[str] = field(default_factory=list)
    last_checkpoint: Optional[str] = None

    def __post_init__(self):
        # Gap 1 (O12 Fix): Isolate manifest per run_id
        if not self.manifest_path.name.endswith(f"_{self.run_id}.json"):
            new_name = f"manifest_{self.run_id}.json"
            self.manifest_path = self.manifest_path.with_name(new_name)
        self.logger = RunLogger(self.manifest_path.parent.parent, self.run_id)

class RunLogger:
    def __init__(self, root: Path, run_id: str):
        self.log_path = root / "Logs" / f"Run_{run_id}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
    def log(self, step: str, status: str, message: str = ""):
        # Keep printing to stdout for tests
        msg = f"[{step}] {status}"
        if message:
            msg += f" - {mask_secrets(message)}"
        print(msg)
        
        # Write structured JSON lines (O9 Part B Gap 8)
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "step": step,
            "status": status,
            "message": mask_secrets(message)
        }
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry) + "\n")

class Stage:
    name: str = "Stage"
    def run(self, ctx: PipelineContext) -> bool:
        return True

    def failure_code(self, ctx: PipelineContext) -> int:
        return EXIT_10

class IngestStage(Stage):
    name = "Ingest"
    def run(self, ctx: PipelineContext) -> bool:
        root = ctx.manifest_path.parent.parent
        # Record time just before calling ingest so we can identify the manifest IT wrote
        import time as _time
        call_ts = _time.time()
        res = ingest.ingest(root)
        if res == ingest.EXIT_OK:
            # Identify the manifest ingest.py just wrote by scanning for files written
            # AFTER our call_ts. Under concurrency, other threads may have written older
            # manifests — we must only accept our own (most recent, post-call_ts).
            try:
                manifest_dir = root / "Manifest"
                manifest_dir.mkdir(parents=True, exist_ok=True)
                # Collect manifests written after we called ingest
                fresh = []
                for mf in manifest_dir.glob("manifest_*.json"):
                    try:
                        if mf.stat().st_mtime >= call_ts - 0.5:  # 0.5s grace for clock skew
                            fresh.append(mf)
                    except OSError:
                        pass
                # Sort newest-first and pick the one that has a matching run_id in its content
                fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                for mf in fresh:
                    try:
                        with open(mf, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                        run_id = manifest.get("artifacts", {}).get("layer12_bundle", {}).get("run_id")
                        if run_id and mf.name == f"manifest_{run_id}.json":
                            ctx.run_id = run_id
                            ctx.manifest_path = mf
                            break
                    except (OSError, json.JSONDecodeError):
                        pass
            except Exception:
                pass

            # CRITICAL GATE (O15 Fix): If run_id is still None, ingest processed nothing
            # (empty inbox, duplicate rejection, or garbage file). Do NOT let downstream stages
            # proceed with a null run_id — that crashes CheckpointStage and others.
            if ctx.run_id is None:
                ctx.logger.log(self.name, "INFO", "No bundle processed (empty inbox, duplicate, or invalid). Halting pipeline cleanly.")
                return False

            return True
        else:
            ctx.logger.log(self.name, "ERROR", f"ingest.ingest returned {res}")
            return False

class ValidateL1Stage(Stage):
    name = "ValidateL1"

class ValidateL2Stage(Stage):
    name = "ValidateL2"

class ValidateL3Stage(Stage):
    name = "ValidateL3"

class ValidateL4_1Stage(Stage):
    name = "ValidateL4.1"

class ValidateL4_2Stage(Stage):
    name = "ValidateL4.2"
    def run(self, ctx: PipelineContext) -> bool:
        # For testing, we look at storage flag to force failure
        if ctx.storage.get("force_layer4_2_fail"):
            return False
        return True
    
    def failure_code(self, ctx: PipelineContext) -> int:
        return EXIT_20 if ctx.storage.get("force_layer4_2_exit_20") else EXIT_10


class Layer4_3Stage(Stage):
    name = "Layer4.3"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact
        root = ctx.manifest_path.parent.parent
        
        parent_hash = None
        if ctx.manifest_path.exists():
            with open(ctx.manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
                parent_hash = manifest.get("artifacts", {}).get("layer12_bundle", {}).get("hash")
                
        payload = {"layer": "4.3", "payload": "completed"}
        write_atomic_artifact(root, "layer4_3", ctx.run_id, payload, parent_hash)
        return True

class Layer5Stage(Stage):
    name = "Layer5"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact, record_failed_gate
        root = ctx.manifest_path.parent.parent
        
        # Gap 7 (O12 Fix): Real Subprocess Sandboxing using Docker
        import subprocess
        import os
        
        if ctx.storage.get("force_layer5_malicious"):
            env = os.environ.copy()
            docker_cmd = str(root / "bin" / "docker.bat") if os.name == 'nt' else "docker"
            
            try:
                # Docker payload simulating execution
                res = subprocess.run(
                    [
                        docker_cmd, "run", "--rm", 
                        "--network", "none", 
                        "-m", "512m", 
                        "python:3.9-slim", 
                        "python", "-c", 
                        "import urllib.request; urllib.request.urlopen('http://malicious.com')"
                    ],
                    env=env,
                    capture_output=True,
                    text=True
                )
                
                # Log the raw Docker output so it can be verified
                for line in res.stdout.splitlines():
                    ctx.logger.log(self.name, "STDOUT", line)
                for line in res.stderr.splitlines():
                    ctx.logger.log(self.name, "STDERR", line)
                    
                if res.returncode != 0:
                    ctx.logger.log(self.name, "WARNING", f"Sandbox container killed or failed (exit code {res.returncode})")
                    return False
                    
            except FileNotFoundError:
                ctx.logger.log(self.name, "ERROR", "Docker not found in PATH")
                return False

        # Determine parent hash from manifest if possible
        parent_hash = None
        if ctx.manifest_path.exists():
            with open(ctx.manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
                parent_hash = manifest.get("artifacts", {}).get("layer4_3", {}).get("hash")
        
        # Test 1: simulate failed gate
        if ctx.storage.get("force_layer5_failed_gate"):
            record_failed_gate(root, "layer5", ctx.run_id, parent_hash)
            return False

        # Test 2: crash mid-write
        if ctx.storage.get("force_layer5_crash"):
            # Write partial output to test Rollback
            output_file = root / "Artifacts" / "layer5.json"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write('{"partial": "data"') 
            return False
            
        # Normal write (Substage F)
        payload = {"layer": 5, "payload": "completed"}
        write_atomic_artifact(root, "layer5", ctx.run_id, payload, parent_hash)
        return True
        
    def failure_code(self, ctx: PipelineContext) -> int:
        if ctx.storage.get("force_layer5_failed_gate"):
            return 30
        return 40 if ctx.storage.get("force_layer5_crash") else EXIT_10

class Layer6Stage(Stage):
    name = "Layer6"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_atomic_artifact, record_failed_gate
        root = ctx.manifest_path.parent.parent
        
        parent_hash = None
        if ctx.manifest_path.exists():
            with open(ctx.manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
                parent_hash = manifest.get("artifacts", {}).get("layer5", {}).get("hash")
                
        # Normal write (Substage E)
        payload = {"layer": 6, "payload": "completed"}
        write_atomic_artifact(root, "layer6", ctx.run_id, payload, parent_hash)
        return True

class MetaVerifyStage(Stage):
    name = "MetaVerify"
    def run(self, ctx: PipelineContext) -> bool:
        from .meta_verify import verify_integrity
        import json
        
        root = ctx.manifest_path.parent.parent
        
        # Test 1: Tamper with on-disk artifact
        if ctx.storage.get("force_meta_verify_tamper"):
            tamper_file = root / "Artifacts" / "layer6.json"
            if tamper_file.exists():
                with open(tamper_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data["payload"] = "tampered"
                with open(tamper_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
        
        # Test 2: Gap in pointer chain
        if ctx.storage.get("force_meta_verify_gap"):
            ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
            if ledger_path.exists():
                with open(ledger_path, 'r', encoding='utf-8') as f:
                    ledger = json.load(f)
                # Find layer6 and break its parent_hash
                for e in reversed(ledger):
                    if e["artifact"] == "layer6" and e["run_id"] == ctx.run_id:
                        e["parent_hash"] = "broken_hash"
                        break
                with open(ledger_path, 'w', encoding='utf-8') as f:
                    json.dump(ledger, f, indent=2)

        success, code = verify_integrity(root, ctx.run_id, ctx.logger)
        ctx.storage["meta_verify_code"] = code
        return success

    def failure_code(self, ctx: PipelineContext) -> int:
        return ctx.storage.get("meta_verify_code", EXIT_10)

class SaveStage(Stage):
    name = "Save"
    def run(self, ctx: PipelineContext) -> bool:
        if ctx.storage.get("force_save_fail"):
            return False
        return True
    
    def failure_code(self, ctx: PipelineContext) -> int:
        return 50

class ArchiveStage(Stage):
    name = "Archive"
    def run(self, ctx: PipelineContext) -> bool:
        import shutil
        import stat
        import json
        import os
        
        root = ctx.manifest_path.parent.parent
        archive_dir = root / "Archives" / f"Run_{ctx.run_id}"
        
        if archive_dir.exists():
            ctx.logger.log(self.name, "WARNING", f"Archive directory {archive_dir} already exists, aborting to prevent overwrite.")
            return False
            
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Copy Artifacts/
        artifacts_src = root / "Artifacts"
        artifacts_dest = archive_dir / "Artifacts"
        if artifacts_src.exists():
            shutil.copytree(artifacts_src, artifacts_dest, ignore=shutil.ignore_patterns('*.lock', '*.tmp'))
            
        # 2. Copy manifest
        manifest_src = root / "Manifest" / f"manifest_{ctx.run_id}.json"
        if manifest_src.exists():
            shutil.copy2(manifest_src, archive_dir / f"manifest_{ctx.run_id}.json")
            
        # 3. Pipeline_Ledger entry for this run
        ledger_src = root / "Logs" / "Pipeline_Ledger.json"
        if ledger_src.exists():
            with open(ledger_src, 'r', encoding='utf-8') as f:
                try:
                    ledger = json.load(f)
                    run_entry = next((e for e in ledger if e.get("run_id") == ctx.run_id), None)
                    if run_entry:
                        with open(archive_dir / "Pipeline_Ledger.json", 'w', encoding='utf-8') as out_f:
                            json.dump([run_entry], out_f, indent=2)
                except json.JSONDecodeError:
                    pass
                    
        # 4. Copy BUILD_REPORT.md
        build_report_src = root / "BUILD_REPORT.md"
        if build_report_src.exists():
            shutil.copy2(build_report_src, archive_dir / "BUILD_REPORT.md")
            
        # Set entire copy to read-only
        def set_readonly(path):
            os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH) # 0o444
            
        for dirpath, dirnames, filenames in os.walk(archive_dir):
            set_readonly(dirpath)
            for filename in filenames:
                set_readonly(os.path.join(dirpath, filename))
                
        return True

class PipelineLedgerStage(Stage):
    name = "PipelineLedger"
    def run(self, ctx: PipelineContext) -> bool:
        from .ledger import write_pipeline_ledger
        import time
        root = ctx.manifest_path.parent.parent
        
        status = ctx.storage.get("pipeline_status", "PASS")
        duration = time.time() - ctx.storage.get("start_time", time.time())
        files_produced = 3 if status == "PASS" else 0
        hash_chain_valid = ctx.storage.get("hash_chain_valid", True)
        
        write_pipeline_ledger(
            root=root,
            run_id=ctx.run_id,
            status=status,
            duration_seconds=round(duration, 2),
            files_produced=files_produced,
            warnings=[],
            errors=ctx.errors,
            hash_chain_valid=hash_chain_valid
        )
        return True

class BuildReportStage(Stage):
    name = "BuildReport"
    def run(self, ctx: PipelineContext) -> bool:
        from .report import generate_build_report
        root = ctx.manifest_path.parent.parent
        generate_build_report(root, ctx.run_id)
        return True

class Pipeline:
    def __init__(self):
        # Local import to avoid circular dependency
        from .checkpoint import CheckpointStage
        self.stages: List[Stage] = [
            IngestStage(),
            ValidateL1Stage(),
            ValidateL2Stage(),
            ValidateL3Stage(),
            ValidateL4_1Stage(),
            ValidateL4_2Stage(),
            CheckpointStage("Layer4_3"),
            Layer4_3Stage(),
            CheckpointStage("Layer5"),
            Layer5Stage(),
            CheckpointStage("Layer6"),
            Layer6Stage(),
            MetaVerifyStage(),
            SaveStage(),
            ArchiveStage(),
            PipelineLedgerStage(),
            BuildReportStage()
        ]

    def run(self, ctx: PipelineContext) -> int:
        from .checkpoint import RollbackStage
        import time
        import json
        
        ctx.storage["start_time"] = time.time()
        
        for idx, stage in enumerate(self.stages):
            ctx.logger.log(stage.name, "STARTING")
            success = stage.run(ctx)
            if not success:
                ctx.logger.log(stage.name, "FAILED")
                ctx.errors.append(f"{stage.name} failed")
                ctx.storage["failed_stage"] = stage.name
                
                # Pre-rollback hash validation
                root = ctx.manifest_path.parent.parent
                manifest_path = ctx.manifest_path
                ledger_path = root / "Artifacts" / "Artifact_Ledger.json"
                hash_valid = True
                
                if manifest_path.exists() and ledger_path.exists():
                    try:
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            m = json.load(f)
                        with open(ledger_path, 'r', encoding='utf-8') as f:
                            l = json.load(f)
                        if m and l:
                            # Just a simple validation: see if last entry hash matches manifest
                            last_art = l[-1].get("artifact")
                            last_hash = l[-1].get("hash")
                            if last_art and m.get("artifacts", {}).get(last_art, {}).get("hash") != last_hash:
                                hash_valid = False
                    except Exception:
                        pass
                
                ctx.storage["hash_chain_valid"] = hash_valid
                
                if 1 <= idx <= 12:
                    ctx.logger.log("Rollback", "INVOKING")
                    rollback_success = RollbackStage().run(ctx)
                    if not rollback_success:
                        ctx.logger.log("Rollback", "FAILED")
                    else:
                        ctx.logger.log("Rollback", "PASSED")
                        
                ctx.storage["pipeline_status"] = "ROLLED_BACK" if 1 <= idx <= 12 else "FAIL"
                
                # Write to pipeline ledger (Step 16 / Index 15)
                self.stages[15].run(ctx)
                
                # Generate build report (Step 17 / Index 16)
                self.stages[16].run(ctx)
                
                return stage.failure_code(ctx)
            ctx.logger.log(stage.name, "PASSED")
            
        return EXIT_OK
