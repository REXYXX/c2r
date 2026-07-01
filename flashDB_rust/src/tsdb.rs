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
