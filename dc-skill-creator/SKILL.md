---
name: dc-skill-creator
description: >
  本仓库专用技能创建工具：新建 SKILL 的脚手架生成 + 13 条规则硬校验（frontmatter 规范、name 唯一、
  description 1024 字符、400 行门禁、agent-map 登记、触发词冲突、输出路径接线）。当用户要求
  创建/新增技能、给主库加 SKILL、或修改技能后需要过检时使用（create a skill / 新建技能 /
  加个 skill）。替代各 App 自带 skill-creator——本工具强制本仓库规约（docs/specs/
  SKILL-AUTHORING-RULES.md + SKILL-MANAGEMENT.md P1-P8）。
metadata:
  version: "0.1"
  family: meta
  role: entry
  load-mode: auto
---

# dc-skill-creator — 本仓库技能创建工具

## 能力概述

CLI 管**形式正确**（确定性校验与骨架生成），本 SKILL 管**内容正确**（交互流程与写作原则）。
13 条强制点（R1-R13）全部机器化，违规即阻断；创建流程六步，每步有对应子命令。

## 触发场景

用户说"创建/新增一个技能""给 dc-skills 加个 SKILL""这个流程值得固化成技能"，或修改现有
技能后需要过检时。仅本仓库（`~/projects/dc-skills`）范围内有效；其他项目的技能创建不走本工具。

## 依赖说明

- Python ≥3.10 + pyyaml（随主库 `.venv`，A 类技能）
- 无外部二进制依赖；规则全文见 `docs/specs/SKILL-AUTHORING-RULES.md`

## 快速用法

```bash
cd ~/projects/dc-skills

# 创建前：评估候选 description 与现有技能的重叠（访谈第 3 步用）
uv run python dc-skill-creator/scripts/check_triggers.py "<候选 description>"

# 生成骨架（dry-run 默认；--apply 落盘 + 登记 agent-map.yaml）
uv run python dc-skill-creator/scripts/new_skill.py <name> \
    --family <族群> --env A|B|C --desc "<能力+触发场景>" [--tier base|on_demand] [--agent dsh]

# 成员标记 + Codex openai.yaml（member 必跑）
uv run python scripts/family-apply.py --apply

# 硬校验（退出码 0 过 / 2 有 E 级 / 3 仅 W 级且 --Werror / 4 环境错）
uv run python dc-skill-creator/scripts/validate.py <name> [--Werror] [--json]

# 物化农场 + 全量体检
uv run python scripts/skills-sync && uv run python scripts/skillctl lint
```

## 交互流程（六步；一次一问，可跳过有默认值的）

1. **命名**（唯一硬卡点）：kebab-case，`^[a-z0-9-]+$`，≤64，须等于目录名；当场用
   `validate.py` 的 R2 逻辑验，不合规立即给归一化建议。查重：主库 + `archive/` + App 自管根。
2. **族群**：thesis / drawing / coding / agentops / info / infra / repo / docs / lark /
   dshplugin / meta；都不匹配 → standalone（`role: standalone, load-mode: auto`）。
   进族群 = `role: member, load-mode: manual`（不进模型清单，`/name` 显式触发）。
3. **能力 + 触发场景**（自由文本）：CLI 不代写 quality，但先用 `check_triggers.py` 预检重叠；
   description 草稿给用户确认或编辑（关键触发词放最前 80 字符，≤1024，祈使句 Use when…）。
4. **环境分类**：A（轻量 Python，建 `.venv` 软链 + `scripts/__init__.py`）/ B（重型独立
   venvs）/ C（无统一 venv，默认）。
5. **外部依赖**：默认空；填了 → 必须先登记 `docs/arch/ENVIRONMENT.md`，否则 R6 拒绝。
6. **启用范围**：on_demand（默认）/ base / 某 agent extra / dsh preset —— 决定 agent-map 写法。

随后：`new_skill.py` 生成骨架 →（member 跑 `family-apply --apply`）→ 按下方写作原则写正文 →
`validate.py` 循环修到退出 0 → 验收清单（`references/checklist.md`）→ git 提交模板。

## 写作原则（内容正确）

- **骨架是起点不是终点**：模板只定 heading 与 frontmatter，正文必须写"改变决策的信息"——
  假设使用方已具备通用能力，不写科普，不复述规范全文（引用条款号）。
- **触发词即门面**：description 决定自动调用；写用户会说的原话（中英文都要），不写内部术语。
- **优先引用已有 CLI 而非写自定义脚本**；确需脚本时走 `scripts/` + `--output` 接线
  （docs/specs/OUTPUT.md C-1/C-3），禁止把产物写进 C-10 明令禁止的 /tmp 日期堆积目录。
- **渐进披露**：SKILL.md ≤400 行（B7a）；长文拆 `references/`，静态资产放 `assets/`，
  文件引用相对 skill 根且只深一层。
- **单一物理副本**：创建器不建农场软链——登记 agent-map 后由 `skills-sync` 物化（P2）。

## 详细规程

- 验收清单全文（提交前逐项打钩）：`references/checklist.md`
- description 写法与 1024 字符预算分配（含 bad/good 对照）：`references/description-guide.md`
- R1-R13 规则表与违规消息的规则编号对照：`docs/specs/SKILL-AUTHORING-RULES.md`
- 与 `skillctl` 的边界：本工具管**创建态**（单技能 + 增量关系），`skillctl lint/inventory`
  管**运行态**（全仓快照）；`skills-sync` 管物化，三者不重叠。

## 已知边界

- 不生成 `agents/openai.yaml`（由 family-apply 统一生成，避免双写）
- 不生成 `evals/`（仅用户显式要求时建空目录 + evals.json 模板，B9）
- 不做打包/发布（`.agent-plugin/` 声明层由 `scripts/plugin-catalog.py` 生成，见 W3）
