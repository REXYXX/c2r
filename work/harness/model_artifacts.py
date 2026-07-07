#!/usr/bin/env python3
"""面向模型的产物辅助函数，用于动态 profile 驱动的 C 到 Rust 转换。

本模块只准备目录、Cargo 元数据、模型任务书和报告。必需 Rust 文件、
API token 和测试要求来自源码实时分析生成的 effective profile，markdown
profile 只作为可选覆盖层。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import re
import shutil
import textwrap
from typing import Any

AGENT_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


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


def slug(value: str) -> str:
    name = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    return "_".join(part for part in name.split("_") if part) or "item"


def write_json(path: Path, value: Any) -> None:
    write(path, json.dumps(value, indent=2, ensure_ascii=False))


def render_agent_template(root: Path, template_name: str, values: dict[str, Any]) -> str:
    template_path = root / "work" / "agents" / template_name
    template = template_path.read_text(encoding="utf-8")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    missing = sorted(set(AGENT_PLACEHOLDER_RE.findall(rendered)))
    if missing:
        raise ValueError(f"{template_path} has unresolved placeholders: {', '.join(missing)}")
    return rendered


def profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("profile") or profile.get("name") or "project")


def display_name(profile: dict[str, Any]) -> str:
    return str(profile.get("display_name") or profile_name(profile))


def artifact_config(profile: dict[str, Any]) -> dict[str, Any]:
    value = profile.get("artifact", {})
    return value if isinstance(value, dict) else {}


def crate_name(profile: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override
    artifact = artifact_config(profile)
    return str(artifact.get("crate_name") or f"{profile_name(profile)}_rust")


def output_dir_name(profile: dict[str, Any]) -> str:
    artifact = artifact_config(profile)
    return str(artifact.get("output_dir") or f"{profile_name(profile)}_rust")


def source_label(profile: dict[str, Any]) -> str:
    artifact = artifact_config(profile)
    return str(artifact.get("source_label") or "C 源码")


def generate_workspace_scaffold(out: Path, profile: dict[str, Any] | None = None, crate: str | None = None) -> None:
    """只创建模型工作区和 Cargo 清单。

    已存在的 Rust 文件保持不变。这里仅给模型一个可构建的 crate 外壳；
    所有 Rust 实现和测试文件都必须由编码模型基于源码和 profile
    约束编写。
    """
    profile = profile or {}
    resolved_crate = crate_name(profile, crate)
    (out / "src").mkdir(parents=True, exist_ok=True)
    (out / "tests").mkdir(parents=True, exist_ok=True)
    write_if_missing(
        out / "Cargo.toml",
        f"""
        [package]
        name = "{resolved_crate}"
        version = "0.1.0"
        edition = "2021"
        description = "由模型编写的 {display_name(profile)} 安全 Rust 重写"
        license = "MIT"

        [lib]
        name = "{resolved_crate}"
        path = "src/lib.rs"

        [dependencies]
        """,
    )


def _module_from_target(target: str) -> str:
    path = Path(target)
    return path.stem if path.suffix else str(target)


def _assign_public_apis_to_modules(profile: dict[str, Any]) -> dict[str, list[str]]:
    public_apis = set((profile.get("c_api_parity_symbols", {}) or {}).get("public_api", []) or [])
    assigned: dict[str, set[str]] = {}

    def add(module: str, api: str) -> None:
        assigned.setdefault(module, set()).add(api)

    for api in public_apis:
        if any(api in values for values in assigned.values()):
            continue
        if api.startswith(("fdb_kvdb_", "fdb_kv_", "fdb_blob_")):
            add("kvdb", api)
        elif api.startswith(("fdb_tsdb_", "fdb_tsl_")):
            add("tsdb", api)
        elif api.startswith("fdb_calc_"):
            add("utils", api)
        else:
            add("flashdb", api)
    return {module: sorted(values) for module, values in sorted(assigned.items())}


def write_code_plan(
    result: Path,
    implementation_files: list[str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    module_apis = _assign_public_apis_to_modules(profile)
    source_to_rust = profile.get("source_to_rust_modules", {}) or {}
    source_files_by_module: dict[str, list[str]] = {}
    for source_file, targets in sorted(source_to_rust.items()):
        for target in targets:
            if not str(target).startswith("src/"):
                continue
            module = _module_from_target(str(target))
            source_files_by_module.setdefault(module, [])
            if source_file not in source_files_by_module[module]:
                source_files_by_module[module].append(str(source_file))
    modules: dict[str, Any] = {}
    for file_path in implementation_files:
        if not file_path.startswith("src/"):
            continue
        module = _module_from_target(file_path)
        modules[module] = {
            "target": file_path,
            "public_api_surface": module_apis.get(module, []),
            "source_files": source_files_by_module.get(module, []),
        }
    plan = {
        "description": "实现蓝图。",
        "policy_doc": "work/agents/code-agent.md",
        "generation_steps": [
            {
                "step": "crate_skeleton",
                "goal": "创建所有 implementation_files，并让 src/lib.rs 暴露 api_symbols 中的模块声明。",
                "inputs": ["result/harness/code-plan.json", "result/harness/code-manifest.json"],
            },
            {
                "step": "public_api_surface",
                "goal": "按每个模块 public_api_surface 先实现可编译 API 表面和核心数据类型。",
                "inputs": ["result/harness/code-plan.json", "result/harness/code-manifest.json", "result/harness/04-function-parity.json"],
            },
            {
                "step": "implementation",
                "goal": "按 implementation_files、source_to_rust_modules 和真实 C 源文件直接完成 src/*.rs。",
                "inputs": ["result/harness/code-plan.json", "result/harness/code-manifest.json"],
            },
            {
                "step": "handoff",
                "goal": "cargo check 可用时先自检，再交给 Test Agent 和 Validation Agent。",
                "inputs": ["result/harness/agent-entry/test-agent.json", "result/harness/agent-entry/validation-agent.json"],
            },
        ],
        "implementation_files": implementation_files,
        "api_symbols": profile.get("api_symbols", {}),
        "modules": modules,
    }
    write_json(result / "harness" / "code-plan.json", plan)
    return {
        "path": "result/harness/code-plan.json",
        "modules": len(modules),
        "public_api_surface": sum(len(item.get("public_api_surface", [])) for item in modules.values()),
    }


def semantic_requirement_summary(spec: dict[str, Any]) -> dict[str, Any]:
    logic = spec.get("logic_consistency", {}) if isinstance(spec, dict) else {}
    observations = spec.get("source_observations", {}) if isinstance(spec, dict) else {}
    validation = spec.get("static_validation", {}) if isinstance(spec, dict) else {}
    return {
        "requirements": spec.get("requirements", []) if isinstance(spec, dict) else [],
        "logic_consistency": logic,
        "static_validation": {
            "required_api_calls": validation.get("required_api_calls", []),
            "required_expanded_api_calls": validation.get("required_expanded_api_calls", []),
            "forbidden_api_calls": validation.get("forbidden_api_calls", []),
            "minimum_assertions": validation.get("minimum_assertions", 0),
        },
        "source_observations": {
            "public_api_calls": observations.get("public_api_calls", []),
            "assertion_count": observations.get("assertion_count", 0),
            "loop_count": observations.get("loop_count", 0),
            "representative_literals": observations.get("representative_literals", []),
        },
    }


def write_test_requirement_files(
    result: Path,
    profile: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    base = result / "harness" / "test-requirements"
    if base.exists():
        shutil.rmtree(base)
    readme_coverage = profile.get("readme_test_coverage", {}) or {}
    required_rust_tests = readme_coverage.get("required_rust_tests", {}) or {}
    benchmark = readme_coverage.get("benchmark", {}) if isinstance(readme_coverage, dict) else {}
    if not isinstance(benchmark, dict):
        benchmark = {}
    semantic_requirements = profile.get("test_semantic_requirements", {}) or {}
    test_suites = profile.get("test_suites", {}) or {}
    source_runs = analysis.get("source_test_runs", {}) or {}
    targets = sorted(
        set(required_rust_tests)
        | set(semantic_requirements)
        | {str(spec.get("target")) for spec in test_suites.values() if spec.get("target")}
    )
    manifest: dict[str, Any] = {
        "description": "测试需求索引。",
        "policy_doc": "work/agents/test-agent.md",
        "targets": {},
    }
    for target in targets:
        target_slug = slug(target)
        target_dir = base / target_slug
        target_semantics = semantic_requirements.get(target, {}) or {}
        semantic_index: dict[str, Any] = {}
        for test_name, spec in sorted(target_semantics.items()):
            test_slug = slug(str(test_name))
            requirement_relative = f"result/harness/test-requirements/{target_slug}/{test_slug}.json"
            write_json(
                target_dir / f"{test_slug}.json",
                {
                    "target": target,
                    "test": test_name,
                    "semantic_requirement": semantic_requirement_summary(spec),
                },
            )
            observations = spec.get("source_observations", {}) if isinstance(spec, dict) else {}
            semantic_index[str(test_name)] = {
                "requirement_file": requirement_relative,
                "public_api_calls": len(observations.get("public_api_calls", []) or []),
                "assertion_count": observations.get("assertion_count", 0),
                "loop_count": observations.get("loop_count", 0),
                "representative_literals": len(observations.get("representative_literals", []) or []),
            }
        suites = {
            suite: {
                "source": spec.get("source"),
                "target": spec.get("target"),
                "source_runs": source_runs.get(suite, []),
            }
            for suite, spec in sorted(test_suites.items())
            if spec.get("target") == target
        }
        required_tests = [str(name) for name in required_rust_tests.get(target, []) or []]
        generation_items = [
            {
                "test": test_name,
                "requirement_file": semantic_index.get(test_name, {}).get("requirement_file"),
                "has_requirement_file": bool(semantic_index.get(test_name, {}).get("requirement_file")),
            }
            for test_name in required_tests
        ]
        target_manifest: dict[str, Any] = {
            "target": target,
            "suites": suites,
            "required_rust_tests": required_tests,
            "generation_items": generation_items,
            "semantic_requirements_index": semantic_index,
            "policy_doc": "work/agents/test-agent.md",
        }
        if isinstance(benchmark, dict) and benchmark.get("rust_target") == target:
            target_manifest["benchmark"] = benchmark
        target_manifest_relative = f"result/harness/test-requirements/{target_slug}.json"
        write_json(base / f"{target_slug}.json", target_manifest)
        manifest["targets"][target] = {
            "manifest": target_manifest_relative,
            "required_tests": len(required_rust_tests.get(target, []) or []),
            "semantic_tests": len(semantic_index),
            "generation_items": len(generation_items),
            "benchmark_tests": len(benchmark.get("operation_tests", []) or [])
            if isinstance(benchmark, dict) and benchmark.get("rust_target") == target
            else 0,
        }
    write_json(base / "manifest.json", manifest)
    return manifest


def write_code_manifest(
    result: Path,
    implementation_files: list[str],
    test_files: list[str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        "description": "Rust 实现索引。",
        "policy_doc": "work/agents/code-agent.md",
        "implementation_files": implementation_files,
        "test_files": test_files,
        "source_to_rust_modules": profile.get("source_to_rust_modules", {}),
        "api_symbols": profile.get("api_symbols", {}),
        "one_to_one_features": profile.get("one_to_one_features", {}),
        "behaviour_model_rejection": profile.get("behaviour_model_rejection", {}),
        "code_plan": "result/harness/code-plan.json",
        "parity_matrix": "result/harness/04-function-parity.json",
    }
    write_json(result / "harness" / "code-manifest.json", manifest)
    return {
        "path": "result/harness/code-manifest.json",
        "implementation_files": len(implementation_files),
        "code_plan": "result/harness/code-plan.json",
        "parity_matrix": "result/harness/04-function-parity.json",
    }


def write_agent_entries(
    root: Path,
    result: Path,
    out: Path,
    code_manifest_summary: dict[str, Any],
    test_requirement_summary: dict[str, Any],
) -> dict[str, str]:
    base = result / "harness" / "agent-entry"
    stale_checkpoints = result / "harness" / "context-checkpoints"
    if stale_checkpoints.exists():
        shutil.rmtree(stale_checkpoints)
    for stale_name in ("MODEL_TASK.md", "TEST_AGENT_TASK.md", "VALIDATION_AGENT_TASK.md"):
        stale_task = out / stale_name
        if stale_task.exists():
            stale_task.unlink()
    entries = {
        "code-agent": {
            "agent": "code_agent",
            "source_doc": "work/agents/code-agent.md",
            "rendered_task": str(result / "MODEL_TASK.md"),
            "summary": code_manifest_summary,
        },
        "test-agent": {
            "agent": "test_agent",
            "source_doc": "work/agents/test-agent.md",
            "rendered_task": str(result / "TEST_AGENT_TASK.md"),
            "summary": test_requirement_summary,
        },
        "validation-agent": {
            "agent": "validation_agent",
            "source_doc": "work/agents/validation-agent.md",
            "rendered_task": str(result / "VALIDATION_AGENT_TASK.md"),
        },
    }
    paths: dict[str, str] = {}
    for name, payload in entries.items():
        relative = f"result/harness/agent-entry/{name}.json"
        write_json(base / f"{name}.json", payload)
        paths[name] = relative
    write_json(
        base / "manifest.json",
        {
            "description": "最小 subagent 入口索引。主线程编排规则维护在 INSTRUCTION.md。",
            "entries": paths,
            "start_here": "INSTRUCTION.md",
            "source_docs": {
                "code-agent": "work/agents/code-agent.md",
                "test-agent": "work/agents/test-agent.md",
                "validation-agent": "work/agents/validation-agent.md",
            },
        },
    )
    return paths


def generate_model_brief(
    root: Path,
    source: Path,
    out: Path,
    result: Path,
    logs: Path,
    profile: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> None:
    """输出 Code/Test/Validation Agent 分工任务书，避免单上下文承载全部工程。"""
    root = root.resolve()
    analysis = analysis or {}
    required_files = [str(path) for path in profile.get("required_output_files", [])]
    implementation_files = [path for path in required_files if not path.startswith("tests/")]
    test_files = [path for path in required_files if path.startswith("tests/")]
    constraint_files = profile.get("constraint_files", [])
    readme_coverage = profile.get("readme_test_coverage", {}) or {}
    source_runs = analysis.get("source_test_runs", {})
    test_semantic_requirements = profile.get("test_semantic_requirements", {}) or {}
    project = display_name(profile)
    artifact = artifact_config(profile)
    task_title = artifact.get("task_title") or f"{project} Rust 模型任务"

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- 无"

    def json_block(value: Any) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    required_rust_tests = readme_coverage.get("required_rust_tests", {})
    benchmark = readme_coverage.get("benchmark", {}) if isinstance(readme_coverage, dict) else {}
    if not isinstance(benchmark, dict):
        benchmark = {}
    test_summary = {
        "source_test_suites": {name: len(entries) for name, entries in source_runs.items()},
        "required_rust_tests": {target: len(tests) for target, tests in required_rust_tests.items()},
        "benchmark_tests": len(benchmark.get("operation_tests", [])) if isinstance(benchmark, dict) else 0,
        "semantic_test_requirements": sum(len(tests) for tests in test_semantic_requirements.values()),
    }
    code_plan_summary = write_code_plan(result, implementation_files, profile)
    code_manifest_summary = write_code_manifest(
        result,
        implementation_files,
        test_files,
        profile,
    )
    test_requirement_manifest = write_test_requirement_files(result, profile, analysis)
    test_requirement_summary = {
        "manifest": "result/harness/test-requirements/manifest.json",
        "targets": {
            target: {
                "manifest": spec.get("manifest"),
                "required_tests": spec.get("required_tests", 0),
                "semantic_tests": spec.get("semantic_tests", 0),
                "generation_items": spec.get("generation_items", 0),
                "benchmark_tests": spec.get("benchmark_tests", 0),
            }
            for target, spec in test_requirement_manifest.get("targets", {}).items()
        },
    }
    agent_entries = write_agent_entries(root, result, out, code_manifest_summary, test_requirement_summary)

    template_values = {
        "project": project,
        "task_title": task_title,
        "source_label": source_label(profile),
        "source": source,
        "out": out,
        "out_src": out / "src",
        "out_tests": out / "tests",
        "out_target": out / "target",
        "result": result,
        "logs": logs,
        "model_task": result / "MODEL_TASK.md",
        "test_agent_task": result / "TEST_AGENT_TASK.md",
        "validation_agent_task": result / "VALIDATION_AGENT_TASK.md",
        "agent_entry_manifest": result / "harness" / "agent-entry" / "manifest.json",
        "code_agent_entry": result / "harness" / "agent-entry" / "code-agent.json",
        "test_agent_entry": result / "harness" / "agent-entry" / "test-agent.json",
        "validation_agent_entry": result / "harness" / "agent-entry" / "validation-agent.json",
        "trace_md": logs / "trace" / "profile-harness-path.md",
        "trace_json": logs / "trace" / "profile-harness-path.json",
        "validation_json": result / "harness" / "07-validation.json",
        "issue_summary": result / "issues" / "00-summary.md",
        "code_plan_path": result / "harness" / "code-plan.json",
        "code_manifest_path": result / "harness" / "code-manifest.json",
        "test_requirements_manifest": result / "harness" / "test-requirements" / "manifest.json",
        "parity_matrix": result / "harness" / "04-function-parity.json",
        "constraint_files_bullets": bullets(constraint_files),
        "required_test_files_bullets": bullets(test_files),
        "agent_entries_json": json_block(agent_entries),
        "code_plan_summary_json": json_block(code_plan_summary),
        "code_manifest_summary_json": json_block(code_manifest_summary),
        "test_summary_json": json_block(test_summary),
        "test_requirement_summary_json": json_block(test_requirement_summary),
    }
    brief = render_agent_template(root, "code-agent.md", template_values)
    test_brief = render_agent_template(root, "test-agent.md", template_values)
    validation_brief = render_agent_template(root, "validation-agent.md", template_values)
    write(result / "MODEL_TASK.md", brief)
    write(result / "harness" / "04-model-generation-brief.md", brief)
    write(result / "TEST_AGENT_TASK.md", test_brief)
    write(result / "harness" / "04-test-agent-task.md", test_brief)
    write(result / "VALIDATION_AGENT_TASK.md", validation_brief)
    write(result / "harness" / "04-validation-agent-task.md", validation_brief)


def generate_report(
    root: Path,
    source: Path,
    out: Path,
    result: Path | None = None,
    logs: Path | None = None,
    validation: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> None:
    profile = profile or {}
    result = result or root / "result"
    logs = logs or root / "logs"
    validation = validation or {}
    analysis = analysis or {}
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = "已找到" if source.exists() else "当前环境未找到"
    failures = validation.get("failures", [])
    cargo_test = validation.get("cargo_test", {"status": "not_run"})
    repair_required = validation.get("repair_required", {}) if isinstance(validation, dict) else {}
    artifact = artifact_config(profile)
    report_title = artifact.get("report_title") or f"{display_name(profile)} Rust 转换执行框架报告"

    def bullet_list(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- 无"

    def route_count(name: str) -> int:
        values = repair_required.get(name, []) if isinstance(repair_required, dict) else []
        return len(values) if isinstance(values, list) else 0

    write(
        result / "output.md",
        f"""
        # {report_title}

        生成时间：{now}

        ## 输入

        - {source_label(profile)}: `{source}` ({status})
        - Rust 输出项目：`{out}`
        - 结果目录：`{result}`
        - 日志目录：`{logs}`

        ## 执行框架职责

        Python 只准备工作区、源码清单、模型任务书和验证产物。它不包含也不输出
        硬编码 Rust 实现。模型必须在 `{out}` 下编写 Rust 源码。

        ## 源码清单

        - 源码文件：{len(analysis.get("src_files", []))}
        - 测试文件：{len(analysis.get("test_files", []))}
        - 头文件/包含文件：{len(analysis.get("include_files", []))}

        ## 验证结果

        - 验证状态：`{validation.get("status", "not_run")}`
        - `cargo test` 状态：`{cargo_test.get("status", "not_run")}`

        ## 修复路由

        - Test Agent 待修复：{route_count("test_agent")}
        - Code Agent 待修复：{route_count("code_agent")}
        - Validation Agent 待处理：{route_count("validation_agent")}

        ## 失败项

        {bullet_list(failures)}

        ## 模型任务书

        主线程按 `{root / "INSTRUCTION.md"}` 编排，不再生成独立编排任务书。
        Code Agent 编写或修复 Rust 实现前，先阅读 `{result / "MODEL_TASK.md"}` 和
        `{result / "harness" / "04-model-generation-brief.md"}`。
        测试迁移必须交给 Test Agent 的 `{result / "TEST_AGENT_TASK.md"}`，
        严格验证必须交给 Validation Agent 的 `{result / "VALIDATION_AGENT_TASK.md"}`。
        """,
    )
    write(
        result / "issues" / "00-summary.md",
        f"""
        # 转换摘要

        - 总体状态：`{validation.get("status", "not_run")}`
        - `cargo test` 状态：`{cargo_test.get("status", "not_run")}`

        ## 失败项

        {bullet_list(failures)}

        ## 修复路由

        - Test Agent 待修复：{route_count("test_agent")}
        - Code Agent 待修复：{route_count("code_agent")}
        - Validation Agent 待处理：{route_count("validation_agent")}

        ## 下一步要求

        主线程必须先按 `{root / "INSTRUCTION.md"}` 分发任务，不读取源码或
        Rust `src/tests`。Code Agent 基于动态 profile 和模型任务书，在 `{out}`
        中编写或修复 Rust 实现。测试迁移由 Test Agent 驱动，严格验证由
        Validation Agent 驱动。
        Python 不得作为预写 Rust 实现字符串的容器。
        """,
    )
