#!/usr/bin/env python3
"""Install and run the FlashDB Rust test suite."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from install_into_crate import install


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FlashDB Rust full test suite.")
    parser.add_argument("--crate", default="flashDB_rust", help="Path to the generated Rust crate")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument("--no-copy", action="store_true", help="Run cargo test without copying tests first")
    parser.add_argument("--nocapture", action="store_true", help="Pass --nocapture to cargo test")
    args = parser.parse_args()
    suite_root = Path(__file__).resolve().parents[1]
    crate = Path(args.crate).resolve()
    if not args.no_copy:
        install(crate, suite_root)
    command = [args.cargo, "test"]
    if args.nocapture:
        command.extend(["--", "--nocapture"])
    return subprocess.run(command, cwd=crate, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
