# FlashDB Harness Profile

This markdown file is the FlashDB-specific constraint profile consumed by the generic harness.
Keep FlashDB API tokens, parity checks, weak-model rejection rules, and context hints here instead of hard-coding them in Python.

```json harness-profile
{
  "profile": "flashdb",
  "display_name": "FlashDB",
  "default_source": "/app/code/judge-assets/02_02_c_to_rust/code/FlashDB",
  "artifact": {
    "crate_name": "flashdb_rust",
    "output_dir": "flashDB_rust",
    "source_label": "FlashDB C source",
    "task_title": "FlashDB Rust Model Task",
    "report_title": "FlashDB Rust Conversion Harness Report"
  },
  "constraint_files": [
    "work/specs/rust_design_rules.md",
    "work/workflows/flashdb_conversion_workflow.md"
  ],
  "constraint_summary_md": "# Constraint Loading\n\nThe harness loaded the fixed FlashDB model guardrails before source analysis.\n\nRequired documents:\n\n- `work/specs/rust_design_rules.md`\n- `work/workflows/flashdb_conversion_workflow.md`\n\nFlashDB API shape, one-to-one storage-engine parity, source mappings, required files, weak-model rejection rules, and test coverage matrices are declared in `work/profiles/flashdb.md`.",
  "source_layout": {
    "source_dirs": [
      "src"
    ],
    "test_dirs": [
      "tests"
    ],
    "include_dirs": [
      "inc",
      "include"
    ],
    "public_api_header": "inc/flashdb.h",
    "public_api_pattern": "\\b(fdb_[A-Za-z0-9_]+)\\s*\\(",
    "test_run_pattern": "TEST_RUN\\((test_[A-Za-z0-9_]+)\\)",
    "anchor_search_dirs": [
      "src"
    ],
    "function_hint_globs": [
      "*.c",
      "*.h"
    ]
  },
  "component_filters": {
    "kvdb": [
      "kv"
    ],
    "tsdb": [
      "tsdb"
    ],
    "port": [
      "port"
    ]
  },
  "test_suites": {
    "kvdb": {
      "source": "tests/fdb_kvdb_tc.c",
      "target": "tests/kvdb_tests.rs"
    },
    "tsdb": {
      "source": "tests/fdb_tsdb_tc.c",
      "target": "tests/tsdb_tests.rs"
    }
  },
  "readme_test_coverage": {
    "source": "tests/README_test.md",
    "required_rust_tests": {
      "tests/kvdb_tests.rs": [
        "test_fdb_kvdb_init",
        "test_fdb_kvdb_init_check",
        "test_fdb_create_kv_blob",
        "test_fdb_change_kv_blob",
        "test_fdb_del_kv_blob",
        "test_fdb_create_kv",
        "test_fdb_change_kv",
        "test_fdb_del_kv",
        "test_fdb_gc",
        "test_fdb_gc2",
        "test_fdb_scale_up",
        "test_fdb_kvdb_set_default",
        "test_fdb_kvdb_deinit"
      ],
      "tests/tsdb_tests.rs": [
        "test_fdb_tsdb_init_ex",
        "test_fdb_tsl_clean_first_run",
        "test_fdb_tsl_append",
        "test_fdb_tsl_iter",
        "test_fdb_tsl_iter_by_time",
        "test_fdb_tsl_query_count",
        "test_fdb_tsl_set_status",
        "test_fdb_tsl_clean_second_run",
        "test_fdb_tsl_iter_by_time_1",
        "test_fdb_tsdb_deinit",
        "test_fdb_github_issue_249"
      ],
      "tests/benchmark_tests.rs": [
        "test_benchmark_kvdb_set_string",
        "test_benchmark_kvdb_get_string",
        "test_benchmark_kvdb_set_blob",
        "test_benchmark_kvdb_get_blob",
        "test_benchmark_kvdb_update_string",
        "test_benchmark_kvdb_iterate_all",
        "test_benchmark_kvdb_delete",
        "test_benchmark_tsdb_append",
        "test_benchmark_tsdb_iterate_all",
        "test_benchmark_tsdb_iter_by_time",
        "test_benchmark_tsdb_query_count"
      ]
    },
    "benchmark": {
      "source": "tests/benchmark/bench_main.c",
      "config": "tests/benchmark/fdb_cfg.h",
      "rust_target": "tests/benchmark_tests.rs",
      "constants": {
        "BENCH_SEC_SIZE": 4096,
        "BENCH_KVDB_SECS": 128,
        "BENCH_TSDB_SECS": 128,
        "BENCH_KV_COUNT": 1000,
        "BENCH_KV_BLOB_SIZE": 128,
        "BENCH_TSL_COUNT": 2000,
        "BENCH_TSL_BLOB_SIZE": 64,
        "BENCH_ITER_COUNT": 3
      },
      "kvdb_operations": [
        {
          "operation": "set (string)",
          "rust_test": "test_benchmark_kvdb_set_string",
          "count": "BENCH_KV_COUNT"
        },
        {
          "operation": "get (string)",
          "rust_test": "test_benchmark_kvdb_get_string",
          "count": "BENCH_KV_COUNT"
        },
        {
          "operation": "set (blob)",
          "rust_test": "test_benchmark_kvdb_set_blob",
          "count": "BENCH_KV_COUNT",
          "blob_size": "BENCH_KV_BLOB_SIZE"
        },
        {
          "operation": "get (blob)",
          "rust_test": "test_benchmark_kvdb_get_blob",
          "count": "BENCH_KV_COUNT",
          "blob_size": "BENCH_KV_BLOB_SIZE"
        },
        {
          "operation": "update (string)",
          "rust_test": "test_benchmark_kvdb_update_string",
          "count": "BENCH_KV_COUNT"
        },
        {
          "operation": "iterate all",
          "rust_test": "test_benchmark_kvdb_iterate_all",
          "expected_count": "BENCH_KV_COUNT * 2"
        },
        {
          "operation": "delete",
          "rust_test": "test_benchmark_kvdb_delete",
          "count": "BENCH_KV_COUNT"
        }
      ],
      "tsdb_operations": [
        {
          "operation": "append",
          "rust_test": "test_benchmark_tsdb_append",
          "count": "BENCH_TSL_COUNT",
          "blob_size": "BENCH_TSL_BLOB_SIZE"
        },
        {
          "operation": "iterate all",
          "rust_test": "test_benchmark_tsdb_iterate_all",
          "expected_count": "BENCH_TSL_COUNT"
        },
        {
          "operation": "iter by time",
          "rust_test": "test_benchmark_tsdb_iter_by_time",
          "expected_count": "BENCH_TSL_COUNT"
        },
        {
          "operation": "query count",
          "rust_test": "test_benchmark_tsdb_query_count",
          "expected_count": "BENCH_TSL_COUNT"
        }
      ],
      "semantic_requirements": [
        "Use file/POSIX-mode equivalent storage paths for benchmark databases.",
        "Reset KVDB with default data before each KVDB benchmark iteration.",
        "Clean TSDB and reset monotonic timestamp counter before each TSDB benchmark iteration.",
        "Use monotonic timestamps equivalent to ++bench_cur_time.",
        "Assert operation counts and resulting database contents, not just timing output.",
        "Do not require wall-clock performance thresholds; validate benchmark semantics and measured result fields are sane."
      ]
    }
  },
  "required_output_files": [
    "Cargo.toml",
    "src/lib.rs",
    "src/blob.rs",
    "src/cache.rs",
    "src/config.rs",
    "src/db.rs",
    "src/error.rs",
    "src/file.rs",
    "src/kvdb.rs",
    "src/low_level.rs",
    "src/sector.rs",
    "src/status.rs",
    "src/tsdb.rs",
    "src/types.rs",
    "tests/kvdb_tests.rs",
    "tests/tsdb_tests.rs",
    "tests/benchmark_tests.rs"
  ],
  "api_symbols": {
    "src/lib.rs": [
      "pub mod blob;",
      "pub mod cache;",
      "pub mod config;",
      "pub mod db;",
      "pub mod error;",
      "pub mod file;",
      "pub mod kvdb;",
      "pub mod low_level;",
      "pub mod sector;",
      "pub mod status;",
      "pub mod tsdb;",
      "pub mod types;",
      "pub use blob::{Blob, SavedBlob};",
      "pub use db::DbCore;",
      "pub use error::{FlashDbError, FlashDbResult};",
      "pub use kvdb::{KvDb, KvError};",
      "pub use status::{KvStatus, SectorDirtyStatus, SectorStoreStatus, TslStatus};",
      "pub use tsdb::{TimeSeriesDb, TimeSeriesRecord, TimeSeriesStatus, TsError};",
      "pub use types::{DbConfig, DbControl, DbKind};"
    ],
    "src/config.rs": [
      "pub const FDB_SW_VERSION",
      "pub const FDB_WRITE_GRAN",
      "pub fn align(",
      "pub fn wg_align(",
      "pub fn status_table_size("
    ],
    "src/error.rs": [
      "pub enum FlashDbError",
      "pub type FlashDbResult",
      "impl std::error::Error for FlashDbError"
    ],
    "src/types.rs": [
      "pub enum DbKind",
      "pub struct DbConfig",
      "pub enum DbControl"
    ],
    "src/status.rs": [
      "pub enum KvStatus",
      "pub enum TslStatus",
      "pub enum SectorStoreStatus",
      "pub enum SectorDirtyStatus",
      "pub struct StatusTable"
    ],
    "src/blob.rs": [
      "pub struct SavedBlob",
      "pub struct Blob",
      "fdb_blob_make",
      "fdb_blob_read"
    ],
    "src/sector.rs": [
      "pub struct KvSectorInfo",
      "pub struct TsSectorInfo"
    ],
    "src/cache.rs": [
      "pub struct KvCacheNode",
      "pub struct SectorCache"
    ],
    "src/db.rs": [
      "pub struct DbCore",
      "pub fn control(&mut self, command: DbControl)"
    ],
    "src/file.rs": [
      "pub struct FileStorage",
      "flash_read",
      "flash_write",
      "flash_erase",
      ".fdb."
    ],
    "src/low_level.rs": [
      "pub fn fdb_align(",
      "pub fn fdb_calc_crc32",
      "pub fn set_status(",
      "pub fn get_status(",
      "pub fn flash_read("
    ],
    "src/kvdb.rs": [
      "pub enum KvError",
      "pub struct KvNode",
      "pub struct KvIterator",
      "pub struct KvDb",
      "core: DbCore",
      "cur_sector: KvSectorInfo",
      "kv_cache_table: Vec<KvCacheNode>",
      "pub fn new() -> Self",
      "pub fn open(path: impl AsRef<Path>) -> Result<Self, KvError>",
      "pub fn control(&mut self, command: DbControl) -> Option<u32>",
      "pub fn set(&mut self, key: impl Into<String>, value: impl AsRef<[u8]>) -> Result<(), KvError>",
      "pub fn set_str(&mut self, key: impl Into<String>, value: impl AsRef<str>) -> Result<(), KvError>",
      "pub fn get(&self, key: &str) -> Option<&[u8]>",
      "pub fn get_string(&self, key: &str) -> Option<String>",
      "pub fn contains_key(&self, key: &str) -> bool",
      "pub fn delete(&mut self, key: &str) -> bool",
      "pub fn clear(&mut self)",
      "pub fn len(&self) -> usize",
      "pub fn is_empty(&self) -> bool",
      "pub fn keys(&self) -> impl Iterator<Item = &str>",
      "pub fn iterator(&self) -> KvIterator",
      "pub fn iterate(&self, iterator: &mut KvIterator) -> bool",
      "pub fn sync(&self) -> Result<(), KvError>"
    ],
    "src/tsdb.rs": [
      "pub enum TsError",
      "pub enum TimeSeriesStatus",
      "pub struct TimeSeriesRecord",
      "pub struct TslNode",
      "pub timestamp: i64",
      "pub payload: Vec<u8>",
      "pub status: TimeSeriesStatus",
      "pub struct TimeSeriesDb",
      "core: DbCore",
      "cur_sec: TsSectorInfo",
      "rollover: bool",
      "pub fn new() -> Self",
      "pub fn open(path: impl AsRef<Path>) -> Result<Self, TsError>",
      "pub fn control(&mut self, command: DbControl) -> Option<u32>",
      "pub fn append(&mut self, timestamp: i64, payload: impl AsRef<[u8]>)",
      "pub fn len(&self) -> usize",
      "pub fn is_empty(&self) -> bool",
      "pub fn iter(&self) -> impl Iterator<Item = &TimeSeriesRecord>",
      "pub fn query(&self, from: i64, to: i64) -> Vec<TimeSeriesRecord>",
      "pub fn query_count(&self, from: i64, to: i64) -> usize",
      "pub fn query_count_by_status(&self, from: i64, to: i64, status: TimeSeriesStatus) -> usize",
      "pub fn latest(&self) -> Option<&TimeSeriesRecord>",
      "pub fn set_status_range(&mut self, from: i64, to: i64, status: TimeSeriesStatus) -> usize",
      "pub fn clear(&mut self)",
      "pub fn latest_node(&self) -> Option<TslNode>",
      "pub fn sync(&self) -> Result<(), TsError>"
    ]
  },
  "c_api_parity_symbols": {
    "kvdb": [
      "fdb_kvdb_init",
      "fdb_kvdb_control",
      "fdb_kvdb_check",
      "fdb_kvdb_deinit",
      "fdb_kv_set",
      "fdb_kv_get",
      "fdb_kv_set_blob",
      "fdb_kv_get_blob",
      "fdb_kv_del",
      "fdb_kv_get_obj",
      "fdb_kv_to_blob",
      "fdb_kv_set_default",
      "fdb_kv_print",
      "fdb_kv_iterator_init",
      "fdb_kv_iterate"
    ],
    "tsdb": [
      "fdb_tsdb_init",
      "fdb_tsdb_control",
      "fdb_tsdb_deinit",
      "fdb_tsl_append",
      "fdb_tsl_append_with_ts",
      "fdb_tsl_iter",
      "fdb_tsl_iter_reverse",
      "fdb_tsl_iter_by_time",
      "fdb_tsl_query_count",
      "fdb_tsl_max_blob_count",
      "fdb_tsl_set_status",
      "fdb_tsl_clean",
      "fdb_tsl_to_blob"
    ],
    "blob_low_level": [
      "fdb_blob_make",
      "fdb_blob_read",
      "fdb_calc_crc32"
    ]
  },
  "c_api_parity_modules": {
    "kvdb": [
      "src/kvdb.rs"
    ],
    "tsdb": [
      "src/tsdb.rs"
    ],
    "blob_low_level": [
      "src/blob.rs",
      "src/low_level.rs"
    ]
  },
  "one_to_one_features": {
    "low_level.rs": {
      "status_table_set_get": [
        "set_status",
        "get_status",
        "StatusTable"
      ],
      "crc32": [
        "fdb_calc_crc32",
        "CRC32"
      ],
      "continue_ff": [
        "continue_ff",
        "FDB_BYTE_ERASED"
      ],
      "flash_ops": [
        "flash_read",
        "flash_write",
        "flash_erase",
        "flash_write_align"
      ]
    },
    "file.rs": {
      "sector_file_layout": [
        ".fdb.",
        "sector_file",
        "sec_size"
      ],
      "address_offset_io": [
        "SeekFrom::Start",
        "addr %",
        "flash_read",
        "flash_write"
      ],
      "erase_to_ff": [
        "FDB_BYTE_ERASED",
        "flash_erase"
      ]
    },
    "kvdb.rs": {
      "kv_magic_header": [
        "KV_MAGIC_WORD",
        "KV_SECTOR_MAGIC_WORD"
      ],
      "kv_status_flow": [
        "PreWrite",
        "Write",
        "PreDelete",
        "Deleted"
      ],
      "kv_crc": [
        "fdb_calc_crc32",
        "crc"
      ],
      "kv_sector_state": [
        "KvSectorInfo",
        "SectorStoreStatus",
        "SectorDirtyStatus"
      ],
      "kv_gc_recovery": [
        "gc_collect",
        "recovery",
        "oldest_addr"
      ],
      "kv_default": [
        "default_kv",
        "fdb_kv_set_default"
      ],
      "kv_iterator_metadata": [
        "iterated_obj_bytes",
        "iterated_value_bytes",
        "sector_addr",
        "traversed_len"
      ],
      "kv_blob_object": [
        "fdb_kv_get_blob",
        "fdb_kv_get_obj",
        "fdb_kv_to_blob"
      ]
    },
    "tsdb.rs": {
      "tsl_magic_header": [
        "TSL_SECTOR_MAGIC_WORD",
        "TslNode"
      ],
      "tsl_statuses": [
        "PreWrite",
        "Write",
        "UserStatus1",
        "Deleted",
        "UserStatus2"
      ],
      "tsl_time_rules": [
        "last_time",
        "<=",
        "WriteErr"
      ],
      "tsl_max_len": [
        "max_len",
        "WriteErr"
      ],
      "tsl_rollover": [
        "rollover",
        "rollover_oldest"
      ],
      "tsl_callback_iteration": [
        "fdb_tsl_iter",
        "FnMut",
        "break"
      ],
      "tsl_reverse_time_iteration": [
        "fdb_tsl_iter_reverse",
        "fdb_tsl_iter_by_time"
      ],
      "tsl_status_by_node": [
        "fdb_tsl_set_status",
        "addr_index"
      ],
      "tsl_max_blob_count": [
        "fdb_tsl_max_blob_count",
        "log_idx"
      ],
      "tsl_to_blob_clean": [
        "fdb_tsl_to_blob",
        "fdb_tsl_clean"
      ]
    }
  },
  "behaviour_model_rejection": {
    "kvdb_map_primary": {
      "file": "src/kvdb.rs",
      "bad": [
        "BTreeMap<String, Vec<u8>>"
      ],
      "required_offsets": [
        "KV_MAGIC_WORD",
        "KvSectorInfo",
        "fdb_calc_crc32",
        "gc_collect"
      ]
    },
    "tsdb_vec_primary": {
      "file": "src/tsdb.rs",
      "bad": [
        "Vec<TimeSeriesRecord>"
      ],
      "required_offsets": [
        "TslNode",
        "TSL_SECTOR_MAGIC_WORD",
        "max_len",
        "rollover",
        "addr_index"
      ]
    },
    "single_flashdb_dat_backend": {
      "file": "src/file.rs",
      "bad": [
        "flashdb.dat"
      ],
      "required_offsets": [
        ".fdb.",
        "sector_file",
        "flash_erase"
      ]
    }
  },
  "internal_parity_anchors": {
    "fdb_utils.c": [
      "fdb_calc_crc32",
      "_fdb_set_status",
      "_fdb_get_status",
      "_fdb_write_status",
      "_fdb_read_status",
      "_fdb_continue_ff_addr",
      "_fdb_flash_read",
      "_fdb_flash_erase",
      "_fdb_flash_write",
      "_fdb_flash_write_align",
      "fdb_blob_make",
      "fdb_blob_read"
    ],
    "fdb_file.c": [
      "get_db_file_path",
      "open_db_file",
      "_fdb_file_read",
      "_fdb_file_write",
      "_fdb_file_erase"
    ],
    "fdb_kvdb.c": [
      "read_sector_info",
      "format_sector",
      "update_sec_status",
      "sector_iterator",
      "alloc_kv",
      "new_kv",
      "new_kv_ex",
      "create_kv_blob",
      "del_kv",
      "set_kv",
      "gc_collect",
      "gc_collect_by_free_size",
      "fdb_kv_set_default",
      "fdb_kv_iterator_init",
      "fdb_kv_iterate",
      "fdb_kv_get_blob",
      "fdb_kv_get_obj",
      "fdb_kv_to_blob"
    ],
    "fdb_tsdb.c": [
      "read_sector_info",
      "update_sec_status",
      "write_tsl",
      "tsl_append",
      "fdb_tsl_append",
      "fdb_tsl_append_with_ts",
      "fdb_tsl_iter",
      "fdb_tsl_iter_reverse",
      "fdb_tsl_iter_by_time",
      "fdb_tsl_query_count",
      "fdb_tsl_max_blob_count",
      "fdb_tsl_set_status",
      "fdb_tsl_clean",
      "fdb_tsl_to_blob"
    ]
  },
  "source_to_rust_modules": {
    "FlashDB/inc/fdb_def.h": [
      "src/config.rs",
      "src/types.rs",
      "src/status.rs",
      "src/sector.rs",
      "src/cache.rs",
      "src/blob.rs"
    ],
    "FlashDB/inc/fdb_low_lvl.h": [
      "src/low_level.rs"
    ],
    "FlashDB/src/fdb.c": [
      "src/db.rs"
    ],
    "FlashDB/src/fdb_file.c": [
      "src/file.rs"
    ],
    "FlashDB/src/fdb_utils.c": [
      "src/blob.rs",
      "src/low_level.rs"
    ],
    "FlashDB/src/fdb_kvdb.c": [
      "src/kvdb.rs",
      "src/sector.rs",
      "src/cache.rs",
      "src/status.rs"
    ],
    "FlashDB/src/fdb_tsdb.c": [
      "src/tsdb.rs",
      "src/sector.rs",
      "src/status.rs"
    ]
  },
  "module_contexts": {
    "kvdb": {
      "component_key": "kvdb",
      "target": "src/kvdb.rs",
      "required_mechanisms": [
        "sector header and KV header layout",
        "KV status table transitions",
        "CRC32 validation",
        "allocation/new_kv/create_kv_blob flow",
        "two-phase delete",
        "GC and recovery",
        "default KV",
        "iterator metadata",
        "blob/object helpers"
      ]
    },
    "tsdb": {
      "component_key": "tsdb",
      "target": "src/tsdb.rs",
      "required_mechanisms": [
        "sector header and log index layout",
        "get_time and append_with_ts split",
        "max_len and monotonic timestamp rejection",
        "rollover/update_sec_status",
        "callback iteration including early stop",
        "reverse and by-time iteration",
        "status update by TSL node",
        "max blob count",
        "clean and to_blob"
      ]
    }
  },
  "function_hint_tokens": [
    "fdb_kv_",
    "fdb_blob_",
    "fdb_tsdb_",
    "fdb_tsl_",
    "gc_collect",
    "read_sector_info",
    "update_sec_status",
    "write_tsl",
    "fdb_calc_crc32"
  ],
  "duplicate_test_name_map": [
    {
      "suite": "tsdb",
      "source": "test_fdb_tsl_clean",
      "occurrence": 1,
      "target": "test_fdb_tsl_clean_first_run"
    },
    {
      "suite": "tsdb",
      "source": "test_fdb_tsl_clean",
      "occurrence": 2,
      "target": "test_fdb_tsl_clean_second_run"
    }
  ],
  "harness_report_appendix": "## Agent harness execution\n\nHarness artifacts are available under `{harness_dir}`.\n\n- OutputScaffoldAgent: required result and logs artifact structure.\n- ConstraintLoadingAgent: FlashDB API, Rust design, workflow, and weak-model guardrails.\n- ProjectAnalysisAgent: source inventory and component buckets.\n- SkeletonGenerationAgent: Cargo crate layout.\n- ContextBuilderAgent: minimum module/function context.\n- ParityMatrixAgent: public API and storage-engine parity matrix.\n- TranslationAgent: Rust module and full FlashDB/tests test generation.\n- CompileAgent: `cargo check` diagnostics when cargo is available.\n- RepairAgent: compile-result triage.\n- ValidationAgent: structural checks, C API parity checks, one-to-one feature checks, translated test coverage checks, and `cargo test` when cargo is available."
}
```
