# FlashDB 可选覆盖 Profile

这个文件不是 FlashDB 的完整规则清单。执行框架会根据 `--source`
实时生成源码布局、公共 API、测试、benchmark、模块映射和 parity 锚点。

这里仅保留少量人工偏好：项目展示名、默认输出目录和通用约束文档。

```json harness-profile
{
  "profile": "flashdb",
  "display_name": "FlashDB",
  "artifact": {
    "crate_name": "flashdb_rust",
    "output_dir": "flashDB_rust",
    "source_label": "FlashDB C 源码",
    "task_title": "FlashDB Rust 模型任务",
    "report_title": "FlashDB Rust 转换执行框架报告"
  },
  "constraint_files": [
    "work/specs/rust_design_rules.md"
  ],
  "disallow_unsafe": true
}
```
