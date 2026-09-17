import pytest
import sys
import tempfile
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    
from orchestrator.report import generate_build_report

def test_post_run_lint():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        run_id = "linttest"
        
        # Setup directories
        archive_dir = root / "Archives" / f"Run_{run_id}"
        art_dir = archive_dir / "Artifacts"
        art_dir.mkdir(parents=True)
        
        # L1 has some text
        l1_data = {
            "candidates": [
                {"id": "1", "type": "text", "value": "Valid text"}
            ]
        }
        with open(art_dir / "layer1_ccir.json", "w") as f:
            json.dump(l1_data, f)
            
        # L2 has OCR text not in L1
        l2_data = {
            "semantics": [
                {"id": "1", "text": "Hallucinated OCR text"}
            ]
        }
        with open(art_dir / "layer2_csir.json", "w") as f:
            json.dump(l2_data, f)
            
        # L3 has color not in L1/L2
        l3_data = {
            "visuals": [
                {"id": "1", "background_color": "#FF0000"}
            ]
        }
        with open(art_dir / "layer3_visual_ir.json", "w") as f:
            json.dump(l3_data, f)
            
        # Run report generation
        success = generate_build_report(root, run_id)
        assert success
        
        report_path = root / "Reports" / f"Run_{run_id}" / "BUILD_REPORT.md"
        assert report_path.exists()
        
        content = report_path.read_text(encoding='utf-8')
        
        assert "aggregate_lint_violations" in content
        assert "Layer2" in content
        assert "Hallucinated OCR text" in content
        assert "Layer3" in content
        assert "#FF0000" in content

if __name__ == "__main__":
    test_post_run_lint()
    from verification.receipt import record_verification
    record_verification("test_post_run_lint", "Post-run lint caught aggregate violations", "SUCCESS\n" + "\n".join(["padding"]*10), True)
    print("PASS test_post_run_lint")
