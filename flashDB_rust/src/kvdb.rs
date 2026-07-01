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
