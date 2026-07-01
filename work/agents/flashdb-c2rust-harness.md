# FlashDB C2Rust Harness Agent

## Purpose

This agent orchestrates a deterministic C-to-Rust migration workflow for the FlashDB contest task.  It is designed as a harness instead of a single translation pass, so every stage emits artifacts that can be inspected by the judge.

For opencode + GLM5.1 or other weaker models, the harness is intentionally
constraint-first: load the API contract and workflow documents before generating
or repairing Rust code.

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

## Constraint Documents

- `work/specs/flashdb_api_contract.md`: fixed public Rust API and behaviours.
- `work/specs/rust_design_rules.md`: safe Rust implementation rules.
- `work/workflows/opencode_glm_flashdb_workflow.md`: stage-by-stage weak-model workflow.
- `work/prompts/opencode_glm_system_prompt.md`: recommended opencode system prompt.

## Workflow

1. `ConstraintLoadingAgent`
   - Loads the weak-model guardrail documents.
   - Records document presence and SHA-256 hashes.
   - Writes `result/harness/00-constraints.json`.

2. `ProjectAnalysisAgent`
   - Inventories C source, headers, and tests.
   - Groups files into KVDB, TSDB, and port/platform buckets.
   - Writes `result/harness/01-analysis.json`.

3. `SkeletonGenerationAgent`
   - Creates a compilable Rust crate layout.
   - Writes `Cargo.toml` and `src/lib.rs`.

4. `ContextBuilderAgent`
   - Builds a minimum dependency/context index for target modules.
   - Records symbol-prefix hints such as `fdb_kv_`, `fdb_blob_`, `fdb_tsdb_`, and `fdb_tsl_`.
   - Writes `result/harness/03-context.json`.

5. `TranslationAgent`
   - Emits safe Rust implementations for key-value and time-series behaviours.
   - Migrates representative tests to Rust `#[test]` cases.

6. `CompileAgent`
   - Runs `cargo check` when Cargo is available.
   - Captures compiler output in `result/harness/05-compile.json`.

7. `RepairAgent`
   - Triage stage for compile results.
   - Records whether repair is needed in `result/harness/06-repair.json`.

8. `ValidationAgent`
   - Runs structural checks, fixed API symbol checks, unsafe counting, and `cargo test` when Cargo is available.
   - Writes `result/harness/07-validation.json`.

## Command

```bash
python3 work/harness/flashdb_harness.py \
  --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result
```
