# SKILL 输出目录规范（OUTPUT.md）—— 兜底规则

> **定位：兜底（fallback）规则，非强制覆盖。** 仅当技能**未显式指明输出目录**时生效：
> - 技能正文显式指定输出位置 → **以技能为准**（如 paper-reader 输出到输入 PDF 同级的
>   `paper-analysis/`；用户显式传 `--output <dir>` 参数 → 以参数为最高优先）；
> - 技能未显式指明 → 按下文两级回退规则执行。
>
> 代码实现：CLI 脚本统一调用顶层 `scripts/common.py::resolve_output_path`（diagram 家族已接入）。

## 两级回退规则（按优先级）

| 优先级 | 条件 | 输出位置 | 示例 |
|---|---|---|---|
| 1 | 用户 cwd 在工作项目目录（cwd 不在 `~/projects/dc-skills`） | `<工作项目目录>/<skill-output-root>/<skill-name>/<filename>` | `/home/dc/projects/MyThesis/thesis-output/diagram/er-diagram.png` |
| 2（兜底） | cwd 在 `~/projects/dc-skills` 或无明确工作项目 | `~/.claude/skills-output/<skill-name>/<filename>` | `~/.claude/skills-output/diagram/er-diagram.png` |

## skill-output-root 命名

| 技能族 | root | 示例 |
|---|---|---|
| 论文类（diagram、thesis-*、md-to-thesis-latex） | `thesis-output/` | diagram-er |
| 数据库（db-skill） | `db-output/` | db-skill |
| 文档提取（word-extractor、paper-reader） | `doc-output/` | word-extractor |
| 其他 | `<skill-name>-output/` | officecli |

## 输出文件名约定（避免互相覆盖）

| Skill | 输出文件名 |
|---|---|
| diagram（er 类） | `er-diagram.png` |
| diagram（ers 类） | `ers-diagram.png` |
| diagram（module 类） | `module-diagram.png` |
| diagram（sequence 类） | `sequence-diagram.png` |
| diagram（usecase 类） | `usecase-diagram.png` |
| diagram（flow 类） | `flow.mmd` / `flow.png` |
| thesis-writing | `第X章-章节名.md` / `full-thesis.md` |
| md-to-thesis-latex | `main.tex`（thesis-output/latex/ 下） |
| word-extractor | `<basename>.md` + `images/` |
| db-skill | 按 `--output` 显式参数为准 |

## 显式指定优先（本文件不覆盖的场景）

1. 技能正文写明输出位置（如 paper-reader → 输入 PDF 同级 `paper-analysis/<pdf_stem>/`）
2. 用户或调用方显式传入 `--output` / `--output-dir` 参数
3. 项目内 `docs/rules/` 或任务上下文明确指定

## 禁止事项

- ❌ 固定文件名互相覆盖（如所有 diagram 类都叫 `diagram.png`）
- ❌ `/tmp/skills-output/<date>/` 永久堆积（历史垃圾目录的根源）
- ❌ 技能目录内 `-workspace` 孤儿目录
