use flashdb_rust::{KvDb, TimeSeriesDb, TimeSeriesStatus};
use std::time::{Duration, Instant};

const BENCH_KV_COUNT: usize = 1000;
const BENCH_TSL_COUNT: usize = 2000;

fn measure<F: FnOnce()>(name: &str, count: usize, f: F) -> Duration {
    let start = Instant::now();
    f();
    let elapsed = start.elapsed();
    eprintln!("{name}: {count} ops in {:?}", elapsed);
    elapsed
}

#[test]
fn kvdb_performance_workload_matches_flashdb_c_benchmark() {
    let count = if std::env::var("FLASHDB_PERF_SCALE").as_deref() == Ok("quick") { 100 } else { BENCH_KV_COUNT };
    let mut db = KvDb::new();
    measure("KVDB set (string)", count, || for i in 0..count { db.set_str(format!("str_{i}"), format!("val_{i}")).unwrap(); });
    measure("KVDB get (string)", count, || for i in 0..count { assert!(db.get_string(&format!("str_{i}")).is_some()); });
    measure("KVDB update (string)", count, || for i in 0..count { db.set_str(format!("str_{i}"), format!("upd_{i}")).unwrap(); });
    measure("KVDB iterate all", count, || assert_eq!(db.keys().count(), count));
    measure("KVDB delete", count, || for i in 0..count { assert!(db.delete(&format!("str_{i}"))); });
}

#[test]
fn tsdb_performance_workload_matches_flashdb_c_benchmark() {
    let count = if std::env::var("FLASHDB_PERF_SCALE").as_deref() == Ok("quick") { 200 } else { BENCH_TSL_COUNT };
    let mut db = TimeSeriesDb::new();
    measure("TSDB append", count, || for i in 0..count { db.append((i + 1) as i64, [0xCD_u8; 64]); });
    measure("TSDB iterate all", count, || assert_eq!(db.iter().count(), count));
    measure("TSDB iter by time", count, || assert_eq!(db.query(1, count as i64).len(), count));
    measure("TSDB query count", count, || assert_eq!(db.query_count_by_status(1, count as i64, TimeSeriesStatus::Write), count));
}
