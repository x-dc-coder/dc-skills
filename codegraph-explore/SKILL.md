---
name: codegraph-explore
description: >
  用 CodeGraph 做代码库符号检索、调用链追踪和变更影响分析。理解大仓代码、定位定义或调用者、评估重构影响、选择测试时使用；一次返回相关源码与调用路径。小仓、配置和 Markdown 仍用 grep/read。
  git/GitHub 工作流（github-workflow）同属 repo 族，由本技能路由。
metadata:
  family: repo
  role: entry
  load-mode: auto
---
# codegraph-explore — 符号级代码检索与影响面分析

## 族群路由（repo 仓库工程入口）

本技能是 **repo 仓库工程族群入口**。族内技能为 manual 加载（不进启动清单），命中下表场景时直接 Read 对应 SKILL.md 后按其规程执行；用户显式要求加载整族时依次读本表全部条目。

| 技能 | 用途 | 何时选它 |
|---|---|---|
| [`github-workflow`](~/projects/dc-skills/github-workflow/SKILL.md) | GitHub-first git 工作流 | git/gh 操作/提交规范/PR/issue/manifest |


## 0. 一句话判据

**仓库源文件数 > 200 → 先 `codegraph_explore`；< 50 → 老实 grep/read。**
（统计源文件数时**必须排除** `.venv` / `venvs` / `.uv-cache` / `.uv-python` / `node_modules` / `site-packages` / `dist` / `build`。
实测教训：Vision-MCP `find` 出 1.7 万文件，排除虚拟环境后**真实源码只有 17 个**。）

---

## 1. 怎么调（DSH 侧的关键差异）

工具只有 **1 个**：`mcp__codegraph__codegraph_explore`（v1.6.0 已把旧版 8 个工具合并）。

```
codegraph_explore(
  query="<符号名袋 或 自然语言问题>",
  projectPath="/home/dc/projects/<repo>",   ← DSH 侧【必传】
  maxFiles=12                                ← 可选，默认 12
)
```

> ⚠️ **DSH 侧必须显式传 `projectPath`**：`dsh-mcp-client` 声明 `capabilities: {}`、**不发送 MCP roots**，
> 服务端无法自行定位项目（启动日志会打印 `no default project, live sync disabled`，属预期）。
> Claude Code 侧按 cwd 自动定位，**无需传参**；终端兜底用 CLI：`cd <repo> && codegraph explore "..."`。

---

## 2. ⚠️ 查询技法（踩过的坑，务必遵守）

**用「符号名 / 文件名袋子」，不要用宽泛的长句自然语言。**

实测反例：问 *"unified-search 的源码结构：入口 main、各搜索源 adapter、去重排序与配额管理如何组织"* ——
codegraph 抓了 `main` 这个极常见符号，返回 `drawio-xml` / `word-extractor` / `github-workflow` 等 **10 个不相关文件**，
**恰恰没返回 unified-search**。
改成符号名袋 `mode_general dedup_and_rank search_keenable search_with_retry load_config` → 一次精准命中 40 符号 / 2 文件。

| 想干什么 | 查询怎么写 |
|---|---|
| 理解某个流程 | 流程两端的**符号名**（`mutateElement renderScene`） |
| 找定义/读源码 | 直接给符号名或文件路径 |
| 看谁调用 / 影响面 | 给符号名，结果里自带 blast radius |
| 定位"某个功能在哪" | 先用 grep/`codegraph query <name>` 拿到符号名，再 explore |

---

## 3. ⚠️ 索引会滞后（无 live watcher）

DSH 侧全部走 `projectPath` 查询，而服务端**只在默认项目上跑文件监听** → 这些索引**不会自动追平**。
响应里出现 `⚠ changed on disk after the last index sync` 就是这个信号。

**规程：重要改动前 / 拿到可疑结果时，先 sync（增量，毫秒级）**
```bash
cd <repo> && codegraph sync
```

> 另一个坑：**CLI `codegraph status` 的 "Index is up to date" 不可信**——它的 `pendingChanges` 常为 0 与事实不符，
> 且 `codegraph query` 不会触发自动同步（实测执行前后 DB mtime 完全不变）。怀疑陈旧就直接 `sync`。

---

## 4. 已建索引仓库（2026-09-11）

cloudreve / RuoYi-AI / grm-repro / grm-upstream / github-release-monitor / container-homework /
D2 / Soul-Spark / lab-monitor / monitor-panel / Custom-Agents / Vision-MCP / dsh-llm-agentrouter /
`~/projects/dc-skills`（后者索引存 `~/.omo/`，经符号链接挂载；合计约 306 MB）

**未索引的仓库不要自动 `codegraph init`**（索引是用户决策），提示用户即可。
`next-ai-draw-io-main` 目录属 root，无 sudo 无法索引。

---

## 5. 与 grep/read 的取舍

| 场景 | 用什么 |
|---|---|
| 大仓里找符号 / 追调用链 / 评估改动影响 | ✅ `codegraph_explore` |
| 小仓（<50 文件） | ⚪ grep + read 更快 |
| 查配置 / 文档 / **Markdown 规则** / shell 脚本 | ⚪ codegraph **不支持 Markdown**（实测 init 纯 md 目录 → "No files found to index"），用 grep/read |
| 确认某个具体细节（explore 未覆盖） | ⚪ 单点 Read |
| 改前看爆炸半径 / 改后选测试 | ✅ `codegraph impact` / `codegraph affected` |

**反模式**：用 codegraph 拿到结果后再 grep 复核一遍——那是重复劳动，浪费上下文。
**特别提醒**：检索 DSH 自己的文档/规则（AGENTS.md、knowledge/*.md、RULES.md 等，都是 Markdown）**不要用 codegraph**，用 grep + read 或查 `knowledge/README.md` 索引。

---

## 6. 完整资料

接入/维护/踩坑全量记录：`~/.dsh/knowledge/codegraph-guide.md`
