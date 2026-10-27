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
    if isinstance(value, str):
        (inbox / filename).write_text(value, encoding="utf-8")
    else:
        (inbox / filename).write_text(json.dumps(value), encoding="utf-8")


def test_malformed_bundle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"layer": 12}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{}
</LAYER2_OUTPUT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        assert result.returncode == 10
        assert not (root / "Artifacts").exists()


def test_valid_bundle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"schema_version": "2.1", "project_id": "test_proj", "layer": 12, "payload": {"x": 1}}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{}
</LAYER2_OUTPUT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        print(result.stdout)
        assert result.returncode == 0, result.stderr

        # Verify it was moved
        assert not (root / "Python_Inbox" / "layer12_bundle.txt").exists()
        assert (root / "Artifacts" / "layer1_ccir.json").exists()

        from verification.receipt import record_verification
        from verification.receipt import ingest_evidence
        record_verification("test_ingest", "Valid bundle ingested", ingest_evidence(root, result), True)
        from orchestrator.lock import locked_read
        with locked_read(root / "Artifacts" / "layer1_ccir.json") as artifact:
            pass
        manifest_files = list((root / "Manifest").glob("manifest_*.json"))
        with locked_read(manifest_files[0]) as manifest:
            pass
        with locked_read(root / "Artifacts" / "Artifact_Ledger.json") as ledger:
            pass

        assert artifact["payload"] == {"x": 1}
        assert ledger[0]["artifact"] == "layer1_ccir"
        assert manifest["artifacts"]["layer1_ccir"]["hash"] == ledger[0]["hash"] == __import__("hashlib").sha256(
            json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert manifest["next_layer"] == 13
        assert list((root / "Python_Output").rglob("layer12_bundle.txt"))


def test_disallowed_filename_is_ignored() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_inbox(root, "unexpected.json", {"layer": 12, "payload": {}})
        result = run(root)
        assert result.returncode == 0
        assert (root / "Python_Inbox" / "unexpected.json").exists()
        assert not (root / "Artifacts").exists()


def test_real_fused_bundle_v2_1_in_manifest_update() -> None:
    """Real fused prompt output places schema_version in MANIFEST_UPDATE/REPORT, not layer bodies."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"project_id": "test_proj", "layer": 12, "candidates": [{"id": "c1", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}}]}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{"semantics": [{"id": "c1", "role": "button"}]}
</LAYER2_OUTPUT>
<MANIFEST_UPDATE>
{"schema_version": "v2.1", "pipeline_id": "pipe_v21"}
</MANIFEST_UPDATE>
<REPORT>
{"schema_version": "v2.1", "status": "pass"}
</REPORT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        assert result.returncode == 0, f"Failed with {result.stderr}, stdout: {result.stdout}"
        assert (root / "Artifacts" / "layer1_ccir.json").exists()
        assert (root / "Artifacts" / "layer2_csir.json").exists()


def test_invalid_schema_version_rejected() -> None:
    """Unsupported schema_version (e.g. 1.0) must be rejected with exit code 10 and not unlinked."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        content = """<LAYER1_OUTPUT>
{"project_id": "test_proj"}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{}
</LAYER2_OUTPUT>
<MANIFEST_UPDATE>
{"schema_version": "1.0"}
</MANIFEST_UPDATE>
<REPORT>
{"schema_version": "1.0"}
</REPORT>"""
        write_inbox(root, "layer12_bundle.txt", content)
        result = run(root)
        assert result.returncode == 10
        assert "Unsupported schema version '1.0'" in result.stdout
        # Inbox file must be preserved for inspection (fail-closed, not unlinked)
        assert (root / "Python_Inbox" / "layer12_bundle.txt").exists()


if __name__ == "__main__":
    for test in (
        test_malformed_bundle,
        test_valid_bundle,
        test_disallowed_filename_is_ignored,
        test_real_fused_bundle_v2_1_in_manifest_update,
        test_invalid_schema_version_rejected,
    ):
        test()
        print(f"PASS {test.__name__}")