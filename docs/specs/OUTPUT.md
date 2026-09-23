# SKILL 输出目录规范（OUTPUT.md）—— 兜底规则

> **定位：兜底（fallback）规则，非强制覆盖。** 仅当技能**未显式指明输出目录**时生效：
> - 技能正文显式指定输出位置 → **以技能为准**（如 paper-reader 输出到输入 PDF 同级的
>   `paper-analysis/`；用户显式传 `--output <dir>` 参数 → 以参数为最高优先）；
> - 技能未显式指明 → 按下文两级回退规则执行。
>
> 代码实现：CLI 脚本统一调用 `~/projects/dc-skills/scripts/common.py` 的 `resolve_output_path` /
> `fallback_output_dir`（diagram 家族、db-skill、word-extractor、unified-search 已接入）。
> 2026-09-24 修订：判定基准修正为**主库根**（C-1），删除 `docs/` 启发式（C-2），
> 新增中间产物（C-5）、临时文件（C-6）、防覆盖（C-7）、跨设备（C-9）条款。

## C-1 两级回退规则（按优先级）

判定基准是**主库根** `~/projects/dc-skills`（解析后的物理路径）。经农场软链
（`~/.claude/skills/<skill>` 等）进入时，`Path.cwd().resolve()` 回到主库，同样判定为
“主库内”，走兜底——不会把产物写进农场目录，也不会写进 git 仓库。

| 优先级 | 条件 | 输出位置 | 示例 |
|---|---|---|---|
| 1 | 用户 cwd 在工作项目目录（cwd 不在主库内） | `<工作项目目录>/<skill-output-root>/<skill-name>/<filename>` | `/home/dc/projects/MyThesis/thesis-output/diagram-er/er-diagram.png` |
| 2（兜底） | cwd 在主库内（含经农场软链）或无明确工作项目 | `~/.claude/skills-output/<skill-name>/<filename>` | `~/.claude/skills-output/diagram-er/er-diagram.png` |

有绝对路径输入文件的技能（diagram 家族）：先从输入文件向上查找**项目根标记**（见 C-2），
命中则用该项目根走优先级 1；未命中再按 cwd 判定。

## C-2 项目根标记（替代原 docs/ 启发式）

项目根的显式标记 = 该目录下已存在对应的 `<skill-output-root>` 目录（如 `thesis-output/`）。
从输入文件向上逐级查找，**遇到主库根即止**（主库内的同名目录不是项目信号）。

- ❌ 删除原 `docs/` 启发式：`docs/` 是极常见目录名，曾把输出写进技能自己的示例目录
  （`diagram-er/docs/`），且反向漏判真实项目。
- 输入文件不在任何带标记的项目内 → 按 C-1 的 cwd 规则处理。

## C-3 skill-output-root 与 skill-name 口径

- 优先级 1 的**最后一段恒为技能名**（脚本目录名，如 `diagram-er`）；`<skill-output-root>`
  才是类型目录。
- 优先级 2 的最后一段同样恒为技能名。

| 技能族 | root | 示例 |
|---|---|---|
| 论文类（diagram 各子类型、thesis-*、md-to-thesis-latex） | `thesis-output/` | `thesis-output/diagram-er/` |
| 数据库（db-skill） | `db-output/` | `db-output/db-skill/` |
| 文档提取（word-extractor、paper-reader） | `doc-output/` | `doc-output/word-extractor/` |
| 其他 | `<skill-name>-output/` | `officecli-output/` |

## C-4 输出文件名约定（避免互相覆盖）

| Skill | 输出文件名 |
|---|---|
| diagram（er 类） | `er-diagram.png` |
| diagram（ers 类） | `ers-diagram.png` |
| diagram（module 类） | `module-diagram.png` |
| diagram（sequence 类） | `sequence-diagram.png` |
| diagram（usecase 类） | `usecase-diagram.png` |
| diagram（flow 类） | `flow.mmd` / `flow.png` |
| diagram（draft 类） | `draft-<basename>.png/svg/html`（**必须含 draft 前缀**，禁止裸 `diagram.png`） |
| thesis-writing | `第X章-章节名.md` / `full-thesis.md` |
| md-to-thesis-latex | `main.tex`（`thesis-output/latex/` 下） |
| thesis-ref-check | `<stem>_修正版.md` + `_state/`（可保留中间产物，见 C-5） |
| thesis-export | `out/<学校>/<时间戳>/导出.docx` |
| word-extractor | `<basename>.md` + `images/` |
| paper-reader | `paper-analysis/<pdf_stem>/`（例外：输入 PDF 同级，不走两级回退） |
| db-skill | 按 `--output` 显式参数为准 |

## C-5 中间产物目录

- 中间产物统一落 **`<输出根>/.work/<skill-name>/`**（与最终产物同根，便于整体打包/清理）；
  `.work/` 已在主库 `.gitignore` 登记，各工作项目自行决定是否忽略。
- 需要跨会话保留的中间产物（如 `thesis-ref-check/_state/`）视为**半永久资产**，须在
  SKILL.md 明示保留窗口与清理方式。
- ❌ 禁止在技能目录内、仓库根、或 `/tmp` 持久化中间产物。

## C-6 临时文件与受控 /tmp 例外

- 临时文件必须用 `tempfile` / `$TMPDIR`，**生命周期绑定进程**，退出即删
  （`debugging` 技能的 journal + cleanup 纪律为正面范式）。
- 需要 `/tmp` 持久的场景实行**登记制**，当前登记两例：

| 位置 | 使用者 | 理由 | 清理时机 |
|---|---|---|---|
| `/tmp/gpu-logs/` | wsl-windows-bridge | inotify 需 Linux 本地 FS（WSL#4739） | 随任务结束 |
| `/tmp/dsh-repo/` | dsh-plugin-troubleshooting | 官方仓库离线克隆 | `git pull` 前手工确认 |

## C-7 防覆盖与复用/清理

- 默认名**必须含技能标识或输入 basename**（C-4 表即最低要求）。
- 同一会话多次执行的产物（如 db-skill 的结果文件）：必须提供 `--reuse` 或写入固定
  `latest.json`，并在会话结束清理随机名文件——禁止用 `mkstemp` 随机名当最终产物无限堆积。
- 输出根允许整体删除：产物须可从输入重现；**用户资产类**（论文正文、总结、修正稿）
  显式豁免。

## C-8 与 AGENT.md 的衔接

`AGENT.md`（本文件上级目录的根级文件，`CLAUDE.md`/`AGENTS.md` 为其兼容软链）只保留**三条铁律摘要 +
指向本文件**；本文件是输出规范的唯一全文。技能 SKILL.md 引用输出规则时引用本文件条款号
（如 C-5），不引用行号。

## C-9 跨设备路径

- WSL 侧读写 Windows 盘一律用 `/mnt/<盘符>/...`，**禁止** `wsl$` / `wsl.localhost` UNC
  （历史 UNC 乱码文件事故的根因）。
- Windows 侧默认值（`E:\venvs\marker` 等）**必须可被 CLI 参数/环境变量覆盖**，并在
  `ENVIRONMENT.md` 登记。

## C-10 禁止事项

- ❌ 固定文件名互相覆盖（如所有 diagram 类都叫 `diagram.png`）
- ❌ `/tmp/skills-output/<date>/` 永久堆积
- ❌ 技能目录内 `-workspace` 孤儿目录
- ❌ **在技能目录内生成任何产物**（项目根标记遇到主库即止，从机制上阻断）
- ❌ 用 `docs/` 等通用目录名作项目根启发式
- ❌ 把随机临时名当成最终产物

## 显式指定优先（本文件不覆盖的场景）

1. 技能正文写明输出位置（如 paper-reader → 输入 PDF 同级 `paper-analysis/<pdf_stem>/`）
2. 用户或调用方显式传入 `--output` / `--output-dir` 参数
3. 项目内 `docs/rules/` 或任务上下文明确指定
