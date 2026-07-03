#!/usr/bin/env python3
"""Generic profile-driven C-to-Rust conversion harness entrypoint."""

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
from profile_harness import run_profile_harness  # noqa: E402


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a markdown-profile C-to-Rust conversion harness.")
    parser.add_argument("--profile", default="work/profiles/flashdb.md", help="Markdown profile containing json harness-profile")
    parser.add_argument("--source", default=None, help="Path to the source C project")
    parser.add_argument("--out", default=None, help="Output Rust project directory")
    parser.add_argument("--result", default="result", help="Result/report directory")
    parser.add_argument("--logs", default="logs", help="Logs directory with interaction and trace artifacts")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument("--skip-cargo", action="store_true", help="Skip cargo check/test even if cargo exists")
    parser.add_argument("--strict", action="store_true", help="Return non-zero unless validation status is passed")
    args = parser.parse_args()

    root = Path.cwd()
    profile_path = _resolve_path(root, args.profile)
    profile = load_markdown_profile(profile_path)
    source = args.source or profile.get("default_source")
    if not source:
        parser.error("--source is required when the profile has no default_source")
    out = args.out or output_dir_name(profile)
    ctx = ConversionContext(
        root=root,
        source=_resolve_path(root, str(source)),
        out=_resolve_path(root, out),
        result=_resolve_path(root, args.result),
        logs=_resolve_path(root, args.logs),
        cargo=args.cargo,
        skip_cargo=args.skip_cargo,
        profile=str(profile.get("profile", profile_path.stem)),
    )
    run_profile_harness(ctx, profile)
    validation_status = ctx.validation_result.get("status", "unknown")
    print(f"profile: {profile_path}")
    print(f"source project: {ctx.source}")
    print(f"generated Rust project: {ctx.out}")
    print(f"harness artifacts: {ctx.result / 'harness'}")
    print(f"log artifacts: {ctx.logs}")
    print(f"validation: {ctx.result / 'harness' / '07-validation.json'}")
    print(f"validation status: {validation_status}")
    if args.strict and validation_status != "passed":
        print("strict validation failed", file=sys.stderr)
        for failure in ctx.validation_result.get("failures", []):
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
