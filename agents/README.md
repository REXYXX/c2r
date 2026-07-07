# Agent 固定说明

本目录保存各执行角色的固定中文说明。Python harness 只负责把路径、动态 profile
摘要、测试计数和 manifest 索引填入模板，不在代码中维护长篇 agent 提示词。

渲染规则：

- `main-thread.md` 渲染为输出 crate 下的 `MAIN_THREAD_TASK.md`。
- `code-agent.md` 渲染为输出 crate 下的 `MODEL_TASK.md`。
- `test-agent.md` 渲染为输出 crate 下的 `TEST_AGENT_TASK.md`。
- `validation-agent.md` 渲染为输出 crate 下的 `VALIDATION_AGENT_TASK.md`。
- `checkpoints/code-agent.md` 作为 `result/harness/context-checkpoints/code-agent.md`
  的初始模板。
- `checkpoints/test-agent.md` 作为 `result/harness/context-checkpoints/test-agent.md`
  的初始模板。
- 模板中的 `{{name}}` 占位符由 `work/harness/model_artifacts.py` 填充。

角色边界：

- Main Thread 只编排、分发和审计 trace。
- Code Agent 只负责 `Cargo.toml` 和 `src/*.rs`。
- Test Agent 只负责 `tests/*.rs` 和测试 fixture。
- Validation Agent 只负责 strict 验证和压缩失败路由。
