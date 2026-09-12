import json
from pathlib import Path
from typing import Optional

def generate_build_report(root: Path, run_id: str) -> None:
    pipeline_ledger = root / "Logs" / "Pipeline_Ledger.json"
    artifact_ledger = root / "Artifacts" / "Artifact_Ledger.json"
    
    pl_data = {}
    if pipeline_ledger.exists():
        with open(pipeline_ledger, 'r', encoding='utf-8') as f:
            try:
                ledger = json.load(f)
                pl_data = next((e for e in ledger if e.get("run_id") == run_id), {})
            except json.JSONDecodeError:
                pass
                
    al_data = []
    if artifact_ledger.exists():
        with open(artifact_ledger, 'r', encoding='utf-8') as f:
            try:
                ledger = json.load(f)
                al_data = [e for e in ledger if e.get("run_id") == run_id]
            except json.JSONDecodeError:
                pass
            
    layers_executed = [e["artifact"] for e in al_data if e.get("status") == "written"]
    
    hash_chain = []
    for e in al_data:
        if e.get("status") == "written":
            hash_chain.append(f"  - {e['artifact']}: {e['hash']}")
    hash_chain_str = "\n".join(hash_chain) if hash_chain else "  None"
    
    fidelity = "N/A (Failed)" if pl_data.get("status") != "PASS" else "100%"
    
    report_content = f"""# BUILD REPORT: {run_id}

1. **pipeline_version**: v2.1
2. **compiler_version**: {pl_data.get('compiler_version', 'v2.1')}
3. **layers_executed**: {', '.join(layers_executed) if layers_executed else 'None'}
4. **duration**: {pl_data.get('duration_seconds', 0.0)}s
5. **artifacts_produced**: {pl_data.get('files_produced', 0)}
6. **warnings**: {', '.join(pl_data.get('warnings', [])) or 'None'}
7. **errors**: {', '.join(pl_data.get('errors', [])) or 'None'}
8. **hash_chain**:
{hash_chain_str}
9. **compiler_integrity**: {'PASS' if pl_data.get('hash_chain_valid') else 'FAIL'}
10. **production_ready (fidelity %)**: {fidelity}
"""

    from .orchestrator import mask_secrets
    report_content = mask_secrets(report_content)

    report_dir = root / "Reports" / f"Run_{run_id}"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "BUILD_REPORT.md"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_content)
        
    # Also write to root so Archive could theoretically pick it up (though sequence implies otherwise)
    root_report = root / "BUILD_REPORT.md"
    with open(root_report, 'w', encoding='utf-8') as f:
        f.write(report_content)

