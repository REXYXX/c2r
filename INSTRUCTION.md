# C to Rust 动态转换框架执行说明

## 0. 执行协议

执行型编码模型必须先运行通用 harness，让框架根据 `--source` 动态生成
`MODEL_TASK.md`、`01-effective-profile.md`、`03-context.json`、
`04-function-parity.json` 和 `07-validation.json`，再按这些产物编写或修复 Rust。

本地开发示例：

```bash
python3 work/run_conversion.py \
  --source /mnt/d/c2rust/c2rust/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

评测环境示例：

```bash
python3 work/run_conversion.py \
  --source /app/code/judge-assets/02_02_c_to_rust/code/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs
```

首次运行失败是正常的，因为 Rust crate 还没有被模型实现。首次运行的目的
是生成动态 profile、模型任务书和验证缺口。实现或修复完成后运行严格入口：

```bash
python3 work/run_conversion.py \
  --source /mnt/d/c2rust/c2rust/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

如果需要保留 FlashDB 的历史输出目录命名，也可以增加可选覆盖 profile：

```bash
python3 work/run_conversion.py \
  --profile work/profiles/flashdb.md \
  --source /mnt/d/c2rust/c2rust/FlashDB \
  --out flashDB_rust \
  --result result \
  --logs logs \
  --strict
```

禁止事项：

- 禁止在生成 `MODEL_TASK.md` 和 `04-function-parity.json` 之前直接开始翻译。
- 禁止只按 README 或个人理解手写测试清单，必须按 harness 的动态 coverage 矩阵覆盖。
- 禁止跳过自动发现出的 benchmark 测试。
- 禁止把 Rust 源码硬编码到 Python 文件中。
- 禁止用 `--skip-cargo` 作为最终验证。

## 1. 设计原则

本工程提供源码驱动的通用 Agent Harness，而不是 FlashDB 专属转换脚本。
Python 负责调度、trace、命令执行、源码实时分析、动态 profile 生成、模型任务书
和通用验证；Rust 业务实现必须由模型基于源码和生成产物编写。

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
```

## 2. 参数说明

- `--source`：源 C 项目目录；动态 profile 的主要输入。
- `--profile`：可选 markdown 覆盖 profile；未提供时完全从 `--source` 生成。
- `--out`：生成 Rust 项目的目录；默认由动态 profile 推导。
- `--result`：报告和 harness 阶段产物目录，默认 `result`。
- `--logs`：交互记录和 trace 日志目录，默认 `logs`。
- `--cargo`：Cargo 可执行文件名，默认 `cargo`。
- `--strict`：验证未通过时返回非零；正式评测必须使用。
- `--skip-cargo`：仅限本地诊断，正式评测不得使用。

## 3. Harness 阶段

Harness 按以下阶段执行，每个阶段都会向 `result/harness/` 写入可审计产物：

1. `OutputScaffoldAgent`：创建 `result/`、`logs/` 和 trace 结构。
2. `ConstraintLoadingAgent`：加载通用 Rust 设计规则和可选 profile 覆盖项。
3. `ProjectAnalysisAgent`：扫描 C 源码并生成 derived/effective profile。
4. `SkeletonGenerationAgent`：准备 Cargo crate 外壳；不写 Rust 实现。
5. `ContextBuilderAgent`：生成模块上下文、函数线索和公共 API 索引。
6. `ParityMatrixAgent`：生成公共 API 与源码模块 parity 矩阵。
7. `TranslationAgent`：生成 `MODEL_TASK.md`，指导模型编写 Rust。
8. `CompileAgent`：执行 `cargo check` 并记录诊断。
9. `RepairAgent`：整理编译结果和修复判断。
10. `ValidationAgent`：检查结构、API parity、测试覆盖、benchmark 覆盖、`unsafe`
    使用，并在 Cargo 可用时执行 `cargo test`。

代码分层：

```text
work/harness/generic_harness.py      # 通用上下文、Agent 基类、trace、约束加载、cargo 调度
work/harness/profile_generator.py    # 从 C 工程动态生成 profile
work/harness/profile_harness.py      # 源码分析、上下文、parity、translation brief 和 validation agents
work/harness/model_artifacts.py      # scaffold、MODEL_TASK.md 和报告生成，不写 Rust 实现
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

FlashDB 中重复出现的 `test_fdb_tsl_clean` 必须被动态 profile 映射为两个独立
Rust 测试名，避免后一个覆盖前一个。

最终模型必须以 `MODEL_TASK.md` 中的 `required_rust_tests` 为准生成 Rust 测试。
benchmark 测试应断言操作数量、结果字段和最终状态合理，不依赖固定墙钟耗时阈值。

## 5. 完成判定

正式评测采用机器判定：

- 执行 `python3 work/run_conversion.py --source ... --out flashDB_rust --result result --logs logs --strict`。
- 退出码 `0` 表示自验证通过。
- 非零退出码表示失败，原因见 `result/harness/07-validation.json` 和
  `result/issues/00-summary.md`。
- `logs/interaction.md` 必须存在；全程无人工干预时保持为空文件。

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

手动复验：

```bash
cd flashDB_rust
cargo build
cargo test
```
