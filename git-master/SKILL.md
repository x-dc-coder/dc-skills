---
name: git-master
description: "git 与 GitHub 全流程：从本地提交到 CI/CD 发布。当用户要 commit/提交（原子分组、中文优先提交信息）、rebase/squash/强推重写本地与远程历史、blame/bisect 考古回答查谁写的与何时引入、初始化仓库、push/推送到远端、用 gh 指令操作 GitHub 的 PR/issue/release、管理 GitHub Actions 的 CI/CD（workflow 编写、自动打包发布、自建站点部署、失败通知）时使用。新仓库用 Conventional Commits 中文起步；已有仓库提交沿用该仓库既有风格。"
metadata:
  family: software
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Git Master — git 与 GitHub 全流程

本技能覆盖从本地提交到远端生命周期再到 CI/CD 的完整链路。纯代码编辑、崩溃调试、测试审计由 programming 入口路由表分流到对应成员，本技能不接。

## Mode Gate

| 模式 | 进入条件（用户话术） | 职责 |
|------|---------------------|------|
| M0 盘点·初始化 | 「现在什么状态」「初始化仓库」「建库」 | 侦察现状；非 git 目录直接初始化本地仓库，远端须确认 |
| M1 考古 | 谁写的、何时引入、哪个提交改的 | 历史问题按工具作答，只读 |
| M2 提交 | 「提交一下」「commit」 | 原子分组提交，中文优先信息 |
| M3 推送 | 「推上去」「提交并推送」 | 推前自查，分组提交后 push |
| M4 重写历史 | rebase、squash、强推、改历史 | 本地整理与远程历史改写（lease 协议） |
| M5 GitHub 指令 | PR、issue、release 的增删改查 | 纯 gh 指令表 |
| M6 CI/CD · Actions | actions、CI、发布、流水线、部署 | workflow 管理、自动打包发布、自建站点部署 |

先归类唯一模式再动手；M3 复用 M2 的分组规则；M0/M1 只读。

## M0 盘点·初始化

```bash
git rev-parse --is-inside-work-tree   # 非 0 = 当前目录不是 git 仓库
git status --short
git diff --stat
git log --oneline -10
git rev-parse --abbrev-ref @{upstream}
```

**已是仓库**：汇报分支、改动、ahead/behind，然后停。只读，不改任何东西。

**不是仓库**：直接初始化（无需确认）——

1. `git init -b master`
2. 身份：`git config user.name x-dc-coder`、`git config user.email x.dc0521@gmail.com`
3. `git config core.quotepath false`（中文文件名不转义）
4. 写三层忽略（模板按栈取自 [references/gitignore-templates.md](references/gitignore-templates.md)，均为本机实战沉淀）：项目 `.gitignore` 放团队共享规则——通用底座（含 WSL 交互垃圾 `*:Zone.Identifier` 与 UNC 残留）加项目栈对应的 Python/Node/Go/Rust/LaTeX 段；`.git/info/exclude` 放本机敏感规则（`.env`、`*.key`、`*.pem`、`credentials*.json`），不入版本控制；机器级工具产物（`skills-output/`、`.codegraph/` 等）由 `~/.gitignore_global` 兜底（输出规范 C-14），团队仓库在项目层再写一份让协作者同样干净。

汇报初始化结果后**询问是否创建 GitHub 远端，等用户确认**。确认且 `gh auth status` 通过后：`gh repo create <name> --private --source=. --remote=origin`，首推走 M3。

## M1 考古

| 问题 | 工具 |
|------|------|
| 这几行谁最后改的 | `git blame -L <start>,<end> -- <file>` |
| 某字符串出现/消失的提交 | `git log -S "<text>"` |
| diff 触及匹配行的提交 | `git log -G "<regex>"` |
| 文件跨重命名的完整历史 | `git log --follow -- <file>` |
| 看某个提交改了什么 | `git show <hash>` |
| 找首次变坏的提交 | `git bisect`（需确定性的通过/失败命令与好坏边界） |
| 本地历史怎么丢的 | `git reflog` |

## M2 提交

```bash
git log -20 --pretty=format:%s     # 探测仓库既有风格
git add <paths>                    # 按组暂存
git diff --staged --stat           # 暂存后核对分组正确
git commit -m "<type>(<scope>): <中文 subject>"
git log -1 --oneline               # 提交后核对
```

流程：探测风格 → 读全 diff（不只看文件名）→ 按行为/模块/可回退性分原子组 → 逐组暂存提交。实现与其直接测试同组；无关脏改动原样保留，不混入。最终汇报提交 hash 列表与遗留未提交文件。

**提交信息规范（中文优先）**：格式 `<type>(<scope>): <中文 subject>`。type/scope 用英文：`feat fix docs style refactor perf test chore build ci revert`；subject 中文优先，不超过 50 字符，动宾结构，结尾无标点；英文专业术语保留原文（token、middleware、API 名），不强行翻译；body 可选，中文，每行不超过 72 字符，与 subject 空一行；禁 `Co-Authored-By`。已有仓库沿用其既有风格；仓库风格混杂或新仓库时用本规范。暂存前扫一眼 `.env`、密钥、证书不进库。

**备份文件检查（M2 暂存前、M3 推送前共用）**：扫备份残留，命中即提示，绝不默认混入提交——

```bash
find . \( -name .git -o -name node_modules -o -name venvs -o -name .venv -o -name __pycache__ \) -prune -o \
  -type f \( -name '*.bak' -o -name '*.bak.*' -o -name '*.orig' -o -name '*.rej' -o -name '*~' \) -print
```

`*.swp` 排除（可能是打开中的编辑会话）。命中后列出数量、总大小、最老的一个，按判定给建议：原始文件（去掉备份后缀的同名文件，如 `a.py.bak` → `a.py`）存在且比备份新 → 已过期，建议删；备份比原始新 → 可能是有用回滚点，停下问用户；无原始文件的孤儿备份 → 超 30 天建议删，否则保留。用户确认后 `rm -- <files>`；已被 git 跟踪的改用 `git rm --cached` 并补 ignore 规则。确认前只报告，不动手。

## M3 推送

```bash
git fetch --dry-run                # 远端有无新提交
git status --short                 # 与会话开始时一致
git push -u origin master          # 首推
git push                           # 日常
```

未提交的改动先按 M2 分组逐批提交（一条提交一条信息），然后一次 push。**push 成功判定**：输出含 `To github.com:...` 与 `master -> master`（或对应分支更新行）。

- 推前自查：工作区状态与预期一致；改动里无 `.env`/密钥/证书/service-account JSON；无备份文件混入（`*.bak`/`*.bak.*`/`*.orig`/`*.rej`/`*~`，检查与判定规则见 M2，含在待推 diff 与未跟踪文件里都算命中）。
- 9p/drvfs 挂载（/mnt/*）上 `git push -u` 可能报 EPERM（无法写 `.git/config`）：退普通 `git push`，内容照常推送。
- 默认分支禁 `--force`；改写已推送历史见 M4。

## M4 重写历史

**本地整理**：

```bash
git commit --fixup=<hash>                                  # 标记待并提交
GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>    # 自动并拢
git rebase --abort                                         # 出错先退，再议
```

冲突读代码按意图解，禁止盲目 ours/theirs；worktree 脏先安顿（commit 或 stash，恢复 stash 前核对内容）。整理完跑项目最便宜的冒烟检查，再展示从 base 到 HEAD 的新日志。

**远程历史改写**：远程历史就是远程分支指针，本地改写后强推即生效。三类代价必须先讲清：协作者与其他机器基于旧历史的工作会分叉（需 `git fetch && git reset --hard origin/<branch>` 对齐）；被替换的旧提交不再从默认分支可达，对应日期贡献绿点消失；为删密钥而改写时旧提交在 GitHub 按 SHA 仍可能被访问，真删除靠作废并轮换密钥。

```bash
git fetch                                       # 先确认远程当前位置
git branch backup/<branch>-<日期>                # 改写前备份
git push --force-with-lease                     # 绝不用裸 --force
git push --force origin <tag>                   # tag 指向旧提交时重打
```

改写后清单：重打受影响的 tag；其他机器同步 reset；CI 跑一遍。

**分级授权**：特性分支（feat/fix 等）整理后随时 lease 强推，常规操作；默认分支 master 默认拒绝改写，仅当用户显式点名且确认为单人仓库时执行，先备份。

## M5 GitHub 指令库

仓库目录下运行，owner/repo 自动推断。登录一次全表可用：`gh auth login`；`gh auth status` 查认证。

### 仓库

| 意图 | 命令 |
|------|------|
| 新建私有仓库并关联本地 | gh repo create <name> --private --source=. --remote=origin |
| 新建公开仓库 | gh repo create <name> --public --source=. --remote=origin |
| 改默认分支 | gh repo edit <o>/<r> --default-branch <b> |
| 列我的仓库 | gh repo list --limit 30 |
| 看仓库信息 | gh repo view --web |

### Issue

| 意图 | 命令 |
|------|------|
| 列出 | gh issue list --limit 30 |
| 创建 | gh issue create --title "<标题>" --body "<正文>" |
| 查看 | gh issue view <n> |
| 评论 | gh issue comment <n> --body "<内容>" |
| 关闭 / 重开 | gh issue close <n> / gh issue reopen <n> |

### PR

| 意图 | 命令 |
|------|------|
| 创建（自动填标题正文） | gh pr create --fill |
| 创建（指定基线分支） | gh pr create --base <b> --title "<标题>" --body "<正文>" |
| 查看 | gh pr view / gh pr view --web |
| CI 状态 | gh pr checks |
| 合并 | gh pr merge --merge |
| 关闭 / 重开 | gh pr close <n> / gh pr reopen <n> |

### Release

| 意图 | 命令 |
|------|------|
| 手动发版（附产物 + 自动 changelog） | gh release create v<版本> dist/* --generate-notes |
| 列出 / 查看 | gh release list / gh release view <tag> |

PR 正文截图走网页附件流（粘贴进编辑框得 user-attachments 链接），规程见 [references/github-attachment-upload.md](references/github-attachment-upload.md)；提交信息里 `fix #N` 可关联关闭 issue，推送默认分支时生效。

## M6 CI/CD · Actions

| 概念 | 含义 |
|------|------|
| Workflow | 仓库 `.github/workflows/` 下一个 YAML 文件 = 一条流水线，推送即生效 |
| 触发器 on | push、pull_request、tags（推 `v*` 发版）、schedule（cron）、workflow_dispatch（手动） |
| Job | 一组步骤，跑在一台 runner 上；`needs:` 编排先后，默认并行 |
| Step | 一条 shell 命令，或一个复用 Action（如 `actions/checkout@v4`） |
| Runner | GitHub 托管执行机；公共仓库免费，私有仓库每月 2000 分钟额度 |
| Secrets | 仓库设置里存密钥，YAML 中以 `secrets.X` 引用，不进代码 |

### 管理指令

| 意图 | 命令 |
|------|------|
| 查运行历史 | gh run list --limit 10 |
| 看失败日志 | gh run view <run-id> --log-failed |
| 实时盯运行 | gh run watch |
| 重跑 / 取消 | gh run rerun <run-id> / gh run cancel <run-id> |
| 下载产物 | gh run download <run-id> |
| 列出 / 查看 workflow | gh workflow list / gh workflow view <名> |
| 手动触发 | gh workflow run <名> |
| PR 的 CI 状态 | gh pr checks |

### 两条主流程

**CI 门禁**：从 [references/actions-recipes.md](references/actions-recipes.md) 复制 `ci.yml` 到项目 `.github/workflows/`（按栈裁剪矩阵），push/PR 自动跑 lint + 测试；首跑 `gh run watch` 盯，红了 `gh run view --log-failed` 定位；PR 合并前 `gh pr checks` 全绿。

**自动打包发布**（tag 驱动，一条命令到上线）：

1. CI 绿后 `git tag v<版本> && git push origin v<版本>`
2. `release.yml` 自动执行：构建 → 建 GitHub Release（提交记录生成 changelog）→ 发 PyPI（Trusted Publishing 免 token）→ rsync 部署自建站点 → 失败推 webhook 通知
3. 手动兜底：`gh release create v<版本> dist/* --generate-notes`

### 密钥纪律（红线）

- 全部凭证走 Secrets；代码、日志、提交信息不出现明文密钥。
- 第三方 Action 只 pin 版本 tag（`@v4`），不 pin `@master`；不认识的 Action 先看源码再引入。
- 部署密钥专用（不与个人登录密钥混用），泄露立即删服务器公钥并换 Secrets。
- `environment: production` 可加人工审批门；PyPI 发布需一次性配置 Trusted Publisher。

## 安全红线（全模式）

1. 不 commit、rebase、push、force-push、reset、stash-pop、删除任何东西，除非用户明确要求该操作；纯调查请求报告后即停。
2. 默认分支（master/main）禁裸 `--force`；改写已推送历史必须 `--force-with-lease` 且用户显式要求。
3. 暂存与推送前扫 `.env`、密钥文件、证书、service-account JSON；已泄露的密钥靠轮换作废，改写历史只是辅助。
4. 忽略规则三层分工：团队共享放 `.gitignore`，本机敏感规则放 `.git/info/exclude`，机器级工具产物由 `~/.gitignore_global` 兜底；密钥类模式禁入 `.gitignore`，用通配符不逐文件列举。

## references

- [references/actions-recipes.md](references/actions-recipes.md) — CI / 发布 / 自建部署 / 失败通知模板与 Secrets 配置
- [references/gitignore-templates.md](references/gitignore-templates.md) — 三层忽略规则与本机栈定制模板
- [references/github-attachment-upload.md](references/github-attachment-upload.md) — PR 证据附件上传规程
