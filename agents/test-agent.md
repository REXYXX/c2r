# {{project}} Test Agent 任务

你是 Test Agent。目标是生成完整 Rust 测试，不占用 Code Agent 上下文。
首读入口必须是 `result/harness/agent-entry/test-agent.json`。

## 输入与输出

- C 源项目：`{{source}}`
- Rust crate：`{{out}}`
- 结果目录：`{{result}}`

## 允许编辑范围

- 优先编辑 `tests/*.rs`。
- 可按需补充测试 fixture、dev-dependencies 或测试辅助模块。
- 不要重写核心 `src/*.rs`；若发现 API 缺口，记录最小缺口并交回 Code Agent 处理。

## 必需测试文件

{{required_test_files_bullets}}

## 测试 Requirement Manifest

- 根索引：`{{test_requirements_manifest}}`
- 先读取根索引，再按目标测试文件读取 target manifest。
- 按 target manifest 的 `batches` 逐批处理，每批最多 3 个测试。
- 每个测试的深层语义只读取对应 semantic shard，不要一次性展开全部 shard。

```json
{{test_requirement_summary_json}}
```

## C 测试语义覆盖方式

同名 Rust 测试不够；semantic shard 由 C 测试函数实时抽取。Test Agent 必须逐个
shard 覆盖其中的公开 API 调用、断言字段、常量/循环规模、辅助函数调用和代表性测试数据。

semantic shard 的 `static_validation` 是硬门禁，必须逐项满足：

- `required_api_calls` 与 `required_api_call_counts`：Rust 测试体中必须出现相同公开 API，并满足 C 测试抽取到的最小调用次数。
- `required_expanded_api_calls` 与 `required_expanded_api_call_counts`：C helper 函数展开后出现的 API，也必须在目标测试文件中有等价覆盖。
- `required_macro_expansion_tokens`：宏链展开出的规模 token 必须体现在测试文件中，用于约束数据量、循环规模和容量边界。
- `required_assertion_fields`、`required_assertion_constants`、`minimum_assertions`：断言不能被弱化为只检查返回值。
- `forbidden_api_calls`：不得用同族但不同语义的公开 API 替代 C 测试实际调用的 API。
- 这些 token 必须出现在可执行测试代码中；注释中的 token 不计入验证覆盖。

## 验证修复闭环

1. 首轮按 manifest 和 shard 生成所有 `tests/*.rs`。
2. 运行 Validation Agent 指定的 strict 验证；本地快速诊断可临时使用 `--validate --skip-cargo`，但最终不能以跳过 cargo 作为通过结果。
3. 若验证失败，只读取 `result/harness/08-repair-context/manifest.json` 和 `test-agent.json/md`，不要读取完整 `07-validation.json`。
4. 对每个压缩 item，只读取 `recommended_reads` 指向的 target manifest 和 semantic shard 后修复。
5. 修复后再次交给 Validation Agent；只要 strict 未通过，就继续处理下一轮失败摘要。
6. 不得通过删除测试、减少 shard、修改 Python validation、降低断言或改写动态 profile 来通过验证。

## 测试工作规则

- 按 target manifest 中的 `required_rust_tests` 逐项创建 Rust `#[test]`。
- 实现或修复时每轮只处理一个 batch；完成 batch 后丢弃上下文。
- 每个 batch 完成后更新 `result/harness/context-checkpoints/test-agent.md`，下一轮只携带 checkpoint。
- 按每个测试的 semantic shard 覆盖深层行为；不能只保留同名空壳或浅层 happy path。
- 对重复 C 测试名使用动态 profile 已生成的唯一 Rust 测试名。
- 每个测试使用隔离临时状态，避免测试间共享全局状态。
- benchmark 风格测试只验证语义和结果结构，不把墙钟耗时作为稳定断言。
- 完成后运行 `cargo test`；若失败，只保留必要诊断摘要给 Validation Agent。
- Validation Agent 返回测试失败摘要时，继续修复直到压缩上下文中 `test_agent` 为空且 strict 通过。
