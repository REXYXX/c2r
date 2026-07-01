#!/usr/bin/env python3
"""Install the FlashDB Rust test suite into a target Cargo crate."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


TEST_FILE_NAMES = [
    "api_compat_tests.rs",
    "kvdb_functional_tests.rs",
    "performance_tests.rs",
    "tsdb_functional_tests.rs",
]


def install(crate: Path, suite_root: Path) -> list[Path]:
    tests_src = suite_root / "tests"
    tests_dst = crate / "tests"
    if not crate.exists():
        raise FileNotFoundError(f"target crate does not exist: {crate}")
    if not (crate / "Cargo.toml").is_file():
        raise FileNotFoundError(f"target crate has no Cargo.toml: {crate}")
    tests_dst.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in TEST_FILE_NAMES:
        source = tests_src / name
        target = tests_dst / name
        shutil.copy2(source, target)
        installed.append(target)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy FlashDB Rust tests into a Cargo crate.")
    parser.add_argument("--crate", default="flashDB_rust", help="Path to the generated Rust crate")
    args = parser.parse_args()
    suite_root = Path(__file__).resolve().parents[1]
    for path in install(Path(args.crate).resolve(), suite_root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
