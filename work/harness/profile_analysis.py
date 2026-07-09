#!/usr/bin/env python3
"""Source analysis and dynamic profile derivation."""

from __future__ import annotations

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

from generic_harness import ConversionContext, HarnessStage, read_text, text_block, write
from model_artifacts import list_relative
from profile_common import (
    CONTROL_KEYWORDS,
    INCLUDE_CONTEXT_SUFFIXES,
    SOURCE_CONTEXT_SUFFIXES,
    TEST_CONTEXT_SUFFIXES,
    _append_unique,
    _as_list,
    _derive_benchmark,
    _derive_readme_tests,
    _duplicate_lookup,
    _extract_c_function_body,
    _extract_function_names,
    _group_public_apis,
    _layout,
    _merge_missing,
    _merge_readme_coverage,
    _module_for_source,
    _profile_name,
    _snake_name,
    _test_runs_from_path,
)
from profile_trace import _record_profile_trace

class ProfileProjectAnalysisStage(HarnessStage):
    name = "ProjectAnalysisStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        layout = _layout(self.profile)
        src_files = self._list_many(ctx.source, _as_list(layout.get("source_dirs")), SOURCE_CONTEXT_SUFFIXES)
        test_files = self._list_many(ctx.source, _as_list(layout.get("test_dirs")), TEST_CONTEXT_SUFFIXES)
        include_files = self._list_many(ctx.source, _as_list(layout.get("include_dirs")), INCLUDE_CONTEXT_SUFFIXES)
        all_files = src_files + include_files + test_files
        _record_profile_trace(
            ctx,
            self.name,
            "scan_source_tree",
            source_dirs=_as_list(layout.get("source_dirs")),
            test_dirs=_as_list(layout.get("test_dirs")),
            include_dirs=_as_list(layout.get("include_dirs")),
            src_files=len(src_files),
            test_files=len(test_files),
            include_files=len(include_files),
        )
        components = self._collect_components(all_files)
        public_apis = self._collect_public_apis(ctx.source / str(layout.get("public_api_header", "")))
        derived_profile = self._derive_profile(ctx.source, src_files, test_files, include_files, public_apis)
        effective_profile = _merge_missing(self.profile, derived_profile)
        source_test_runs = self._collect_source_test_runs(ctx.source, effective_profile)
        internal_anchors = self._collect_internal_anchors(ctx.source, effective_profile)
        readme_coverage = effective_profile.get("readme_test_coverage", {})
        required_rust_tests = readme_coverage.get("required_rust_tests", {})
        benchmark = readme_coverage.get("benchmark", {})
        semantic_requirements = effective_profile.get("test_semantic_requirements", {})
        _record_profile_trace(
            ctx,
            self.name,
            "derive_dynamic_profile",
            component_groups=len(components),
            public_apis=len(public_apis),
            test_suites=sorted(effective_profile.get("test_suites", {}).keys()),
            required_rust_tests={target: len(tests) for target, tests in required_rust_tests.items()},
            benchmark_tests=len(benchmark.get("operation_tests", [])) if isinstance(benchmark, dict) else 0,
            semantic_test_requirements=sum(len(tests) for tests in semantic_requirements.values()),
            source_to_rust_modules=len(effective_profile.get("source_to_rust_modules", {})),
        )
        ctx.analysis = {
            "profile": _profile_name(self.profile),
            "source_path": str(ctx.source),
            "source_exists": ctx.source.exists(),
            "src_files": src_files,
            "test_files": test_files,
            "include_files": include_files,
            "components": components,
            "source_test_runs": source_test_runs,
            "public_apis": public_apis,
            "internal_parity_anchors": internal_anchors,
            "derived_profile": derived_profile,
            "effective_profile": effective_profile,
        }
        removed_output_count = self._remove_full_profile_artifacts(ctx)
        profile_summary = self._profile_summary(
            ctx,
            src_files=src_files,
            test_files=test_files,
            include_files=include_files,
            components=components,
            effective_profile=effective_profile,
        )
        write(ctx.artifact("01-profile-summary.json"), json.dumps(profile_summary, indent=2, ensure_ascii=False))
        write(ctx.artifact("01-profile-summary.md"), self._profile_summary_markdown(profile_summary))
        write(ctx.artifact("01-dependency-map.md"), self._dependency_markdown(ctx, src_files, test_files, include_files))
        _record_profile_trace(
            ctx,
            self.name,
            "write_analysis_artifacts",
            outputs=[
                "result/harness/01-profile-summary.json",
                "result/harness/01-profile-summary.md",
                "result/harness/01-dependency-map.md",
            ],
            full_profile_artifacts="not_emitted",
            removed_stale_output_count=removed_output_count,
        )

    def _remove_full_profile_artifacts(self, ctx: ConversionContext) -> int:
        removed = 0
        for name in (
            "01-analysis.json",
            "01-derived-profile.json",
            "01-effective-profile.json",
            "01-effective-profile.md",
        ):
            path = ctx.artifact(name)
            if path.exists():
                path.unlink()
                removed += 1
        return removed

    def _profile_summary(
        self,
        ctx: ConversionContext,
        *,
        src_files: list[str],
        test_files: list[str],
        include_files: list[str],
        components: dict[str, list[str]],
        effective_profile: dict[str, Any],
    ) -> dict[str, Any]:
        readme = effective_profile.get("readme_test_coverage", {}) or {}
        if not isinstance(readme, dict):
            readme = {}
        required_tests = readme.get("required_rust_tests", {}) or {}
        if not isinstance(required_tests, dict):
            required_tests = {}
        benchmark = readme.get("benchmark", {}) or {}
        if not isinstance(benchmark, dict):
            benchmark = {}
        semantic = effective_profile.get("test_semantic_requirements", {}) or {}
        if not isinstance(semantic, dict):
            semantic = {}
        parity = effective_profile.get("c_api_parity_symbols", {}) or {}
        public_apis = parity.get("public_api", []) if isinstance(parity, dict) else []
        required_files = effective_profile.get("required_output_files", []) or []
        source_to_rust = effective_profile.get("source_to_rust_modules", {}) or {}
        return {
            "profile": _profile_name(self.profile),
            "source": str(ctx.source),
            "source_exists": ctx.source.exists(),
            "source_files": len(src_files),
            "include_files": len(include_files),
            "test_files": len(test_files),
            "component_groups": {name: len(paths) for name, paths in sorted(components.items())},
            "public_api_count": len(public_apis),
            "source_to_rust_mappings": len(source_to_rust) if isinstance(source_to_rust, dict) else 0,
            "required_output_files": len(required_files),
            "required_test_targets": sorted(str(target) for target in required_tests),
            "semantic_test_requirements": {
                str(target): len(tests) for target, tests in sorted(semantic.items()) if isinstance(tests, dict)
            },
            "benchmark_tests": len(benchmark.get("operation_tests", [])),
            "model_entrypoints": {
                "code_agent": "result/harness/agent-entry/code-agent.json",
                "test_agent": "result/harness/agent-entry/test-agent.json",
                "validation_agent": "result/harness/agent-entry/validation-agent.json",
            },
            "artifact_entrypoints": {
                "code_plan": "result/harness/code-plan.json",
                "code_manifest": "result/harness/code-manifest.json",
                "test_requirements": "result/harness/test-requirements/manifest.json",
                "trace": "logs/trace/profile-harness-path.md",
            },
            "full_profile_artifacts": "not_emitted",
        }

    def _profile_summary_markdown(self, summary: dict[str, Any]) -> str:
        targets = summary.get("required_test_targets", [])
        semantic = summary.get("semantic_test_requirements", {})
        components = summary.get("component_groups", {})
        return text_block(
            f"""
            # Profile Summary

            - profile: {summary.get("profile")}
            - source: {summary.get("source")}
            - source files: {summary.get("source_files")}
            - include files: {summary.get("include_files")}
            - test files: {summary.get("test_files")}
            - public API count: {summary.get("public_api_count")}
            - required output files: {summary.get("required_output_files")}
            - benchmark tests: {summary.get("benchmark_tests")}
            - full analysis/profile artifacts: not emitted

            ## Required Test Targets

            {json.dumps(targets, ensure_ascii=False, indent=2)}

            ## Semantic Test Requirement Counts

            {json.dumps(semantic, ensure_ascii=False, indent=2)}

            ## Component Groups

            {json.dumps(components, ensure_ascii=False, indent=2)}

            ## Artifact Entrypoints

            {json.dumps(summary.get("artifact_entrypoints", {}), ensure_ascii=False, indent=2)}
            """
        )

    def _list_many(self, root: Path, subdirs: list[str], suffixes: set[str] | None = None) -> list[str]:
        files: list[str] = []
        for subdir in subdirs:
            for relative in list_relative(root, subdir):
                if suffixes is not None and Path(relative).suffix.lower() not in suffixes:
                    continue
                files.append(relative)
        return files

    def _collect_components(self, files: list[str]) -> dict[str, list[str]]:
        components: dict[str, list[str]] = {}
        filters = self.profile.get("component_filters", {})
        for name, tokens in filters.items():
            lowered_tokens = [str(token).lower() for token in tokens]
            components[name] = [
                path
                for path in files
                if Path(path).suffix.lower() in SOURCE_CONTEXT_SUFFIXES
                and any(token in path.lower() for token in lowered_tokens)
            ]
        return components

    def _derive_profile(
        self,
        source: Path,
        src_files: list[str],
        test_files: list[str],
        include_files: list[str],
        public_apis: list[str],
    ) -> dict[str, Any]:
        test_suites = self._derive_test_suites(source, test_files)
        duplicate_test_name_map = self._derive_duplicate_test_name_map(source, test_suites)
        profile_for_tests = _merge_missing(self.profile, {"duplicate_test_name_map": duplicate_test_name_map})
        readme_coverage = _derive_readme_tests(source, profile_for_tests, test_suites)
        benchmark_coverage = _derive_benchmark(source, profile_for_tests)
        readme_coverage = _merge_readme_coverage(readme_coverage, benchmark_coverage)
        c_api_parity = _group_public_apis(public_apis, self.profile)
        test_semantic_requirements = self._derive_test_semantic_requirements(source, test_suites, profile_for_tests, public_apis)
        source_to_rust = {
            relative: _module_for_source(relative, self.profile)
            for relative in include_files + src_files
            if relative.endswith((".c", ".h"))
        }
        public_api_source_modules = self._derive_public_api_source_modules(source, src_files, public_apis, source_to_rust)
        return {
            "test_suites": test_suites,
            "duplicate_test_name_map": duplicate_test_name_map,
            "readme_test_coverage": readme_coverage,
            "test_semantic_requirements": test_semantic_requirements,
            "c_api_parity_symbols": c_api_parity,
            "c_api_parity_modules": self._derive_c_api_parity_modules(source_to_rust, c_api_parity),
            "internal_parity_anchors": self._derive_internal_parity_anchors(source, src_files),
            "source_to_rust_modules": source_to_rust,
            "public_api_source_modules": public_api_source_modules,
            "required_output_files": self._derive_required_output_files(source_to_rust, test_suites, readme_coverage),
            "api_symbols": self._derive_api_symbols(source_to_rust),
            "api_equivalent_prefixes": self._derive_api_equivalent_prefixes(public_apis),
        }

    def _derive_test_suites(self, source: Path, test_files: list[str]) -> dict[str, dict[str, str]]:
        suites: dict[str, dict[str, str]] = {}
        for relative in test_files:
            if not relative.endswith(".c") or "benchmark" in relative.lower():
                continue
            runs = self._test_runs_from_file(source / relative)
            if not runs:
                continue
            name = _snake_name(relative, self.profile)
            suites[name] = {
                "source": relative,
                "target": f"tests/{name}_tests.rs",
            }
        return suites

    def _derive_duplicate_test_name_map(self, source: Path, test_suites: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
        mappings: list[dict[str, Any]] = []
        suffixes = {1: "first_run", 2: "second_run", 3: "third_run"}
        for suite, spec in test_suites.items():
            runs = self._test_runs_from_file(source / spec["source"])
            totals = {name: runs.count(name) for name in set(runs)}
            counts: dict[str, int] = {}
            for name in runs:
                counts[name] = counts.get(name, 0) + 1
                if totals[name] <= 1:
                    continue
                suffix = suffixes.get(counts[name], f"run_{counts[name]}")
                mappings.append(
                    {
                        "suite": suite,
                        "source": name,
                        "occurrence": counts[name],
                        "target": f"{name}_{suffix}",
                    }
                )
        return mappings

    def _derive_c_api_parity_modules(
        self,
        source_to_rust: dict[str, list[str]],
        c_api_parity: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        modules = sorted({module for values in source_to_rust.values() for module in values if module.startswith("src/")})
        return {group: modules for group in c_api_parity}

    def _derive_required_output_files(
        self,
        source_to_rust: dict[str, list[str]],
        test_suites: dict[str, dict[str, str]],
        readme_coverage: dict[str, Any],
    ) -> list[str]:
        required = ["Cargo.toml", "src/lib.rs"]
        for module in sorted({module for modules in source_to_rust.values() for module in modules if module.startswith("src/")}):
            _append_unique(required, module)
        for spec in test_suites.values():
            _append_unique(required, str(spec["target"]))
        for target in readme_coverage.get("required_rust_tests", {}):
            _append_unique(required, str(target))
        return required

    def _derive_test_semantic_requirements(
        self,
        source: Path,
        test_suites: dict[str, dict[str, str]],
        profile: dict[str, Any],
        public_apis: list[str],
    ) -> dict[str, dict[str, Any]]:
        requirements: dict[str, dict[str, Any]] = {}
        pattern = _layout(profile).get("test_run_pattern")
        duplicate_names = _duplicate_lookup(profile)
        public_api_set = set(public_apis)
        for suite, spec in test_suites.items():
            relative = str(spec.get("source", ""))
            target = str(spec.get("target", ""))
            path = source / relative
            text = read_text(path)
            macros = self._macro_definitions(text)
            counts: dict[str, int] = {}
            for raw_name in _test_runs_from_path(path, pattern):
                counts[raw_name] = counts.get(raw_name, 0) + 1
                rust_name = duplicate_names.get((suite, raw_name, counts[raw_name]), raw_name)
                body = _extract_c_function_body(text, raw_name)
                semantic = self._semantic_requirements_from_body(raw_name, body, text, macros, public_api_set)
                if semantic:
                    semantic["source"] = relative
                    semantic["c_test"] = raw_name
                    requirements.setdefault(target, {})[rust_name] = semantic
        return requirements

    def _semantic_requirements_from_body(
        self,
        test_name: str,
        body: str,
        file_text: str,
        macros: dict[str, str],
        public_api_set: set[str],
    ) -> dict[str, Any]:
        calls = self._calls_from_body(body)
        assertions = self._assertion_expressions(body)
        loop_headers = [match.group(0) for match in re.finditer(r"\b(?:for|while)\s*\([^{}]{0,240}\)", body)]
        public_api_call_counts = Counter(name for name in calls if name in public_api_set)
        public_api_calls = sorted(public_api_call_counts)
        assertion_fields = sorted({field for expression in assertions for field in self._field_tokens(expression)})
        assertion_constants = sorted({token for expression in assertions for token in self._constant_tokens(expression)})
        loop_tokens = sorted({token for header in loop_headers for token in self._constant_tokens(header)})
        body_constants = sorted(set(self._constant_tokens(body)))
        macro_tokens = self._macro_dependency_tokens(body_constants, macros)
        literals = self._representative_literals(body)
        defined_helpers = self._defined_function_names(file_text)
        direct_helper_calls = {
            name
            for name in calls
            if name not in public_api_set
            and not name.startswith("test_assert")
            and name not in CONTROL_KEYWORDS
        }
        referenced_helper_callbacks = {
            name
            for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", body)
            if name in defined_helpers
            and name not in direct_helper_calls
            and name not in public_api_set
            and not name.startswith("test_assert")
            and name not in CONTROL_KEYWORDS
        }
        helper_calls = sorted(direct_helper_calls | referenced_helper_callbacks)
        helper_observations = self._expanded_helper_observations(file_text, helper_calls, public_api_set)
        expanded_api_calls = helper_observations.get("public_api_calls", [])
        expanded_api_call_counts = helper_observations.get("public_api_call_counts", {})
        allowed_api_calls = set(public_api_calls) | set(expanded_api_calls)
        forbidden_api_calls = self._forbidden_public_api_calls(public_api_set, allowed_api_calls, test_name)

        if not (
            public_api_calls
            or assertion_fields
            or assertion_constants
            or loop_tokens
            or literals
            or helper_calls
            or macro_tokens
            or expanded_api_calls
        ):
            return {}

        expanded_assertions = int(helper_observations.get("assertion_count", 0) or 0)
        minimum_assertions = 1 if assertions or expanded_assertions else 0
        logic_consistency = {
            "target_api": {
                "required": public_api_calls,
                "helper_expanded": expanded_api_calls,
                "forbidden_substitutes": forbidden_api_calls[:16],
                "rule": "Rust 测试调用的 API 必须与 C 测试 API 语义等价，不要求保留 C 函数名。",
            },
            "test_data": {
                "representative_literals": literals[:8],
                "loop_tokens": loop_tokens[:8],
                "macro_scale_tokens": macro_tokens[:8],
                "rule": "key/value、blob、初始化参数和数据规模保持语义等价；值可不同但含义不能改变。",
            },
            "assertion_conditions": {
                "minimum_assertions": minimum_assertions,
                "fields": assertion_fields[:8],
                "expected_constants": assertion_constants[:8],
                "rule": "断言类型和期望行为等价，例如 C 成功码可映射为 Rust 的 is_ok()。",
            },
        }
        static_validation = {
            "required_api_calls": public_api_calls,
            "required_expanded_api_calls": expanded_api_calls,
            "forbidden_api_calls": forbidden_api_calls[:16],
            "minimum_assertions": minimum_assertions,
        }
        observations = {
            "public_api_calls": public_api_calls,
            "public_api_call_counts": dict(sorted(public_api_call_counts.items())),
            "assertion_count": len(assertions),
            "assertion_fields": assertion_fields[:8],
            "assertion_constants": assertion_constants[:8],
            "macro_expansion_tokens": macro_tokens[:8],
            "loop_count": len(loop_headers),
            "loop_tokens": loop_tokens[:8],
            "representative_literals": literals[:8],
            "helper_calls": helper_calls,
            "expanded_public_api_calls": expanded_api_calls,
            "expanded_public_api_call_counts": expanded_api_call_counts,
        }
        return {
            "requirements": [
                "根据 C 测试源码实时抽取：Rust 测试必须在目标 API、测试数据、断言条件三层保持语义等价。"
            ],
            "logic_consistency": logic_consistency,
            "source_observations": observations,
            "static_validation": static_validation,
        }

    def _calls_from_body(self, body: str) -> list[str]:
        return [
            name
            for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)
            if name not in CONTROL_KEYWORDS and name not in {"sizeof", "defined"}
        ]

    def _macro_definitions(self, text: str) -> dict[str, str]:
        definitions: dict[str, str] = {}
        logical_lines: list[str] = []
        current = ""
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if current:
                current += " " + line.rstrip("\\").strip()
            else:
                current = line.rstrip("\\").strip()
            if line.endswith("\\"):
                continue
            logical_lines.append(current)
            current = ""
        if current:
            logical_lines.append(current)
        for line in logical_lines:
            match = re.match(r"\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*\([^)]*\))?\s+(.+?)\s*$", line)
            if match:
                definitions[match.group(1)] = match.group(2)
        return definitions

    def _macro_dependency_tokens(self, constants: list[str], macros: dict[str, str]) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()

        def visit(name: str, depth: int) -> None:
            if depth > 8 or name in seen or name not in macros:
                return
            seen.add(name)
            _append_unique(tokens, name)
            expression = macros[name]
            for token in self._constant_tokens(expression):
                _append_unique(tokens, token)
            for number in re.findall(r"\b\d+\b", expression):
                if int(number) >= 8:
                    _append_unique(tokens, number)
            for child in self._constant_tokens(expression):
                visit(child, depth + 1)

        for constant in constants:
            visit(constant, 0)
        return tokens

    def _defined_function_names(self, text: str) -> set[str]:
        names: set[str] = set()
        for match in re.finditer(
            r"(?m)^\s*(?:static\s+)?(?:inline\s+)?[A-Za-z_][A-Za-z0-9_\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{",
            text,
        ):
            names.add(match.group(1))
        return names

    def _expanded_helper_observations(
        self,
        file_text: str,
        helper_calls: list[str],
        public_api_set: set[str],
    ) -> dict[str, Any]:
        api_counts: Counter[str] = Counter()
        assertion_count = 0
        assertion_fields: set[str] = set()
        assertion_constants: set[str] = set()
        loop_headers: list[str] = []
        loop_tokens: set[str] = set()
        literals: list[str] = []
        helper_chain: list[str] = []
        visited: set[str] = set()

        def visit(name: str, depth: int) -> None:
            nonlocal assertion_count
            if depth > 3 or name in visited:
                return
            visited.add(name)
            body = _extract_c_function_body(file_text, name)
            if not body:
                return
            _append_unique(helper_chain, name)
            calls = self._calls_from_body(body)
            api_counts.update(call for call in calls if call in public_api_set)
            assertions = self._assertion_expressions(body)
            assertion_count += len(assertions)
            for expression in assertions:
                assertion_fields.update(self._field_tokens(expression))
                assertion_constants.update(self._constant_tokens(expression))
            for match in re.finditer(r"\b(?:for|while)\s*\([^{}]{0,240}\)", body):
                header = match.group(0)
                _append_unique(loop_headers, header)
                loop_tokens.update(self._constant_tokens(header))
            for literal in self._representative_literals(body):
                _append_unique(literals, literal)
            for call in calls:
                if call not in public_api_set and not call.startswith("test_assert") and call not in CONTROL_KEYWORDS:
                    visit(call, depth + 1)

        for helper in helper_calls:
            visit(helper, 0)
        return {
            "helper_call_chain": helper_chain,
            "public_api_calls": sorted(api_counts),
            "public_api_call_counts": dict(sorted(api_counts.items())),
            "assertion_count": assertion_count,
            "assertion_fields": sorted(assertion_fields),
            "assertion_constants": sorted(assertion_constants),
            "loop_count": len(loop_headers),
            "loop_headers": loop_headers,
            "loop_tokens": sorted(loop_tokens),
            "representative_literals": literals,
        }

    def _forbidden_public_api_calls(
        self,
        public_api_set: set[str],
        allowed_api_calls: set[str],
        test_name: str,
    ) -> list[str]:
        operation_tokens = self._operation_tokens(test_name)
        forbidden = []
        for name in sorted(public_api_set):
            if name in allowed_api_calls:
                continue
            if re.search(r"(?:^|_)(?:init|deinit|control|check|set_default)(?:_|$)", name):
                continue
            if self._is_same_operation_variant(name, allowed_api_calls) or self._is_named_operation_substitute(
                name, allowed_api_calls, operation_tokens
            ):
                forbidden.append(name)
        return forbidden

    def _operation_tokens(self, test_name: str) -> set[str]:
        aliases = {
            "del": {"del", "delete"},
            "delete": {"del", "delete"},
            "append": {"append"},
            "set": {"set"},
            "clean": {"clean"},
            "erase": {"erase"},
            "format": {"format"},
            "write": {"write"},
        }
        tokens = set(re.findall(r"[A-Za-z0-9]+", test_name.lower()))
        operations: set[str] = set()
        for token in tokens:
            operations.update(aliases.get(token, set()))
        return operations

    def _is_same_operation_variant(self, candidate: str, allowed_api_calls: set[str]) -> bool:
        for allowed in allowed_api_calls:
            if candidate.startswith(f"{allowed}_") or allowed.startswith(f"{candidate}_"):
                return True
        return False

    def _is_named_operation_substitute(
        self,
        candidate: str,
        allowed_api_calls: set[str],
        operation_tokens: set[str],
    ) -> bool:
        if not operation_tokens:
            return False
        candidate_ops = self._operation_tokens(candidate)
        if not (candidate_ops & operation_tokens):
            return False
        allowed_ops = set().union(*(self._operation_tokens(name) for name in allowed_api_calls)) if allowed_api_calls else set()
        if allowed_ops & candidate_ops:
            return False
        if self._api_family(candidate) not in {self._api_family(name) for name in allowed_api_calls}:
            return False
        return True

    def _api_family(self, name: str) -> str:
        parts = name.split("_")
        return "_".join(parts[:2]) if len(parts) >= 2 else name

    def _assertion_expressions(self, body: str) -> list[str]:
        return re.findall(r"\b(?:test_assert[A-Za-z0-9_]*|assert)\s*\((.*?)\)\s*;", body, re.S)

    def _field_tokens(self, text: str) -> list[str]:
        return sorted(set(re.findall(r"(?:->|\.)\s*([A-Za-z_][A-Za-z0-9_]*)", text)))

    def _constant_tokens(self, text: str) -> list[str]:
        return sorted(set(re.findall(r"(?<![A-Za-z0-9_])_?[A-Z][A-Z0-9_]{2,}(?![A-Za-z0-9_])", text)))

    def _representative_literals(self, body: str) -> list[str]:
        literals = []
        for value in re.findall(r'"([^"\n]{1,64})"', body):
            if "%" in value or not re.search(r"[A-Za-z0-9]", value):
                continue
            if re.fullmatch(r"[A-Za-z0-9_./:-]+", value):
                _append_unique(literals, value)
        return literals[:32]

    def _derive_api_symbols(self, source_to_rust: dict[str, list[str]]) -> dict[str, list[str]]:
        modules = sorted({Path(module).stem for values in source_to_rust.values() for module in values if module.startswith("src/")})
        return {"src/lib.rs": [f"pub mod {module};" for module in modules if module != "lib"]}

    def _derive_public_api_source_modules(
        self,
        source: Path,
        src_files: list[str],
        public_apis: list[str],
        source_to_rust: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        api_set = set(public_apis)
        mapping: dict[str, list[str]] = {}
        for relative in src_files:
            text = read_text(source / relative)
            for api in api_set:
                if not self._has_function_definition(text, api):
                    continue
                for module in source_to_rust.get(relative, []):
                    if module.startswith("src/"):
                        _append_unique(mapping.setdefault(api, []), module)
        return {api: modules for api, modules in sorted(mapping.items())}

    def _has_function_definition(self, text: str, name: str) -> bool:
        pattern = rf"(?:^|\n)\s*(?:static\s+)?[A-Za-z_][\w\s\*]*\s+{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{"
        return bool(re.search(pattern, text, re.M))

    def _derive_api_equivalent_prefixes(self, public_apis: list[str]) -> list[str]:
        prefix_counts: dict[str, int] = {}
        for symbol in public_apis:
            parts = [part for part in str(symbol).split("_") if part]
            for index in range(1, len(parts)):
                prefix = "_".join(parts[:index]) + "_"
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        return sorted(
            (prefix for prefix, count in prefix_counts.items() if count >= 2),
            key=lambda item: (-len(item), item),
        )[:32]

    def _collect_source_test_runs(self, source: Path, profile: dict[str, Any]) -> dict[str, list[str]]:
        return {
            name: self._test_runs_from_file(source / spec["source"])
            for name, spec in profile.get("test_suites", {}).items()
        }

    def _derive_internal_parity_anchors(self, source: Path, src_files: list[str]) -> dict[str, list[str]]:
        prefixes = _as_list(self.profile.get("internal_anchor_prefixes"))
        anchors: dict[str, list[str]] = {}
        for relative in src_files:
            path = source / relative
            text = read_text(path)
            names = _extract_function_names(text, prefixes)
            if names:
                anchors[relative] = names
        return anchors

    def _test_runs_from_file(self, path: Path) -> list[str]:
        pattern = _layout(self.profile).get("test_run_pattern")
        if not pattern or not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
        return re.findall(str(pattern), text)

    def _collect_public_apis(self, header: Path) -> list[str]:
        pattern = _layout(self.profile).get("public_api_pattern")
        if not pattern or not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(str(pattern), text)))

    def _collect_internal_anchors(self, source: Path, profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
        anchors: dict[str, dict[str, Any]] = {}
        search_dirs = _as_list(_layout(profile).get("anchor_search_dirs")) or _as_list(_layout(profile).get("source_dirs"))
        for relative, expected in profile.get("internal_parity_anchors", {}).items():
            path = self._resolve_source_path(source, relative, search_dirs)
            text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            expected_tokens = _as_list(expected)
            anchors[relative] = {
                "path": str(path),
                "exists": path.exists(),
                "expected": expected_tokens,
                "present": [name for name in expected_tokens if name in text],
                "missing_in_source": [name for name in expected_tokens if name not in text],
            }
        return anchors

    def _resolve_source_path(self, source: Path, relative: str, search_dirs: list[str]) -> Path:
        direct = source / relative
        if direct.exists() or "/" in relative or "\\" in relative:
            return direct
        for subdir in search_dirs:
            candidate = source / subdir / relative
            if candidate.exists():
                return candidate
        return source / (search_dirs[0] if search_dirs else "") / relative

    def _dependency_markdown(
        self,
        ctx: ConversionContext,
        src_files: list[str],
        test_files: list[str],
        include_files: list[str],
    ) -> str:
        components = ctx.analysis.get("components", {})
        source_runs = ctx.analysis.get("source_test_runs", {})
        component_lines = "\n".join(f"- {name}: {len(paths)} 个文件" for name, paths in components.items()) or "- 无"
        test_lines = "\n".join(f"- {name}: {len(entries)} 个源码测试项" for name, entries in source_runs.items()) or "- 无"
        return f"""
        # 项目分析

        Profile：`{_profile_name(self.profile)}`
        源码路径：`{ctx.source}`

        - 源码文件：{len(src_files)}
        - 测试文件：{len(test_files)}
        - 头文件/包含文件：{len(include_files)}
        - 公共 API 项：{len(ctx.analysis.get("public_apis", []))}
        - 内部 parity 锚点组：{len(ctx.analysis.get("internal_parity_anchors", {}))}

        ## 组件分组

        {component_lines}

        ## 源码测试项

        {test_lines}
        """
