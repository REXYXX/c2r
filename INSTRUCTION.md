# C to Rust Agent 执行协议

本文件只规定总入口和分发方式。角色细节以 `work/agents/*.md` 渲染出的任务书为准。

## 0. Bootstrap

第一步必须运行：

```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

bootstrap 只生成轻量 profile 摘要、agent 入口、任务书、测试需求文件和 trace，不开始写 Rust。

bootstrap 后必须存在：

```text
result/harness/agent-entry/code-agent.json
result/harness/agent-entry/test-agent.json
result/harness/agent-entry/validation-agent.json
result/harness/00-project-document-constraints.json
result/harness/00-project-document-constraints.md
result/MODEL_TASK.md
result/TEST_AGENT_TASK.md
result/VALIDATION_AGENT_TASK.md
logs/trace/profile-harness-path.json
logs/trace/profile-harness-path.md
```

## 0.1 文档优先约束

bootstrap 必须先执行 `ProjectDocumentStage`，自动发现输入工程中的根 README、
API、用例、测试、配置、移植、示例和使用文档，生成：

- `result/harness/00-project-document-constraints.json`：轻量索引、关键 MUST/SHOULD 约束和分类分片路径。
- `result/harness/00-project-document-constraints.md`：供人工与 Agent 快速阅读的高优先级摘要。
- `result/harness/document-constraints/*.json`：按 API、测试、用例、配置、移植等分类保存的完整约束，仅按任务需要加载。

后续 Agent 必须先读取该约束索引，再读取公共头文件、C 测试和实现源码。
禁止 Agent 默认一次加载全部分类分片，以免文档上下文再次膨胀。
可选 markdown profile 可通过 `documentation_discovery` 配置 `files`、
`exclude_dirs`、`max_files`、`max_bytes_per_file` 和约束数量上限。

## 1. opencode subagent 分发

主线程只编排，不读取 C 源码，不读取 Rust `src/` 或 `tests/`，不生成代码。

在 opencode 中必须使用 subagent 分别执行：

- `codeagent` subagent：读取 `result/harness/agent-entry/code-agent.json` 和 `result/MODEL_TASK.md`。
- `testagent` subagent：读取 `result/harness/agent-entry/test-agent.json` 和 `result/TEST_AGENT_TASK.md`。
- `validationagent` subagent：读取 `result/harness/agent-entry/validation-agent.json` 和 `result/VALIDATION_AGENT_TASK.md`。

启动任一 subagent 前，必须以对应 `result/*_TASK.md` 或 `result/MODEL_TASK.md`
的完整原文作为唯一 agent 说明，并核对 agent-entry 中的 `rendered_task_sha256`。
禁止手写、摘要、改写、复用历史缓存或使用外部英文提示词。若实际提示词与
`rendered_task` 不一致，必须停止并重新运行 bootstrap。

主线程禁止扮演上述任一 agent，禁止展开三个任务书正文代跑。若当前 opencode 环境不能创建 subagent，必须停止并报告阻塞。

## 2. 固定顺序

1. 主线程检查 `logs/trace/profile-harness-path.md`，确认 `ProjectDocumentStage` 在 `ProjectAnalysisStage` 之前完成，且 bootstrap 只完成到 `TranslationStage`。
2. 主线程启动 `codeagent` subagent。
3. Code Agent 完成后，主线程启动 `testagent` subagent。
4. Test Agent 完成后，主线程启动 `validationagent` subagent。
5. strict 失败时，主线程读取 `result/harness/07-validation.json` 的 `repair_required` 并按路由分发。

## 3. 完成判定

完成必须满足：

- `python3 work/run_conversion.py --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB --out flashDB_rust --result result --logs logs --strict` 返回 0。
- `logs/trace/profile-harness-path.json` 中阶段顺序完整。
- `result/output.md`、`logs/interaction.md`、`logs/trace/` 存在。

未通过 strict、跳过 cargo、只生成部分测试、只完成同名空壳测试，都不是完成状态。
