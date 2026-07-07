#!/usr/bin/env python3
"""Strict validation and repair routing."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from generic_harness import (
    ConversionContext,
    HarnessStage,
    check_artifact_structure,
    check_required_files,
    check_token_map,
    count_token_in_rust,
    read_text,
    run_cargo,
    text_block,
    write,
)
from model_artifacts import display_name, generate_report
from profile_common import _duplicate_lookup, _effective_profile, _extract_rust_function_body
from profile_trace import _record_profile_trace

class ProfileValidationStage(HarnessStage):
    name = "ValidationStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        effective = _effective_profile(ctx, self.profile)
        self._ensure_report_placeholders(ctx)
        checks = {
            "required_artifact_structure": check_artifact_structure(
                ctx,
                [("result_function_parity_json", ctx.result / "harness" / "04-function-parity.json")],
            ),
            "constraint_docs": {item["path"]: item["exists"] for item in ctx.constraints},
            "required_files": check_required_files(ctx.out, effective.get("required_output_files", [])),
            "api_symbols": check_token_map(ctx.out, effective.get("api_symbols", {})),
            "c_api_parity": self._check_c_api_parity(ctx.out, effective),
            "one_to_one_features": self._check_one_to_one_features(ctx.out, effective),
            "behaviour_model_rejection": self._check_behaviour_model_rejection(ctx.out, effective),
            "translated_test_coverage": self._check_translated_test_coverage(ctx),
            "readme_test_coverage": self._check_readme_test_coverage(ctx.out, effective),
            "test_semantic_requirements": self._check_test_semantic_requirements(ctx.out, effective),
            "unsafe_occurrences": count_token_in_rust(ctx.out, "unsafe") if effective.get("disallow_unsafe", True) else 0,
        }
        if ctx.skip_cargo or cargo_path is None:
            cargo_test = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            cargo_test = run_cargo(ctx, ["test"])
        failures = self._validation_failures(checks, cargo_test, effective)
        repair_required = self._repair_required(failures, cargo_test)
        ctx.validation_result = {
            "status": "failed" if failures else "passed",
            "failures": failures,
            "repair_required": repair_required,
            "checks": checks,
            "cargo_test": cargo_test,
        }
        write(ctx.artifact("07-validation.json"), json.dumps(ctx.validation_result, indent=2, ensure_ascii=False))
        generate_report(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, ctx.validation_result, ctx.analysis, effective)
        self._append_harness_report(ctx)
        _record_profile_trace(
            ctx,
            self.name,
            "run_profile_validation",
            status=ctx.validation_result.get("status"),
            failures=len(ctx.validation_result.get("failures", [])),
            cargo_status=ctx.validation_result.get("cargo_test", {}).get("status"),
            output="result/harness/07-validation.json",
        )

    def _ensure_report_placeholders(self, ctx: ConversionContext) -> None:
        project = display_name(self.profile)
        if not (ctx.result / "output.md").exists():
            write(ctx.result / "output.md", f"# {project} Rust 转换执行报告\n\n等待验证。\n")
        if not (ctx.result / "issues" / "00-summary.md").exists():
            write(ctx.result / "issues" / "00-summary.md", "# 转换摘要\n\n等待验证。\n")

    def _check_c_api_parity(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        modules_by_group = profile.get("c_api_parity_modules", {})
        for group, symbols in profile.get("c_api_parity_symbols", {}).items():
            text = "\n".join(read_text(out / relative) for relative in modules_by_group.get(group, []))
            missing = [symbol for symbol in symbols if symbol not in text]
            result[group] = {
                "ok": not missing,
                "missing": missing,
            }
        return result

    def _check_one_to_one_features(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for relative, features in profile.get("one_to_one_features", {}).items():
            text = read_text(self._output_path(out, relative, default_dir="src"))
            feature_result = {}
            for feature, tokens in features.items():
                missing = [token for token in tokens if token not in text]
                feature_result[feature] = {
                    "ok": not missing,
                    "missing": missing,
                }
            result[relative] = feature_result
        return result

    def _check_behaviour_model_rejection(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, rule in profile.get("behaviour_model_rejection", {}).items():
            text = read_text(out / rule["file"])
            bad_hits = [token for token in rule.get("bad", []) if token in text]
            missing_offsets = [token for token in rule.get("required_offsets", []) if token not in text]
            result[name] = {
                "ok": not (bad_hits and missing_offsets),
                "bad_hits": bad_hits,
                "missing_required_offsets": missing_offsets,
            }
        return result

    def _check_translated_test_coverage(self, ctx: ConversionContext) -> dict[str, Any]:
        source_runs = ctx.analysis.get("source_test_runs", {})
        effective = _effective_profile(ctx, self.profile)
        suites = effective.get("test_suites", {})
        expected = {
            suite: self._expected_rust_test_names(source_runs.get(suite, []), suite, effective)
            for suite in suites
        }
        actual = {
            suite: self._rust_test_names(ctx.out / spec["target"])
            for suite, spec in suites.items()
        }
        return {
            "source_runs": source_runs,
            "expected_rust_tests": expected,
            "actual_rust_tests": actual,
            "missing": {
                suite: [name for name in expected[suite] if name not in actual[suite]]
                for suite in suites
            },
        }

    def _check_readme_test_coverage(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        coverage = profile.get("readme_test_coverage", {})
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

    def _check_test_semantic_requirements(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        requirements = profile.get("test_semantic_requirements", {})
        result: dict[str, Any] = {}
        for relative, tests in requirements.items():
            text = read_text(out / relative)
            test_results: dict[str, Any] = {}
            for test_name, spec in tests.items():
                body = _extract_rust_function_body(text, str(test_name))
                body_code = self._strip_rust_comments(body)
                text_code = self._strip_rust_comments(text)
                validation = spec.get("static_validation", {})
                required_api_calls = [str(token) for token in validation.get("required_api_calls", [])]
                required_expanded_api_calls = [str(token) for token in validation.get("required_expanded_api_calls", [])]
                required_fields = [str(token) for token in validation.get("required_assertion_fields", [])]
                required_constants = [str(token) for token in validation.get("required_assertion_constants", [])]
                required_loop_tokens = [str(token) for token in validation.get("required_loop_tokens", [])]
                required_literals = [str(token) for token in validation.get("required_representative_literals", [])]
                required_macro_tokens = [str(token) for token in validation.get("required_macro_expansion_tokens", [])]
                forbidden_api_calls = [str(token) for token in validation.get("forbidden_api_calls", [])]
                assertion_count = len(re.findall(r"\bassert(?:_eq|_ne)?!|\bassert\s*\(", body_code))
                minimum_assertions = int(validation.get("minimum_assertions", 0) or 0)
                has_loop = bool(re.search(r"\b(?:for|while|loop)\b|\.for_each\s*\(", body_code))
                missing_api_calls = [token for token in required_api_calls if not self._has_equivalent_api_token(body_code, token)]
                missing_expanded_api_calls = [
                    token for token in required_expanded_api_calls if not self._has_equivalent_api_token(text_code, token)
                ]
                advisory_missing_fields = [token for token in required_fields if token not in body_code]
                advisory_missing_constants = [token for token in required_constants if self._token_count(body_code, token) == 0]
                advisory_missing_loop_tokens = [token for token in required_loop_tokens if self._token_count(body_code, token) == 0]
                advisory_missing_literals = [token for token in required_literals if token not in body]
                advisory_missing_macro_tokens = [token for token in required_macro_tokens if self._token_count(text_code, token) == 0]
                forbidden_present = [token for token in forbidden_api_calls if self._token_count(body_code, token) > 0]
                test_results[str(test_name)] = {
                    "ok": (
                        bool(body)
                        and not missing_api_calls
                        and not missing_expanded_api_calls
                        and not forbidden_present
                        and assertion_count >= minimum_assertions
                    ),
                    "has_body": bool(body),
                    "missing_api_calls": missing_api_calls,
                    "missing_expanded_api_calls": missing_expanded_api_calls,
                    "advisory_missing_assertion_fields": advisory_missing_fields,
                    "advisory_missing_assertion_constants": advisory_missing_constants,
                    "advisory_missing_loop_tokens": advisory_missing_loop_tokens,
                    "advisory_missing_representative_literals": advisory_missing_literals,
                    "advisory_missing_macro_expansion_tokens": advisory_missing_macro_tokens,
                    "forbidden_api_calls_present": forbidden_present,
                    "assertion_count": assertion_count,
                    "minimum_assertions": minimum_assertions,
                    "has_loop": has_loop,
                    "logic_consistency": spec.get("logic_consistency", {}),
                    "requirements": spec.get("requirements", []),
                }
            result[str(relative)] = test_results
        return result

    def _has_equivalent_api_token(self, text: str, c_api: str) -> bool:
        return any(self._token_count(text, token) > 0 for token in self._api_equivalent_tokens(c_api))

    def _api_equivalent_tokens(self, c_api: str) -> list[str]:
        token = str(c_api)
        candidates = [token]
        prefixes = ("fdb_kvdb_", "fdb_tsdb_", "fdb_tsl_", "fdb_kv_", "fdb_blob_", "fdb_")
        for prefix in prefixes:
            if token.startswith(prefix):
                candidates.append(token[len(prefix) :])
        if token.startswith("fdb_"):
            candidates.append(token[4:])
        return sorted({candidate for candidate in candidates if candidate})

    def _expected_rust_test_names(self, source_runs: list[str], suite: str, profile: dict[str, Any]) -> list[str]:
        duplicate_test_name_map = _duplicate_lookup(profile)
        counts: dict[str, int] = {}
        expected = []
        for name in source_runs:
            counts[name] = counts.get(name, 0) + 1
            mapped = duplicate_test_name_map.get((suite, name, counts[name]))
            expected.append(mapped or name)
        return expected

    def _rust_test_names(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(r"fn\s+(test_[A-Za-z0-9_]+)\s*\(", text)

    def _token_count(self, text: str, token: str) -> int:
        if not token:
            return 0
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
        elif re.fullmatch(r"\d+", token):
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
        else:
            pattern = re.escape(token)
        return len(re.findall(pattern, text))

    def _strip_rust_comments(self, text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"//.*", "", text)

    def _output_path(self, out: Path, relative: str, default_dir: str) -> Path:
        direct = out / relative
        if direct.exists() or "/" in relative or "\\" in relative:
            return direct
        return out / default_dir / relative

    def _validation_failures(self, checks: dict[str, Any], cargo_test: dict[str, Any], profile: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for path, exists in checks.get("constraint_docs", {}).items():
            if not exists:
                failures.append(f"missing constraint document: {path}")
        for artifact, exists in checks.get("required_artifact_structure", {}).items():
            if not exists:
                failures.append(f"missing required artifact: {artifact}")
        for path, exists in checks.get("required_files", {}).items():
            if not exists:
                failures.append(f"missing generated file: {path}")
        for path, result in checks.get("api_symbols", {}).items():
            if not result["ok"]:
                failures.append(f"missing API symbols in {path}: {', '.join(result['missing'])}")
        for group, result in checks.get("c_api_parity", {}).items():
            if not result["ok"]:
                failures.append(f"missing C API parity symbols in {group}: {', '.join(result['missing'])}")
        for module, features in checks.get("one_to_one_features", {}).items():
            for feature, result in features.items():
                if not result["ok"]:
                    failures.append(f"missing coverage feature {module}:{feature}: {', '.join(result['missing'])}")
        for rule, result in checks.get("behaviour_model_rejection", {}).items():
            if not result["ok"]:
                failures.append(
                    f"behaviour-model shortcut detected ({rule}): bad tokens {', '.join(result['bad_hits'])}; "
                    f"missing offsets {', '.join(result['missing_required_offsets'])}"
                )
        for suite, missing in checks.get("translated_test_coverage", {}).get("missing", {}).items():
            if missing:
                failures.append(f"missing translated {suite.upper()} tests: {', '.join(missing)}")
        for path, missing in checks.get("readme_test_coverage", {}).get("missing", {}).items():
            if missing:
                failures.append(f"missing README/benchmark coverage in {path}: {', '.join(missing)}")
        for path, tests in checks.get("test_semantic_requirements", {}).items():
            for name, result in tests.items():
                if result.get("ok", False):
                    continue
                problems = []
                if not result.get("has_body", False):
                    problems.append("missing test body")
                if result.get("missing_api_calls"):
                    problems.append("missing target API semantics: " + ", ".join(result["missing_api_calls"]))
                if result.get("missing_expanded_api_calls"):
                    problems.append("missing helper-expanded API semantics: " + ", ".join(result["missing_expanded_api_calls"]))
                if result.get("forbidden_api_calls_present"):
                    problems.append("unexpected substitute API calls: " + ", ".join(result["forbidden_api_calls_present"]))
                if int(result.get("assertion_count", 0) or 0) < int(result.get("minimum_assertions", 0) or 0):
                    problems.append(
                        f"assertions {result.get('assertion_count', 0)} < {result.get('minimum_assertions', 0)}"
                    )
                failures.append(f"missing semantic coverage in {path}::{name}: {'; '.join(problems)}")
        if checks.get("unsafe_occurrences", 0) != 0:
            failures.append(f"unsafe occurrences must be 0, got {checks['unsafe_occurrences']}")
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        if cargo_test.get("status") == "skipped" and profile.get("cargo_test_required", True):
            failures.append(f"cargo test skipped: {cargo_test.get('reason', 'unknown reason')}")
        return failures

    def _repair_required(self, failures: list[str], cargo_test: dict[str, Any]) -> dict[str, Any]:
        route: dict[str, Any] = {
            "must_continue": bool(failures),
            "next_action": "strict validation passed" if not failures else "repair routed failures and rerun strict validation",
            "test_agent": [],
            "code_agent": [],
            "validation_agent": [],
        }
        for failure in failures:
            if self._is_test_failure(failure):
                route["test_agent"].append(failure)
            elif failure.startswith("cargo test skipped"):
                route["validation_agent"].append(failure)
            elif failure == "cargo test failed":
                route["validation_agent"].append("read cargo test diagnostics and route concrete src/tests failures")
            else:
                route["code_agent"].append(failure)
        if cargo_test.get("status") == "failed" and "cargo test failed" not in failures:
            route["validation_agent"].append("read cargo test diagnostics and route concrete src/tests failures")
        return route

    def _is_test_failure(self, failure: str) -> bool:
        if "tests/" in failure or "tests\\" in failure:
            return True
        return failure.startswith(
            (
                "missing translated ",
                "missing README/benchmark coverage",
            )
        )

    def _append_harness_report(self, ctx: ConversionContext) -> None:
        report = ctx.result / "output.md"
        existing = report.read_text(encoding="utf-8") if report.exists() else ""
        appendix = self.profile.get("harness_report_appendix")
        if appendix:
            rendered = str(appendix).format(harness_dir=ctx.result / "harness")
        else:
            rendered = f"""
            ## 执行框架阶段记录

            执行框架产物位于 `{ctx.result / "harness"}`。

            - OutputScaffoldStage：创建 result 和 logs 产物结构。
            - ConstraintLoadingStage：加载 markdown profile 约束。
            - ProjectAnalysisStage：生成源码清单和组件分组。
            - SkeletonGenerationStage：准备 Cargo crate 布局。
            - ParityMatrixStage：生成 profile 提供的 parity 矩阵。
            - TranslationStage：从 work/agents/ 固定模板渲染 Code Agent、Test Agent 和 Validation Agent 任务书。
            - CompileStage：Cargo 可用时记录 `cargo check` 诊断。
            - RepairStage：整理编译结果和修复判断。
            - ValidationStage：执行 profile 驱动的验证门禁。
            """
        write(report, existing + text_block(rendered))
