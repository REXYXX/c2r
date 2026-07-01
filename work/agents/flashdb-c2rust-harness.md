# FlashDB C2Rust Harness Agent

## Purpose

This agent orchestrates a deterministic C-to-Rust migration workflow for the FlashDB contest task.  It is designed as a harness instead of a single translation pass, so every stage emits artifacts that can be inspected by the judge.

## Input

- FlashDB C project root: `/app/code/judge-assets/02_02_c_to_rust/code/FlashDB`
- Expected important subdirectories:
  - `src/`
  - `tests/`
  - `inc/` or `include/` when present

## Output

- Rust project: `flashDB_rust/`
- Harness artifacts: `result/harness/`
- Summary reports:
  - `result/output.md`
  - `result/issues/00-summary.md`

## Workflow

1. `ProjectAnalysisAgent`
   - Inventories C source, headers, and tests.
   - Groups files into KVDB, TSDB, and port/platform buckets.
   - Writes `result/harness/01-analysis.json`.

2. `SkeletonGenerationAgent`
   - Creates a compilable Rust crate layout.
   - Writes `Cargo.toml` and `src/lib.rs`.

3. `ContextBuilderAgent`
   - Builds a minimum dependency/context index for target modules.
   - Records symbol-prefix hints such as `fdb_kv_`, `fdb_blob_`, `fdb_tsdb_`, and `fdb_tsl_`.
   - Writes `result/harness/03-context.json`.

4. `TranslationAgent`
   - Emits safe Rust implementations for key-value and time-series behaviours.
   - Migrates representative tests to Rust `#[test]` cases.

5. `CompileAgent`
   - Runs `cargo check` when Cargo is available.
   - Captures compiler output in `result/harness/05-compile.json`.

6. `RepairAgent`
   - Triage stage for compile results.
   - Records whether repair is needed in `result/harness/06-repair.json`.

7. `ValidationAgent`
   - Runs structural checks and `cargo test` when Cargo is available.
   - Writes `result/harness/07-validation.json`.

## Command

```bash
python3 work/harness/flashdb_harness.py \
  --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result
```
