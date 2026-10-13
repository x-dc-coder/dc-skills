---
name: codegraph-explore
description: >
  用 CodeGraph 做代码库符号检索、调用链追踪和变更影响分析。当用户要理解大仓代码、定位定义或调用者、评估重构影响、选择测试时使用；一次返回相关源码与调用路径。任意语言。小仓、配置和 Markdown 仍用 grep/read。
metadata:
  family: software
  role: member
  load-mode: manual
disable-model-invocation: true
---
# codegraph-explore — 符号级代码检索与影响面分析

本技能是 software 族的成员，由 programming 路由进来。git、GitHub、PR、issue 不在这里处理。

## 0. 一句话判据

**仓库源文件数 > 200 → 先 `codegraph_explore`；< 50 → 老实 grep/read。**
（统计源文件数时**必须排除** `.venv` / `venvs` / `.uv-cache` / `.uv-python` / `node_modules` / `site-packages` / `dist` / `build`。
实测教训：Vision-MCP `find` 出 1.7 万文件，排除虚拟环境后**真实源码只有 17 个**。）

---
## 1. 怎么调

工具只有 **1 个**：`codegraph_explore`（旧版多个工具已合并）。查询用符号名或文件名，少用长句。

| 宿主 | projectPath | 索引新鲜度 |
|---|---|---|
| Grok、Claude Code | 问当前会话项目时省略，用默认项目；问另一个仓库时传绝对路径 | **仓库在原生盘**（~/、/home）才有文件监听，滞后大约 1 秒 |
| 仓库在 WSL2 `/mnt/*` 挂载盘（无论默认项目与否） | 同上 | **文件监听被禁用**（daemon 明示 /mnt/ 上 fs.watch 不可靠），索引永不自动更新；`git mv`/目录重构后必须手动 `codegraph sync`（2026-10-10 事故：Words-Production 迁移 src/ 后索引仍记旧路径，explore 全部命中 unreadable、0 源码返回） |
| dsh | 必传。该客户端不发送 MCP roots，省略则没有默认项目 | 非默认项目没有实时监听。结果可疑或刚改完要查时，先 `cd <repo> && codegraph sync` |

终端兜底：`cd <repo> && codegraph explore "..."`。`maxFiles` 可选，默认 12。

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

## 3. ⚠️ 索引会滞后

dsh 上按 `projectPath` 查询的仓库没有实时监听，改完不会自动追平。
Grok / Claude Code 的默认项目**在原生盘上**有文件监听，仍可能滞后大约 1 秒；**在 /mnt/ 挂载盘上监听被禁用**（见上表）。
响应里出现 `⚠ changed on disk after the last index sync` 就是这个信号。

**规程：重要改动前 / 拿到可疑结果时 / 目录重构（git mv、rename、搬 src/）后，先 sync（增量，毫秒级）**
```bash
cd <repo> && codegraph sync
```

**返回尾部 `Explore budget: N calls` 的正确解读**：这是按项目文件数给 AI 的建议探索深度（<500 文件→1 次，<5000→2 次），**无状态、每次调用原样重复，不是任何配额或限制**。怀疑是硬配额时，再调一次看计数是否递减即可证伪（2026-10-10 事故：主智能体把它误读成"预算用尽"放弃了工具，实际还有无限次）。

**索引保鲜三层方案（2026-10-11 设计）**

1. **Git 钩子（首选，事件驱动）**：`/mnt/` 仓库装 post-commit / post-checkout / post-merge 钩子，后台增量同步，一切经 git 的结构变更（git mv/rename）提交即触发：
   ```bash
   for h in post-commit post-checkout post-merge; do printf '#!/bin/sh\n(codegraph sync -q >/dev/null 2>&1 &)\n' > .git/hooks/$h; chmod +x .git/hooks/$h; done
   ```
   （`sync -q` 官方注释即为 git hooks 设计；后台化避免大重构时 88 秒级 sync 阻塞 commit。Words-Production 已装。）
2. **读时自愈（非预期结果即更新）**：explore 返回 **0 源码 / 符号全进 `Not shown above` / 出现 `changed on disk` 警告** 任一信号 = 索引过期嫌疑 → 立即 `codegraph sync` → **原查询重试一次** → 仍失败降级 grep/read 并向用户明确报告索引问题。禁止把过期结果当真，也禁止只降级不同步（2026-10-10 事故：0 源码返回被当真，白白损失两次调用）。
3. **新鲜度预检（/mnt/ 仓库首次调用前）**：`find <repo> -newer <repo>/.codegraph/codegraph.db \( -name '*.py' -o -name '*.ts' -o -name '*.go' -o -name '*.rs' -o -name '*.js' \) -type f 2>/dev/null | head -3` 非空即先 sync 再查（亚秒级）。比定时器精准：每次读前等效检查，且避免盲定时在 /mnt/ 上制造 IO 压力与大 sync 风暴。

> 另一个坑：**CLI `codegraph status` 的 "Index is up to date" 不可信**——它的 `pendingChanges` 常为 0 与事实不符，
> 且 `codegraph query` 不会触发自动同步（实测执行前后 DB mtime 完全不变）。怀疑陈旧就直接 `sync`。

---

## 4. 已建索引仓库（2026-09-11）

cloudreve / RuoYi-AI / grm-repro / grm-upstream / github-release-monitor / container-homework /
D2 / Soul-Spark / lab-monitor / monitor-panel / Custom-Agents / Vision-MCP / dsh-llm-agentrouter /
Words-Production（/mnt/e，628 文件，/mnt/ 无监听需手动 sync）/
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

接入与踩坑记录：`~/.dsh/knowledge/codegraph-guide.md`。触发方式以本文件文首为准。
