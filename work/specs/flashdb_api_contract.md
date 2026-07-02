# FlashDB Rust API Contract

This file is the fixed target contract for weaker-model C-to-Rust runs. The
model must generate code that satisfies this contract before adding any extra
surface area. The target is a one-to-one Rust rewrite of FlashDB's storage
engine, not a compact behaviour-only crate.

This API contract is subordinate to
`work/specs/flashdb_one_to_one_contract.md`. If any wording here appears to
permit a shortcut that the one-to-one contract forbids, the one-to-one contract
wins.

## Crate Layout

- Package name: `flashdb_rust`
- Rust edition: `2021`
- External dependencies: none by default
- Required files:
  - `Cargo.toml`
  - `src/lib.rs`
  - `src/config.rs`
  - `src/error.rs`
  - `src/types.rs`
  - `src/status.rs`
  - `src/blob.rs`
  - `src/db.rs`
  - `src/file.rs`
  - `src/low_level.rs`
  - `src/sector.rs`
  - `src/cache.rs`
  - `src/kvdb.rs`
  - `src/tsdb.rs`
  - `tests/kvdb_tests.rs`
  - `tests/tsdb_tests.rs`

## Structure Preservation Contract

The Rust module layout must mirror the original FlashDB structure:

| C source/header | Required Rust module | Required role |
|---|---|---|
| `inc/fdb_def.h` | `config`, `types`, `status`, `sector`, `cache`, `blob` | constants, enums, database config, KV/TSL/sector/cache/blob data types |
| `inc/fdb_low_lvl.h`, `src/fdb.c` | `low_level`, `db` | alignment helpers, status-table helpers, common database core/control state |
| `src/fdb_file.c` | `file` | file-mode storage abstraction |
| `src/fdb_utils.c` | `blob` | blob construction/read semantics |
| `src/fdb_kvdb.c` | `kvdb` | KVDB API, iterator, cache, GC/recovery state, persistence |
| `src/fdb_tsdb.c` | `tsdb` | TSDB API, TSL node/status, rollover/control state, persistence |

The implementation must not collapse all logic into only `src/kvdb.rs` and
`src/tsdb.rs`. `kvdb.rs` and `tsdb.rs` must depend on shared modules for blob,
status, sector, database core, file storage, and low-level helpers.

## Public Exports

`src/lib.rs` must contain these public modules and re-exports:

```rust
pub mod blob;
pub mod cache;
pub mod config;
pub mod db;
pub mod error;
pub mod file;
pub mod kvdb;
pub mod low_level;
pub mod sector;
pub mod status;
pub mod tsdb;
pub mod types;

pub use blob::{Blob, SavedBlob};
pub use db::DbCore;
pub use error::{FlashDbError, FlashDbResult};
pub use kvdb::{KvDb, KvError};
pub use status::{KvStatus, SectorDirtyStatus, SectorStoreStatus, TslStatus};
pub use tsdb::{TimeSeriesDb, TimeSeriesRecord, TimeSeriesStatus, TsError};
pub use types::{DbConfig, DbControl, DbKind};
```

## Common Module Contract

Required common structures:

- `config.rs`: version constants, `FDB_WRITE_GRAN`, `align`, `align_down`, `wg_align`, `wg_align_down`, `status_table_size`.
- `error.rs`: `FlashDbError`, `FlashDbResult`, `Display`, `Error`, `From<std::io::Error>`.
- `types.rs`: `DbKind`, `DbConfig`, `DbControl`, address/config data.
- `status.rs`: `KvStatus`, `TslStatus`, `SectorStoreStatus`, `SectorDirtyStatus`, `StatusTable`.
- `blob.rs`: `Blob`, `SavedBlob`, buffer read semantics.
- `sector.rs`: `KvSectorInfo`, `TsSectorInfo`.
- `cache.rs`: `KvCacheNode`, `SectorCache`.
- `db.rs`: `DbCore` with name, kind, storage, config, init state, and `control`.
- `file.rs`: `FileStorage` with whole-file and offset read/write helpers.
- `low_level.rs`: alignment/status/flash read-write helper functions,
  including Rust equivalents of `fdb_calc_crc32`, `_fdb_set_status`,
  `_fdb_get_status`, `_fdb_write_status`, `_fdb_read_status`,
  `_fdb_continue_ff_addr`, `_fdb_flash_read`, `_fdb_flash_write`,
  `_fdb_flash_erase`, and `_fdb_flash_write_align`.

## KVDB Contract

`src/kvdb.rs` must provide:

- `pub enum KvError`
  - `Io(std::io::Error)`
  - `Corrupt(String)`
  - `InvalidKey`
  - `SavedFull`
  - `InitFailed`
  - `WriteErr`
- `impl std::fmt::Display for KvError`
- `impl std::error::Error for KvError`
- `impl From<std::io::Error> for KvError`
- `pub struct KvDb`
  - fields must be private
  - must own all stored values
  - must not return references to temporary data
  - must include common/core fields equivalent to `struct fdb_kvdb`: `DbCore`, current KV/sector, cache tables, GC/recovery flags
- `pub struct KvNode`
- `pub struct KvIterator`

Required methods:

```rust
impl Default for KvDb;

impl KvDb {
    pub fn new() -> Self;
    pub fn open(path: impl AsRef<std::path::Path>) -> Result<Self, KvError>;
    pub fn control(&mut self, command: DbControl) -> Option<u32>;
    pub fn set(&mut self, key: impl Into<String>, value: impl AsRef<[u8]>) -> Result<(), KvError>;
    pub fn set_str(&mut self, key: impl Into<String>, value: impl AsRef<str>) -> Result<(), KvError>;
    pub fn get(&self, key: &str) -> Option<&[u8]>;
    pub fn get_string(&self, key: &str) -> Option<String>;
    pub fn contains_key(&self, key: &str) -> bool;
    pub fn delete(&mut self, key: &str) -> bool;
    pub fn clear(&mut self);
    pub fn len(&self) -> usize;
    pub fn is_empty(&self) -> bool;
    pub fn keys(&self) -> impl Iterator<Item = &str>;
    pub fn iterator(&self) -> KvIterator;
    pub fn iterate(&self, iterator: &mut KvIterator) -> bool;
    pub fn sync(&self) -> Result<(), KvError>;
}
```

Required KVDB behaviours:

- Empty keys and keys longer than `FDB_KV_NAME_MAX` must return `Err(KvError::InvalidKey)`.
- `set`/`set_blob` must create/update KV nodes using FlashDB KV header, aligned
  key/value storage, CRC32, status table, magic word, and sector allocation.
- Updating an existing key must preserve the FlashDB two-phase flow: write the
  new node, mark the old node `PRE_DELETE`, then mark it `DELETED`.
- `get` and `get_blob` must read from node/blob metadata equivalent to
  `fdb_kv_get_blob`.
- `delete` must update FlashDB node status and sector dirty state; it must not
  only remove an entry from a map.
- Deterministic maps may be used only as auxiliary indexes/caches. They must not
  replace sector/node storage, CRC, GC, recovery, and status transitions.
- `open` must scan sector files and recover state from FlashDB-compatible
  metadata, including dirty-GC recovery.
- `sync` must persist sector-addressed FlashDB storage. A single custom
  `flashdb.dat` is not sufficient.
- Corrupt persisted data must return `KvError::Corrupt`.

## TSDB Contract

`src/tsdb.rs` must provide:

- `pub enum TsError`
  - `Io(std::io::Error)`
  - `Corrupt(String)`
  - `WriteErr`
  - `InitFailed`
- `impl std::fmt::Display for TsError`
- `impl std::error::Error for TsError`
- `impl From<std::io::Error> for TsError`
- `pub enum TimeSeriesStatus`
  - `Unused`
  - `PreWrite`
  - `Write`
  - `UserStatus1`
  - `Deleted`
  - `UserStatus2`
- `pub struct TimeSeriesRecord`
  - `pub timestamp: i64`
  - `pub payload: Vec<u8>`
  - `pub status: TimeSeriesStatus`
  - derives `Debug`, `Clone`, `PartialEq`, `Eq`
- `pub struct TimeSeriesDb`
  - fields must be private
  - must own all stored records
  - must include common/core fields equivalent to `struct fdb_tsdb`: `DbCore`, current sector, last time, max log length, rollover flag
- `pub struct TslNode`

Required methods:

```rust
impl TimeSeriesDb {
    pub fn new() -> Self;
    pub fn open(path: impl AsRef<std::path::Path>) -> Result<Self, TsError>;
    pub fn control(&mut self, command: DbControl) -> Option<u32>;
    pub fn append(&mut self, timestamp: i64, payload: impl AsRef<[u8]>);
    pub fn len(&self) -> usize;
    pub fn is_empty(&self) -> bool;
    pub fn iter(&self) -> impl Iterator<Item = &TimeSeriesRecord>;
    pub fn query(&self, from: i64, to: i64) -> Vec<TimeSeriesRecord>;
    pub fn query_count(&self, from: i64, to: i64) -> usize;
    pub fn query_count_by_status(&self, from: i64, to: i64, status: TimeSeriesStatus) -> usize;
    pub fn latest(&self) -> Option<&TimeSeriesRecord>;
    pub fn set_status_range(&mut self, from: i64, to: i64, status: TimeSeriesStatus) -> usize;
    pub fn clear(&mut self);
    pub fn latest_node(&self) -> Option<TslNode>;
    pub fn sync(&self) -> Result<(), TsError>;
}
```

Required TSDB behaviours:

- `append` must copy payload bytes into FlashDB log storage and preserve TSL
  index/log addresses.
- `fdb_tsl_append` must use the configured `get_time` callback.
- `fdb_tsl_append_with_ts` must reject timestamps less than or equal to
  `last_time`.
- Append must reject payloads larger than `max_len`.
- Records must be observed through FlashDB sector/index iteration semantics.
- `query(from, to)` must be inclusive at both ends and support reverse ranges.
- `query_count` must count all records in the inclusive range.
- `query_count_by_status` must count only records with the requested status.
- `latest` must return the greatest timestamp record.
- `set_status_range` may exist as a convenience helper, but the core parity API
  must update status through a `TslNode` equivalent to `fdb_tsl_set_status`.
- `clear` must format/clean FlashDB TSDB sectors.
- `open` must scan sector files and rebuild current sector, last time, oldest
  address, and rollover state.
- `sync` must persist sector-addressed FlashDB storage. A single custom
  `flashdb.dat` is not sufficient.
- Corrupt persisted data must return `TsError::Corrupt`.

## Persistence Format Constraints

- Use FlashDB-equivalent sector files and sector/node/log index layouts.
- Use little-endian integer encoding.
- Check all offsets with bounds checks before slicing.
- Reject trailing bytes.
- Never panic on malformed input; return `Corrupt`.

## Prohibited Output

- No `unsafe`.
- No C FFI bindings.
- No generated C code.
- No global mutable state.
- No direct mutation of the input FlashDB tree.
- No hidden network or package download step.
- No panics for normal error cases.
- No two-file flattened implementation that hides FlashDB's original module boundaries.
- No custom map/vector-only persistence model.
- No single-file `flashdb.dat` as the only backend.
