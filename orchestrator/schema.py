"""
orchestrator/schema.py — Shared schema version extraction and validation logic.

Fused-prompt specification:
- Real production fused prompts place schema_version in <MANIFEST_UPDATE> and <REPORT>,
  typically as "v2.1" (with a 'v' prefix).
- LAYER*_OUTPUT JSON bodies legitimately do not contain schema_version in real output.
- Legacy test fixtures and hand-crafted bundles may place schema_version directly
  in layer bodies, typically as "2.1" (without 'v').

This module provides a unified extraction and validation helper used by all ingest resolvers
(ingest.py, ingest_layer342.py) to prevent location and formatting mismatches.
"""

from typing import Any, Optional
import re

VALID_SCHEMA_VERSIONS = {"2.1", "v2.1", "2.1.0", "v2.1.0"}


def extract_schema_version(
    manifest_update: Optional[Any] = None,
    report: Optional[Any] = None,
    layer_body: Optional[Any] = None,
    raw_text: Optional[str] = None,
) -> Optional[str]:
    """
    Extract schema_version following the fused-prompt specification:
    1. Primary location: MANIFEST_UPDATE.schema_version
    2. Fallback location: REPORT.schema_version (dict or parsed text)
    3. Backward-compatibility fallback: layer_body.schema_version (for older test fixtures)
    4. Backward-compatibility fallback: raw_text regex search (for bundles with schema_version in nested tags)

    Returns stripped string value or None if not found.
    """
    # 1. Primary: MANIFEST_UPDATE
    if isinstance(manifest_update, dict):
        val = manifest_update.get("schema_version")
        if val is not None:
            return str(val).strip()

    # 2. Fallback: REPORT
    if isinstance(report, dict):
        val = report.get("schema_version")
        if val is not None:
            return str(val).strip()
    elif isinstance(report, str):
        # Extract from text lines or json-like snippets
        m = re.search(r'(?i)schema[_-]?version\s*[:=]\s*["\']?([^"\'\s,\n\r]+)', report)
        if m:
            return m.group(1).strip()

    # 3. Fallback: layer_body (legacy test fixtures or VERSIONING block)
    if isinstance(layer_body, dict):
        val = layer_body.get("schema_version")
        if val is not None:
            return str(val).strip()
        versioning = layer_body.get("VERSIONING") or layer_body.get("versioning")
        if isinstance(versioning, dict):
            val = versioning.get("schema_version")
            if val is not None:
                return str(val).strip()

    # 4. Fallback: raw_text search
    if isinstance(raw_text, str):
        m = re.search(r'(?i)["\']schema[_-]?version["\']\s*:\s*["\']([^"\']+)["\']', raw_text)
        if m:
            return m.group(1).strip()

    return None


def is_valid_schema_version(version: Optional[str]) -> bool:
    """
    Validate that schema_version is supported ("2.1", "v2.1", "2.1.0", "v2.1.0", etc.).
    Accepts both "v2.1" / "2.1.0" (production fused-prompt output) and "2.1" (canonical fixtures).
    Rejects missing, empty, or unsupported versions (e.g. "1.0", "3.0").
    """
    if not version:
        return False
    v = version.strip().lower()
    return v in VALID_SCHEMA_VERSIONS or v.lstrip("v").startswith("2.1")

