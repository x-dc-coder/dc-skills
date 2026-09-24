# SKILL 输出目录规范（OUTPUT.md）—— 统一产物树

> **定位：唯一事实源。** 所有 SKILL 的文件产物（最终产物、中间产物、跨会话状态）默认落入
> **统一产物树**；显式指定（`--output` / 技能正文）优先，但最终产物始终在主库留审计副本。
> 代码实现：`scripts/common.py` 的 `plan_output` / `run_dir` / `audit_dir` / `commit_final`。
>
> 2026-09-24 大改：废弃「类型目录两级回退」与 `~/.claude/skills-output/` 兜底；
> 统一为 `<cwd>/skills-output/<族群>/<技能名>/<时间戳>/`。

## C-1 统一输出树（单一规则，无兜底分叉）

所有产物默认落在**当前工作目录**下的统一文件夹：

```
<cwd>/skills-output/<族群>/<技能名>/<YYYYMMDD-HHMMSS>/
```

| 段 | 取值 | 示例 |
|---|---|---|
| `<cwd>` | 运行技能时的当前目录（工作项目或主库） | `~/projects/MyThesis` |
| `<族群>` | agent-map.yaml `families:` 的键（= SKILL.md `metadata.family`） | `thesis`、`drawing`、`db` |
| `<技能名>` | 技能目录名；diagram-* 用子类型目录名 | `thesis-writing`、`diagram-er` |
| `<时间戳>` | 本次运行的目录，格式见 C-2 | `20260924-143022` |

- 主库内运行（`cwd` = `~/projects/dc-skills`）同样落 `<master>/skills-output/…`，已入 `.gitignore`，仓库永远干净
- `~/.claude/skills-output/` **已废弃**，不再产生新内容；存量目录保留至用户确认后清理

## C-2 时间戳（每次运行独立目录）

- 格式 `YYYYMMDD-HHMMSS`（本地时间；无冒号，Windows 安全；字典序即时间序）
- **进程级单次**：一个 CLI 进程的多次落盘共享同一时间戳目录；两次调用 = 两个目录
- 时间戳目录天然防覆盖；不再需要 `--reuse` / `latest.json` 机制（C-7 旧条款废止）
- 清理策略：产物可按时间戳目录整批清理；用户资产类（论文正文、总结）豁免自动清理

## C-3 族群与技能名口径

- `<族群>` 以 `agent-map.yaml` `families:` 段为唯一事实源；单体族（db/bridge/lark/meta）同样占一层，保持结构一致
- `<技能名>` 默认 = 技能目录名；**diagram 家族特例**：用子类型脚本目录名（`diagram-er`/`diagram-ers`/`diagram-module`/`diagram-sequence`/`diagram-usecase`/`diagram-draft`），因为六类图表共用 `diagram` 技能入口但产物不同
- 新技能登记族群后才能正确落盘（`family-apply` 已把 `metadata.family` 写入每个 SKILL.md）

## C-4 最终产物与审计副本

- **默认**：最终产物落 C-1 目录；若 `cwd` 不在主库内，落盘后**自动复制审计副本**到
  `<master>/skills-output/<族群>/<技能名>/<时间戳>/`（`commit_final()`）——主库 skills-output
  是全机产物审计轨迹，与主产物位置重复时不去重拷贝
- **显式 `--output <目录>`**：最终产物落指定目录（用户/项目指定优先），**审计副本仍然生成**
- 审计副本只复制**最终产物**；中间产物与状态不复制
- paper-reader 例外：见 C-13

## C-5 中间产物

- 同一运行的中间产物落 **`<时间戳>/.work/`**（与最终产物同根，整批清理/打包）
- 示例：`diagram-ers` 的 graphviz `.dot`、`diagram-sequence` 的 mmdc 临时 `.mmd`、
  `db-skill` 查询结果、`newapi-management` 落盘前的中间 JSON

## C-6 跨会话状态

- 需要跨运行保留的状态（非本次产物）落 **`<cwd>/skills-output/<族群>/<技能名>/.state/<语料标识>/`**，
  `<语料标识>` = 输入文件名去扩展名（如论文 stem）；**SKILL.md 必须写明保留窗口与清理方式**
- 已登记：`thesis-ref-check` 的术语表/修改日志（`.state/<论文stem>/term_glossary.md` 等）

## C-7 临时文件与受控 /tmp 例外

- 进程级临时文件必须用 `tempfile`/`$TMPDIR`，退出即删（`debugging` 的 journal 纪律为正面范式）
- 需要 `/tmp` 持久的场景实行**登记制**，当前登记两例：

| 位置 | 使用者 | 理由 | 清理时机 |
|---|---|---|---|
| `/tmp/gpu-logs/` | wsl-windows-bridge | inotify 需 Linux 本地 FS（WSL#4739） | 随任务结束 |
| `/tmp/dsh-repo/` | dsh-plugin-troubleshooting | 官方仓库离线克隆 | `git pull` 前手工确认 |

## C-8 全局运行时状态登记制

跨项目共享的技能自身簿记（非用户产物）可留在技能目录外登记的固定位置，**不得放技能目录内**：

| 位置 | 使用者 | 内容 |
|---|---|---|
| `~/.local/state/dc-skills/unified-search/` | unified-search | `history.db`、`quota.json`、`dblp_cookies.json`（全局配额/历史，按 cwd 拆分会破坏配额感知） |
| `~/.cache/gpu-governor/` | wsl-windows-bridge | 设备级 GPU 协调 ledger（跨进程，非技能产物） |
| `<项目>/.git/…` | github-workflow | review manifest 在 git 内部命名空间（`github-workflow-review.json`、`candidates/`） |

## C-9 文件名约定（防覆盖）

时间戳目录内用**固定名**（目录已隔离，无需文件名带时间戳）：

| Skill | 输出文件名 |
|---|---|
| diagram / er | `er-diagram.png` |
| diagram / ers | `ers-diagram.png` |
| diagram / module | `module-diagram.png` |
| diagram / sequence | `sequence-diagram.png` |
| diagram / usecase | `usecase-diagram.png` |
| diagram / flow | `flow.mmd` / `flow.png` |
| diagram / draft | `draft-<basename>.png/svg/html`（**必须 draft- 前缀**） |
| thesis-writing | `第X章-章节名.md` / `full-thesis.md` |
| md-to-thesis-latex | `main.tex`（`<ts>/latex/` 下） |
| thesis-ref-check | `<stem>_修正版.md` + `<stem>_术语修改报告.md` |
| thesis-export | `导出.docx`（工具链自带 `out/<学校>/<时间戳>/`，符合本规范精神） |
| word-extractor | `<basename>.md` + `<basename>.json`（不产 images/） |
| db-skill | `<ts>/mysql-result.json` / `pg-result.json`（固定名，禁 mkstemp 随机名） |

## C-10 安装/脚手架类动作豁免

往**用户项目源码树**写入的动作不是"产物"，不适用本规范：
- `test-guardian` 的 `pytest_test_guard.py`/`conftest.py` 安装
- `programming` 脚手架的 `new-script.py`（默认落 cwd，由用户决定去留）
- `officecli` 直接操作用户文档（save/close 落用户指定路径）

## C-11 跨设备路径

- WSL 侧读写 Windows 盘一律用 `/mnt/<盘符>/...`，**禁止** `wsl$` / `wsl.localhost` UNC
- Windows 侧默认值（`E:\venvs\marker` 等）必须可被 CLI 参数/环境变量覆盖，并在 `docs/arch/ENVIRONMENT.md` 登记

## C-12 禁止事项

- ❌ 硬编码 `~/.claude/skills-output`（已废弃）或任何本规范外的兜底目录
- ❌ 绕过 `common.py` 自拼输出路径（validate R10 门禁）
- ❌ 在技能目录内生成任何文件（`data/`、`cache/`、`output/`、`-workspace/` 均禁止；全局状态走 C-8 登记）
- ❌ `mkstemp` 随机名当最终产物（时间戳目录已防覆盖）
- ❌ `/tmp/skills-output/<date>/` 式永久堆积
- ❌ UNC 路径（C-11）

## C-13 已登记例外

| 例外 | 内容 | 状态 |
|---|---|---|
| paper-reader | 产物落输入 PDF 同级四层树（`paper-conversion/paper-merged/paper-summaries/`）+ 断点状态 | 待收编，见 issue #27 |
| C-7 /tmp 两例 | gpu-logs、dsh-repo | 长期 |
| C-8 全局状态三例 | unified-search state、gpu-governor、.git manifest | 长期 |
| C-10 安装/脚手架 | 写用户项目源码树 | 长期 |

## 显式指定优先（本文件不覆盖的场景）

1. 用户或调用方显式传入 `--output` / `--output-dir`（最终产物落指定处，审计副本仍生成，C-4）
2. 项目内 `docs/rules/` 或任务上下文明确指定
