#!/usr/bin/env python3
"""Profile harness trace recording."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from generic_harness import ConversionContext, write

def _reset_profile_trace(ctx: ConversionContext) -> None:
    setattr(ctx, "_profile_harness_trace", [])


def _profile_trace_entries(ctx: ConversionContext) -> list[dict[str, Any]]:
    trace = getattr(ctx, "_profile_harness_trace", None)
    if isinstance(trace, list):
        return trace
    trace = []
    setattr(ctx, "_profile_harness_trace", trace)
    return trace


def _profile_trace_summary(entry: dict[str, Any]) -> str:
    details = {
        key: value
        for key, value in entry.items()
        if key not in {"index", "time", "stage", "action"}
    }
    if not details:
        return ""
    summary = json.dumps(details, ensure_ascii=False)
    return summary if len(summary) <= 240 else summary[:237] + "..."


def _profile_trace_markdown(payload: dict[str, Any]) -> str:
    rows = [
        f"| {entry['index']} | `{entry['stage']}` | `{entry['action']}` | {_profile_trace_summary(entry)} |"
        for entry in payload["path"]
    ]
    return "\n".join(
        [
            "# Profile Harness 执行路径",
            "",
            f"- profile: `{payload['profile']}`",
            f"- source: `{payload['source']}`",
            f"- out: `{payload['out']}`",
            f"- result: `{payload['result']}`",
            "",
            "| Step | 节点 | 动作 | 关键数据 |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
        ]
    )


def _record_profile_trace(ctx: ConversionContext, stage: str, action: str, **data: Any) -> None:
    trace = _profile_trace_entries(ctx)
    trace.append(
        {
            "index": len(trace) + 1,
            "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "action": action,
            **data,
        }
    )
    payload = {
        "profile": ctx.profile,
        "source": str(ctx.source),
        "out": str(ctx.out),
        "result": str(ctx.result),
        "logs": str(ctx.logs),
        "path": trace,
    }
    write(ctx.trace_artifact("profile-harness-path.json"), json.dumps(payload, indent=2, ensure_ascii=False))
    write(ctx.trace_artifact("profile-harness-path.md"), _profile_trace_markdown(payload))
