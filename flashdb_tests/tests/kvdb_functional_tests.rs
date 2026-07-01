use flashdb_rust::KvDb;

#[test]
fn test_fdb_kvdb_init() { let db = KvDb::new(); assert!(db.is_empty()); }
#[test]
fn test_fdb_kvdb_init_check() { let db = KvDb::new(); assert_eq!(db.len(), 0); }
#[test]
fn test_fdb_create_kv_blob() { let mut db = KvDb::new(); db.set("kv_blob_test", [1, 2, 3]).unwrap(); assert_eq!(db.get("kv_blob_test"), Some([1, 2, 3].as_slice())); }
#[test]
fn test_fdb_change_kv_blob() { let mut db = KvDb::new(); db.set("kv_blob_test", [1]).unwrap(); db.set("kv_blob_test", [2]).unwrap(); assert_eq!(db.get("kv_blob_test"), Some([2].as_slice())); }
#[test]
fn test_fdb_del_kv_blob() { let mut db = KvDb::new(); db.set("kv_blob_test", [1]).unwrap(); assert!(db.delete("kv_blob_test")); }
#[test]
fn test_fdb_create_kv() { let mut db = KvDb::new(); db.set_str("kv_test", "100").unwrap(); assert_eq!(db.get_string("kv_test").as_deref(), Some("100")); }
#[test]
fn test_fdb_change_kv() { let mut db = KvDb::new(); db.set_str("kv_test", "100").unwrap(); db.set_str("kv_test", "101").unwrap(); assert_eq!(db.get_string("kv_test").as_deref(), Some("101")); }
#[test]
fn test_fdb_del_kv() { let mut db = KvDb::new(); db.set_str("kv_test", "100").unwrap(); assert!(db.delete("kv_test")); assert_eq!(db.get_string("kv_test"), None); }
#[test]
fn test_fdb_gc() { let mut db = KvDb::new(); for i in 0..4 { db.set_str(format!("kv{i}"), i.to_string()).unwrap(); } db.set_str("kv0", "00").unwrap(); db.delete("kv2"); assert_eq!(db.len(), 3); }
#[test]
fn test_fdb_gc2() { let mut db = KvDb::new(); db.set_str("kv4", "4".repeat(4096)).unwrap(); db.set_str("kv5", "5".repeat(3072)).unwrap(); assert_eq!(db.get("kv4").unwrap().len(), 4096); }
#[test]
fn test_fdb_scale_up() { let mut db = KvDb::new(); for i in 0..8 { db.set_str(format!("kv{i}"), i.to_string()).unwrap(); } assert_eq!(db.len(), 8); }
#[test]
fn test_fdb_kvdb_set_default() { let mut db = KvDb::new(); db.set_str("kv_test", "100").unwrap(); db.clear(); assert!(db.is_empty()); }
#[test]
fn test_fdb_kvdb_deinit() { let mut db = KvDb::new(); db.set_str("ssid", "lab-net").unwrap(); assert_eq!(db.get_string("ssid").as_deref(), Some("lab-net")); }
