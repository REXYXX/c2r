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
    read_text,
    run_cargo,
    text_block,
    write,
)
from model_artifacts import display_name, generate_report
from profile_common import _as_list, _duplicate_lookup, _effective_profile, _extract_rust_function_body
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
                [
                    ("project_document_constraints_json", ctx.result / "harness" / "00-project-document-constraints.json"),
                    ("project_document_constraints_md", ctx.result / "harness" / "00-project-document-constraints.md"),
                    ("project_document_catalog_json", ctx.result / "harness" / "document-constraints" / "documents.json"),
                    ("result_function_parity_json", ctx.result / "harness" / "04-function-parity.json"),
                ],
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
            "unsafe_policy": self._check_unsafe_policy(ctx.out, effective),
        }
        if ctx.skip_cargo or cargo_path is None:
            cargo_test = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            cargo_test = run_cargo(ctx, ["test"])
        failures = self._validation_failures(checks, cargo_test, effective)
        failure_ownership = self._failure_ownership(failures, cargo_test)
        repair_required = self._repair_required(failures, failure_ownership)
        ctx.validation_result = {
            "status": "failed" if failures else "passed",
            "failures": failures,
            "repair_required": repair_required,
            "failure_ownership": failure_ownership,
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

    def _check_unsafe_policy(self, out: Path, profile: dict[str, Any]) -> dict[str, Any]:
        policy = profile.get("unsafe_policy", {})
        if not isinstance(policy, dict):
            policy = {}
        enabled = bool(policy.get("enabled", True))
        max_ratio = float(policy.get("max_ratio", 0.10))
        unsafe_blocks = 0
        rust_code_lines = 0
        files: dict[str, dict[str, Any]] = {}
        if not enabled:
            return {
                "enabled": False,
                "ok": True,
                "unsafe_blocks": 0,
                "rust_code_lines": 0,
                "ratio": 0.0,
                "max_ratio": max_ratio,
                "files": {},
            }
        for path in sorted(out.rglob("*.rs")):
            if "target" in path.parts:
                continue
            text = read_text(path)
            stripped = self._strip_rust_comments(text)
            file_unsafe = self._token_count(stripped, "unsafe")
            file_lines = sum(1 for line in stripped.splitlines() if line.strip())
            unsafe_blocks += file_unsafe
            rust_code_lines += file_lines
            if file_unsafe:
                relative = str(path.relative_to(out)).replace("\\", "/")
                files[relative] = {
                    "unsafe_blocks": file_unsafe,
                    "rust_code_lines": file_lines,
                }
        ratio = (unsafe_blocks / rust_code_lines) if rust_code_lines else (1.0 if unsafe_blocks else 0.0)
        return {
            "enabled": True,
            "ok": ratio < max_ratio,
            "unsafe_blocks": unsafe_blocks,
            "rust_code_lines": rust_code_lines,
            "ratio": ratio,
            "max_ratio": max_ratio,
            "metric": str(policy.get("metric", "unsafe_keyword_per_nonempty_rust_line")),
            "files": files,
        }

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
                missing_api_calls = [
                    token for token in required_api_calls if not self._has_equivalent_api_token(body_code, token, profile)
                ]
                missing_expanded_api_calls = [
                    token for token in required_expanded_api_calls if not self._has_equivalent_api_token(text_code, token, profile)
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

    def _has_equivalent_api_token(self, text: str, c_api: str, profile: dict[str, Any]) -> bool:
        return any(self._token_count(text, token) > 0 for token in self._api_equivalent_tokens(c_api, profile))

    def _api_equivalent_tokens(self, c_api: str, profile: dict[str, Any]) -> list[str]:
        token = str(c_api)
        candidates = [token]
        for prefix in _as_list(profile.get("api_equivalent_prefixes")):
            if token.startswith(prefix):
                candidates.append(token[len(prefix) :])
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
        unsafe_policy = checks.get("unsafe_policy", {})
        if isinstance(unsafe_policy, dict) and not unsafe_policy.get("ok", True):
            failures.append(
                "unsafe ratio must be < "
                f"{unsafe_policy.get('max_ratio', 0.10):.2%}, got {unsafe_policy.get('ratio', 0.0):.2%} "
                f"({unsafe_policy.get('unsafe_blocks', 0)} unsafe blocks / "
                f"{unsafe_policy.get('rust_code_lines', 0)} Rust code lines)"
            )
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        if cargo_test.get("status") == "skipped" and profile.get("cargo_test_required", True):
            failures.append(f"cargo test skipped: {cargo_test.get('reason', 'unknown reason')}")
        return failures

    def _failure_ownership(self, failures: list[str], cargo_test: dict[str, Any]) -> dict[str, Any]:
        ownership: dict[str, Any] = {
            "policy": (
                "ValidationStage 先归因再修复。Test Agent 只修复测试生成缺陷，不能删除测试、"
                "降低断言、缩小数据规模或替换目标 API 来绕过实现缺陷。语义完整测试运行失败时，"
                "默认交给 Code Agent 修复实现；归因不明确时交给 Validation Agent 进一步诊断。"
            ),
            "test_agent": [],
            "code_agent": [],
            "validation_agent": [],
            "ambiguous": [],
        }
        for failure in failures:
            owner, reason, action = self._classify_failure(failure, cargo_test)
            ownership[owner].append(
                {
                    "failure": failure,
                    "reason": reason,
                    "allowed_action": action,
                }
            )
        return ownership

    def _classify_failure(self, failure: str, cargo_test: dict[str, Any]) -> tuple[str, str, str]:
        if self._is_test_failure(failure):
            return (
                "test_agent",
                "test_generation_or_coverage_gap",
                "repair tests while preserving target API, data scale and assertion strength",
            )
        if failure.startswith("cargo test skipped"):
            return (
                "validation_agent",
                "validation_not_executed",
                "rerun strict validation with cargo available; skipped cargo is not a pass",
            )
        if failure == "cargo test failed":
            output = "\n".join(str(cargo_test.get(key, "")) for key in ("stdout", "stderr"))
            if re.search(r"-->\s+tests[/\\]", output) and re.search(r"error(?:\[E\d+\])?:", output):
                return (
                    "test_agent",
                    "test_compile_error",
                    "fix test harness code without simplifying test semantics",
                )
            if re.search(r"-->\s+src[/\\]|no method named|cannot find|unresolved import", output, re.I):
                return (
                    "code_agent",
                    "implementation_compile_or_api_gap",
                    "repair implementation or exported API exposed by tests",
                )
            return (
                "validation_agent",
                "cargo_failure_needs_routing",
                "inspect cargo diagnostics and route concrete src/tests failures",
            )
        if failure.startswith(("missing constraint document:", "missing required artifact:")):
            return (
                "validation_agent",
                "harness_artifact_or_constraint_missing",
                "repair harness inputs or rerun profile harness before code/test repair",
            )
        return (
            "code_agent",
            "implementation_or_api_gap",
            "repair Rust implementation instead of weakening tests",
        )

    def _repair_required(self, failures: list[str], failure_ownership: dict[str, Any]) -> dict[str, Any]:
        route: dict[str, Any] = {
            "must_continue": bool(failures),
            "next_action": "strict validation passed" if not failures else "repair routed failures and rerun strict validation",
            "ownership_policy": failure_ownership.get("policy", ""),
            "test_agent": [],
            "code_agent": [],
            "validation_agent": [],
            "ambiguous": [],
        }
        for owner in ("test_agent", "code_agent", "validation_agent", "ambiguous"):
            for item in failure_ownership.get(owner, []):
                route[owner].append(
                    f"{item.get('failure', '')} [{item.get('reason', '')}] {item.get('allowed_action', '')}"
                )
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
            - ProjectDocumentStage：在源码分析前读取项目文档并生成规范化约束。
            - ProjectAnalysisStage：生成源码清单和组件分组。
            - SkeletonGenerationStage：准备 Cargo crate 布局。
            - ParityMatrixStage：生成 profile 提供的 parity 矩阵。
            - TranslationStage：从 work/agents/ 固定模板渲染 Code Agent、Test Agent 和 Validation Agent 任务书。
            - CompileStage：Cargo 可用时记录 `cargo check` 诊断。
            - RepairStage：整理编译结果和修复判断。
            - ValidationStage：执行 profile 驱动的验证门禁。
            """
        write(report, existing + text_block(rendered))
