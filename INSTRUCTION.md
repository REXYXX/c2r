# C to Rust 动态转换框架执行说明

## 0. 执行协议

执行型编码模型必须先运行通用 harness，让框架根据 `--source` 动态生成
`MAIN_THREAD_TASK.md`、`MODEL_TASK.md`、`TEST_AGENT_TASK.md`、
`VALIDATION_AGENT_TASK.md`、`code-manifest.json`、`context/manifest.json`、
`test-requirements/manifest.json` 和 `04-function-parity.json`。
主线程只负责编排、分发和审计 trace，不读取项目源码，不读取 Rust
`src/` 或 `tests/`，不生成 Rust 代码。
Code Agent 才负责 Rust 库代码生成；Rust 测试迁移必须交给 Test Agent；
严格验证必须交给 Validation Agent。
主线程首读入口必须是 `result/harness/agent-entry/main-thread.json`。
所有子 agent 首读入口必须是 `result/harness/agent-entry/*.json`，再按入口文件
给出的 `read_order` 渐进读取。
只有 Rust 代码和测试写完后，才运行验证阶段生成 `07-validation.json`。
如果 strict 验证失败，失败结果只能作为下一轮修复输入，不能作为完成状态；
必须按 `result/harness/08-repair-context/manifest.json` 中的压缩路由继续修复并复验。

执行顺序必须通过 `logs/trace/` 中的 profile harness 路径产物审计：

- `logs/trace/profile-harness-path.json`：profile harness 内部关键节点执行路径。
- `logs/trace/profile-harness-path.md`：便于人工查看的 profile harness 内部路径表。

开发示例：
```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

首次运行是 bootstrap 阶段，只生成动态 profile、上下文、parity 矩阵和
四份任务书，不会运行 `CompileStage`、`RepairStage`、`ValidationStage`。
实现或修复完成后运行严格入口：

开发示例：
```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
  --strict
```

禁止事项：

- 禁止在生成 `MAIN_THREAD_TASK.md`、`MODEL_TASK.md` 和 `04-function-parity.json` 之前直接开始翻译。
- 禁止只按 README 或个人理解手写测试清单，必须按 harness 的动态 coverage 矩阵覆盖。
- 禁止跳过自动发现出的 benchmark 测试。
- 禁止把 Rust 源码硬编码到 Python 文件中。
- 禁止主线程读取 C 源码、Rust `src/`、Rust `tests/`、完整 context
  或完整测试矩阵；这些内容必须交给对应子 agent 渐进读取。
- 禁止主线程打开 `MODEL_TASK.md`、`TEST_AGENT_TASK.md`、
  `VALIDATION_AGENT_TASK.md` 的正文来代替子 agent 执行任务；主线程只传递入口路径。
- 禁止用 `--skip-cargo` 作为最终验证。
- 禁止在 strict 验证失败时停止；必须修复到 strict 验证通过或明确说明外部环境缺失。
- 禁止 Code Agent 在主上下文展开完整测试矩阵和验证日志；必须交给
  Test Agent 和 Validation Agent 处理。
- 禁止 agent 一次性读取 `result/harness/01-analysis.json`、
  `result/harness/01-effective-profile.json` 或完整测试语义矩阵。
- 禁止多轮修复时回灌完整 `result/harness/07-validation.json`；必须先读
  `result/harness/08-repair-context/manifest.json` 和对应 agent shard。
- 禁止读取 Cargo `target/` 目录、`.o` 文件、`.fdb.*` 数据文件等非源码产物。
- 禁止同一 agent 会话连续处理多个 module/chunk/batch 而不压缩；完成后必须
  更新 `result/harness/context-checkpoints/*.md`，下一轮只携带 checkpoint。

## 1. 设计原则

本工程提供源码驱动的通用 HarnessStage 执行框架，而不是 FlashDB 专属转换脚本。
Python 负责调度、trace、命令执行、源码实时分析、动态 profile 生成、分工任务书
和通用验证；Rust 业务实现必须由模型基于源码和生成产物编写。
固定 agent 行为说明位于 `agents/` 目录，Python 只读取这些 Markdown 模板并填充
动态路径、manifest 摘要和统计信息，不在脚本中维护长篇 agent 提示词。
为适配有限上下文，主线程只处理 `MAIN_THREAD_TASK.md`、`agent-entry`
和 `logs/trace`，不读取源码或生成代码。Code Agent 只处理 `MODEL_TASK.md` 与
`result/harness/code-manifest.json` 中的 Rust 实现任务；
测试矩阵和 benchmark 细节由 Test Agent 读取 `TEST_AGENT_TASK.md` 与
`result/harness/test-requirements/manifest.json` 后按 shard 处理；
严格验证和压缩失败摘要由 Validation Agent 读取 `VALIDATION_AGENT_TASK.md` 处理。

Agent 使用顺序必须固定：

1. 主线程运行 bootstrap，先读取 `result/harness/agent-entry/main-thread.json` 与 `MAIN_THREAD_TASK.md`。
2. 主线程只确认 agent-entry、任务书和 trace 是否生成，不读取 C 源码或 Rust `src/tests`。
3. 主线程启动 Code Agent，并只传递 `result/harness/agent-entry/code-agent.json` 与 `MODEL_TASK.md` 路径。
4. Code Agent 实现某个模块时，只读取 `context/manifest.json` 中对应模块 `compact_manifest`，函数提示一次只读一个 `function_hint_chunks`。
5. Code Agent 必须调用 Test Agent，并把 `TEST_AGENT_TASK.md` 作为测试任务入口。
6. Test Agent 先读取 `result/harness/agent-entry/test-agent.json`，再读取 `test-requirements/manifest.json`。
7. Test Agent 一次只处理一个 target manifest 中的一个 batch，每个 batch 最多 3 个测试和必要 semantic shard。
8. Test Agent 生成或修复 `tests/*.rs`，只返回测试变更摘要和必要失败摘要。
9. Code Agent 必须调用 Validation Agent，并把 `VALIDATION_AGENT_TASK.md` 作为验证任务入口。
10. Validation Agent 运行 strict 验证，只返回压缩失败摘要；源码问题回到 Code Agent，测试问题回到 Test Agent。
11. 主线程只读取 `08-repair-context` 压缩路由，并按 `next_agent` 分发下一轮。
12. 只要压缩修复上下文的 `status` 不是 `passed`，就按 `next_agent` 和对应 shard 继续闭环，直到 strict 通过。

`work/profiles/flashdb.md` 只是可选覆盖层，当前仅保留展示名、crate 名和输出目录
偏好。以下内容不再写在 `flashdb.md` 中，而是由 harness 从 C 工程实时生成：

- 源码目录、测试目录、include 目录和公共 API 头文件。
- 公共 C API parity token。
- 源码文件到 Rust 模块的映射。
- `TEST_RUN(...)` 单元测试清单。
- `README_test.md` 声明的测试覆盖矩阵。
- benchmark 源文件、常量和被主流程调用的 benchmark 操作。
- 重复测试名映射，例如 FlashDB 中两次 `test_fdb_tsl_clean`。
- 内部函数锚点和模块上下文。

动态 profile 会写入：

```text
result/harness/01-derived-profile.json
result/harness/01-effective-profile.json
result/harness/01-effective-profile.md
result/harness/code-manifest.json
result/harness/context/manifest.json
result/harness/test-requirements/manifest.json
result/harness/agent-entry/manifest.json
result/harness/agent-entry/main-thread.json
```

固定 agent 模板位于：

```text
agents/main-thread.md
agents/code-agent.md
agents/test-agent.md
agents/validation-agent.md
agents/checkpoints/code-agent.md
agents/checkpoints/test-agent.md
```

其中 `01-*` 文件只用于机器审计和人工调试。Agent 正常执行时必须从
`agent-entry/*.json` 进入，再按需读取 `code-manifest.json`、
`context/manifest.json` 和 `test-requirements/manifest.json`。

Main Thread 的上下文读取顺序：

```text
agent-entry/main-thread.json
MAIN_THREAD_TASK.md
agent-entry/manifest.json
logs/trace/profile-harness-path.md
08-repair-context/manifest.json（验证失败后）
08-repair-context/<next-agent>.json（只读压缩路由）
context-checkpoints/*.md（只读进度摘要）
```

Main Thread 禁止读取源码目录、Rust `src/`、Rust `tests/`、完整
`01-analysis.json`、完整 `07-validation.json`、完整 context 和完整测试矩阵。

Code Agent 的上下文读取顺序：

```text
agent-entry/code-agent.json
MODEL_TASK.md
code-manifest.json
context/manifest.json
context/modules/<module>/manifest.json
context/modules/<module>/functions-NNN.json
context-checkpoints/code-agent.md
```

Test Agent 的上下文读取顺序：

```text
agent-entry/test-agent.json
TEST_AGENT_TASK.md
test-requirements/manifest.json
test-requirements/<target>.json
test-requirements/batches/<target>/batch-NNN.json
test-requirements/<target>/<test>.json
context-checkpoints/test-agent.md
```

每完成一个 Code chunk 或 Test batch，agent 需要把已完成内容压缩进对应
checkpoint 文件，然后在新上下文继续下一 chunk/batch。checkpoint 只记录已完成项、
当前项、待办项、阻塞项和必要 API/测试摘要，不记录完整源码、完整测试或完整日志。

验证失败后会额外写入：

```text
result/harness/08-repair-context/manifest.json
result/harness/08-repair-context/summary.md
result/harness/08-repair-context/code-agent.json
result/harness/08-repair-context/test-agent.json
result/harness/08-repair-context/validation-agent.json
```

多轮修复时，agent 必须先读 `08-repair-context/manifest.json`，再只读自己
负责的 agent shard 和每个 item 的 `recommended_reads`。`07-validation.json`
保留为机器审计文件，不应被反复塞进模型上下文。

## 2. 参数说明

- `--source`：源 C 项目目录；动态 profile 的主要输入。
- `--profile`：可选 markdown 覆盖 profile；未提供时完全从 `--source` 生成。
- `--out`：生成 Rust 项目的目录；默认由动态 profile 推导。
- `--result`：报告和 harness 阶段产物目录，默认 `result`。
- `--logs`：交互记录和 trace 日志目录，默认 `logs`。
- `--cargo`：Cargo 可执行文件名，默认 `cargo`。
- `--validate`：显式运行 compile/repair/validation 阶段；调试时可用。
- `--strict`：验证未通过时返回非零；正式评测必须使用。
- `--skip-cargo`：仅限本地诊断，正式评测不得使用。

## 3. Harness 阶段

Harness 分为 bootstrap 和 validation 两段，每个阶段都会向 `result/harness/`
和 `logs/trace/` 写入可审计产物。

默认 bootstrap 阶段只执行：

1. `OutputScaffoldStage`：创建 `result/`、`logs/` 和 trace 结构。
2. `ConstraintLoadingStage`：加载通用 Rust 设计规则和可选 profile 覆盖项。
3. `ProjectAnalysisStage`：扫描 C 源码并生成 derived/effective profile。
4. `SkeletonGenerationStage`：准备 Cargo crate 外壳；不写 Rust 实现。
5. `ContextBuilderStage`：生成按模块拆分的上下文 shard 和 compact 索引。
6. `ParityMatrixStage`：生成公共 API 与源码模块 parity 矩阵。
7. `TranslationStage`：从 `agents/` 固定模板渲染 Main Thread、Code Agent、Test Agent 和 Validation Agent 任务书。

当传入 `--validate` 或 `--strict` 时，继续执行：

8. `CompileStage`：执行 `cargo check` 并记录诊断。
9. `RepairStage`：整理编译结果和修复判断。
10. `ValidationStage`：检查结构、API parity、测试覆盖、benchmark 覆盖、`unsafe`
    使用，并在 Cargo 可用时执行 `cargo test`。

`profile_harness.py` 会把计划阶段、每个 HarnessStage 的开始/完成、源码扫描、
动态 profile 推导、上下文索引、parity 矩阵、分工任务书和 profile 验证写入
`logs/trace/profile-harness-path.json` 与 `logs/trace/profile-harness-path.md`。
bootstrap 阶段的 `planned_stages` 应只包含前 7 个 HarnessStage；严格验证阶段
的 `planned_stages` 应包含 10 个 HarnessStage。判断模型是否按序执行时，以
`stage_start` / `stage_complete` 的 `step` 和 `stage_name` 顺序为准。

代码分层：

```text
work/harness/generic_harness.py      # 通用上下文、HarnessStage 基类、约束加载、cargo 调度
work/harness/profile_generator.py    # 从 C 工程动态生成 profile
work/harness/profile_harness.py      # 源码分析、上下文、parity、translation brief 和 validation stages
work/harness/model_artifacts.py      # scaffold、分工任务书和报告生成，不写 Rust 实现
work/run_conversion.py               # 通用入口：--source + 可选 --profile
work/profiles/flashdb.md             # FlashDB 可选覆盖项，不承载测试/API/benchmark 清单
work/specs/rust_design_rules.md      # 项目无关 Rust 设计规则
```

## 4. FlashDB 覆盖要求

当 `--source` 指向 FlashDB 时，harness 必须自动发现并要求覆盖：

- `tests/fdb_kvdb_tc.c` 中 13 个 KVDB `TEST_RUN(...)` 用例。
- `tests/fdb_tsdb_tc.c` 中 11 个 TSDB `TEST_RUN(...)` 用例。
- `tests/README_test.md` 中列出的全部单元测试。
- `tests/benchmark/bench_main.c` 主流程调用的 11 个 benchmark 操作。

Test Agent 必须以 `result/harness/test-requirements/manifest.json` 指向的
target manifest 中的 `required_rust_tests` 为准生成 Rust 测试。
benchmark 测试应断言操作数量、结果字段和最终状态合理，不依赖固定墙钟耗时阈值。

## 5. 完成判定

正式评测采用机器判定：

- 执行 `python3 work/run_conversion.py --source ... --out flashDB_rust --result result --logs logs --strict`。
- 退出码 `0` 表示自验证通过。
- 非零退出码表示失败且未完成，详细机器结果见 `result/harness/07-validation.json`；
  下一轮修复入口是 `result/harness/08-repair-context/manifest.json`。
- `logs/interaction.md` 必须存在；全程无人工干预时保持为空文件。

完成后根目录应生成：

```text
flashDB_rust/
logs/
logs/interaction.md
logs/trace/
logs/trace/profile-harness-path.json
logs/trace/profile-harness-path.md
result/output.md
result/issues/00-summary.md
result/harness/
```

手动复验：

```bash
cd flashDB_rust
cargo build
cargo test
```
