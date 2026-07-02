# FlashDB One-To-One Rust Rewrite Contract

This is the strict contract for opencode + GLM generation. The target is not a
behaviour-compatible demo crate. The target is a logic-faithful Rust rewrite of
FlashDB's C source, preserving the storage engine, state machines, file-mode
layout, public API semantics, and test behaviours.

## Source Of Truth

The generator must treat these files as mandatory source context:

- `FlashDB/inc/flashdb.h`
- `FlashDB/inc/fdb_def.h`
- `FlashDB/inc/fdb_low_lvl.h`
- `FlashDB/src/fdb.c`
- `FlashDB/src/fdb_file.c`
- `FlashDB/src/fdb_utils.c`
- `FlashDB/src/fdb_kvdb.c`
- `FlashDB/src/fdb_tsdb.c`
- `FlashDB/tests/fdb_kvdb_tc.c`
- `FlashDB/tests/fdb_tsdb_tc.c`
- `FlashDB/tests/README_test.md`

Before writing Rust code, produce a local migration matrix in
`result/harness/04-function-parity.json` that maps every public `fdb_*` API and
every important internal storage function to a Rust function, method, or module.

## Hard Definition Of "One-To-One"

The Rust project must preserve these FlashDB mechanisms:

- File-mode storage uses sector-addressed files equivalent to
  `get_db_file_path`: `db_name.fdb.<sector_index>` with 8-character name
  truncation and address-to-sector offset mapping.
- Flash read/write/erase semantics exist as Rust helpers equivalent to
  `_fdb_flash_read`, `_fdb_flash_write`, `_fdb_flash_erase`, and
  `_fdb_flash_write_align`.
- Erased bytes are `0xFF`; erase fills a sector/file region with `0xFF`.
- Write granularity and alignment helpers preserve `FDB_ALIGN`,
  `FDB_ALIGN_DOWN`, `FDB_WG_ALIGN`, and `FDB_STATUS_TABLE_SIZE`.
- Status tables preserve `_fdb_set_status`, `_fdb_get_status`,
  `_fdb_write_status`, and `_fdb_read_status` semantics for FlashDB statuses.
- CRC32 uses the same algorithm as `fdb_calc_crc32` from `fdb_utils.c`.
- Common DB core preserves init validation, `sec_size`, `max_size`,
  `oldest_addr`, `file_mode`, `not_formatable`, lock/unlock callbacks,
  user data, and control commands.

## KVDB Required Semantics

KVDB must preserve the storage engine from `fdb_kvdb.c`, including:

- `SECTOR_MAGIC_WORD` for KV sectors and `KV_MAGIC_WORD` for KV nodes.
- Rust equivalents of `sector_hdr_data`, `kv_hdr_data`, `kvdb_sec_info`,
  `fdb_kv`, `fdb_kv_iterator`, and `kv_cache_node`.
- KV statuses: `UNUSED`, `PRE_WRITE`, `WRITE`, `PRE_DELETE`, `DELETED`,
  `ERR_HDR`.
- Sector store statuses and dirty statuses, including dirty `GC`.
- KV node layout with status table, CRC32, magic, total length, value length,
  name length, name bytes, aligned value bytes, start address, and value
  address.
- Sector read/format/update functions equivalent to `read_sector_info`,
  `format_sector`, `update_sec_status`, sector iteration, and sector cache
  updates.
- KV allocation equivalent to `alloc_kv`, `new_kv`, and `new_kv_ex`.
- KV creation/update/delete sequence equivalent to `create_kv_blob`,
  `set_kv`, `del_kv`, `fdb_kv_set_blob`, `fdb_kv_set`, and `fdb_kv_del`.
- Update must perform the two-phase old-node deletion flow:
  `PRE_DELETE` followed by `DELETED` after the new node is written.
- GC and recovery paths equivalent to `gc_collect`, `gc_collect_by_free_size`,
  `check_and_recovery_gc_cb`, `fdb_kvdb_check`, and oldest-sector discovery.
- Default KV handling equivalent to `fdb_kv_set_default`; do not replace it
  with a generic `clear`.
- Iterator semantics equivalent to `fdb_kv_iterator_init` and
  `fdb_kv_iterate`, including current KV metadata, sector address, traversed
  length, object bytes, and value bytes.
- Blob/object helpers equivalent to `fdb_kv_get_blob`, `fdb_kv_get_obj`, and
  `fdb_kv_to_blob`.

## TSDB Required Semantics

TSDB must preserve the storage engine from `fdb_tsdb.c`, including:

- `SECTOR_MAGIC_WORD` for TSDB sectors and log index layout equivalent to
  `log_idx_data`.
- Rust equivalents of `sector_hdr_data`, `log_idx_data`, `tsdb_sec_info`,
  `fdb_tsdb`, and `fdb_tsl`.
- TSL statuses: `UNUSED`, `PRE_WRITE`, `WRITE`, `USER_STATUS1`, `DELETED`,
  `USER_STATUS2`.
- `get_time` callback semantics for `fdb_tsl_append` and explicit timestamp
  semantics for `fdb_tsl_append_with_ts`.
- Append must reject blobs larger than `max_len`.
- Append must reject timestamps less than or equal to `last_time`.
- Sector status update and rollover behaviour equivalent to `update_sec_status`.
- TSL writes equivalent to `write_tsl`, including index and log data addresses.
- Iteration functions equivalent to `fdb_tsl_iter`,
  `fdb_tsl_iter_reverse`, and `fdb_tsl_iter_by_time`; callback return value
  must stop iteration.
- Count and status update functions equivalent to `fdb_tsl_query_count`,
  `fdb_tsl_max_blob_count`, and `fdb_tsl_set_status`.
- Cleaning and blob conversion equivalent to `fdb_tsl_clean` and
  `fdb_tsl_to_blob`.

## Public API Parity

The Rust crate does not need to expose a C ABI, but it must expose safe Rust
functions or methods with C API parity names for every symbol in `flashdb.h`:

- `fdb_kvdb_init`, `fdb_kvdb_control`, `fdb_kvdb_check`, `fdb_kvdb_deinit`
- `fdb_tsdb_init`, `fdb_tsdb_control`, `fdb_tsdb_deinit`
- `fdb_blob_make`, `fdb_blob_read`
- `fdb_kv_set`, `fdb_kv_get`, `fdb_kv_set_blob`, `fdb_kv_get_blob`
- `fdb_kv_del`, `fdb_kv_get_obj`, `fdb_kv_to_blob`, `fdb_kv_set_default`
- `fdb_kv_print`, `fdb_kv_iterator_init`, `fdb_kv_iterate`
- `fdb_tsl_append`, `fdb_tsl_append_with_ts`, `fdb_tsl_iter`
- `fdb_tsl_iter_reverse`, `fdb_tsl_iter_by_time`, `fdb_tsl_query_count`
- `fdb_tsl_max_blob_count`, `fdb_tsl_set_status`, `fdb_tsl_clean`
- `fdb_tsl_to_blob`, `fdb_calc_crc32`

## Prohibited Shortcuts

The following outputs fail validation:

- KVDB implemented primarily as `BTreeMap<String, Vec<u8>>` without sector
  files, KV headers, status transitions, CRC, GC, and recovery.
- TSDB implemented primarily as `Vec<TimeSeriesRecord>` without sector headers,
  log index/data addresses, rollover, `max_len`, monotonic timestamp checks,
  and callback iteration.
- A single `flashdb.dat` persistence file as the only storage backend.
- A custom binary format that is unrelated to FlashDB sector/node layouts.
- Placeholder fields that exist but are never updated by write/delete/append.
- Tests that only assert high-level set/get/query behaviour.
- Documentation claiming "known limitations" for missing FlashDB core logic.

## Required Validation Evidence

`result/harness/07-validation.json` must include:

- required file checks
- API symbol checks
- C API parity checks
- one-to-one feature matrix checks
- behaviour-model rejection checks
- full `FlashDB/tests` translated coverage
- `cargo test` result

`result/issues/00-summary.md` must list missing one-to-one features by module
when validation fails.
