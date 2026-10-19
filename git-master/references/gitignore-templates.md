# git 忽略规则模板（按本机开发栈定制）

模板源自本机真实踩坑（dc-skills 生产 `.gitignore` 与 `~/.gitignore_global` 的沉淀），按项目栈挑选拼装。git-master M0 初始化新仓库时使用。

## 三层结构

| 层 | 文件 | 放什么 | 入版本控制 |
|----|------|--------|-----------|
| 1 团队共享 | 项目 `.gitignore` | 构建产物、依赖目录、编辑器噪声、平台垃圾 | 是 |
| 2 本机私有 | `.git/info/exclude` | 密钥、证书、本地配置变体（每人不一样，公示文件名等于泄露线索） | 否 |
| 3 机器级兜底 | `~/.gitignore_global`（`core.excludesFile`） | 本机工具运行产物（`skills-output/`、`.codegraph/` 等），跨项目一次配置全生效 | 否 |

第 3 层本机已配置（输出规范 C-14）。团队仓库建议把工具产物再写进项目 `.gitignore` 一份，协作者和其他机器克隆后同样干净；私人仓库只靠全局层即可。

## 通用底座（所有仓库默认带）

```gitignore
# 系统与平台垃圾
.DS_Store
Thumbs.db
desktop.ini

# WSL ↔ Windows 交互痕迹（NTFS Alternate Data Stream 元数据，
# 文件名形如 "file.md:Zone.Identifier"，跨系统拷贝必留）
*:Zone.Identifier

# WSL UNC 测试残留（反斜杠路径被 git 当成单个文件名，须用 * 通配匹配）
*wsl$*
*wsl.localhost*

# IDE 与编辑器
.idea/
.vscode/
*.swp
*.swo
*~

# 日志
*.log
logs/
```

## Python（uv 工作流）

```gitignore
# 虚拟环境（.venv 由 uv.lock + pyproject.toml 重建，不入库）
.venv/
venv/
# .venv 若是指向统一 venv 的符号链接，git 视作普通文件，需单独匹配
*.venv

__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
dist/
build/
```

`uv.lock`、`pyproject.toml` 入库。可复现环境靠锁文件，靠忽略规则只会适得其反。

## Node / TypeScript

```gitignore
node_modules/
dist/
*.tsbuildinfo
```

锁文件（`package-lock.json` / `bun.lock`）入库。

## Go

```gitignore
bin/
coverage.out
*.test
*.prof
```

## Rust

```gitignore
/target/
```

`Cargo.lock` 入库（本仓场景均为应用而非库）。

## LaTeX（毕设/论文编译流水线）

```gitignore
*.aux
*.bbl
*.bcf
*.blg
*.fdb_latexmk
*.fls
*.lof
*.lot
*.out
*.run.xml
*.synctex.gz
*.toc
*.xdv
```

## AI / 模型重型资产

```gitignore
# 技能/项目内独立重型环境（含 GPU 权重，单份 500MB-5GB+）
**/venvs/
```

超百 MB 的模型权重（`*.gguf`、`*.safetensors` 等）不入库也不进 LFS，放机器盘固定路径，文档里记位置。

## 本机工具产物（生态约定，团队仓库写进第 1 层）

```gitignore
.codegraph/
skills-output/
.omo/
.claude/settings.local.json
.state/
tmp/
```

## 敏感规则（写进第 2 层 `.git/info/exclude`）

```gitignore
.env
.env.*
*.key
*.pem
*.p12
*.pfx
credentials*.json
service-account*.json
application-local.yml
```

## 验证与禁止做法

```bash
git check-ignore -v <path>       # 查某文件被哪条规则命中
git status --ignored --short     # 抽查忽略是否生效
```

- 禁止把密钥/证书模式放进 `.gitignore`（公示敏感文件名；放 `.git/info/exclude`）。
- 禁止逐文件列举环境配置变体，用 `.env.*` 通配。
- 禁止把文档型仓库的生成物过滤（`*.png`、`*.pdf`、`*.svg`——dc-skills 先例）搬进代码仓库，会误杀资源图；反向同理。
