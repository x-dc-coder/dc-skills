---
name: orca-cli
description: >-
  Operate Orca-managed worktrees, folder contexts, terminals, repos, automations, artifacts,
  skill sharing, worktree comments, and Orca's embedded browser through the `orca` CLI. Use
  when the user says "$orca-cli", "Orca worktree", "child worktree", "spawn codex/claude in a
  worktree", "read/wait/send Orca terminal", "handoff" / "handover" / "give this to another
  agent", "Orca browser", "orca artifacts", or "share skills". Prefer it over raw git
  worktree, ad hoc PTYs, or Computer Use when Orca state is involved. Use Computer Use only
  when a visible window needs GUI control that a CLI, filesystem, or API cannot do.
  Orca 生态的多 agent 编排（orchestration）、跨会话任务中心（task-hub）、可见窗口 GUI 驱动（computer-use）、本机 NewAPI 网关运维（newapi-management）同属 agentops 族，由本技能路由。
metadata:
  family: agentops
  role: entry
  load-mode: auto
---
# Orca CLI

## 族群路由（agentops Agent 运维入口）

本技能是 **agentops Agent 运维族群入口**。族内技能为 manual 加载（不进启动清单），命中下表场景时直接 Read 对应 SKILL.md 后按其规程执行；用户显式要求加载整族时依次读本表全部条目。

| 技能 | 用途 | 何时选它 |
|---|---|---|
| [`orchestration`](~/projects/dc-skills/orchestration/SKILL.md) | Orca worker 编排 | 多 worker 监督/线程消息/任务分发/DAG |
| [`task-hub`](~/projects/dc-skills/task-hub/SKILL.md) | 跨会话任务中心 | 任务新建/编辑/查询/推进/报表看板 |
| [`computer-use`](~/projects/dc-skills/computer-use/SKILL.md) | GUI 驱动（orca computer） | 可见窗口需要 GUI 控制且 CLI/文件/API 无法替代时 |
| [`newapi-management`](~/projects/dc-skills/newapi-management/SKILL.md) | 本机 NewAPI 网关渠道/日志运维 | 配置 NewAPI 渠道/排查会话日志（模型链路运维） |


This discovery stub loads the version-matched guide from the Orca executable used for this session.

## Resolve the CLI for this session

Choose the executable once and reuse it for every later command:

- If the `ORCA_CLI_COMMAND` environment variable is set, use its value. Orca exports this
  for managed WSL sessions.
- Otherwise, in a dev checkout whose session exposes `ORCA_DEV_REPO_ROOT`, use `orca-dev`.
- Otherwise, on Linux outside an Orca-managed terminal, use `orca-ide`. Never run bare
  `orca` there — outside Orca's terminals it normally resolves to the
  GNOME Orca screen reader (`/usr/bin/orca`) and starts speech on the user's machine.
- Otherwise, use `orca`.

Below, `ORCA` is a placeholder for the executable you resolved. Substitute it before
running anything; do not create a shell variable or run `ORCA` literally. This works the
same way in POSIX shells, PowerShell, and cmd.exe.

If the selected executable cannot run, report its exact error and stop. Do not fall through
to another executable, which could silently target a different Orca build.

## Load the version-matched guide before running Orca commands

```text
ORCA skills get orca-cli
```

Prefer `--json`. Use the selected executable's `--help` for commands or flags the guide does
not cover. If a command reports that Orca is not running, start it with `ORCA open --json`
and retry. If `skills get` is unknown, explain that updating Orca restores the guide; use
`--help` for read-only discovery and do not guess unsupported commands.
