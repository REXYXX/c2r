# FlashDB Rust API Contract

This file is the fixed target contract for weaker-model C-to-Rust runs. The
model must generate code that satisfies this contract before adding any extra
surface area.

## Crate Layout

- Package name: `flashdb_rust`
- Rust edition: `2021`
- External dependencies: none by default
- Required files:
  - `Cargo.toml`
  - `src/lib.rs`
  - `src/kvdb.rs`
  - `src/tsdb.rs`
  - `tests/kvdb_tests.rs`
  - `tests/tsdb_tests.rs`

## Public Exports

`src/lib.rs` must contain these public modules and re-exports:

```rust
pub mod kvdb;
pub mod tsdb;

pub use kvdb::{KvDb, KvError};
pub use tsdb::{TimeSeriesDb, TimeSeriesRecord, TimeSeriesStatus, TsError};
```

## KVDB Contract

`src/kvdb.rs` must provide:

- `pub enum KvError`
  - `Io(std::io::Error)`
  - `Corrupt(String)`
  - `InvalidKey`
- `impl std::fmt::Display for KvError`
- `impl std::error::Error for KvError`
- `impl From<std::io::Error> for KvError`
- `pub struct KvDb`
  - fields must be private
  - must own all stored values
  - must not return references to temporary data

Required methods:

```rust
impl Default for KvDb;

impl KvDb {
    pub fn new() -> Self;
    pub fn open(path: impl AsRef<std::path::Path>) -> Result<Self, KvError>;
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
    pub fn sync(&self) -> Result<(), KvError>;
}
```

Required KVDB behaviours:

- Empty keys must return `Err(KvError::InvalidKey)`.
- `set` must create or update a key.
- `get` must return bytes exactly as written.
- `set_str` and `get_string` must round-trip valid UTF-8 strings.
- `delete` must remove a key and report whether it existed.
- `keys` must be deterministic; use `BTreeMap` unless there is a strong reason not to.
- `open` must load previously synced data from disk.
- `sync` must persist all current values and create parent directories when needed.
- Corrupt persisted data must return `KvError::Corrupt`.

## TSDB Contract

`src/tsdb.rs` must provide:

- `pub enum TsError`
  - `Io(std::io::Error)`
  - `Corrupt(String)`
- `impl std::fmt::Display for TsError`
- `impl std::error::Error for TsError`
- `impl From<std::io::Error> for TsError`
- `pub enum TimeSeriesStatus`
  - `Write`
  - `UserStatus1`
  - `Deleted`
- `pub struct TimeSeriesRecord`
  - `pub timestamp: i64`
  - `pub payload: Vec<u8>`
  - `pub status: TimeSeriesStatus`
  - derives `Debug`, `Clone`, `PartialEq`, `Eq`
- `pub struct TimeSeriesDb`
  - fields must be private
  - must own all stored records

Required methods:

```rust
impl TimeSeriesDb {
    pub fn new() -> Self;
    pub fn open(path: impl AsRef<std::path::Path>) -> Result<Self, TsError>;
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
    pub fn sync(&self) -> Result<(), TsError>;
}
```

Required TSDB behaviours:

- `append` must copy payload bytes into owned storage.
- Records must be observed in ascending timestamp order.
- `query(from, to)` must be inclusive at both ends and support reverse ranges.
- `query_count` must count all records in the inclusive range.
- `query_count_by_status` must count only records with the requested status.
- `latest` must return the greatest timestamp record.
- `set_status_range` must update matching records and return the number changed.
- `clear` must remove all records.
- `open` must load previously synced records from disk.
- `sync` must persist all current records and create parent directories when needed.
- Corrupt persisted data must return `TsError::Corrupt`.

## Persistence Format Constraints

- Use a small binary format with a fixed magic header per database kind.
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
