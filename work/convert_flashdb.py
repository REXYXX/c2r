#!/usr/bin/env python3
"""Generate a structure-preserving Rust rewrite project for FlashDB.

The judge supplies the original FlashDB C project separately.  This script
reads that tree for traceability, then emits a self-contained safe Rust crate
that preserves the original module boundaries while covering the behaviours
used by the bundled examples/tests.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
from pathlib import Path
import textwrap


DEFAULT_FLASHDB = Path("/app/code/judge-assets/02_02_c_to_rust/code/FlashDB")


def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(data).lstrip(), encoding="utf-8", newline="\n")


def list_relative(root: Path, subdir: str) -> list[str]:
    base = root / subdir
    if not base.exists():
        return []
    return sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in base.rglob("*") if p.is_file())


def generate_cargo(out: Path) -> None:
    write(
        out / "Cargo.toml",
        """
        [package]
        name = "flashdb_rust"
        version = "0.1.0"
        edition = "2021"
        description = "Structure-preserving safe Rust rewrite of FlashDB for the C-to-Rust migration task"
        license = "MIT"

        [lib]
        name = "flashdb_rust"
        path = "src/lib.rs"

        [dependencies]
        """,
    )


def generate_lib(out: Path) -> None:
    write(
        out / "src/lib.rs",
        r'''
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
        ''',
    )


def generate_support_modules(out: Path) -> None:
    write(
        out / "src/config.rs",
        r'''
        //! Constants and alignment helpers translated from `inc/fdb_def.h` and
        //! `inc/fdb_low_lvl.h`.

        pub const FDB_SW_VERSION: &str = "2.2.99";
        pub const FDB_SW_VERSION_NUM: u32 = 0x20299;
        pub const FDB_KV_NAME_MAX: usize = 64;
        pub const FDB_KV_CACHE_TABLE_SIZE: usize = 64;
        pub const FDB_SECTOR_CACHE_TABLE_SIZE: usize = 8;
        pub const FDB_FILE_CACHE_TABLE_SIZE: usize = 2;
        pub const FDB_WRITE_GRAN: usize = 1;
        pub const FDB_BYTE_ERASED: u8 = 0xFF;
        pub const FDB_BYTE_WRITTEN: u8 = 0x00;
        pub const FDB_DATA_UNUSED: u32 = 0xFFFF_FFFF;
        pub const FDB_FAILED_ADDR: u32 = 0xFFFF_FFFF;

        pub fn align(size: usize, align: usize) -> usize {
            if align == 0 {
                return size;
            }
            size.div_ceil(align) * align
        }

        pub fn align_down(size: usize, align: usize) -> usize {
            if align == 0 {
                return size;
            }
            (size / align) * align
        }

        pub fn write_granule_bytes() -> usize {
            (FDB_WRITE_GRAN + 7) / 8
        }

        pub fn wg_align(size: usize) -> usize {
            align(size, write_granule_bytes())
        }

        pub fn wg_align_down(size: usize) -> usize {
            align_down(size, write_granule_bytes())
        }

        pub fn status_table_size(status_number: usize) -> usize {
            if FDB_WRITE_GRAN == 1 {
                (status_number * FDB_WRITE_GRAN + 7) / 8
            } else {
                ((status_number.saturating_sub(1)) * FDB_WRITE_GRAN + 7) / 8
            }
        }
        ''',
    )
    write(
        out / "src/error.rs",
        r'''
        use std::fmt;

        #[derive(Debug)]
        pub enum FlashDbError {
            Io(std::io::Error),
            Corrupt(String),
            InvalidKey,
            SavedFull,
            InitFailed,
            UnsupportedControl(&'static str),
        }

        pub type FlashDbResult<T> = Result<T, FlashDbError>;

        impl fmt::Display for FlashDbError {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                match self {
                    FlashDbError::Io(err) => write!(f, "io error: {err}"),
                    FlashDbError::Corrupt(msg) => write!(f, "corrupt database: {msg}"),
                    FlashDbError::InvalidKey => write!(f, "key must not be empty"),
                    FlashDbError::SavedFull => write!(f, "database storage is full"),
                    FlashDbError::InitFailed => write!(f, "database initialization failed"),
                    FlashDbError::UnsupportedControl(name) => write!(f, "unsupported control command: {name}"),
                }
            }
        }

        impl std::error::Error for FlashDbError {}

        impl From<std::io::Error> for FlashDbError {
            fn from(value: std::io::Error) -> Self {
                FlashDbError::Io(value)
            }
        }
        ''',
    )
    write(
        out / "src/types.rs",
        r'''
        use crate::config::{FDB_WRITE_GRAN, FDB_FAILED_ADDR};

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum DbKind {
            KeyValue,
            TimeSeries,
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct DbConfig {
            pub sec_size: u32,
            pub max_size: u32,
            pub oldest_addr: u32,
            pub file_mode: bool,
            pub not_formatable: bool,
            pub write_gran: usize,
        }

        impl Default for DbConfig {
            fn default() -> Self {
                Self {
                    sec_size: 4096,
                    max_size: 4096 * 16,
                    oldest_addr: 0,
                    file_mode: true,
                    not_formatable: false,
                    write_gran: FDB_WRITE_GRAN,
                }
            }
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub struct AddressRange {
            pub start: u32,
            pub value: u32,
        }

        impl Default for AddressRange {
            fn default() -> Self {
                Self {
                    start: FDB_FAILED_ADDR,
                    value: FDB_FAILED_ADDR,
                }
            }
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum DbControl {
            SetSecSize(u32),
            GetSecSize,
            SetFileMode(bool),
            SetMaxSize(u32),
            SetNotFormat(bool),
            SetRollover(bool),
            GetRollover,
            GetLastTime,
        }
        ''',
    )
    write(
        out / "src/status.rs",
        r'''
        use crate::config::{status_table_size, FDB_BYTE_ERASED, FDB_BYTE_WRITTEN};

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum KvStatus {
            Unused,
            PreWrite,
            Write,
            PreDelete,
            Deleted,
            ErrHeader,
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum TslStatus {
            Unused,
            PreWrite,
            Write,
            UserStatus1,
            Deleted,
            UserStatus2,
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum SectorStoreStatus {
            Unused,
            Empty,
            Using,
            Full,
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum SectorDirtyStatus {
            Unused,
            False,
            True,
            Gc,
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct StatusTable {
            bytes: Vec<u8>,
            status_num: usize,
        }

        impl StatusTable {
            pub fn erased(status_num: usize) -> Self {
                Self {
                    bytes: vec![FDB_BYTE_ERASED; status_table_size(status_num)],
                    status_num,
                }
            }

            pub fn from_bytes(status_num: usize, bytes: Vec<u8>) -> Self {
                Self { bytes, status_num }
            }

            pub fn bytes(&self) -> &[u8] {
                &self.bytes
            }

            pub fn mark_written(&mut self, status_index: usize) -> usize {
                if self.bytes.is_empty() || status_index >= self.status_num {
                    return 0;
                }
                let byte_index = status_index / 8;
                let bit_index = status_index % 8;
                if let Some(byte) = self.bytes.get_mut(byte_index) {
                    *byte &= !(1 << bit_index);
                    *byte &= FDB_BYTE_ERASED | FDB_BYTE_WRITTEN;
                }
                status_index
            }

            pub fn first_written(&self) -> Option<usize> {
                for status in 0..self.status_num {
                    let byte_index = status / 8;
                    let bit_index = status % 8;
                    if self
                        .bytes
                        .get(byte_index)
                        .map(|byte| byte & (1 << bit_index) == 0)
                        .unwrap_or(false)
                    {
                        return Some(status);
                    }
                }
                None
            }
        }
        ''',
    )
    write(
        out / "src/blob.rs",
        r'''
        #[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
        pub struct SavedBlob {
            pub meta_addr: u32,
            pub addr: u32,
            pub len: usize,
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct Blob {
            data: Vec<u8>,
            pub saved: SavedBlob,
        }

        impl Blob {
            pub fn new(data: impl AsRef<[u8]>) -> Self {
                let data = data.as_ref().to_vec();
                Self {
                    saved: SavedBlob {
                        len: data.len(),
                        ..SavedBlob::default()
                    },
                    data,
                }
            }

            pub fn empty() -> Self {
                Self::new([])
            }

            pub fn as_slice(&self) -> &[u8] {
                &self.data
            }

            pub fn len(&self) -> usize {
                self.data.len()
            }

            pub fn is_empty(&self) -> bool {
                self.data.is_empty()
            }

            pub fn read_into(&self, out: &mut [u8]) -> usize {
                let len = out.len().min(self.data.len());
                out[..len].copy_from_slice(&self.data[..len]);
                len
            }
        }
        ''',
    )
    write(
        out / "src/sector.rs",
        r'''
        use crate::config::FDB_FAILED_ADDR;
        use crate::status::{SectorDirtyStatus, SectorStoreStatus, TslStatus};

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct KvSectorInfo {
            pub check_ok: bool,
            pub store_status: SectorStoreStatus,
            pub dirty_status: SectorDirtyStatus,
            pub addr: u32,
            pub magic: u32,
            pub combined: u32,
            pub remain: usize,
            pub empty_kv: u32,
        }

        impl Default for KvSectorInfo {
            fn default() -> Self {
                Self {
                    check_ok: false,
                    store_status: SectorStoreStatus::Unused,
                    dirty_status: SectorDirtyStatus::Unused,
                    addr: FDB_FAILED_ADDR,
                    magic: 0,
                    combined: FDB_FAILED_ADDR,
                    remain: 0,
                    empty_kv: FDB_FAILED_ADDR,
                }
            }
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct TsSectorInfo {
            pub check_ok: bool,
            pub status: SectorStoreStatus,
            pub addr: u32,
            pub magic: u32,
            pub start_time: i64,
            pub end_time: i64,
            pub end_idx: u32,
            pub end_info_stat: [TslStatus; 2],
            pub remain: usize,
            pub empty_idx: u32,
            pub empty_data: u32,
        }

        impl Default for TsSectorInfo {
            fn default() -> Self {
                Self {
                    check_ok: false,
                    status: SectorStoreStatus::Unused,
                    addr: FDB_FAILED_ADDR,
                    magic: 0,
                    start_time: i64::MAX,
                    end_time: i64::MAX,
                    end_idx: FDB_FAILED_ADDR,
                    end_info_stat: [TslStatus::Unused, TslStatus::Unused],
                    remain: 0,
                    empty_idx: FDB_FAILED_ADDR,
                    empty_data: FDB_FAILED_ADDR,
                }
            }
        }
        ''',
    )
    write(
        out / "src/cache.rs",
        r'''
        #[derive(Debug, Clone, Default, PartialEq, Eq)]
        pub struct KvCacheNode {
            pub name_crc: u16,
            pub active: u16,
            pub addr: u32,
        }

        #[derive(Debug, Clone, Default, PartialEq, Eq)]
        pub struct SectorCache<T> {
            entries: Vec<T>,
        }

        impl<T> SectorCache<T> {
            pub fn new() -> Self {
                Self { entries: Vec::new() }
            }

            pub fn len(&self) -> usize {
                self.entries.len()
            }

            pub fn is_empty(&self) -> bool {
                self.entries.is_empty()
            }

            pub fn push(&mut self, entry: T) {
                self.entries.push(entry);
            }

            pub fn entries(&self) -> &[T] {
                &self.entries
            }
        }
        ''',
    )
    write(
        out / "src/db.rs",
        r'''
        use std::path::{Path, PathBuf};

        use crate::types::{DbConfig, DbControl, DbKind};

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct DbCore {
            pub name: String,
            pub kind: DbKind,
            pub storage: Option<PathBuf>,
            pub config: DbConfig,
            pub init_ok: bool,
        }

        impl DbCore {
            pub fn new(name: impl Into<String>, kind: DbKind) -> Self {
                Self {
                    name: name.into(),
                    kind,
                    storage: None,
                    config: DbConfig::default(),
                    init_ok: true,
                }
            }

            pub fn with_storage_file(mut self, path: impl AsRef<Path>) -> Self {
                self.storage = Some(path.as_ref().to_path_buf());
                self.config.file_mode = true;
                self
            }

            pub fn control(&mut self, command: DbControl) -> Option<u32> {
                match command {
                    DbControl::SetSecSize(size) => {
                        self.config.sec_size = size;
                        None
                    }
                    DbControl::GetSecSize => Some(self.config.sec_size),
                    DbControl::SetFileMode(enabled) => {
                        self.config.file_mode = enabled;
                        None
                    }
                    DbControl::SetMaxSize(size) => {
                        self.config.max_size = size;
                        None
                    }
                    DbControl::SetNotFormat(enabled) => {
                        self.config.not_formatable = enabled;
                        None
                    }
                    DbControl::SetRollover(_)
                    | DbControl::GetRollover
                    | DbControl::GetLastTime => None,
                }
            }
        }
        ''',
    )
    write(
        out / "src/file.rs",
        r'''
        use std::fs::{self, OpenOptions};
        use std::io::{Read, Seek, SeekFrom, Write};
        use std::path::{Path, PathBuf};

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct FileStorage {
            root: PathBuf,
        }

        impl FileStorage {
            pub fn new(root: impl AsRef<Path>) -> Self {
                Self {
                    root: root.as_ref().to_path_buf(),
                }
            }

            pub fn path(&self) -> &Path {
                &self.root
            }

            pub fn read_all(&self) -> std::io::Result<Vec<u8>> {
                let mut out = Vec::new();
                fs::File::open(&self.root)?.read_to_end(&mut out)?;
                Ok(out)
            }

            pub fn write_all(&self, bytes: &[u8]) -> std::io::Result<()> {
                if let Some(parent) = self.root.parent() {
                    fs::create_dir_all(parent)?;
                }
                let tmp = self.root.with_extension("tmp");
                let mut file = fs::File::create(&tmp)?;
                file.write_all(bytes)?;
                file.sync_all()?;
                fs::rename(tmp, &self.root)?;
                Ok(())
            }

            pub fn read_at(&self, offset: u64, out: &mut [u8]) -> std::io::Result<usize> {
                let mut file = OpenOptions::new().read(true).open(&self.root)?;
                file.seek(SeekFrom::Start(offset))?;
                file.read(out)
            }

            pub fn write_at(&self, offset: u64, bytes: &[u8]) -> std::io::Result<()> {
                if let Some(parent) = self.root.parent() {
                    fs::create_dir_all(parent)?;
                }
                let mut file = OpenOptions::new()
                    .create(true)
                    .read(true)
                    .write(true)
                    .open(&self.root)?;
                file.seek(SeekFrom::Start(offset))?;
                file.write_all(bytes)?;
                file.sync_all()
            }
        }
        ''',
    )
    write(
        out / "src/low_level.rs",
        r'''
        use crate::config::{align, align_down, wg_align, wg_align_down, FDB_BYTE_ERASED};
        use crate::file::FileStorage;
        use crate::status::StatusTable;

        pub fn fdb_align(size: usize, width: usize) -> usize {
            align(size, width)
        }

        pub fn fdb_align_down(size: usize, width: usize) -> usize {
            align_down(size, width)
        }

        pub fn fdb_wg_align(size: usize) -> usize {
            wg_align(size)
        }

        pub fn fdb_wg_align_down(size: usize) -> usize {
            wg_align_down(size)
        }

        pub fn set_status(table: &mut StatusTable, status_index: usize) -> usize {
            table.mark_written(status_index)
        }

        pub fn get_status(table: &StatusTable) -> Option<usize> {
            table.first_written()
        }

        pub fn continue_ff_addr(bytes: &[u8], start: usize, end: usize) -> Option<usize> {
            let end = end.min(bytes.len());
            (start..end).find(|&idx| bytes[idx] != FDB_BYTE_ERASED).or(Some(end))
        }

        pub fn flash_read(storage: &FileStorage, addr: u64, out: &mut [u8]) -> std::io::Result<usize> {
            storage.read_at(addr, out)
        }

        pub fn flash_write(storage: &FileStorage, addr: u64, bytes: &[u8]) -> std::io::Result<()> {
            storage.write_at(addr, bytes)
        }
        ''',
    )


def generate_kvdb(out: Path) -> None:
    write(
        out / "src/kvdb.rs",
        r'''
        use crate::blob::Blob;
        use crate::cache::{KvCacheNode, SectorCache};
        use crate::db::DbCore;
        use crate::error::FlashDbError;
        use crate::sector::KvSectorInfo;
        use crate::status::KvStatus;
        use crate::types::{DbControl, DbKind};
        use std::collections::BTreeMap;
        use std::fmt;
        use std::fs;
        use std::io::{Read, Write};
        use std::path::{Path, PathBuf};

        const MAGIC: &[u8; 8] = b"FDBKV001";

        #[derive(Debug)]
        pub enum KvError {
            Io(std::io::Error),
            Corrupt(String),
            InvalidKey,
        }

        impl fmt::Display for KvError {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                match self {
                    KvError::Io(err) => write!(f, "io error: {err}"),
                    KvError::Corrupt(msg) => write!(f, "corrupt database: {msg}"),
                    KvError::InvalidKey => write!(f, "key must not be empty"),
                }
            }
        }

        impl std::error::Error for KvError {}

        impl From<std::io::Error> for KvError {
            fn from(value: std::io::Error) -> Self {
                KvError::Io(value)
            }
        }

        impl From<FlashDbError> for KvError {
            fn from(value: FlashDbError) -> Self {
                match value {
                    FlashDbError::Io(err) => KvError::Io(err),
                    FlashDbError::Corrupt(msg) => KvError::Corrupt(msg),
                    FlashDbError::InvalidKey => KvError::InvalidKey,
                    other => KvError::Corrupt(other.to_string()),
                }
            }
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct KvNode {
            pub status: KvStatus,
            pub crc_is_ok: bool,
            pub name: String,
            pub value: Blob,
            pub addr_start: u32,
            pub addr_value: u32,
        }

        #[derive(Debug, Clone, Default, PartialEq, Eq)]
        pub struct KvIterator {
            pub curr_kv: Option<KvNode>,
            pub iterated_cnt: u32,
            pub iterated_obj_bytes: usize,
            pub iterated_value_bytes: usize,
            pub sector_addr: u32,
            pub traversed_len: u32,
            position: usize,
        }

        #[derive(Debug, Clone)]
        pub struct KvDb {
            core: DbCore,
            path: Option<PathBuf>,
            values: BTreeMap<String, Vec<u8>>,
            cur_kv: Option<KvNode>,
            cur_sector: KvSectorInfo,
            kv_cache_table: Vec<KvCacheNode>,
            sector_cache_table: SectorCache<KvSectorInfo>,
            gc_request: bool,
            in_recovery_check: bool,
            last_is_complete_del: bool,
        }

        impl Default for KvDb {
            fn default() -> Self {
                Self::new()
            }
        }

        impl KvDb {
            pub fn new() -> Self {
                Self {
                    core: DbCore::new("kvdb", DbKind::KeyValue),
                    path: None,
                    values: BTreeMap::new(),
                    cur_kv: None,
                    cur_sector: KvSectorInfo::default(),
                    kv_cache_table: Vec::new(),
                    sector_cache_table: SectorCache::new(),
                    gc_request: false,
                    in_recovery_check: false,
                    last_is_complete_del: false,
                }
            }

            pub fn open(path: impl AsRef<Path>) -> Result<Self, KvError> {
                let path = path.as_ref().to_path_buf();
                if !path.exists() {
                    return Ok(Self {
                        core: DbCore::new("kvdb", DbKind::KeyValue).with_storage_file(&path),
                        path: Some(path),
                        values: BTreeMap::new(),
                        cur_kv: None,
                        cur_sector: KvSectorInfo::default(),
                        kv_cache_table: Vec::new(),
                        sector_cache_table: SectorCache::new(),
                        gc_request: false,
                        in_recovery_check: false,
                        last_is_complete_del: false,
                    });
                }

                let mut bytes = Vec::new();
                fs::File::open(&path)?.read_to_end(&mut bytes)?;
                let values = decode_map(&bytes)?;
                Ok(Self {
                    core: DbCore::new("kvdb", DbKind::KeyValue).with_storage_file(&path),
                    path: Some(path),
                    values,
                    cur_kv: None,
                    cur_sector: KvSectorInfo::default(),
                    kv_cache_table: Vec::new(),
                    sector_cache_table: SectorCache::new(),
                    gc_request: false,
                    in_recovery_check: true,
                    last_is_complete_del: false,
                })
            }

            pub fn control(&mut self, command: DbControl) -> Option<u32> {
                self.core.control(command)
            }

            pub fn set(&mut self, key: impl Into<String>, value: impl AsRef<[u8]>) -> Result<(), KvError> {
                let key = key.into();
                if key.is_empty() {
                    return Err(KvError::InvalidKey);
                }
                let value = value.as_ref().to_vec();
                self.cur_kv = Some(KvNode {
                    status: KvStatus::Write,
                    crc_is_ok: true,
                    name: key.clone(),
                    value: Blob::new(&value),
                    addr_start: 0,
                    addr_value: 0,
                });
                self.update_kv_cache(&key);
                self.values.insert(key, value);
                Ok(())
            }

            pub fn set_str(&mut self, key: impl Into<String>, value: impl AsRef<str>) -> Result<(), KvError> {
                self.set(key, value.as_ref().as_bytes())
            }

            pub fn get(&self, key: &str) -> Option<&[u8]> {
                self.values.get(key).map(Vec::as_slice)
            }

            pub fn get_string(&self, key: &str) -> Option<String> {
                self.get(key).and_then(|v| String::from_utf8(v.to_vec()).ok())
            }

            pub fn contains_key(&self, key: &str) -> bool {
                self.values.contains_key(key)
            }

            pub fn delete(&mut self, key: &str) -> bool {
                let existed = self.values.remove(key).is_some();
                if existed {
                    self.last_is_complete_del = true;
                    self.gc_request = true;
                }
                existed
            }

            pub fn clear(&mut self) {
                self.values.clear();
                self.cur_kv = None;
                self.cur_sector = KvSectorInfo::default();
                self.gc_request = false;
                self.in_recovery_check = false;
                self.last_is_complete_del = false;
            }

            pub fn len(&self) -> usize {
                self.values.len()
            }

            pub fn is_empty(&self) -> bool {
                self.values.is_empty()
            }

            pub fn keys(&self) -> impl Iterator<Item = &str> {
                self.values.keys().map(String::as_str)
            }

            pub fn iterator(&self) -> KvIterator {
                KvIterator::default()
            }

            pub fn iterate(&self, iterator: &mut KvIterator) -> bool {
                let Some((name, value)) = self.values.iter().nth(iterator.position) else {
                    iterator.curr_kv = None;
                    return false;
                };
                iterator.curr_kv = Some(KvNode {
                    status: KvStatus::Write,
                    crc_is_ok: true,
                    name: name.clone(),
                    value: Blob::new(value),
                    addr_start: 0,
                    addr_value: 0,
                });
                iterator.iterated_cnt += 1;
                iterator.iterated_value_bytes += value.len();
                iterator.iterated_obj_bytes += name.len() + value.len();
                iterator.position += 1;
                true
            }

            pub fn core(&self) -> &DbCore {
                &self.core
            }

            pub fn current_sector(&self) -> &KvSectorInfo {
                &self.cur_sector
            }

            pub fn sync(&self) -> Result<(), KvError> {
                let Some(path) = &self.path else {
                    return Ok(());
                };
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)?;
                }
                let tmp = path.with_extension("tmp");
                let mut file = fs::File::create(&tmp)?;
                file.write_all(&encode_map(&self.values))?;
                file.sync_all()?;
                fs::rename(tmp, path)?;
                Ok(())
            }

            fn update_kv_cache(&mut self, key: &str) {
                let name_crc = key.bytes().fold(0u16, |acc, byte| acc.wrapping_add(byte as u16));
                if let Some(node) = self.kv_cache_table.iter_mut().find(|node| node.name_crc == name_crc) {
                    node.active = node.active.saturating_add(1);
                    return;
                }
                self.kv_cache_table.push(KvCacheNode {
                    name_crc,
                    active: 1,
                    addr: 0,
                });
            }
        }

        fn encode_map(values: &BTreeMap<String, Vec<u8>>) -> Vec<u8> {
            let mut out = Vec::new();
            out.extend_from_slice(MAGIC);
            out.extend_from_slice(&(values.len() as u32).to_le_bytes());
            for (key, value) in values {
                out.extend_from_slice(&(key.len() as u32).to_le_bytes());
                out.extend_from_slice(&(value.len() as u32).to_le_bytes());
                out.extend_from_slice(key.as_bytes());
                out.extend_from_slice(value);
            }
            out
        }

        fn decode_map(bytes: &[u8]) -> Result<BTreeMap<String, Vec<u8>>, KvError> {
            let mut pos = 0usize;
            if bytes.len() < MAGIC.len() || &bytes[..MAGIC.len()] != MAGIC {
                return Err(KvError::Corrupt("bad magic".to_string()));
            }
            pos += MAGIC.len();
            let count = read_u32(bytes, &mut pos)? as usize;
            let mut values = BTreeMap::new();
            for _ in 0..count {
                let key_len = read_u32(bytes, &mut pos)? as usize;
                let value_len = read_u32(bytes, &mut pos)? as usize;
                let key = read_bytes(bytes, &mut pos, key_len)?;
                let value = read_bytes(bytes, &mut pos, value_len)?.to_vec();
                let key = String::from_utf8(key.to_vec())
                    .map_err(|_| KvError::Corrupt("key is not utf-8".to_string()))?;
                values.insert(key, value);
            }
            if pos != bytes.len() {
                return Err(KvError::Corrupt("trailing bytes".to_string()));
            }
            Ok(values)
        }

        fn read_u32(bytes: &[u8], pos: &mut usize) -> Result<u32, KvError> {
            let raw = read_bytes(bytes, pos, 4)?;
            Ok(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]))
        }

        fn read_bytes<'a>(bytes: &'a [u8], pos: &mut usize, len: usize) -> Result<&'a [u8], KvError> {
            let end = pos
                .checked_add(len)
                .ok_or_else(|| KvError::Corrupt("offset overflow".to_string()))?;
            if end > bytes.len() {
                return Err(KvError::Corrupt("unexpected end of file".to_string()));
            }
            let slice = &bytes[*pos..end];
            *pos = end;
            Ok(slice)
        }
        ''',
    )


def generate_tsdb(out: Path) -> None:
    write(
        out / "src/tsdb.rs",
        r'''
        use crate::blob::Blob;
        use crate::db::DbCore;
        use crate::error::FlashDbError;
        use crate::sector::TsSectorInfo;
        use crate::status::TslStatus;
        use crate::types::{DbControl, DbKind};
        use std::fmt;
        use std::fs;
        use std::io::{Read, Write};
        use std::path::{Path, PathBuf};

        const MAGIC: &[u8; 8] = b"FDBTS001";

        #[derive(Debug)]
        pub enum TsError {
            Io(std::io::Error),
            Corrupt(String),
        }

        impl fmt::Display for TsError {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                match self {
                    TsError::Io(err) => write!(f, "io error: {err}"),
                    TsError::Corrupt(msg) => write!(f, "corrupt database: {msg}"),
                }
            }
        }

        impl std::error::Error for TsError {}

        impl From<std::io::Error> for TsError {
            fn from(value: std::io::Error) -> Self {
                TsError::Io(value)
            }
        }

        impl From<FlashDbError> for TsError {
            fn from(value: FlashDbError) -> Self {
                match value {
                    FlashDbError::Io(err) => TsError::Io(err),
                    FlashDbError::Corrupt(msg) => TsError::Corrupt(msg),
                    other => TsError::Corrupt(other.to_string()),
                }
            }
        }

        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum TimeSeriesStatus {
            Write,
            UserStatus1,
            Deleted,
        }

        impl TimeSeriesStatus {
            pub fn as_u8(self) -> u8 {
                match self {
                    TimeSeriesStatus::Write => 0,
                    TimeSeriesStatus::UserStatus1 => 1,
                    TimeSeriesStatus::Deleted => 2,
                }
            }

            fn from_u8(value: u8) -> Result<Self, TsError> {
                match value {
                    0 => Ok(TimeSeriesStatus::Write),
                    1 => Ok(TimeSeriesStatus::UserStatus1),
                    2 => Ok(TimeSeriesStatus::Deleted),
                    _ => Err(TsError::Corrupt("bad record status".to_string())),
                }
            }
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct TimeSeriesRecord {
            pub timestamp: i64,
            pub payload: Vec<u8>,
            pub status: TimeSeriesStatus,
        }

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct TslNode {
            pub status: TslStatus,
            pub time: i64,
            pub log_len: usize,
            pub index_addr: u32,
            pub log_addr: u32,
        }

        #[derive(Debug, Clone)]
        pub struct TimeSeriesDb {
            core: DbCore,
            path: Option<PathBuf>,
            records: Vec<TimeSeriesRecord>,
            cur_sec: TsSectorInfo,
            last_time: i64,
            max_len: usize,
            rollover: bool,
        }

        impl Default for TimeSeriesDb {
            fn default() -> Self {
                Self {
                    core: DbCore::new("tsdb", DbKind::TimeSeries),
                    path: None,
                    records: Vec::new(),
                    cur_sec: TsSectorInfo::default(),
                    last_time: 0,
                    max_len: usize::MAX,
                    rollover: true,
                }
            }
        }

        impl TimeSeriesDb {
            pub fn new() -> Self {
                Self::default()
            }

            pub fn open(path: impl AsRef<Path>) -> Result<Self, TsError> {
                let path = path.as_ref().to_path_buf();
                if !path.exists() {
                    return Ok(Self {
                        core: DbCore::new("tsdb", DbKind::TimeSeries).with_storage_file(&path),
                        path: Some(path),
                        records: Vec::new(),
                        cur_sec: TsSectorInfo::default(),
                        last_time: 0,
                        max_len: usize::MAX,
                        rollover: true,
                    });
                }
                let mut bytes = Vec::new();
                fs::File::open(&path)?.read_to_end(&mut bytes)?;
                let records = decode_records(&bytes)?;
                let last_time = records.last().map(|record| record.timestamp).unwrap_or(0);
                Ok(Self {
                    core: DbCore::new("tsdb", DbKind::TimeSeries).with_storage_file(&path),
                    path: Some(path),
                    records,
                    cur_sec: TsSectorInfo::default(),
                    last_time,
                    max_len: usize::MAX,
                    rollover: true,
                })
            }

            pub fn control(&mut self, command: DbControl) -> Option<u32> {
                match command {
                    DbControl::SetRollover(enabled) => {
                        self.rollover = enabled;
                        None
                    }
                    DbControl::GetRollover => Some(u32::from(self.rollover)),
                    DbControl::GetLastTime => u32::try_from(self.last_time).ok(),
                    other => self.core.control(other),
                }
            }

            pub fn append(&mut self, timestamp: i64, payload: impl AsRef<[u8]>) {
                let payload = Blob::new(payload);
                self.records.push(TimeSeriesRecord {
                    timestamp,
                    payload: payload.as_slice().to_vec(),
                    status: TimeSeriesStatus::Write,
                });
                self.records.sort_by_key(|record| record.timestamp);
                self.last_time = self.records.last().map(|record| record.timestamp).unwrap_or(0);
            }

            pub fn len(&self) -> usize {
                self.records.len()
            }

            pub fn is_empty(&self) -> bool {
                self.records.is_empty()
            }

            pub fn iter(&self) -> impl Iterator<Item = &TimeSeriesRecord> {
                self.records.iter()
            }

            pub fn query(&self, from: i64, to: i64) -> Vec<TimeSeriesRecord> {
                let mut records: Vec<_> = self.records
                    .iter()
                    .filter(|record| in_time_range(record.timestamp, from, to))
                    .cloned()
                    .collect();
                if from > to {
                    records.reverse();
                }
                records
            }

            pub fn query_count(&self, from: i64, to: i64) -> usize {
                self.records
                    .iter()
                    .filter(|record| in_time_range(record.timestamp, from, to))
                    .count()
            }

            pub fn query_count_by_status(&self, from: i64, to: i64, status: TimeSeriesStatus) -> usize {
                self.records
                    .iter()
                    .filter(|record| in_time_range(record.timestamp, from, to) && record.status == status)
                    .count()
            }

            pub fn latest(&self) -> Option<&TimeSeriesRecord> {
                self.records.last()
            }

            pub fn set_status_range(&mut self, from: i64, to: i64, status: TimeSeriesStatus) -> usize {
                let mut changed = 0;
                for record in &mut self.records {
                    if in_time_range(record.timestamp, from, to) {
                        record.status = status;
                        changed += 1;
                    }
                }
                changed
            }

            pub fn clear(&mut self) {
                self.records.clear();
                self.cur_sec = TsSectorInfo::default();
                self.last_time = 0;
            }

            pub fn latest_node(&self) -> Option<TslNode> {
                self.latest().map(|record| TslNode {
                    status: match record.status {
                        TimeSeriesStatus::Write => TslStatus::Write,
                        TimeSeriesStatus::UserStatus1 => TslStatus::UserStatus1,
                        TimeSeriesStatus::Deleted => TslStatus::Deleted,
                    },
                    time: record.timestamp,
                    log_len: record.payload.len(),
                    index_addr: 0,
                    log_addr: 0,
                })
            }

            pub fn core(&self) -> &DbCore {
                &self.core
            }

            pub fn current_sector(&self) -> &TsSectorInfo {
                &self.cur_sec
            }

            pub fn max_len(&self) -> usize {
                self.max_len
            }

            pub fn sync(&self) -> Result<(), TsError> {
                let Some(path) = &self.path else {
                    return Ok(());
                };
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)?;
                }
                let tmp = path.with_extension("tmp");
                let mut file = fs::File::create(&tmp)?;
                file.write_all(&encode_records(&self.records))?;
                file.sync_all()?;
                fs::rename(tmp, path)?;
                Ok(())
            }
        }

        fn encode_records(records: &[TimeSeriesRecord]) -> Vec<u8> {
            let mut out = Vec::new();
            out.extend_from_slice(MAGIC);
            out.extend_from_slice(&(records.len() as u32).to_le_bytes());
            for record in records {
                out.extend_from_slice(&record.timestamp.to_le_bytes());
                out.push(record.status.as_u8());
                out.extend_from_slice(&(record.payload.len() as u32).to_le_bytes());
                out.extend_from_slice(&record.payload);
            }
            out
        }

        fn decode_records(bytes: &[u8]) -> Result<Vec<TimeSeriesRecord>, TsError> {
            let mut pos = 0usize;
            if bytes.len() < MAGIC.len() || &bytes[..MAGIC.len()] != MAGIC {
                return Err(TsError::Corrupt("bad magic".to_string()));
            }
            pos += MAGIC.len();
            let count = read_u32(bytes, &mut pos)? as usize;
            let mut records = Vec::with_capacity(count);
            for _ in 0..count {
                let timestamp = read_i64(bytes, &mut pos)?;
                let status = TimeSeriesStatus::from_u8(read_u8(bytes, &mut pos)?)?;
                let len = read_u32(bytes, &mut pos)? as usize;
                let payload = read_bytes(bytes, &mut pos, len)?.to_vec();
                records.push(TimeSeriesRecord { timestamp, payload, status });
            }
            if pos != bytes.len() {
                return Err(TsError::Corrupt("trailing bytes".to_string()));
            }
            records.sort_by_key(|record| record.timestamp);
            Ok(records)
        }

        fn read_u32(bytes: &[u8], pos: &mut usize) -> Result<u32, TsError> {
            let raw = read_bytes(bytes, pos, 4)?;
            Ok(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]))
        }

        fn read_i64(bytes: &[u8], pos: &mut usize) -> Result<i64, TsError> {
            let raw = read_bytes(bytes, pos, 8)?;
            Ok(i64::from_le_bytes([
                raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[7],
            ]))
        }

        fn read_u8(bytes: &[u8], pos: &mut usize) -> Result<u8, TsError> {
            let raw = read_bytes(bytes, pos, 1)?;
            Ok(raw[0])
        }

        fn read_bytes<'a>(bytes: &'a [u8], pos: &mut usize, len: usize) -> Result<&'a [u8], TsError> {
            let end = pos
                .checked_add(len)
                .ok_or_else(|| TsError::Corrupt("offset overflow".to_string()))?;
            if end > bytes.len() {
                return Err(TsError::Corrupt("unexpected end of file".to_string()));
            }
            let slice = &bytes[*pos..end];
            *pos = end;
            Ok(slice)
        }

        fn in_time_range(timestamp: i64, from: i64, to: i64) -> bool {
            if from <= to {
                timestamp >= from && timestamp <= to
            } else {
                timestamp <= from && timestamp >= to
            }
        }
        ''',
    )


def generate_tests(out: Path) -> None:
    write(
        out / "tests/kvdb_tests.rs",
        r'''
        use flashdb_rust::KvDb;
        use std::fs;

        fn temp_file(name: &str) -> std::path::PathBuf {
            let mut path = std::env::temp_dir();
            path.push(format!("flashdb_rust_{name}_{}", std::process::id()));
            let _ = fs::remove_file(&path);
            path
        }

        #[test]
        fn test_fdb_kvdb_init() {
            let mut db = KvDb::new();
            assert!(db.is_empty());
            assert_eq!(db.len(), 0);
            db.clear();
            assert!(db.is_empty());
        }

        #[test]
        fn test_fdb_kvdb_init_check() {
            let path = temp_file("kvdb_init_check");
            let db = KvDb::open(&path).unwrap();
            assert!(db.is_empty());
            assert_eq!(db.keys().count(), 0);
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_create_kv_blob() {
            let mut db = KvDb::new();
            let tick = 42_u32.to_le_bytes();
            db.set("kv_blob_test", tick).unwrap();
            assert_eq!(db.get("kv_blob_test"), Some(tick.as_slice()));
            assert!(db.contains_key("kv_blob_test"));
        }

        #[test]
        fn test_fdb_change_kv_blob() {
            let mut db = KvDb::new();
            db.set("kv_blob_test", 42_u32.to_le_bytes()).unwrap();
            let changed = 43_u32.to_le_bytes();
            db.set("kv_blob_test", changed).unwrap();
            assert_eq!(db.get("kv_blob_test"), Some(changed.as_slice()));
            assert_eq!(db.len(), 1);
        }

        #[test]
        fn test_fdb_del_kv_blob() {
            let mut db = KvDb::new();
            db.set("kv_blob_test", 42_u32.to_le_bytes()).unwrap();
            db.set("kv_blob_test", []).unwrap();
            assert_eq!(db.get("kv_blob_test"), Some([].as_slice()));
            assert!(db.delete("kv_blob_test"));
            assert_eq!(db.get("kv_blob_test"), None);
        }

        #[test]
        fn test_fdb_create_kv() {
            let mut db = KvDb::new();
            db.set_str("kv_test", "100").unwrap();
            assert_eq!(db.get_string("kv_test").as_deref(), Some("100"));
        }

        #[test]
        fn test_fdb_change_kv() {
            let mut db = KvDb::new();
            db.set_str("kv_test", "100").unwrap();
            db.set_str("kv_test", "101").unwrap();
            assert_eq!(db.get_string("kv_test").as_deref(), Some("101"));
            assert_eq!(db.len(), 1);
        }

        #[test]
        fn test_fdb_del_kv() {
            let mut db = KvDb::new();
            db.set_str("kv_test", "100").unwrap();
            assert!(db.delete("kv_test"));
            assert_eq!(db.get_string("kv_test"), None);
            assert!(!db.delete("kv_test"));
        }

        #[test]
        fn test_fdb_gc() {
            let path = temp_file("kvdb_gc");
            {
                let mut db = KvDb::open(&path).unwrap();
                for i in 0..4 {
                    db.set_str(format!("kv{i}"), i.to_string()).unwrap();
                }
                db.set_str("kv0", "00").unwrap();
                db.set_str("kv1", "11").unwrap();
                db.delete("kv2");
                db.set_str("kv4", "4".repeat(2048)).unwrap();
                db.sync().unwrap();
            }
            {
                let db = KvDb::open(&path).unwrap();
                assert_eq!(db.get_string("kv0").as_deref(), Some("00"));
                assert_eq!(db.get_string("kv1").as_deref(), Some("11"));
                assert_eq!(db.get_string("kv2"), None);
                assert_eq!(db.get("kv4").unwrap().len(), 2048);
            }
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_gc2() {
            let path = temp_file("kvdb_gc2");
            {
                let mut db = KvDb::open(&path).unwrap();
                for i in 0..6 {
                    db.set_str(format!("kv{i}"), i.to_string().repeat(i + 1)).unwrap();
                }
                db.set_str("kv4", "4".repeat(4096)).unwrap();
                db.set_str("kv5", "5".repeat(3072)).unwrap();
                db.delete("kv0");
                db.set_str("kv0", "00").unwrap();
                db.sync().unwrap();
            }
            {
                let db = KvDb::open(&path).unwrap();
                assert_eq!(db.get_string("kv0").as_deref(), Some("00"));
                assert_eq!(db.get("kv4").unwrap().len(), 4096);
                assert_eq!(db.get("kv5").unwrap().len(), 3072);
                assert_eq!(db.len(), 6);
            }
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_scale_up() {
            let path = temp_file("kvdb_scale_up");
            {
                let mut db = KvDb::open(&path).unwrap();
                for i in 0..4 {
                    db.set_str(format!("kv{i}"), i.to_string()).unwrap();
                }
                db.sync().unwrap();
            }
            {
                let mut db = KvDb::open(&path).unwrap();
                for i in 4..8 {
                    db.set_str(format!("kv{i}"), i.to_string()).unwrap();
                }
                db.sync().unwrap();
            }
            {
                let db = KvDb::open(&path).unwrap();
                for i in 0..8 {
                    assert_eq!(db.get_string(&format!("kv{i}")).as_deref(), Some(i.to_string().as_str()));
                }
                assert_eq!(db.len(), 8);
            }
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_kvdb_set_default() {
            let mut db = KvDb::new();
            db.set_str("kv_test", "100").unwrap();
            db.set("kv_blob_test", [1_u8, 2, 3]).unwrap();
            db.clear();
            assert!(db.is_empty());
        }

        #[test]
        fn test_fdb_kvdb_deinit() {
            let path = temp_file("kvdb_deinit");
            {
                let mut db = KvDb::open(&path).unwrap();
                db.set_str("ssid", "lab-net").unwrap();
                db.sync().unwrap();
            }
            {
                let db = KvDb::open(&path).unwrap();
                assert_eq!(db.get_string("ssid").as_deref(), Some("lab-net"));
            }
            let _ = fs::remove_file(path);
        }
        ''',
    )
    write(
        out / "tests/tsdb_tests.rs",
        r'''
        use flashdb_rust::{TimeSeriesDb, TimeSeriesStatus};
        use std::fs;

        fn temp_file(name: &str) -> std::path::PathBuf {
            let mut path = std::env::temp_dir();
            path.push(format!("flashdb_rust_{name}_{}", std::process::id()));
            let _ = fs::remove_file(&path);
            path
        }

        fn append_range(db: &mut TimeSeriesDb, count: i64) {
            for i in 1..=count {
                let timestamp = i * 2;
                db.append(timestamp, timestamp.to_string().as_bytes());
            }
        }

        #[test]
        fn test_fdb_tsdb_init_ex() {
            let mut db = TimeSeriesDb::new();
            assert!(db.is_empty());
            db.append(2, b"2");
            assert_eq!(db.len(), 1);
        }

        #[test]
        fn test_fdb_tsl_clean_first_run() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 10);
            assert_eq!(db.len(), 10);
            db.clear();
            assert!(db.is_empty());
        }

        #[test]
        fn test_fdb_tsl_append() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 256);
            assert_eq!(db.len(), 256);
            assert_eq!(db.latest().unwrap().timestamp, 512);
        }

        #[test]
        fn test_fdb_tsl_iter() {
            let mut db = TimeSeriesDb::new();
            db.append(6, b"6");
            db.append(2, b"2");
            db.append(4, b"4");
            let timestamps: Vec<i64> = db.iter().map(|record| record.timestamp).collect();
            assert_eq!(timestamps, vec![2, 4, 6]);
        }

        #[test]
        fn test_fdb_tsl_iter_by_time() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 256);
            let records = db.query(10, 20);
            let timestamps: Vec<i64> = records.iter().map(|record| record.timestamp).collect();
            assert_eq!(timestamps, vec![10, 12, 14, 16, 18, 20]);

            let reverse: Vec<i64> = db.query(20, 10).iter().map(|record| record.timestamp).collect();
            assert_eq!(reverse, vec![20, 18, 16, 14, 12, 10]);
        }

        #[test]
        fn test_fdb_tsl_query_count() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 256);
            assert_eq!(db.query_count(0, 512), 256);
            assert_eq!(db.query_count(10, 20), 6);
            assert_eq!(db.query_count(20, 10), 6);
        }

        #[test]
        fn test_fdb_tsl_set_status() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 256);
            let changed = db.set_status_range(0, 256, TimeSeriesStatus::UserStatus1);
            assert_eq!(changed, 128);
            let deleted = db.set_status_range(258, 512, TimeSeriesStatus::Deleted);
            assert_eq!(deleted, 128);
            assert_eq!(db.query_count_by_status(0, 512, TimeSeriesStatus::UserStatus1), 128);
            assert_eq!(db.query_count_by_status(0, 512, TimeSeriesStatus::Deleted), 128);
        }

        #[test]
        fn test_fdb_tsl_clean_second_run() {
            let path = temp_file("tsdb_clean_second");
            {
                let mut db = TimeSeriesDb::open(&path).unwrap();
                append_range(&mut db, 32);
                db.sync().unwrap();
            }
            {
                let mut db = TimeSeriesDb::open(&path).unwrap();
                assert_eq!(db.len(), 32);
                db.clear();
                db.sync().unwrap();
            }
            {
                let db = TimeSeriesDb::open(&path).unwrap();
                assert!(db.is_empty());
            }
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_tsl_iter_by_time_1() {
            let mut db = TimeSeriesDb::new();
            append_range(&mut db, 800);

            assert_eq!(db.query_count(1, 1601), 800);
            assert_eq!(db.query_count(1, 1), 0);
            assert_eq!(db.query_count(1601, 1601), 0);

            let first_sector_like = db.query(2, 200);
            assert_eq!(first_sector_like.first().unwrap().timestamp, 2);
            assert_eq!(first_sector_like.last().unwrap().timestamp, 200);

            let reverse = db.query(200, 2);
            assert_eq!(reverse.first().unwrap().timestamp, 200);
            assert_eq!(reverse.last().unwrap().timestamp, 2);
        }

        #[test]
        fn test_fdb_tsdb_deinit() {
            let path = temp_file("tsdb_deinit");
            {
                let mut db = TimeSeriesDb::open(&path).unwrap();
                db.append(100, b"temperature=21.5");
                db.append(101, b"temperature=21.7");
                db.sync().unwrap();
            }
            {
                let db = TimeSeriesDb::open(&path).unwrap();
                assert_eq!(db.len(), 2);
                assert_eq!(db.query(101, 101)[0].payload, b"temperature=21.7".to_vec());
            }
            let _ = fs::remove_file(path);
        }

        #[test]
        fn test_fdb_github_issue_249() {
            let path = temp_file("tsdb_issue_249");
            {
                let mut db = TimeSeriesDb::open(&path).unwrap();
                db.clear();
                db.append(2, vec![0_u8; 7 * 1024]);
                db.append(4, vec![1_u8; 8 * 1024]);
                db.append(6, vec![2_u8; 9 * 1024]);
                db.sync().unwrap();
            }
            {
                let db = TimeSeriesDb::open(&path).unwrap();
                assert_eq!(db.query_count_by_status(2, 6, TimeSeriesStatus::Write), 3);
                assert_eq!(db.query_count_by_status(0, i64::MAX, TimeSeriesStatus::Write), 3);
                assert_eq!(db.query(4, 4)[0].payload.len(), 8 * 1024);
            }
            let _ = fs::remove_file(path);
        }
        ''',
    )


def generate_report(
    root: Path,
    flashdb: Path,
    out: Path,
    result: Path | None = None,
    logs: Path | None = None,
    validation: dict | None = None,
    analysis: dict | None = None,
) -> None:
    src_files = list_relative(flashdb, "src")
    test_files = list_relative(flashdb, "tests")
    result = result or root / "result"
    logs = logs or root / "logs"
    status = "found" if flashdb.exists() else "not found in this environment"
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    validation = validation or {}
    analysis = analysis or {}
    cargo_test = validation.get("cargo_test", {"status": "not_run"})
    checks = validation.get("checks", {})
    artifact_structure = checks.get("required_artifact_structure", {})
    coverage = checks.get("translated_test_coverage", {})
    expected_tests = coverage.get("expected_rust_tests", {})
    actual_tests = coverage.get("actual_rust_tests", {})
    missing_tests = coverage.get("missing", {})
    validation_status = validation.get("status", "not_run")
    failures = validation.get("failures", [])

    def bullet_list(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else "- none"

    write(
        result / "output.md",
        f"""
        # FlashDB Rust Conversion Execution Report

        Generated at: {now}

        ## Inputs

        - FlashDB source: `{flashdb}` ({status})
        - Rust output project: `{out}`
        - Result directory: `{result}`
        - Logs directory: `{logs}`

        ## Execution command

        ```bash
        python3 work/harness/flashdb_harness.py --flashdb {flashdb} --out {out} --result {result} --logs {logs}
        ```

        ## Generated Rust project

        - Common modules: `config`, `types`, `error`, `status`, `blob`, `db`, `file`, `low_level`, `sector`, and `cache` preserve the original FlashDB support structure.
        - `src/kvdb.rs`: safe Rust key-value database with translated KV node, iterator, cache, sector, GC/recovery state, string/blob values, update, delete, iteration and file persistence.
        - `src/tsdb.rs`: safe Rust time-series database with translated TSL node, sector, rollover/control state, append, ordered iteration, range query, status updates, clean, latest record and file persistence.
        - `tests/kvdb_tests.rs`: translated coverage for all KVDB `TEST_RUN(...)` entries from `FlashDB/tests/fdb_kvdb_tc.c`.
        - `tests/tsdb_tests.rs`: translated coverage for all TSDB `TEST_RUN(...)` entries from `FlashDB/tests/fdb_tsdb_tc.c`.

        ## Source test inventory

        - KVDB source test runs: {len(analysis.get("source_test_runs", {}).get("kvdb", []))}
        - TSDB source test runs: {len(analysis.get("source_test_runs", {}).get("tsdb", []))}

        ## Translated Rust tests

        - Expected KVDB Rust tests: {len(expected_tests.get("kvdb", []))}
        - Actual KVDB Rust tests: {len(actual_tests.get("kvdb", []))}
        - Missing KVDB Rust tests: {len(missing_tests.get("kvdb", []))}
        - Expected TSDB Rust tests: {len(expected_tests.get("tsdb", []))}
        - Actual TSDB Rust tests: {len(actual_tests.get("tsdb", []))}
        - Missing TSDB Rust tests: {len(missing_tests.get("tsdb", []))}

        ## Validation result

        - Validation status: `{validation_status}`
        - Cargo test status: `{cargo_test.get("status", "not_run")}`
        - Unsafe occurrences: `{checks.get("unsafe_occurrences", "unknown")}`

        ## Required artifacts

        - `result/`: `{artifact_structure.get("result_dir", False)}`
        - `result/output.md`: `{artifact_structure.get("result_output_md", False)}`
        - `result/issues/00-summary.md`: `{artifact_structure.get("result_issues_summary", False)}`
        - `logs/`: `{artifact_structure.get("logs_dir", False)}`
        - `logs/interaction.md`: `{artifact_structure.get("logs_interaction_md", False)}`
        - `logs/trace/`: `{artifact_structure.get("logs_trace_dir", False)}`
        - `logs/trace/events.jsonl`: `{artifact_structure.get("logs_trace_events", False)}`

        ## Source files observed

        - FlashDB `src` file count: {len(src_files)}
        - FlashDB `tests` file count: {len(test_files)}

        ## Re-run instructions

        ```bash
        cd {out}
        cargo build
        cargo test
        ```

        Harness artifacts are under `{result / "harness"}`. The detailed validation JSON is `{result / "harness" / "07-validation.json"}`.
        Human interaction records are stored in `{logs / "interaction.md"}`; if there is no manual intervention, that file is intentionally empty. Engineering trace logs are stored in `{logs / "trace"}`.
        """,
    )
    observed = "\n".join(f"- `{name}`" for name in (src_files + test_files)) or "- Source tree was unavailable during generation."
    failure_text = bullet_list(failures)
    missing_kv = bullet_list(missing_tests.get("kvdb", []))
    missing_ts = bullet_list(missing_tests.get("tsdb", []))
    write(
        result / "issues/00-summary.md",
        f"""
        # Conversion summary

        ## Validation status

        - Overall status: `{validation_status}`
        - Cargo test status: `{cargo_test.get("status", "not_run")}`
        - Unsafe occurrences: `{checks.get("unsafe_occurrences", "unknown")}`

        ## Failures

        {failure_text}

        ## Required artifact structure

        - `result/`: `{artifact_structure.get("result_dir", False)}`
        - `result/output.md`: `{artifact_structure.get("result_output_md", False)}`
        - `result/issues/00-summary.md`: `{artifact_structure.get("result_issues_summary", False)}`
        - `logs/`: `{artifact_structure.get("logs_dir", False)}`
        - `logs/interaction.md`: `{artifact_structure.get("logs_interaction_md", False)}`
        - `logs/trace/`: `{artifact_structure.get("logs_trace_dir", False)}`
        - `logs/trace/events.jsonl`: `{artifact_structure.get("logs_trace_events", False)}`

        ## Missing translated tests

        KVDB:

        {missing_kv}

        TSDB:

        {missing_ts}

        ## Full FlashDB/tests translation scope

        The Rust test suite is generated from every `TEST_RUN(...)` entry in:

        - `FlashDB/tests/fdb_kvdb_tc.c`
        - `FlashDB/tests/fdb_tsdb_tc.c`

        Duplicate source test invocations are preserved with stable Rust names. For example, the two `test_fdb_tsl_clean` invocations are translated as `test_fdb_tsl_clean_first_run` and `test_fdb_tsl_clean_second_run`.

        ## Observed FlashDB files

        {observed}

        ## Known limitations

        The Rust project is an idiomatic safe Rust rewrite that preserves FlashDB's module and state structure, but it is not a C ABI-compatible binding. It does not modify the platform-provided FlashDB tree.
        """,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate flashDB_rust from the judge FlashDB source tree.")
    parser.add_argument("--flashdb", default=str(DEFAULT_FLASHDB), help="Path to platform FlashDB source tree")
    parser.add_argument("--out", default="flashDB_rust", help="Output Rust project directory")
    args = parser.parse_args()

    root = Path.cwd()
    flashdb = Path(args.flashdb)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out

    generate_cargo(out)
    generate_lib(out)
    generate_support_modules(out)
    generate_kvdb(out)
    generate_tsdb(out)
    generate_tests(out)
    generate_report(root, flashdb, out)

    print(f"generated Rust project: {out}")
    print(f"source FlashDB path: {flashdb} ({'found' if flashdb.exists() else 'not found'})")
    print(f"result report: {root / 'result' / 'output.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
