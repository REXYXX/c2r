# {{project}} Main Thread 任务

你是主线程，只负责编排和分发。不要读取项目源码，不要读取 Rust `src/` 或
`tests/`，不要生成、修复或审查 Rust 代码内容。代码生成完全交给 Code Agent。

## 主线程入口

- 首读：`{{main_thread_entry}}`
- Agent 入口索引：`{{agent_entry_manifest}}`
- 执行路径 trace：`{{trace_md}}`
- 验证失败压缩路由：`{{repair_manifest}}`

## 允许动作

- 运行 bootstrap harness，让 Python 扫描源码并生成轻量摘要、manifest 和任务书。
- 把 `agent-entry/code-agent.json` 与 `MODEL_TASK.md` 交给 Code Agent。
- 把 `agent-entry/test-agent.json` 与 `TEST_AGENT_TASK.md` 交给 Test Agent。
- 把 `agent-entry/validation-agent.json` 与 `VALIDATION_AGENT_TASK.md` 交给 Validation Agent。
- 读取 `logs/trace/profile-harness-path.*` 审计 harness 节点是否按序执行。
- 验证失败时只读取 `08-repair-context` 的 manifest、summary 和对应 agent shard。

## 禁止动作

- 不要打开或摘要 C 源项目：`{{source}}`。
- 不要打开或编辑 Rust 实现目录：`{{out_src}}`。
- 不要打开或编辑 Rust 测试目录：`{{out_tests}}`。
- 不要读取 Code Agent 任务书正文：`{{model_task}}`；只把路径交给 Code Agent。
- 不要读取 Test Agent 任务书正文：`{{test_agent_task}}`；只把路径交给 Test Agent。
- 不要读取 Validation Agent 任务书正文：`{{validation_agent_task}}`；只把路径交给 Validation Agent。
- 全量 analysis/profile 文件默认不生成；不要请求或构造这类大文件。
- 不要读取完整 `07-validation.json`、完整 context 或完整测试矩阵。
- 不要在主线程里写 Rust 代码、Rust 测试或根据错误日志直接修复源码。

## 固定分发顺序

1. bootstrap 完成后，主线程只确认 agent-entry 和 trace 已生成。
2. 主线程启动 Code Agent；Code Agent 负责 `Cargo.toml` 和 `src/*.rs`。
3. Code Agent 完成实现后必须启动 Test Agent；Test Agent 负责 `tests/*.rs`。
4. Test Agent 完成后必须启动 Validation Agent；Validation Agent 运行 strict 验证。
5. 若验证失败，主线程只按 `08-repair-context/manifest.json` 的 `next_agent` 分发下一轮。
6. 直到 Validation Agent 返回 strict passed，主线程才可以整理最终报告。

## Agent Entry 摘要

```json
{{agent_entries_json}}
```
