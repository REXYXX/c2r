#!/usr/bin/env python3
"""确定性转换执行框架的通用基础组件。

项目领域合同、token 检查和生成约束应来自动态 profile 或可选覆盖文档。
本文件只负责编排、产物布局、约束加载和命令执行；trace 由具体 profile harness 输出。
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


LEGACY_TRACE_ARTIFACTS = (
    "execution-plan.json",
    "execution-path.json",
    "execution-path.md",
    "events.jsonl",
    "scaffold.json",
)

LEGACY_HARNESS_ARTIFACTS = (
    "00-events.json",
)


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
    stage_history: list[dict[str, Any]] = field(default_factory=list)

    def artifact(self, relative: str) -> Path:
        return self.result / "harness" / relative

    def trace_artifact(self, relative: str) -> Path:
        return self.logs / "trace" / relative


class HarnessStage:
    name = "HarnessStage"

    def run(self, ctx: ConversionContext) -> None:
        raise NotImplementedError

    def __call__(
        self,
        ctx: ConversionContext,
    ) -> None:
        started_at = time.monotonic()
        try:
            self.run(ctx)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            ctx.stage_history.append(
                {
                    "stage": self.name,
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    },
                }
            )
            raise
        duration_ms = int((time.monotonic() - started_at) * 1000)
        ctx.stage_history.append(
            {
                "stage": self.name,
                "status": "completed",
                "duration_ms": duration_ms,
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
        for relative in LEGACY_TRACE_ARTIFACTS:
            path = ctx.trace_artifact(relative)
            if path.exists():
                path.unlink()
        for relative in LEGACY_HARNESS_ARTIFACTS:
            path = ctx.artifact(relative)
            if path.exists():
                path.unlink()


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
        "logs_trace_profile_harness_path_json": (ctx.logs / "trace" / "profile-harness-path.json").is_file(),
        "logs_trace_profile_harness_path_md": (ctx.logs / "trace" / "profile-harness-path.md").is_file(),
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
