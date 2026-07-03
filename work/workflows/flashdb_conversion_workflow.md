# FlashDB Conversion Workflow

This workflow is the model-facing execution contract for the FlashDB profile. It
intentionally does not restate every command and output rule from
`INSTRUCTION.md`; use `INSTRUCTION.md` for repository entrypoints and use this
file for stage ordering, implementation guardrails, and stop conditions.

The workflow is implemented by the reusable profile harness in
`work/run_conversion.py`, `work/harness/profile_harness.py`, and
`work/harness/generic_harness.py`. FlashDB-specific API, parity, source mapping,
and test coverage checks stay in `work/profiles/flashdb.md`; generic Rust design
rules stay under `work/specs/`.

## Required Stage Order

Execution models must keep this stage order:

1. `BootstrapHarness`
2. `ReadHarnessArtifacts`
3. `ImplementRustCrate`
4. `RunCargoChecks`
5. `RunStrictHarness`

Do not begin Rust implementation before the bootstrap harness has generated
`flashDB_rust/MODEL_TASK.md`, `result/harness/03-context.json`,
`result/harness/04-function-parity.json`, and
`result/harness/07-validation.json`.

## Documents To Read

Read these documents before authoring Rust code:

1. `work/profiles/flashdb.md`
2. `work/specs/rust_design_rules.md`
3. `work/workflows/flashdb_conversion_workflow.md`

## Implementation Guardrails

- Treat the input FlashDB C tree as read-only source context.
- Write Rust implementation only under `flashDB_rust/`.
- Do not place Rust implementation strings in Python.
- Preserve the module boundaries required by `work/profiles/flashdb.md`.
- Keep the public Rust API and C parity token names required by the profile.
- Use safe Rust only; `unsafe` must remain absent.
- Do not use C FFI or external dependencies unless the profile is changed to
  allow them.
- Do not replace the FlashDB storage engine with a high-level behaviour model.
- Use sector-addressed file storage equivalent to FlashDB, not a single custom
  `flashdb.dat` backend.

## Source Translation Scope

The Rust implementation must cover the core FlashDB source areas declared in
the profile:

- shared config, types, status, blob, DB core, file, low-level, sector, and
  cache modules
- KVDB sector/node/status/CRC/default-KV/GC/recovery/iterator/blob behaviour
- TSDB sector/log-index/status/max_len/rollover/time-order/callback behaviour
- every public API token and one-to-one feature listed in
  `work/profiles/flashdb.md`

## Test And Benchmark Scope

Rust tests must cover all profile-declared source tests and README coverage:

- every `TEST_RUN(...)` entry in `FlashDB/tests/fdb_kvdb_tc.c`
- every `TEST_RUN(...)` entry in `FlashDB/tests/fdb_tsdb_tc.c`
- every unit-test item declared by `FlashDB/tests/README_test.md`
- every benchmark operation declared by `FlashDB/tests/benchmark/bench_main.c`
- `tests/benchmark_tests.rs` with semantic benchmark assertions, not fixed
  wall-clock thresholds

Duplicate source test names must use the stable target names declared in
`duplicate_test_name_map`.

## Cargo And Validation

After each substantial generation or repair step, run:

```bash
cd flashDB_rust
cargo check
cargo test
```

The final strict harness must pass. A run is complete only when
`result/harness/07-validation.json` reports `status: "passed"`.

## Failure Policy

- Keep generated files for inspection when a stage fails.
- Use validation failures as repair guidance, not as reasons to weaken profile
  checks.
- Fix compile errors before redesigning APIs.
- Do not claim completion when one-to-one feature checks fail, even if
  high-level tests pass.
- Do not use `--skip-cargo` for final validation.
