use flashdb_rust::KvDb;
use std::fs;

fn temp_file(name: &str) -> std::path::PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!("flashdb_rust_{name}_{}", std::process::id()));
    let _ = fs::remove_file(&path);
    path
}

#[test]
fn test_fdb_kvdb_init() {
    let mut db = KvDb::new();
    assert!(db.is_empty());
    assert_eq!(db.len(), 0);
    db.clear();
    assert!(db.is_empty());
}

#[test]
fn test_fdb_kvdb_init_check() {
    let path = temp_file("kvdb_init_check");
    let db = KvDb::open(&path).unwrap();
    assert!(db.is_empty());
    assert_eq!(db.keys().count(), 0);
    let _ = fs::remove_file(path);
}

#[test]
fn test_fdb_create_kv_blob() {
    let mut db = KvDb::new();
    let tick = 42_u32.to_le_bytes();
    db.set("kv_blob_test", tick).unwrap();
    assert_eq!(db.get("kv_blob_test"), Some(tick.as_slice()));
    assert!(db.contains_key("kv_blob_test"));
}

#[test]
fn test_fdb_change_kv_blob() {
    let mut db = KvDb::new();
    db.set("kv_blob_test", 42_u32.to_le_bytes()).unwrap();
    let changed = 43_u32.to_le_bytes();
    db.set("kv_blob_test", changed).unwrap();
    assert_eq!(db.get("kv_blob_test"), Some(changed.as_slice()));
    assert_eq!(db.len(), 1);
}

#[test]
fn test_fdb_del_kv_blob() {
    let mut db = KvDb::new();
    db.set("kv_blob_test", 42_u32.to_le_bytes()).unwrap();
    db.set("kv_blob_test", []).unwrap();
    assert_eq!(db.get("kv_blob_test"), Some([].as_slice()));
    assert!(db.delete("kv_blob_test"));
    assert_eq!(db.get("kv_blob_test"), None);
}

#[test]
fn test_fdb_create_kv() {
    let mut db = KvDb::new();
    db.set_str("kv_test", "100").unwrap();
    assert_eq!(db.get_string("kv_test").as_deref(), Some("100"));
}

#[test]
fn test_fdb_change_kv() {
    let mut db = KvDb::new();
    db.set_str("kv_test", "100").unwrap();
    db.set_str("kv_test", "101").unwrap();
    assert_eq!(db.get_string("kv_test").as_deref(), Some("101"));
    assert_eq!(db.len(), 1);
}

#[test]
fn test_fdb_del_kv() {
    let mut db = KvDb::new();
    db.set_str("kv_test", "100").unwrap();
    assert!(db.delete("kv_test"));
    assert_eq!(db.get_string("kv_test"), None);
    assert!(!db.delete("kv_test"));
}

#[test]
fn test_fdb_gc() {
    let path = temp_file("kvdb_gc");
    {
        let mut db = KvDb::open(&path).unwrap();
        for i in 0..4 {
            db.set_str(format!("kv{i}"), i.to_string()).unwrap();
        }
        db.set_str("kv0", "00").unwrap();
        db.set_str("kv1", "11").unwrap();
        db.delete("kv2");
        db.set_str("kv4", "4".repeat(2048)).unwrap();
        db.sync().unwrap();
    }
    {
        let db = KvDb::open(&path).unwrap();
        assert_eq!(db.get_string("kv0").as_deref(), Some("00"));
        assert_eq!(db.get_string("kv1").as_deref(), Some("11"));
        assert_eq!(db.get_string("kv2"), None);
        assert_eq!(db.get("kv4").unwrap().len(), 2048);
    }
    let _ = fs::remove_file(path);
}

#[test]
fn test_fdb_gc2() {
    let path = temp_file("kvdb_gc2");
    {
        let mut db = KvDb::open(&path).unwrap();
        for i in 0..6 {
            db.set_str(format!("kv{i}"), i.to_string().repeat(i + 1)).unwrap();
        }
        db.set_str("kv4", "4".repeat(4096)).unwrap();
        db.set_str("kv5", "5".repeat(3072)).unwrap();
        db.delete("kv0");
        db.set_str("kv0", "00").unwrap();
        db.sync().unwrap();
    }
    {
        let db = KvDb::open(&path).unwrap();
        assert_eq!(db.get_string("kv0").as_deref(), Some("00"));
        assert_eq!(db.get("kv4").unwrap().len(), 4096);
        assert_eq!(db.get("kv5").unwrap().len(), 3072);
        assert_eq!(db.len(), 6);
    }
    let _ = fs::remove_file(path);
}

#[test]
fn test_fdb_scale_up() {
    let path = temp_file("kvdb_scale_up");
    {
        let mut db = KvDb::open(&path).unwrap();
        for i in 0..4 {
            db.set_str(format!("kv{i}"), i.to_string()).unwrap();
        }
        db.sync().unwrap();
    }
    {
        let mut db = KvDb::open(&path).unwrap();
        for i in 4..8 {
            db.set_str(format!("kv{i}"), i.to_string()).unwrap();
        }
        db.sync().unwrap();
    }
    {
        let db = KvDb::open(&path).unwrap();
        for i in 0..8 {
            assert_eq!(db.get_string(&format!("kv{i}")).as_deref(), Some(i.to_string().as_str()));
        }
        assert_eq!(db.len(), 8);
    }
    let _ = fs::remove_file(path);
}

#[test]
fn test_fdb_kvdb_set_default() {
    let mut db = KvDb::new();
    db.set_str("kv_test", "100").unwrap();
    db.set("kv_blob_test", [1_u8, 2, 3]).unwrap();
    db.clear();
    assert!(db.is_empty());
}

#[test]
fn test_fdb_kvdb_deinit() {
    let path = temp_file("kvdb_deinit");
    {
        let mut db = KvDb::open(&path).unwrap();
        db.set_str("ssid", "lab-net").unwrap();
        db.sync().unwrap();
    }
    {
        let db = KvDb::open(&path).unwrap();
        assert_eq!(db.get_string("ssid").as_deref(), Some("lab-net"));
    }
    let _ = fs::remove_file(path);
}
