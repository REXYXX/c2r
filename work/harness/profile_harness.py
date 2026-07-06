#!/usr/bin/env python3
"""动态 profile 驱动的 C 到 Rust 转换执行框架阶段。

markdown profile 只作为人工覆盖层；源码布局、测试映射、benchmark、公共 API
和 parity 锚点优先由源码分析阶段实时生成。
"""

from __future__ import annotations

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
    write_context_shards,
)
from profile_generator import markdown_profile


CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
}


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
        src_files = self._list_many(ctx.source, _as_list(layout.get("source_dirs")))
        test_files = self._list_many(ctx.source, _as_list(layout.get("test_dirs")))
        include_files = self._list_many(ctx.source, _as_list(layout.get("include_dirs")))
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
        write(ctx.artifact("01-analysis.json"), json.dumps(ctx.analysis, indent=2, ensure_ascii=False))
        write(ctx.artifact("01-derived-profile.json"), json.dumps(derived_profile, indent=2, ensure_ascii=False))
        write(ctx.artifact("01-effective-profile.json"), json.dumps(effective_profile, indent=2, ensure_ascii=False))
        write(ctx.artifact("01-effective-profile.md"), markdown_profile(effective_profile))
        write(ctx.artifact("01-dependency-map.md"), self._dependency_markdown(ctx, src_files, test_files, include_files))
        _record_profile_trace(
            ctx,
            self.name,
            "write_analysis_artifacts",
            outputs=[
                "result/harness/01-analysis.json",
                "result/harness/01-derived-profile.json",
                "result/harness/01-effective-profile.json",
                "result/harness/01-effective-profile.md",
                "result/harness/01-dependency-map.md",
            ],
        )

    def _list_many(self, root: Path, subdirs: list[str]) -> list[str]:
        files: list[str] = []
        for subdir in subdirs:
            files.extend(list_relative(root, subdir))
        return files

    def _collect_components(self, files: list[str]) -> dict[str, list[str]]:
        components: dict[str, list[str]] = {}
        filters = self.profile.get("component_filters", {})
        for name, tokens in filters.items():
            lowered_tokens = [str(token).lower() for token in tokens]
            components[name] = [path for path in files if any(token in path.lower() for token in lowered_tokens)]
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
            counts: dict[str, int] = {}
            for raw_name in _test_runs_from_path(path, pattern):
                counts[raw_name] = counts.get(raw_name, 0) + 1
                rust_name = duplicate_names.get((suite, raw_name, counts[raw_name]), raw_name)
                body = _extract_c_function_body(text, raw_name)
                semantic = self._semantic_requirements_from_body(body, public_api_set)
                if semantic:
                    semantic["source"] = relative
                    semantic["c_test"] = raw_name
                    requirements.setdefault(target, {})[rust_name] = semantic
        return requirements

    def _semantic_requirements_from_body(self, body: str, public_api_set: set[str]) -> dict[str, Any]:
        calls = self._calls_from_body(body)
        assertions = self._assertion_expressions(body)
        loop_headers = [match.group(0) for match in re.finditer(r"\b(?:for|while)\s*\([^{}]{0,240}\)", body)]
        public_api_calls = sorted({name for name in calls if name in public_api_set})
        assertion_fields = sorted({field for expression in assertions for field in self._field_tokens(expression)})
        assertion_constants = sorted({token for expression in assertions for token in self._constant_tokens(expression)})
        loop_tokens = sorted({token for header in loop_headers for token in self._constant_tokens(header)})
        literals = self._representative_literals(body)
        helper_calls = sorted(
            {
                name
                for name in calls
                if name not in public_api_set
                and not name.startswith("test_assert")
                and name not in CONTROL_KEYWORDS
            }
        )

        if not (public_api_calls or assertion_fields or assertion_constants or loop_tokens or literals or helper_calls):
            return {}

        minimum_assertions = min(max(1, len(assertions) // 2), 8) if assertions else 0
        static_validation = {
            "required_api_calls": public_api_calls,
            "required_assertion_fields": assertion_fields,
            "required_assertion_constants": assertion_constants[:24],
            "required_loop_tokens": loop_tokens[:16],
            "required_representative_literals": literals[:16],
            "minimum_assertions": minimum_assertions,
            "requires_loop": bool(loop_headers),
        }
        observations = {
            "public_api_calls": public_api_calls,
            "assertion_count": len(assertions),
            "assertion_fields": assertion_fields,
            "assertion_constants": assertion_constants,
            "loop_count": len(loop_headers),
            "loop_headers": loop_headers,
            "loop_tokens": loop_tokens,
            "representative_literals": literals,
            "helper_calls": helper_calls,
        }
        return {
            "requirements": [
                "根据 C 测试源码实时抽取：Rust 测试必须覆盖相同的公开 API 调用、断言状态字段、常量/循环规模和代表性测试数据。"
            ],
            "source_observations": observations,
            "static_validation": static_validation,
        }

    def _calls_from_body(self, body: str) -> list[str]:
        return [
            name
            for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)
            if name not in CONTROL_KEYWORDS and name not in {"sizeof", "defined"}
        ]

    def _assertion_expressions(self, body: str) -> list[str]:
        return re.findall(r"\b(?:test_assert[A-Za-z0-9_]*|assert)\s*\((.*?)\)\s*;", body, re.S)

    def _field_tokens(self, text: str) -> list[str]:
        return sorted(set(re.findall(r"(?:->|\.)\s*([A-Za-z_][A-Za-z0-9_]*)", text)))

    def _constant_tokens(self, text: str) -> list[str]:
        return sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)))

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
            for token in profile.get("function_hint_tokens", []):
                if str(token) in text:
                    hints.append({"file": str(path.relative_to(source)).replace("\\", "/"), "symbol_prefix": str(token)})
        return hints


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

            执行框架已输出面向 Code Agent、Test Agent 和 Validation Agent
            的生成指引，而不是从 Python 写入 Rust 实现。

            - Code Agent 实现任务：`{ctx.out / "MODEL_TASK.md"}`
            - Test Agent 测试任务：`{ctx.out / "TEST_AGENT_TASK.md"}`
            - Validation Agent 验证任务：`{ctx.out / "VALIDATION_AGENT_TASK.md"}`
            - Code Agent manifest：`{ctx.result / "harness" / "code-manifest.json"}`
            - Context manifest：`{ctx.result / "harness" / "context" / "manifest.json"}`
            - Test requirement manifest：`{ctx.result / "harness" / "test-requirements" / "manifest.json"}`
            - 执行框架主任务书：`{ctx.result / "harness" / "04-model-generation-brief.md"}`
            - parity 矩阵：`{ctx.result / "harness" / "04-function-parity.json"}`

            Code Agent 实现 `src/*.rs`；Test Agent 生成 `tests/*.rs`；
            Validation Agent 运行 strict 验证并返回压缩失败摘要。
            Agent 不应一次性展开 `01-analysis.json`、`01-effective-profile.json`
            或完整测试语义矩阵。
            """,
        )
        _record_profile_trace(
            ctx,
            self.name,
            "generate_model_and_subagent_briefs",
            outputs=[
                "out/MODEL_TASK.md",
                "out/TEST_AGENT_TASK.md",
                "out/VALIDATION_AGENT_TASK.md",
                "result/harness/code-manifest.json",
                "result/harness/context/manifest.json",
                "result/harness/test-requirements/manifest.json",
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
        ctx.validation_result = {
            "status": self._validation_status(checks, cargo_test, effective),
            "failures": self._validation_failures(checks, cargo_test, effective),
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
                validation = spec.get("static_validation", {})
                required_api_calls = [str(token) for token in validation.get("required_api_calls", [])]
                required_fields = [str(token) for token in validation.get("required_assertion_fields", [])]
                required_constants = [str(token) for token in validation.get("required_assertion_constants", [])]
                required_loop_tokens = [str(token) for token in validation.get("required_loop_tokens", [])]
                required_literals = [str(token) for token in validation.get("required_representative_literals", [])]
                assertion_count = len(re.findall(r"\bassert(?:_eq|_ne)?!|\bassert\s*\(", body))
                minimum_assertions = int(validation.get("minimum_assertions", 0) or 0)
                has_loop = bool(re.search(r"\b(?:for|while|loop)\b|\.for_each\s*\(", body))
                missing_api_calls = [token for token in required_api_calls if token not in body]
                missing_fields = [token for token in required_fields if token not in body]
                missing_constants = [token for token in required_constants if token not in body]
                missing_loop_tokens = [token for token in required_loop_tokens if token not in body]
                missing_literals = [token for token in required_literals if token not in body]
                test_results[str(test_name)] = {
                    "ok": (
                        bool(body)
                        and not missing_api_calls
                        and not missing_fields
                        and not missing_constants
                        and not missing_loop_tokens
                        and not missing_literals
                        and assertion_count >= minimum_assertions
                        and (has_loop or not validation.get("requires_loop", False))
                    ),
                    "has_body": bool(body),
                    "missing_api_calls": missing_api_calls,
                    "missing_assertion_fields": missing_fields,
                    "missing_assertion_constants": missing_constants,
                    "missing_loop_tokens": missing_loop_tokens,
                    "missing_representative_literals": missing_literals,
                    "assertion_count": assertion_count,
                    "minimum_assertions": minimum_assertions,
                    "has_loop": has_loop,
                    "requires_loop": bool(validation.get("requires_loop", False)),
                    "requirements": spec.get("requirements", []),
                }
            result[str(relative)] = test_results
        return result

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
                    problems.append("missing C API calls: " + ", ".join(result["missing_api_calls"]))
                if result.get("missing_assertion_fields"):
                    problems.append("missing assertion fields: " + ", ".join(result["missing_assertion_fields"]))
                if result.get("missing_assertion_constants"):
                    problems.append("missing assertion constants: " + ", ".join(result["missing_assertion_constants"]))
                if result.get("missing_loop_tokens"):
                    problems.append("missing loop tokens: " + ", ".join(result["missing_loop_tokens"]))
                if result.get("missing_representative_literals"):
                    problems.append("missing representative literals: " + ", ".join(result["missing_representative_literals"]))
                if int(result.get("assertion_count", 0) or 0) < int(result.get("minimum_assertions", 0) or 0):
                    problems.append(
                        f"assertions {result.get('assertion_count', 0)} < {result.get('minimum_assertions', 0)}"
                    )
                if result.get("requires_loop") and not result.get("has_loop"):
                    problems.append("missing loop/batch structure")
                failures.append(f"missing semantic coverage in {path}::{name}: {'; '.join(problems)}")
        if checks.get("unsafe_occurrences", 0) != 0:
            failures.append(f"unsafe occurrences must be 0, got {checks['unsafe_occurrences']}")
        if cargo_test.get("status") == "failed":
            failures.append("cargo test failed")
        if cargo_test.get("status") == "skipped" and profile.get("cargo_test_required", True):
            failures.append(f"cargo test skipped: {cargo_test.get('reason', 'unknown reason')}")
        return failures

    def _validation_status(self, checks: dict[str, Any], cargo_test: dict[str, Any], profile: dict[str, Any]) -> str:
        return "failed" if self._validation_failures(checks, cargo_test, profile) else "passed"

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
            - TranslationStage：生成 Code Agent、Test Agent 和 Validation Agent 任务书。
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
    _record_profile_trace(
        ctx,
        "ProfileHarness",
        "complete",
        completed_stages=[record.get("stage") for record in ctx.stage_history if record.get("status") == "completed"],
    )
    return ctx









