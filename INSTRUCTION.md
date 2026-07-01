# FlashDB C to Rust Agent Harness 执行说明

本提交物提供一个更通用的 Agent Harness，而不是单次硬编码翻译脚本。评测系统只需要执行一个非交互式命令，Harness 会按阶段读取平台提供的 FlashDB C 工程，生成 Rust 项目、迁移测试，并输出每个阶段的可审计产物。

本工程也包含面向 `opencode + GLM5.1` 等较弱模型的约束包。弱模型执行时必须先读取固定 API 合同、Rust 设计规则和 workflow，再进入代码生成阶段，避免模型自由发挥导致接口漂移、测试缺失或生成不可维护代码。

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

在参赛作品根目录执行：

```bash
python3 work/harness/flashdb_harness.py \
  --flashdb /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result
```

参数说明：

- `--flashdb`：平台提供的 FlashDB C 工程目录。
- `--out`：生成 Rust 项目的目录，默认是 `flashDB_rust`。
- `--result`：报告和 Harness 阶段产物目录，默认是 `result`。
- `--cargo`：Cargo 可执行文件名，默认是 `cargo`。
- `--skip-cargo`：可选参数，用于没有 Rust 工具链的环境，仅生成项目和报告，不执行 `cargo check` / `cargo test`。

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
3. `SkeletonGenerationAgent`：生成可编译 Rust crate 骨架，包括 `Cargo.toml` 和 `src/lib.rs`。
4. `ContextBuilderAgent`：为目标模块构建最小上下文，记录 `fdb_kv_`、`fdb_blob_`、`fdb_tsdb_`、`fdb_tsl_` 等符号线索。
5. `TranslationAgent`：生成安全 Rust 实现和 Rust 测试。
6. `CompileAgent`：执行 `cargo check`，收集编译输出。
7. `RepairAgent`：根据编译结果生成修复判定和诊断记录。
8. `ValidationAgent`：检查交付结构、固定 API 符号、`unsafe` 数量，并在 Cargo 可用时执行 `cargo test`。

Agent 描述文件位于：

```text
work/agents/flashdb-c2rust-harness.md
```

弱模型约束文件位于：

```text
work/specs/flashdb_api_contract.md
work/specs/rust_design_rules.md
work/workflows/opencode_glm_flashdb_workflow.md
work/prompts/opencode_glm_system_prompt.md
```

使用 opencode + GLM5.1 时，建议将 `work/prompts/opencode_glm_system_prompt.md` 作为系统提示词，并要求模型严格按 `work/workflows/opencode_glm_flashdb_workflow.md` 分阶段执行。

## 4. 完成判定

命令返回码为 `0` 表示 Harness 执行完成。完成后根目录应生成：

```text
flashDB_rust/
result/output.md
result/issues/00-summary.md
result/harness/
```

进一步判定方式：

- `flashDB_rust/Cargo.toml` 存在。
- `flashDB_rust/src/` 存在 Rust 源码。
- `flashDB_rust/tests/` 存在 Rust 测试。
- `result/harness/07-validation.json` 存在并记录结构检查结果。
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
result/harness/00-constraints.json
result/harness/00-events.json
result/harness/01-analysis.json
result/harness/03-context.json
result/harness/05-compile.json
result/harness/06-repair.json
result/harness/07-validation.json
```

## 6. 实现说明

生成的 Rust 项目使用安全 Rust 实现 FlashDB 核心行为：

- Key-Value 数据库：支持字符串和二进制 blob 的 set/get/update/delete、键迭代、清空和文件持久化。
- Time-Series 数据库：支持追加记录、按时间排序、范围查询、获取最新记录和文件持久化。
- 测试迁移：使用 Rust 原生 `#[test]` 测试覆盖 KV 与 TSDB 的主要场景。
- `unsafe` 使用比例：0%，生成项目不包含 `unsafe` 块。
