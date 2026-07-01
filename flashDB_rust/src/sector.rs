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
