---
name: agent-infra
description: >
  Agent基础设施运维总入口：统一大模型网关调度与各编程Agent额度用量对账。当用户需要管理本地模型网关（渠道增删/测速/冷却复位/权重调优）或查询多Agent账户余额与用量配额（Grok/Codex/Cursor/ZCode）时使用。网关运维路由至 unified-gateway，额度对账路由至 agent-balances。Use when managing agent infrastructure, model gateways, or querying agent balance quotas.
metadata:
  version: "1.0"
  family: agent-infra
  role: entry
  load-mode: auto
---

# agent-infra — Agent 基础设施运维总入口

本技能为 **Agent-Infra（Agent 基础设施）** 族群的总入口技能（entry），采用自动加载模式（`load-mode: auto`），常驻各编程 Agent 启动上下文。
负责统揽本机大模型网关路由调度、性能测速诊断，以及各主流编程 Agent 的账户额度与用量对账。

---

## 1. 族内能力矩阵与路由分流

当用户提出 Agent 基础设施、网关配置或用量查询诉求时，入口按如下路由表调度至对应成员技能（member）：

| 业务领域 | 核心场景与触发词 | 目标成员技能 | 加载与触发机制 |
|---|---|---|---|
| **统一模型网关运维** | 渠道增删/配置修改、上游协议切换（chat/responses）、流式性能测速（TTFT/TPS）、冷却熔断排查与即时复位、权重调整、Grok 模型发现同步 | [`unified-gateway`](../unified-gateway/SKILL.md) | `manual` 成员<br>`/unified-gateway` 显式调用或经本入口分流 |
| **Agent 额度与用量对账** | 查询 Grok、Codex、Cursor、Claude、Kimi、ZCode 额度余额、周/月周期消耗对账、导出机器可读 JSON 给监控系统（如 monitor-panel） | [`agent-balances`](../agent-balances/SKILL.md) | `manual` 成员<br>`/agent-balances` 显式调用或经本入口分流 |

---

## 2. 核心系统拓扑与同源性

```
                           [用户与各 Agent 交互界面]
                                     │
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
       [模型转发层: unified-gateway]     [用量账本层: agent-balances]
                     │                               │
        127.0.0.1:8320 (本地网关)        直接抓取多 Provider 认证端点
                     │                               │
         上游模型提供商接入矩阵              Grok / Codex / Cursor / Claude 等
   (端砚 / 智谱 / StepFun / XLF 等)         (数据与 Orca 状态栏及计费中心同源)
```

---

## 3. 成员技能规约与使用指引

### 3.1 统一模型网关运维 (`unified-gateway`)
- **适用场景**：
  - 新增上游渠道或更换 API Key（热重载 `config.json`，无需重启守护进程）。
  - 测试新模型响应延迟：测量首字时间（TTFT）、流式解码速率（TPS）及思考耗时占比。
  - 遇到 429 或 502 时排查熔断根因：区分上游集群全局 RPM 限流（Cluster RPM）与个人额度耗尽（insufficient_quota）。
  - 刷新渠道状态：在额度恢复后平稳重启网关以清空内存累积冷却。
  - 同步新模型至 Grok 客户端配置（`~/.grok/config.toml`）。
- **快速执行**：详细规程见 [`unified-gateway/SKILL.md`](../unified-gateway/SKILL.md)。

### 3.2 Agent 额度与用量对账 (`agent-balances`)
- **适用场景**：
  - 终端快速输出全平台额度表格：`uv run python agent-balances/scripts/cli.py`。
  - 导出 JSON 结构化数据接入全机监控（`--json` 或 `--output`）。
  - 单平台故障隔离排查：支持 `--provider grok,codex --raw` 诊断单家 Token 状态。
- **快速执行**：详细规程见 [`agent-balances/SKILL.md`](../agent-balances/SKILL.md)。

---

## 4. 协同规范

1. **入口轻量，成员内聚**：
   - 本入口技能负责意图识别与导航分流，不直接编写长篇网关脚本或抓取代码。
   - 深入的网关测试脚本与 API 逆向规范分别收敛在各成员技能内部。
2. **单一事实源**：
   - 网关拓扑以 `~/.local/share/model-gateway/config.json` 为准。
   - 额度查询逻辑以各 CLI 本地 Session/Token 及官方用量端点为准。
