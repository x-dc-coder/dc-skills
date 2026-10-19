---
name: agent-balances
description: >
  查询本机各编程 Agent 的额度与用量余额（Grok/Codex/Cursor/ZCode 等，数字与 Orca 状态栏同源）。当用户要查 agent 余额、对账用量、监控额度、或把结果导出 JSON 喂给 monitor-panel 时使用。查余额、查额度、agent usage、quota、balance、各 agent 用量。
metadata:
  family: agent-infra
  version: "0.1"
  role: member
  load-mode: manual
  requires-bins: "node, powershell"
disable-model-invocation: true
---

# Agent Balances

## 能力概述

查询本机各编程 Agent 的额度与用量余额（Grok/Codex/Cursor/ZCode 等，数字与 Orca 状态栏同源）。当用户要查 agent 余额、对账用量、监控额度、或把结果导出 JSON 喂给 monitor-panel 时使用。查余额、查额度、agent usage、quota、balance、各 agent 用量。

## 触发场景

用户说"查一下各 agent 余额/额度/用量"、"grok 这周用了多少"、"codex 会话窗还剩多少"、"对账 agent 用量"、"把 agent 余额采集给 monitor-panel"，或需要一份机器可读的额度快照（JSON）时。本技能为 standalone manual 加载：`/agent-balances` 或 `$agent-balances` 显式触发。

## 依赖说明

- **A 类环境**：轻量 stdlib Python，经主库 `.venv` 软链执行；无第三方包（R7 干净）。
- **条件外部二进制**（仅 ZCode 的 Orca 加密 key 解密路径触发，缺失时该 provider 降级为 unavailable，不影响其余）：
  - `powershell`（Windows DPAPI，从 WSL 经 interop 调用）
  - `node`（AES-256-GCM 解密）
  - 均已登记 [docs/arch/环境依赖.md](../../../docs/arch/环境依赖.md)；换机器时用 `AGENT_BALANCES_WIN_HOME` 覆盖 Windows 配置目录。

## 快速用法

```bash
cd ~/projects/dc-skills

# 全量表格（默认只打印 stdout，不落盘）
uv run python agent-balances/scripts/cli.py

# 机器可读 JSON（接 monitor-panel 用）
uv run python agent-balances/scripts/cli.py --json

# 子集 + 带原始响应（与 Orca 界面逐字段对照时用）
uv run python agent-balances/scripts/cli.py --provider grok,codex --raw

# 落盘产物（输出规范.md C-1/C-4：统一树 + 主库审计副本）
uv run python agent-balances/scripts/cli.py --output
```

## 详细规程

### 数据来源与同源性

本技能**不复用 Orca 接口**（Orca 不开放额度读取：CLI 无 usage 命令、额度不落盘、IPC 仅进程内），而是复刻其抓取逻辑——端点、请求头、字段解析均从 Orca app.asar 逐一逆向提取，输出数字与 Orca 状态栏/用量面板同源。用户对照 Orca 界面时若不一致，先查各家 token 是否刚被 CLI 续期。

### Provider 覆盖矩阵

| Agent | 凭据来源 | 端点 | 窗口语义 |
|---|---|---|---|
| grok | `~/.grok/auth.json`（auth.x.ai 条目） | `cli-chat-proxy.grok.com/v1/billing?format=credits` | weekly=`creditUsagePercent`；monthly=monthlyLimit/used |
| codex | `~/.codex/auth.json`（tokens.access_token/account_id） | `chatgpt.com/backend-api/wham/usage` | primary_window→session(300min)；secondary_window→weekly |
| cursor | `<WIN>/AppData/Roaming/Cursor/User/globalStorage/state.vscdb`（cursorAuth/*） | `cursor.com/api/usage-summary` | plan 月窗 + auto/api 分桶 + onDemand |
| claude | `.claude/.credentials.json`（WSL 或 Windows 侧） | `api.anthropic.com/api/oauth/usage` | five_hour→session；seven_day→weekly |
| zcode | `<WIN>/.orca/zcode-plan-api-key.enc`（DPAPI+AES-GCM 解密）或 `~/.zcode/cli/config.json` | `open.bigmodel.cn`（或 api.z.ai）`/api/monitor/usage/quota/limit` | limits 的 unit×number 映射分钟数；300=session，10080=weekly |
| commandcode | `~/.commandcode/auth.json` 或网关 `config.json` | `api.commandcode.ai/alpha/billing/credits` 等 | fiveHour→session(300min)；weekly→weekly；monthlyCredits→monthly |
| kimi | `~/.kimi-code/credentials/kimi-code.json` | `api.kimi.com/coding/v1/usages` | 原始响应（`--raw` 查看） |
| gemini / minimax / opencode-go / antigravity | 见下方"未接通" | — | — |

未接通的四家：gemini 需 `~/.gemini` OAuth（两步：loadCodeAssist 取 project id 再 retrieveUserQuota，规格见 references/）；minimax/opencode-go 的 key 与 zcode 同链可解密（`.orca/*.enc`，脚本已识别存在性，usage 抓取未接线）；antigravity 未找到本地凭据来源。新增 provider 的扩展流程见 references/。

### 输出约定

- 默认打印表格到 stdout，**不产生文件产物**；`--json` 输出机器可读 JSON。
- `--output`（可带路径或目录）时 JSON 落 `<cwd>/skills-output/agentops/agent-balances/<时间戳>/agent-balances.json`（固定名，C-1/C-9；显式路径优先），并另存主库审计副本（C-4）。
- 单家查询失败（网络/401/未配置）只影响该行，输出带 `status` 与 `error` 字段，退出码恒为 0——采集侧应按 `status` 字段判别，勿以退出码判成功。

### 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| grok/codex/claude `HTTP 401` | 本地 token 过期（grok 6h、codex JWT 数天） | 跑对应 CLI 登录/续期后重查；grok 的跨 WSL/Windows 文件分裂见 memory 主题 grok-token-keepalive |
| cursor `HTTP 401` + tokenExpired | state.vscdb 的 accessToken 过期（Orca 同样只读它，不读 refreshToken） | 在 Cursor IDE 重新登录 |
| `network: ...timeout` | 代理链路劣化（OpenAI 系域名已知高失败率） | 换时段重试或检查 mihomo(127.0.0.1:7899) |
| zcode `DPAPI decrypt failed` | 非 Windows 侧运行或 powershell 缺失 | 在 WSL 且 Windows interop 可用处运行 |
| zcode `AES-GCM decrypt failed` | node 缺失 | 装 node（已登记环境依赖.md） |
| 某家 unavailable | 未配置/未登录 | 属预期；错误字段说明具体缺什么 |
