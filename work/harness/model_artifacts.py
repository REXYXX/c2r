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
import textwrap
from typing import Any


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
    module_contexts = context_index.get("module_contexts", {}) or {}
    function_hints = context_index.get("function_hints", []) or []
    public_apis = context_index.get("public_apis", []) or []
    internal_anchors = context_index.get("internal_parity_anchors", {}) or {}
    manifest: dict[str, Any] = {
        "description": "按模块拆分的 Code Agent 上下文索引；不要一次性读取完整 03-context.json。",
        "modules": {},
        "global": {
            "public_apis": "result/harness/context/public-apis.json",
            "internal_parity_anchors": "result/harness/context/internal-parity-anchors.json",
        },
    }
    for module, spec in sorted(module_contexts.items()):
        module_slug = slug(str(module))
        source_hints = [str(item) for item in spec.get("source_hints", [])]
        source_set = set(source_hints)
        shard_hints = [item for item in function_hints if str(item.get("file", "")) in source_set]
        shard = {
            "module": module,
            "target": spec.get("target"),
            "source_hints": source_hints,
            "required_mechanisms": spec.get("required_mechanisms", []),
            "function_hints": shard_hints,
            "internal_parity_anchors": {
                key: value for key, value in internal_anchors.items() if key in source_set
            },
        }
        shard_relative = f"result/harness/context/{module_slug}.json"
        write_json(base / f"{module_slug}.json", shard)
        manifest["modules"][str(module)] = {
            "target": spec.get("target"),
            "shard": shard_relative,
            "source_hints": len(source_hints),
            "function_hints": len(shard_hints),
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
            "不要一次性读取 result/harness/01-effective-profile.json 或 01-analysis.json。",
            "不要把所有 semantic shard 同时展开到同一个上下文。",
            "每次只处理一个 tests/*.rs 目标文件；必要时每次只读取一个测试用例 shard。",
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
        target_manifest: dict[str, Any] = {
            "target": target,
            "suites": suites,
            "required_rust_tests": required_rust_tests.get(target, []),
            "semantic_requirements_index": semantic_index,
            "rules": [
                "先用 required_rust_tests 建立完整 #[test] 清单。",
                "对每个测试读取对应 semantic shard，覆盖 C 源测试抽取出的 API、断言、循环规模和代表性数据。",
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
        "description": "Code Agent 最小实现入口；按模块读取 context shard，不读取完整测试矩阵。",
        "implementation_files": implementation_files,
        "test_files": test_files,
        "source_to_rust_modules": profile.get("source_to_rust_modules", {}),
        "api_symbols": profile.get("api_symbols", {}),
        "one_to_one_features": profile.get("one_to_one_features", {}),
        "behaviour_model_rejection": profile.get("behaviour_model_rejection", {}),
        "context_manifest": "result/harness/context/manifest.json",
        "context_modules": context_manifest.get("modules", {}),
        "parity_matrix": "result/harness/04-function-parity.json",
        "rules": [
            "只实现 Cargo.toml 和 src/*.rs。",
            "不要读取完整测试矩阵；测试交给 Test Agent。",
            "实现某个 src/*.rs 前，只读取对应 context shard 和必要 C 源文件。",
        ],
    }
    write_json(result / "harness" / "code-manifest.json", manifest)
    return {
        "path": "result/harness/code-manifest.json",
        "implementation_files": len(implementation_files),
        "context_modules": len(context_manifest.get("modules", {})),
        "parity_matrix": "result/harness/04-function-parity.json",
    }


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
    """输出 Code/Test/Validation Agent 分工任务书，避免单上下文承载全部测试矩阵。"""
    del root
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
                "shard": f"result/harness/context/{slug(str(name))}.json",
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
                "benchmark_tests": spec.get("benchmark_tests", 0),
            }
            for target, spec in test_requirement_manifest.get("targets", {}).items()
        },
    }

    brief = "\n".join(
        [
            f"# {task_title}",
            "",
            "你是 Code Agent。Python 执行框架不会生成 Rust 实现；Code Agent",
            "只负责实现 Rust crate 的 `Cargo.toml` 与 `src/*.rs`。",
            "Rust 测试必须委派给 Test Agent；严格验证必须委派给 Validation Agent。",
            "",
            "## 源码与输出",
            "",
            f"- {source_label(profile)}: `{source}`",
            f"- Rust crate 输出：`{out}`",
            f"- 结果产物：`{result}`",
            f"- 日志目录：`{logs}`",
            "",
            "## Agent 分工",
            "",
            "- Code Agent：实现库代码、公共 API、模块边界和核心语义，只处理 `Cargo.toml` 与 `src/*.rs`。",
            f"- Test Agent：读取 `{out / 'TEST_AGENT_TASK.md'}`，只展开测试矩阵和 benchmark 细节，负责 `tests/*.rs`。",
            f"- Validation Agent：读取 `{out / 'VALIDATION_AGENT_TASK.md'}`，运行 strict 验证并输出压缩失败摘要。",
            "- Code Agent 不要把完整 README/benchmark 覆盖矩阵或完整验证日志读入当前上下文。",
            "",
            "## Code Agent 强制流程",
            "",
            "1. 先实现 `Cargo.toml` 与 `src/*.rs`，不要直接编写 `tests/*.rs`。",
            f"2. 实现完成后必须调用 Test Agent，并只把 `{out / 'TEST_AGENT_TASK.md'}` 作为测试任务入口。",
            "3. Test Agent 完成后，Code Agent 只接收测试变更摘要；不要回读完整测试矩阵。",
            f"4. 测试生成完成后必须调用 Validation Agent，并只把 `{out / 'VALIDATION_AGENT_TASK.md'}` 作为验证任务入口。",
            "5. Validation Agent 返回失败摘要后，`src/*.rs` 问题由 Code Agent 修复，`tests/*.rs` 问题交回 Test Agent。",
            "6. 每次修复后重复 Test Agent / Validation Agent 交接，直到 strict 验证通过。",
            "",
            "## 生成产物索引",
            "",
            f"- Code Agent manifest：`{result / 'harness' / 'code-manifest.json'}`",
            f"- context manifest：`{result / 'harness' / 'context' / 'manifest.json'}`",
            f"- parity matrix: `{result / 'harness' / '04-function-parity.json'}`",
            f"- Test Agent 任务书：`{out / 'TEST_AGENT_TASK.md'}`",
            f"- Validation Agent 任务书：`{out / 'VALIDATION_AGENT_TASK.md'}`",
            "",
            "不要读取 `01-analysis.json`、`01-effective-profile.json` 或完整 `03-context.json`；",
            "这些文件只用于机器审计和调试。",
            "",
            "## 必读约束文档",
            "",
            bullets(constraint_files),
            "",
            "## Code Agent Manifest",
            "",
            "```json",
            json_block(code_manifest_summary),
            "```",
            "",
            "## 测试与 benchmark 摘要",
            "",
            "完整测试清单不要在 Code Agent 上下文展开；这里只保留计数，具体内容见 Test Agent 任务书。",
            "",
            "```json",
            json_block(test_summary),
            "```",
            "",
            "## 上下文索引摘要",
            "",
            "Code Agent 按模块读取 `result/harness/context/manifest.json` 中指向的 shard。",
            "",
            "```json",
            json_block(context_summary),
            "```",
            "",
            "## 工作规则",
            "",
            f"- 直接在 `{out}` 中编写 Rust 源码；不要把 Rust 源码写进 Python。",
            "- 从 `code-manifest.json` 读取模块边界、API token 和快捷实现拦截规则。",
            "- 实现某个模块时，只读取对应 context shard 和必要 C 源文件。",
            "- 仅使用安全 Rust；除非动态 profile 明确允许，不要使用 C FFI。",
            "- Code Agent 完成库实现后，必须交给 Test Agent 生成 `tests/*.rs`。",
            "- Test Agent 完成后，必须交给 Validation Agent 运行 strict 验证。",
            "- 把验证失败视为生成修复提示，不要为了通过而削弱 profile 检查。",
            "",
        ]
    )

    test_brief = "\n".join(
        [
            f"# {project} Test Agent 任务",
            "",
            "你是 Test Agent。目标是生成完整 Rust 测试，不占用 Code Agent 上下文。",
            "",
            "## 输入与输出",
            "",
            f"- C 源项目：`{source}`",
            f"- Rust crate：`{out}`",
            f"- 结果目录：`{result}`",
            "",
            "## 允许编辑范围",
            "",
            "- 优先编辑 `tests/*.rs`。",
            "- 可按需补充测试 fixture、dev-dependencies 或测试辅助模块。",
            "- 不要重写核心 `src/*.rs`；若发现 API 缺口，记录最小缺口并交回 Code Agent 处理。",
            "",
            "## 必需测试文件",
            "",
            bullets(test_files),
            "",
            "## 测试 Requirement Manifest",
            "",
            f"- 根索引：`{result / 'harness' / 'test-requirements' / 'manifest.json'}`",
            "- 先读取根索引，再按目标测试文件读取 target manifest。",
            "- 每个测试的深层语义只读取对应 semantic shard，不要一次性展开全部 shard。",
            "",
            "```json",
            json_block(test_requirement_summary),
            "```",
            "",
            "## C 测试语义覆盖方式",
            "",
            "同名 Rust 测试不够；semantic shard 由 C 测试函数实时抽取。",
            "Test Agent 必须逐个 shard 覆盖其中的公开 API 调用、断言字段、",
            "常量/循环规模、辅助函数调用和代表性测试数据。",
            "",
            "## 测试工作规则",
            "",
            "- 按 target manifest 中的 `required_rust_tests` 逐项创建 Rust `#[test]`。",
            "- 按每个测试的 semantic shard 覆盖深层行为；不能只保留同名空壳或浅层 happy path。",
            "- 对重复 C 测试名使用动态 profile 已生成的唯一 Rust 测试名。",
            "- 每个测试使用隔离临时状态，避免测试间共享全局状态。",
            "- benchmark 风格测试只验证语义和结果结构，不把墙钟耗时作为稳定断言。",
            "- 完成后运行 `cargo test`；若失败，只保留必要诊断摘要给 Validation Agent。",
            "",
        ]
    )

    validation_brief = "\n".join(
        [
            f"# {project} Validation Agent 任务",
            "",
            "你是 Validation Agent。目标是运行严格验证并压缩失败诊断，不把完整日志回灌给 Code Agent。",
            "",
            "## 验证命令",
            "",
            "```bash",
            f"python work/run_conversion.py --source {source} --out {out} --result {result} --logs {logs} --strict",
            "```",
            "",
            "必要时也在 Rust crate 中运行：",
            "",
            "```bash",
            f"cd {out}",
            "cargo check",
            "cargo test",
            "```",
            "",
            "## 必读验证产物",
            "",
            f"- `{result / 'harness' / '07-validation.json'}`",
            f"- `{result / 'issues' / '00-summary.md'}`",
            f"- `{logs / 'trace' / 'profile-harness-path.json'}`",
            "",
            "## 诊断边界",
            "",
            "- 优先归类编译错误、缺失 API token、缺失测试、README/benchmark 覆盖失败。",
            "- `src/*.rs` 或 `Cargo.toml` 问题交回 Code Agent。",
            "- `tests/*.rs` 问题交回 Test Agent。",
            "- 不要删除或削弱动态 profile、测试矩阵、benchmark 覆盖或 `unsafe` 检查。",
            "- 输出给 Code Agent 的摘要只保留失败类别、涉及文件和下一步动作，避免回灌完整日志。",
            "",
        ]
    )
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
    artifact = artifact_config(profile)
    report_title = artifact.get("report_title") or f"{display_name(profile)} Rust 转换执行框架报告"

    def bullet_list(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- 无"

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

        ## 失败项

        {bullet_list(failures)}

        ## 模型任务书

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

        ## 下一步要求

        Code Agent 必须基于动态 profile 和模型任务书，在 `{out}` 中编写或修复
        Rust 实现。测试迁移由 Test Agent 驱动，严格验证和压缩诊断由
        Validation Agent 驱动。
        Python 不得作为预写 Rust 实现字符串的容器。
        """,
    )
