# Rust Design Rules

These rules are project-neutral constraints for model-guided C-to-Rust
conversion. Project-specific APIs, module names, parity tokens, source mappings,
and test matrices belong in the selected markdown profile.

## Core Principles

- Prefer safe, idiomatic Rust while preserving the source project's observable
  semantics and ownership boundaries.
- Keep the generated crate small enough to audit, but do not collapse unrelated
  source subsystems into an opaque behaviour-only model.
- Use explicit data structures for source-domain concepts instead of hiding all
  state behind generic maps or vectors.
- Keep public APIs stable once the profile has declared them.
- Keep helper functions private unless the profile explicitly requires them to
  be public.
- Avoid global mutable state.
- Do not use `unsafe` unless the profile explicitly permits it.
- Do not use C FFI unless the profile explicitly permits it.
- Do not add external dependencies unless the profile explicitly permits them.

## Error Handling

- Return `Result` for I/O, parsing, corruption, overflow, and invalid-state
  errors.
- Return `Option` when absence is an expected lookup result.
- Implement `From<std::io::Error>` for project error types when I/O is used.
- Use stable, short parser error messages that identify the failed condition.
- Do not use `unwrap`, `expect`, or `panic!` in library code for malformed input
  or recoverable runtime errors.
- Tests may use `unwrap` and `expect` for setup and assertions.

## Binary And Text Parsing

- Keep parser cursor state explicit.
- Check bounds before slicing or indexing.
- Use `checked_add`, `checked_mul`, or equivalent guards for offset arithmetic.
- Validate trailing bytes when decoding fixed-format records.
- Validate magic values, version fields, lengths, checksums, and status values
  when the profile requires them.
- Reject malformed input with errors instead of silently truncating or
  defaulting data.

## Persistence

- Keep persistence format choices aligned with the source project and profile.
- Create parent directories when writing persistent files.
- Prefer write-to-temporary-file plus rename for whole-file persistence.
- Flush or sync persistent state when the public API promises durability.
- Do not invent a simplified persistence format when the profile requires a
  source-compatible layout.
- Keep read, write, erase, append, and recovery paths testable as separate
  logic where the source project separates them.

## Module Structure

- Preserve module boundaries declared by the profile.
- Put shared types, constants, errors, storage helpers, and domain logic in
  separate modules when the profile declares separate outputs.
- Avoid giant single-file implementations unless the profile explicitly asks for
  one.
- Keep generated code formatted by `rustfmt`.
- Keep comments short and useful; explain non-obvious translations or invariants
  rather than restating code.

## Tests

- Translate every source test entry required by the profile.
- Include regression tests for parser errors, boundary sizes, persistence,
  iteration order, update/delete flows, and recovery paths when those behaviours
  exist in the source project.
- Benchmark-style tests should assert operation semantics, counts, and sane
  measured result fields; they must not depend on fixed wall-clock thresholds.
- Tests should create isolated temporary state and clean it up when practical.

## Repair Rules

When compile or tests fail:

1. Read the compiler or test failure literally.
2. Change the smallest relevant file and function.
3. Preserve public APIs declared by the profile.
4. Preserve validation tokens and one-to-one feature checks declared by the
   profile.
5. Re-run `cargo check`.
6. Re-run `cargo test`.
7. Do not weaken profile checks to make a failing implementation pass.
