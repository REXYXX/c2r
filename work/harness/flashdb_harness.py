#!/usr/bin/env python3
"""Agent-style harness for the FlashDB C-to-Rust migration task.

The harness is intentionally deterministic and non-interactive so the judge can
run it as a normal command.  Each phase is represented as a small agent that
accepts a shared context, writes trace artifacts, and passes control to the next
phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

from convert_flashdb import (  # noqa: E402
    DEFAULT_FLASHDB,
    generate_cargo,
    generate_kvdb,
    generate_lib,
    generate_report,
    generate_tests,
    generate_tsdb,
    list_relative,
    write,
)


CONSTRAINT_FILES = [
    "work/specs/flashdb_api_contract.md",
    "work/specs/rust_design_rules.md",
    "work/workflows/opencode_glm_flashdb_workflow.md",
    "work/prompts/opencode_glm_system_prompt.md",
]

REQUIRED_OUTPUT_FILES = [
    "Cargo.toml",
    "src/lib.rs",
    "src/kvdb.rs",
    "src/tsdb.rs",
    "tests/kvdb_tests.rs",
    "tests/tsdb_tests.rs",
]

API_SYMBOLS = {
    "src/lib.rs": [
        "pub mod kvdb;",
        "pub mod tsdb;",
        "pub use kvdb::{KvDb, KvError};",
        "pub use tsdb::{TimeSeriesDb, TimeSeriesRecord, TsError};",
    ],
    "src/kvdb.rs": [
        "pub enum KvError",
        "pub struct KvDb",
        "pub fn new() -> Self",
        "pub fn open(path: impl AsRef<Path>) -> Result<Self, KvError>",
        "pub fn set(&mut self, key: impl Into<String>, value: impl AsRef<[u8]>) -> Result<(), KvError>",
        "pub fn set_str(&mut self, key: impl Into<String>, value: impl AsRef<str>) -> Result<(), KvError>",
        "pub fn get(&self, key: &str) -> Option<&[u8]>",
        "pub fn get_string(&self, key: &str) -> Option<String>",
        "pub fn contains_key(&self, key: &str) -> bool",
        "pub fn delete(&mut self, key: &str) -> bool",
        "pub fn clear(&mut self)",
        "pub fn len(&self) -> usize",
        "pub fn is_empty(&self) -> bool",
        "pub fn keys(&self) -> impl Iterator<Item = &str>",
        "pub fn sync(&self) -> Result<(), KvError>",
    ],
    "src/tsdb.rs": [
        "pub enum TsError",
        "pub struct TimeSeriesRecord",
        "pub timestamp: i64",
        "pub payload: Vec<u8>",
        "pub struct TimeSeriesDb",
        "pub fn new() -> Self",
        "pub fn open(path: impl AsRef<Path>) -> Result<Self, TsError>",
        "pub fn append(&mut self, timestamp: i64, payload: impl AsRef<[u8]>)",
        "pub fn len(&self) -> usize",
        "pub fn is_empty(&self) -> bool",
        "pub fn iter(&self) -> impl Iterator<Item = &TimeSeriesRecord>",
        "pub fn query(&self, from: i64, to: i64) -> Vec<TimeSeriesRecord>",
        "pub fn latest(&self) -> Option<&TimeSeriesRecord>",
        "pub fn sync(&self) -> Result<(), TsError>",
    ],
}


@dataclass
class HarnessContext:
    root: Path
    flashdb: Path
    out: Path
    result: Path
    cargo: str = "cargo"
    skip_cargo: bool = False
    analysis: dict[str, Any] = field(default_factory=dict)
    context_index: dict[str, Any] = field(default_factory=dict)
    compile_result: dict[str, Any] = field(default_factory=dict)
    validation_result: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def artifact(self, relative: str) -> Path:
        return self.result / "harness" / relative

    def log(self, agent: str, status: str, **data: Any) -> None:
        self.events.append(
            {
                "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "agent": agent,
                "status": status,
                **data,
            }
        )


class Agent:
    name = "Agent"

    def run(self, ctx: HarnessContext) -> None:
        raise NotImplementedError

    def __call__(self, ctx: HarnessContext) -> None:
        ctx.log(self.name, "started")
        self.run(ctx)
        ctx.log(self.name, "completed")


class ConstraintLoadingAgent(Agent):
    name = "ConstraintLoadingAgent"

    def run(self, ctx: HarnessContext) -> None:
        constraints = []
        for relative in CONSTRAINT_FILES:
            path = ctx.root / relative
            item: dict[str, Any] = {
                "path": relative,
                "exists": path.exists(),
            }
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
                item.update(
                    {
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "bytes": len(text.encode("utf-8")),
                        "lines": text.count("\n") + 1 if text else 0,
                    }
                )
            constraints.append(item)
        ctx.constraints = constraints
        write(ctx.artifact("00-constraints.json"), json.dumps({"constraints": constraints}, indent=2, ensure_ascii=False))
        write(
            ctx.artifact("00-constraints.md"),
            """
            # Constraint Loading

            The harness loaded the fixed weak-model guardrails before source analysis.

            Required documents:

            - `work/specs/flashdb_api_contract.md`
            - `work/specs/rust_design_rules.md`
            - `work/workflows/opencode_glm_flashdb_workflow.md`
            - `work/prompts/opencode_glm_system_prompt.md`

            These files constrain public API shape, Rust design choices, workflow stages, validation, and the opencode+GLM prompt.
            """,
        )


class ProjectAnalysisAgent(Agent):
    name = "ProjectAnalysisAgent"

    def run(self, ctx: HarnessContext) -> None:
        src_files = list_relative(ctx.flashdb, "src")
        test_files = list_relative(ctx.flashdb, "tests")
        include_files = list_relative(ctx.flashdb, "inc") + list_relative(ctx.flashdb, "include")
        components = {
            "kvdb": [name for name in src_files + include_files + test_files if "kv" in name.lower()],
            "tsdb": [name for name in src_files + include_files + test_files if "ts" in name.lower()],
            "port": [name for name in src_files + include_files if "port" in name.lower()],
        }
        ctx.analysis = {
            "flashdb_path": str(ctx.flashdb),
            "flashdb_exists": ctx.flashdb.exists(),
            "src_files": src_files,
            "test_files": test_files,
            "include_files": include_files,
            "components": components,
        }
        write(ctx.artifact("01-analysis.json"), json.dumps(ctx.analysis, indent=2, ensure_ascii=False))
        write(
            ctx.artifact("01-dependency-map.md"),
            f"""
            # Project Analysis

            Source path: `{ctx.flashdb}`

            - Source files: {len(src_files)}
            - Test files: {len(test_files)}
            - Header/include files: {len(include_files)}

            ## Component buckets

            - KVDB-related files: {len(components["kvdb"])}
            - TSDB-related files: {len(components["tsdb"])}
            - Port/platform files: {len(components["port"])}
            """,
        )


class SkeletonGenerationAgent(Agent):
    name = "SkeletonGenerationAgent"

    def run(self, ctx: HarnessContext) -> None:
        generate_cargo(ctx.out)
        generate_lib(ctx.out)
        write(
            ctx.artifact("02-skeleton.md"),
            f"""
            # Skeleton Generation

            Generated Rust crate skeleton at `{ctx.out}`.

            - `Cargo.toml`
            - `src/lib.rs`
            - planned modules: `kvdb`, `tsdb`
            """,
        )


class ContextBuilderAgent(Agent):
    name = "ContextBuilderAgent"

    def run(self, ctx: HarnessContext) -> None:
        function_hints = self._collect_function_hints(ctx.flashdb)
        ctx.context_index = {
            "module_contexts": {
                "kvdb": {
                    "source_hints": ctx.analysis.get("components", {}).get("kvdb", []),
                    "target": "src/kvdb.rs",
                    "behaviours": ["set", "get", "update", "delete", "blob", "persistence"],
                },
                "tsdb": {
                    "source_hints": ctx.analysis.get("components", {}).get("tsdb", []),
                    "target": "src/tsdb.rs",
                    "behaviours": ["append", "ordered iteration", "range query", "latest", "persistence"],
                },
            },
            "function_hints": function_hints,
        }
        write(ctx.artifact("03-context.json"), json.dumps(ctx.context_index, indent=2, ensure_ascii=False))

    def _collect_function_hints(self, flashdb: Path) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        if not flashdb.exists():
            return hints
        for path in sorted((flashdb / "src").rglob("*.[ch]")) + sorted((flashdb / "tests").rglob("*.[ch]")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for token in ["fdb_kv_", "fdb_blob_", "fdb_tsdb_", "fdb_tsl_"]:
                if token in text:
                    hints.append({"file": str(path.relative_to(flashdb)).replace("\\", "/"), "symbol_prefix": token})
        return hints


class TranslationAgent(Agent):
    name = "TranslationAgent"

    def run(self, ctx: HarnessContext) -> None:
        generate_kvdb(ctx.out)
        generate_tsdb(ctx.out)
        generate_tests(ctx.out)
        write(
            ctx.artifact("04-translation.md"),
            f"""
            # Translation

            Emitted safe Rust implementations and migrated tests.

            - `src/kvdb.rs`: key-value and blob behaviours.
            - `src/tsdb.rs`: time-series append/query behaviours.
            - `tests/kvdb_tests.rs`
            - `tests/tsdb_tests.rs`
            """,
        )


class CompileAgent(Agent):
    name = "CompileAgent"

    def run(self, ctx: HarnessContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        if ctx.skip_cargo or cargo_path is None:
            ctx.compile_result = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            ctx.compile_result = self._run_cargo(ctx, ["check"])
        write(ctx.artifact("05-compile.json"), json.dumps(ctx.compile_result, indent=2, ensure_ascii=False))

    def _run_cargo(self, ctx: HarnessContext, args: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [ctx.cargo, *args],
            cwd=ctx.out,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return {
            "status": "passed" if proc.returncode == 0 else "failed",
            "command": [ctx.cargo, *args],
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-8000:],
        }


class RepairAgent(Agent):
    name = "RepairAgent"

    def run(self, ctx: HarnessContext) -> None:
        status = ctx.compile_result.get("status")
        repair = {
            "status": "not_needed" if status in {"passed", "skipped"} else "manual_review_required",
            "compile_status": status,
            "notes": [],
        }
        if status == "failed":
            repair["notes"].append("The deterministic generator produced code that cargo check rejected.")
            repair["notes"].append("Inspect result/harness/05-compile.json for compiler diagnostics.")
        write(ctx.artifact("06-repair.json"), json.dumps(repair, indent=2, ensure_ascii=False))


class ValidationAgent(Agent):
    name = "ValidationAgent"

    def run(self, ctx: HarnessContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        checks = {
            "constraint_docs": {item["path"]: item["exists"] for item in ctx.constraints},
            "required_files": self._check_required_files(ctx.out),
            "api_symbols": self._check_api_symbols(ctx.out),
            "unsafe_occurrences": self._count_unsafe(ctx.out),
        }
        if ctx.skip_cargo or cargo_path is None:
            cargo_test = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            cargo_test = CompileAgent()._run_cargo(ctx, ["test"])
        ctx.validation_result = {
            "status": self._validation_status(checks, cargo_test),
            "failures": self._validation_failures(checks, cargo_test),
            "checks": checks,
            "cargo_test": cargo_test,
        }
        write(ctx.artifact("07-validation.json"), json.dumps(ctx.validation_result, indent=2, ensure_ascii=False))
        generate_report(ctx.root, ctx.flashdb, ctx.out, ctx.result)
        self._append_harness_report(ctx)

    def _count_unsafe(self, out: Path) -> int:
        count = 0
        for path in out.rglob("*.rs"):
            try:
                count += path.read_text(encoding="utf-8", errors="ignore").count("unsafe")
            except OSError:
                pass
        return count

    def _check_required_files(self, out: Path) -> dict[str, bool]:
        return {relative: (out / relative).is_file() for relative in REQUIRED_OUTPUT_FILES}

    def _check_api_symbols(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for relative, symbols in API_SYMBOLS.items():
            path = out / relative
            text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            missing = [symbol for symbol in symbols if symbol not in text]
            result[relative] = {
                "ok": not missing,
                "missing": missing,
            }
        return result

    def _validation_failures(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for path, exists in checks["constraint_docs"].items():
            if not exists:
                failures.append(f"missing constraint document: {path}")
        for path, exists in checks["required_files"].items():
            if not exists:
                failures.append(f"missing generated file: {path}")
        for path, result in checks["api_symbols"].items():
            if not result["ok"]:
                failures.append(f"missing API symbols in {path}: {', '.join(result['missing'])}")
        if checks["unsafe_occurrences"] != 0:
            failures.append(f"unsafe occurrences must be 0, got {checks['unsafe_occurrences']}")
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        return failures

    def _validation_status(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> str:
        return "failed" if self._validation_failures(checks, cargo_test) else "passed"

    def _append_harness_report(self, ctx: HarnessContext) -> None:
        report = ctx.result / "output.md"
        existing = report.read_text(encoding="utf-8") if report.exists() else ""
        appendix = f"""

        ## Agent harness execution

        Harness artifacts are available under `{ctx.result / "harness"}`.

        - ConstraintLoadingAgent: weak-model API, Rust design, workflow, and prompt guardrails.
        - ProjectAnalysisAgent: source inventory and component buckets.
        - SkeletonGenerationAgent: Cargo crate layout.
        - ContextBuilderAgent: minimum module/function context.
        - TranslationAgent: Rust module and test generation.
        - CompileAgent: `cargo check` diagnostics when cargo is available.
        - RepairAgent: compile-result triage.
        - ValidationAgent: structural checks and `cargo test` when cargo is available.
        """
        write(report, existing + text_block(appendix))


def text_block(value: str) -> str:
    import textwrap

    return textwrap.dedent(value).lstrip()


def run_harness(ctx: HarnessContext) -> None:
    agents: list[Agent] = [
        ConstraintLoadingAgent(),
        ProjectAnalysisAgent(),
        SkeletonGenerationAgent(),
        ContextBuilderAgent(),
        TranslationAgent(),
        CompileAgent(),
        RepairAgent(),
        ValidationAgent(),
    ]
    for agent in agents:
        agent(ctx)
    write(ctx.artifact("00-events.json"), json.dumps(ctx.events, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FlashDB C-to-Rust agent harness.")
    parser.add_argument("--flashdb", default=str(DEFAULT_FLASHDB), help="Path to platform FlashDB source tree")
    parser.add_argument("--out", default="flashDB_rust", help="Output Rust project directory")
    parser.add_argument("--result", default="result", help="Result/report directory")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument("--skip-cargo", action="store_true", help="Skip cargo check/test even if cargo exists")
    args = parser.parse_args()

    root = Path.cwd()
    out = Path(args.out)
    result = Path(args.result)
    ctx = HarnessContext(
        root=root,
        flashdb=Path(args.flashdb),
        out=out if out.is_absolute() else root / out,
        result=result if result.is_absolute() else root / result,
        cargo=args.cargo,
        skip_cargo=args.skip_cargo,
    )
    run_harness(ctx)
    print(f"generated Rust project: {ctx.out}")
    print(f"harness artifacts: {ctx.result / 'harness'}")
    print(f"validation: {ctx.result / 'harness' / '07-validation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
