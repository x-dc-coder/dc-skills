# Claude Code Skills 统一环境管理

本文档说明本目录下 Python Skill 的统一环境管理方式。

> **跨设备搭建手册**：新设备（Linux/WSL 或 Windows）从零配置环境，见 [CROSS-DEVICE-SETUP.md](CROSS-DEVICE-SETUP.md)。

> **注意**：本文件为辅助说明文档，不影响 Claude Code/OpenCode 的 Skill 系统。各子目录中的 `SKILL.md`（需含 YAML frontmatter）才是 Skill 的规范定义文件。

## 目录结构

```
~/projects/dc-skills/
├── README.md              # 本说明文档
├── CLAUDE.md              # 执行约束（cd ~/projects/dc-skills 再 uv run）
├── ENVIRONMENT.md         # 外部工具依赖登记表（12 工具 + 验证命令 + 声明模板）
├── OUTPUT.md              # 输出目录兜底规范（两级回退，唯一事实源）
├── SKILL-AUTHORING-RULES.md # SKILL 开发与修改规则
├── pyproject.toml / uv.lock / .venv/   # 统一依赖与虚拟环境（轻量 skill 共享）
│
├── lark-cli/              # 飞书/Lark 聚合技能（23 域统一入口）
│   ├── SKILL.md           # 路由表 + 共享底座 + 维护命令
│   └── resources/         # CLI 不内嵌的机器资源（scripts/创意资产/模板）
├── diagram/               # 图表聚合技能（7 类统一入口）
│   ├── SKILL.md           # 路由表 + 通用规范
│   └── references/        # 各类型规程分册（er/ers/module/usecase/sequence/flow/draft）
│
├── diagram-er/            # ⚠️ 非独立技能：聚合技能的脚本目录（单表 ER 图）
│   ├── .venv -> ../.venv  # 符号链接到统一环境
│   └── scripts/           # -m scripts.cli 入口 + renderer/parser/models
├── diagram-ers/           # 多实体 ER 图脚本目录（ECharts/Playwright 或 Pillow）
├── diagram-module/        # 功能模块树形图脚本目录
├── diagram-sequence/      # UML 时序图脚本目录（mmdc 渲染）
├── diagram-usecase/       # UML 用例图脚本目录
├── diagram-draft/         # ASCII 架构草稿（graph-easy render.sh，无 Python）
├── diagram-flow/          # Mermaid 流程图（纯代码输出，无脚本）
│
├── design-ui/             # 前端页面设计（原 artifact-design）
├── design-diagram/        # Artifact 内联 SVG 图表（原 artifact-diagramming）
├── design-dataviz/        # 数据可视化设计系统（原 dataviz）
│
├── db-skill/              # MySQL/PostgreSQL 工具
├── newapi-management/     # NewAPI 渠道与日志管理（REST API 客户端）
├── word-extractor/        # .docx 提取
├── unified-search/        # 7 源聚合搜索（含 keenable 源，原 keenable-cli 已并入）
├── github-workflow/       # GitHub-first git 工作流（gh CLI）
├── drawio-xml/            # .drawio 图表（MCP 协同，npx 按需拉取）
├── officecli/             # Office 文档（docx/xlsx/pptx）
├── kimi-webbridge/        # 真实浏览器控制（daemon）
├── vision-workflow/       # 视觉任务编排（Vision MCP）
├── thesis-writing/  thesis-ref-check/  md-to-thesis-latex/   # 论文写作族
├── paper-metrics/         # 论文写作特征指标层（纯 stdlib 确定性测量；Mode B 上游）
│
├── paper-reader/          # ⚠️ 例外：持有独立重型 venvs
│   └── venvs/             # marker ~5.1GB / mineru ~613MB（GPU 模型权重，不共享）
└── wsl-windows-bridge/    # WSL→Windows 桥接（系统 Python，不纳入统一 .venv）
```

## 三类环境策略

| 类别 | 环境 | 适合的 Skill | 说明 |
|------|------|-------------|------|
| **A. 统一共享** | `.venv/` 符号链接 | diagram 聚合的脚本目录（diagram-er/ers/module/sequence/usecase）、db-skill、word-extractor、unified-search、github-workflow、thesis-writing、paper-metrics、newapi-management | 依赖轻量（Pillow/sqlglot/psycopg2/requests/pytest 等），共享一份 venv |
| **B. 独立重型** | skill 内 `venvs/` | paper-reader | 含 GPU 模型权重（5GB+），不可合并，`.gitignore` 已忽略 |
| **C. 无统一 venv** | 系统/Windows Python 或二进制 | diagram-draft/flow、design-ui/diagram/dataviz（纯文档）、lark-cli（Node）、drawio-xml（npx）、officecli（二进制）、kimi-webbridge、md-to-thesis-latex、thesis-ref-check、wsl-windows-bridge | 无统一 venv（系统/Windows Python 或自带运行时） |

## 统一执行约定（所有 Python skill 必须遵守）

**所有 Python 脚本必须先 `cd ~/projects/dc-skills` 再用 `uv run` 执行**，绝不依赖用户当前项目的 `pyproject.toml`。

### 标准格式

```bash
cd ~/projects/dc-skills && uv run python <skill-name>/scripts/<script>.py ...
```

### 为什么是 `cd` 而非 `--directory`

`uv run --directory` 只影响 `pyproject.toml` 的查找位置，但**脚本相对路径仍基于当前工作目录解析**。先 `cd` 再执行可同时保证：pyproject.toml 正确 + 相对路径正确。

### 例外：diagram 聚合技能的 `scripts.cli` 模块

diagram 聚合技能（er/ers/module/sequence/usecase 五类）使用 Python 包形式（`-m scripts.cli`），
脚本保留在原 `diagram-*` 目录，需进入对应子目录执行：

```bash
cd ~/projects/dc-skills/diagram-er && uv run python -m scripts.cli ...
```

这是因为 `-m scripts.cli` 要求 `scripts/` 在 cwd 下；脚本内 `sys.path` 引用顶层 `scripts/common.py`
（`resolve_output_path`，实现 OUTPUT.md 两级回退）。其 `.venv` 符号链接确保 uv 仍解析到统一环境。
类型路由与使用规范见 `diagram/SKILL.md`。

## 环境管理命令

### 添加新依赖

```bash
cd ~/projects/dc-skills
uv add <package-name>
```

### 同步环境（新机器/重装后）

```bash
cd ~/projects/dc-skills
uv sync
```

### 验证环境

```bash
cd ~/projects/dc-skills
uv run python -c "import PIL, sqlglot, psycopg2, pymysql; print('OK')"
```

### 重建符号链接（新机器/重装后必做）

```bash
cd ~/projects/dc-skills
for skill in diagram-er diagram-ers diagram-module diagram-sequence diagram-usecase db-skill; do
  ln -sf ../.venv "$skill/.venv"
done
# 注：diagram-er 等为 diagram 聚合技能的脚本目录（非独立技能）
```

### paper-reader 重型 venv 重建

```bash
bash ~/projects/dc-skills/paper-reader/scripts/bootstrap.sh
```

## 工作目录依赖（重要）

`uv run` 通过当前工作目录查找 `pyproject.toml` 来定位虚拟环境。以下场景需注意：

1. **在 skills 目录内运行（推荐）**
   ```bash
   cd ~/projects/dc-skills
   uv run python db-skill/scripts/pg_tool.py ...
   ```
   正常找到虚拟环境。

2. **在临时/外部目录运行（错误）**
   ```bash
   cd /tmp
   uv run python ~/projects/dc-skills/db-skill/scripts/pg_tool.py ...
   ```
   `uv` 在 `/tmp` 找不到 `pyproject.toml`，会使用系统默认 Python，导致依赖缺失（如 `psycopg2` 找不到）。

3. **子进程调用时应使用 `sys.executable`**
   当脚本需要作为子进程在其他目录运行时，应使用当前 Python 解释器的绝对路径，而非 `uv run`：
   ```python
   import sys
   subprocess.run([sys.executable, "db-skill/scripts/pg_tool.py", ...])
   ```

4. **db-skill 配置发现也依赖 cwd**
   `mysql_tool.py` 和 `pg_tool.py` 会从当前工作目录向上查找 `.db-skill/mysql.json` 或 `.db-skill/pg.json`。在项目根目录下运行才能正确发现配置，或使用 `--config` 显式指定。

## 当前依赖

| 包名 | 用途 |
|------|------|
| `Pillow` | 图片生成与处理（diagram 聚合：er/ers/module/usecase 脚本） |
| `sqlglot` | SQL 解析（diagram 聚合：er 脚本） |
| `python-docx` | .docx 提取（word-extractor） |
| `psycopg2-binary` | PostgreSQL（db-skill） |
| `pymysql` | MySQL（db-skill） |
| `cryptography` | 加密（db-skill） |
| `requests` | HTTP 请求（kimi-web-search 已删，unified-search 等） |
| `beautifulsoup4` | HTML 解析 |
| `openai` | OpenAI 兼容 API |
| `httpx` | 异步 HTTP |
| `pyyaml` | YAML 解析 |
| `feedparser` | RSS/Atom 解析 |

Node 工具（非 Python 依赖，完整登记表见 ENVIRONMENT.md）：
- `@mermaid-js/mermaid-cli`（mmdc）：用于 diagram 聚合的 sequence 类渲染 Mermaid 到 PNG（推荐用 `npx` 调用，避免全局安装）。
- `lark-cli`：lark-cli 聚合技能硬依赖（Node 全局安装）。
- `keenable`：unified-search 硬依赖（安装/认证/MCP 配置见 unified-search/references/keenable-setup.md）。

## WSL ↔ Windows 桥接 Skill

以下 skill 需要 Windows 侧执行，不适用于纯 Linux 环境：

| Skill | Windows 依赖 | 机制 |
|------|------------|------|
| `paper-reader` | `E:\venvs\marker`、`E:\venvs\mineru` | `paper_reader.py` 自动检测 WSL，通过 `cmd.exe` 桥接 GPU 到 Windows 原生 Python |
| `wsl-windows-bridge` | `cmd.exe`、`powershell.exe`、`reg.exe` 等 | 三层桥接：cmd.exe(~55ms) / Direct EXE(~10ms) / PowerShell(~600ms) + GPU 资源治理（GpuLimits + GpuGovernor） |

## 新增含 Python 的 Skill 时的 checklist

1. 在该 Skill 目录下创建符号链接：
   ```bash
   ln -sf ~/projects/dc-skills/.venv ~/projects/dc-skills/<new-skill>/.venv
   ```
2. 如果新 Skill 需要新的 Python 包，在 `~/projects/dc-skills/pyproject.toml` 中添加依赖，然后执行 `uv lock` 或 `uv sync`。
3. 如果新 Skill 依赖 GPU/重型模型（如 paper-reader），保持独立 `venvs/`，并在 `.gitignore` 中添加忽略条目。
4. SKILL.md **必须**以 YAML frontmatter 开头（`---` 包裹的 `name` + `description`），否则 OpenCode 无法识别。
5. 更新本 README 的依赖列表和环境分类表。
