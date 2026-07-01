# FlashDB C API to Rust API Mapping

This inventory is based on `FlashDB/inc/flashdb.h`. The Rust suite validates
the fixed safe Rust contract rather than C ABI compatibility or C FFI.

| C API group | Rust API/module |
|---|---|
| `fdb_kvdb_init`, `fdb_kvdb_deinit`, `fdb_kvdb_control`, `fdb_kvdb_check` | `KvDb`, `DbCore`, `DbControl`, `KvError` |
| `fdb_tsdb_init`, `fdb_tsdb_deinit`, `fdb_tsdb_control` | `TimeSeriesDb`, `DbCore`, `DbControl`, `TsError` |
| `fdb_blob_make`, `fdb_blob_read` | `Blob`, `SavedBlob`, `KvDb::get`, `TimeSeriesRecord::payload` |
| `fdb_kv_set`, `fdb_kv_get`, `fdb_kv_del` | `KvDb::set_str`, `KvDb::get_string`, `KvDb::delete` |
| `fdb_kv_set_blob`, `fdb_kv_get_blob` | `KvDb::set`, `KvDb::get` |
| `fdb_kv_get_obj`, `fdb_kv_to_blob` | `KvNode`, `Blob`, `KvDb::iterate` |
| `fdb_kv_set_default` | `KvDb::clear` |
| `fdb_kv_iterator_init`, `fdb_kv_iterate` | `KvIterator`, `KvDb::iterator`, `KvDb::iterate` |
| `fdb_tsl_append`, `fdb_tsl_append_with_ts` | `TimeSeriesDb::append` |
| `fdb_tsl_iter`, `fdb_tsl_iter_reverse`, `fdb_tsl_iter_by_time` | `TimeSeriesDb::iter`, `TimeSeriesDb::query` |
| `fdb_tsl_query_count` | `TimeSeriesDb::query_count`, `query_count_by_status` |
| `fdb_tsl_set_status` | `TimeSeriesDb::set_status_range` |
| `fdb_tsl_clean` | `TimeSeriesDb::clear` |
| `fdb_tsl_to_blob` | `TslNode`, `TimeSeriesRecord::payload` |

Structure mapping:

- `fdb_db` -> `DbCore`
- `fdb_kvdb` -> `KvDb`
- `fdb_tsdb` -> `TimeSeriesDb`
- `fdb_blob` -> `Blob` and `SavedBlob`
- `fdb_kv` -> `KvNode`
- `fdb_kv_iterator` -> `KvIterator`
- `fdb_tsl` -> `TslNode`
- `kvdb_sec_info` -> `KvSectorInfo`
- `tsdb_sec_info` -> `TsSectorInfo`
- `kv_cache_node` -> `KvCacheNode`
