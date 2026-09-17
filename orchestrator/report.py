from pathlib import Path
from .lock import locked_read, locked_write

def generate_build_report(root: Path, run_id: str, logger=None) -> bool:
    try:
        pipeline_ledger = root / "Logs" / "Pipeline_Ledger.json"
        artifact_ledger = root / "Artifacts" / "Artifact_Ledger.json"

        pl_data = {}
        if pipeline_ledger.exists():
            with locked_read(pipeline_ledger, default=[]) as ledger:
                if isinstance(ledger, list):
                    pl_data = next((e for e in ledger if isinstance(e, dict) and e.get("run_id") == run_id), {})
                
        al_data = []
        if artifact_ledger.exists():
            with locked_read(artifact_ledger, default=[]) as ledger:
                if isinstance(ledger, list):
                    al_data = [e for e in ledger if isinstance(e, dict) and e.get("run_id") == run_id]
            
        layers_executed = [e.get("artifact", "unknown") for e in al_data if isinstance(e, dict) and e.get("status") == "written"]
    
        hash_chain = []
        for e in al_data:
            if isinstance(e, dict) and e.get("status") == "written":
                hash_chain.append(f"  - {e.get('artifact', 'unknown')}: {e.get('hash', 'unknown')}")
        hash_chain_str = "\n".join(hash_chain) if hash_chain else "  None"
    
        compiler_ver = pl_data.get('compiler_version', 'v2.1') if isinstance(pl_data, dict) else 'v2.1'
        duration = pl_data.get('duration_seconds', 0.0) if isinstance(pl_data, dict) else 0.0
        files_prod = pl_data.get('files_produced', 0) if isinstance(pl_data, dict) else 0
        warnings_list = pl_data.get('warnings', []) if isinstance(pl_data, dict) else []
        errors_list = pl_data.get('errors', []) if isinstance(pl_data, dict) else []
        integrity = 'PASS' if isinstance(pl_data, dict) and pl_data.get('hash_chain_valid') else 'FAIL'
        fidelity = "N/A (Failed)" if (not isinstance(pl_data, dict) or pl_data.get("status") != "PASS") else "100%"
        
        # Stage 7: Post-run lint
        from .lint import post_run_lint
        archive_dir = root / "Archives" / f"Run_{run_id}"
        lint_violations = []
        if archive_dir.exists():
            lint_violations = post_run_lint(archive_dir)
            
        lint_section = "None"
        if lint_violations:
            lint_section = "\n" + "\n".join(f"  - {v}" for v in lint_violations)
            integrity = 'FAIL'
    
        report_content = f"""# BUILD REPORT: {run_id}

1. **pipeline_version**: v2.1
2. **compiler_version**: {compiler_ver}
3. **layers_executed**: {', '.join(layers_executed) if layers_executed else 'None'}
4. **duration**: {duration}s
5. **artifacts_produced**: {files_prod}
6. **warnings**: {', '.join(warnings_list) if warnings_list else 'None'}
7. **errors**: {', '.join(errors_list) if errors_list else 'None'}
8. **hash_chain**:
{hash_chain_str}
9. **compiler_integrity**: {integrity}
10. **production_ready (fidelity %)**: {fidelity}
11. **aggregate_lint_violations**: {lint_section}
"""

        from .orchestrator import mask_secrets
        report_content = mask_secrets(report_content)

        report_dir = root / "Reports" / f"Run_{run_id}"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "BUILD_REPORT.md"
        with locked_write(report_path, report_content):
            pass
        root_report = root / "BUILD_REPORT.md"
        with locked_write(root_report, report_content):
            pass
        return True
    except (TimeoutError, OSError) as exc:
        if logger is not None:
            logger.log("BuildReport", "ERROR", f"Shared-file access failed: {exc}")
        return False
    except Exception as exc:
        if logger is not None:
            logger.log("BuildReport", "ERROR", f"Build report failed: {exc}")
        return False

