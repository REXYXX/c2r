# Rust Design Rules For Weak-Model Runs

These rules constrain model output so the generated crate stays simple,
auditable, and easy to repair.

## Default Design

- Prefer a safe, idiomatic Rust rewrite, but preserve FlashDB's original logic
  structure and ownership boundaries. Do not compress the project into a tiny
  behaviour-only model.
- Use owned Rust data (`String`, `Vec<u8>`) for memory safety, but do not let
  high-level collections replace FlashDB's storage engine. `BTreeMap` or
  `Vec<T>` may only be auxiliary indexes/caches over sector/node records.
- Keep module boundaries fixed and close to the C project:
  - `config.rs`, `types.rs`, `status.rs`: constants, enums, config, control commands
  - `blob.rs`: blob buffer/saved metadata
  - `db.rs`: common database core
  - `file.rs`: file-mode storage abstraction
  - `low_level.rs`: alignment, status table, flash read/write helpers
  - `sector.rs`: KVDB/TSDB sector metadata
  - `cache.rs`: KV and sector cache types
  - `kvdb.rs`: KVDB-specific state machine/API
  - `tsdb.rs`: TSDB-specific state machine/API
  - `lib.rs`: public modules and re-exports
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

## Structure Fidelity

- Keep explicit Rust equivalents for the major C structs:
  - `fdb_db` -> `DbCore`
  - `fdb_kvdb` -> `KvDb`
  - `fdb_tsdb` -> `TimeSeriesDb`
  - `fdb_blob` -> `Blob`/`SavedBlob`
  - `fdb_kv` -> `KvNode`
  - `fdb_kv_iterator` -> `KvIterator`
  - `fdb_tsl` -> `TslNode`
  - `kvdb_sec_info` -> `KvSectorInfo`
  - `tsdb_sec_info` -> `TsSectorInfo`
  - `kv_cache_node` -> `KvCacheNode`
- KVDB/TSDB modules must make sector files, node/log headers, status tables,
  CRC, GC/recovery, rollover, and control state the primary source of truth.
  High-level maps/vectors are allowed only as rebuilt indexes for lookup speed.
- Validation must fail if the output only contains `kvdb.rs` and `tsdb.rs` as
  implementation modules.

## One-To-One Fidelity Rules

- Read `work/specs/flashdb_one_to_one_contract.md` before writing code.
- Preserve FlashDB file-mode storage: `db_name.fdb.<sector_index>` files,
  sector-address offsets, erase-to-`0xFF`, and write alignment.
- Preserve status-table transitions enough for tests and recovery checks to
  observe `PRE_WRITE`, `WRITE`, `PRE_DELETE`, `DELETED`, dirty `GC`, and sector
  `FULL`/`USING`/`EMPTY`.
- Preserve `fdb_calc_crc32`; do not substitute another checksum.
- Preserve KVDB allocation, update/delete, default KV, GC, recovery, iterator,
  cache, and blob/object metadata.
- Preserve TSDB append timestamp rules, `max_len`, rollover, log index/data
  addresses, callback iteration, status update, max blob count, and clean.
- Missing core FlashDB logic is a validation failure, not an acceptable
  limitation to document away.

## Tests

The generated tests must cover every `TEST_RUN(...)` entry in
`FlashDB/tests/fdb_kvdb_tc.c` and `FlashDB/tests/fdb_tsdb_tc.c`. Duplicate source
invocations must be preserved with stable Rust names.

The generated tests must also cover every unit-test and benchmark item listed in
`FlashDB/tests/README_test.md`, including the benchmark suite under
`FlashDB/tests/benchmark/bench_main.c`. Benchmark tests should validate operation
semantics, operation counts, cleanup, and sane timing/result fields; they must
not rely on fixed wall-clock performance thresholds.

The translated tests must include:

- KVDB string set/get/update/delete
- KVDB binary blob round trip
- KVDB persistence across `open` calls
- KVDB GC-like repeated update/delete/persistence scenarios
- KVDB scale-up-like reopen and growth scenario
- KVDB set-default/clear scenario
- TSDB strict append rejects out-of-order/non-increasing timestamps through the
  `fdb_tsl_*` API
- TSDB inclusive range query
- TSDB reverse range query
- TSDB query count
- TSDB status update/count
- Benchmark KVDB set/get string, set/get blob, update string, iterate all, and delete
- Benchmark TSDB append, iterate all, iter by time, and query count
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
