# SKILL 开发与修改规则

> 本文档基于 2026-07-19 全量 SKILL 审查（23 个修复项）沉淀的规则。后续新增 SKILL 或修改现有 SKILL 行为时**必须**遵守本文档。违反任意一条都可能导致审查被退回。
>
> **规范基线（2026-09-23 修订）**：frontmatter 与目录结构规则对齐 [agentskills.io](https://agentskills.io/specification.md) Agent Skills 开放标准（Anthropic 2025-10 发起，40+ 客户端遵循：Claude Code、Codex、OpenCode、Cursor、Gemini CLI、Grok 等）。凡本文与规范冲突处，以规范的字段全集（6 个）为准；本文的额外约束（行数门禁、触发词防冲突等）是更严的内部标准。

---

## 一、目录与环境

### 1.1 环境分类（A/B/C）

新增 SKILL 前，先确定它属于哪一类：

| 类别 | 条件 | venv 策略 | 示例 |
|------|------|----------|------|
| **A 统一共享** | 含 Python 脚本，依赖轻量（Pillow/sqlglot/psycopg2 等已在 pyproject.toml） | `.venv -> ../.venv` 符号链接 | diagram-er, db-skill, word-extractor |
| **B 独立重型** | 含 GPU 模型权重或大型独立依赖（>1GB） | skill 内 `venvs/` 目录，加入 `.gitignore` | paper-reader |
| **C 无统一 venv** | 纯代码生成 / 调外部二进制 / 使用系统 Python | 无 `.venv` 符号链接 | diagram-flow, wsl-windows-bridge |

**规则 A1**：声明为 A 类的 SKILL **必须**创建 `.venv -> ../.venv` 符号链接，否则 `uv run` 在 skill 子目录内会找不到依赖。
```bash
ln -sf ../.venv <skill-name>/.venv
```

**规则 A2**：C 类 SKILL 若含 `.py` 文件（如 wsl-windows-bridge），必须在 README.md 环境分类表中注明"含 Python 脚本但使用系统 Python"，不可写"无 Python"。

**规则 A3**：新增依赖**必须**先加到 `pyproject.toml` 再 `uv sync`，不可在 skill 内单独 `pip install`。删除依赖前**必须** grep 全仓库确认无 import（参见本次 TODO-D2 教训：cryptography 被误判为未使用，实际被 gitcode-workflow/scripts/lib/ssh_key.py 使用）。

### 1.2 目录结构

**规则 A4**：每个含 Python 的 SKILL **必须**有 `scripts/__init__.py`（空文件即可），否则 `python -m scripts.cli` 依赖 try/except ImportError workaround，脆弱。

**规则 A5**：SKILL 目录结构标准：
```
<skill-name>/
├── SKILL.md              # 必须有 YAML frontmatter
├── scripts/              # Python 脚本
│   ├── __init__.py       # 必须存在（A 类）
│   ├── cli.py            # 主入口
│   └── test_*.py         # 测试（必须）
├── references/           # 可选：按需加载的参考文档（SKILL.md 过长时拆分到此）
├── assets/               # 可选：静态模板/图片/查找表（与 references/ 的文档区分）
├── evals/                # 可选：评估数据（触发问句，见规则 B9）
└── .venv -> ../.venv     # A 类必须
```
文件引用相对 skill 根、只引用一层深。

**规则 A6**：禁止在仓库根目录或 skill 目录内创建 `-workspace/` 形式的临时目录（如 `doubao-vision-workspace/`）。评估产物应放到 `.omo/` 或 gitignored 的临时目录。

---

## 二、SKILL.md 规范

### 2.1 Frontmatter

**规则 B1**：每个 SKILL.md **必须**以 YAML frontmatter 开头：
```yaml
---
name: <skill-name>
description: >
  <一段话描述能力 + 触发场景。不可过短（<10词）或过长（>200词）。>
---
```

`name` 硬约束（agentskills.io 规范，Claude Code/OpenCode/Cursor/Codex 均校验）：
1-64 字符；仅小写字母/数字/连字符；不得以 `-` 开头或结尾；不得含 `--`；**必须与父目录名一致**。

**规则 B2**：`description` 中**必须**明示触发场景，不可只描述能力。例：✅ "当用户需要生成 ER 图、表结构图时触发" ❌ "生成 ER 图"

`description` 长度与写法（2026-09-23 按 agentskills.io 规范修订）：
- 硬上限 **1024 字符**（规范口径；替代原"200 词"说法，中文建议 ≤500 字）
- 官方推荐祈使句写法 "Use when …" / "当用户需要……时使用"，可适度"主动"——列出用户不点名领域时也应触发的情形
- **关键触发词放最前**：Claude Code 的技能 listing 预算仅为上下文 1%，description 超长时先被截断

**规则 B3**：frontmatter 只使用 agentskills.io 规范字段 + `metadata` 扩展。**禁止使用 Claude Code 私有字段**（`when_to_use`、`disable-model-invocation`、`context`、`model`、`paths`、`argument-hint`、`hooks` 等）——它们出了 CC 即失效。各客户端普遍忽略未知字段（不报错），本禁令的目的是**可移植性**而非防报错。

规范字段全集（仅 6 个）：`name`、`description`（以上必需）；`license`、`compatibility`（≤500 字符，环境要求；官方明言多数技能不需要）、`metadata`（string→string map，扩展点）、`allowed-tools`（Experimental，慎用）。注意：**`compatibility` 是规范字段，不在禁止之列**（2026-09-23 修正，旧版规则曾误禁）。

**规则 B3a（推荐字段，2026-08-29 补充；2026-09-23 按规范修订）**：除 `name`/`description` 必填外，推荐声明以下字段：
```yaml
metadata:
  version: "1.0"          # 语义化版本，便于追踪技能变更（规范钦点放 metadata 子键；
                          #   顶层 version 是非规范字段，各端忽略）
  requires-bins: "dot, mmdc"   # 外部依赖声明（字符串值；metadata 规定 string→string，
                          #   数组形式仅自家工具链可读）。工具名与安装方式见 ENVIRONMENT.md
```
- **触发信息一律写进 `description`**（跨端最大兼容）。顶层 `whenToUse`（驼峰）CC/dsh 之外均不识别，CC 的对应字段是下划线 `when_to_use`——确需分离时仅对 dsh/CC 双写，否则废弃驼峰拼写。
- 引入新外部工具时**必须**登记到 `ENVIRONMENT.md` 依赖登记表并在此声明。

### 2.2 触发词设计（防冲突）

**规则 B4**：新增 SKILL 前，**必须**检查现有 SKILL.md 的触发词，避免重叠。检查命令：
```bash
grep -l "<你的触发词>" ~/projects/dc-skills/*/SKILL.md
```

**规则 B5**：如果一个能力已被专用 SKILL 覆盖，通用 SKILL **不可**在其触发词中重复该关键词。曾发现某兜底 SKILL 的 trigger list 含"流程图/架构图/ER图/时序图/模块图"等 40+ 关键词，与多个专用 diagram skill 冲突——已修正为"仅当其他 diagram-* 无法满足时使用"。

**规则 B6**：通用/兜底 SKILL **必须**在 description 中声明优先级规则："仅当其他专用 skill 无法满足时使用本 skill"。

### 2.3 SKILL.md 长度

**规则 B7**：SKILL.md 主文档**应** ≤ 400 行。超过 400 行时**必须**将参考内容拆到 `references/` 子目录。曾发现某兜底 SKILL 主文档 911 行——已拆分为短主文档 + `references/` 子文档。

口径说明（2026-09-23 对齐 agentskills.io）：官方建议 SKILL.md < 500 行、正文 < 5000 tokens（progressive disclosure：元数据启动即载 → 正文激活时载 → 资源按需）。400 行门禁作为**更严的内部标准**保留，行数只是 token 数的 proxy。

**规则 B7a（行数门禁，2026-08-22 补充）**：提交前必须跑以下检查，任何 SKILL.md 超 400 行即阻塞提交：
```bash
cd ~/projects/dc-skills && find . -name SKILL.md -not -path "*/node_modules/*" -not -path "*/.venv/*" -not -path "./archive/*" -not -path "./.git/*" \
  | xargs wc -l | awk '$1 > 400 && $2 != "total" {print "❌ 超限:", $2, "("$1"行)"; bad=1} END {if (!bad) print "✅ 全部 ≤400 行"}'
```

**规则 B9（规范校验与触发评估，2026-09-23 补充）**：
- 命名冲突零容忍：`name` 必须全仓库唯一——Codex 同名不合并（两个都出现在选择器），Grok 按 name 去重（用户级覆盖 bundled）。新增 SKILL 前跑 `ls ~/projects/dc-skills/ | grep <name>`。
- Codex 扫 `.agents/skills`（CWD 逐级到 repo 根）且支持 symlink——skills-sync 软链接农场对 Codex 天然可用；每个 skill 的 name 唯一性即兼容性保障。
- 重要技能（base + 高频 on_demand）在 `evals/` 维护约 20 条 should/shouldn't-trigger 评估问句（官方 optimizing-descriptions 方法论）；有官方校验器 `skills-ref validate`（github.com/agentskills/agentskills）时进 CI，替代等价自研校验。

**规则 B8**：SKILL.md 顶部**应**包含：能力概述、触发场景、依赖说明、快速用法。详细语法/模板/示例放 `references/`。

---

## 三、输出路径约定

**规则 C0（兜底定位，2026-08-29 补充）**：本章为**兜底规则**——技能正文显式指定输出位置（如
paper-reader 输出到输入 PDF 同级）、用户显式传 `--output` 参数时，**以显式指定为准**；仅当技能
未显式指明输出目录时按本章两级回退执行。统一约定全文见 `OUTPUT.md`（唯一事实源，AGENT.md
与本章均引用它）。

### 3.1 禁止 /tmp/skills-output

**规则 C1**：**绝对禁止**在代码或文档中硬编码 `/tmp/skills-output/<date>/<skill>/` 路径。AGENT.md 输出约定明令禁止。

**规则 C2**：所有产出文件的 SKILL **必须**实现两级回退：
```python
def resolve_output_path(input_file, skill_name, default_name):
    if input_file and input_file.is_absolute():
        # 优先：从输入文件推断项目目录
        parent = find_project_root(input_file)
        if parent:
            return parent / "<skill-output-root>" / skill_name / default_name
    # 兜底：cwd 检测
    cwd = Path.cwd()
    if cwd.is_relative_to(Path.home() / ".claude" / "skills"):
        return Path.home() / ".claude" / "skills-output" / skill_name / default_name
    else:
        return cwd / "<skill-output-root>" / skill_name / default_name
```

**规则 C3**：优先复用 `scripts/common.py` 中的 `resolve_output_path` 函数，不可在每个 skill 内重复实现（本次 TODO-C1 已提取 5 个 diagram skill 的共享实现）。

### 3.2 skill-output-root 命名

| SKILL 类型 | skill-output-root | 示例 |
|-----------|------------------|------|
| 论文类（diagram-*、thesis-*、md-to-thesis-latex） | `thesis-output/` | `thesis-output/diagram-er/er-diagram.png` |
| 数据库类（db-skill） | `db-output/` | `db-output/db-skill/result.json` |
| 文档提取类（word-extractor、paper-reader） | `doc-output/` | `doc-output/word-extractor/<basename>.md` |
| 其他 | `<skill-name>-output/` | `unified-search-output/result.json` |

### 3.3 输出文件名

**规则 C4**：每个 SKILL 的默认输出文件名**必须**与 OUTPUT.md 约定表一致，且**必须**包含 skill 标识，避免多 skill 互相覆盖。例：`er-diagram.png`（不是 `diagram.png`）。

**规则 C5**：CLI 参数名**必须**统一为 `--output`（不是 `--out`、`--output-dir`）。保留旧名作为 alias 可接受，但主名必须是 `--output`。

**规则 C6**：所有产出文件的 CLI **必须**支持 `--output <dir>` 显式覆盖回退逻辑，优先级最高。

---

## 四、代码质量

### 4.1 测试

**规则 D1**：所有含 Python 的 SKILL **必须**有 pytest 测试。最低要求：
- 纯函数：每个公开函数至少 1 个测试
- CLI：至少 1 个 `--help` 退出 0 测试
- 文件处理：至少覆盖正常输入 + 边界输入（空文件、编码异常）

**规则 D2**：新增功能（如合并单元格、新参数）**必须**同时新增测试，不可后补。

**规则 D3**：测试**必须**纳入 `pyproject.toml` 的 `testpaths`。当前：`["word-extractor/scripts", "paper-reader/scripts", "paper-metrics/scripts", "thesis-writing/scripts", "unified-search/scripts"]`。

### 4.2 死代码与重复

**规则 D4**：禁止保留"未来可能用到"的死代码。本次审查删除了 5 个 diagram skill 中查找 `scripts/fonts/` 的死代码（该目录从未创建）。

**规则 D5**：跨 skill 重复的逻辑**必须**提取到 `scripts/common.py`。本次审查提取了 5 个 diagram skill 的 `_resolve_output_path`（80% 重复）到共享模块。

### 4.3 错误处理

**规则 D6**：外部二进制依赖（mmdc、dot、d2、pdftoppm）缺失时**必须**优雅报错并给出安装命令，不可直接 `FileNotFoundError` 崩溃。

**规则 D7**：文件读取**必须**处理编码 fallback（UTF-8 → GBK → latin-1），不可只 `read_text(encoding="utf-8")`。

---

## 五、跨平台与可移植性

### 5.1 字体

**规则 E1**：禁止硬编码单一字体名。**必须**提供回退链：
```python
# ✅ 正确
font_candidates = [
    "Noto Sans CJK SC",
    "Noto Sans CJK",
    "SimSun",
    "Source Han Sans SC",
]
# ❌ 错误
fontname = "Noto Sans CJK SC"  # 在仅装 SimSun 的机器上方块
```

**规则 E2**：字体路径回退链**应**包含 Linux + Windows + macOS 路径，但**不可**硬编码跨 skill 引用（如 diagram-ers 曾硬编码引用 diagram-er/scripts/fonts——已删除）。

### 5.2 模板与外部路径

**规则 E3**：硬编码的外部仓库 URL（如 gitee 模板）**必须**提供 fallback：本地已有 > 显式参数 > 网络下载。网络不可达时**必须**优雅报错。

**规则 E4**：硬编码的绝对路径（如 `/home/dc/projects/...`）**绝对禁止**。曾发现某 SKILL 硬编码本地工具包绝对路径——不可移植。

### 5.3 依赖说明

**规则 E5**：SKILL.md **必须**列出所有外部二进制依赖及其安装方式（登记表见 `ENVIRONMENT.md`）：
```
依赖：见 ENVIRONMENT.md 依赖登记表
- dot (graphviz)：apt install graphviz / brew install graphviz
- pdftoppm (poppler-utils)：apt install poppler-utils / dnf install poppler-utils
```
新工具先登记 `ENVIRONMENT.md`，技能内只需声明工具名 + 引用登记表，不重复安装步骤。

---

## 六、修改现有 SKILL 的流程

### 6.1 向后兼容

**规则 F1**：修改 CLI 参数默认值或语义**必须**评估 BREAKING 影响。如果改变默认行为（如 `--downsample` 默认从 True→False），**必须**：
1. 在 SKILL.md 顶部加 `**BREAKING CHANGE**` 段落
2. commit message 加 `BREAKING CHANGE:` 前缀
3. 提供回退路径（如 `--downsample` 显式恢复旧行为）

**规则 F2**：重命名 CLI 参数**必须**保留旧名作为 alias。本次审查将 `--out` → `--output`，保留 `--out` 为 alias。

**规则 F3**：删除 CLI 参数前**必须**确认其在 v2 已废弃且打印 deprecation 警告。本次审查移除 paper-reader 的 `--output`（已废弃但静默忽略）。

### 6.2 文档同步

**规则 F4**：修改代码行为后**必须**同步更新 SKILL.md 中的路径/参数/默认值描述。本次审查发现 5 个 diagram SKILL.md 声明输出 `thesis-output/img/diagram.png`，但代码实际输出 `thesis-output/diagram-er/er-diagram.png`——文档与代码完全脱节。

**规则 F5**：修改输出文件名或路径后**必须**同步更新 OUTPUT.md 的约定表。

### 6.3 跨 skill 影响

**规则 F6**：修改共享代码（如 `scripts/common.py`）后**必须**验证所有依赖该代码的 skill 行为不变。验证方法：用相同 fixture 生成输出，before/after byte-exact 或像素 diff 为 0。

**规则 F7**：修改一个 SKILL 的触发词后**必须**检查是否影响其他 SKILL 的触发。检查命令：
```bash
grep -l "<你的触发词>" ~/projects/dc-skills/*/SKILL.md
```

---

## 七、提交前检查清单

新增或修改 SKILL 后，提交前**必须**逐项确认：

### 环境与结构
- [ ] `.venv` 符号链接存在（A 类）或正确无 venv（B/C 类）
- [ ] `scripts/__init__.py` 存在（A 类）
- [ ] 新依赖已加到 `pyproject.toml` 并 `uv sync`
- [ ] 无 `-workspace/` 孤儿目录
- [ ] 无 NTFS Zone.Identifier 污染文件

### SKILL.md
- [ ] YAML frontmatter 含 `name` + `description`
- [ ] 触发词不与现有 SKILL 冲突（已 grep 验证）
- [ ] SKILL.md ≤ 400 行（超长已拆到 `references/`）
- [ ] 外部依赖已列出安装方式

### 代码
- [ ] 无 `/tmp/skills-output` 硬编码（grep 验证）
- [ ] 实现了两级回退输出路径（或复用 `scripts/common.py`）
- [ ] `--output` 参数支持显式覆盖
- [ ] 无硬编码单一字体（有回退链）
- [ ] 无硬编码绝对路径
- [ ] 外部二进制缺失时优雅报错
- [ ] 无死代码（未使用的 fonts/ 查找等）

### 测试
- [ ] 新增功能有对应 pytest 测试
- [ ] `cd ~/projects/dc-skills && uv run pytest` 全绿
- [ ] 测试路径已加到 `pyproject.toml` testpaths

### 文档同步
- [ ] SKILL.md 的路径/参数/默认值与代码一致（grep 对照）
- [ ] OUTPUT.md 约定表与实际输出文件名一致
- [ ] README.md 环境分类表与实际目录结构一致

### BREAKING CHANGE（如适用）
- [ ] SKILL.md 顶部有 BREAKING 标记
- [ ] commit message 有 `BREAKING CHANGE:` 前缀
- [ ] 提供了回退路径
- [ ] before/after 行为对比已验证

---

## 八、常见陷阱（基于本次审查）

| 陷阱 | 教训 | 规则 |
|------|------|------|
| 误判依赖未使用 | cryptography 被认为全仓库无 import，实际被 ssh_key.py 使用 | A3: 删除前 grep 全仓库 |
| 文档与代码脱节 | 5 个 diagram SKILL.md 声明错误路径，代码正确 | F4: 改代码同步改文档 |
| 死代码累积 | 5 个 skill 查找从未创建的 fonts/ 目录 | D4: 禁止保留死代码 |
| 重复实现 | 5 个 skill 各有一份 80% 相同的函数 | D5: 提取共享模块 |
| 参数语义不一致 | 4 个 diagram skill 的 --downsample 默认值/参数名各不同 | F1: BREAKING 评估 |
| 测试缺失 | word-extractor 和 paper-reader 零测试 | D1: 必须有测试 |

---

## 九、快速参考

### 新增 SKILL 的最小步骤
1. 确定环境分类（A/B/C）
2. 创建目录 + SKILL.md（frontmatter + 触发场景 + 依赖说明）
3. 创建 `scripts/__init__.py` + `scripts/cli.py`
4. 创建 `.venv` 符号链接（A 类）
5. 实现 `--output` 参数 + 两级回退（复用 `scripts/common.py`）
6. 写 pytest 测试
7. 更新 README.md 环境分类表
8. grep 检查触发词冲突
9. 运行提交前检查清单

### 修改 SKILL 行为的最小步骤
1. 评估是否 BREAKING（改默认值/参数语义 = BREAKING）
2. 改代码
3. 同步改 SKILL.md（路径/参数/默认值）
4. 改或新增测试
5. 验证 `uv run pytest` 全绿
6. 检查跨 skill 影响（共享代码、触发词）
7. BREAKING 则加标记 + commit 前缀 + 回退路径

---

*本文档由 2026-07-19 SKILL 全量审查（23 修复项 / 5 commits / 39 tests）沉淀。维护者：DC。*
