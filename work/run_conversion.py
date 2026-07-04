#!/usr/bin/env python3
"""源码驱动的通用 C 到 Rust 转换入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
HARNESS = WORK / "harness"
for path in (HARNESS, WORK):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from generic_harness import ConversionContext, load_markdown_profile  # noqa: E402
from model_artifacts import output_dir_name  # noqa: E402
from profile_generator import build_dynamic_profile  # noqa: E402
from profile_harness import run_profile_harness  # noqa: E402


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="运行源码动态 profile 驱动的 C 到 Rust 转换执行框架。")
    parser.add_argument("--profile", default=None, help="可选 markdown 覆盖 profile；未提供时完全从 --source 动态生成")
    parser.add_argument("--source", default=None, help="源 C 项目路径")
    parser.add_argument("--out", default=None, help="Rust 输出项目目录")
    parser.add_argument("--result", default="result", help="结果/报告目录")
    parser.add_argument("--logs", default="logs", help="交互记录和 trace 产物日志目录")
    parser.add_argument("--cargo", default="cargo", help="Cargo 可执行文件")
    parser.add_argument("--skip-cargo", action="store_true", help="即使存在 cargo 也跳过 cargo check/test")
    parser.add_argument("--validate", action="store_true", help="运行 compile/repair/validation 阶段；--strict 会自动启用")
    parser.add_argument("--strict", action="store_true", help="验证状态不是 passed 时返回非零")
    args = parser.parse_args()

    root = Path.cwd()
    profile_path: Path | None = None
    overrides = {}
    if args.profile:
        profile_path = _resolve_path(root, args.profile)
        overrides = load_markdown_profile(profile_path)
    source = args.source or overrides.get("default_source")
    if not source:
        parser.error("未提供 --source，且覆盖 profile 没有 default_source")
    source_path = _resolve_path(root, str(source))
    profile = build_dynamic_profile(source_path, overrides)
    out = args.out or output_dir_name(profile)
    ctx = ConversionContext(
        root=root,
        source=source_path,
        out=_resolve_path(root, out),
        result=_resolve_path(root, args.result),
        logs=_resolve_path(root, args.logs),
        cargo=args.cargo,
        skip_cargo=args.skip_cargo,
        profile=str(profile.get("profile") or (profile_path.stem if profile_path else "project")),
    )
    include_validation = args.validate or args.strict
    run_profile_harness(ctx, profile, include_validation=include_validation)
    validation_status = ctx.validation_result.get("status", "not_run")
    print(f"profile：{profile_path if profile_path else '动态生成'}")
    print(f"源项目：{ctx.source}")
    print(f"生成的 Rust 项目：{ctx.out}")
    print(f"执行框架产物：{ctx.result / 'harness'}")
    print(f"动态 profile：{ctx.result / 'harness' / '01-effective-profile.md'}")
    print(f"日志产物：{ctx.logs}")
    if include_validation:
        print(f"验证文件：{ctx.result / 'harness' / '07-validation.json'}")
        print(f"验证状态：{validation_status}")
    else:
        print("验证阶段：未运行（bootstrap 阶段只生成任务书和动态 profile）")
    if args.strict and validation_status != "passed":
        print("严格验证失败", file=sys.stderr)
        for failure in ctx.validation_result.get("failures", []):
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
