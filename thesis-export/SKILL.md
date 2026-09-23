---
name: thesis-export
description: >
  论文 Markdown → docx 导出与格式验证工具链（word-production）手册：导出前格式检查（check_markdown_spec）、导出（--engine auto/linux/com）、导出后验证、脚注重排、COM 封面拼接、Word 批注/模板格式提取（doc_intake）。当用户要求导出论文/导出 docx/论文排版/检查格式/验证 docx/提取 Word 格式/提取批注/从模板生成配置/封面拼接/看学校配置时使用；与 thesis-writing（写草稿上游）、word-extractor（提取）分工。
---

# 论文导出 Skill：word-production 工具链手册

> **与 thesis-agent 预设的关系**：本 skill 是 word-production **工具链手册**（CLI 命令/退出码/超时表）；
> `thesis-agent` 预设（`~/.dsh/.agent-presets/thesis-agent/agent.cordis.yml`）是运行时**规则**（MCP 工具链 + 模板文档流水线 + 红线）。
> 两者互补：预设按规则走流程，本 skill 按需查命令细节。**红线以预设 persona 为准**，修改任一方的红线需同步另一方（当前两处红线内容一致，勿单独漂移）。

> 项目根：`/mnt/e/AllProjects202604/Words-Production`（下文命令均以项目根为 cwd；
> 环境：WSL2 + 可选 Windows Word COM；Python ≥3.12，项目自带 `.venv`（python3.14）。

## 0. 铁律（必须先读）

1. **导出前必须跑 md-check**，除非用户明确说 `--skip-md-check`。流水线会自动检查，但
   Agent 应主动先跑一遍拿到问题清单，再决定是否导出。
2. **COM 定稿必须提示降级语义**：`--engine auto` 产物 `mode=linux` 时若配置含封面/每页
   脚注重置，必须明示用户「当前为纯 Linux 产物，封面拼接/每页脚注重置需 Word COM 定稿」。
3. **不删用户产物**：`out/` 目录产物是交付物，任何删除/覆盖前先确认；导出用默认时间戳
   目录避免覆盖。
4. **COM 类调用先探测**：调 `com-extra` 或 `--engine com` 前，先 `com_health`（curl
   `http://127.0.0.1:8765/health`，2s 超时）。服务不可用 → 告知用户「需先在 Windows 侧
   启动 `python.exe -m thesis_exporter.com_service --port 8765`」，不默默降级。
5. **超时对齐**：任何 bash 调用 WP CLI 都传 `timeout` 且 ≥ 下表值，否则 COM 分钟级任务
   会被默认超时误杀。禁止在 bash 里裸跑分钟级 COM 命令（≤1800s 场景用工具/后台任务）。

## 1. 标准工作流（检查 → 导出 → 验证 → 定稿）

```
1. md-check     python tools/check_markdown_spec.py --md <文件> --json [--strict]
                （有 ERROR/非零退出码 → 先修再导出；--strict 下 WARN 也算失败）
2. export       python -m thesis_exporter.cli --engine auto --md <文件> \
                --config config/schools/<学校>.yaml --json
                （读 {mode, out, conditions}：mode=linux 纯 Linux 产物；staged/com 需 COM 步骤）
3. verify       python -m format_extractor.cli verify --docx <导出.docx> \
                --config config/schools/<学校>.yaml --json --engine xml
                （失败退出码 1 / 严格门禁 4；双闸门渲染检查用 --engine auto 自决策）
4. 定稿(可选)   python -m thesis_exporter.cli com-extra --docx <半成品.docx> \
                --config <配置> [--cover-doc <封面.docx>] --json   （需 COM 健康）
```

## 2. CLI 命令手册

### 2.1 Markdown 格式检查（导出前门禁，必做）

```bash
python tools/check_markdown_spec.py --md <文件.md> --json [--strict|--lenient] [--view issues]
```

- 50+ 项检查（引用编号连续性/重复/起始值、图表题注编号、标题跳级、图片表格缺说明等）
- `--json` 输出信封 `{success, findings, summary}`；findings 项含 category/code/severity/
  path/expected/actual/message
- 退出码：0 通过 / 2 有 ERROR / 3 strict 下有 WARN / 1 运行错误
- 转译规则：error→ 修复后重试；warning→ 提示用户；strict 下 warning 也需处理

### 2.2 论文导出（主 CLI：thesis-export / python -m thesis_exporter.cli）

```bash
python -m thesis_exporter.cli --engine auto --md <文件.md> --config config/schools/<学校>.yaml --json
python -m thesis_exporter.cli --engine linux --md <文件.md> --config <配置> --json   # 强制纯 Linux
python -m thesis_exporter.cli --engine com   --md <文件.md> --config <配置> --json   # 强制完整 COM
python -m thesis_exporter.cli --engine auto --json --md ... --config ...              # 预演输出执行计划
python -m thesis_exporter.cli config-help [--json] [--key <点路径>]                    # 配置自省
```

- `--engine auto`（默认）：Linux 优先，按 `STEP_DEPENDENCIES` 自决策
  `mode ∈ {linux, staged, com}`，`--json` 返回 `{mode, out, conditions, plan_s}`
- 输出路径自动派生：`out/<学校>/<时间戳>/导出.docx`；`--out`/`--output-name` 可覆盖
- `--cover-doc <docx>`：封面拼接（auto 下触发分阶段 portable + com-extra）
- `--skip-md-check`：跳过内置格式检查（除非用户明确要求，否则不跳过）
- `--verify` / `--verify-strict`：导出后自动验证；strict 失败退出码 4
- `--profile <course_assignment|fragment>`：加载 config/profiles/ 预设（与 --config 互斥）

### 2.3 格式验证（format_extractor.cli）

```bash
python -m format_extractor.cli verify --docx <导出.docx> --config <配置> --json [--engine xml|com|auto]
python -m format_extractor.cli verify-batch --docx a.docx b.docx --config A.yaml B.yaml --json --engine xml
python -m format_extractor.cli extract --docx <参考.docx> --out reports/ [--format all|markdown|json|yaml]
```

- `--engine` 语义（与导出 CLI 不同！）：`xml`=仅 XML 声明检查（Linux 默认）；`com`=仅 COM
  渲染检查；`auto`=XML + 检测到 COM 时叠加（无 Word 时降级 warning 不失败）
- verify 失败退出码 1；`--view issues` 分类视图
- extract：从参考 docx 提取格式信息（页面/字体/样式/报告），用于配置新学校

### 2.4 后处理（com-extra / footnote-restyle）

```bash
python -m thesis_exporter.cli com-extra --docx <已有.docx> --cover <封面.docx> \
  --config <配置> --out <最终.docx> --json          # 封面拼接+分节页码/页眉重设（需 COM）
python -m thesis_exporter.cli footnote-restyle --docx <已有.docx> --config <配置> --json
                                                    # 脚注标记后处理（纯 Python）
```

- footnote-restyle：自动编号→带圈①/括号[n]；`restart_page`（每页重置）纯 Linux 降级为
  连续编号 + warning → 需提示用户「需 --engine com 定稿才能每页重置」

### 2.5 WSL 快捷桥接（用户友好入口，也可让用户自跑）

```bash
./scripts/wsl_export.sh --school <学校名> --format <配置id>   # 自动发现 in/<学校>/*.md
./scripts/wsl_export.sh --list-formats / --list-schools
```

### 2.6 模板填充与网页预览（fill-template / preview）

```bash
# 模板填充：官方模板 docx 表格单元格按结构化 JSON 写入（开题报告/任务书等）
python -m thesis_exporter.cli fill-template --template <模板.docx> --content <内容.json> \
  --out <输出.docx> [--json]
#   参数：--template（必填，官方 docx 模板）/ --content（必填，内容 JSON）/
#         --out（必填，输出 docx）/ --json（JSON 信封）
#   失败：模板或内容文件不存在 → 报错退出码 1（EXIT_FAIL）

# docx 网页预览：生成自包含 HTML（内嵌 docx + docx-preview/jszip 前端渲染），无需 Word 查看
python -m thesis_exporter.cli preview --docx <文件.docx> [--out <预览.html>] [--open] [--json]
#   参数：--docx（必填）/ --out（默认 <docx 同目录>/<stem>.preview.html）/
#         --open（自动打开浏览器，WSL 无默认浏览器时仅提示不报错）/ --json
```

- 超时对齐：fill-template / preview 均按 **120s**（中小文档；preview HTML 生成本地操作）
- 详细说明：`docs/模板填充说明.md`（内容 JSON 规范）、`docs/docx网页预览说明.md`（预览原理/vendor/边界）
- 与 MCP 关系：这两命令**未接入** thesis MCP（mcp/README.md 标注"预留、本期仅 CLI"），Agent 直接用 bash 调用

### 2.7 Word 文档信息提取（doc_intake，issue #48）

Word 侧信息 → 配置/md 的桥接（纯 Python，无需 COM）：

```bash
# 批注/修订/标记：作者、时间、内容、锚点段落、已解决状态、回复链
python -m doc_intake.cli comments --docx <模板.docx> [--out <目录>] [--json]

# 模板格式 + 批注要求 → YAML 配置模板（生成后立即过 SchoolConfig 校验）
python -m doc_intake.cli config --docx <模板.docx> [--out config/schools/<新学校>.yaml] \
  [--base-config <基线.yaml>] [--prefer comment|sample] [--scan-body-text] [--json]

# 图片清单：段落位置/尺寸/题注/未引用媒体
python -m doc_intake.cli images --docx <模板.docx> [--json]

# 批注 → md 修改建议（默认 dry-run；--apply 才写盘并留 .bak）
python -m doc_intake.cli md-changes --docx <带批注.docx> --md <论文.md> [--apply] [--json]

# 一次跑齐
python -m doc_intake.cli all --docx <模板.docx> [--md <论文.md>] [--out <目录>] [--json]
```

- **典型用法**：学校只给 Word 模板且要求写在批注里 → 先 `config` 出配置骨架 + `unresolved`
  清单，人工确认未决项后再用于导出；导师在 docx 上留批注 → `md-changes` 出修改请求（含
  md 行号），确认无误再 `--apply`。
- 退出码：0 成功 / 1 文件不存在或配置未过 schema 校验 / md 应用出错。
- 信封：`{success, command, summary, data, findings, warnings}`；未决项与需人工处理项为
  `severity=warning` 的 Finding（可直接给用户看）。
- 超时对齐：**120s**（大文档适当放宽）。
- 详细说明：`docs/Word文档信息提取.md`；MCP 工具：`doc_intake`（command 参数选子命令）。

## 3. 退出码约定（shared/exit_codes.py）

| 码 | 含义 | Agent 处理 |
|---|---|---|
| 0 | 成功 | 正常 |
| 1 | 通用失败/用法错误 | 读 stderr 定位 |
| 2 | md 检查有 ERROR | 修复 Markdown 后重试 |
| 3 | md strict 下有 WARN | strict 门禁场景需处理 |
| 4 | verify-strict 失败 | 按 findings 修复格式后重导出 |

## 4. 超时对齐表（bash 调用必须传 ≥ 该值）

| 操作 | 超时 |
|---|---|
| md-check（超大 md） | 120s |
| doc_intake（comments/config/images/md-changes/all） | 120s |
| verify xml 单文档 / com_bridge 单文档渲染检查 | 120s |
| verify-batch（批量） | 600s |
| com_service HTTP op（大文档） | 1800s |
| com_service heartbeat | 8s（运维参数） |
| com_service idle 自动退出 | 720s |
| /health 探测 | 2s |

## 5. 学校配置清单（config/schools/，20 个）

```
binjiang_undergrad      cjlu_undergrad          course_normal
custom_thesis           custom_thesis_auto      graduate_thesis
hrbu_ai_undergrad       jingmao_internship      ncpe_undergrad
neusoft_project_report  new_thesis              sdu_undergrad
se_course_design        shanghai_style          software_test_experiment
wenzhou_undergrad       wuxi_course_design      wxu_undergrad
x-dc_test               yunchou_yu_guanli
```

- 配置支持 `extends:` 基线继承（深合并）；`config-help --json` 可查完整 schema
- 拿不准学校名/配置键时，先跑 `config-help --json` 核对，**不要猜测**
- profiles：`course_assignment` / `fragment`（--profile 加载）

## 6. 环境判定与常见失败处理

### 6.1 环境判定（选引擎前）

```bash
curl -s -m 2 http://127.0.0.1:8765/health    # 有 com_service：{session_alive, op_count, degraded}
```

- 有服务 → COM 类操作可用（com-extra / --engine com / verify --engine auto 渲染检查）
- 无服务但本机是 WSL/Windows → 提示用户可启动常驻服务（见铁律 4）
- 纯 Linux（无 Windows）→ 只用 `--engine linux`/`auto`（无封面场景），并提示降级边界

### 6.2 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| 退出码 2/3 | md 检查失败 | 按 findings 修 md，ERROR 必须清零 |
| 退出码 4 | verify-strict 失败 | 按 findings 修格式，重导出 |
| pandoc 缺失/版本差异 | Linux/Windows pandoc 行为差异 | 两平台产物互验是设计内行为，不误判失败 |
| docx 打开失败 | 文件被 Word 占用 | 提示用户关闭 Word 中的文档后重试 |
| COM 超时/降级 | Word 忙（heartbeat 8s 误判已放宽） | com_health 看 degraded，报告用户等待，不连环重试 |
| 中文路径乱码 | Windows argv 转义 | WP CLI 已内置 UTF-8 三层防护；Agent 调用用列表传参，不拼 shell 字符串 |
| /mnt/e IO 慢 | 9p 挂载 | 大文件操作预期慢，超时给足；产物读取用 /mnt/e 路径，不写 wsl$ UNC |

## 7. 不建议 Agent 自动化的能力（告知用户人工使用）

- `watch` 实时预览：长驻交互式 HTTP 服务 + 浏览器刷新，人工使用
- `resident` 常驻批量导出：属运维批量工具，Agent 场景由 com_service 后台常驻替代
- `dump` / `batch --ops`：底层操作回放，模板编辑专用（人/CI 用）
- 封面模板选择、Word 内人工目检：无 headless 语义，需人工介入
