# FlashDB Rust Test Coverage Matrix

## Functional Coverage

### KVDB

| C test | Rust test |
|---|---|
| `test_fdb_kvdb_init` | `test_fdb_kvdb_init` |
| `test_fdb_kvdb_init_check` | `test_fdb_kvdb_init_check` |
| `test_fdb_create_kv_blob` | `test_fdb_create_kv_blob` |
| `test_fdb_change_kv_blob` | `test_fdb_change_kv_blob` |
| `test_fdb_del_kv_blob` | `test_fdb_del_kv_blob` |
| `test_fdb_create_kv` | `test_fdb_create_kv` |
| `test_fdb_change_kv` | `test_fdb_change_kv` |
| `test_fdb_del_kv` | `test_fdb_del_kv` |
| `test_fdb_gc` | `test_fdb_gc` |
| `test_fdb_gc2` | `test_fdb_gc2` |
| `test_fdb_scale_up` | `test_fdb_scale_up` |
| `test_fdb_kvdb_set_default` | `test_fdb_kvdb_set_default` |
| `test_fdb_kvdb_deinit` | `test_fdb_kvdb_deinit` |

### TSDB

| C test | Rust test |
|---|---|
| `test_fdb_tsdb_init_ex` | `test_fdb_tsdb_init_ex` |
| `test_fdb_tsl_clean` first run | `test_fdb_tsl_clean_first_run` |
| `test_fdb_tsl_append` | `test_fdb_tsl_append` |
| `test_fdb_tsl_iter` | `test_fdb_tsl_iter` |
| `test_fdb_tsl_iter_by_time` | `test_fdb_tsl_iter_by_time` |
| `test_fdb_tsl_query_count` | `test_fdb_tsl_query_count` |
| `test_fdb_tsl_set_status` | `test_fdb_tsl_set_status` |
| `test_fdb_tsl_clean` second run | `test_fdb_tsl_clean_second_run` |
| `test_fdb_tsl_iter_by_time_1` | `test_fdb_tsl_iter_by_time_1` |
| `test_fdb_tsdb_deinit` | `test_fdb_tsdb_deinit` |
| `test_fdb_github_issue_249` | `test_fdb_github_issue_249` |

## API Compatibility Coverage

The API suite covers public exports, error traits, invalid key handling, corrupt
file handling, persistence, `DbControl`, status mutation, `KvIterator`,
`KvNode`, `TslNode`, `Blob`, sector structs, and low-level alignment helpers.

## Performance Coverage

| C benchmark operation | Rust performance section |
|---|---|
| KVDB set string | `KVDB set (string)` |
| KVDB get string | `KVDB get (string)` |
| KVDB set blob | `KVDB set (blob)` |
| KVDB get blob | `KVDB get (blob)` |
| KVDB update string | `KVDB update (string)` |
| KVDB iterate all | `KVDB iterate all` |
| KVDB delete | `KVDB delete` |
| TSDB append | `TSDB append` |
| TSDB iterate all | `TSDB iterate all` |
| TSDB iter by time | `TSDB iter by time` |
| TSDB query count | `TSDB query count` |
