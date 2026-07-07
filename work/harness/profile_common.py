#!/usr/bin/env python3
"""Shared helpers for profile analysis and validation."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from generic_harness import ConversionContext, read_text

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
