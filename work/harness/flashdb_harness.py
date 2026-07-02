#!/usr/bin/env python3
"""FlashDB profile harness for the reusable C-to-Rust conversion framework.

The generic harness owns deterministic orchestration, trace artifacts, command
execution, and shared validation helpers.  FlashDB-specific constraints live in
`work/profiles/flashdb.md`, while this module only wires those constraints to
the FlashDB source analyzer, generator, and validator.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
HARNESS = WORK / "harness"
for path in (WORK, HARNESS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from convert_flashdb import (  # noqa: E402
    DEFAULT_FLASHDB,
    generate_model_brief,
    generate_report,
    generate_workspace_scaffold,
    list_relative,
)
from generic_harness import (  # noqa: E402
    Agent,
    CompileAgent,
    ConstraintLoadingAgent,
    ConversionContext,
    OutputScaffoldAgent,
    RepairAgent,
    check_artifact_structure,
    check_required_files,
    check_token_map,
    count_token_in_rust,
    load_markdown_profile,
    read_text,
    run_agents,
    run_cargo,
    text_block,
    write,
)


PROFILE_PATH = WORK / "profiles" / "flashdb.md"
PROFILE = load_markdown_profile(PROFILE_PATH)
DUPLICATE_TEST_NAME_MAP = {
    (item["suite"], item["source"], item["occurrence"]): item["target"]
    for item in PROFILE.get("duplicate_test_name_map", [])
}


class HarnessContext(ConversionContext):
    @property
    def flashdb(self) -> Path:
        return self.source


class ProjectAnalysisAgent(Agent):
    name = "ProjectAnalysisAgent"

    def run(self, ctx: ConversionContext) -> None:
        layout = PROFILE["source_layout"]
        src_files = self._list_many(ctx.source, layout["source_dirs"])
        test_files = self._list_many(ctx.source, layout["test_dirs"])
        include_files = self._list_many(ctx.source, layout["include_dirs"])
        all_files = src_files + include_files + test_files
        components = {
            name: [path for path in all_files if any(token in path.lower() for token in tokens)]
            for name, tokens in PROFILE["component_filters"].items()
        }
        source_test_runs = self._collect_source_test_runs(ctx.source)
        public_apis = self._collect_public_apis(ctx.source / layout["public_api_header"])
        internal_anchors = self._collect_internal_anchors(ctx.source)
        ctx.analysis = {
            "flashdb_path": str(ctx.source),
            "flashdb_exists": ctx.source.exists(),
            "src_files": src_files,
            "test_files": test_files,
            "include_files": include_files,
            "components": components,
            "source_test_runs": source_test_runs,
            "public_apis": public_apis,
            "internal_parity_anchors": internal_anchors,
        }
        write(ctx.artifact("01-analysis.json"), json.dumps(ctx.analysis, indent=2, ensure_ascii=False))
        write(
            ctx.artifact("01-dependency-map.md"),
            f"""
            # Project Analysis

            Source path: `{ctx.source}`

            - Source files: {len(src_files)}
            - Test files: {len(test_files)}
            - Header/include files: {len(include_files)}

            ## Component buckets

            - KVDB-related files: {len(components["kvdb"])}
            - TSDB-related files: {len(components["tsdb"])}
            - Port/platform files: {len(components["port"])}
            - KVDB TEST_RUN entries: {len(source_test_runs["kvdb"])}
            - TSDB TEST_RUN entries: {len(source_test_runs["tsdb"])}
            - Public FlashDB API entries: {len(public_apis)}
            - Internal parity anchor groups: {len(internal_anchors)}
            """,
        )

    def _list_many(self, root: Path, subdirs: list[str]) -> list[str]:
        files: list[str] = []
        for subdir in subdirs:
            files.extend(list_relative(root, subdir))
        return files

    def _collect_source_test_runs(self, flashdb: Path) -> dict[str, list[str]]:
        return {
            name: self._test_runs_from_file(flashdb / spec["source"])
            for name, spec in PROFILE["test_suites"].items()
        }

    def _test_runs_from_file(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(PROFILE["source_layout"]["test_run_pattern"], text)

    def _collect_public_apis(self, header: Path) -> list[str]:
        if not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(PROFILE["source_layout"]["public_api_pattern"], text)))

    def _collect_internal_anchors(self, flashdb: Path) -> dict[str, dict[str, Any]]:
        anchors: dict[str, dict[str, Any]] = {}
        for filename, expected in PROFILE["internal_parity_anchors"].items():
            path = flashdb / "src" / filename
            text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            anchors[filename] = {
                "exists": path.exists(),
                "expected": expected,
                "present": [name for name in expected if name in text],
                "missing_in_source": [name for name in expected if name not in text],
            }
        return anchors


class SkeletonGenerationAgent(Agent):
    name = "SkeletonGenerationAgent"

    def run(self, ctx: ConversionContext) -> None:
        generate_workspace_scaffold(ctx.out)
        write(
            ctx.artifact("02-skeleton.md"),
            f"""
            # Skeleton Generation

            Prepared the Rust crate work area at `{ctx.out}`.

            Python creates only directories and Cargo manifest scaffolding.
            The model must author `src/*.rs` and `tests/*.rs` from the profile,
            specs, and source code.
            """,
        )


class ContextBuilderAgent(Agent):
    name = "ContextBuilderAgent"

    def run(self, ctx: ConversionContext) -> None:
        module_contexts: dict[str, Any] = {}
        for name, spec in PROFILE["module_contexts"].items():
            component_key = spec["component_key"]
            module_contexts[name] = {
                "source_hints": ctx.analysis.get("components", {}).get(component_key, []),
                "target": spec["target"],
                "required_mechanisms": spec["required_mechanisms"],
            }
        ctx.context_index = {
            "module_contexts": module_contexts,
            "function_hints": self._collect_function_hints(ctx.source),
            "public_apis": ctx.analysis.get("public_apis", []),
            "internal_parity_anchors": ctx.analysis.get("internal_parity_anchors", {}),
        }
        write(ctx.artifact("03-context.json"), json.dumps(ctx.context_index, indent=2, ensure_ascii=False))

    def _collect_function_hints(self, flashdb: Path) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        if not flashdb.exists():
            return hints
        layout = PROFILE["source_layout"]
        paths = []
        for subdir in layout["source_dirs"] + layout["test_dirs"]:
            paths.extend(sorted((flashdb / subdir).rglob("*.[ch]")))
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for token in PROFILE["function_hint_tokens"]:
                if token in text:
                    hints.append({"file": str(path.relative_to(flashdb)).replace("\\", "/"), "symbol_prefix": token})
        return hints


class ParityMatrixAgent(Agent):
    name = "ParityMatrixAgent"

    def run(self, ctx: ConversionContext) -> None:
        layout = PROFILE["source_layout"]
        public_apis = self._public_apis(ctx.source / layout["public_api_header"])
        parity = {
            "public_apis": public_apis,
            "expected_public_apis": PROFILE["c_api_parity_symbols"],
            "expected_one_to_one_features": PROFILE["one_to_one_features"],
            "source_to_rust_modules": PROFILE["source_to_rust_modules"],
            "notes": [
                "This matrix is supplied by the FlashDB profile, not by the generic harness.",
                "Validation fails when generated Rust lacks public C API parity names or required storage-engine features.",
                "Map/vector-only behaviour models are rejected even if high-level tests pass.",
            ],
        }
        write(ctx.artifact("04-function-parity.json"), json.dumps(parity, indent=2, ensure_ascii=False))

    def _public_apis(self, header: Path) -> list[str]:
        if not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(PROFILE["source_layout"]["public_api_pattern"], text)))


class TranslationAgent(Agent):
    name = "TranslationAgent"

    def run(self, ctx: ConversionContext) -> None:
        generate_model_brief(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, PROFILE, ctx.analysis, ctx.context_index)
        write(
            ctx.artifact("04-translation.md"),
            f"""
            # Model-Guided Translation

            The harness emitted model-facing generation guidance instead of
            writing Rust implementation code from Python.

            - Work item: `{ctx.out / "MODEL_TASK.md"}`
            - Harness brief: `{ctx.result / "harness" / "04-model-generation-brief.md"}`
            - Parity matrix: `{ctx.result / "harness" / "04-function-parity.json"}`

            The model must author or repair the Rust crate under `{ctx.out}`.
            Validation failures are kept as feedback for that generation loop.
            """,
        )


class ValidationAgent(Agent):
    name = "ValidationAgent"

    def run(self, ctx: ConversionContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        self._ensure_report_placeholders(ctx)
        checks = {
            "required_artifact_structure": check_artifact_structure(
                ctx,
                [("result_function_parity_json", ctx.result / "harness" / "04-function-parity.json")],
            ),
            "constraint_docs": {item["path"]: item["exists"] for item in ctx.constraints},
            "required_files": check_required_files(ctx.out, PROFILE["required_output_files"]),
            "api_symbols": check_token_map(ctx.out, PROFILE["api_symbols"]),
            "c_api_parity": self._check_c_api_parity(ctx.out),
            "one_to_one_features": self._check_one_to_one_features(ctx.out),
            "behaviour_model_rejection": self._check_behaviour_model_rejection(ctx.out),
            "translated_test_coverage": self._check_translated_test_coverage(ctx),
            "readme_test_coverage": self._check_readme_test_coverage(ctx.out),
            "unsafe_occurrences": count_token_in_rust(ctx.out, "unsafe"),
        }
        if ctx.skip_cargo or cargo_path is None:
            cargo_test = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            cargo_test = run_cargo(ctx, ["test"])
        ctx.validation_result = {
            "status": self._validation_status(checks, cargo_test),
            "failures": self._validation_failures(checks, cargo_test),
            "checks": checks,
            "cargo_test": cargo_test,
        }
        write(ctx.artifact("07-validation.json"), json.dumps(ctx.validation_result, indent=2, ensure_ascii=False))
        generate_report(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, ctx.validation_result, ctx.analysis)
        self._append_harness_report(ctx)

    def _ensure_report_placeholders(self, ctx: ConversionContext) -> None:
        if not (ctx.result / "output.md").exists():
            write(ctx.result / "output.md", "# FlashDB Rust Conversion Execution Report\n\nPending validation.\n")
        if not (ctx.result / "issues" / "00-summary.md").exists():
            write(ctx.result / "issues" / "00-summary.md", "# Conversion summary\n\nPending validation.\n")

    def _check_c_api_parity(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for group, symbols in PROFILE["c_api_parity_symbols"].items():
            text = "\n".join(read_text(out / relative) for relative in PROFILE["c_api_parity_modules"][group])
            missing = [symbol for symbol in symbols if symbol not in text]
            result[group] = {
                "ok": not missing,
                "missing": missing,
            }
        return result

    def _check_one_to_one_features(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for relative, features in PROFILE["one_to_one_features"].items():
            text = read_text(out / "src" / relative)
            feature_result = {}
            for feature, tokens in features.items():
                missing = [token for token in tokens if token not in text]
                feature_result[feature] = {
                    "ok": not missing,
                    "missing": missing,
                }
            result[relative] = feature_result
        return result

    def _check_behaviour_model_rejection(self, out: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, rule in PROFILE["behaviour_model_rejection"].items():
            text = read_text(out / rule["file"])
            bad_hits = [token for token in rule["bad"] if token in text]
            missing_offsets = [token for token in rule["required_offsets"] if token not in text]
            result[name] = {
                "ok": not (bad_hits and missing_offsets),
                "bad_hits": bad_hits,
                "missing_required_offsets": missing_offsets,
            }
        return result

    def _check_translated_test_coverage(self, ctx: ConversionContext) -> dict[str, Any]:
        source_runs = ctx.analysis.get("source_test_runs", {"kvdb": [], "tsdb": []})
        expected = {
            suite: self._expected_rust_test_names(source_runs.get(suite, []), suite)
            for suite in PROFILE["test_suites"]
        }
        actual = {
            suite: self._rust_test_names(ctx.out / spec["target"])
            for suite, spec in PROFILE["test_suites"].items()
        }
        return {
            "source_runs": source_runs,
            "expected_rust_tests": expected,
            "actual_rust_tests": actual,
            "missing": {
                suite: [name for name in expected[suite] if name not in actual[suite]]
                for suite in PROFILE["test_suites"]
            },
        }

    def _check_readme_test_coverage(self, out: Path) -> dict[str, Any]:
        coverage = PROFILE.get("readme_test_coverage", {})
        required = coverage.get("required_rust_tests", {})
        result: dict[str, Any] = {
            "source": coverage.get("source"),
            "required_rust_tests": required,
            "actual_rust_tests": {},
            "missing": {},
        }
        for relative, expected in required.items():
            actual = self._rust_test_names(out / relative)
            result["actual_rust_tests"][relative] = actual
            result["missing"][relative] = [name for name in expected if name not in actual]
        return result

    def _expected_rust_test_names(self, source_runs: list[str], suite: str) -> list[str]:
        counts: dict[str, int] = {}
        expected = []
        for name in source_runs:
            counts[name] = counts.get(name, 0) + 1
            mapped = DUPLICATE_TEST_NAME_MAP.get((suite, name, counts[name]))
            expected.append(mapped or name)
        return expected

    def _rust_test_names(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(r"fn\s+(test_[A-Za-z0-9_]+)\s*\(", text)

    def _validation_failures(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for path, exists in checks["constraint_docs"].items():
            if not exists:
                failures.append(f"missing constraint document: {path}")
        for artifact, exists in checks["required_artifact_structure"].items():
            if not exists:
                failures.append(f"missing required artifact: {artifact}")
        for path, exists in checks["required_files"].items():
            if not exists:
                failures.append(f"missing generated file: {path}")
        for path, result in checks["api_symbols"].items():
            if not result["ok"]:
                failures.append(f"missing API symbols in {path}: {', '.join(result['missing'])}")
        for group, result in checks["c_api_parity"].items():
            if not result["ok"]:
                failures.append(f"missing C API parity symbols in {group}: {', '.join(result['missing'])}")
        for module, features in checks["one_to_one_features"].items():
            for feature, result in features.items():
                if not result["ok"]:
                    failures.append(f"missing one-to-one feature {module}:{feature}: {', '.join(result['missing'])}")
        for rule, result in checks["behaviour_model_rejection"].items():
            if not result["ok"]:
                failures.append(
                    f"behaviour-model shortcut detected ({rule}): bad tokens {', '.join(result['bad_hits'])}; "
                    f"missing offsets {', '.join(result['missing_required_offsets'])}"
                )
        coverage = checks["translated_test_coverage"]["missing"]
        for suite, missing in coverage.items():
            if missing:
                failures.append(f"missing translated {suite.upper()} tests: {', '.join(missing)}")
        readme_coverage = checks["readme_test_coverage"]["missing"]
        for path, missing in readme_coverage.items():
            if missing:
                failures.append(f"missing README_test coverage in {path}: {', '.join(missing)}")
        if checks["unsafe_occurrences"] != 0:
            failures.append(f"unsafe occurrences must be 0, got {checks['unsafe_occurrences']}")
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        if cargo_test.get("status") == "skipped":
            failures.append(f"cargo test skipped: {cargo_test.get('reason', 'unknown reason')}")
        return failures

    def _validation_status(self, checks: dict[str, Any], cargo_test: dict[str, Any]) -> str:
        return "failed" if self._validation_failures(checks, cargo_test) else "passed"

    def _append_harness_report(self, ctx: ConversionContext) -> None:
        report = ctx.result / "output.md"
        existing = report.read_text(encoding="utf-8") if report.exists() else ""
        appendix = PROFILE["harness_report_appendix"].format(harness_dir=ctx.result / "harness")
        write(report, existing + text_block(appendix))


def run_harness(ctx: ConversionContext) -> ConversionContext:
    agents: list[Agent] = [
        OutputScaffoldAgent(),
        ConstraintLoadingAgent(PROFILE["constraint_files"], PROFILE.get("constraint_summary_md")),
        ProjectAnalysisAgent(),
        SkeletonGenerationAgent(),
        ContextBuilderAgent(),
        ParityMatrixAgent(),
        TranslationAgent(),
        CompileAgent(),
        RepairAgent(),
        ValidationAgent(),
    ]
    return run_agents(ctx, agents)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FlashDB C-to-Rust agent harness.")
    parser.add_argument("--flashdb", default=str(DEFAULT_FLASHDB), help="Path to platform FlashDB source tree")
    parser.add_argument("--out", default="flashDB_rust", help="Output Rust project directory")
    parser.add_argument("--result", default="result", help="Result/report directory")
    parser.add_argument("--logs", default="logs", help="Logs directory with interaction and trace artifacts")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument("--skip-cargo", action="store_true", help="Skip cargo check/test even if cargo exists")
    parser.add_argument("--strict", action="store_true", help="Return non-zero unless validation status is passed")
    args = parser.parse_args()

    root = Path.cwd()
    out = Path(args.out)
    result = Path(args.result)
    logs = Path(args.logs)
    ctx = HarnessContext(
        root=root,
        source=Path(args.flashdb),
        out=out if out.is_absolute() else root / out,
        result=result if result.is_absolute() else root / result,
        logs=logs if logs.is_absolute() else root / logs,
        cargo=args.cargo,
        skip_cargo=args.skip_cargo,
        profile="flashdb",
    )
    run_harness(ctx)
    validation_status = ctx.validation_result.get("status", "unknown")
    print(f"generated Rust project: {ctx.out}")
    print(f"harness artifacts: {ctx.result / 'harness'}")
    print(f"log artifacts: {ctx.logs}")
    print(f"validation: {ctx.result / 'harness' / '07-validation.json'}")
    print(f"validation status: {validation_status}")
    if args.strict and validation_status != "passed":
        print("strict validation failed", file=sys.stderr)
        for failure in ctx.validation_result.get("failures", []):
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
