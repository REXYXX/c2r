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
