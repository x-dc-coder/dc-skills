# Agent 技能项目（dc-skills）

## 核心约束：Python 脚本执行目录

**所有 skill 中的 Python 脚本必须先 `cd` 到 `~/projects/dc-skills` 再通过 uv 执行**，绝不能依赖用户当前项目的 `pyproject.toml`。

### 执行规则

```bash
cd ~/projects/dc-skills && uv run python <skill-name>/scripts/<script>.py ...
```

- `cd ~/projects/dc-skills` 确保 uv 的 `pyproject.toml` 解析起点正确
- 技能脚本位于各自的子目录下，如 `github-workflow/scripts/github_bootstrap.py`

### 为什么是 `cd` 而非 `--directory`

`uv run --directory` 只影响 `pyproject.toml` 的查找位置，但**脚本相对路径仍基于当前工作目录解析**。先 `cd` 再执行可以同时保证：pyproject.toml 正确 + 相对路径正确。

### 禁止的做法

```bash
# ❌ 错误：会解析当前项目（如 Words-Production）的 pyproject.toml
uv run python scripts/github_issues.py ...

# ❌ 错误：可能缺少 skills 项目的依赖
python3 scripts/github_issues.py ...

# ❌ 错误：--directory 不会改变相对路径的解析基准
uv run --directory ~/projects/dc-skills python scripts/github_issues.py ...
```

### 为什么需要这个约束

- `uv run` 在当前目录向上查找最近的 `pyproject.toml` 来决定依赖环境
- 用户的项目（如 Words-Production）可能依赖 Windows-only 包（如 `pywin32`），在 WSL/Linux 下无法安装
- `~/projects/dc-skills` 有自己独立的 `pyproject.toml`，所有依赖都是跨平台的
- skill 脚本应该使用 skills 项目的 venv，不依赖也不污染用户项目的环境

## 核心约束：输出目录

所有 SKILL 的文件输出遵循 `OUTPUT.md`（docs/specs/OUTPUT.md，**唯一事实源**）的两级回退规则：
显式指定（`--output`）优先；否则工作项目内落 `<项目>/<skill-output-root>/<skill-name>/`，
主库内（含经农场软链进入）兜底 `~/.claude/skills-output/<skill-name>/`。禁止固定名互相覆盖、
`/tmp/skills-output/` 堆积、技能目录内产物。CLI 统一调用 `scripts/common.py::resolve_output_path`。

## 三条铁律（摘要）

1. **执行目录**：Python 脚本一律先 `cd ~/projects/dc-skills` 再 `uv run`（见上）。
2. **输出位置**：以 `OUTPUT.md` 为唯一事实源（见上）；SKILL.md 引用条款号，不引用行号。
3. **单一物理副本**：技能只存在于主库；各 Agent 经软链农场可见；App 自带技能不收编
   （架构见 `SKILL-MANAGEMENT.md`，启用清单见 `agent-map.yaml`）。
