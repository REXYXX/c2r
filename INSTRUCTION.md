# C to Rust Agent 执行协议

本文件只规定执行顺序。不要把它当项目说明阅读。

## 0. 总入口

第一步必须运行 bootstrap：

```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

bootstrap 只允许生成轻量 profile 摘要、agent 入口、任务书、上下文 shard、
测试 shard 和 trace，不允许开始手写 Rust。

bootstrap 后必须存在：

```text
result/harness/agent-entry/main-thread.json
result/harness/agent-entry/code-agent.json
result/harness/agent-entry/test-agent.json
result/harness/agent-entry/validation-agent.json
result/harness/code-plan.json
result/harness/code-manifest.json
result/harness/01-profile-summary.md
result/harness/context/manifest.json
result/harness/test-requirements/manifest.json
logs/trace/profile-harness-path.json
logs/trace/profile-harness-path.md
```

## 1. 主线程步骤

主线程只编排，不生成代码，不读取 C 源码，不读取 Rust `src/` 或 `tests/`。

按顺序执行：

1. 读取 `result/harness/agent-entry/main-thread.json`。
2. 读取 `<RUST_OUT>/MAIN_THREAD_TASK.md`。
3. 检查 `logs/trace/profile-harness-path.md`，确认 bootstrap 阶段只完成到 `TranslationStage`。
4. 启动 Code Agent，只传递 `result/harness/agent-entry/code-agent.json` 和 `<RUST_OUT>/MODEL_TASK.md` 路径。
5. Code Agent 完成后，确认其已调用 Test Agent。
6. Test Agent 完成后，确认 Code Agent 已调用 Validation Agent。
7. 验证失败时只读取 `result/harness/08-repair-context/manifest.json` 和对应 agent shard，再按 `next_agent` 分发。

主线程禁止打开：

```text
<C_PROJECT>/**
<RUST_OUT>/src/**
<RUST_OUT>/tests/**
<RUST_OUT>/target/**
result/harness/07-validation.json
result/harness/context/**/*.json
result/harness/test-requirements/**/*.json
```

## 2. Code Agent 固定步骤

Code Agent 只负责 `Cargo.toml` 和 `src/*.rs`。测试必须交给 Test Agent，验证必须交给 Validation Agent。

按顺序执行：

1. 读取 `result/harness/agent-entry/code-agent.json`。
2. 读取 `<RUST_OUT>/MODEL_TASK.md`。
3. 读取 `result/harness/code-plan.json`。
4. 读取 `result/harness/code-manifest.json`。
5. Phase `crate_skeleton`：创建所有 implementation files，并让 `src/lib.rs` 暴露必需模块。
6. Phase `public_api_surface`：按 `code-plan.json` 中每个模块的 `public_api_surface` 先实现可编译 API 表面和核心数据类型。
7. Phase `module_behaviour`：只在 API/模块语义不清楚时读取该模块 compact manifest；仍不够时才读取一个 `functions-NNN.json`。
8. 只编辑 `Cargo.toml` 和 `src/*.rs`。
9. 完成当前 module 后只保留简短完成摘要。
10. 丢弃当前 module 上下文，再处理下一项。
11. Cargo 可用时先运行 `cargo check` 修复编译错误。
12. 所有实现完成后，调用 Test Agent，并只传递 `result/harness/agent-entry/test-agent.json` 和 `<RUST_OUT>/TEST_AGENT_TASK.md` 路径。
13. Test Agent 返回后，Code Agent 只接收测试变更摘要，不回读完整测试矩阵。
14. 调用 Validation Agent，并只传递 `result/harness/agent-entry/validation-agent.json` 和 `<RUST_OUT>/VALIDATION_AGENT_TASK.md` 路径。
15. Validation Agent 返回失败摘要时：
    - `code_agent` 路由非空：Code Agent 修复 `Cargo.toml` 或 `src/*.rs`。
    - `test_agent` 路由非空：交回 Test Agent 修复 `tests/*.rs`。
    - `validation_agent` 路由非空：交回 Validation Agent 处理验证环境或命令问题。
16. 重复 Test Agent -> Validation Agent 闭环，直到 strict 验证通过。

Code Agent 禁止：

- 禁止编辑 `tests/*.rs`。
- 全量 analysis/profile 文件默认不生成；禁止请求或构造这类大文件。
- 禁止一次性展开完整 context 或完整测试矩阵。
- 禁止从 `functions-NNN.json` 开始生成代码；必须先执行 `code-plan.json` 的 skeleton/API surface 阶段。
- 禁止读取 Cargo `target/`。
- 禁止把 Rust 源码写进 Python。
- 禁止在 strict 失败时停止。

## 3. Test Agent 固定步骤

Test Agent 只负责 `tests/*.rs` 和必要测试 fixture。

按顺序执行：

1. 读取 `result/harness/agent-entry/test-agent.json`。
2. 读取 `<RUST_OUT>/TEST_AGENT_TASK.md`。
3. 读取 `result/harness/test-requirements/manifest.json`。
4. 每轮只选择一个 target manifest。
5. 每轮只处理一个 batch。
6. 每个测试只读取对应 compact semantic shard。
7. 按 shard 中的 `logic_consistency` 蓝图实现目标 API、测试数据和断言条件。
8. 逐个测试检查“目标 API、测试数据、断言条件”三层逻辑一致性。
9. 只编辑 `tests/*.rs`、测试 fixture 或必要 dev-dependencies。
10. 完成 batch 后只保留简短完成摘要。
11. 丢弃当前 batch 上下文，再处理下一批。
12. 完成后只返回测试变更摘要和发现的最小 API 缺口。

Test Agent 禁止：

- 禁止重写核心 `src/*.rs`。
- 禁止删除测试、减少 shard、降低断言或修改 validation 规则来通过验证。
- 禁止用同族但不同语义的 API 替代 shard 中要求的 API。

## 4. Validation Agent 固定步骤

Validation Agent 只负责 strict 验证和压缩失败路由。

按顺序执行：

1. 读取 `result/harness/agent-entry/validation-agent.json`。
2. 读取 `<RUST_OUT>/VALIDATION_AGENT_TASK.md`。
3. 运行 strict 验证：

```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

4. Cargo 可用时，在 `flashDB_rust` 下运行：

```bash
cargo check
cargo test
```

5. 验证失败时，生成并返回：

```text
result/harness/08-repair-context/manifest.json
result/harness/08-repair-context/code-agent.json
result/harness/08-repair-context/test-agent.json
result/harness/08-repair-context/validation-agent.json
result/harness/08-repair-context/summary.md
```

Validation Agent 禁止：

- 禁止把完整 `07-validation.json`、完整 cargo 日志或完整测试矩阵回灌给 Code Agent/Test Agent。
- 禁止削弱 profile、测试矩阵、benchmark 覆盖或 unsafe 检查。
- 禁止把 `--skip-cargo` 当作最终通过。

## 5. 修复闭环

每次 strict 失败后，只按压缩上下文修复：

1. 先读 `result/harness/08-repair-context/manifest.json`。
2. 查看 `next_agent`。
3. 只读取对应 agent shard。
4. 只读取 shard item 中的 `recommended_reads`。
5. 对应 agent 修复后，再回到 Validation Agent。
6. 只有 strict 验证通过才算完成。

## 6. 完成判定

完成必须满足：

- `python3 work/run_conversion.py --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB --out flashDB_rust --result result --logs logs --strict` 返回 0。
- `logs/trace/profile-harness-path.json` 中阶段顺序完整。
- `result/output.md` 存在。
- `logs/interaction.md` 存在。
- `logs/trace/` 存在。
- `result/harness/08-repair-context/manifest.json` 中无待修复路由，或 strict 已通过。

未通过 strict、跳过 cargo、只生成部分测试、只完成同名空壳测试，都不是完成状态。
