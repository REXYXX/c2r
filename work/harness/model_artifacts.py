#!/usr/bin/env python3
"""Model-facing artifact helpers for profile-driven C-to-Rust conversions.

This module prepares directories, Cargo metadata, model briefs, and reports. It
must stay project-neutral: domain rules, expected Rust files, API tokens, and
test requirements are read from the markdown profile.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import textwrap
from typing import Any


def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(data).lstrip(), encoding="utf-8", newline="\n")


def write_if_missing(path: Path, data: str) -> None:
    if not path.exists():
        write(path, data)


def list_relative(root: Path, subdir: str) -> list[str]:
    base = root / subdir
    if not base.exists():
        return []
    return sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in base.rglob("*") if p.is_file())


def profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("profile") or profile.get("name") or "project")


def display_name(profile: dict[str, Any]) -> str:
    return str(profile.get("display_name") or profile_name(profile))


def artifact_config(profile: dict[str, Any]) -> dict[str, Any]:
    value = profile.get("artifact", {})
    return value if isinstance(value, dict) else {}


def crate_name(profile: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override
    artifact = artifact_config(profile)
    return str(artifact.get("crate_name") or f"{profile_name(profile)}_rust")


def output_dir_name(profile: dict[str, Any]) -> str:
    artifact = artifact_config(profile)
    return str(artifact.get("output_dir") or f"{profile_name(profile)}_rust")


def source_label(profile: dict[str, Any]) -> str:
    artifact = artifact_config(profile)
    return str(artifact.get("source_label") or "C source")


def generate_workspace_scaffold(out: Path, profile: dict[str, Any] | None = None, crate: str | None = None) -> None:
    """Create only the model work area and Cargo manifest.

    Existing Rust files are left untouched. The manifest gives the model a valid
    crate shell; all Rust implementation and test files must be authored by the
    coding model from source context and profile constraints.
    """
    profile = profile or {}
    resolved_crate = crate_name(profile, crate)
    (out / "src").mkdir(parents=True, exist_ok=True)
    (out / "tests").mkdir(parents=True, exist_ok=True)
    write_if_missing(
        out / "Cargo.toml",
        f"""
        [package]
        name = "{resolved_crate}"
        version = "0.1.0"
        edition = "2021"
        description = "Model-authored safe Rust rewrite of {display_name(profile)}"
        license = "MIT"

        [lib]
        name = "{resolved_crate}"
        path = "src/lib.rs"

        [dependencies]
        """,
    )


def generate_model_brief(
    root: Path,
    source: Path,
    out: Path,
    result: Path,
    logs: Path,
    profile: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    context_index: dict[str, Any] | None = None,
) -> None:
    """Emit markdown instructions that guide the model to write Rust code."""
    del root
    analysis = analysis or {}
    context_index = context_index or {}
    required_files = profile.get("required_output_files", [])
    constraint_files = profile.get("constraint_files", [])
    one_to_one = profile.get("one_to_one_features", {})
    rejection = profile.get("behaviour_model_rejection", {})
    c_api = profile.get("c_api_parity_symbols", {})
    source_to_rust = profile.get("source_to_rust_modules", {})
    readme_coverage = profile.get("readme_test_coverage", {})
    source_runs = analysis.get("source_test_runs", {})
    project = display_name(profile)
    artifact = artifact_config(profile)
    task_title = artifact.get("task_title") or f"{project} Rust Model Task"

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- none"

    def json_block(value: Any) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    brief = "\n".join(
        [
            f"# {task_title}",
            "",
            "The Python harness does not generate the Rust implementation. Write the",
            f"Rust crate under `{out}` by reading the source project and the constraint",
            "documents listed here.",
            "",
            "## Source And Output",
            "",
            f"- {source_label(profile)}: `{source}`",
            f"- Rust crate output: `{out}`",
            f"- Result artifacts: `{result}`",
            f"- Logs: `{logs}`",
            "",
            "## Required Constraint Documents",
            "",
            bullets(constraint_files),
            "",
            "## Required Rust Files",
            "",
            bullets(required_files),
            "",
            "## Source To Rust Module Mapping",
            "",
            "```json",
            json_block(source_to_rust),
            "```",
            "",
            "## Public C API Parity Tokens",
            "",
            "```json",
            json_block(c_api),
            "```",
            "",
            "## One-To-One Logic Checks",
            "",
            "```json",
            json_block(one_to_one),
            "```",
            "",
            "## Behaviour Shortcut Rejection Rules",
            "",
            "```json",
            json_block(rejection),
            "```",
            "",
            "## Source Test Runs To Translate",
            "",
            "```json",
            json_block(source_runs),
            "```",
            "",
            "## README And Benchmark Coverage",
            "",
            "Translate every unit test and benchmark item declared by the profile.",
            "Benchmark cases should validate operation semantics and sane measurement",
            "fields; they should not depend on fixed wall-clock performance thresholds.",
            "",
            "```json",
            json_block(readme_coverage),
            "```",
            "",
            "## Context Index",
            "",
            "```json",
            json_block(context_index),
            "```",
            "",
            "## Work Rules",
            "",
            f"- Author Rust source files directly in `{out}`; do not add Rust source to Python.",
            "- Preserve module boundaries from the source-to-Rust mapping.",
            "- Use safe Rust only and avoid C FFI unless a profile explicitly permits it.",
            "- Translate every source test entry required by the profile into Rust tests.",
            "- Run `cargo check` and `cargo test` once the crate is authored.",
            "- Treat validation failures as generation guidance, not as reasons to weaken the profile checks.",
            "",
        ]
    )
    write(out / "MODEL_TASK.md", brief)
    write(result / "harness" / "04-model-generation-brief.md", brief)


def generate_report(
    root: Path,
    source: Path,
    out: Path,
    result: Path | None = None,
    logs: Path | None = None,
    validation: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> None:
    profile = profile or {}
    result = result or root / "result"
    logs = logs or root / "logs"
    validation = validation or {}
    analysis = analysis or {}
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = "found" if source.exists() else "not found in this environment"
    failures = validation.get("failures", [])
    cargo_test = validation.get("cargo_test", {"status": "not_run"})
    artifact = artifact_config(profile)
    report_title = artifact.get("report_title") or f"{display_name(profile)} Rust Conversion Harness Report"

    def bullet_list(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- none"

    write(
        result / "output.md",
        f"""
        # {report_title}

        Generated at: {now}

        ## Inputs

        - {source_label(profile)}: `{source}` ({status})
        - Rust output project: `{out}`
        - Result directory: `{result}`
        - Logs directory: `{logs}`

        ## Harness Role

        Python prepares the work area, source inventory, model task brief, and
        validation artifacts. It does not contain or emit a hard-coded Rust
        implementation. The model must author Rust source files under `{out}`.

        ## Source Inventory

        - Source files: {len(analysis.get("src_files", []))}
        - Test files: {len(analysis.get("test_files", []))}
        - Header/include files: {len(analysis.get("include_files", []))}

        ## Validation Result

        - Validation status: `{validation.get("status", "not_run")}`
        - Cargo test status: `{cargo_test.get("status", "not_run")}`

        ## Failures

        {bullet_list(failures)}

        ## Model Brief

        Read `{out / "MODEL_TASK.md"}` and
        `{result / "harness" / "04-model-generation-brief.md"}` before writing
        or repairing Rust code.
        """,
    )
    write(
        result / "issues" / "00-summary.md",
        f"""
        # Conversion summary

        - Overall status: `{validation.get("status", "not_run")}`
        - Cargo test status: `{cargo_test.get("status", "not_run")}`

        ## Failures

        {bullet_list(failures)}

        ## Required next step

        The model must write or repair the Rust crate in `{out}` using the
        profile and model task brief. Python must not be used as a container
        for prewritten Rust implementation strings.
        """,
    )
