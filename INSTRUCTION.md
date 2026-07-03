# FlashDB C to Rust Agent Harness 执行说明

## 0. 执行型模型协议

执行型编码模型必须先读取并遵循 `work/workflows/flashdb_conversion_workflow.md`。不要把任务简化为“读取说明、浏览源码、直接翻译、验证”；具体阶段顺序、必读文件、实现边界和失败策略以 workflow 为准。

本地/WSL 开发时，首先运行：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /mnt/d/c2rust/c2rust/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

如果 `/mnt/d/c2rust/c2rust/FlashDB` 不存在，则使用平台路径：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

首次 harness 运行失败是正常的，因为 Rust crate 还没有被模型实现。此失败不是最终交付结果，而是生成 `MODEL_TASK.md` 和 validation 缺口清单的 bootstrap 步骤。

实现和修复完成后，必须运行正式严格入口：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /mnt/d/c2rust/c2rust/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

正式评测环境中如果没有 `/mnt/d/...` 路径，则改用 `/app/code/judge-assets/02_02_c_to_rust/code/FlashDB`。最终只有严格入口返回 `0` 且 `result/harness/07-validation.json` 为 `passed` 才算完成。

禁止事项：

- 禁止在生成 `MODEL_TASK.md` 和 `04-function-parity.json` 之前直接开始翻译。
- 禁止只按 README 或个人理解手写测试清单，必须按 profile/harness 的 coverage 矩阵覆盖。
- 禁止跳过 `tests/benchmark_tests.rs`。
- 禁止把 Rust 源码硬编码到 Python 文件中。
- 禁止用 `--skip-cargo` 作为最终验证。

本提交物提供一个 profile 驱动的通用 Agent Harness，而不是单次硬编码翻译脚本。评测系统只需要执行一个非交互式命令，Harness 会按阶段读取 C 工程、生成 Rust 项目任务书、迁移测试，并输出每个阶段的可审计产物。通用执行入口位于 `work/run_conversion.py`；通用 Agent 位于 `work/harness/profile_harness.py`；通用上下文、trace、Cargo 调度位于 `work/harness/generic_harness.py`；FlashDB 的 API 合同、one-to-one 特性矩阵、行为捷径拦截规则和上下文提示位于 `work/profiles/flashdb.md`。

当前 Rust 生成目标是 **结构忠实迁移**，不是只覆盖测试行为的精简重写。生成项目必须保留 FlashDB 原工程的主要逻辑边界：配置/类型/状态、blob、公共 DB core、file port、low-level helper、sector/cache 元数据、KVDB、TSDB 等模块。

当前严格目标进一步升级为 **FlashDB 一比一逻辑复刻**：生成框架必须约束模型迁移原 C 工程的 sector 文件布局、status table、CRC32、KV node/header、KV GC/recovery、默认 KV、TSDB log index/data、rollover、`max_len`、单调时间检查和 callback iteration。`BTreeMap`/`Vec` 只能作为辅助索引，不能作为主存储逻辑；单个自定义 `flashdb.dat` 不能作为唯一持久化格式。

本工程也包含面向执行型模型和较弱模型的约束包。模型执行时必须先读取 profile 中的项目 API/1:1 矩阵、通用 Rust 设计规则和 workflow，再进入代码生成阶段，避免自由发挥导致接口漂移、测试缺失或生成不可维护代码。后续接入其他 C 项目时，应新增对应 profile 和约束文档，把项目约束放在 markdown profile 或 agent 文档中，保持 Python 层只负责调度、文件产物、trace、命令执行、通用检查和模型任务说明，不在 Python 中预写 Rust 实现。

## 1. 环境准备

需要以下命令可用：

- `python3`：运行 Agent Harness。
- Rust toolchain：包含 `cargo`、`rustc`，用于构建和测试生成的 Rust 项目。

平台提供的 FlashDB 原始工程路径为：

```bash
/app/code/judge-assets/02_02_c_to_rust/code/FlashDB
```

本提交物不会修改平台提供的原始 FlashDB 材料，只读取其中的 `src/`、`tests/`、`inc/` 或 `include/` 文件。

## 2. 执行方式

正式评测时，在参赛作品根目录只执行这个非交互式入口：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

该入口固定启用严格验证：只有结构检查、API 符号检查、C API parity 检查、FlashDB 一比一特性矩阵检查、行为模型捷径拦截、FlashDB `tests` 全量翻译覆盖检查、`unsafe` 检查和 `cargo test` 全部通过时才返回 `0`。验证失败时返回非零，并在 `result/` 和 `logs/` 中保留诊断产物，不需要人工补充交付说明。

开发调试时也可以直接调用通用 Harness：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

参数说明：

- `--profile`：markdown profile，FlashDB 使用 `work/profiles/flashdb.md`。
- `--source`：平台提供的 C 工程目录。
- `--out`：生成 Rust 项目的目录，默认是 `flashDB_rust`。
- `--result`：报告和 Harness 阶段产物目录，默认是 `result`。
- `--logs`：交互记录和工程 trace 日志目录，默认是 `logs`。
- `--cargo`：Cargo 可执行文件名，默认是 `cargo`。
- `--strict`：严格模式，验证未通过时返回非零；正式评测必须使用。
- `--skip-cargo`：仅限本地诊断使用；正式评测不得使用，因为全量测试必须执行。

生成完成后，可在 Rust 项目目录手动复验：

```bash
cd flashDB_rust
cargo build
cargo test
```

## 3. Agent Harness 工作流

Harness 按以下阶段执行，每个阶段都会向 `result/harness/` 写入结构化产物：

1. `ConstraintLoadingAgent`：读取弱模型约束文档，输出 `00-constraints.json` 和 `00-constraints.md`。
2. `ProjectAnalysisAgent`：分析 C 工程，扫描源码、测试、头文件，并按 KVDB、TSDB、port/platform 归类。
3. `SkeletonGenerationAgent`：生成 Rust crate 工作区和 `Cargo.toml`；`src/*.rs` 与 `tests/*.rs` 必须由模型按 profile 编写。
4. `ContextBuilderAgent`：为目标模块构建最小上下文，记录 profile 中声明的符号线索。
5. `ParityMatrixAgent`：生成 `04-function-parity.json`，要求 profile 声明的公共 API 和关键内部机制都有 Rust 映射。
6. `TranslationAgent`：生成模型任务说明和 parity 矩阵，引导模型在 `flashDB_rust/` 中编写一比一复刻 FlashDB 存储引擎语义的安全 Rust 实现，并按 `FlashDB/tests` 下 `TEST_RUN(...)` 清单、`README_test.md` 单元测试清单和 benchmark 操作清单生成全量 Rust 测试。
7. `CompileAgent`：执行 `cargo check`，收集编译输出。
8. `RepairAgent`：根据编译结果生成修复判定和诊断记录。
9. `ValidationAgent`：检查交付结构、固定 API 符号、C API parity、一比一特性矩阵、行为模型捷径、全量测试翻译覆盖、`unsafe` 数量，并在 Cargo 可用时执行 `cargo test`。

Agent 描述文件位于：

```text
work/agents/flashdb-c2rust-harness.md
```

Harness 代码分层如下：

```text
work/harness/generic_harness.py     # 通用上下文、Agent 基类、trace、约束加载、cargo 调度和通用检查
work/harness/profile_harness.py     # profile 驱动的源码分析、上下文、parity、translation brief 和 validation agents
work/harness/model_artifacts.py     # 通用 scaffold、MODEL_TASK.md 和报告生成，不写 Rust 实现
work/run_conversion.py              # 通用入口：--profile + --source
work/profiles/flashdb.md            # FlashDB 专属约束、API token、parity token、捷径拦截和上下文提示
work/harness/flashdb_harness.py     # FlashDB 兼容入口，仅加载 profile 并调用通用 harness
```

维护原则：通用流程问题优先改 `generic_harness.py` 或 `profile_harness.py`；项目约束、评测 token、弱模型提示和 parity 矩阵优先改对应 `work/profiles/*.md` 或 agent 文档。FlashDB wrapper 只做兼容参数解析，不承载 FlashDB 分析、验证或 Rust 生成规则。Python 只解析 markdown 中的 `json harness-profile` 结构化块，不承载 FlashDB 规则本身，也不承载预写好的 Rust 业务实现。

弱模型约束文件位于：

```text
work/profiles/flashdb.md
work/specs/rust_design_rules.md
work/workflows/flashdb_conversion_workflow.md
```

使用执行型模型时，要求模型严格按 `work/workflows/flashdb_conversion_workflow.md` 分阶段执行；该 workflow 已合并弱模型执行约束，不再需要单独的 prompt 文件。

## 4. 完成判定

正式评测采用机器判定，不需要人工交付：

- 执行 `python3 work/run_conversion.py --profile work/profiles/flashdb.md --source ... --strict`。
- 退出码 `0` 表示作品自验证通过。
- 非零退出码表示失败，失败原因见 `result/harness/07-validation.json` 和 `result/issues/00-summary.md`。
- `logs/interaction.md` 必须存在；全程无人工干预时该文件保持为空。

完成后根目录应生成：

```text
flashDB_rust/
logs/
logs/interaction.md
logs/trace/
result/output.md
result/issues/00-summary.md
result/harness/
```

进一步判定方式：

- `flashDB_rust/Cargo.toml` 存在。
- `flashDB_rust/src/` 存在 Rust 源码，且不能压缩为只有 `kvdb.rs` / `tsdb.rs` 的两文件实现。
- `flashDB_rust/src/config.rs`、`types.rs`、`status.rs`、`blob.rs`、`db.rs`、`file.rs`、`low_level.rs`、`sector.rs`、`cache.rs`、`kvdb.rs`、`tsdb.rs` 必须全部存在。
- `flashDB_rust/tests/` 存在 Rust 测试。
- `logs/interaction.md` 存在，记录选手和作品人工交互；若全程无干预，保留为空文件。
- `logs/trace/` 存在，用于存放工程推理/执行 trace 日志。
- `result/output.md` 存在，记录作品成功输出和自验证信息。
- `result/harness/07-validation.json` 存在，并且 `status` 为 `passed`。
- `result/harness/04-function-parity.json` 存在，并覆盖 `flashdb.h` 公共 API 与 FlashDB 核心存储机制。
- `result/harness/07-validation.json` 中 `c_api_parity`、`one_to_one_features`、`behaviour_model_rejection` 均通过。
- 在 `flashDB_rust/` 内执行 `cargo build` 成功。
- 在 `flashDB_rust/` 内执行 `cargo test` 成功。

## 5. 结果获取方式

最终交付的 Rust 重写项目位于：

```text
flashDB_rust/
```

评测系统可从该目录读取：

```text
flashDB_rust/Cargo.toml
flashDB_rust/src/config.rs
flashDB_rust/src/types.rs
flashDB_rust/src/status.rs
flashDB_rust/src/blob.rs
flashDB_rust/src/db.rs
flashDB_rust/src/file.rs
flashDB_rust/src/low_level.rs
flashDB_rust/src/sector.rs
flashDB_rust/src/cache.rs
flashDB_rust/src/lib.rs
flashDB_rust/src/kvdb.rs
flashDB_rust/src/tsdb.rs
flashDB_rust/tests/kvdb_tests.rs
flashDB_rust/tests/tsdb_tests.rs
```

转换、验证和 Harness 报告位于：

```text
result/output.md
result/issues/00-summary.md
logs/interaction.md
logs/trace/events.jsonl
result/harness/00-constraints.json
result/harness/00-events.json
result/harness/01-analysis.json
result/harness/03-context.json
result/harness/05-compile.json
result/harness/06-repair.json
result/harness/07-validation.json
```

## 6. 实现说明

生成的 Rust 项目使用安全 Rust 实现 FlashDB 核心行为，并保留原工程逻辑结构：

- 公共结构：保留 `fdb_def.h` / `fdb_low_lvl.h` 中的配置、状态、DB core、sector、cache、blob、低层 helper 等抽象。
- Key-Value 数据库：保留 KV node、KV iterator、KV cache、sector、GC/recovery 状态，并支持字符串和二进制 blob 的 set/get/update/delete、键迭代、清空和文件持久化。
- Time-Series 数据库：保留 TSL node、sector、rollover/control 状态，并支持追加记录、按时间排序、范围查询、获取最新记录和文件持久化。
- 测试迁移：使用 Rust 原生 `#[test]` 测试覆盖 KV 与 TSDB 的主要场景。
- `unsafe` 使用比例：0%，生成项目不包含 `unsafe` 块。
