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
    """输出用于指导模型编写 Rust 代码的 markdown 任务书。"""
    del root
    analysis = analysis or {}
    context_index = context_index or {}
    required_files = profile.get("required_output_files", [])
    constraint_files = profile.get("constraint_files", [])
    one_to_one = profile.get("one_to_one_features", {})
    rejection = profile.get("behaviour_model_rejection", {})
    c_api = profile.get("c_api_parity_symbols", {})
    source_to_rust = profile.get("source_to_rust_modules", {})
    readme_coverage = profile.get("readme_test_coverage", {})
    source_runs = analysis.get("source_test_runs", {})
    project = display_name(profile)
    artifact = artifact_config(profile)
    task_title = artifact.get("task_title") or f"{project} Rust 模型任务"

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- 无"

    def json_block(value: Any) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    brief = "\n".join(
        [
            f"# {task_title}",
            "",
            "Python 执行框架不会生成 Rust 实现。请读取源项目和下列约束文档，",
            f"在 `{out}` 下编写 Rust crate。",
            "",
            "## 源码与输出",
            "",
            f"- {source_label(profile)}: `{source}`",
            f"- Rust crate 输出：`{out}`",
            f"- 结果产物：`{result}`",
            f"- 日志目录：`{logs}`",
            "",
            "## 必读约束文档",
            "",
            bullets(constraint_files),
            "",
            "## 必需 Rust 文件",
            "",
            bullets(required_files),
            "",
            "## 源码到 Rust 模块映射",
            "",
            "```json",
            json_block(source_to_rust),
            "```",
            "",
            "## 公共 C API 等价 token",
            "",
            "```json",
            json_block(c_api),
            "```",
            "",
            "## 附加逻辑覆盖检查",
            "",
            "```json",
            json_block(one_to_one),
            "```",
            "",
            "## 快捷实现拦截规则",
            "",
            "```json",
            json_block(rejection),
            "```",
            "",
            "## 需要迁移的源码测试",
            "",
            "```json",
            json_block(source_runs),
            "```",
            "",
            "## README 与 benchmark 覆盖",
            "",
            "必须迁移动态 profile 声明的每个单元测试和 benchmark 项。",
            "benchmark 用例应校验操作语义和测量结果字段是否合理；",
            "不要依赖固定墙钟耗时阈值。",
            "",
            "```json",
            json_block(readme_coverage),
            "```",
            "",
            "## 上下文索引",
            "",
            "```json",
            json_block(context_index),
            "```",
            "",
            "## 工作规则",
            "",
            f"- 直接在 `{out}` 中编写 Rust 源码；不要把 Rust 源码写进 Python。",
            "- 保留源码到 Rust 模块映射声明的模块边界。",
            "- 仅使用安全 Rust；除非动态 profile 明确允许，不要使用 C FFI。",
            "- 把动态 profile 要求的每个源码测试项都迁移为 Rust 测试。",
            "- crate 编写完成后运行 `cargo check` 和 `cargo test`。",
            "- 把验证失败视为生成修复提示，不要为了通过而削弱 profile 检查。",
            "",
        ]
    )
    write(out / "MODEL_TASK.md", brief)
    write(result / "harness" / "04-model-generation-brief.md", brief)


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

        编写或修复 Rust 代码前，先阅读 `{out / "MODEL_TASK.md"}` 和
        `{result / "harness" / "04-model-generation-brief.md"}`。
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

        模型必须基于动态 profile 和模型任务书，在 `{out}` 中编写或修复 Rust crate。
        Python 不得作为预写 Rust 实现字符串的容器。
        """,
    )
