#!/usr/bin/env python3
"""Lightweight profile harness stages."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from generic_harness import ConversionContext, HarnessStage, write
from model_artifacts import generate_model_brief, generate_workspace_scaffold
from profile_common import _effective_profile, _layout, _profile_name
from profile_trace import _record_profile_trace


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
        generate_model_brief(
            ctx.root,
            ctx.source,
            ctx.out,
            ctx.result,
            ctx.logs,
            _effective_profile(ctx, self.profile),
            ctx.analysis,
        )
        write(
            ctx.artifact("04-translation.md"),
            f"""
            # 模型引导翻译

            执行框架已从 `work/agents/` 固定中文模板渲染出面向
            Code Agent、Test Agent 和 Validation Agent 的分工指引，而不是
            从 Python 写入 Rust 实现或内联长篇 agent 提示词。

            - Agent 固定模板目录：`{ctx.root / "work" / "agents"}`
            - Code Agent 实现任务：`{ctx.result / "MODEL_TASK.md"}`
            - Test Agent 测试任务：`{ctx.result / "TEST_AGENT_TASK.md"}`
            - Validation Agent 验证任务：`{ctx.result / "VALIDATION_AGENT_TASK.md"}`
            - Agent entry manifest：`{ctx.result / "harness" / "agent-entry" / "manifest.json"}`
            - Code Agent plan：`{ctx.result / "harness" / "code-plan.json"}`
            - Code Agent manifest：`{ctx.result / "harness" / "code-manifest.json"}`
            - Test requirement manifest：`{ctx.result / "harness" / "test-requirements" / "manifest.json"}`
            - 执行框架主任务书：`{ctx.result / "harness" / "04-model-generation-brief.md"}`
            - parity 矩阵：`{ctx.result / "harness" / "04-function-parity.json"}`

            主线程只做编排，不读取 C 源码或 Rust `src/tests`；
            Code Agent 实现 `src/*.rs`；Test Agent 生成 `tests/*.rs`；
            Validation Agent 运行 strict 验证并返回失败摘要。
            全量 analysis/profile 文件默认不生成；Agent 只读取 plan、manifest、
            测试需求文件或验证结果，不展开完整测试语义矩阵。
            """,
        )
        _record_profile_trace(
            ctx,
            self.name,
            "generate_model_and_subagent_briefs",
            inputs=[
                "work/agents/code-agent.md",
                "work/agents/test-agent.md",
                "work/agents/validation-agent.md",
            ],
            outputs=[
                "result/MODEL_TASK.md",
                "result/TEST_AGENT_TASK.md",
                "result/VALIDATION_AGENT_TASK.md",
                "result/harness/agent-entry/manifest.json",
                "result/harness/agent-entry/code-agent.json",
                "result/harness/agent-entry/test-agent.json",
                "result/harness/agent-entry/validation-agent.json",
                "result/harness/code-plan.json",
                "result/harness/code-manifest.json",
                "result/harness/test-requirements/manifest.json",
                "result/harness/04-model-generation-brief.md",
                "result/harness/04-test-agent-task.md",
                "result/harness/04-validation-agent-task.md",
                "result/harness/04-translation.md",
                "result/harness/04-function-parity.json",
            ],
        )
