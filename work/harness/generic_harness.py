#!/usr/bin/env python3
"""Reusable primitives for deterministic conversion harnesses.

Project-specific harnesses should keep domain contracts, token checks, and
translation generators outside this module.  This file owns only orchestration,
artifact layout, trace logging, constraint loading, and command execution.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import textwrap
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

    def artifact(self, relative: str) -> Path:
        return self.result / "harness" / relative

    def trace_artifact(self, relative: str) -> Path:
        return self.logs / "trace" / relative

    def log(self, agent: str, status: str, **data: Any) -> None:
        event = {
            "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "profile": self.profile,
            "agent": agent,
            "status": status,
            **data,
        }
        self.events.append(event)
        trace_path = self.trace_artifact("events.jsonl")
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")


class Agent:
    name = "Agent"

    def run(self, ctx: ConversionContext) -> None:
        raise NotImplementedError

    def __call__(self, ctx: ConversionContext) -> None:
        ctx.log(self.name, "started")
        self.run(ctx)
        ctx.log(self.name, "completed")


class OutputScaffoldAgent(Agent):
    name = "OutputScaffoldAgent"

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


class ConstraintLoadingAgent(Agent):
    name = "ConstraintLoadingAgent"

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
        # Constraint Loading

        The harness loaded the `{ctx.profile}` profile constraints before source analysis.

        Required documents:

        {bullet_list}
        """


class CompileAgent(Agent):
    name = "CompileAgent"

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


class RepairAgent(Agent):
    name = "RepairAgent"

    def run(self, ctx: ConversionContext) -> None:
        status = ctx.compile_result.get("status")
        repair = {
            "status": "not_needed" if status in {"passed", "skipped"} else "manual_review_required",
            "compile_status": status,
            "notes": [],
        }
        if status == "failed":
            repair["notes"].append("The deterministic generator produced code that cargo check rejected.")
            repair["notes"].append("Inspect result/harness/05-compile.json for compiler diagnostics.")
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


def run_agents(ctx: ConversionContext, agents: Sequence[Agent]) -> ConversionContext:
    for agent in agents:
        agent(ctx)
    write(ctx.artifact("00-events.json"), json.dumps(ctx.events, indent=2, ensure_ascii=False))
    return ctx
