# SKILL 统一管理方案（全机唯一事实源）

> 2026-09-23 建立。本文件是技能管理架构的**唯一事实源**：主库位置、农场模式、各 Agent 自注册机制、管理规约与运维命令。
> 格式规范见 `SKILL-AUTHORING-RULES.md`（对齐 agentskills.io 开放标准）；启用清单见 `agent-map.yaml`。

---

## 1. 架构总览：三层 + 一类

```
┌─ 项目级（第三层）── 各项目仓库内的 .agents/skills、.claude/skills、.dsh/skills
│   例：Lumina/.agents/skills（9 个项目专属技能）。随项目走，不进主库。
│
├─ 主库（第一层）──── ~/projects/dc-skills（git 仓库）
│   全机唯一物理技能目录。33 个注册技能 + diagram-* 脚本目录 + archive/ 归档区。
│   所有跨项目通用的技能都必须物理位于此处。
│
├─ 农场（第二层）──── 各 Agent 技能目录里的软链接，指向主库
│   ~/.claude/skills(16)  ~/.grok/skills(16)  ~/.dsh/skills(18)
│   ~/.zcode/skills(16)   ~/.codex/skills(16)
│   dsh 预设：thesis-agent/skills(6)  plugin-specialist/skills(2)
│   共 90 条软链，由 `~/projects/dc-skills/scripts/skills-sync` 按 agent-map.yaml 物化。
│
└─ App 自管根（只读参考，不进主库）
    ~/.grok/bundled/skills(22)   ~/.codex/skills/.system(6)
    ~/.codex/plugins/cache(21)   ~/.claude/plugins/cache(2)
    ~/.agents/skills（社区 skills CLI 着陆区，约定保持为空）
```

**核心不变式**：一个技能在全机只有一份物理副本（项目级技能除外，它们属于各自项目）。
任何 Agent 看到的技能，要么来自主库软链，要么来自项目仓库，要么来自 App 自带——三者不重叠、不分叉。

## 2. 事实源与工具链

| 文件/工具 | 职责 |
|---|---|
| `agent-map.yaml` | 唯一事实源：base / on_demand / 各 agent 农场与 extra / dsh 预设挂载 |
| `scripts/skills-sync` | 物化农场（补齐缺失软链、回收失联软链、不动真实目录与非主库链接） |
| `scripts/skillctl`（根级） | 生命周期管理：`lint`（契约+死链体检）、`inventory`（全机清单+自注册漂移）、`inspect`、`remove`（5 阶段隔离移除）、`restore` |
| `SKILL-AUTHORING-RULES.md` | 技能创作/修改规则（agentskills.io 基线 + 内部更严标准） |
| `SKILL-MANAGEMENT.md` | 本文件：架构与规约 |

日常运维：

```bash
cd ~/projects/dc-skills
uv run python scripts/skills-sync            # 改完 agent-map.yaml 后物化
uv run python scripts/skills-sync --check    # 漂移检查（零输出动作=健康）
uv run python scripts/skillctl lint          # 契约/死链/环境体检
uv run python scripts/skillctl inventory     # 全机清单：主库 vs App 自管根
```

## 3. 各 Agent 自注册机制与风险（2026-09-23 实测）

| Agent | 自注册能力 | 与我方农场的冲突风险 | 现有防线 |
|---|---|---|---|
| **Claude Code** 2.1.269 | 插件市场（`claude plugin install`，技能随插件缓存于 `~/.claude/plugins/cache/`，以 `plugin:name` 命名空间隔离）；claude.ai 账号技能同步（落 `~/.claude/skills/synced/`，**位于农场根内**） | 🟡 中：当前 2 个插件技能无重名；账号同步若开启会在农场根内长裸名目录 | 账号同步当前未开启；`inventory` 监控插件缓存重名 |
| **Codex** 0.154.0 | `.system` 技能随 CLI 更新；**`skill-installer` 系统技能可被模型自行触发**，copytree 到 `$CODEX_HOME/skills`；`codex plugin add`（21 个插件技能已缓存）；外部 agent 配置迁移导入 | 🔴 高（机制）：安装器遇已存在目标报 `Destination already exists`，不静默覆盖但会阻塞/诱发删除 | 农场软链被 Codex 正常发现（已验证 `$CODEX_HOME/skills` 为用户级根）；`inventory` 监控 `.system`/插件缓存重名 |
| **dsh** 0.1.5-rc.1 | **不装技能，只装插件**（`dsh plugin add` 装 cordis/npm bundle）；bundle 可携带技能（出厂 `cordis` 预设自带 2 个，rank 300） | 🟡 中：出厂预设技能 rank 300 > 农场 rank 400，同名被静默覆盖（当前无重名）；`~/.agents/skills` rank 500 被扫描 | 农场 rank 400 在默认预设 ptc 下生效（ptc 注册 skill-filesystem，已验证）；`inventory` 监控 |
| **Orca** | 自带 8 个指南编译进 `orca-ide` 二进制（`orca skills list/get`，本地读取不联网）；**`orca skills install` 转发社区 skills CLI**（`npx skills add --global`），分发到检测到的各 agent 目录 + `~/.agents/skills` | 🔴🔴 最高：唯一会往农场目录写**真实目录**的机制。上次运行 `lastSelectedAgents` 不含我方四家（运气，非设计） | `~/.agents/skills` 当前为空；Grok 已 ignore 该路径；`inventory` 非空即告警 |
| **lark-cli** 1.0.94 | 28 个域技能**编译进二进制**（`lark-cli skills list/read`），随 `lark-cli update` 走；历史 well-known 端点安装路径已废弃 | 🟢 低：已聚合为主库单一 `lark-cli` 技能（路由表 + `resources/` 补齐二进制不嵌入的脚本/资产） | 着陆区为空即无重建 |
| **Keenable** 0.2.3 | **无技能自注册**。仅注册 MCP server（2 个搜索/抓取工具）+ 向客户端写 `managed_deny_tools: ["WebSearch","WebFetch"]` 让自家工具胜出 | 🟢 无。用户此前的担心不存在，排查结论存档于此避免反复排查 | — |

**dsh 技能发现根与优先级**（`dsh-skill-filesystem` 实测，rank 小者胜）：

| Rank | 根 | 说明 |
|---|---|---|
| 100 | `<project>/.dsh/skills` | 项目级 |
| 200 | `<project>/.agents/skills` | 项目级（Lumina 在用） |
| 300 | `customSkillDirs` | 预设经此接入自己的 skills/（见规约 P6） |
| 400 | `~/.dsh/skills` | **我方 dsh 农场** |
| 500 | `~/.agents/skills` | 社区安装器着陆区——农场模型最大结构性漏洞，靠监控+规约 P1 防线 |
| 600 | `bundledSkillDir` | 环境变量 `DSH_BUNDLED_SKILL_DIR` |

## 4. 管理规约（Policies）

**P1 着陆区保持为空。** `~/.agents/skills` 是社区 skills CLI（orca skills install 等）的默认落点。规约：安装技能时必须带 `--agent universal`（只落共享目录）或显式 `--agent <不含我方五家>`；一旦 `inventory` 报该目录非空，按 P2 收编。Grok 已 `[skills] ignore` 屏蔽；dsh/Codex 无 ignore 能力，只能靠本规约+监控。

**P2 新技能进主库流程。** 任何来源（安装器着陆区、vendor、预设、手建）的技能要全机可用，必须：`mv` 进主库 → `agent-map.yaml` 登记（base/on_demand/extra/preset 四选一或组合）→ `skills-sync` 物化 → `skillctl lint` 过检 → 提交 git。禁止在农场目录里留真实目录（skills-sync 会跳过并告警）。

**P3 App 自带技能不收编。** `~/.grok/bundled/skills`、`~/.codex/skills/.system`、各插件市场缓存由 App 自行更新。同名用户技能会覆盖 App 本体（Grok/Codex/dsh 均如此），收编等于制造一个随 App 更新而静默过期的分叉。它们在 `inventory` 中作为只读参考清单监控重名。

**P4 项目级技能留在项目里。** 项目专属技能（如 Lumina 的 9 个）放项目仓库内的 `.agents/skills/`，随项目版本走，不进主库、不进农场。主库只收跨项目通用技能。

**P5 命名全局唯一。** `name` 必须全仓库唯一（Codex 同名不合并、Grok 按 name 去重、dsh 出厂预设 rank 更高会静默覆盖）。新增前 `ls ~/projects/dc-skills | grep <name>` + `skillctl inventory` 看重名报告。

**P6 dsh 预设技能必须经 customSkillDirs 接入。** dsh 运行时不扫描 `<preset>/skills/`——未接线的预设 skills 目录对 dsh 不可见（2026-09-23 已为 thesis-agent / plugin-specialist 接入，镜像出厂 cordis 预设做法，rank 300）。预设技能仅在该预设的会话中可见（预设作用域）；需要全会话可见的技能进 dsh `extra`（rank 400 农场）。

**P7 格式走 agentskills.io 基线。** frontmatter 只用规范 6 字段 + metadata 扩展；触发信息写进 description；详见 `SKILL-AUTHORING-RULES.md`（2026-09-23 已按规范修订 B1/B2/B3/B3a/A5/B7/B9）。

**P8 命名去厂商品牌化（2026-09-24 用户决策）。** 本仓库自有产物使用 Agent 中立命名：
- 根目录规则文件为 `AGENT.md`（唯一实体）；`CLAUDE.md`、`AGENTS.md` 是指向它的**兼容软链**
  （分别供 Claude Code/Grok 与 Codex/opencode 自动加载），改内容只改 AGENT.md。
- W3 的插件声明层用 `.agent-plugin/`（`marketplace.json`/`plugin.json`）而非 `.claude-plugin/`；
  因 Codex/Grok 当前只识别 `.claude-plugin/`，以 `.claude-plugin/ -> .agent-plugin/` 软链兼容，
  待两端支持中立名后移除。插件名一律 `dc-<族群>` 前缀。
- 客户端产品名（Claude Code、Codex、Grok）在文档中如实出现不属于品牌化问题；禁止的是把
  厂商名写进本仓库的文件名/目录名/manifest 键。

## 5. 已知风险与缓解（按优先级）

| # | 风险 | 缓解 | 状态 |
|---|---|---|---|
| 1 | Orca/社区 skills CLI 误装进我方农场目录（写真实目录） | P1 规约 + `inventory` 着陆区监控 + 安装时 `--agent universal` | ✅ 监控已上线 |
| 2 | Codex `skill-installer` 被模型自行触发，遇农场软链报错/诱发删除 | 监控 `~/.codex/skills` 非软链条目；重名即 `inventory` 可见 | ✅ 监控已上线 |
| 3 | Claude 账号技能同步开启后在农场根内长裸名目录 | 保持 `CLAUDE_CODE_SYNC_SKILLS` 关闭；如开启，把 `synced` 加入 skills-sync 跳过清单 | ⚠️ 潜伏（当前关闭） |
| 4 | dsh 出厂 cordis 预设技能 rank 300 覆盖同名农场技能 | `inventory` 重名检查；当前无重名 | ✅ 监控已上线 |
| 5 | `~/.agents/skills` rank 500 被 dsh 扫描 | P1 + 监控。激进方案（`includeDefaultRoots:false` + customSkillDirs 重建农场根）会连带禁用项目级根，不采用 | ✅ 规约+监控 |
| 6 | 第三方 vendored 技能（omo 4 个）随上游演进而过期 | vendor 审计源留 `~/.dsh/vendor/omo-skills/`（SOURCE.txt 记录 omo 4.17.1）；更新时重跑清洗流程并比对 | 📌 人工周期任务 |
| 7 | **加固动作本身写坏客户端配置**（2026-09-24 实例：Codex `[skills] bundled = false` 写成 bool，而 schema 是 table `BundledSkillsConfig { enabled: bool }`，导致 Codex 全量不可用；正确写法 `bundled = { enabled = false }`，`codex doctor` 显示 `✓ config loaded` 为判据） | 任何跨客户端配置改动后必须跑该客户端的 doctor/自检；W3 的 `assets-doctor` 将 `config_loads` 列为固定检查项（CLI 版本通过≠配置可加载） | ✅ 已修复+入库 |
| 8 | 文档声明与实机漂移（本文件曾写 74 条软链，实机 90 条） | `assets-doctor`（W3）加"文档声明数 vs 实机数"断言；链数以 `find <farm> -maxdepth 1 -type l \| wc -l` 实测为准 | ✅ 本条已修正 |

## 6. 变更日志

- **2026-09-23**：全机聚合收编 7 技能（omo 4 + thesis-export + dsh 预设 2）；新增 codex 农场；dsh-plugin-troubleshooting 补 frontmatter；本文件建立；`skillctl inventory` 上线；thesis-agent/plugin-specialist 接入 customSkillDirs；SKILL-AUTHORING-RULES.md 对齐 agentskills.io。
- **2026-09-24**：`CLAUDE.md`→`AGENT.md` 改名（+两兼容软链，22 处引用修正）；OUTPUT.md 重写（C-1~C-10）并修复 `resolve_output_path`/db-skill/word-extractor/ai4scholar 的输出基准 P0 bug（6 场景实测通过）；.gitignore 补 db-output/doc-output/unified-search-output/.work；B3 增 manual 例外、B3a 增族群元数据三件套；链数修正 74→90；Codex 加固事故入风险台账；新增 P8 命名去品牌化。
