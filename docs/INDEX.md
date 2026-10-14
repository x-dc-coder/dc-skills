# docs/ — 全局文档索引

> 本文件是全库文档的唯一地图。**新增/重命名/删除任何 `docs/**` 文档或根级规范文档，必须在同一 commit 内更新本索引。**
> "最近更新"列由 `git log -1 --format=%cs -- <path>` 生成，不做人工维护；"事实源"列标记唯一事实源，防止双写。

## 文档总表

| 文档 | 类型 | 职责 | 何时读 | 最近更新 | 事实源 |
|---|---|---|---|---|---|
| [../AGENT.md](../AGENT.md) | 规约 | 三条铁律：执行目录（cd 主库再 uv run）、输出位置（指向 输出规范.md）、单一物理副本。`CLAUDE.md`/`AGENTS.md` 为其兼容软链 | 每次会话开始；Agent 自动加载 | 2026-09-24 | ✅ 执行约束 |
| [specs/技能编写规则.md](specs/技能编写规则.md) | spec | 技能开发/修改的强制规则（A1–F7、B1–B9）与提交前清单 | 新建或改技能行为前 | 2026-09-24 | ✅ 开发规范 |
| [specs/技能管理.md](specs/技能管理.md) | spec | 主库/农场/项目级三层架构、P1–P8 规约、各 Agent 自注册机制、风险台账 | 动 `agent-map.yaml`、排查注册漂移时 | 2026-09-24 | ✅ 管理架构 |
| [specs/全能力农场重构四阶段路线图.md](specs/全能力农场重构四阶段路线图.md) | spec | 全机能力农场重构四阶段演进路线图（各阶段范围、交付物、退出门槛与四模型协作工作流） | 执行农场重构、规划能力演进时 | 2026-10-14 | ✅ 现行基准 |
| [specs/全资产治理.md](specs/全资产治理.md) | spec | 全能力资产治理平台规格说明书（三层架构、三桶治理模型、AGENTS.md 指令与三层记忆、agent-map/registry 扩展 Schema、阶段二编译契约与盲区清单） | 开展全资产治理、重构编译工具链、排查三桶对账漂移时 | 2026-10-14 | ✅ 现行基准 |
| [specs/上下文预算与农场优化方案.md](specs/上下文预算与农场优化方案.md) | spec | 启动上下文预算审计与农场优化方案（现状机制、实测构成、P0/P1/P2 分期、待决问题清单；GPT-6 仲裁） | 审查/实施上下文优化、动 load-mode 机制前 | 2026-10-14 | 📝 草案（待审查） |
| [specs/输出规范.md](specs/输出规范.md) | spec | 输出位置规范（C-1 统一产物树：族群/技能名/时间戳、显式 --output、审计副本） | 实现/审计任何文件产出前 | 2026-09-29 | ✅ 输出规范 |
| [arch/环境依赖.md](arch/环境依赖.md) | arch | 外部工具/MCP 依赖登记表 + 版本基线 + 验证命令 | 引入新外部依赖、跨设备验证时 | 2026-09-22 | ✅ 依赖登记 |
| [arch/模型渠道.md](arch/模型渠道.md) | arch | Grok 模型渠道/协议/窗口/思考档位/套餐到期（含 Codex/Claude/DSH 渠道位置索引） | 配模型或排查模型行为时 | 2026-09-24 | ✅ 模型登记 |
| [runbook/跨设备搭建.md](runbook/跨设备搭建.md) | runbook | 新设备从零搭建（Linux/WSL/Windows 双侧） | 新机器部署时 | 2026-09-24 | — |
| [research/canonical-base-design-2026-09-14.md](research/canonical-base-design-2026-09-14.md) | research | 规范基座设计草案（历史决策输入） | 追溯设计依据时 | 2026-09-14 | ❌ 历史快照 |
| [research/open-source-tech-matrix.md](research/open-source-tech-matrix.md) | research | 开源技术矩阵调研（四组并行） | 追溯选型依据时 | 2026-09-13 | ❌ 历史快照 |
| [research/remaining-work-2026-09-14.md](research/remaining-work-2026-09-14.md) | research | 收尾工作清单（基于 issue 拉取） | 追溯计划时 | 2026-09-14 | ❌ 历史快照 |

根级其余文件：[../README.md](../README.md)（人的入口：目录总览 + 常用命令）、[../agent-map.yaml](../agent-map.yaml)（技能启用唯一事实源，被 skills-sync/skillctl 读取，**不移动**）。、[../registry.yaml](../registry.yaml)（资产健康断言登记，被 assets-doctor 消费，**不移动**）。

## 目录职责

| 目录 | 放什么 |
|---|---|
| `specs/` | 规范类：开发规则、管理架构、输出规范（唯一事实源聚集地） |
| `arch/` | 架构/登记类：外部依赖、模型渠道 |
| `runbook/` | 运维手册：跨设备搭建 |
| `research/` | 调研档案：**仅存档不实现**，被现行规范取代的结论以 specs/ 为准 |

## 技能内文档（不在本索引）

各技能的 `SKILL.md`（不可移动，agentskills 规范要求技能根唯一入口）与 `references/`、`resources/`、`assets/` 是技能自身的按需加载级资产，随技能目录走，不在此登记。
