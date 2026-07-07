#!/usr/bin/env python3
"""Profile harness orchestration."""

from __future__ import annotations

from generic_harness import (
    ConversionContext,
    CompileStage,
    ConstraintLoadingStage,
    HarnessStage,
    OutputScaffoldStage,
    RepairStage,
    write,
)
from profile_analysis import ProfileProjectAnalysisStage
from profile_stages import (
    ProfileParityMatrixStage,
    ProfileSkeletonGenerationStage,
    ProfileTranslationStage,
)
from profile_trace import _record_profile_trace, _reset_profile_trace
from profile_validation import ProfileValidationStage


def build_profile_stages(profile: dict[str, Any], include_validation: bool = False) -> list[HarnessStage]:
    stages: list[HarnessStage] = [
        OutputScaffoldStage(),
        ConstraintLoadingStage(profile.get("constraint_files", []), profile.get("constraint_summary_md")),
        ProfileProjectAnalysisStage(profile),
        ProfileSkeletonGenerationStage(profile),
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
        - Code Agent 任务书：`{ctx.result / "MODEL_TASK.md"}`
        - Test Agent 任务书：`{ctx.result / "TEST_AGENT_TASK.md"}`
        - Validation Agent 任务书：`{ctx.result / "VALIDATION_AGENT_TASK.md"}`
        - Code Agent 入口：`{ctx.result / "harness" / "agent-entry" / "code-agent.json"}`
        - Test Agent 入口：`{ctx.result / "harness" / "agent-entry" / "test-agent.json"}`
        - Validation Agent 入口：`{ctx.result / "harness" / "agent-entry" / "validation-agent.json"}`
        - 执行 trace：`{ctx.logs / "trace" / "profile-harness-path.md"}`

        bootstrap 只生成轻量摘要、任务书、manifest、测试需求文件和 trace；
        未执行 CompileStage、RepairStage 或 ValidationStage。
        """,
    )
    write(
        ctx.result / "issues" / "00-summary.md",
        """
        # 转换摘要

        当前只完成 bootstrap，验证阶段未运行。
        后续按 INSTRUCTION.md 分发 Code Agent、Test Agent 和 Validation Agent。
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
