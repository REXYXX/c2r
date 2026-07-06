#!/usr/bin/env python3
"""从 C 工程源码生成通用转换 profile。

静态 markdown profile 只作为人工覆盖层使用；源码布局、测试清单、
benchmark 操作、模块名和公共 API 入口都优先由这里按项目实时推导。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


TEST_DIR_TOKENS = {"test", "tests", "utest", "unittest", "benchmark", "bench"}
DOC_DIR_TOKENS = {"doc", "docs", "demo", "demos", "sample", "samples", "example", "examples"}
HELPER_BENCH_NAMES = {
    "bench_start",
    "bench_end",
    "bench_calc",
    "bench_print",
    "bench_get_time",
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 profile，override 显式覆盖 base。"""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def build_dynamic_profile(source: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """根据源码目录生成 profile，并合并 markdown 覆盖项。"""
    overrides = overrides or {}
    project_name = str(overrides.get("profile") or _project_name(source))
    display = str(overrides.get("display_name") or _display_name(source))
    layout = _detect_layout(source)
    strip_prefixes = _common_module_prefixes(source, layout)
    generated = {
        "profile": project_name,
        "display_name": display,
        "artifact": {
            "crate_name": _safe_identifier(f"{project_name}_rust"),
            "output_dir": f"{project_name}_rust",
            "source_label": f"{display} C 源码",
            "task_title": f"{display} Rust 模型任务",
            "report_title": f"{display} Rust 转换执行框架报告",
        },
        "constraint_files": ["work/specs/rust_design_rules.md"],
        "constraint_summary_md": _constraint_summary(),
        "source_layout": layout,
        "module_name_strip_prefixes": strip_prefixes,
        "component_filters": _component_filters(source, layout, strip_prefixes),
        "readme_test_discovery": _readme_test_discovery(source),
        "benchmark_discovery": _benchmark_discovery(source),
        "disallow_unsafe": True,
        "cargo_test_required": True,
        "harness_report_appendix": _report_appendix(),
    }
    return deep_merge(generated, overrides)


def markdown_profile(profile: dict[str, Any]) -> str:
    """把生成的 profile 序列化为可复用 markdown。"""
    return "# 动态生成的转换 Profile\n\n```json harness-profile\n" + json.dumps(
        profile,
        indent=2,
        ensure_ascii=False,
    ) + "\n```\n"


def _project_name(source: Path) -> str:
    return _slug(source.name or "project")


def _display_name(source: Path) -> str:
    return source.name or "Project"


def _safe_identifier(value: str) -> str:
    name = _slug(value).replace("-", "_")
    if not re.match(r"^[A-Za-z_]", name):
        name = f"project_{name}"
    return name


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "project"


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _parts_lower(path: Path) -> set[str]:
    return {_slug(part) for part in path.parts if part not in {"", "."}}


def _top_dir(relative: str) -> str:
    parts = Path(relative).parts
    if len(parts) <= 1:
        return "."
    return parts[0].replace("\\", "/")


def _list_files(source: Path, suffix: str) -> list[str]:
    if not source.exists():
        return []
    return sorted(_relative(path, source) for path in source.rglob(f"*{suffix}") if path.is_file())


def _detect_layout(source: Path) -> dict[str, Any]:
    c_files = _list_files(source, ".c")
    h_files = _list_files(source, ".h")
    source_dirs = _source_dirs(c_files)
    test_dirs = _test_dirs(c_files)
    include_dirs = _include_dirs(h_files)
    public_header = _public_api_header(source, h_files, include_dirs)
    return {
        "source_dirs": source_dirs,
        "test_dirs": test_dirs,
        "include_dirs": include_dirs,
        "public_api_header": public_header,
        "public_api_pattern": r"(?:^|\n)\s*(?:extern\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s+)+\*?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*;",
        "test_run_pattern": r"(?:TEST_RUN|UTEST_UNIT_RUN|RUN_TEST)\((test_[A-Za-z0-9_]+)\)",
        "anchor_search_dirs": source_dirs,
        "function_hint_globs": ["*.c", "*.h"],
    }


def _source_dirs(c_files: list[str]) -> list[str]:
    dirs: set[str] = set()
    for relative in c_files:
        parts = _parts_lower(Path(relative))
        if parts & TEST_DIR_TOKENS or parts & DOC_DIR_TOKENS:
            continue
        dirs.add(_top_dir(relative))
    return sorted(dirs) or ["."]


def _test_dirs(c_files: list[str]) -> list[str]:
    dirs: set[str] = set()
    for relative in c_files:
        parts = _parts_lower(Path(relative))
        if parts & TEST_DIR_TOKENS:
            dirs.add(_top_dir(relative))
    return sorted(dirs) or ["tests"]


def _include_dirs(h_files: list[str]) -> list[str]:
    preferred = []
    other = []
    for relative in h_files:
        top = _top_dir(relative)
        top_slug = _slug(top)
        if top_slug in {"inc", "include", "includes"}:
            preferred.append(top)
        elif not (_parts_lower(Path(relative)) & (TEST_DIR_TOKENS | DOC_DIR_TOKENS)):
            other.append(top)
    dirs = sorted(set(preferred)) or sorted(set(other))
    return dirs or ["include", "inc"]


def _public_api_header(source: Path, h_files: list[str], include_dirs: list[str]) -> str:
    candidates = []
    include_set = set(include_dirs)
    for relative in h_files:
        if include_set and _top_dir(relative) not in include_set:
            continue
        text = _read(source / relative)
        score = len(_prototype_names(text))
        if score:
            candidates.append((score, -len(relative), relative))
    if candidates:
        return max(candidates)[2]
    return h_files[0] if h_files else ""


def _prototype_names(text: str) -> list[str]:
    return re.findall(
        r"(?:^|\n)\s*(?:extern\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s+)+\*?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*;",
        text,
    )


def _common_module_prefixes(source: Path, layout: dict[str, Any]) -> list[str]:
    stems = []
    roots = list(layout.get("source_dirs", [])) + list(layout.get("include_dirs", []))
    for root in roots:
        base = source / str(root)
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() in {".c", ".h"}:
                stems.append(path.stem.lower())
    prefix_counts: dict[str, int] = {}
    for stem in stems:
        match = re.match(r"([a-z][a-z0-9]{1,8}_)", stem)
        if match:
            prefix_counts[match.group(1)] = prefix_counts.get(match.group(1), 0) + 1
    threshold = max(2, len(stems) // 3)
    return sorted(prefix for prefix, count in prefix_counts.items() if count >= threshold)


def _module_name(relative: str, strip_prefixes: list[str]) -> str:
    stem = Path(relative).stem.lower()
    for prefix in strip_prefixes:
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    stem = re.sub(r"_tc$", "", stem)
    return _slug(stem)


def _component_filters(source: Path, layout: dict[str, Any], strip_prefixes: list[str]) -> dict[str, list[str]]:
    filters: dict[str, list[str]] = {}
    for root in list(layout.get("source_dirs", [])) + list(layout.get("include_dirs", [])):
        base = source / str(root)
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() in {".c", ".h"}:
                name = _module_name(_relative(path, source), strip_prefixes)
                filters.setdefault(name, [name])
    return filters


def _readme_test_discovery(source: Path) -> dict[str, Any]:
    if source.exists():
        candidates = sorted(
            _relative(path, source)
            for path in source.rglob("*.md")
            if "test" in path.name.lower() and "readme" in path.name.lower()
        )
        if candidates:
            return {"source": candidates[0]}
    return {"source": "tests/README_test.md"}


def _benchmark_discovery(source: Path) -> dict[str, Any]:
    if source.exists():
        bench_sources = sorted(
            _relative(path, source)
            for path in source.rglob("*.c")
            if "bench" in "/".join(part.lower() for part in path.parts)
        )
        if bench_sources:
            source_file = bench_sources[0]
            config = ""
            source_dir = source / Path(source_file).parent
            for name in ("fdb_cfg.h", "config.h", "bench_config.h"):
                candidate = source_dir / name
                if candidate.exists():
                    config = _relative(candidate, source)
                    break
            return {
                "source": source_file,
                "config": config,
                "rust_target": "tests/benchmark_tests.rs",
                "operation_prefix": "bench_",
                "excluded_functions": sorted(HELPER_BENCH_NAMES),
                "semantic_requirements": [
                    "benchmark 测试应覆盖每个被 benchmark 主流程调用的操作函数。",
                    "benchmark 测试应断言操作数量、结果字段和最终数据状态，不依赖固定墙钟阈值。",
                    "benchmark 测试应隔离并清理临时状态。",
                ],
            }
    return {}


def _constraint_summary() -> str:
    return (
        "# 约束加载\n\n"
        "执行框架会加载通用 Rust 设计规则，并从输入 C 工程实时分析 API、测试、"
        "benchmark、源码映射和内部锚点。markdown profile 仅作为可选人工覆盖层。\n\n"
        "必读文档：\n\n- `work/specs/rust_design_rules.md`"
    )


def _report_appendix() -> str:
    return (
        "## 执行框架阶段记录\n\n"
        "执行框架产物位于 `{harness_dir}`。\n\n"
        "- OutputScaffoldStage：创建 result/logs 产物结构。\n"
        "- ConstraintLoadingStage：加载通用约束和可选 profile 覆盖项。\n"
        "- ProjectAnalysisStage：从 C 工程实时生成 effective profile、API、测试、benchmark、源码映射和内部锚点。\n"
        "- SkeletonGenerationStage：准备 Cargo crate 布局。\n"
        "- ContextBuilderStage：生成最小模块/函数上下文。\n"
        "- ParityMatrixStage：生成公共 API 与源码锚点矩阵。\n"
        "- TranslationStage：生成 Code Agent、Test Agent 和 Validation Agent 任务书。\n"
        "- CompileStage：Cargo 可用时记录 `cargo check` 诊断。\n"
        "- RepairStage：整理编译结果和修复判断。\n"
        "- ValidationStage：执行结构检查、API parity、测试覆盖、benchmark 覆盖和 `cargo test`。"
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
