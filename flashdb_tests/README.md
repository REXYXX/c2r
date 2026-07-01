# FlashDB Rust Test Suite

This directory contains a full Rust test suite derived from the FlashDB C
source and Linux tests. It validates a structure-preserving Rust rewrite of
FlashDB with the public API defined in `work/specs/flashdb_api_contract.md`.

## Scope

- Functional tests: every `TEST_RUN(...)` entry from
  `FlashDB/tests/fdb_kvdb_tc.c` and `FlashDB/tests/fdb_tsdb_tc.c`.
- API compatibility tests: public Rust exports, method signatures, error
  behaviour, persistence, corrupt-data handling, status handling, iterator
  semantics, and structure-preserving helper modules.
- Performance tests: Rust equivalents of the C benchmark workload in
  `FlashDB/tests/benchmark/bench_main.c`.

## Layout

```text
flashdb_tests/
├── README.md
├── api_mapping.md
├── coverage_matrix.md
├── test_manifest.json
├── scripts/
│   ├── install_into_crate.py
│   └── run_flashdb_tests.py
└── tests/
    ├── api_compat_tests.rs
    ├── kvdb_functional_tests.rs
    ├── performance_tests.rs
    └── tsdb_functional_tests.rs
```

## Run Against a Generated Rust Crate

From the repository root:

```bash
python3 flashdb_tests/scripts/run_flashdb_tests.py --crate flashDB_rust --nocapture
```

The script copies all Rust files from `flashdb_tests/tests/` into the target
crate's `tests/` directory, then runs:

```bash
cargo test -- --nocapture
```

Set `FLASHDB_PERF_SCALE=quick` to reduce performance counts for smoke runs.
The performance tests print throughput metrics and assert semantic correctness
without hard machine-dependent timing thresholds.
