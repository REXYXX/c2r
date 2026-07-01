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
