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
