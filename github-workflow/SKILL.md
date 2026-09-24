---
name: github-workflow
description: >
  基于官方 gh CLI 的 GitHub-first git 工作流：本地仓库初始化（仓库级身份、master 默认分支、.gitignore）、提交前预览与敏感文件扫描、Conventional Commits 提交信息生成、review manifest 发布、gh repo create 建远程。当用户需要初始化 git、配置仓库身份、预览待提交文件、生成提交信息、创建 GitHub 远程或管理 issues/PR 时使用；替代已退役的 gitcode-workflow。
metadata:
  family: software
  role: member
  load-mode: manual
disable-model-invocation: true
---
# GitHub Git Workflow（基于 gh CLI）

## 定位：官方 gh 之上的优化与扩展层

本技能**不重复实现 GitHub 能力**，而是把官方 GitHub CLI（`gh`）当作认证与 API 主干，
在它之上补齐"本地 git 流程的优化与扩展"：

| 层面 | 负责方 |
|------|--------|
| 认证 | `gh auth login`（不再需要 token/SSH 密钥配置） |
| 创建仓库 / Issue / PR / 用户信息 | `gh repo create` / `gh issue` / `gh pr` / `gh api` |
| **本地 git 初始化、待提交预览、安全扫描、提交信息候选生成、review manifest、分层提交建议、提交规范** | **本技能（gh 没有的流程与判断能力）** |

已弃用 GitCode；旧版 gitcode-workflow 已归档到 `~/skills-archive/gitcode-workflow`（未注册，仅供查档）。

## Quick Commands

### 仓库操作（本技能 CLI）

| 用户意图 | 指令 |
|---------|------|
| 初始化本地 Git | `uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py local-only --project <path> --json` |
| 预览待提交文件 | `... github_bootstrap.py preview --project <path> --json` |
| 创建 GitHub 远端 | `... github_bootstrap.py create-remote --project <path> --json`（内部调 `gh repo create --source --remote`） |
| 提交并推送 | `... github_bootstrap.py publish --project <path> --commit-message "<msg>" --json` |
| 分析已有项目 | `... github_bootstrap.py adopt-existing-project --project <path> --json` |
| 查看个人信息 | `... github_bootstrap.py profile --action show --json` |

### 直接用官方 gh（技能推荐的 GitHub 侧途径）

| 用户意图 | 指令 |
|---------|------|
| 登录 / 查看认证 | `gh auth login` / `gh auth status` |
| 创建仓库并一键推送 | `gh repo create <name> --private --source=. --remote=origin --push` |
| Issue 列表 / 创建 / 关闭 / 重新打开 | `gh issue list --repo <o>/<r>` / `gh issue create` / `gh issue close <n>` / `gh issue reopen <n>` |
| PR 创建 / 查看 | `gh pr create` / `gh pr view` |
| 修改默认分支 | `gh repo edit <o>/<r> --default-branch <b>` |
| 查看我的仓库 / 用户信息 | `gh repo list` / `gh api user` |

gh 命令全表 → `references/gh-cheatsheet.md`（替换了旧版 GitCode API 手册）。

## 执行环境约束

所有 Python 脚本用 `uv --project` 指定 skill 项目运行（**保持当前工作目录为项目仓库**，脚本自动从 git remote 推断 owner/repo）：
```bash
uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/<script>.py ...
```

## 输出规范（脚本 --json 模式）

- **stdout 是纯 JSON**，日志/进度走 stderr —— 读取结果时**勿用 `2>&1` 混流**。
- 长输出会截断：重定向到文件后分段读：
  ```bash
  uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py preview --project . --json > /tmp/gw_preview.json 2>/tmp/gw_preview.err
  ```
  安全扫描在 `safety_scan` 字段，候选输入在 `diff_excerpt` / `files_by_kind` / `type_hints` / `scope_hints`。

## Decide the mode first

1. 判断用户意图：
   - **local-only**：初始化本地仓库、配置 Git 身份和忽略规则
   - **adopt-existing-project**：分析已有目录，输出分层提交策略（只读，不改任何文件）
   - **create-remote**：创建 GitHub 远端仓库（内部走 `gh repo create`，需用户明确要求）
   - **preview**：预览待提交文件，生成候选提交信息（安全操作，写 review manifest 到 `.git/`）
   - **publish**：提交并推送（需用户确认最终提交信息后执行）
2. 副作用边界：
   - `local-only`：仅改仓库本地配置
   - `adopt-existing-project`：纯分析，零修改
   - `create-remote`：外部副作用（`gh repo create` 创建 GitHub 仓库 + 设置 remote）
   - `preview`：写 `.git/github-workflow-review.json`
   - `publish`：外部副作用（push 到 GitHub），需用户显式确认
3. 配置优先级：CLI args > 环境变量（`GITHUB_*`）> `~/.config/github-workflow/config.json` > 内置默认值
4. 提交规则：优先读 `docs/rules/Git-Commit.md`（项目内）；不存在时用 `references/commit-rules.md`（内置模板）

## local-only workflow

```bash
uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py local-only --project /path/to/project --json
```

此模式：初始化 Git、配置仓库级身份（默认 `x-dc-coder / x.dc0521@gmail.com`）、设置 `master` 为默认分支、检测技术栈、追加 `.gitignore` + `.git/info/exclude` 规则。完成后汇报结果并停止。不预览、不创建远端、不提交。

## adopt-existing-project workflow

```bash
uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py adopt-existing-project --project /path/to/project --max-layers 6 --json
```

输出启发式分层提交建议（路径+文件名推断），标注为"需人工审查"。不初始化 Git、不修改文件。

## create-remote workflow（GitHub 版）

先执行 local-only，再：
1. 检查 `gh auth status`（未登录先提示用户 `gh auth login`）
2. 脚本内部执行：`gh repo create <owner>/<name> --private|--public --source=<project> --remote=<origin>`
3. 设置 `remote.pushDefault`（9p 挂载下可能失败，容忍并告警）
仅在用户**明确要求**创建远端时执行。

## preview workflow

```bash
uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py preview --project /path/to/project --json
```

预览待提交文件（按 added/modified/deleted/renamed/untracked 分组）、安全扫描高风险文件、生成候选提交信息、写入 review manifest。

### 提交信息生成（子代理主力）

候选提交信息**完全通过子代理生成**（无脚本 fallback）：

1. 运行 `preview` 拿到 `diff_excerpt`、`files_by_kind`、`type_hints`、`scope_hints`
2. 判断改动规模：
   - **单批次**（files ≤ 6）：spawn **1 个子代理**，生成 3 个候选
   - **多批次**（files > 6 或多种改动类型混合）：先按逻辑关系分组（如核心代码/工具链/文档），每组 spawn **1 个子代理**，每组生成 2-3 个候选
3. 子代理模型：**deepseek-v4-flash**（快速、低成本）
4. **子代理必须先把候选 JSON 落盘、再在回复中重述**（防主代理中断/通知丢失）：
   - 落盘路径：`.git/github-workflow-candidates/<batch>.json`（纯 JSON，无代码块包裹）
   - 主代理从文件读取候选，不以子代理回复文本为唯一来源
5. 候选展示后由开发者审查确认，再决定采用或调整

**候选信息结构**（3 条精简候选，开发者选其一或自行修改）：
- 全部为精简 Conventional Commits 格式 — 中文 subject ≤50 字符，动宾结构
- 格式：`<type>(<scope>): <subject>`
- 3 个候选从不同角度概括（如功能实现 / 问题修复 / 结构调整），便于快速挑选
- ⭐ 不要求逐文件 body——如需补充细节，开发者确认后自行添加即可

**子代理 prompt 模板**：
```
你是代码审查专家。请仔细阅读以下 git diff，分析每个文件的具体改动内容。
基于实际改动内容生成 3 个候选提交信息（均为精简 Conventional Commits 格式）。

- 中文 subject，≤50 字符，动宾结构
- 格式：<type>(<scope>): <subject>
- type 从 {valid_types} 中选择
- 3 个候选从不同角度概括（如功能实现 / 问题修复 / 结构调整），供开发者挑选

改动文件：
{file_list}

Diff 内容：
{diff_excerpt}

注意：
- 若某个文件标注 "preview omitted because the file is larger than ..."，用
  `git diff HEAD -- <file>`（已跟踪文件）或读取文件的结构摘要（untracked）补充分析，
  不要猜测其内容。
- 完成分析后，先把候选 JSON **原样写入** .git/github-workflow-candidates/<batch>.json
  （纯 JSON 文件，无 markdown 代码块、无前后缀文本），再在回复中重述同一 JSON。

返回 JSON，candidates 数组固定 3 个元素。
```

**结构化输出 Schema**：
```json
{
  "type": "object",
  "properties": {
    "candidates": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": {
        "type": "object",
        "properties": {
          "type": {"type": "string", "enum": ["feat","fix","docs","style","refactor","perf","test","chore","revert"]},
          "scope": {"type": "string"},
          "subject": {"type": "string", "maxLength": 50}
        },
        "required": ["type", "scope", "subject"]
      }
    }
  },
  "required": ["candidates"]
}
```

**候选 JSON 容错解析**（子代理回复可能带前后缀文本）：
1. 优先读落盘文件 `.git/github-workflow-candidates/<batch>.json`（文件应为纯 JSON）
2. 若读回复文本：剥离 markdown 代码块，取首个 `{` 到末个 `}` 之间的子串再 `json.loads`
3. 解析失败（`JSONDecodeError`）：把原文发给子代理要求"仅重述 JSON，无其他文本"

### 预览后的流程

1. 展示分组后的待提交文件列表 + 安全扫描结果
2. 展示子代理生成的候选信息，由开发者审查确认
3. 多批次时：先展示分批方案和各批候选，等待用户逐批确认
4. 用户确认最终消息后，记录 review manifest 路径和 snapshot hash
5. **不要**在用户确认前执行 publish

### review manifest 结构（.git/github-workflow-review.json）

| key | 类型 | 说明 |
|---|---|---|
| `version` | int | manifest 版本 |
| `project_path` | str | 项目路径 |
| `created_at` | str | 生成时间（ISO） |
| `head_commit` | str | 生成时的 HEAD 提交 |
| `status_lines` | list[str] | `git status --short` 原文行 |
| `counts` | dict | 各状态文件计数 |
| `files` | list[dict] | 逐文件详情（path/kind/status/raw） |
| `stage_targets` | list[dict] | 建议的提交分组（含候选提示） |
| `snapshot_hash` | str | 工作区快照哈希（**worktree 一致性校验**：publish 前重新计算比对，不一致须重跑 preview） |

## publish workflow

仅用户明确确认提交信息后执行：

```bash
uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_bootstrap.py publish --project /path/to/project --commit-message "<msg>" --json
```

**单批次**：加载 review manifest → 验证 worktree 未变 → 安全扫描 → stage 文件 → commit → push → 清除 manifest

**多批次**：优先用 publish 的 `--batches <json>`（每批 `{files, message}`，脚本统一做
manifest 校验 → snapshot 比对 → 安全扫描 → 逐批 commit → 一次 push）；脚本不可用时
才手动逐批 `git add <files>` + `git commit -m "<msg>"`，全部完成后一次 `git push`。

如果 worktree 在 preview 后发生了变化，必须重新运行 preview。

**push 完成判定**：输出含 `To github.com:...` 与 `master -> master`（或对应分支）即为成功。

**9p/drvfs 挂载（/mnt/*）注意事项（重要）**：
- 9p 挂载（如 /mnt/e）无 `metadata` 选项时，**chmod 返回 EPERM**，导致一切写
  `.git/config` 的 git 命令失败：`git config`、`git remote add/set-url`、
  `git push -u`（写 upstream）。这不是噪音，是必然失败。
- 本脚本已对 push 做**自动降级**：`git push -u` 失败时自动改为不带 `-u` 的普通 push
  （内容推送成功，仅上游跟踪未设置），无需手动处理。
- 其余 config 写操作（remote add 等）在 9p 上仍会失败：直接编辑 `.git/config` 绕过，
  或先根治挂载（`~/bin/enable-drvfs-metadata.sh`，需 sudo + `wsl --shutdown` 重启）。

## 中断恢复 SOP（主代理中断 / 子代理通知丢失）

1. `list_agents`（scope: descendants）查看子代理状态：`ready` = 结果已产出可恢复
2. 读候选落盘文件 `.git/github-workflow-candidates/*.json` —— 有文件则直接取结果
3. 无落盘文件时 `send_message` 让子代理"原样重述最终 JSON，不要重新分析"
4. 子代理上下文也未留存（回复称无法重述）→ 按 review manifest 的 `files` / `stage_targets`
   重新 spawn 子代理（输入用 manifest 中记录的 diff 信息）
5. 恢复完成后校验 `snapshot_hash` 是否仍与工作区一致，再进入候选确认流程

## gh 认证与 Git 协议

1. `gh auth status` 检查登录；未登录先 `gh auth login`
2. `gh auth setup-git` 让 git 走 gh 凭证（https 推送无需密码）
3. SSH 方式验证：`ssh -T git@github.com`（应显示 Hi x-dc-coder!）
4. 非默认 key 且无 ssh-agent 时可用临时 fallback：
   ```bash
   GIT_SSH_COMMAND='ssh -i ~/.ssh/<your-key> -o IdentitiesOnly=yes' git push -u origin master
   ```

## 分支与合并规范

### 分支命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/user-auth` |
| `fix/` | Bug 修复 | `fix/login-redirect` |
| `docs/` | 文档更新 | `docs/api-reference` |
| `refactor/` | 代码重构 | `refactor/payment-flow` |
| `chore/` | 工程杂项 | `chore/update-deps` |
| `exp/` | 实验性分支 | `exp/new-algorithm` |

### 分支策略

- 默认分支：`master`（**注意与 GitHub 仓库默认分支一致**；不一致时 `gh repo edit <o>/<r> --default-branch master`）
- 新功能/修复：从默认分支切出分支，开发完成后合并回默认分支
- **禁止 force-push 到默认分支**
- 合并方式：默认 `git merge`（保留完整提交历史）。小改动、单 commit 特性可用 `git merge --squash`
- 合并前确保目标分支已拉取最新

## .gitignore 规范

### 两层规则体系

| 文件 | 用途 | 纳入版本控制 | 示例 |
|------|------|-------------|------|
| `.gitignore` | 团队共享的忽略规则 | ✅ 是 | `target/`, `__pycache__/`, `node_modules/`, `.idea/`, `*.log` |
| `.git/info/exclude` | 本机的敏感/私有规则 | ❌ 否 | `.env`, `*.key`, `*.pem`, `application-local.yml`, `credentials*.json` |

### 禁止做法

- ❌ 将密钥/证书/本地配置模式放在 `.gitignore`（必须放在 `.git/info/exclude`）
- ❌ 将 IDE 个人配置（如 `.vscode/settings.json`）放在 `.gitignore`
- ❌ 手动编辑 `.gitignore` 后不运行 `preview` 验证
- ❌ 在 `.gitignore` 中逐文件列出所有环境配置变体（应使用通配符）

### 标准忽略项（脚本自动追加）

- **通用**：`.DS_Store`, `Thumbs.db`, `.idea/`, `.vscode/`, `*.log`, `logs/`
- **Java/Spring**：`target/`, `build/`, `.gradle/`, `*.class`, `out/`
- **Python**：`__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.venv/`, `venv/`, `dist/`, `*.egg-info/`
- **Go**：`bin/`, `coverage.out`, `*.coverprofile`, `*.test`

## 提交信息规范

### 格式约束

- **绝对禁止**在提交信息末尾或正文添加 `Co-Authored-By` 标记
- 格式：`<type>(<scope>): <subject>`
- 可选 type：`feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `revert`
- scope 强烈建议提供，命名简短清晰
- 中文 subject，不超过 50 字符，结尾不加标点
- 多行消息：subject 与 body 之间空一行，body 每行不超过 72 字符

### 提交规则来源

优先读项目内的 `docs/rules/Git-Commit.md`，不存在时用内置模板 `references/commit-rules.md`。

## CJK 路径

仓库含中文/日文/韩文文件名时，设置：
```bash
git config core.quotepath false
```

## Issue / PR 管理（gh 版）

### Issue

| 意图 | 命令 |
|------|------|
| 列出 | `uv run --project ~/projects/dc-skills python ~/projects/dc-skills/github-workflow/scripts/github_issues.py --owner <o> --repo <r> list [--state open|closed|all] [--labels <l>]` |
| 创建 | `... github_issues.py --owner <o> --repo <r> create --title "..." [--body "..."] [--labels "..."] [--assignee <a>]` |
| 查看 | `... github_issues.py --owner <o> --repo <r> get <number>` |
| 更新 | `... github_issues.py --owner <o> --repo <r> update <number> [--title "..."] [--body "..."] [--labels "..."]` |
| 关闭 / 重新打开 | `... github_issues.py --owner <o> --repo <r> close <number>` / `reopen <number>` |
| 评论列表 / 添加 | `... github_issues.py --owner <o> --repo <r> comments <number>` / `comment-create <number> --body "..."` |

也可直接用官方：`gh issue list --repo <o>/<r>` 等。**`--owner` 和 `--repo` 在仓库目录下运行时自动从 git remote 推断，可不指定。**

### commit message 引用关闭 Issue

在提交信息中使用 `fix #N` / `close #N` / `closes #N` 可自动关闭 Issue（GitHub 支持：
提交推送到默认分支或通过 PR 合并时触发）。**可靠做法**：commit-push 后手动 `gh issue close <n> --repo <o>/<r>`。

### PR

个人项目按需使用：`gh pr create --base master --title "..." --body "..."`；
PR 合并（`gh pr merge --merge`）后的提交会正常计入贡献图。

## 安全规则

- 团队共享的忽略规则放 `.gitignore`
- 本机敏感文件模板放 `.git/info/exclude`
- GitHub 认证统一走 `gh`（凭证由 gh 管理，不落配置文件）
- 每次 push 前自动扫描：`.env`、密钥文件、证书、service-account JSON、本地 Spring 配置

## GitHub 贡献图（绿点）速查

提交要被计入贡献图，必须**同时满足**：
1. 提交位于**默认分支**（或通过 PR 合并进默认分支）—— 迁移后绿点不显示的头号原因
2. 提交邮箱与账号**已验证邮箱**匹配（`git log --format='%ae'` vs `gh api user/emails`）
3. 提交日期在**最近一年**内（贡献图是滚动的一年，老历史无法补点）
4. 非 fork 仓库
5. 私有仓库的贡献默认不显示：Profile → Contribution settings 勾选 *Include private contributions*

修复顺序：`gh repo edit <o>/<r> --default-branch master` → 检查邮箱 → 等待 GitHub 重新计算
（缓存可能延迟数小时；必要时推一个空提交触发刷新：`git commit --allow-empty -m "chore: refresh"`）。

## Worked examples

### 示例 A：初始化本地 Git
用户："帮我在这个项目里先初始化 Git，但先不要连远端。"
→ 执行 `local-only`，汇报结果后停止。

### 示例 B：预览提交
用户："帮我看看现在会提交哪些文件，并给我几个提交信息备选。"
→ 执行 `preview` → spawn 子代理分析 diff → 展示分组文件 + 安全发现 + 候选信息 → 等待用户确认。

### 示例 C：确认后推送（单批次）
用户："就用第 2 个提交信息，开始推送。"
→ 执行 `publish`，校验 manifest，推送（GitHub），汇报结果。

### 示例 D：确认后推送（多批次）
用户："第一批用选项 1，第二批用选项 2，第三批用选项 1，帮我推送。"
→ 逐批 `git add` + `git commit`，全部完成后一次 push。

## 配置文件

- `references/config-format.md` — 配置格式与优先级（`~/.config/github-workflow/config.json`）
- `references/gh-cheatsheet.md` — gh CLI 命令速查（官方能力全表）
- `references/commit-rules.md` — 内置提交规范模板
- `references/safety-and-ignore.md` — 安全扫描与忽略策略
- `assets/config.example.json` — 配置模板

## 脚本

- `scripts/github_bootstrap.py` — 核心 bootstrap CLI（local-only / preview / create-remote / publish / profile / adopt-existing-project）
- `scripts/github_issues.py` — Issue 管理 CLI（薄封装 `gh issue`）
- `scripts/lib/` — 共享模块（gh.py / config.py / git.py / preview.py / manifest.py / safety.py / adopt.py / profile.py / remote.py / common.py）
