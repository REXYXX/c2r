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
