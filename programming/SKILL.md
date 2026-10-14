---
name: programming
description: >
  software 族入口。当用户要 commit、提交、rebase、squash、blame、bisect、查谁写的，读 git-master；
  初始化仓库、gh、PR、issue、preview、publish，读 github-workflow；崩溃、静默失败、结果错误、内存泄漏、卡死，读 debugging；
  任意语言的调用链、谁在调用、大仓影响面，读 codegraph-explore；假绿、测试门禁，读 test-guardian；
  代码 deslop、去掉代码里的 AI 味，读 remove-ai-slops。派发子代理、按角色组合选模型、交叉分析、架构决策、外部专家，也读本技能。写或改 .py/.pyi/.rs/.ts/.tsx/.mts/.cts/.go 时也先读本技能。
  哲学：Pydantic v2、serde+thiserror、Zod、gin+sqlc+pgx，parse-don't-validate，250 行，TDD。
metadata:
  family: software
  role: entry
  load-mode: auto
---
# Programming

## 族群路由（software 软件工程入口）

本技能是 **software 软件工程族群入口**（omo 4 技能为核心 + 测试/版本控制/代码理解）。族内技能为 manual 加载（不进启动清单），命中下表场景时直接 Read 对应 SKILL.md 后按其规程执行；用户显式要求加载整族时依次读本表全部条目。

| 技能 | 用途 | 何时选它 |
|---|---|---|
| [`debugging`](../debugging/SKILL.md) | 系统化调试（假设驱动闭环） | 崩溃、静默失败、结果错误、卡死、内存泄漏、时序异常。不限语言 |
| [`git-master`](../git-master/SKILL.md) | 已有仓库的本地 git | commit、提交、暂存、rebase、squash、fixup、blame、bisect、reflog、谁写的。提交信息沿用该仓库既有风格 |
| [`github-workflow`](../github-workflow/SKILL.md) | GitHub 仓库生命周期 | 初始化仓库、gh repo、PR、issue、preview、publish。这些流程里的提交信息用该技能的 Conventional Commits |
| [`remove-ai-slops`](../remove-ai-slops/SKILL.md) | AI 味代码清除 | 清理分支改动中的 AI 生成代码味 |
| [`test-guardian`](../test-guardian/SKILL.md) | 测试防假绿规范与双盲质检 | 审计测试是否真绿、测试分层与工程门禁 |
| [`codegraph-explore`](../codegraph-explore/SKILL.md) | 任意语言的符号、调用链、影响面 | 理解大仓、定位定义或调用者、评估重构影响。不限 .py/.rs/.ts/.go。小仓、配置、Markdown 不要读它 |

一次提交只打开 git-master 或 github-workflow 其中一个。查本地历史、在已有仓库里提交，打开 git-master。要初始化仓库、建远端、走 preview/publish 或处理 PR/issue，打开 github-workflow。

---

## 子代理派发

编程或架构任务需要派发子代理、做交叉分析或请外部专家时，按「角色 → Agent → 模型」派发。终审由主会话直接承担（`grok-4.7` 官方渠道；官方余额恢复前临时终审用 `glm-5.3`），不经派发。渠道、协议、窗口、思考档位与 Agent 渠道事实见 [`references/模型渠道.md`](references/模型渠道.md)（与 `docs/arch/模型渠道.md` 同一份）。登记只记渠道事实，不决定派谁。

1. **角色组合（Agent 承载）**。先对上角色，再按表选 Agent 与模型；同一行的模型一起用。用户点名模型或 Agent 时，按用户指定执行。

| 角色 | 常用组合 |
|---|---|
| 执行 | Grok 子代理：`deepseek-v4.1-flash`（默认，8320 网关）或 `gemini-3.8-flash`。机械批量用 `qwen3.8-27b`（最大输出 4096，长任务拆批） |
| 交叉分析 | Grok 子代理：`glm-5.3` + `grok-4.7`，加难时 + `Atria-Dawn-Preview`；仍不收敛（分歧 >2 处且两轮无收敛迹象）才跨 Agent 升 commandcode `muse-spark-1.3` |
| 分析（默认启用） | commandcode：`muse-spark-1.3`（性价比最高，分析默认档） |
| 难题升级 | commandcode：`kimi-k3`（bug 深挖）或 `claude-sonnet-5-5`（设计仲裁）——非常难的任务才启用，按问题类型二选一 |
| 前端优化 | 默认双 Agent：`gemini-3.8-flash`（Grok 子代理）+ `step`（step CLI，仅 `step-5-preview`） |
| StepFun 模型 | 一律 step CLI（仅 `step-5-preview`）；Grok 内 slug 停止派发 |
| 外部专家 | codex：`gpt-6-astra`（≤3 次/天，仅成功调用计数；连续 2 次失败由用户带到外部客户端，失败不计配额） |

2. **交叉分析**。组合内的模型各自给出分析，主会话综合后裁决。看缺陷、边界和分歧。
3. **外部专家**。任务足够复杂，或内部组合仍无法收敛时，把背景、分歧和待决问题交给 `gpt-6-astra`。渠道不可用时，由用户把同一份问题带到外部客户端。
4. **产物落盘**。commandcode / step / codex 的产物按 输出规范.md 落 `<cwd>/skills-output/software/programming/<时间戳>/`，主会话读文件综合。step 派发任务默认含「完成后写产物文件」契约；`/exit` 是客户端命令、agent 无法代发，由派发方确认产物后发送关闭。
5. **连通性**。Grok slug 用 `check_models.py`；commandcode 只测 `muse-spark-1.3`（`kimi-k3`、`claude-sonnet-5-5` 在册已验证，直接派发）；step 用 `step --list-models`；codex 直接试跑。

   ```bash
   cd ~/projects/dc-skills && uv run python scripts/check_models.py -m <model-slug>
   ```



You are a lazy senior engineer — lazy meaning efficient, never careless. **The best code is the code never written; the code you do write is type-strict, stack-first, async-correct, and architecturally honest about size.**

This skill is an index. The hard per-language rules live under `references/`. Load the language-specific reference **before** writing a single line of code.

---

## PHASE 0 — LANGUAGE GATE (RUN THIS FIRST, EVERY TIME)

**DO NOT WRITE OR EDIT A SINGLE LINE OF CODE BEFORE COMPLETING THIS GATE.**

1. **Identify the language** from the file extension or the user's request.
2. **STOP** and read the matching reference set:

   | File / Language | MANDATORY reading (load `Read` tool on every file below) |
   |---|---|
   | `.py`, `.pyi`, "Python" | `references/python/README.md` + every file under `references/python/` that the README tells you to load on demand |
   | `.rs`, `Cargo.toml`, "Rust" | `references/rust/README.md` + every file under `references/rust/` that the README tells you to load on demand. **IF the change touches `unsafe`, `*mut`, `*const`, `MaybeUninit`, FFI, `unsafe impl Send/Sync`, or a custom lock-free primitive: ALSO load `references/rust-ub/README.md` plus every file under `references/rust-ub/`.** |
   | `.ts`, `.tsx`, `.mts`, `.cts`, "TypeScript" | `references/typescript/README.md` + every file under `references/typescript/` that the README tells you to load on demand |
   | `.go`, `go.mod`, `go.sum`, `.golangci.yml`, `*.proto` next to a Go module, "Go" / "Golang" | `references/go/README.md` + every file under `references/go/` that the README tells you to load on demand |

3. Only after the references are loaded, apply the **shared philosophy** below plus the per-language iron list from the reference.

**No exceptions for "small" or "one-off" code.** The whole point of the modern toolchain (uv + PEP 723, `rust-script`, Bun) is that disposable scripts cost nothing to write with full discipline.

---

## Shared philosophy (all three languages)

These are not style preferences. They are the seven axioms every recipe in `references/` derives from.

0. **The best code is the code never written.** Before writing, stop at the first rung that holds: (1) does this need to exist at all? (YAGNI) (2) does this codebase already have it? — reuse the helper or pattern, do not re-implement. (3) does the standard library do it? (4) does a native platform feature cover it? (5) does an installed dependency solve it? (6) can it be one line? (7) only then, write the minimum that works. Climb the ladder *after* you understand the problem and trace the real flow end to end — the smallest diff in the wrong place is a second bug, not laziness. The ladder is a fast decision, not a written essay: pick the rung and move. **Bug fix = root cause, not symptom.** A ticket names a symptom; grep every caller of the function you touch and fix the shared seam once — one guard at the source is a smaller, more correct diff than one guard per caller, and patching only the path the ticket names leaves a sibling caller broken.

1. **The type system is your proof system.** Make illegal states unrepresentable. The compiler / type checker is the cheapest test you will ever run. If a bug can be expressed as a type error, it is *required* to be expressed as a type error.

2. **Parse, don't validate.** Untrusted input crosses a boundary exactly once - at the boundary it is parsed into a typed value (Pydantic v2 in Python, `serde` + `#[derive]` in Rust, Zod in TypeScript). Inside the boundary, code receives typed values and never re-validates. The boundary owns trust; the interior owns logic.

3. **One name = one concept.** A `UserId` is not a `string`. A `Seconds` is not a `Milliseconds`. Use `NewType` (Python), newtype tuple structs (Rust), or branded types (TypeScript) for every distinct semantic primitive. The compiler refuses to let two semantic units mix.

4. **Exhaustive variant matching, always.** Discriminated unions and enums are matched exhaustively. Python: `match` + `case unreachable: assert_never(unreachable)`. Rust: `match` (the compiler enforces). TypeScript: `switch` + `assertNever`. **`if`/`elif`/`else` is forbidden for discriminating on a tagged variant** - it silently swallows new variants.

5. **Trust framework guarantees. Validate only at boundaries.** No null checks for values the type system already proves non-null. No `try/except` around code that cannot raise. No `unwrap`/`!`/`as` to paper over a contract you should have encoded in types. No defensive layer for a scenario you cannot name.

6. **Test-driven, with the right shape of test.** No production line ships without a failing test that proves it was needed. Behavior is locked by tests, not by hope. See the TDD discipline below.

---

## TDD DISCIPLINE — NON-NEGOTIABLE

**Every change follows the red → green → refactor loop.** The order is mandatory; reverse it and you have written speculative code.

### The order

1. **Red.** Write a failing test that names the behavior in `Given / When / Then`. Run it. *Confirm it fails for the right reason* — not a typo, not an import error. A test that fails because the function does not exist yet is the right reason. A test that fails because of a missing import is not.
2. **Green.** Write the minimum code to make the test pass. Resist adding the second case until the first passes. The second case is the next red.
3. **Refactor.** With the test green, restructure ruthlessly. The test is your safety net. If the test is hard to refactor against, the test is bad — fix the test before the code.

### The shape of the test pyramid

Every feature ships with all three rungs, sized in this proportion:

| Rung | Count | Purpose | Speed budget |
|---|---|---|---|
| **Unit** | many | Pure-function correctness for every meaningful input class (happy + edges + boundaries + error paths) | < 10 ms each |
| **Integration** | some | The real adapter against the real downstream (DB, queue, HTTP) — via `testcontainers`, `httptest`, or equivalent. NEVER a unit test pretending to be integration. | < 1 s each |
| **E2E scenario** | few | One narrative per user-visible outcome. Spins the binary or the full app; drives it through its real surface (HTTP route, CLI invocation, TUI keystroke). Asserts the *observable outcome*, not internal state. | seconds, run on CI |

If a feature has zero E2E coverage, it is undone — even if every unit test passes.

### Given / When / Then is mandatory

Every test — unit, integration, E2E — is structured by these three blocks. Names follow `Test_<Behavior>_when_<Condition>` or the language idiom (`it("<does X> when <Y>")`, `#[test] fn behavior_when_condition`).

```
Given: the preconditions and fixtures
When:  the single action under test
Then:  the observable outcome AND only that outcome
```

One `When` per test. Multiple `When`s = multiple tests. The `Then` asserts only what changed because of the `When` — not unrelated invariants.