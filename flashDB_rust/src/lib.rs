//! Structure-preserving safe Rust rewrite of FlashDB.
//!
//! The module layout mirrors the original C project instead of flattening
//! it into only KVDB and TSDB behaviour files:
//!
//! - `config`, `types`, `status`: `inc/fdb_def.h` and `inc/fdb_low_lvl.h`
//! - `db`, `low_level`, `file`: `src/fdb.c` and `src/fdb_file.c`
//! - `blob`: `src/fdb_utils.c`
//! - `sector`, `cache`: sector metadata and KV cache structures
//! - `kvdb`: `src/fdb_kvdb.c`
//! - `tsdb`: `src/fdb_tsdb.c`

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
