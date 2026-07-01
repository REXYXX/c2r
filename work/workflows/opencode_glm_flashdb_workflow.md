# Opencode + GLM FlashDB Workflow

This workflow is intended for weaker models. It narrows the task into small,
checkable stages and forbids broad rewrites after validation begins.

## Model Operating Rules

- Follow `work/specs/flashdb_api_contract.md` first.
- Follow `work/specs/rust_design_rules.md` second.
- Treat original FlashDB C files as source context, not as files to modify.
- Generate the Rust crate in `flashDB_rust/`.
- Write audit output under `result/harness/`.
- Keep every stage deterministic and non-interactive.
- Prefer boring code that compiles over clever code.

## Stage 1: Source Inventory

Inputs:

- `FlashDB/src`
- `FlashDB/inc` or `FlashDB/include`
- `FlashDB/tests`

Required output:

- list C sources, headers, and tests
- bucket files into `kvdb`, `tsdb`, and `port`
- record symbol hints for:
  - `fdb_kv_`
  - `fdb_blob_`
  - `fdb_tsdb_`
  - `fdb_tsl_`

Stop condition:

- `result/harness/01-analysis.json` exists.

## Stage 2: Skeleton

Required output:

- `flashDB_rust/Cargo.toml`
- `flashDB_rust/src/lib.rs`

Stop condition:

- crate metadata is valid
- public modules and re-exports match the API contract

## Stage 3: KVDB

Required output:

- `flashDB_rust/src/kvdb.rs`
- KVDB tests in `flashDB_rust/tests/kvdb_tests.rs`

Allowed implementation:

- `BTreeMap<String, Vec<u8>>`
- binary file persistence with a magic header
- safe helper parser functions

Stop condition:

- KVDB contract symbols are present
- KVDB tests cover set/get/update/delete/blob/persistence

## Stage 4: TSDB

Required output:

- `flashDB_rust/src/tsdb.rs`
- TSDB tests in `flashDB_rust/tests/tsdb_tests.rs`

Allowed implementation:

- `Vec<TimeSeriesRecord>`
- sort records by timestamp after append/decode
- inclusive query range
- binary file persistence with a magic header

Stop condition:

- TSDB contract symbols are present
- TSDB tests cover append/order/query/latest/persistence

## Stage 5: Compile

Run:

```bash
cd flashDB_rust
cargo check
```

Required output:

- `result/harness/05-compile.json`

Repair rule:

- If compile fails, fix only compile errors first. Do not redesign APIs.

## Stage 6: Validate

Run:

```bash
cd flashDB_rust
cargo test
```

Required checks:

- required files exist
- required public symbols exist
- `unsafe` occurrence count is zero
- `cargo test` passes when Cargo is available

Required output:

- `result/harness/07-validation.json`

## Stage 7: Report

Required output:

- `result/output.md`
- `result/issues/00-summary.md`

The report must state:

- source path
- Rust project path
- implemented KVDB behaviours
- implemented TSDB behaviours
- compile/test result
- known limitations

## Failure Policy

If a stage fails:

- record the exact failing command and stderr
- keep generated files for inspection
- do not hide failure by skipping validation unless Cargo is unavailable
- do not claim completion unless structural checks and tests pass
