#!/usr/bin/env python3
"""Prepare model-facing artifacts for the FlashDB C-to-Rust migration.

This module intentionally does not contain Rust implementation strings.  The
conversion harness should guide the coding model with source inventory,
contracts, and validation gates; the model is responsible for writing the Rust
crate under `flashDB_rust/`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
import textwrap
from typing import Any


DEFAULT_FLASHDB = Path("/app/code/judge-assets/02_02_c_to_rust/code/FlashDB")


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


def generate_workspace_scaffold(out: Path, crate_name: str = "flashdb_rust") -> None:
    """Create only the model's work area and Cargo manifest.

    Existing Rust files are left untouched.  The manifest is useful context for
    the model, but `src/*.rs` and tests must be authored by the model.
    """
    (out / "src").mkdir(parents=True, exist_ok=True)
    (out / "tests").mkdir(parents=True, exist_ok=True)
    write_if_missing(
        out / "Cargo.toml",
        f"""
        [package]
        name = "{crate_name}"
        version = "0.1.0"
        edition = "2021"
        description = "Model-authored safe Rust rewrite of FlashDB"
        license = "MIT"

        [lib]
        name = "{crate_name}"
        path = "src/lib.rs"

        [dependencies]
        """,
    )


def generate_model_brief(
    root: Path,
    flashdb: Path,
    out: Path,
    result: Path,
    logs: Path,
    profile: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    context_index: dict[str, Any] | None = None,
) -> None:
    """Emit markdown instructions that guide the model to write Rust code."""
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

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- none"

    def json_block(value: Any) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    brief = "\n".join(
        [
            "# FlashDB Rust Model Task",
            "",
            "The Python harness does not generate the Rust implementation. Write the",
            f"Rust crate under `{out}` by reading the FlashDB C source and the constraint",
            "documents listed here.",
            "",
            "## Source And Output",
            "",
            f"- FlashDB C source: `{flashdb}`",
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
            "## One-To-One Storage-Engine Checks",
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
            "## README_test And Benchmark Coverage",
            "",
            "Translate every unit test and benchmark item described by FlashDB's README_test.md.",
            "The benchmark cases should validate operation semantics and sane measurement fields;",
            "they should not depend on fixed wall-clock performance thresholds.",
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
            "- Preserve FlashDB module boundaries from the source-to-Rust mapping.",
            "- Use safe Rust only and avoid C FFI.",
            "- Translate every source `TEST_RUN(...)` entry into Rust tests.",
            "- Run `cargo check` and `cargo test` once the crate is authored.",
            "- Treat validation failures as generation guidance, not as reasons to weaken the profile checks.",
            "",
        ]
    )
    write(out / "MODEL_TASK.md", brief)
    write(result / "harness" / "04-model-generation-brief.md", brief)


def generate_report(
    root: Path,
    flashdb: Path,
    out: Path,
    result: Path | None = None,
    logs: Path | None = None,
    validation: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
) -> None:
    result = result or root / "result"
    logs = logs or root / "logs"
    validation = validation or {}
    analysis = analysis or {}
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = "found" if flashdb.exists() else "not found in this environment"
    failures = validation.get("failures", [])
    cargo_test = validation.get("cargo_test", {"status": "not_run"})

    def bullet_list(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- none"

    write(
        result / "output.md",
        f"""
        # FlashDB Rust Conversion Harness Report

        Generated at: {now}

        ## Inputs

        - FlashDB source: `{flashdb}` ({status})
        - Rust output project: `{out}`
        - Result directory: `{result}`
        - Logs directory: `{logs}`

        ## Harness Role

        Python prepares the work area, source inventory, model task brief, and
        validation artifacts.  It does not contain or emit a hard-coded FlashDB
        Rust implementation.  The model must author the Rust source files under
        `{out}`.

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
        profile and model task brief.  Python must not be used as a container
        for prewritten Rust implementation strings.
        """,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare FlashDB model-generation artifacts.")
    parser.add_argument("--flashdb", default=str(DEFAULT_FLASHDB), help="Path to platform FlashDB source tree")
    parser.add_argument("--out", default="flashDB_rust", help="Output Rust project directory")
    parser.add_argument("--result", default="result", help="Result/report directory")
    parser.add_argument("--logs", default="logs", help="Logs directory")
    args = parser.parse_args()

    root = Path.cwd()
    flashdb = Path(args.flashdb)
    out = Path(args.out)
    result = Path(args.result)
    logs = Path(args.logs)
    out = out if out.is_absolute() else root / out
    result = result if result.is_absolute() else root / result
    logs = logs if logs.is_absolute() else root / logs

    generate_workspace_scaffold(out)
    generate_report(root, flashdb, out, result, logs)
    print(f"prepared Rust work area: {out}")
    print(f"source FlashDB path: {flashdb} ({'found' if flashdb.exists() else 'not found'})")
    print(f"result report: {result / 'output.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
