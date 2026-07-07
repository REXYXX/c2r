# {{task_title}}

如果你是主线程，请停止展开本文件；只把本文件路径交给 Code Agent。

你是 Code Agent。Python 执行框架不会生成 Rust 实现；Code Agent 只负责实现
Rust crate 的 `Cargo.toml` 与 `src/*.rs`。Rust 测试必须委派给 Test Agent；
严格验证必须委派给 Validation Agent。首读入口必须是
`result/harness/agent-entry/code-agent.json`，不要从大 JSON 开始。

## 源码与输出

- {{source_label}}：`{{source}}`
- Rust crate 输出：`{{out}}`
- 结果产物：`{{result}}`
- 日志目录：`{{logs}}`

## Agent 分工

- Main Thread：读取 `{{main_thread_task}}` 和 `agent-entry/main-thread.json`，只做编排，不读源码、不写 Rust。
- Code Agent：实现库代码、公共 API、模块边界和核心语义，只处理 `Cargo.toml` 与 `src/*.rs`。
- Test Agent：读取 `{{test_agent_task}}`，只展开测试矩阵和 benchmark 细节，负责 `tests/*.rs`。
- Validation Agent：读取 `{{validation_agent_task}}`，运行 strict 验证并输出压缩失败摘要。
- Code Agent 不要把完整 README/benchmark 覆盖矩阵或完整验证日志读入当前上下文。

## Code Agent 强制流程

1. 先读取 `{{code_plan_path}}`，不要从 function chunk 开始。
2. Phase `crate_skeleton`：创建所有 implementation files，并让 `src/lib.rs` 暴露必需模块。
3. Phase `public_api_surface`：按 `code-plan.json` 中每个模块的 `public_api_surface` 先实现可编译 API 表面和核心数据类型。
4. Phase `module_behaviour`：只在某个 API/模块语义不清楚时，读取该模块 compact manifest；仍不够时才读取一个 `functions-NNN.json`。
5. 每完成一个模块，只保留简短完成摘要，丢弃该模块上下文，再处理下一模块。
6. Cargo 可用时先运行 `cargo check` 修复编译错误；不要进入 `tests/*.rs`。
7. 实现完成后必须调用 Test Agent，并只把 `{{test_agent_task}}` 作为测试任务入口。
8. Test Agent 完成后，Code Agent 只接收测试变更摘要；不要回读完整测试矩阵。
9. 测试生成完成后必须调用 Validation Agent，并只把 `{{validation_agent_task}}` 作为验证任务入口。
10. Validation Agent 返回失败摘要后，`src/*.rs` 问题由 Code Agent 修复，`tests/*.rs` 问题交回 Test Agent。
11. 每次修复后重复 Test Agent / Validation Agent 交接，直到 strict 验证通过。
12. strict 验证失败不是完成状态；失败摘要必须作为下一轮修复输入继续处理。

## 生成产物索引

- Agent entry manifest：`{{agent_entry_manifest}}`
- Code plan：`{{code_plan_path}}`
- Code Agent manifest：`{{code_manifest_path}}`
- context manifest：`{{context_manifest_path}}`
- parity matrix：`{{parity_matrix}}`
- Test Agent 任务书：`{{test_agent_task}}`
- Validation Agent 任务书：`{{validation_agent_task}}`
- 修复压缩上下文：`{{repair_manifest}}`（验证失败后生成）

全量 analysis/profile 文件默认不生成；不要请求或构造这类大文件。
不要读取完整 `03-context.json`、`{{out_target}}` 或任何 Cargo target 目录。

## 必读约束文档

{{constraint_files_bullets}}

## Code Agent Manifest

先读 Code Plan，按 phase 执行：

```json
{{code_plan_summary_json}}
```

Code Manifest 只作为机器索引和补充信息：

```json
{{code_manifest_summary_json}}
```

## 测试与 benchmark 摘要

完整测试清单不要在 Code Agent 上下文展开；这里只保留计数，具体内容见 Test Agent 任务书。

```json
{{test_summary_json}}
```

## 上下文索引摘要

Code Agent 按模块读取 `result/harness/context/manifest.json` 中指向的
`compact_manifest`。`legacy_full_shard` 只允许人工调试，不进入模型上下文。

```json
{{context_summary_json}}
```

## 工作规则

- 直接在 `{{out}}` 中编写 Rust 源码；不要把 Rust 源码写进 Python。
- 从 `code-plan.json` 读取模块顺序、公共 API surface 和四阶段执行计划。
- 实现某个模块时，先读取对应 compact_manifest；只有行为仍不清楚时才读取一个 function_hint chunk。
- 完成一个模块后丢弃该上下文，再处理下一模块。
- 每个模块或 chunk 完成后只保留简短完成摘要，下一轮不要携带完整模块上下文。
- 仅使用安全 Rust；除非动态 profile 明确允许，不要使用 C FFI。
- Code Agent 完成库实现后，必须交给 Test Agent 生成 `tests/*.rs`。
- Test Agent 完成后，必须交给 Validation Agent 运行 strict 验证。
- 把验证失败视为生成修复提示，不要为了通过而削弱 profile 检查。
- 验证失败后优先读取 `result/harness/08-repair-context/manifest.json`，不要读取完整 `07-validation.json`。
- 如果压缩上下文中 `test_agent` 路由非空，必须再次调用 Test Agent 修复测试。
- 如果压缩上下文中 `code_agent` 路由非空，Code Agent 必须修复实现后再进入测试/验证闭环。
