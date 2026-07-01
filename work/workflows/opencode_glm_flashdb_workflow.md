# Opencode + GLM FlashDB Workflow

This workflow is intended for weaker models. It narrows the task into small,
checkable stages and forbids broad rewrites after validation begins.

## Model Operating Rules

- The official opencode entrypoint is `python3 work/run_opencode_flashdb.py --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB`.
- Do not rely on manual hand-off text. The exit code and required artifact files are the delivery contract.
- Follow `work/specs/flashdb_api_contract.md` first.
- Follow `work/specs/rust_design_rules.md` second.
- Treat original FlashDB C files as source context, not as files to modify.
- Generate the Rust crate in `flashDB_rust/`.
- Preserve the original FlashDB module structure. Do not flatten the rewrite
  into only KVDB/TSDB files.
- Write audit output under `result/harness/`.
- Always create `result/`, `result/output.md`, `logs/interaction.md`, and `logs/trace/`.
- If there is no manual intervention, keep `logs/interaction.md` as an empty file.
- Keep every stage deterministic and non-interactive.
- Do not use `--skip-cargo` during official opencode testing.
- Prefer boring code that compiles over clever code.

## Non-Interactive Entrypoint

Official opencode testing must run:

```bash
python3 work/run_opencode_flashdb.py \
  --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB
```

The wrapper runs the harness with `--strict`. Return code `0` means validation
passed. Any non-zero return code is a failed submission. All diagnostics must be
written under `result/` and `logs/`; no human-written completion note is
required.

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
- `flashDB_rust/src/config.rs`
- `flashDB_rust/src/error.rs`
- `flashDB_rust/src/types.rs`
- `flashDB_rust/src/status.rs`
- `flashDB_rust/src/blob.rs`
- `flashDB_rust/src/db.rs`
- `flashDB_rust/src/file.rs`
- `flashDB_rust/src/low_level.rs`
- `flashDB_rust/src/sector.rs`
- `flashDB_rust/src/cache.rs`

Stop condition:

- crate metadata is valid
- public modules and re-exports match the API contract
- module layout maps to `inc/fdb_def.h`, `inc/fdb_low_lvl.h`, `src/fdb.c`,
  `src/fdb_file.c`, and `src/fdb_utils.c`

## Stage 3: KVDB

Required output:

- `flashDB_rust/src/kvdb.rs`
- KVDB tests in `flashDB_rust/tests/kvdb_tests.rs`
- one Rust `#[test]` for every KVDB `TEST_RUN(...)` entry in `FlashDB/tests/fdb_kvdb_tc.c`
- explicit translated structures for `fdb_kvdb`, `fdb_kv`, `fdb_kv_iterator`,
  `kvdb_sec_info`, and `kv_cache_node`

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
- one Rust `#[test]` for every TSDB `TEST_RUN(...)` entry in `FlashDB/tests/fdb_tsdb_tc.c`; duplicate source invocations must use stable disambiguated names
- explicit translated structures for `fdb_tsdb`, `fdb_tsl`, and `tsdb_sec_info`

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

- `result/` exists
- `result/output.md` exists and records successful output/self-validation
- `logs/` exists
- `logs/interaction.md` exists
- `logs/trace/` exists and contains engineering trace logs
- required files exist
- required public symbols exist
- required structure-preserving modules exist
- translated Rust tests cover all `FlashDB/tests` `TEST_RUN(...)` entries
- `unsafe` occurrence count is zero
- `cargo test` passes when Cargo is available

Required output:

- `result/harness/07-validation.json`

## Stage 7: Report

Required output:

- `result/output.md`
- `result/issues/00-summary.md`
- `logs/interaction.md`
- `logs/trace/`

The report must state:

- source path
- Rust project path
- implemented KVDB behaviours
- implemented TSDB behaviours
- full translated test coverage counts
- compile/test result
- known limitations

## Failure Policy

If a stage fails:

- record the exact failing command and stderr
- keep generated files for inspection
- do not hide failure by skipping validation unless Cargo is unavailable
- do not claim completion unless structural checks and tests pass
- in official opencode testing, return a non-zero exit code unless `result/harness/07-validation.json` has `status: "passed"`
