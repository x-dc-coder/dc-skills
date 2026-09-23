---
name: diagram
description: >-
  图表生成统一入口（聚合原 7 个 diagram-* 技能）：单表 ER 图（Chen 风格）、多实体 ER 图（1:1/1:n/m:n
  关系）、功能模块图（树形）、用例图（Actor-UseCase）、UML 时序图/顺序图、Mermaid 流程图（软件工程
  论文级，白底黑字）、终端 ASCII 架构草稿。当用户需要生成 ER 图/实体关系图/E-R 图/模块图/用例图/
  时序图/流程图/架构草稿/ASCII 架构图/系统架构草图等软件工程图表（本科毕设/课程设计论文插图）时
  使用本技能：按路由表识别类型，读对应分册规程，执行脚本生成 PNG/代码；输出统一到
  {项目}/thesis-output/{类型}/ 或 ~/.claude/skills-output/{类型}/。正式可编辑图（.drawio）走 drawio-xml。
  族内还有 drawio-xml（可编辑 .drawio）、design-diagram（内联 SVG）、design-dataviz（数据可视化）、design-ui（页面设计），由本技能按需路由。
metadata:
  family: drawing
  role: entry
  load-mode: auto
---
# diagram — 图表生成统一入口（聚合技能）

## 族群路由（drawing 绘图设计入口）

本技能是 **drawing 绘图设计族群入口**。族内技能为 manual 加载（不进启动清单），命中下表场景时直接 Read 对应 SKILL.md 后按其规程执行；用户显式要求加载整族时依次读本表全部条目。

| 技能 | 用途 | 何时选它 |
|---|---|---|
| [`drawio-xml`](~/projects/dc-skills/drawio-xml/SKILL.md) | .drawio 原生可编辑图表（XML） | 用户要可继续编辑的图表文件，或图片转 drawio |
| [`design-diagram`](~/projects/dc-skills/design-diagram/SKILL.md) | Artifact 内联 SVG 图表规范 | 在对话/artifact 内画架构图/流程图/机制图/状态图 |
| [`design-dataviz`](~/projects/dc-skills/design-dataviz/SKILL.md) | 数据可视化设计系统 | 图表/仪表盘/统计卡片的正确性与美观规范 |
| [`design-ui`](~/projects/dc-skills/design-ui/SKILL.md) | 前端页面设计 | 页面/UI 设计与实现指导 |


聚合原 7 个 `diagram-*` 独立技能（draft / er / ers / flow / module / sequence / usecase），
模型目录只保留本技能一个条目。各类型权威规程在 `references/` 分册（原 SKILL.md 正文保真迁移），
脚本与示例保留在各自原目录，路径不变。

## 1. 路由表（意图 → 类型 → 执行）

| 类型 | 用户意图 | 脚本/工具位置 | 执行方式 | 分册 |
|---|---|---|---|---|
| `er` | 单表 ER 图、表结构图、Chen 风格 | `diagram-er/scripts/` | cd diagram-er → `uv run python -m scripts.cli --sql-file x.sql` | references/er.md |
| `ers` | 多实体 ER 图、表关系（1:1/1:n/m:n）、数据模型 | `diagram-ers/scripts/` | cd diagram-ers → `uv run python -m scripts.cli --json-file er.json`（默认 ECharts+Playwright 引擎，可 --engine pillow 兜底） | references/ers.md |
| `module` | 功能模块图、树形层次、模块结构 | `diagram-module/scripts/` | cd diagram-module → `uv run python -m scripts.cli --json-file m.json` | references/module.md |
| `usecase` | 用例图、Actor 与 UseCase 关联 | `diagram-usecase/scripts/` | cd diagram-usecase → `uv run python -m scripts.cli --json-file u.json` | references/usecase.md |
| `sequence` | 时序图、顺序图、交互流程 | `diagram-sequence/scripts/` | cd diagram-sequence → `uv run python -m scripts.cli --json-file s.json`（或 mmd 直渲） | references/sequence.md |
| `flow` | 流程图、业务/功能流程图（论文级 Mermaid 代码） | 无脚本（纯代码输出） | 按分册生成 Mermaid 代码（白底纯黑节点，稳定性优先） | references/flow.md |
| `draft` | ASCII 架构草稿、快速草图、终端架构图 | `diagram-draft/scripts/render.sh` + examples/ | 写描述文件 → `bash render.sh`（graph-easy，可导出 PNG/SVG/HTML） | references/draft.md |

## 2. 通用规范（所有类型一致）

1. **执行环境**：遵守 AGENT.md 核心约束——先 `cd ~/projects/dc-skills` 再 `uv run python`（确保解析根 pyproject 环境）；各技能子目录无需自建 .venv，uv 自动向上查找。脚本内 `common.py`（顶层 scripts/）提供 `resolve_output_path`，已在各 CLI 中接入。
2. **输出路径两级回退**（docs/specs/OUTPUT.md 统一约定，勿改）：
   - 用户 cwd 在工作项目 → `{项目}/thesis-output/{类型}/<文件名>`
   - cwd 在 `~/projects/dc-skills` 或无明确项目 → `~/.claude/skills-output/{类型}/<文件名>`
3. **论文风格**：流程图/时序图/ER 系列统一白色背景、纯黑节点、黑白打印友好（细节见各分册）。
4. **输入格式**：er 用 SQL DDL；ers/module/usecase/sequence 用 JSON；draft 用 graph-easy 描述文本；flow 直接对话生成 Mermaid。
5. **质量自检**：生成后向用户展示并确认；论文场景注意分辨率（分册有说明）。

## 3. 使用流程

1. 按用户意图在路由表识别图表类型
2. 读对应分册 `references/<type>.md` 获取输入规范、命令参数、注意事项（含示例）
3. 准备输入（SQL / JSON / 描述文本）
4. 在对应脚本目录执行命令生成 PNG/代码
5. 按两级回退规则输出，展示给用户

## 4. 备注

- 兼容：原 `diagram-*` 7 个独立技能已聚合，如需找回某类型的完整原文见对应分册（内容一致）
- 互补路由：正式可编辑图表（.drawio 文件、截图复刻）→ `drawio-xml`；图表设计规范（选型/配色/标记）→ `design-dataviz`
