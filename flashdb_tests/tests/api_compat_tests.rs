use flashdb_rust::*;

#[test]
fn public_exports_compile() {
    let _core = DbCore::new("kv", DbKind::KeyValue);
    let _blob = Blob::new([1_u8, 2, 3]);
    let _kv_status = KvStatus::Write;
    let _tsl_status = TslStatus::Write;
    let _sector = SectorStoreStatus::Using;
}

#[test]
fn low_level_alignment_helpers_match_flashdb_macros() {
    assert_eq!(low_level::fdb_align(13, 4), 16);
    assert_eq!(low_level::fdb_align_down(13, 4), 12);
}

#[test]
fn kvdb_iterator_api_matches_c_iterator_shape() {
    let mut db = KvDb::new();
    db.set_str("a", "1").unwrap();
    let mut iter = db.iterator();
    assert!(db.iterate(&mut iter));
    assert_eq!(iter.curr_kv.unwrap().name, "a");
}

#[test]
fn tsdb_tsl_node_api_matches_c_tsl_shape() {
    let mut db = TimeSeriesDb::new();
    db.append(1, b"x");
    assert_eq!(db.latest_node().unwrap().time, 1);
}

#[test]
fn kvdb_invalid_key_is_reported() {
    let mut db = KvDb::new();
    assert!(matches!(db.set("", b"x"), Err(KvError::InvalidKey)));
}

#[test]
fn status_table_marks_status() {
    let mut table = status::StatusTable::erased(6);
    status::StatusTable::mark_written(&mut table, 2);
    assert_eq!(table.first_written(), Some(2));
}

#[test]
fn db_control_updates_config() {
    let mut db = KvDb::new();
    db.control(DbControl::SetSecSize(8192));
    assert_eq!(db.control(DbControl::GetSecSize), Some(8192));
}

#[test]
fn tsdb_control_updates_rollover() {
    let mut db = TimeSeriesDb::new();
    db.control(DbControl::SetRollover(false));
    assert_eq!(db.control(DbControl::GetRollover), Some(0));
}
