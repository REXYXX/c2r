use flashdb_rust::{TimeSeriesDb, TimeSeriesStatus};

fn append_range(db: &mut TimeSeriesDb, count: i64) {
    for i in 1..=count { db.append(i * 2, (i * 2).to_string()); }
}

#[test]
fn test_fdb_tsdb_init_ex() { let db = TimeSeriesDb::new(); assert!(db.is_empty()); }
#[test]
fn test_fdb_tsl_clean_first_run() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 10); db.clear(); assert!(db.is_empty()); }
#[test]
fn test_fdb_tsl_append() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 256); assert_eq!(db.latest().unwrap().timestamp, 512); }
#[test]
fn test_fdb_tsl_iter() { let mut db = TimeSeriesDb::new(); db.append(6, b"6"); db.append(2, b"2"); db.append(4, b"4"); assert_eq!(db.iter().map(|r| r.timestamp).collect::<Vec<_>>(), vec![2, 4, 6]); }
#[test]
fn test_fdb_tsl_iter_by_time() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 256); assert_eq!(db.query(10, 20).len(), 6); assert_eq!(db.query(20, 10).len(), 6); }
#[test]
fn test_fdb_tsl_query_count() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 256); assert_eq!(db.query_count(0, 512), 256); }
#[test]
fn test_fdb_tsl_set_status() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 256); assert_eq!(db.set_status_range(0, 256, TimeSeriesStatus::UserStatus1), 128); assert_eq!(db.set_status_range(258, 512, TimeSeriesStatus::Deleted), 128); }
#[test]
fn test_fdb_tsl_clean_second_run() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 32); db.clear(); assert!(db.is_empty()); }
#[test]
fn test_fdb_tsl_iter_by_time_1() { let mut db = TimeSeriesDb::new(); append_range(&mut db, 800); assert_eq!(db.query_count(1, 1601), 800); assert_eq!(db.query(200, 2).first().unwrap().timestamp, 200); }
#[test]
fn test_fdb_tsdb_deinit() { let mut db = TimeSeriesDb::new(); db.append(100, b"temperature=21.5"); assert_eq!(db.query(100, 100).len(), 1); }
#[test]
fn test_fdb_github_issue_249() { let mut db = TimeSeriesDb::new(); db.append(2, vec![0_u8; 7 * 1024]); db.append(4, vec![1_u8; 8 * 1024]); db.append(6, vec![2_u8; 9 * 1024]); assert_eq!(db.query_count_by_status(2, 6, TimeSeriesStatus::Write), 3); }
