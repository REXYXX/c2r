#!/usr/bin/env python3
"""确定性转换执行框架的通用基础组件。

项目领域合同、token 检查和生成约束应来自动态 profile 或可选覆盖文档。
本文件只负责编排、产物布局、trace 记录、约束加载和命令执行。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(data).lstrip(), encoding="utf-8", newline="\n")


def text_block(value: str) -> str:
    return textwrap.dedent(value).lstrip()


def load_markdown_profile(path: Path, block_name: str = "harness-profile") -> dict[str, Any]:
    """Load a structured JSON profile embedded in a markdown document."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    pattern = rf"```[ \t]*json[ \t]+{re.escape(block_name)}[ \t]*\n(.*?)\n```"
    match = re.search(pattern, text, re.DOTALL)
    if match is None:
        raise ValueError(f"missing fenced JSON block: json {block_name} in {path}")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError(f"profile block must decode to a JSON object: {path}")
    return data


HARNESS_STAGE_DESCRIPTIONS = {
    "OutputScaffoldStage": "创建 result/logs/trace 基础目录和交互日志。",
    "ConstraintLoadingStage": "加载通用 Rust 设计规则和可选覆盖 profile。",
    "ProjectAnalysisStage": "扫描 C 源码并生成 derived/effective profile。",
    "SkeletonGenerationStage": "准备 Cargo crate 外壳，不写 Rust 实现。",
    "ContextBuilderStage": "生成模块上下文、函数线索和公共 API 索引。",
    "ParityMatrixStage": "生成公共 API 与源码模块 parity 矩阵。",
    "TranslationStage": "生成 MODEL_TASK.md 和模型生成指引。",
    "CompileStage": "执行或跳过 cargo check，并记录诊断。",
    "RepairStage": "整理编译结果和修复判断。",
    "ValidationStage": "执行结构、API、测试覆盖和 cargo test 验证。",
}


HARNESS_STAGE_OUTPUTS = {
    "OutputScaffoldStage": [
        "logs/interaction.md",
        "logs/trace/scaffold.json",
        "logs/trace/events.jsonl",
        "logs/trace/execution-plan.json",
    ],
    "ConstraintLoadingStage": [
        "result/harness/00-constraints.json",
        "result/harness/00-constraints.md",
    ],
    "ProjectAnalysisStage": [
        "result/harness/01-analysis.json",
        "result/harness/01-derived-profile.json",
        "result/harness/01-effective-profile.json",
        "result/harness/01-effective-profile.md",
        "result/harness/01-dependency-map.md",
    ],
    "SkeletonGenerationStage": [
        "out/Cargo.toml",
        "result/harness/02-skeleton.md",
    ],
    "ContextBuilderStage": [
        "result/harness/03-context.json",
    ],
    "ParityMatrixStage": [
        "result/harness/04-function-parity.json",
    ],
    "TranslationStage": [
        "out/MODEL_TASK.md",
        "result/harness/04-model-generation-brief.md",
        "result/harness/04-translation.md",
    ],
    "CompileStage": [
        "result/harness/05-compile.json",
    ],
    "RepairStage": [
        "result/harness/06-repair.json",
    ],
    "ValidationStage": [
        "result/harness/07-validation.json",
        "result/output.md",
        "result/issues/00-summary.md",
    ],
}


@dataclass
class ConversionContext:
    root: Path
    source: Path
    out: Path
    result: Path
    logs: Path
    cargo: str = "cargo"
    skip_cargo: bool = False
    profile: str = "generic"
    analysis: dict[str, Any] = field(default_factory=dict)
    context_index: dict[str, Any] = field(default_factory=dict)
    compile_result: dict[str, Any] = field(default_factory=dict)
    validation_result: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    execution_plan: list[dict[str, Any]] = field(default_factory=list)
    execution_path: list[dict[str, Any]] = field(default_factory=list)

    def artifact(self, relative: str) -> Path:
        return self.result / "harness" / relative

    def trace_artifact(self, relative: str) -> Path:
        return self.logs / "trace" / relative

    def log(self, stage: str, status: str, **data: Any) -> dict[str, Any]:
        event = {
            "event_index": len(self.events) + 1,
            "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "profile": self.profile,
            "stage": stage,
            "status": status,
            **data,
        }
        self.events.append(event)
        trace_path = self.trace_artifact("events.jsonl")
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def initialize_execution_plan(self, stages: Sequence["HarnessStage"]) -> None:
        self.events = []
        write(self.trace_artifact("events.jsonl"), "")
        self.execution_plan = [
            {
                "step": index,
                "stage": stage.name,
                "description": HARNESS_STAGE_DESCRIPTIONS.get(stage.name, ""),
                "expected_outputs": HARNESS_STAGE_OUTPUTS.get(stage.name, []),
            }
            for index, stage in enumerate(stages, start=1)
        ]
        write(
            self.trace_artifact("execution-plan.json"),
            json.dumps(
                {
                    "profile": self.profile,
                    "source": str(self.source),
                    "out": str(self.out),
                    "result": str(self.result),
                    "logs": str(self.logs),
                    "expected_sequence": [item["stage"] for item in self.execution_plan],
                    "plan": self.execution_plan,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
        self.write_execution_path()

    def record_stage(self, record: dict[str, Any]) -> None:
        self.execution_path.append(record)
        self.write_execution_path()

    def stage_outputs(self, stage_name: str) -> list[dict[str, Any]]:
        outputs = []
        for logical_path in HARNESS_STAGE_OUTPUTS.get(stage_name, []):
            resolved = self.resolve_logical_path(logical_path)
            outputs.append(
                {
                    "path": logical_path,
                    "resolved": str(resolved),
                    "exists": resolved.exists(),
                    "bytes": resolved.stat().st_size if resolved.is_file() else None,
                }
            )
        return outputs

    def resolve_logical_path(self, logical_path: str) -> Path:
        if logical_path.startswith("result/"):
            return self.result / logical_path[len("result/") :]
        if logical_path.startswith("logs/"):
            return self.logs / logical_path[len("logs/") :]
        if logical_path.startswith("out/"):
            return self.out / logical_path[len("out/") :]
        return self.root / logical_path

    def write_execution_path(self) -> None:
        expected = [item["stage"] for item in self.execution_plan]
        actual = [item["stage"] for item in self.execution_path]
        prefix_ok = actual == expected[: len(actual)]
        completed = len(actual) == len(expected) and all(item.get("status") == "completed" for item in self.execution_path)
        order_ok = prefix_ok and all(item.get("order_ok", False) for item in self.execution_path)
        payload = {
            "profile": self.profile,
            "source": str(self.source),
            "out": str(self.out),
            "result": str(self.result),
            "logs": str(self.logs),
            "expected_sequence": expected,
            "actual_sequence": actual,
            "order_ok": order_ok,
            "completed": completed,
            "plan": self.execution_plan,
            "path": self.execution_path,
        }
        write(self.trace_artifact("execution-path.json"), json.dumps(payload, indent=2, ensure_ascii=False))
        write(self.trace_artifact("execution-path.md"), self._execution_path_markdown(payload))

    def _execution_path_markdown(self, payload: dict[str, Any]) -> str:
        rows = []
        records_by_step = {record.get("step"): record for record in self.execution_path}
        for planned in self.execution_plan:
            record = records_by_step.get(planned["step"], {})
            status = record.get("status", "pending")
            duration = record.get("duration_ms", "")
            order = "yes" if record.get("order_ok") else ("pending" if status == "pending" else "no")
            outputs = record.get("outputs", [])
            output_summary = f"{sum(1 for item in outputs if item.get('exists'))}/{len(outputs)}" if outputs else "-"
            rows.append(f"| {planned['step']} | `{planned['stage']}` | {status} | {order} | {duration} | {output_summary} |")
        table = "\n".join(rows)
        return f"""
        # Harness 执行路径

        - 顺序符合预期：`{payload["order_ok"]}`
        - 已完成全部阶段：`{payload["completed"]}`
        - 预期顺序：`{" -> ".join(payload["expected_sequence"])}`
        - 实际顺序：`{" -> ".join(payload["actual_sequence"]) or "尚未开始"}`

        | Step | HarnessStage | Status | Order OK | Duration ms | Outputs |
        | --- | --- | --- | --- | --- | --- |
        {table}
        """


class HarnessStage:
    name = "HarnessStage"

    def run(self, ctx: ConversionContext) -> None:
        raise NotImplementedError

    def __call__(
        self,
        ctx: ConversionContext,
        *,
        step_index: int | None = None,
        total_steps: int | None = None,
        expected_stage: str | None = None,
    ) -> None:
        order_ok = expected_stage in {None, self.name}
        stage_data = {
            "step": step_index,
            "total_steps": total_steps,
            "expected_stage": expected_stage,
            "order_ok": order_ok,
        }
        started_at = time.monotonic()
        started_event = ctx.log(self.name, "started", **stage_data)
        try:
            self.run(ctx)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            completed_event = ctx.log(
                self.name,
                "failed",
                **stage_data,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            ctx.record_stage(
                {
                    **stage_data,
                    "stage": self.name,
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "started_event_index": started_event["event_index"],
                    "completed_event_index": completed_event["event_index"],
                    "outputs": ctx.stage_outputs(self.name),
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    },
                }
            )
            raise
        duration_ms = int((time.monotonic() - started_at) * 1000)
        completed_event = ctx.log(self.name, "completed", **stage_data, duration_ms=duration_ms)
        ctx.record_stage(
            {
                **stage_data,
                "stage": self.name,
                "status": "completed",
                "duration_ms": duration_ms,
                "started_event_index": started_event["event_index"],
                "completed_event_index": completed_event["event_index"],
                "outputs": ctx.stage_outputs(self.name),
            }
        )


class OutputScaffoldStage(HarnessStage):
    name = "OutputScaffoldStage"

    def run(self, ctx: ConversionContext) -> None:
        ctx.result.mkdir(parents=True, exist_ok=True)
        (ctx.result / "issues").mkdir(parents=True, exist_ok=True)
        (ctx.result / "harness").mkdir(parents=True, exist_ok=True)
        ctx.logs.mkdir(parents=True, exist_ok=True)
        (ctx.logs / "trace").mkdir(parents=True, exist_ok=True)
        interaction = ctx.logs / "interaction.md"
        if not interaction.exists():
            write(interaction, "")
        write(
            ctx.trace_artifact("scaffold.json"),
            json.dumps(
                {
                    "profile": ctx.profile,
                    "result_dir": str(ctx.result),
                    "required_result_output": str(ctx.result / "output.md"),
                    "logs_dir": str(ctx.logs),
                    "required_interaction_log": str(interaction),
                    "trace_dir": str(ctx.logs / "trace"),
                },
                indent=2,
                ensure_ascii=False,
            ),
        )


class ConstraintLoadingStage(HarnessStage):
    name = "ConstraintLoadingStage"

    def __init__(self, constraint_files: Sequence[str], summary_markdown: str | None = None) -> None:
        self.constraint_files = list(constraint_files)
        self.summary_markdown = summary_markdown

    def run(self, ctx: ConversionContext) -> None:
        constraints = []
        for relative in self.constraint_files:
            path = ctx.root / relative
            item: dict[str, Any] = {
                "path": relative,
                "exists": path.exists(),
            }
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
                item.update(
                    {
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "bytes": len(text.encode("utf-8")),
                        "lines": text.count("\n") + 1 if text else 0,
                    }
                )
            constraints.append(item)
        ctx.constraints = constraints
        write(ctx.artifact("00-constraints.json"), json.dumps({"constraints": constraints}, indent=2, ensure_ascii=False))
        write(ctx.artifact("00-constraints.md"), self._summary_markdown(ctx))

    def _summary_markdown(self, ctx: ConversionContext) -> str:
        if self.summary_markdown is not None:
            return self.summary_markdown
        bullet_list = "\n".join(f"- `{path}`" for path in self.constraint_files)
        return f"""
        # 约束加载

        执行框架已在源码分析前加载 `{ctx.profile}` 的通用约束和可选覆盖项。

        必读文档：

        {bullet_list}
        """


class CompileStage(HarnessStage):
    name = "CompileStage"

    def run(self, ctx: ConversionContext) -> None:
        cargo_path = shutil.which(ctx.cargo)
        if ctx.skip_cargo or cargo_path is None:
            ctx.compile_result = {
                "status": "skipped",
                "reason": "cargo not found" if cargo_path is None else "disabled by --skip-cargo",
            }
        else:
            ctx.compile_result = run_cargo(ctx, ["check"])
        write(ctx.artifact("05-compile.json"), json.dumps(ctx.compile_result, indent=2, ensure_ascii=False))


class RepairStage(HarnessStage):
    name = "RepairStage"

    def run(self, ctx: ConversionContext) -> None:
        status = ctx.compile_result.get("status")
        repair = {
            "status": "not_needed" if status in {"passed", "skipped"} else "manual_review_required",
            "compile_status": status,
            "notes": [],
        }
        if status == "failed":
            repair["notes"].append("生成结果未通过 cargo check。")
            repair["notes"].append("请查看 result/harness/05-compile.json 中的编译诊断。")
        write(ctx.artifact("06-repair.json"), json.dumps(repair, indent=2, ensure_ascii=False))


def run_cargo(ctx: ConversionContext, args: Sequence[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [ctx.cargo, *args],
        cwd=ctx.out,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "status": "passed" if proc.returncode == 0 else "failed",
        "command": [ctx.cargo, *args],
        "returncode": proc.returncode,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-8000:],
    }


def count_token_in_rust(out: Path, token: str) -> int:
    count = 0
    for path in out.rglob("*.rs"):
        try:
            count += path.read_text(encoding="utf-8", errors="ignore").count(token)
        except OSError:
            pass
    return count


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def check_required_files(root: Path, relative_paths: Iterable[str]) -> dict[str, bool]:
    return {relative: (root / relative).is_file() for relative in relative_paths}


def check_artifact_structure(ctx: ConversionContext, extra: Iterable[tuple[str, Path]] = ()) -> dict[str, bool]:
    checks = {
        "result_dir": ctx.result.is_dir(),
        "result_output_md": (ctx.result / "output.md").is_file(),
        "result_issues_summary": (ctx.result / "issues" / "00-summary.md").is_file(),
        "logs_dir": ctx.logs.is_dir(),
        "logs_interaction_md": (ctx.logs / "interaction.md").is_file(),
        "logs_trace_dir": (ctx.logs / "trace").is_dir(),
        "logs_trace_events": (ctx.logs / "trace" / "events.jsonl").is_file(),
        "logs_trace_execution_plan": (ctx.logs / "trace" / "execution-plan.json").is_file(),
        "logs_trace_execution_path": (ctx.logs / "trace" / "execution-path.json").is_file(),
    }
    checks.update({name: path.is_file() if path.suffix else path.exists() for name, path in extra})
    return checks


def check_token_map(out: Path, expected: dict[str, Sequence[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for relative, symbols in expected.items():
        text = read_text(out / relative)
        missing = [symbol for symbol in symbols if symbol not in text]
        result[relative] = {
            "ok": not missing,
            "missing": missing,
        }
    return result


def run_stages(ctx: ConversionContext, stages: Sequence[HarnessStage]) -> ConversionContext:
    ctx.initialize_execution_plan(stages)
    ctx.log("Harness", "plan_created", total_steps=len(stages), expected_sequence=[stage.name for stage in stages])
    for index, stage in enumerate(stages, start=1):
        stage(ctx, step_index=index, total_steps=len(stages), expected_stage=ctx.execution_plan[index - 1]["stage"])
    write(ctx.artifact("00-events.json"), json.dumps(ctx.events, indent=2, ensure_ascii=False))
    ctx.write_execution_path()
    return ctx
