<!-- DSH 适配修改（本地）：OmO/opencode 方言已改写为 DSH 语义；上游原件见 SOURCE.txt。 -->

# Phase 2 + 3 — Hypothesis Formation & Parallel Investigation

One hypothesis is a hunch. Three hypotheses is a decision. Investigation is how you turn the decision into runtime evidence.

---

## Phase 2 — Hypothesis Formation (Minimum Three)

### Why three, not one

A single hypothesis creates confirmation bias: you'll read runtime state looking for evidence that confirms it and unconsciously discount contradictions. Three hypotheses force you to design queries that *distinguish* between them, which is the only way runtime evidence becomes decisive.

### Generate across orthogonal axes

If your three hypotheses are all variations of "the handler has a bug", you don't actually have three hypotheses. Span the space:

| Axis | Example framing |
|---|---|
| **User-code logic** | "The handler early-returns because condition X is unexpectedly true" |
| **Library/SDK behavior** | "The third-party client swallows the error and returns a stub" |
| **Environment/config** | "The env var is read at module-load time before it gets populated, so it's empty" |
| **Async/timing** | "The promise rejects (or goroutine panics) after the response is already sent" |
| **Silent side-effect** | "An earlier turn mutated shared state that the current turn inherits" |
| **Observability gap** | "The error is raised but suppressed before logging; it only exists as an unawaited rejection / ignored signal" |
| **Binary-level** (when applicable) | "The function we think is running is actually jumped over by a patched thunk / a different version loaded" |
| **Build-vs-runtime** | "The code we're reading is not the code that's running — stale build, wrong symlink, cached wheel, or dist/ ahead of src/" |

### For each hypothesis, write in the journal

1. **Claim** — one sentence.
2. **Distinguishing evidence** — the exact value or state that confirms or refutes it, AND where to read it (file:line, log source, breakpoint location, memory address).
3. **If true, the fix is** — two words. Forces you to think through fix cost before committing to the hunt.

### Collapse rule

If two hypotheses have identical distinguishing evidence, they aren't actually different — collapse them and find a real alternative. If you can't come up with a third distinct hypothesis, you don't understand the system well enough yet. Go read a little more code before investigating.

---

## Phase 3 — Parallel Investigation

Branch depending on what's available.

### Path A: AgentTeams 可用时

当 `agent_teams_*` 工具可用时（DSH AgentTeams），按下面的**动态编排**建一个 debug-squad 小队，把排查按证据来源拆给成员并行推进。只要假设 ≥3 个、且其中任一单独排查超过 10 分钟，这就是默认做法。

**建队步骤（全部在运行时完成；⚠️ 不要改任何配置文件——改配置需要重启宿主，且违反「自带预设只读」红线）：**

1. `agent_teams_create({ name: "debug-squad", description: "<本次缺陷一句话>", approval: "automatic" })` 建队。
2. 用 `agent_teams_add_member` 逐个加入下面 4 个成员，把角色提示写进各自的 `executionPrompt`（无长度上限）。
3. 用 `agent_teams_create_task` 把每个假设建成一个任务（先后关系用 `dependencies`），`assignee` 指到最可能证实/证伪它的成员。
4. 结果通过任务 `output` 与成员消息回传；用 `agent_teams_status` 看全局，`agent_teams_send_message` 定向纠偏。

**四个角色的 executionPrompt：**

- `runtime-inspector` — You are the Runtime State Inspector. 附着到活动进程、打断点、读程序状态（变量/堆/goroutine/栈/寄存器），逐字回报观测值；没看到就说没看到。回报带 file:line 或地址与捕获值。禁止改源码、禁止跑 git；需要插桩语句（breakpoint()/debugger;/dbg!）先问队长。
- `log-archaeologist` — You are the Log Archaeologist. 检索服务日志、stderr、SDK 内部调试输出（DEBUG/RUST_LOG/GODEBUG/…）并对齐时间戳，产出带延迟的事件时间线。标记可疑吞异常：silent catch、被忽略的 rejection、recover 掉的 panic、成功响应里混着失败信号（HTTP 200 空 body、exit 0 但 stdout 有错误）。禁止改源码。
- `repro-engineer` — You are the Reproduction Engineer. 构造最小可靠复现（curl / vitest / pytest / go test / tmux / Playwright / pwntools），必须一次跑通且可被队长直接复制粘贴。记录精确输入、期望输出、实际输出；产物放 /tmp/ 并告知队长。浏览器类缺陷必须用 Playwright CLI，不得用 curl 模拟。
- `trace-correlator` — You are the Trace Correlator. 汇总其他成员发现并交叉关联，构建症状→疑似成因因果链，指出缺失证据，提出下一个最决定性的观测。只基于已捕获证据推理，禁止改源码。若假设在关联后显著发散，立刻报告队长——那是启动 Oracle Triple 的信号。

> 只有**长期固定**的小队才值得落成 AgentTeams profile（写在 `~/.dsh/profiles/web/cordis.patch.yml` 的 `agent-teams` 行里，由用户决定并重启）；一次性排查一律用上面的运行时步骤。

**Assignment rule**: 一个假设 → 一个 `agent_teams_create_task`。把完整假设清单逐个 `agent_teams_send_message` 发给成员（DSH 无广播语义）。

**Lead responsibilities**:
- 维护排查日志（成员不写日志）。
- 批准任何源码改动（含 `debugger;` / `breakpoint()` / `dbg!` 插桩）。
- 把成员回报综合成假设状态。
- 收队：`agent_teams_delete`；只移除个别成员用 `agent_teams_remove_member`。

**Oracle 不进小队编制** —— 它以只读顾问身份单独用于 Phase 4（见 `04-oracle-triple.md`）。

### Path B: Team mode DISABLED

Fan out async explore/deep subagents instead. Same rule: one hypothesis per subagent.

```
subagent(run_in_background=true,  # DSH: 角色 explore 写在 prompt 内
     prompt="[CONTEXT: bug summary + which hypothesis you own + what state to look at]
     Runtime state investigation for hypothesis 1: ...")
subagent(run_in_background=true,  # DSH: 角色 explore 写在 prompt 内
     prompt="Log/timing investigation for hypothesis 2: ...")
subagent(run_in_background=true,  # DSH: 深度要求写在 prompt 内
     prompt="Reproduction minimizer for hypothesis 3: ...")
```

End your response, wait for completion notifications, then synthesize.

---

## Evidence capture discipline (both paths)

For every piece of runtime state captured, record in the journal:

```markdown
### <ISO timestamp> — <what you looked at>
- Source: <file:line | log source | curl command | breakpoint address>
- Value: `<verbatim>`
- Interpretation: <one line — why this matters>
- Refutes/Confirms: H<n>
```

**Verbatim values only. No paraphrasing.**

- `messages.length=0` is evidence.
- "messages seemed empty" is not evidence — it's a memory of an observation, and memory of observations is where debug sessions go to die.

If you find yourself about to paraphrase, stop, go back, and copy the raw value.

---

## Round completion

A "round" is complete when every hypothesis has either confirming or refuting evidence — or when you have exhausted the evidence sources available without a decisive result. If the round ends inconclusively, that counts as a failed round for the counter in the journal. See `04-oracle-triple.md` for what to do at 2 consecutive failed rounds.
