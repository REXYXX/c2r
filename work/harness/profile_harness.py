#!/usr/bin/env python3
"""动态 profile 驱动的 C 到 Rust 转换执行框架阶段。

markdown profile 只作为人工覆盖层；源码布局、测试映射、benchmark、公共 API
和 parity 锚点优先由源码分析阶段实时生成。
"""

from __future__ import annotations

from collections import Counter
import copy
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generic_harness import (
    ConversionContext,
    CompileStage,
    ConstraintLoadingStage,
    HarnessStage,
    OutputScaffoldStage,
    RepairStage,
    check_artifact_structure,
    check_required_files,
    check_token_map,
    count_token_in_rust,
    read_text,
    run_cargo,
    text_block,
    write,
)
from model_artifacts import (
    compact_context_index,
    display_name,
    generate_model_brief,
    generate_report,
    generate_workspace_scaffold,
    list_relative,
    slug,
    write_context_shards,
)


CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
}

SOURCE_CONTEXT_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".inc"}
TEST_CONTEXT_SUFFIXES = SOURCE_CONTEXT_SUFFIXES | {".md"}
INCLUDE_CONTEXT_SUFFIXES = {".h", ".hpp", ".hh", ".inc"}


def _profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("profile") or profile.get("name") or "project")


def _layout(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("source_layout", {})


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _merge_missing(base: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    """把自动发现结果补到 profile 中，profile 中已有的人工约束优先。"""
    merged = copy.deepcopy(base)
    for key, value in derived.items():
        if value in (None, [], {}):
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_missing(current, value)
        elif current in (None, [], {}):
            merged[key] = copy.deepcopy(value)
    return merged


def _effective_profile(ctx: ConversionContext, profile: dict[str, Any]) -> dict[str, Any]:
    effective = ctx.analysis.get("effective_profile")
    if isinstance(effective, dict):
        return effective
    return profile


def _reset_profile_trace(ctx: ConversionContext) -> None:
    setattr(ctx, "_profile_harness_trace", [])


def _profile_trace_entries(ctx: ConversionContext) -> list[dict[str, Any]]:
    trace = getattr(ctx, "_profile_harness_trace", None)
    if isinstance(trace, list):
        return trace
    trace = []
    setattr(ctx, "_profile_harness_trace", trace)
    return trace


def _profile_trace_summary(entry: dict[str, Any]) -> str:
    details = {
        key: value
        for key, value in entry.items()
        if key not in {"index", "time", "stage", "action"}
    }
    if not details:
        return ""
    summary = json.dumps(details, ensure_ascii=False)
    return summary if len(summary) <= 240 else summary[:237] + "..."


def _profile_trace_markdown(payload: dict[str, Any]) -> str:
    rows = [
        f"| {entry['index']} | `{entry['stage']}` | `{entry['action']}` | {_profile_trace_summary(entry)} |"
        for entry in payload["path"]
    ]
    return "\n".join(
        [
            "# Profile Harness 执行路径",
            "",
            f"- profile: `{payload['profile']}`",
            f"- source: `{payload['source']}`",
            f"- out: `{payload['out']}`",
            f"- result: `{payload['result']}`",
            "",
            "| Step | 节点 | 动作 | 关键数据 |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
        ]
    )


def _record_profile_trace(ctx: ConversionContext, stage: str, action: str, **data: Any) -> None:
    trace = _profile_trace_entries(ctx)
    trace.append(
        {
            "index": len(trace) + 1,
            "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "action": action,
            **data,
        }
    )
    payload = {
        "profile": ctx.profile,
        "source": str(ctx.source),
        "out": str(ctx.out),
        "result": str(ctx.result),
        "logs": str(ctx.logs),
        "path": trace,
    }
    write(ctx.trace_artifact("profile-harness-path.json"), json.dumps(payload, indent=2, ensure_ascii=False))
    write(ctx.trace_artifact("profile-harness-path.md"), _profile_trace_markdown(payload))


def _strip_module_prefix(stem: str, profile: dict[str, Any]) -> str:
    for prefix in _as_list(profile.get("module_name_strip_prefixes")):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _snake_name(value: str, profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}
    name = Path(value).stem.lower()
    name = _strip_module_prefix(name, profile)
    name = re.sub(r"_tc$", "", name)
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_") or "tests"


def _module_for_source(relative: str, profile: dict[str, Any]) -> list[str]:
    rules = profile.get("source_module_rules", {})
    filename = Path(relative).name.lower()
    for token, modules in rules.items():
        if str(token).lower() in filename:
            return _as_list(modules)
    stem = Path(relative).stem.lower()
    stem = _strip_module_prefix(stem, profile)
    stem = re.sub(r"_tc$", "", stem)
    return [f"src/{stem}.rs"]


def _group_public_apis(symbols: list[str], profile: dict[str, Any]) -> dict[str, list[str]]:
    groups = {str(name): [] for name in profile.get("api_group_prefixes", {})}
    fallback = "public_api"
    for symbol in symbols:
        target = None
        for group, prefixes in profile.get("api_group_prefixes", {}).items():
            if any(symbol.startswith(str(prefix)) for prefix in _as_list(prefixes)):
                target = str(group)
                break
        groups.setdefault(target or fallback, []).append(symbol)
    return {group: sorted(values) for group, values in groups.items() if values}


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _parse_define_constants(text: str) -> dict[str, int]:
    constants: dict[str, int] = {}
    for name, value in re.findall(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([0-9]+)\b", text, re.M):
        constants[name] = int(value)
    return constants


def _extract_function_names(text: str, prefixes: list[str]) -> list[str]:
    pattern = r"(?:^|\n)\s*(?:static\s+)?[A-Za-z_][\w\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
    names = []
    for name in re.findall(pattern, text, re.M):
        if name in CONTROL_KEYWORDS:
            continue
        if not prefixes or any(name.startswith(prefix) for prefix in prefixes):
            names.append(name)

    return sorted(set(names))


def _extract_c_function_body(text: str, name: str) -> str:
    pattern = rf"(?:^|\n)\s*(?:static\s+)?[A-Za-z_][\w\s\*]*\s+{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{"
    match = re.search(pattern, text, re.M)
    if match is None:
        return ""
    return _extract_braced_body(text, match.end() - 1)


def _extract_rust_function_body(text: str, name: str) -> str:
    pattern = rf"(?:^|\n)\s*(?:#\[[^\]]+\]\s*)*(?:pub\s+)?fn\s+{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{"
    match = re.search(pattern, text, re.M)
    if match is None:
        return ""
    return _extract_braced_body(text, match.end() - 1)


def _extract_braced_body(text: str, open_brace: int) -> str:
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : index]
    return ""


def _duplicate_lookup(profile: dict[str, Any]) -> dict[tuple[str, str, int], str]:
    return {
        (str(item["suite"]), str(item["source"]), int(item["occurrence"])): str(item["target"])
        for item in profile.get("duplicate_test_name_map", [])
    }


def _test_runs_from_path(path: Path, pattern: str | None) -> list[str]:
    if not pattern or not path.exists():
        return []
    return re.findall(str(pattern), path.read_text(encoding="utf-8", errors="ignore"))


def _derive_readme_tests(source: Path, profile: dict[str, Any], test_suites: dict[str, Any]) -> dict[str, Any]:
    spec = profile.get("readme_test_discovery", {})
    readme = source / str(spec.get("source", "tests/README_test.md"))
    required: dict[str, list[str]] = {}
    if readme.exists():
        text = readme.read_text(encoding="utf-8", errors="ignore")
        names = re.findall(r"\b(test_[A-Za-z0-9_]+)\b", text)
        pattern = _layout(profile).get("test_run_pattern")
        duplicate_names = _duplicate_lookup(profile)
        candidates_by_name: dict[str, list[tuple[str, str]]] = {}
        for suite, item in test_suites.items():
            target = str(item.get("target", ""))
            counts: dict[str, int] = {}
            for raw in _test_runs_from_path(source / str(item.get("source", "")), pattern):
                counts[raw] = counts.get(raw, 0) + 1
                mapped = duplicate_names.get((suite, raw, counts[raw]), raw)
                candidates_by_name.setdefault(raw, []).append((target, mapped))
        seen_occurrences: dict[str, int] = {}
        for raw in names:
            candidates = candidates_by_name.get(raw, [])
            if candidates:
                index = seen_occurrences.get(raw, 0)
                target, rust_test = candidates[min(index, len(candidates) - 1)]
                seen_occurrences[raw] = index + 1
            else:
                target, rust_test = _fallback_test_target(raw, test_suites), raw
            if target:
                required.setdefault(str(target), [])
                _append_unique(required[str(target)], rust_test)
    return {
        "source": str(spec.get("source", "tests/README_test.md")),
        "required_rust_tests": required,
    }


def _fallback_test_target(name: str, test_suites: dict[str, Any]) -> str:
    for suite, item in test_suites.items():
        if suite and suite in name:
            return str(item.get("target", ""))
    if test_suites:
        return str(next(iter(test_suites.values())).get("target", ""))
    return ""


def _derive_benchmark(source: Path, profile: dict[str, Any]) -> dict[str, Any]:
    spec = profile.get("benchmark_discovery", {})
    if not spec:
        return {}
    bench_source = str(spec.get("source", "tests/benchmark/bench_main.c"))
    bench_config = str(spec.get("config", "tests/benchmark/fdb_cfg.h"))
    rust_target = str(spec.get("rust_target", "tests/benchmark_tests.rs"))
    config_text = read_text(source / bench_config) if bench_config else ""
    bench_text = read_text(source / bench_source)
    constants = _parse_define_constants(config_text)
    constants.update(_parse_define_constants(bench_text))
    operations: list[str] = []
    if spec.get("operation_tokens"):
        for token, rust_test in spec.get("operation_tokens", {}).items():
            if str(token) in bench_text:
                _append_unique(operations, str(rust_test))
    else:
        for rust_test in _benchmark_tests_from_source(bench_text, spec):
            _append_unique(operations, rust_test)
    for rust_test in spec.get("extra_tests", []):
        _append_unique(operations, str(rust_test))
    return {
        "benchmark": {
            "source": bench_source,
            "config": bench_config,
            "rust_target": rust_target,
            "constants": constants,
            "operation_tests": operations,
            "semantic_requirements": spec.get("semantic_requirements", []),
        },
        "required_rust_tests": {rust_target: operations} if operations else {},
    }


def _benchmark_tests_from_source(text: str, spec: dict[str, Any]) -> list[str]:
    prefix = str(spec.get("operation_prefix", "bench_"))
    excluded = {str(item) for item in spec.get("excluded_functions", [])}
    functions = re.findall(
        r"(?:^|\n)\s*(?:static\s+)?[A-Za-z_][\w\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{",
        text,
    )
    main_match = re.search(r"\bmain\s*\([^)]*\)\s*\{(?P<body>.*)\n\}", text, re.S)
    main_body = main_match.group("body") if main_match else text
    ordered = sorted(
        (name for name in functions if name.startswith(prefix) and name not in excluded and f"{name}(" in main_body),
        key=lambda name: main_body.find(f"{name}("),
    )
    return [f"test_benchmark_{name[len(prefix):]}" for name in ordered]


def _merge_readme_coverage(unit_coverage: dict[str, Any], benchmark_coverage: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(unit_coverage)
    merged.setdefault("required_rust_tests", {})
    for target, tests in benchmark_coverage.get("required_rust_tests", {}).items():
        merged["required_rust_tests"].setdefault(target, [])
        for test in tests:
            _append_unique(merged["required_rust_tests"][target], test)
    if benchmark_coverage.get("benchmark"):
        merged["benchmark"] = benchmark_coverage["benchmark"]
    return merged


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
                "main_thread": "result/harness/agent-entry/main-thread.json",
                "code_agent": "result/harness/agent-entry/code-agent.json",
                "test_agent": "result/harness/agent-entry/test-agent.json",
                "validation_agent": "result/harness/agent-entry/validation-agent.json",
            },
            "compact_artifacts": {
                "code_plan": "result/harness/code-plan.json",
                "code_manifest": "result/harness/code-manifest.json",
                "context_manifest": "result/harness/context/manifest.json",
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

            ## Compact Entrypoints

            {json.dumps(summary.get("compact_artifacts", {}), ensure_ascii=False, indent=2)}
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
        function_tokens = self._derive_function_hint_tokens(source)
        return {
            "test_suites": test_suites,
            "duplicate_test_name_map": duplicate_test_name_map,
            "readme_test_coverage": readme_coverage,
            "test_semantic_requirements": test_semantic_requirements,
            "c_api_parity_symbols": c_api_parity,
            "c_api_parity_modules": self._derive_c_api_parity_modules(source_to_rust, c_api_parity),
            "internal_parity_anchors": self._derive_internal_parity_anchors(source, src_files),
            "source_to_rust_modules": source_to_rust,
            "function_hint_tokens": function_tokens,
            "required_output_files": self._derive_required_output_files(source_to_rust, test_suites, readme_coverage),
            "api_symbols": self._derive_api_symbols(source_to_rust),
            "module_contexts": self._derive_module_contexts(source_to_rust),
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
                "rule": "断言类型和期望行为等价，例如 FDB_NO_ERR 可映射为 Rust 的 is_ok()。",
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

    def _derive_module_contexts(self, source_to_rust: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        for source_file, modules in source_to_rust.items():
            for module in modules:
                name = Path(module).stem
                contexts.setdefault(
                    name,
                    {
                        "component_key": name,
                        "target": module,
                        "required_mechanisms": [],
                    },
                )
                _append_unique(contexts[name]["required_mechanisms"], f"迁移 `{source_file}` 中的可观察语义和边界条件")
        return contexts

    def _collect_source_test_runs(self, source: Path, profile: dict[str, Any]) -> dict[str, list[str]]:
        return {
            name: self._test_runs_from_file(source / spec["source"])
            for name, spec in profile.get("test_suites", {}).items()
        }

    def _derive_function_hint_tokens(self, source: Path) -> list[str]:
        prefixes = _as_list(self.profile.get("function_name_prefixes"))
        tokens: set[str] = set(prefixes)
        roots = _as_list(_layout(self.profile).get("source_dirs")) + _as_list(_layout(self.profile).get("include_dirs"))
        globs = _as_list(_layout(self.profile).get("function_hint_globs")) or ["*.c", "*.h"]
        for subdir in roots:
            base = source / subdir
            if not base.exists():
                continue
            for glob in globs:
                for path in base.rglob(glob):
                    text = read_text(path)
                    tokens.update(_extract_function_names(text, prefixes))
        return sorted(tokens)

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


class ProfileSkeletonGenerationStage(HarnessStage):
    name = "SkeletonGenerationStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        effective = _effective_profile(ctx, self.profile)
        generate_workspace_scaffold(ctx.out, effective)
        write(
            ctx.artifact("02-skeleton.md"),
            f"""
            # 骨架生成

            已在 `{ctx.out}` 准备 Rust crate 工作区。

            Python 只创建目录和 Cargo 清单骨架。模型必须基于动态生成的
            effective profile、规范文档和源码编写 `src/*.rs` 与 `tests/*.rs`。
            """,
        )
        _record_profile_trace(
            ctx,
            self.name,
            "generate_workspace_scaffold",
            crate_name=effective.get("crate_name"),
            out=str(ctx.out),
            outputs=["Cargo.toml", "src/", "tests/"],
        )


class ProfileContextBuilderStage(HarnessStage):
    name = "ContextBuilderStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        module_contexts: dict[str, Any] = {}
        effective = _effective_profile(ctx, self.profile)
        for name, spec in effective.get("module_contexts", {}).items():
            component_key = spec.get("component_key")
            module_contexts[name] = {
                "source_hints": ctx.analysis.get("components", {}).get(component_key, []),
                "target": spec.get("target"),
                "required_mechanisms": spec.get("required_mechanisms", []),
            }
        ctx.context_index = {
            "module_contexts": module_contexts,
            "function_hints": self._collect_function_hints(ctx.source, effective),
            "public_apis": ctx.analysis.get("public_apis", []),
            "internal_parity_anchors": ctx.analysis.get("internal_parity_anchors", {}),
        }
        context_manifest = write_context_shards(ctx.result, ctx.context_index)
        write(ctx.artifact("03-context.json"), json.dumps(compact_context_index(ctx.context_index, context_manifest), indent=2, ensure_ascii=False))
        _record_profile_trace(
            ctx,
            self.name,
            "build_context_index",
            module_contexts=len(module_contexts),
            function_hints=len(ctx.context_index.get("function_hints", [])),
            public_apis=len(ctx.context_index.get("public_apis", [])),
            internal_anchor_groups=len(ctx.context_index.get("internal_parity_anchors", {})),
            output="result/harness/context/manifest.json",
        )

    def _collect_function_hints(self, source: Path, profile: dict[str, Any]) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        if not source.exists():
            return hints
        layout = _layout(profile)
        roots = _as_list(layout.get("source_dirs")) + _as_list(layout.get("include_dirs")) + _as_list(layout.get("test_dirs"))
        globs = _as_list(layout.get("function_hint_globs")) or ["*.c", "*.h"]
        seen: set[Path] = set()
        paths: list[Path] = []
        for subdir in roots:
            base = source / subdir
            if not base.exists():
                continue
            for glob in globs:
                for path in sorted(base.rglob(glob)):
                    if path.is_file() and path not in seen:
                        paths.append(path)
                        seen.add(path)
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines = text.splitlines()
            for token in profile.get("function_hint_tokens", []):
                token_text = str(token)
                if token_text not in text:
                    continue
                definition_line = self._function_definition_line(lines, token_text)
                line_numbers = [definition_line] if definition_line else [idx + 1 for idx, line in enumerate(lines) if token_text in line]
                first_line = line_numbers[0] if line_numbers else None
                excerpt = self._line_excerpt(lines, first_line) if first_line else []
                hints.append(
                    {
                        "file": str(path.relative_to(source)).replace("\\", "/"),
                        "symbol_prefix": token_text,
                        "kind": "definition" if definition_line else "reference",
                        "line": first_line,
                        "excerpt": excerpt,
                    }
                )
        return hints

    def _function_definition_line(self, lines: list[str], token: str) -> int | None:
        pattern = re.compile(rf"\b{re.escape(token)}\s*\(")
        for index, line in enumerate(lines):
            if not pattern.search(line):
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//", "*")):
                continue
            prefix = stripped.split(token, 1)[0].strip()
            if prefix.startswith(("return", "if", "while", "for", "switch")):
                continue
            if any(operator in prefix for operator in ("=", ",", "&&", "||")):
                continue
            window = " ".join(part.strip() for part in lines[index : min(len(lines), index + 4)])
            if ";" in window.split("{", 1)[0]:
                continue
            if "{" in window or index + 1 < len(lines):
                return index + 1
        return None

    def _line_excerpt(self, lines: list[str], line_number: int | None, radius: int = 3) -> list[str]:
        if line_number is None:
            return []
        start = max(1, line_number - radius)
        end = min(len(lines), line_number + radius)
        return [f"{idx}: {lines[idx - 1].strip()[:160]}" for idx in range(start, end + 1)]


class ProfileParityMatrixStage(HarnessStage):
    name = "ParityMatrixStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        effective = _effective_profile(ctx, self.profile)
        layout = _layout(effective)
        header = ctx.source / str(layout.get("public_api_header", ""))
        public_apis = self._public_apis(header, effective)
        parity = {
            "profile": _profile_name(self.profile),
            "public_apis": public_apis,
            "expected_public_apis": effective.get("c_api_parity_symbols", {}),
            "expected_one_to_one_features": effective.get("one_to_one_features", {}),
            "source_to_rust_modules": effective.get("source_to_rust_modules", {}),
            "notes": [
                "该矩阵来自源码实时分析合并可选 markdown 覆盖项。",
                "生成的 Rust 缺少必需公共 API parity 名称或动态 profile 特性时，验证会失败。",
                "即使高层测试通过，profile 定义的行为捷径拦截规则仍会执行。",
            ],
        }
        write(ctx.artifact("04-function-parity.json"), json.dumps(parity, indent=2, ensure_ascii=False))
        _record_profile_trace(
            ctx,
            self.name,
            "build_parity_matrix",
            public_apis=len(public_apis),
            expected_api_groups=len(parity["expected_public_apis"]),
            source_mappings=len(parity["source_to_rust_modules"]),
            output="result/harness/04-function-parity.json",
        )

    def _public_apis(self, header: Path, profile: dict[str, Any]) -> list[str]:
        pattern = _layout(profile).get("public_api_pattern")
        if not pattern or not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="ignore")
        return sorted(set(re.findall(str(pattern), text)))


class ProfileTranslationStage(HarnessStage):
    name = "TranslationStage"

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def run(self, ctx: ConversionContext) -> None:
        generate_model_brief(ctx.root, ctx.source, ctx.out, ctx.result, ctx.logs, _effective_profile(ctx, self.profile), ctx.analysis, ctx.context_index)
        write(
            ctx.artifact("04-translation.md"),
            f"""
            # 模型引导翻译

            执行框架已从 `work/agents/` 固定中文模板渲染出面向 Main Thread、
            Code Agent、Test Agent 和 Validation Agent 的分工指引，而不是
            从 Python 写入 Rust 实现或内联长篇 agent 提示词。

            - Agent 固定模板目录：`{ctx.root / "work" / "agents"}`
            - 主线程编排任务：`{ctx.out / "MAIN_THREAD_TASK.md"}`
            - Code Agent 实现任务：`{ctx.out / "MODEL_TASK.md"}`
            - Test Agent 测试任务：`{ctx.out / "TEST_AGENT_TASK.md"}`
            - Validation Agent 验证任务：`{ctx.out / "VALIDATION_AGENT_TASK.md"}`
            - Agent entry manifest：`{ctx.result / "harness" / "agent-entry" / "manifest.json"}`
            - Main Thread entry：`{ctx.result / "harness" / "agent-entry" / "main-thread.json"}`
            - Code Agent plan：`{ctx.result / "harness" / "code-plan.json"}`
            - Code Agent manifest：`{ctx.result / "harness" / "code-manifest.json"}`
            - Context manifest：`{ctx.result / "harness" / "context" / "manifest.json"}`
            - Test requirement manifest：`{ctx.result / "harness" / "test-requirements" / "manifest.json"}`
            - 执行框架主任务书：`{ctx.result / "harness" / "04-model-generation-brief.md"}`
            - parity 矩阵：`{ctx.result / "harness" / "04-function-parity.json"}`

            Main Thread 只做编排，不读取 C 源码或 Rust `src/tests`；
            Code Agent 实现 `src/*.rs`；Test Agent 生成 `tests/*.rs`；
            Validation Agent 运行 strict 验证并返回压缩失败摘要。
            全量 analysis/profile 文件默认不生成；Agent 只读取 plan、manifest、
            compact shard 或压缩修复上下文，不展开完整测试语义矩阵。
            """,
        )
        _record_profile_trace(
            ctx,
            self.name,
            "generate_model_and_subagent_briefs",
            inputs=[
                "work/agents/main-thread.md",
                "work/agents/code-agent.md",
                "work/agents/test-agent.md",
                "work/agents/validation-agent.md",
            ],
            outputs=[
                "out/MAIN_THREAD_TASK.md",
                "out/MODEL_TASK.md",
                "out/TEST_AGENT_TASK.md",
                "out/VALIDATION_AGENT_TASK.md",
                "result/harness/agent-entry/manifest.json",
                "result/harness/agent-entry/main-thread.json",
                "result/harness/agent-entry/code-agent.json",
                "result/harness/agent-entry/test-agent.json",
                "result/harness/agent-entry/validation-agent.json",
                "result/harness/code-plan.json",
                "result/harness/code-manifest.json",
                "result/harness/context/manifest.json",
                "result/harness/test-requirements/manifest.json",
                "result/harness/04-main-thread-task.md",
                "result/harness/04-model-generation-brief.md",
                "result/harness/04-test-agent-task.md",
                "result/harness/04-validation-agent-task.md",
                "result/harness/04-translation.md",
                "result/harness/04-function-parity.json",
            ],
        )


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
        compressed_repair_context = self._write_repair_context(ctx, failures, repair_required, cargo_test)
        ctx.validation_result = {
            "status": "failed" if failures else "passed",
            "failures": failures,
            "repair_required": repair_required,
            "compressed_repair_context": compressed_repair_context,
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
            repair_context="result/harness/08-repair-context/manifest.json",
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
            "compressed_context": "result/harness/08-repair-context/manifest.json",
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

    def _write_repair_context(
        self,
        ctx: ConversionContext,
        failures: list[str],
        repair_required: dict[str, Any],
        cargo_test: dict[str, Any],
    ) -> dict[str, Any]:
        base = ctx.result / "harness" / "08-repair-context"
        base.mkdir(parents=True, exist_ok=True)
        agents = ("code_agent", "test_agent", "validation_agent")
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        route_files: dict[str, str] = {}
        route_counts: dict[str, int] = {}
        for agent in agents:
            raw_items = [str(item) for item in repair_required.get(agent, [])]
            items = [self._repair_item(agent, idx + 1, failure) for idx, failure in enumerate(raw_items)]
            route_counts[agent] = len(items)
            json_name = f"{agent.replace('_', '-')}.json"
            md_name = f"{agent.replace('_', '-')}.md"
            route_files[agent] = f"result/harness/08-repair-context/{json_name}"
            write(
                base / json_name,
                json.dumps(
                    {
                        "agent": agent,
                        "generated_at": generated_at,
                        "status": "empty" if not items else "repair_required",
                        "item_count": len(items),
                        "items": items,
                        "rules": [
                            "只读取本文件和每个 item 的 recommended_reads。",
                            "不要读取完整 result/harness/07-validation.json，除非压缩上下文明确要求。",
                            "修复后重新运行 strict 验证，由 harness 生成下一轮 repair-context。",
                        ],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            write(base / md_name, self._repair_context_markdown(agent, items))
        next_agent = (
            "code_agent"
            if route_counts["code_agent"]
            else "test_agent"
            if route_counts["test_agent"]
            else "validation_agent"
            if route_counts["validation_agent"]
            else "none"
        )
        manifest = {
            "description": "自动压缩的多轮修复上下文入口；agent 修复时优先读取本 manifest，再读取对应 agent shard。",
            "generated_at": generated_at,
            "status": "passed" if not failures else "repair_required",
            "source": str(ctx.source),
            "out": str(ctx.out),
            "next_agent": next_agent,
            "counts": route_counts,
            "routes": route_files,
            "markdown_routes": {
                agent: f"result/harness/08-repair-context/{agent.replace('_', '-')}.md"
                for agent in agents
            },
            "cargo_test": {
                "status": cargo_test.get("status"),
                "reason": cargo_test.get("reason"),
                "diagnostics": "result/harness/07-validation.json#cargo_test" if cargo_test.get("status") == "failed" else None,
            },
            "rules": [
                "多轮修复禁止回灌完整 07-validation.json、完整测试矩阵或完整源码上下文。",
                "Code Agent 只读 code-agent shard 及其中 recommended_reads。",
                "Test Agent 只读 test-agent shard、目标 target manifest 和单个 semantic shard。",
                "Validation Agent 只读 validation-agent shard 和必要 cargo 诊断尾部。",
            ],
        }
        write(base / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        write(base / "summary.md", self._repair_manifest_markdown(manifest))
        return {
            "manifest": "result/harness/08-repair-context/manifest.json",
            "summary": "result/harness/08-repair-context/summary.md",
            "next_agent": next_agent,
            "counts": route_counts,
        }

    def _repair_item(self, agent: str, index: int, failure: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": f"{agent.replace('_', '-')}-{index:03d}",
            "agent": agent,
            "summary": self._compact_failure_text(failure),
            "failure_excerpt": failure[:900],
            "recommended_reads": [],
        }
        semantic = re.match(r"missing semantic coverage in ([^:]+)::([^:]+):\s*(.*)", failure)
        if semantic:
            target = semantic.group(1)
            test_name = semantic.group(2)
            detail = semantic.group(3)
            target_slug = slug(target)
            test_slug = slug(test_name)
            item.update(
                {
                    "category": "test_semantic_coverage",
                    "target_file": target,
                    "test_name": test_name,
                    "detail": self._compact_problem_detail(detail),
                    "target_manifest": f"result/harness/test-requirements/{target_slug}.json",
                    "semantic_shard": f"result/harness/test-requirements/{target_slug}/{test_slug}.json",
                    "recommended_reads": [
                        f"result/harness/test-requirements/{target_slug}.json",
                        f"result/harness/test-requirements/{target_slug}/{test_slug}.json",
                    ],
                }
            )
            return item
        readme = re.match(r"missing README/benchmark coverage in ([^:]+):\s*(.*)", failure)
        if readme:
            target = readme.group(1)
            target_slug = slug(target)
            item.update(
                {
                    "category": "readme_or_benchmark_coverage",
                    "target_file": target,
                    "detail": self._compact_problem_detail(readme.group(2)),
                    "target_manifest": f"result/harness/test-requirements/{target_slug}.json",
                    "recommended_reads": [f"result/harness/test-requirements/{target_slug}.json"],
                }
            )
            return item
        translated = re.match(r"missing translated ([A-Z0-9_]+) tests:\s*(.*)", failure)
        if translated:
            item.update(
                {
                    "category": "missing_translated_tests",
                    "suite": translated.group(1).lower(),
                    "detail": self._compact_problem_detail(translated.group(2)),
                    "recommended_reads": ["result/harness/test-requirements/manifest.json"],
                }
            )
            return item
        if failure.startswith("cargo test skipped"):
            item.update(
                {
                    "category": "validation_environment",
                    "detail": failure,
                    "recommended_reads": ["result/harness/08-repair-context/validation-agent.json"],
                }
            )
            return item
        if failure == "cargo test failed":
            item.update(
                {
                    "category": "cargo_test_failed",
                    "detail": "读取 cargo_test 的 stdout/stderr 尾部后再按 src/tests 归类。",
                    "recommended_reads": ["result/harness/07-validation.json"],
                }
            )
            return item
        generated = re.match(r"missing generated file:\s*(.*)", failure)
        if generated:
            item.update(
                {
                    "category": "missing_generated_file",
                    "target_file": generated.group(1),
                    "recommended_reads": ["result/harness/code-manifest.json"],
                }
            )
            return item
        api = re.match(r"missing API symbols in ([^:]+):\s*(.*)", failure)
        if api:
            item.update(
                {
                    "category": "missing_api_symbols",
                    "target_file": api.group(1),
                    "detail": self._compact_problem_detail(api.group(2)),
                    "recommended_reads": ["result/harness/code-manifest.json"],
                }
            )
            return item
        item["category"] = "generic"
        return item

    def _compact_problem_detail(self, detail: str) -> list[str]:
        parts = [part.strip() for part in detail.split(";") if part.strip()]
        if not parts:
            return []
        return [self._compact_failure_text(part) for part in parts[:8]]

    def _compact_failure_text(self, text: str, max_tokens: int = 18, max_chars: int = 240) -> str:
        if len(text) <= max_chars:
            return text
        tokens = [token.strip() for token in re.split(r",\s*", text) if token.strip()]
        if len(tokens) > max_tokens:
            return ", ".join(tokens[:max_tokens]) + f", ... (+{len(tokens) - max_tokens} more; read referenced shard)"
        return text[: max_chars - 3] + "..."

    def _repair_context_markdown(self, agent: str, items: list[dict[str, Any]]) -> str:
        title = agent.replace("_", " ").title()
        if not items:
            return f"# {title} 压缩修复上下文\n\n无待修复项。\n"
        lines = [
            f"# {title} 压缩修复上下文",
            "",
            "只读取本文件和每项 `recommended_reads` 指向的 shard；不要读取完整 `07-validation.json`。",
            "",
        ]
        for item in items:
            lines.extend(
                [
                    f"## {item['id']}",
                    "",
                    f"- 类别：`{item.get('category', 'generic')}`",
                    f"- 摘要：{item.get('summary', '')}",
                ]
            )
            if item.get("target_file"):
                lines.append(f"- 文件：`{item['target_file']}`")
            if item.get("test_name"):
                lines.append(f"- 测试：`{item['test_name']}`")
            if item.get("detail"):
                detail = item["detail"]
                if isinstance(detail, list):
                    lines.extend(f"- 细节：{part}" for part in detail)
                else:
                    lines.append(f"- 细节：{detail}")
            reads = item.get("recommended_reads", [])
            if reads:
                lines.append("- 必读 shard：" + ", ".join(f"`{path}`" for path in reads))
            lines.append("")
        return "\n".join(lines)

    def _repair_manifest_markdown(self, manifest: dict[str, Any]) -> str:
        counts = manifest.get("counts", {})
        routes = manifest.get("markdown_routes", {})
        return "\n".join(
            [
                "# 压缩修复上下文",
                "",
                f"- 状态：`{manifest.get('status')}`",
                f"- 下一 agent：`{manifest.get('next_agent')}`",
                f"- Code Agent 待修复：{counts.get('code_agent', 0)}",
                f"- Test Agent 待修复：{counts.get('test_agent', 0)}",
                f"- Validation Agent 待处理：{counts.get('validation_agent', 0)}",
                "",
                "## 路由",
                "",
                f"- Code Agent：`{routes.get('code_agent')}`",
                f"- Test Agent：`{routes.get('test_agent')}`",
                f"- Validation Agent：`{routes.get('validation_agent')}`",
                "",
                "修复轮次只读对应路由文件及其 recommended_reads，避免上下文持续膨胀。",
                "",
            ]
        )

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
            - ContextBuilderStage：生成模块/函数上下文。
            - ParityMatrixStage：生成 profile 提供的 parity 矩阵。
            - TranslationStage：从 work/agents/ 固定模板渲染 Main Thread、Code Agent、Test Agent 和 Validation Agent 任务书。
            - CompileStage：Cargo 可用时记录 `cargo check` 诊断。
            - RepairStage：整理编译结果和修复判断。
            - ValidationStage：执行 profile 驱动的验证门禁。
            """
        write(report, existing + text_block(rendered))


def build_profile_stages(profile: dict[str, Any], include_validation: bool = False) -> list[HarnessStage]:
    stages: list[HarnessStage] = [
        OutputScaffoldStage(),
        ConstraintLoadingStage(profile.get("constraint_files", []), profile.get("constraint_summary_md")),
        ProfileProjectAnalysisStage(profile),
        ProfileSkeletonGenerationStage(profile),
        ProfileContextBuilderStage(profile),
        ProfileParityMatrixStage(profile),
        ProfileTranslationStage(profile),
    ]
    if include_validation:
        stages.extend(
            [
                CompileStage(),
                RepairStage(),
                ProfileValidationStage(profile),
            ]
        )
    return stages


def _clear_stale_validation_artifacts(ctx: ConversionContext) -> int:
    removed = 0
    for name in ("05-compile.json", "06-repair.json", "07-validation.json"):
        path = ctx.artifact(name)
        if path.exists():
            path.unlink()
            removed += 1
    repair_context = ctx.artifact("08-repair-context")
    if repair_context.exists():
        if repair_context.is_dir():
            shutil.rmtree(repair_context)
        else:
            repair_context.unlink()
        removed += 1
    return removed


def _write_bootstrap_report(ctx: ConversionContext) -> None:
    write(
        ctx.result / "output.md",
        f"""
        # 转换执行报告

        - 当前状态：`bootstrap_completed`
        - 验证阶段：`not_run`
        - Rust 输出目录：`{ctx.out}`
        - Profile 摘要：`{ctx.result / "harness" / "01-profile-summary.md"}`
        - 主线程入口：`{ctx.result / "harness" / "agent-entry" / "main-thread.json"}`
        - Code Agent 入口：`{ctx.result / "harness" / "agent-entry" / "code-agent.json"}`
        - Test Agent 入口：`{ctx.result / "harness" / "agent-entry" / "test-agent.json"}`
        - Validation Agent 入口：`{ctx.result / "harness" / "agent-entry" / "validation-agent.json"}`
        - 执行 trace：`{ctx.logs / "trace" / "profile-harness-path.md"}`

        bootstrap 只生成轻量摘要、任务书、manifest、context shard、test shard 和 trace；
        未执行 CompileStage、RepairStage 或 ValidationStage。
        """,
    )
    write(
        ctx.result / "issues" / "00-summary.md",
        """
        # 转换摘要

        当前只完成 bootstrap，验证阶段未运行。
        后续由 Main Thread 按 agent-entry 分发 Code Agent、Test Agent 和 Validation Agent。
        """,
    )


def run_profile_harness(ctx: ConversionContext, profile: dict[str, Any], include_validation: bool = False) -> ConversionContext:
    stages = build_profile_stages(profile, include_validation=include_validation)
    ctx.stage_history = []
    _reset_profile_trace(ctx)
    _record_profile_trace(
        ctx,
        "ProfileHarness",
        "start",
        include_validation=include_validation,
        planned_stages=[stage.name for stage in stages],
    )
    if not include_validation:
        removed_validation_artifacts = _clear_stale_validation_artifacts(ctx)
        _record_profile_trace(
            ctx,
            "ProfileHarness",
            "clear_stale_validation_artifacts",
            removed_count=removed_validation_artifacts,
        )
    try:
        total_stages = len(stages)
        for index, stage in enumerate(stages, start=1):
            _record_profile_trace(
                ctx,
                "ProfileHarness",
                "stage_start",
                step=index,
                total_stages=total_stages,
                stage_name=stage.name,
            )
            before_count = len(ctx.stage_history)
            try:
                stage(ctx)
            except Exception as exc:
                record = ctx.stage_history[-1] if len(ctx.stage_history) > before_count else {}
                _record_profile_trace(
                    ctx,
                    "ProfileHarness",
                    "stage_failed",
                    step=index,
                    total_stages=total_stages,
                    stage_name=stage.name,
                    duration_ms=record.get("duration_ms"),
                    error=str(exc),
                )
                raise
            record = ctx.stage_history[-1] if len(ctx.stage_history) > before_count else {}
            _record_profile_trace(
                ctx,
                "ProfileHarness",
                "stage_complete",
                step=index,
                total_stages=total_stages,
                stage_name=stage.name,
                status=record.get("status"),
                duration_ms=record.get("duration_ms"),
            )
    except Exception as exc:
        _record_profile_trace(
            ctx,
            "ProfileHarness",
            "failed",
            error=str(exc),
            completed_stages=[record.get("stage") for record in ctx.stage_history if record.get("status") == "completed"],
        )
        raise
    if not include_validation:
        ctx.compile_result = {}
        ctx.validation_result = {"status": "not_run", "reason": "bootstrap_only"}
        _write_bootstrap_report(ctx)
        _record_profile_trace(
            ctx,
            "ProfileHarness",
            "write_bootstrap_report",
            outputs=["result/output.md", "result/issues/00-summary.md"],
        )
    _record_profile_trace(
        ctx,
        "ProfileHarness",
        "complete",
        completed_stages=[record.get("stage") for record in ctx.stage_history if record.get("status") == "completed"],
    )
    return ctx









