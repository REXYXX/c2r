#!/usr/bin/env python3
"""Internal analysis-state cache for incremental validation runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from generic_harness import ConversionContext, write


CACHE_SCHEMA_VERSION = 1
CACHE_RELATIVE_PATH = ".cache/analysis-state.json"
IGNORED_SOURCE_PARTS = {".git", "target", "build", "dist", "out", "__pycache__"}


class AnalysisStateError(RuntimeError):
    """Raised when validate-only cannot safely reuse cached analysis."""


def source_fingerprint(source: Path) -> dict[str, Any]:
    """Build a fast change detector from relative path, size, and nanosecond mtime."""
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    if not source.is_dir():
        return {"sha256": digest.hexdigest(), "files": 0, "bytes": 0}
    for path in sorted((item for item in source.rglob("*") if item.is_file()), key=lambda item: str(item).lower()):
        relative = path.relative_to(source)
        if any(part.lower() in IGNORED_SOURCE_PARTS for part in relative.parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        normalized = str(relative).replace("\\", "/")
        digest.update(normalized.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        total_bytes += stat.st_size
    return {"sha256": digest.hexdigest(), "files": file_count, "bytes": total_bytes}


def write_analysis_state(ctx: ConversionContext, profile: dict[str, Any]) -> dict[str, Any]:
    fingerprint = source_fingerprint(ctx.source)
    cached_analysis = {
        key: value
        for key, value in ctx.analysis.items()
        if key not in {"derived_profile", "analysis_cache"}
    }
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "source": str(ctx.source.resolve()),
        "source_fingerprint": fingerprint,
        "profile": profile,
        "analysis": cached_analysis,
    }
    path = ctx.artifact(CACHE_RELATIVE_PATH)
    write(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return {
        "status": "written",
        "path": str(path),
        "source_fingerprint": fingerprint,
    }


def load_analysis_state(source: Path, result: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    path = result / "harness" / CACHE_RELATIVE_PATH
    if not path.is_file():
        raise AnalysisStateError(f"missing analysis cache: {path}; run bootstrap first")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisStateError(f"invalid analysis cache: {path}: {exc}") from exc
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise AnalysisStateError(f"unsupported analysis cache schema in {path}; rerun bootstrap")
    cached_source = Path(str(payload.get("source", ""))).resolve()
    if cached_source != source.resolve():
        raise AnalysisStateError(
            f"analysis cache belongs to {cached_source}, not {source.resolve()}; rerun bootstrap"
        )
    expected = payload.get("source_fingerprint", {})
    actual = source_fingerprint(source)
    if expected.get("sha256") != actual.get("sha256"):
        raise AnalysisStateError("C source changed after bootstrap; rerun bootstrap before validate-only")
    profile = payload.get("profile")
    analysis = payload.get("analysis")
    if not isinstance(profile, dict) or not isinstance(analysis, dict):
        raise AnalysisStateError(f"analysis cache is incomplete: {path}; rerun bootstrap")
    metadata = {
        "status": "hit",
        "path": str(path),
        "source_fingerprint": actual,
    }
    analysis["analysis_cache"] = metadata
    return profile, analysis, metadata
