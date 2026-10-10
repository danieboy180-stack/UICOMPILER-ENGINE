import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "ingest.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )

def write_inbox(root: Path, filename: str, value: str) -> None:
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(value, encoding="utf-8")

def test_split_ingest_missing_layer2() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"schema_version": "2.1", "project_id": "test_proj"}
</LAYER1_OUTPUT>
<MANIFEST_UPDATE>
{}
</MANIFEST_UPDATE>
<REPORT>
</REPORT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        print(f"Code: {result.returncode}, OUT: {result.stdout}, ERR: {result.stderr}")
        assert result.returncode == 10
        assert not (root / "Artifacts").exists()

def test_split_ingest_valid() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"schema_version": "2.1", "project_id": "test_proj", "l1_data": 1}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{"l2_data": 2}
</LAYER2_OUTPUT>
<MANIFEST_UPDATE>
{"manifest": "info"}
</MANIFEST_UPDATE>
<REPORT>
test report
</REPORT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        assert result.returncode == 0, result.stderr

        # Verify it was moved
        assert not (root / "Python_Inbox" / "layer12_bundle.txt").exists()
        assert (root / "Artifacts" / "layer1_ccir.json").exists()
        assert (root / "Artifacts" / "layer2_csir.json").exists()

        from verification.receipt import record_verification
        from verification.receipt import ingest_evidence
        record_verification("test_split_ingest", "Valid split bundle ingested", ingest_evidence(root, result), True)

        from orchestrator.lock import locked_read
        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            pass

        assert len(ledger) == 2
        artifacts = {e["artifact"] for e in ledger}
        assert "layer1_ccir" in artifacts
        assert "layer2_csir" in artifacts
        assert ledger[0]["hash"] != ledger[1]["hash"]

if __name__ == "__main__":
    for test in (test_split_ingest_missing_layer2, test_split_ingest_valid):
        test()
        print(f"PASS {test.__name__}")
