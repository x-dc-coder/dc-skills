---
name: dsh-plugin-troubleshooting
description: >
  DSH 插件问题排查：安装失败、配置冲突、服务注册冲突、挂载失败、插件不生效、版本不匹配，
  以及"安装插件遇到未知问题"类请求。当用户报告 DSH 插件报错、插件不生效、配置 invalid、
  peer-deps 缺失或需要诊断 ~/.dsh 插件体系时使用。含 plugin-doctor 只读自动检测、标准排障
  流程与修复红线（不自行重启 DSH、不改 shipped 预设）。
  DSH 插件 UI 优化（dsh-ui-optimization）同属 dshplugin 族，由本技能路由。
metadata:
  family: dshplugin
  role: entry
  load-mode: auto
---
# DSH 插件问题排查（dsh-plugin-troubleshooting）

## 族群路由（dshplugin DSH 插件入口）

本技能是 **dshplugin DSH 插件族群入口**。族内技能为 manual 加载（不进启动清单），命中下表场景时直接 Read 对应 SKILL.md 后按其规程执行；用户显式要求加载整族时依次读本表全部条目。

| 技能 | 用途 | 何时选它 |
|---|---|---|
| [`dsh-ui-optimization`](~/projects/dc-skills/dsh-ui-optimization/SKILL.md) | DSH 插件前端优化（薄壳编排） | 优化 DSH client 插件注入的界面/settings 卡片 |


> 适用场景：DSH 插件安装失败、配置冲突、服务注册冲突、挂载失败、插件不生效、版本不匹配，以及"安装插件遇到未知问题"类请求。
> 加载本技能后，先读规则库与文档索引，再按"标准排障流程"执行。

## 0. 先读什么（按顺序）

1. `~/.dsh/.agent-presets/plugin-specialist/RULES.md` —— 本机规则与经验沉淀（必读；先看 §10 文档过期仲裁）
2. 预设 docs/ 索引（**快照**；引用前先判新鲜度——RULES §10 / docs/local/docs-status.md）：
   - `docs/local/config-quickref.md` —— 本机配置/命令实测速查（**首选，本机事实**）
   - `docs/local/docs-status.md` —— 快照新鲜度人读版（一眼判断文档可信度）
   - `docs/README.zh.md` —— **仓库项目简介**（不是命令文档！命令事实源见下方"命令事实源"）
   - `docs/cordis-primer.zh.md` —— Cordis 插件模型基础
   - `docs/config-catalog.zh.md` —— 配置项目录（插件 config 事实源；Tier-1 高频漂移，引用前查新鲜度）
   - `docs/capability-seams.zh.md` —— host 组合 vs 预设的平面规则
   - `docs/defensive-patterns.zh.md` —— 防御性模式（插件常见坑）
   - `docs/cookbook/` —— 实操配方（adding-a-package / adding-a-tool / adding-a-vendored-package / extension-cookbook）
   - `docs/subsystems/*.md` —— 各子系统（settings/credentials/permission-presets/agent-team 等）权威文档
   - `docs/user/` —— 端用户与开发者指南（guide/ 用 UI、develop/ 写插件）
3. **命令事实源（随版本走，首选）**：`/home/dc/.nvm/versions/node/v24.16.0/lib/node_modules/@deepseek-ai/dsh/README.md`（与运行版本永远匹配；上游 apps/cli/README.md 同源）
4. 本地安装包源码：`/home/dc/.nvm/versions/node/v24.16.0/lib/node_modules/@deepseek-ai/dsh/`（含 config/agent-presets/ 下 shipped 预设）
5. 官方仓库克隆（离线查阅；**须定期 git pull 刷新**）：`/tmp/dsh-repo/`

文档路径约定：上述 `docs/` 均指预设根目录 `/home/dc/.dsh/.agent-presets/plugin-specialist/docs/`（不含 docs/packages/——那是打包者镜像，非上游 docs/ 结构）。

## 1. 环境事实

- DSH 版本：以 `dsh --version` 实测为准（2026-09-07：0.1.2-rc.1）；DSH_HOME=`~/.dsh`
- shipped 预设（只读）：`<dsh 安装目录>/config/agent-presets/` → standard / code / minimal / cordis
- 用户预设：`~/.dsh/.agent-presets/` → linux-ops、standard-flash、thesis-agent、plugin-specialist（本预设）
- 全局补丁层 `~/.dsh/cordis.patch.yml`（mcp-vision / mcp-browser / doc-autosync）；手动插件 `~/.dsh/plugins/`（dsh-crypto-randomuuid-polyfill、dsh-plugin-doctor、dsh-doc-autosync）；profile 在 `~/.dsh/profiles/<name>/`（package.json 内 dsh.profile.bundles + cordis.patch.yml + cordis.yml 根文件（boot 重写为 []，勿手编））
- 配置层叠：空根 → `dsh.profile.bundles` → profile `cordis.patch.yml` → `$DSH_HOME/cordis.patch.yml` → `--patch`
- 排查命令：`dsh --profile <name> --dump-config` / `dsh --profile <name> --dump-default-config` 对比定位问题层（**必须带 --profile**；web profile 可用 `dsh web --dump-config`；dump 会重写 profile 根 cordis.yml，幂等）
- 代理：WSL `http://127.0.0.1:7890`；GitHub 直连

## 2. 标准排障流程

0. **先跑自动检测**（本预设已挂 plugin-doctor 工具包，全部只读）——**双前提：宿主 dsh 新鲜度 + docs 快照新鲜度**：
   - `plugin_doctor_health` → 自动发现 peer-deps 缺失/版本不满足、核心包物理副本、版本漂移、patch 行问题、runtime-compat、宿主新鲜度、docs 快照新鲜度、settings 体检
   - 装新插件前 → `plugin_doctor_predict`（装前冲突预测）
   - 用户问"有没有更新" → `plugin_update_scan`；问"该不该升级" → `plugin_update_brief`
   - 宿主或 docs 快照过期 → 回答中显式标注"结论可能受版本漂移影响"
1. 收集事实：完整报错、`dsh --version`、插件安装方式与位置、涉及 profile。
2. 归类错误（对照 RULES.md §2 速查表）：
   - `Cannot find package …` → 包未安装 → `dsh plugin --profile <name> add <pkg>`
   - `invalid config` → 对照 config-catalog（查新鲜度）/ 包 README 补 config
   - `N row(s) did not activate: waiting for <service>` → 平面错误
   - `service "x" has been registered` / `published process-global service(s)` → 预设发布服务未隔离
   - 插件不生效 → disabled / config 被上层覆盖 / 实例遮蔽 / 物理副本（plugin_doctor_health 的 duplicate-copy）
3. `dsh --profile <name> --dump-config` vs `--dump-default-config` diff 定位层。
4. 按平面规则修复（host 组合 vs 预设；isolate realm）。
5. 修改配置后验证（启动安全 SOP）：
   a. 备份：`cp <配置文件> <配置文件>.bak-<时间戳>`
   b. 编辑：只改包外平面（settings.yaml / cordis.patch.yml / package.json.dsh.profile），禁改包内文件；禁 `!!js` 内联凭据
   c. 验证 gate：`dsh --profile <name> --dump-config` 退出码 0 + 目标行在位（grep）；高风险改动先在临时 profile 试跑
   d. dsh CLI 校验失败或 DSH 进程不可用 → 用独立脚本兜底：`bash ~/.dsh/.agent-presets/plugin-specialist/scripts/doc-sync-check.sh check`（配置 YAML 语法检查 + 回滚指引）
   e. 交用户重启（Agent 不自行重启），重启后验证：`dsh --version`、新会话确认工具在位
   f. boot 失败回滚：恢复最近 `.bak` → 重跑 c → 交用户重启
6. 预设改动用挂载验证（standingKeyFor / preset_validate）；插件代码用动态插件机制验证。
7. 修改前备份；破坏性操作先说明影响面；不自行重启 DSH（用户手动）。

## 3. 工具与机制

- 动态插件（cordis_define）：插件 ID 由 Host 分配；Package 不可变（改代码=追加新 Package）；`run` 首次/重启/回滚，`update` 切版本；授权单勾=当前包、双勾=未来版本。
- 动态工具：`harness.registerTool(ctx, harness.defineTool({...}))`，注册后下一个模型步骤可调用；参数 `required` 只能为 true；output 用 `{ schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: v }] }`。
- 服务访问：可选用 `ctx.get(name)` + undefined 检查；硬依赖才 `inject`；副作用必须属于当前 Fiber。

## 4. 红线

1. 禁止自行重启 DSH 进程（kill/重启 `dsh web`）；需重启 → 用户手动，Agent 只做准备与验证。
2. 禁止修改/删除 shipped 预设（standard/code/minimal/cordis）；改副本到 `~/.dsh/.agent-presets/`。
3. 不编造文档/配置：引用 docs/ 前先判新鲜度（RULES §10）；fresh 才以 docs 为事实源；过期以本机安装包源码 + `dsh --help`/实测为准；命令签名一律实测，禁止凭记忆写命令。
4. 用 `dsh plugin --profile <name> <pnpm args>` 管理插件，不在 profile 外裸跑 pnpm。
5. 破坏性操作先备份 + 说明影响面。

## 5. 交付

- 中文回答；先结论（根因+修复），再证据链（错误原文、配置 diff、文档出处）。
- 修复后给出用户待办（如手动重启 DSH、新会话验证）与验证清单。
- 新经验追加到 RULES.md（按日期）。
