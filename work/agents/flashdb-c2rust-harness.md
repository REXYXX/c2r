# FlashDB C2Rust Harness Agent

## Purpose

This agent orchestrates a deterministic C-to-Rust migration workflow for the FlashDB contest task.  It is designed as a harness instead of a single translation pass, so every stage emits artifacts that can be inspected by the judge.

The framework is split into a reusable Python layer and a FlashDB profile:

- `work/harness/generic_harness.py` owns orchestration, context, trace logging,
  constraint loading, command execution, cargo result capture, and generic
  file/token checks.
- `work/profiles/flashdb.md` owns FlashDB-specific API tokens, one-to-one
  storage-engine parity tokens, weak-model rejection rules, source context
  hints, and required output files through a `json harness-profile` block.
- `work/harness/flashdb_harness.py` wires the FlashDB profile into the generic
  agents and implements only FlashDB-specific source analysis, generation, and
  validation glue. It should not hard-code FlashDB parity token matrices.
- `work/convert_flashdb.py` prepares the model work area, `MODEL_TASK.md`, and
  reports. It must not embed prewritten Rust implementations.

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
- Required logs:
  - `logs/interaction.md`
  - `logs/trace/`

## Constraint Documents

- `work/specs/flashdb_api_contract.md`: fixed public Rust API and behaviours.
- `work/specs/flashdb_one_to_one_contract.md`: strict storage-engine parity contract.
- `work/specs/rust_design_rules.md`: safe Rust implementation rules.
- `work/workflows/opencode_glm_flashdb_workflow.md`: stage-by-stage weak-model workflow.
- `work/prompts/opencode_glm_system_prompt.md`: recommended opencode system prompt.

## Workflow

1. `OutputScaffoldAgent`
   - Creates required `result/` and `logs/` artifact structure.
   - Ensures `logs/interaction.md` exists; it remains empty when there is no manual intervention.
   - Writes engineering trace artifacts under `logs/trace/`.

2. `ConstraintLoadingAgent`
   - Loads the weak-model guardrail documents.
   - Uses the FlashDB profile's constraint list instead of hard-coded generic
     harness constants.
   - Records document presence and SHA-256 hashes.
   - Writes `result/harness/00-constraints.json`.

3. `ProjectAnalysisAgent`
   - Inventories C source, headers, and tests.
   - Groups files into KVDB, TSDB, and port/platform buckets.
   - Writes `result/harness/01-analysis.json`.

4. `SkeletonGenerationAgent`
   - Creates a compilable Rust crate layout.
   - Writes `Cargo.toml` and `src/lib.rs`.

5. `ContextBuilderAgent`
   - Builds a minimum dependency/context index for target modules.
   - Records symbol-prefix hints such as `fdb_kv_`, `fdb_blob_`, `fdb_tsdb_`, and `fdb_tsl_`.
   - Writes `result/harness/03-context.json`.

6. `ParityMatrixAgent`
   - Builds `result/harness/04-function-parity.json`.
   - Reads FlashDB profile parity rules from `work/profiles/flashdb.md`.
   - Requires every public `fdb_*` API and every required internal storage
     mechanism to map to Rust code before translation is considered complete.

7. `TranslationAgent`
   - Emits model-facing instructions instead of Rust implementation strings.
   - Directs the model to implement FlashDB KVDB and TSDB storage-engine logic,
     not just high-level behaviours.
   - Directs the model to migrate every `TEST_RUN(...)` entry from
     `FlashDB/tests/fdb_kvdb_tc.c` and `FlashDB/tests/fdb_tsdb_tc.c` to Rust
     `#[test]` cases.
   - Directs the model to cover every unit-test and benchmark item from
     `FlashDB/tests/README_test.md`, including KVDB/TSDB benchmark operations
     from `FlashDB/tests/benchmark/bench_main.c`.

8. `CompileAgent`
   - Runs `cargo check` when Cargo is available.
   - Captures compiler output in `result/harness/05-compile.json`.

9. `RepairAgent`
   - Triage stage for compile results.
   - Records whether repair is needed in `result/harness/06-repair.json`.

10. `ValidationAgent`
   - Runs structural checks, fixed API symbol checks, C API parity checks,
     one-to-one feature checks, behaviour-model rejection checks, full
     translated test coverage checks, README/benchmark coverage checks, unsafe
     counting, and `cargo test` when Cargo is available.
   - Keeps FlashDB-specific pass/fail criteria in the profile so future projects
     can reuse the same generic harness with different constraints.
   - Writes `result/harness/07-validation.json`.

## Command

```bash
python3 work/run_opencode_flashdb.py \
  --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB
```

This command is non-interactive and strict. It returns `0` only when
`result/harness/07-validation.json` reports `status: "passed"`.
