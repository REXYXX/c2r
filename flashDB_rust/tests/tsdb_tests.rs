use flashdb_rust::{TimeSeriesDb, TimeSeriesStatus};
use std::fs;

fn temp_file(name: &str) -> std::path::PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!("flashdb_rust_{name}_{}", std::process::id()));
    let _ = fs::remove_file(&path);
    path
}

fn append_range(db: &mut TimeSeriesDb, count: i64) {
    for i in 1..=count {
        let timestamp = i * 2;
        db.append(timestamp, timestamp.to_string().as_bytes());
    }
}

#[test]
fn test_fdb_tsdb_init_ex() {
    let mut db = TimeSeriesDb::new();
    assert!(db.is_empty());
    db.append(2, b"2");
    assert_eq!(db.len(), 1);
}

#[test]
fn test_fdb_tsl_clean_first_run() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 10);
    assert_eq!(db.len(), 10);
    db.clear();
    assert!(db.is_empty());
}

#[test]
fn test_fdb_tsl_append() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 256);
    assert_eq!(db.len(), 256);
    assert_eq!(db.latest().unwrap().timestamp, 512);
}

#[test]
fn test_fdb_tsl_iter() {
    let mut db = TimeSeriesDb::new();
    db.append(6, b"6");
    db.append(2, b"2");
    db.append(4, b"4");
    let timestamps: Vec<i64> = db.iter().map(|record| record.timestamp).collect();
    assert_eq!(timestamps, vec![2, 4, 6]);
}

#[test]
fn test_fdb_tsl_iter_by_time() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 256);
    let records = db.query(10, 20);
    let timestamps: Vec<i64> = records.iter().map(|record| record.timestamp).collect();
    assert_eq!(timestamps, vec![10, 12, 14, 16, 18, 20]);

    let reverse: Vec<i64> = db.query(20, 10).iter().map(|record| record.timestamp).collect();
    assert_eq!(reverse, vec![20, 18, 16, 14, 12, 10]);
}

#[test]
fn test_fdb_tsl_query_count() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 256);
    assert_eq!(db.query_count(0, 512), 256);
    assert_eq!(db.query_count(10, 20), 6);
    assert_eq!(db.query_count(20, 10), 6);
}

#[test]
fn test_fdb_tsl_set_status() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 256);
    let changed = db.set_status_range(0, 256, TimeSeriesStatus::UserStatus1);
    assert_eq!(changed, 128);
    let deleted = db.set_status_range(258, 512, TimeSeriesStatus::Deleted);
    assert_eq!(deleted, 128);
    assert_eq!(db.query_count_by_status(0, 512, TimeSeriesStatus::UserStatus1), 128);
    assert_eq!(db.query_count_by_status(0, 512, TimeSeriesStatus::Deleted), 128);
}

#[test]
fn test_fdb_tsl_clean_second_run() {
    let path = temp_file("tsdb_clean_second");
    {
        let mut db = TimeSeriesDb::open(&path).unwrap();
        append_range(&mut db, 32);
        db.sync().unwrap();
    }
    {
        let mut db = TimeSeriesDb::open(&path).unwrap();
        assert_eq!(db.len(), 32);
        db.clear();
        db.sync().unwrap();
    }
    {
        let db = TimeSeriesDb::open(&path).unwrap();
        assert!(db.is_empty());
    }
    let _ = fs::remove_file(path);
}

#[test]
fn test_fdb_tsl_iter_by_time_1() {
    let mut db = TimeSeriesDb::new();
    append_range(&mut db, 800);

    assert_eq!(db.query_count(1, 1601), 800);
    assert_eq!(db.query_count(1, 1), 0);
    assert_eq!(db.query_count(1601, 1601), 0);

    let first_sector_like = db.query(2, 200);
    assert_eq!(first_sector_like.first().unwrap().timestamp, 2);
    assert_eq!(first_sector_like.last().unwrap().timestamp, 200);

    let reverse = db.query(200, 2);
    assert_eq!(reverse.first().unwrap().timestamp, 200);
    assert_eq!(reverse.last().unwrap().timestamp, 2);
}

#[test]
fn test_fdb_tsdb_deinit() {
    let path = temp_file("tsdb_deinit");
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

#[test]
fn test_fdb_github_issue_249() {
    let path = temp_file("tsdb_issue_249");
    {
        let mut db = TimeSeriesDb::open(&path).unwrap();
        db.clear();
        db.append(2, vec![0_u8; 7 * 1024]);
        db.append(4, vec![1_u8; 8 * 1024]);
        db.append(6, vec![2_u8; 9 * 1024]);
        db.sync().unwrap();
    }
    {
        let db = TimeSeriesDb::open(&path).unwrap();
        assert_eq!(db.query_count_by_status(2, 6, TimeSeriesStatus::Write), 3);
        assert_eq!(db.query_count_by_status(0, i64::MAX, TimeSeriesStatus::Write), 3);
        assert_eq!(db.query(4, 4)[0].payload.len(), 8 * 1024);
    }
    let _ = fs::remove_file(path);
}
