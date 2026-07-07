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
import textwrap
from typing import Any

SOURCE_CONTEXT_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".inc"}
FUNCTION_HINT_CHUNK_SIZE = 12
TEST_BATCH_SIZE = 3
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
    template_path = root / "agents" / template_name
    template = template_path.read_text(encoding="utf-8")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    missing = sorted(set(AGENT_PLACEHOLDER_RE.findall(rendered)))
    if missing:
        raise ValueError(f"{template_path} has unresolved placeholders: {', '.join(missing)}")
    return rendered


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def source_context_files(values: list[str]) -> list[str]:
    return [value for value in values if Path(value).suffix.lower() in SOURCE_CONTEXT_SUFFIXES]


def hint_name(hint: dict[str, Any]) -> str | None:
    for key in ("name", "symbol", "symbol_prefix", "function"):
        if hint.get(key):
            return str(hint[key])
    return None


def hint_metadata(hint: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in hint.items() if key != "excerpt"}


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
    所有 Rust 实现和测试文件都必须由编码模型基于源码上下文和 profile
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


def write_context_shards(result: Path, context_index: dict[str, Any]) -> dict[str, Any]:
    base = result / "harness" / "context"
    compact_base = base / "modules"
    module_contexts = context_index.get("module_contexts", {}) or {}
    function_hints = context_index.get("function_hints", []) or []
    public_apis = context_index.get("public_apis", []) or []
    internal_anchors = context_index.get("internal_parity_anchors", {}) or {}
    manifest: dict[str, Any] = {
        "description": "Code Agent 上下文索引；默认读取 compact_manifest，不读取 legacy full shard。",
        "rules": [
            "优先读取 result/harness/agent-entry/code-agent.json。",
            "每次只处理一个 module compact_manifest。",
            "函数提示按 function_hint_chunks 分批读取，每批最多 12 项。",
            "legacy_full_shard 仅用于人工调试，不进入模型上下文。",
        ],
        "modules": {},
        "global": {
            "public_apis": "result/harness/context/public-apis.json",
            "internal_parity_anchors": "result/harness/context/internal-parity-anchors.json",
        },
    }
    for module, spec in sorted(module_contexts.items()):
        module_slug = slug(str(module))
        source_hints = source_context_files([str(item) for item in spec.get("source_hints", [])])
        source_set = set(source_hints)
        shard_hints = [item for item in function_hints if str(item.get("file", "")) in source_set]
        hint_chunks = chunks(shard_hints, FUNCTION_HINT_CHUNK_SIZE)
        function_chunk_index: list[dict[str, Any]] = []
        module_dir = compact_base / module_slug
        for index, hint_chunk in enumerate(hint_chunks, start=1):
            chunk_name = f"functions-{index:03d}.json"
            chunk_relative = f"result/harness/context/modules/{module_slug}/{chunk_name}"
            write_json(
                module_dir / chunk_name,
                {
                    "module": module,
                    "target": spec.get("target"),
                    "chunk": index,
                    "chunk_count": len(hint_chunks),
                    "function_hints": hint_chunk,
                    "rules": [
                        "只在实现本 chunk 中相关函数时读取。",
                        "优先使用每个 function_hint 的 excerpt，避免打开完整 C 源文件。",
                        "读完并完成局部实现后丢弃本 chunk 上下文。",
                    ],
                },
            )
            function_chunk_index.append(
                {
                    "chunk": index,
                    "path": chunk_relative,
                    "function_hints": len(hint_chunk),
                    "first": hint_name(hint_chunk[0]) if hint_chunk else None,
                    "last": hint_name(hint_chunk[-1]) if hint_chunk else None,
                }
            )
        shard = {
            "module": module,
            "target": spec.get("target"),
            "source_hints": source_hints,
            "required_mechanisms": spec.get("required_mechanisms", []),
            "function_hints": [hint_metadata(item) for item in shard_hints],
            "internal_parity_anchors": {
                key: value for key, value in internal_anchors.items() if key in source_set
            },
        }
        shard_relative = f"result/harness/context/{module_slug}.json"
        write_json(base / f"{module_slug}.json", shard)
        compact_relative = f"result/harness/context/modules/{module_slug}/manifest.json"
        write_json(
            module_dir / "manifest.json",
            {
                "module": module,
                "target": spec.get("target"),
                "source_files": source_hints,
                "required_mechanisms": spec.get("required_mechanisms", []),
                "function_hint_chunks": function_chunk_index,
                "legacy_full_shard": shard_relative,
                "rules": [
                    "这是 Code Agent 默认模块入口。",
                    "不要读取 legacy_full_shard，除非 compact chunks 不足以定位问题。",
                    "不要读取 tests/*.rs 或 test-requirements；测试交给 Test Agent。",
                ],
            },
        )
        manifest["modules"][str(module)] = {
            "target": spec.get("target"),
            "compact_manifest": compact_relative,
            "legacy_full_shard": shard_relative,
            "source_hints": len(source_hints),
            "function_hints": len(shard_hints),
            "function_hint_chunks": len(function_chunk_index),
        }
    write_json(base / "public-apis.json", {"public_apis": public_apis})
    write_json(base / "internal-parity-anchors.json", internal_anchors)
    write_json(base / "manifest.json", manifest)
    return manifest


def compact_context_index(context_index: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": "完整上下文已拆分到 result/harness/context/；Code Agent 只按模块读取 shard。",
        "manifest": "result/harness/context/manifest.json",
        "module_count": len((context_index.get("module_contexts", {}) or {})),
        "function_hint_count": len((context_index.get("function_hints", []) or [])),
        "public_api_count": len((context_index.get("public_apis", []) or [])),
        "internal_anchor_group_count": len((context_index.get("internal_parity_anchors", {}) or {})),
        "modules": manifest.get("modules", {}),
    }


def write_test_requirement_shards(
    result: Path,
    profile: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    base = result / "harness" / "test-requirements"
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
        "description": "Test Agent 入口索引；按目标测试文件读取 target manifest，再按单个测试读取 semantic shard。",
        "rules": [
            "优先读取 result/harness/agent-entry/test-agent.json。",
            "不要一次性读取 result/harness/01-effective-profile.json 或 01-analysis.json。",
            "不要把所有 semantic shard 同时展开到同一个上下文。",
            "每次只处理一个 batch；每个 batch 最多 3 个测试用例。",
        ],
        "targets": {},
    }
    for target in targets:
        target_slug = slug(target)
        target_dir = base / target_slug
        target_semantics = semantic_requirements.get(target, {}) or {}
        semantic_index: dict[str, Any] = {}
        for test_name, spec in sorted(target_semantics.items()):
            test_slug = slug(str(test_name))
            shard_relative = f"result/harness/test-requirements/{target_slug}/{test_slug}.json"
            write_json(
                target_dir / f"{test_slug}.json",
                {
                    "target": target,
                    "test": test_name,
                    "semantic_requirement": spec,
                },
            )
            observations = spec.get("source_observations", {}) if isinstance(spec, dict) else {}
            semantic_index[str(test_name)] = {
                "shard": shard_relative,
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
        batch_index: list[dict[str, Any]] = []
        for batch_number, test_batch in enumerate(chunks(required_tests, TEST_BATCH_SIZE), start=1):
            batch_items = []
            for test_name in test_batch:
                semantic = semantic_index.get(test_name, {})
                batch_items.append(
                    {
                        "test": test_name,
                        "semantic_shard": semantic.get("shard"),
                        "has_semantic_shard": bool(semantic.get("shard")),
                    }
                )
            batch_relative = f"result/harness/test-requirements/batches/{target_slug}/batch-{batch_number:03d}.json"
            write_json(
                base / "batches" / target_slug / f"batch-{batch_number:03d}.json",
                {
                    "target": target,
                    "batch": batch_number,
                    "batch_count": (len(required_tests) + TEST_BATCH_SIZE - 1) // TEST_BATCH_SIZE if required_tests else 0,
                    "items": batch_items,
                    "rules": [
                        "只读取本 batch 中 item 指向的 semantic_shard。",
                        "完成本 batch 后丢弃上下文，再处理下一 batch。",
                        "不要读取同一 target 的所有 semantic shard。",
                    ],
                },
            )
            batch_index.append(
                {
                    "batch": batch_number,
                    "path": batch_relative,
                    "tests": len(test_batch),
                    "first": test_batch[0] if test_batch else None,
                    "last": test_batch[-1] if test_batch else None,
                }
            )
        target_manifest: dict[str, Any] = {
            "target": target,
            "suites": suites,
            "required_rust_tests": required_tests,
            "batches": batch_index,
            "semantic_requirements_index": semantic_index,
            "rules": [
                "先用 required_rust_tests 建立完整 #[test] 清单。",
                "按 batches 顺序处理，每次只展开一个 batch。",
                "对 batch 中每个测试读取对应 semantic shard，覆盖 C 源测试抽取出的 API、断言、循环规模和代表性数据。",
                "同名空壳测试或只覆盖 happy path 不合格。",
            ],
        }
        if isinstance(benchmark, dict) and benchmark.get("rust_target") == target:
            target_manifest["benchmark"] = benchmark
        target_manifest_relative = f"result/harness/test-requirements/{target_slug}.json"
        write_json(base / f"{target_slug}.json", target_manifest)
        manifest["targets"][target] = {
            "manifest": target_manifest_relative,
            "required_tests": len(required_rust_tests.get(target, []) or []),
            "semantic_tests": len(semantic_index),
            "batches": len(batch_index),
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
    context_manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        "description": "Code Agent 最小实现入口；按模块读取 compact context，不读取完整测试矩阵。",
        "implementation_files": implementation_files,
        "test_files": test_files,
        "source_to_rust_modules": profile.get("source_to_rust_modules", {}),
        "api_symbols": profile.get("api_symbols", {}),
        "one_to_one_features": profile.get("one_to_one_features", {}),
        "behaviour_model_rejection": profile.get("behaviour_model_rejection", {}),
        "context_manifest": "result/harness/context/manifest.json",
        "compact_context_manifest": "result/harness/context/manifest.json",
        "context_modules": context_manifest.get("modules", {}),
        "parity_matrix": "result/harness/04-function-parity.json",
        "rules": [
            "只实现 Cargo.toml 和 src/*.rs。",
            "不要读取完整测试矩阵；测试交给 Test Agent。",
            "实现某个 src/*.rs 前，只读取对应 compact_manifest。",
            "函数提示按 function_hint_chunks 分批读取；不要读取 legacy_full_shard。",
        ],
    }
    write_json(result / "harness" / "code-manifest.json", manifest)
    return {
        "path": "result/harness/code-manifest.json",
        "implementation_files": len(implementation_files),
        "context_modules": len(context_manifest.get("modules", {})),
        "parity_matrix": "result/harness/04-function-parity.json",
    }


def write_agent_entries(
    root: Path,
    result: Path,
    source: Path,
    out: Path,
    code_manifest_summary: dict[str, Any],
    test_requirement_summary: dict[str, Any],
) -> dict[str, str]:
    base = result / "harness" / "agent-entry"
    checkpoint_base = result / "harness" / "context-checkpoints"
    write_if_missing(
        checkpoint_base / "code-agent.md",
        render_agent_template(root, "checkpoints/code-agent.md", {}),
    )
    write_if_missing(
        checkpoint_base / "test-agent.md",
        render_agent_template(root, "checkpoints/test-agent.md", {}),
    )
    entries = {
        "main-thread": {
            "agent": "main_thread",
            "purpose": "只负责编排、分发和审计 trace；不读取项目源码，不生成 Rust 代码。",
            "source_doc": "agents/main-thread.md",
            "rendered_task": str(out / "MAIN_THREAD_TASK.md"),
            "read_order": [
                str(out / "MAIN_THREAD_TASK.md"),
                "result/harness/agent-entry/manifest.json",
                "result/harness/agent-entry/code-agent.json",
                "result/harness/agent-entry/test-agent.json",
                "result/harness/agent-entry/validation-agent.json",
                "logs/trace/profile-harness-path.md",
                "result/harness/08-repair-context/manifest.json if present",
            ],
            "allowed_reads": [
                "INSTRUCTION.md",
                str(out / "MAIN_THREAD_TASK.md"),
                "result/harness/agent-entry/*.json",
                "logs/trace/profile-harness-path.json",
                "logs/trace/profile-harness-path.md",
                "result/harness/08-repair-context/manifest.json",
                "result/harness/08-repair-context/summary.md",
                "result/harness/08-repair-context/*-agent.json",
                "result/harness/08-repair-context/*-agent.md",
                "result/harness/context-checkpoints/*.md",
                "result/issues/00-summary.md",
                "result/output.md",
            ],
            "forbidden_reads": [
                str(source),
                str(out / "src"),
                str(out / "tests"),
                str(out / "target"),
                str(out / "MODEL_TASK.md"),
                str(out / "TEST_AGENT_TASK.md"),
                str(out / "VALIDATION_AGENT_TASK.md"),
                "result/harness/01-analysis.json",
                "result/harness/01-effective-profile.json",
                "result/harness/01-effective-profile.md",
                "result/harness/03-context.json",
                "result/harness/07-validation.json",
                "result/harness/context/**/*.json except context-checkpoints",
                "result/harness/test-requirements/**/*.json",
            ],
            "handoff_order": [
                {
                    "to": "code_agent",
                    "entry": "result/harness/agent-entry/code-agent.json",
                    "task": str(out / "MODEL_TASK.md"),
                    "owns": ["Cargo.toml", "src/*.rs"],
                },
                {
                    "to": "test_agent",
                    "entry": "result/harness/agent-entry/test-agent.json",
                    "task": str(out / "TEST_AGENT_TASK.md"),
                    "owns": ["tests/*.rs", "test fixtures"],
                },
                {
                    "to": "validation_agent",
                    "entry": "result/harness/agent-entry/validation-agent.json",
                    "task": str(out / "VALIDATION_AGENT_TASK.md"),
                    "owns": ["strict validation", "compressed repair context"],
                },
            ],
            "rule_source": "agents/main-thread.md",
        },
        "code-agent": {
            "agent": "code_agent",
            "purpose": "实现或修复 Cargo.toml 与 src/*.rs，测试交给 Test Agent。",
            "owner": "subagent",
            "source_doc": "agents/code-agent.md",
            "rendered_task": str(out / "MODEL_TASK.md"),
            "read_order": [
                str(out / "MODEL_TASK.md"),
                "result/harness/code-manifest.json",
                "result/harness/context/manifest.json",
            ],
            "context_budget": {
                "max_files_per_turn": 6,
                "max_function_hint_chunks_per_turn": 1,
                "reset_after_module": True,
            },
            "checkpoint": "result/harness/context-checkpoints/code-agent.md",
            "allowed_large_reads": [],
            "forbidden_reads": [
                "result/harness/01-analysis.json",
                "result/harness/01-effective-profile.json",
                "result/harness/01-effective-profile.md",
                "result/harness/07-validation.json",
                "result/harness/context/*.json legacy_full_shard",
                str(out / "target"),
                str(out / "tests"),
            ],
            "handoff_rule_source": "agents/main-thread.md",
            "summary": code_manifest_summary,
        },
        "test-agent": {
            "agent": "test_agent",
            "purpose": "生成或修复 tests/*.rs；按 batch 和 semantic shard 渐进读取。",
            "owner": "subagent",
            "source_doc": "agents/test-agent.md",
            "rendered_task": str(out / "TEST_AGENT_TASK.md"),
            "read_order": [
                str(out / "TEST_AGENT_TASK.md"),
                "result/harness/test-requirements/manifest.json",
                "result/harness/08-repair-context/manifest.json if present",
            ],
            "context_budget": {
                "max_target_files_per_turn": 1,
                "max_batches_per_turn": 1,
                "max_tests_per_batch": TEST_BATCH_SIZE,
                "reset_after_batch": True,
            },
            "checkpoint": "result/harness/context-checkpoints/test-agent.md",
            "allowed_large_reads": [],
            "forbidden_reads": [
                "result/harness/01-analysis.json",
                "result/harness/01-effective-profile.json",
                "result/harness/07-validation.json",
                "all files under result/harness/test-requirements/** at once",
                str(out / "src"),
                str(out / "target"),
            ],
            "handoff_rule_source": "agents/main-thread.md",
            "summary": test_requirement_summary,
        },
        "validation-agent": {
            "agent": "validation_agent",
            "purpose": "运行 strict 验证，回传 08-repair-context 压缩路由。",
            "owner": "subagent",
            "source_doc": "agents/validation-agent.md",
            "rendered_task": str(out / "VALIDATION_AGENT_TASK.md"),
            "read_order": [
                str(out / "VALIDATION_AGENT_TASK.md"),
                "result/harness/08-repair-context/manifest.json after validation",
            ],
            "context_budget": {
                "return_only_repair_context": True,
                "max_log_tail_chars": 8000,
            },
            "forbidden_reads": [
                "full cargo stdout/stderr beyond tail",
                "full result/harness/01-analysis.json",
            ],
            "handoff_rule_source": "agents/main-thread.md",
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
            "description": "最小 agent 入口。主线程先读 main-thread.json；子 agent 再按各自 read_order 读取下一层小文件。",
            "entries": paths,
            "start_here": "result/harness/agent-entry/main-thread.json",
            "source_docs": {
                "main-thread": "agents/main-thread.md",
                "code-agent": "agents/code-agent.md",
                "test-agent": "agents/test-agent.md",
                "validation-agent": "agents/validation-agent.md",
                "code-agent-checkpoint": "agents/checkpoints/code-agent.md",
                "test-agent-checkpoint": "agents/checkpoints/test-agent.md",
            },
            "rule_source": "agents/README.md",
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
    context_index: dict[str, Any] | None = None,
) -> None:
    """输出 Main/Code/Test/Validation Agent 分工任务书，避免单上下文承载全部工程。"""
    root = root.resolve()
    analysis = analysis or {}
    context_index = context_index or {}
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
    context_summary = {
        "module_contexts": len(context_index.get("module_contexts", {})),
        "function_hints": len(context_index.get("function_hints", [])),
        "public_apis": len(context_index.get("public_apis", [])),
        "internal_parity_anchor_groups": len(context_index.get("internal_parity_anchors", {})),
    }
    test_summary = {
        "source_test_suites": {name: len(entries) for name, entries in source_runs.items()},
        "required_rust_tests": {target: len(tests) for target, tests in required_rust_tests.items()},
        "benchmark_tests": len(benchmark.get("operation_tests", [])) if isinstance(benchmark, dict) else 0,
        "semantic_test_requirements": sum(len(tests) for tests in test_semantic_requirements.values()),
    }
    context_manifest = {
        "modules": {
            str(name): {
                "target": spec.get("target"),
                "compact_manifest": f"result/harness/context/modules/{slug(str(name))}/manifest.json",
                "legacy_full_shard": f"result/harness/context/{slug(str(name))}.json",
            }
            for name, spec in sorted((context_index.get("module_contexts", {}) or {}).items())
        }
    }
    code_manifest_summary = write_code_manifest(
        result,
        implementation_files,
        test_files,
        profile,
        context_manifest,
    )
    test_requirement_manifest = write_test_requirement_shards(result, profile, analysis)
    test_requirement_summary = {
        "manifest": "result/harness/test-requirements/manifest.json",
        "targets": {
            target: {
                "manifest": spec.get("manifest"),
                "required_tests": spec.get("required_tests", 0),
                "semantic_tests": spec.get("semantic_tests", 0),
                "batches": spec.get("batches", 0),
                "benchmark_tests": spec.get("benchmark_tests", 0),
            }
            for target, spec in test_requirement_manifest.get("targets", {}).items()
        },
    }
    agent_entries = write_agent_entries(root, result, source, out, code_manifest_summary, test_requirement_summary)

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
        "main_thread_task": out / "MAIN_THREAD_TASK.md",
        "model_task": out / "MODEL_TASK.md",
        "test_agent_task": out / "TEST_AGENT_TASK.md",
        "validation_agent_task": out / "VALIDATION_AGENT_TASK.md",
        "agent_entry_manifest": result / "harness" / "agent-entry" / "manifest.json",
        "main_thread_entry": result / "harness" / "agent-entry" / "main-thread.json",
        "code_agent_entry": result / "harness" / "agent-entry" / "code-agent.json",
        "test_agent_entry": result / "harness" / "agent-entry" / "test-agent.json",
        "validation_agent_entry": result / "harness" / "agent-entry" / "validation-agent.json",
        "trace_md": logs / "trace" / "profile-harness-path.md",
        "trace_json": logs / "trace" / "profile-harness-path.json",
        "repair_manifest": result / "harness" / "08-repair-context" / "manifest.json",
        "validation_json": result / "harness" / "07-validation.json",
        "issue_summary": result / "issues" / "00-summary.md",
        "code_manifest_path": result / "harness" / "code-manifest.json",
        "context_manifest_path": result / "harness" / "context" / "manifest.json",
        "test_requirements_manifest": result / "harness" / "test-requirements" / "manifest.json",
        "parity_matrix": result / "harness" / "04-function-parity.json",
        "constraint_files_bullets": bullets(constraint_files),
        "required_test_files_bullets": bullets(test_files),
        "agent_entries_json": json_block(agent_entries),
        "code_manifest_summary_json": json_block(code_manifest_summary),
        "test_summary_json": json_block(test_summary),
        "context_summary_json": json_block(context_summary),
        "test_requirement_summary_json": json_block(test_requirement_summary),
    }
    main_brief = render_agent_template(root, "main-thread.md", template_values)
    brief = render_agent_template(root, "code-agent.md", template_values)
    test_brief = render_agent_template(root, "test-agent.md", template_values)
    validation_brief = render_agent_template(root, "validation-agent.md", template_values)
    write(out / "MAIN_THREAD_TASK.md", main_brief)
    write(result / "harness" / "04-main-thread-task.md", main_brief)
    write(out / "MODEL_TASK.md", brief)
    write(result / "harness" / "04-model-generation-brief.md", brief)
    write(out / "TEST_AGENT_TASK.md", test_brief)
    write(result / "harness" / "04-test-agent-task.md", test_brief)
    write(out / "VALIDATION_AGENT_TASK.md", validation_brief)
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
    compressed_repair_context = validation.get("compressed_repair_context", {}) if isinstance(validation, dict) else {}
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
        - 压缩上下文：`{compressed_repair_context.get("manifest", "result/harness/08-repair-context/manifest.json")}`

        ## 失败项

        {bullet_list(failures)}

        ## 模型任务书

        主线程先阅读 `{out / "MAIN_THREAD_TASK.md"}` 和
        `{result / "harness" / "agent-entry" / "main-thread.json"}`，只做编排。
        Code Agent 编写或修复 Rust 实现前，先阅读 `{out / "MODEL_TASK.md"}` 和
        `{result / "harness" / "04-model-generation-brief.md"}`。
        测试迁移必须交给 Test Agent 的 `{out / "TEST_AGENT_TASK.md"}`，
        严格验证必须交给 Validation Agent 的 `{out / "VALIDATION_AGENT_TASK.md"}`。
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
        - 压缩上下文：`{compressed_repair_context.get("manifest", "result/harness/08-repair-context/manifest.json")}`

        ## 下一步要求

        主线程必须先按 `{out / "MAIN_THREAD_TASK.md"}` 分发任务，不读取源码或
        Rust `src/tests`。Code Agent 基于动态 profile 和模型任务书，在 `{out}`
        中编写或修复 Rust 实现。测试迁移由 Test Agent 驱动，严格验证和压缩诊断由
        Validation Agent 驱动。
        Python 不得作为预写 Rust 实现字符串的容器。
        """,
    )
