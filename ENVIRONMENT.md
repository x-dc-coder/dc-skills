# SKILL 环境依赖总览（ENVIRONMENT.md）

> **定位**：本仓库全部技能运行所需的**外部工具统一登记表**与验证入口。
> 新增/修改技能引入新工具依赖时：① 登记到下表 → ② 技能 frontmatter 声明
> `metadata.requires.bins` → ③ 正文引用本文件（勿在技能内自写安装段，避免散落）。
> 与 SKILL-AUTHORING-RULES.md §5.3（依赖说明）配套使用。

## 验证命令速查（一条命令核对核心依赖）

```bash
for t in uv gh git node lark-cli keenable mmdc officecli graph-easy mysql; do
  printf '%-12s' "$t:"; "$t" --version 2>/dev/null | head -1 || echo "❌ 缺失"
done
```

> **更省事的替代**：`skill-update-check`（版本/更新，见下方「自动化工具链」）
> 与 `skill-doctor`（有效性，<200ms）已覆盖本节手工命令的全部能力。

## 依赖登记表（2026-08-29 实采；版本以 `skill-update-check` 为准）

| 工具 | 本机版本 | 依赖技能 | 安装方式 | 验证 |
|---|---|---|---|---|
| uv | 0.11.28 | 全部 Python 技能（执行环境：先 `cd ~/projects/dc-skills` 再 `uv run`，见 CLAUDE.md 核心约束） | 官方安装脚本（~/.local/bin/uv） | `uv --version` |
| gh | 2.97.0 | github-workflow（认证/远端/Issue/PR） | 官方安装（~/.local/bin/gh） | `gh auth status` |
| git | 2.55.0 | 全部（github-workflow 为基座） | 系统包 | `git --version` |
| lark-cli | 1.0.94 | lark-cli（飞书 23 域聚合技能，硬依赖） | nvm npm 全局 `@larksuite/cli` | `lark-cli --version`；升级 `lark-cli update` |
| keenable | 0.2.3 | unified-search（keenable 源，**硬依赖**；脚本直接调用二进制） | 官方安装脚本（~/.cargo/bin）；升级 `keenable update` | `keenable --version`；安装/认证/MCP 配置见 unified-search/references/keenable-setup.md |
| mmdc | 11.17.0 | diagram（sequence 类渲染） | npm（mermaid-cli，nvm 环境） | `mmdc --version` |
| graph-easy | v0.76 | diagram（draft 类 ASCII 图） | `sudo apt install libgraph-easy-perl graphviz` | `graph-easy --version`（注意：退出码为 2，属正常） |
| Playwright + Chromium | chromium-1134/1234 | diagram（ers 类 ECharts 引擎，默认）；缺失时用 `--engine pillow` 兜底 | pip playwright + `playwright install chromium`（~/.cache/ms-playwright） | `ls ~/.cache/ms-playwright` |
| officecli | 1.0.149 | officecli（docx/xlsx/pptx） | 官方安装（~/.local/bin/officecli）；**自更新**，版本漂移常见 | `officecli --version` |
| codegraph | 1.6.0 | 代码知识图谱（符号检索/调用图/MCP 工具，非 skill 依赖） | 官方 installer（`~/.codegraph/versions/`）；升级 `codegraph upgrade` | `codegraph --version`；接入规程见 `~/.dsh/knowledge/codegraph-guide.md` |
| mysql | 8.0.46 | db-skill（项目本地库） | 系统/容器 | `mysql --version` |
| node / npx | v24.16.0 / 11.13.0 | drawio-xml（`npx @next-ai-drawio/mcp-server` 按需拉取）、lark-cli | nvm | `node --version` |
| python3 | 3.10.12 | 脚本运行（uv 环境内） | 系统包 | `python3 --version` |

## MCP 工具清单（2026-08-29 全量核实）

> 核实数据源：`claude mcp list`（Claude Code 侧）+ `~/.dsh/cordis.patch.yml`（DSH 插件侧）+
> `~/.claude/.mcp.json` / `/home/dc/.mcp.json`（项目级）。**遗漏风险点**：插件挂载的 MCP
> （如 browser）不在 `claude mcp list` 中，需查 cordis.patch.yml。

| Server | 挂载侧 | 状态 | 依赖技能 / 用途 |
|---|---|---|---|
| vision | Claude Code（`~/.claude.json`） | ✔ | vision-workflow（编排 8 工具）、drawio-xml（视觉复核）、design-*（间接转调） |
| keenable | Claude Code（`~/.claude.json`） | ✔ | unified-search（源之一，可 CLI 可 MCP） |
| codegraph | Claude Code（`~/.claude.json`） | ✔ | 技能仓库 `.codegraph` 项目索引（代码检索） |
| context7 | Claude Code 插件 | ✔ | 无技能引用（孤儿，保留） |
| browser（Playwright） | DSH 插件（`cordis.patch.yml`，`playwright-mcp --headless`） | ✔ | **浏览器自动化默认通道**（`mcp__browser__*`，24 工具）；kimi-webbridge 仅在用户显式指定时使用；用法见 `~/.dsh/knowledge/browser-mcp-guide.md` |
| drawio | 按需 `npx @next-ai-drawio/mcp-server` | ⚠️ 未注册 | drawio-xml（执行层：会话/预览/编辑门控/导出）；待 `claude mcp add drawio` 后全链路可用 |
| fiddler / visio | — | ✘ 已清理（2026-08-29） | 孤儿（无技能引用），配置已从 `~/.claude.json` 与 `/home/dc/.mcp.json` 移除 |

## 环境分类（与 SKILL-AUTHORING-RULES.md §1.1 一致）

- **A 类（仓库自含）**：uv + pyproject.toml 声明，克隆即用，无需额外安装。
- **B 类（本机工具）**：上表所列，每台机器安装一次即可。
- **C 类（按需拉取）**：运行时下载，如 drawio MCP（npx）、Chromium（playwright install）。

## 技能依赖声明模板

frontmatter 声明（模型目录与检查工具可读）：

```yaml
metadata:
  requires:
    bins: ["lark-cli"]   # 工具名，多个用列表；对应用户环境的 B 类工具
```

正文声明：依赖段写"依赖见 `ENVIRONMENT.md` 依赖登记表"并指明所需工具即可，不重复安装步骤
（keenable 例外：其安装/认证/MCP 配置细节保留在 unified-search/references/keenable-setup.md）。

## 自动化工具链（scripts/，全部只读）

三个工具职责严格分离，互不重叠：

| 命令 | 脚本 | 职责 | 网络 | 耗时 |
|---|---|---|---|---|
| `skill-doctor` | `scripts/skill-doctor.mjs` | **有效性 + 注册完整性**：同时扫描 `~/projects/dc-skills`、`~/.agents/skills`、`~/.dsh/skills`，按 realpath 去重；检查缺失 `SKILL.md`/断链注册、二进制、守护进程、venv 与凭据 → READY·DEGRADED·UNAVAILABLE | ❌ 零网络 | 通常 <250ms |
| `skill-update-check` | `scripts/skill-update-check.py` | **版本**：npm / pypi / github / self / baseline 五类通道扫更新 + ENVIRONMENT.md 登记漂移 | ✅ 只读 | ~1-3s |
| `skillctl` | `scripts/skillctl` | **生命周期**：契约 lint / 死链清理 / 5 阶段安全移除 / 秒级回滚 | ❌ 零网络 | 毫秒级 |

`check-engine-updates` 为向后兼容薄壳，等价于 `skill-update-check --focus engines`。

**新增外部依赖时**：① 本表登记版本 → ② 技能 frontmatter 声明 `metadata.requires.bins`
→ ③ 在 `scripts/skill-update-check.py` 的 `TRACKED_DEPS` 加一行（含 channel 与 upgrade 命令），
即可纳入自动更新扫描。

> **登记漂移**：本表版本会被 `skill-update-check` 作为基线比对；自更新型工具
> （officecli 等）版本漂移频繁，发现漂移时同步更新本表即可。
