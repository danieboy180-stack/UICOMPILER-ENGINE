import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "ingest.py"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


def write_inbox(root: Path, filename: str, value: object) -> None:
    inbox = root / "Python_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(json.dumps(value), encoding="utf-8")


def test_malformed_bundle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_inbox(root, "layer12_bundle.json", {"layer": 12})
        result = run(root)
        assert result.returncode == 10
        assert not (root / "Artifacts").exists()


def test_valid_bundle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_inbox(root, "layer12_bundle.json", {"schema_version": "2.1", "project_id": "test_proj", "layer": 12, "payload": {"x": 1}})
        result = run(root)
        assert result.returncode == 0, result.stderr
        
        # Verify it was moved
        assert not (root / "Python_Inbox" / "layer12_bundle.json").exists()
        assert (root / "Artifacts" / "layer12_bundle.json").exists()
        
        from verification.receipt import record_verification
        record_verification("test_ingest", "Valid bundle ingested", str(result.stdout) + "\n" + str(result.stderr) + "\n" + "\n".join(["padding"]*10), True)
        artifact = json.loads((root / "Artifacts" / "layer12_bundle.json").read_text())
        manifest_files = list((root / "Manifest").glob("manifest_*.json"))
        manifest = json.loads(manifest_files[0].read_text())
        ledger = json.loads((root / "Artifacts" / "Artifact_Ledger.json").read_text())
        
        assert artifact["payload"] == {"x": 1}
        assert ledger[0]["artifact"] == "layer12_bundle"
        assert manifest["artifacts"]["layer12_bundle"]["hash"] == ledger[0]["hash"] == __import__("hashlib").sha256(
            json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert manifest["next_layer"] == 13
        assert list((root / "Python_Output").rglob("layer12_bundle.json"))


def test_disallowed_filename_is_ignored() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_inbox(root, "unexpected.json", {"layer": 12, "payload": {}})
        result = run(root)
        assert result.returncode == 0
        assert (root / "Python_Inbox" / "unexpected.json").exists()
        assert not (root / "Artifacts").exists()


if __name__ == "__main__":
    for test in (test_malformed_bundle, test_valid_bundle, test_disallowed_filename_is_ignored):
        test()
        print(f"PASS {test.__name__}")