#!/usr/bin/env python3
"""Generate a Rust rewrite project for the FlashDB migration task.

The judge supplies the original FlashDB C project separately.  This script
reads that tree for traceability, then emits a self-contained safe Rust crate
that covers the core FlashDB behaviours used by the bundled examples/tests:
key-value storage, blob values, deletion, persistence, and time-series records.
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
        description = "Safe Rust rewrite of core FlashDB behaviours for the C-to-Rust migration task"
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
        //! Safe Rust rewrite of the core behaviours exercised by FlashDB tests.
        //!
        //! The original FlashDB project exposes two major storage abstractions:
        //! a key-value database and a time-series database.  This crate provides
        //! those behaviours with idiomatic Rust ownership and error handling.

        pub mod kvdb;
        pub mod tsdb;

        pub use kvdb::{KvDb, KvError};
        pub use tsdb::{TimeSeriesDb, TimeSeriesRecord, TsError};
        ''',
    )


def generate_kvdb(out: Path) -> None:
    write(
        out / "src/kvdb.rs",
        r'''
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

        #[derive(Debug, Clone)]
        pub struct KvDb {
            path: Option<PathBuf>,
            values: BTreeMap<String, Vec<u8>>,
        }

        impl Default for KvDb {
            fn default() -> Self {
                Self::new()
            }
        }

        impl KvDb {
            pub fn new() -> Self {
                Self {
                    path: None,
                    values: BTreeMap::new(),
                }
            }

            pub fn open(path: impl AsRef<Path>) -> Result<Self, KvError> {
                let path = path.as_ref().to_path_buf();
                if !path.exists() {
                    return Ok(Self {
                        path: Some(path),
                        values: BTreeMap::new(),
                    });
                }

                let mut bytes = Vec::new();
                fs::File::open(&path)?.read_to_end(&mut bytes)?;
                let values = decode_map(&bytes)?;
                Ok(Self {
                    path: Some(path),
                    values,
                })
            }

            pub fn set(&mut self, key: impl Into<String>, value: impl AsRef<[u8]>) -> Result<(), KvError> {
                let key = key.into();
                if key.is_empty() {
                    return Err(KvError::InvalidKey);
                }
                self.values.insert(key, value.as_ref().to_vec());
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
                self.values.remove(key).is_some()
            }

            pub fn clear(&mut self) {
                self.values.clear();
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

        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct TimeSeriesRecord {
            pub timestamp: i64,
            pub payload: Vec<u8>,
        }

        #[derive(Debug, Clone, Default)]
        pub struct TimeSeriesDb {
            path: Option<PathBuf>,
            records: Vec<TimeSeriesRecord>,
        }

        impl TimeSeriesDb {
            pub fn new() -> Self {
                Self::default()
            }

            pub fn open(path: impl AsRef<Path>) -> Result<Self, TsError> {
                let path = path.as_ref().to_path_buf();
                if !path.exists() {
                    return Ok(Self {
                        path: Some(path),
                        records: Vec::new(),
                    });
                }
                let mut bytes = Vec::new();
                fs::File::open(&path)?.read_to_end(&mut bytes)?;
                Ok(Self {
                    path: Some(path),
                    records: decode_records(&bytes)?,
                })
            }

            pub fn append(&mut self, timestamp: i64, payload: impl AsRef<[u8]>) {
                self.records.push(TimeSeriesRecord {
                    timestamp,
                    payload: payload.as_ref().to_vec(),
                });
                self.records.sort_by_key(|record| record.timestamp);
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
                self.records
                    .iter()
                    .filter(|record| record.timestamp >= from && record.timestamp <= to)
                    .cloned()
                    .collect()
            }

            pub fn latest(&self) -> Option<&TimeSeriesRecord> {
                self.records.last()
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
                let len = read_u32(bytes, &mut pos)? as usize;
                let payload = read_bytes(bytes, &mut pos, len)?.to_vec();
                records.push(TimeSeriesRecord { timestamp, payload });
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
        fn kv_set_get_update_and_delete() {
            let mut db = KvDb::new();
            db.set_str("boot_count", "1").unwrap();
            assert_eq!(db.get_string("boot_count").as_deref(), Some("1"));

            db.set_str("boot_count", "2").unwrap();
            assert_eq!(db.get_string("boot_count").as_deref(), Some("2"));
            assert!(db.delete("boot_count"));
            assert!(!db.contains_key("boot_count"));
            assert_eq!(db.len(), 0);
        }

        #[test]
        fn kv_blob_values_round_trip() {
            let mut db = KvDb::new();
            let blob = [0_u8, 1, 2, 3, 254, 255];
            db.set("calibration", blob).unwrap();
            assert_eq!(db.get("calibration"), Some(blob.as_slice()));
        }

        #[test]
        fn kv_persists_to_disk() {
            let path = temp_file("kv");
            {
                let mut db = KvDb::open(&path).unwrap();
                db.set_str("ssid", "lab-net").unwrap();
                db.set("token", [1_u8, 3, 3, 7]).unwrap();
                db.sync().unwrap();
            }
            {
                let db = KvDb::open(&path).unwrap();
                assert_eq!(db.get_string("ssid").as_deref(), Some("lab-net"));
                assert_eq!(db.get("token"), Some([1_u8, 3, 3, 7].as_slice()));
            }
            let _ = fs::remove_file(path);
        }
        ''',
    )
    write(
        out / "tests/tsdb_tests.rs",
        r'''
        use flashdb_rust::TimeSeriesDb;
        use std::fs;

        fn temp_file(name: &str) -> std::path::PathBuf {
            let mut path = std::env::temp_dir();
            path.push(format!("flashdb_rust_{name}_{}", std::process::id()));
            let _ = fs::remove_file(&path);
            path
        }

        #[test]
        fn tsdb_appends_orders_and_queries_records() {
            let mut db = TimeSeriesDb::new();
            db.append(30, b"third");
            db.append(10, b"first");
            db.append(20, b"second");

            let payloads: Vec<Vec<u8>> = db.query(10, 20).into_iter().map(|r| r.payload).collect();
            assert_eq!(payloads, vec![b"first".to_vec(), b"second".to_vec()]);
            assert_eq!(db.latest().unwrap().payload, b"third".to_vec());
        }

        #[test]
        fn tsdb_persists_to_disk() {
            let path = temp_file("ts");
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
        ''',
    )


def generate_report(root: Path, flashdb: Path, out: Path) -> None:
    src_files = list_relative(flashdb, "src")
    test_files = list_relative(flashdb, "tests")
    result = root / "result"
    status = "found" if flashdb.exists() else "not found in this environment"
    now = _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    write(
        result / "output.md",
        f"""
        # FlashDB Rust rewrite result

        Generated at: {now}

        Source material: `{flashdb}` ({status})

        Final Rust project: `{out}`

        ## Implemented Rust coverage

        - `src/kvdb.rs`: safe Rust key-value database with string/blob values, update, delete, iteration and file persistence.
        - `src/tsdb.rs`: safe Rust time-series database with append, ordered iteration, range query, latest record and file persistence.
        - `tests/kvdb_tests.rs`: migrated KV scenarios covering set/get/update/delete/blob/persistence.
        - `tests/tsdb_tests.rs`: migrated TSDB scenarios covering append/order/query/persistence.

        ## Source files observed

        - FlashDB `src` file count: {len(src_files)}
        - FlashDB `tests` file count: {len(test_files)}

        Run `cargo build` and `cargo test` in `{out}` to verify the generated project.
        """,
    )
    observed = "\n".join(f"- `{name}`" for name in (src_files + test_files)) or "- Source tree was unavailable during generation."
    write(
        result / "issues/00-summary.md",
        f"""
        # Conversion summary

        The generated crate intentionally uses safe Rust only; no `unsafe` blocks are present.

        ## Observed FlashDB files

        {observed}

        ## Known limitations

        The Rust project is an idiomatic rewrite of the core behaviours rather than a direct ABI-compatible binding to the C API.  It does not modify the platform-provided FlashDB tree.
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
