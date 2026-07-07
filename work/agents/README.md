# Agent 固定说明

本目录保存各执行角色的固定中文说明。Python harness 只负责把路径、动态 profile
摘要、测试计数和 manifest 索引填入模板，不在代码中维护长篇 agent 提示词。

Agent 行为规则只维护在本目录的 Markdown 中，不再拆到额外 JSON 配置文件。
`result/harness/agent-entry/*.json` 只是运行时生成的最小入口索引，便于模型先定位
对应任务书；它不承载读取顺序、禁止事项、上下文预算或修复闭环规则。

渲染规则：

- `main-thread.md` 渲染为输出 crate 下的 `MAIN_THREAD_TASK.md`。
- `code-agent.md` 渲染为 `result/MODEL_TASK.md`。
- `test-agent.md` 渲染为 `result/TEST_AGENT_TASK.md`。
- `validation-agent.md` 渲染为 `result/VALIDATION_AGENT_TASK.md`。
- 模板中的 `{{name}}` 占位符由 `work/harness/model_artifacts.py` 填充。
- 若需要调整 agent 行为，只改对应的 `*.md` 模板，不新增 JSON 规则模板。

角色边界：

- Main Thread 只编排、分发和审计 trace。
- Code Agent 只负责 `Cargo.toml` 和 `src/*.rs`。
- Test Agent 只负责 `tests/*.rs` 和测试 fixture。
- Validation Agent 只负责 strict 验证和压缩失败路由。
