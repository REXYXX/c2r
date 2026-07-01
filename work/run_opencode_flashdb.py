#!/usr/bin/env python3
"""One-command non-interactive entrypoint for opencode FlashDB evaluation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_FLASHDB = "/app/code/judge-assets/02_02_c_to_rust/code/FlashDB"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run the strict FlashDB C-to-Rust harness for opencode.")
    parser.add_argument("--flashdb", default=DEFAULT_FLASHDB, help="Path to platform FlashDB source tree")
    parser.add_argument("--out", default="flashDB_rust", help="Output Rust project directory")
    parser.add_argument("--result", default="result", help="Required result/report directory")
    parser.add_argument("--logs", default="logs", help="Required logs directory")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    args = parser.parse_args()

    harness = root / "work" / "harness" / "flashdb_harness.py"
    command = [
        sys.executable,
        str(harness),
        "--flashdb",
        args.flashdb,
        "--out",
        args.out,
        "--result",
        args.result,
        "--logs",
        args.logs,
        "--cargo",
        args.cargo,
        "--strict",
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
