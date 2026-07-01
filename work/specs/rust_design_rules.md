# Rust Design Rules For Weak-Model Runs

These rules constrain model output so the generated crate stays simple,
auditable, and easy to repair.

## Default Design

- Prefer a safe, idiomatic Rust rewrite over line-by-line C emulation.
- Use owned data structures first: `String`, `Vec<u8>`, `BTreeMap`, `Vec<T>`.
- Keep module boundaries fixed:
  - KVDB logic only in `src/kvdb.rs`
  - TSDB logic only in `src/tsdb.rs`
  - public exports only in `src/lib.rs`
- Keep all struct fields private except `TimeSeriesRecord`.
- Keep helper functions private.
- Return `Result` for I/O and corruption errors.
- Return `Option` when a value may not exist.

## Error Handling

- Convert `std::io::Error` through `From<std::io::Error>`.
- Use domain errors for invalid data.
- For parser errors, include a short stable message such as `bad magic`,
  `unexpected end of file`, `trailing bytes`, or `offset overflow`.
- Do not use `unwrap`, `expect`, or `panic!` in library code.
- Tests may use `unwrap` for setup and assertions.

## Binary Parsing

- Keep one mutable `pos: usize` cursor.
- All reads must go through helpers equivalent to:
  - `read_bytes(bytes, &mut pos, len)`
  - `read_u32(bytes, &mut pos)`
  - `read_i64(bytes, &mut pos)`
- Use `checked_add` before slicing.
- Verify `pos == bytes.len()` after decoding.
- Sort decoded TSDB records by timestamp.

## Persistence

- `open(path)`:
  - if the path does not exist, return an empty database with `path` stored
  - if the path exists, read all bytes and decode them
- `sync()`:
  - if no path is attached, return `Ok(())`
  - create parent directory when one exists
  - write to a temporary file first
  - call `sync_all`
  - rename temporary file to the final path

## Tests

The generated tests must cover every `TEST_RUN(...)` entry in
`FlashDB/tests/fdb_kvdb_tc.c` and `FlashDB/tests/fdb_tsdb_tc.c`. Duplicate source
invocations must be preserved with stable Rust names.

The translated tests must include:

- KVDB string set/get/update/delete
- KVDB binary blob round trip
- KVDB persistence across `open` calls
- KVDB GC-like repeated update/delete/persistence scenarios
- KVDB scale-up-like reopen and growth scenario
- KVDB set-default/clear scenario
- TSDB append with out-of-order timestamps
- TSDB inclusive range query
- TSDB reverse range query
- TSDB query count
- TSDB status update/count
- TSDB clean
- TSDB latest record
- TSDB persistence across `open` calls
- TSDB large-payload regression scenario equivalent to GitHub issue 249

## Repair Rules

When compile or tests fail:

1. Read the compiler/test error literally.
2. Change only the smallest file and function needed.
3. Re-run `cargo check`.
4. Re-run `cargo test`.
5. Do not change the public API contract to silence a test.
6. Do not add dependencies unless the contract explicitly allows them.
