---
name: paper-reader
description: >
  学术论文 PDF 双引擎对照阅读器 — 四阶段流水线（预检→转换→合并→总结）。
  Marker + MinerU 双引擎并行转换，自动合并、差异对照、文献总结。
  内置 PDF 预检、异常处理三级响应、断点续跑、流水线状态追踪。当用户要求读论文/分析 PDF
  文献/精读对照时使用；纯文本或单页快摘走其他技能。
metadata:
  family: thesis
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Paper Reader — 四阶段论文分析流水线

## 流水线四阶段

```
源PDF目录                 转换目录                  合并目录                 文献总结
papers/              paper-conversion/         paper-merged/           paper-summaries/
    │                      │                        │                       │
    ▼                      ▼                        ▼                       ▼
┌────────┐   ┌──────────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ 预检    │──▶│ Marker + MinerU 并行  │──▶│ 合并 + 差异对照    │──▶│ LLM 生成结构化总结 │
│ 秒级    │   │ ~6 分钟/篇            │   │ ~2 秒             │   │                  │
└────────┘   └──────────────────────┘   └──────────────────┘   └──────────────────┘
```

## 何时使用（必须用）

- 用户提到"读论文"、"读 PDF"、"分析文献"、"精读"、"对照阅读"
- 用户给出 PDF 路径，需要分析其中方法、公式、实验、图表
- 批量处理目录中所有 PDF

**不要用此 skill 处理**：纯文本 PDF（小说、合同）、扫描件 OCR 单一诉求（直接用 MinerU 单路）、单页快速摘要（用 `look_at` 即可）。

## 输出目录结构（v2 四层架构）

```
<papers_dir>/                          # 源 PDF 目录
├── _pipeline_state.json               # ⭐ 流水线状态 → git 跟踪
├── _download_manifest.json            # 搜索桥接文件 → git 跟踪
├── paper-conversion/                  # 阶段1: 转换 → gitignore
│   └── <stem>/
│       ├── marker/                    # Marker 原始输出（含图片）
│       │   ├── <stem>.md
│       │   └── *.jpeg
│       └── mineru/                    # MinerU 原始输出（含图片）
│           └── auto/
│               ├── <stem>.md
│               └── images/*.jpg
├── paper-merged/                      # 阶段2: 合并 → gitignore（可从 PDF 重现）
│   └── <stem>/
│       ├── images/                    # 从两引擎复制的 Figure 图片（自包含）
│       ├── _MERGED.md                 # 合并版 Markdown
│       ├── _DIFF.md                   # 差异对照
│       ├── _META.json                 # 转换元数据（pdf_sha256 + engine_versions 溯源 + language）
│       └── _textlayer_probe.json      # 阶段 1.5 文本层探针（旁路证据，见下）
└── paper-summaries/                   # 阶段3: 文献总结 → git 跟踪
    └── <stem>.md                      # 结构化总结（含期刊等级）
```

### 各层 git 策略

| 目录 | 内容 | git | 理由 |
|---|---|---|---|
| `papers/` | 源 PDF | ✅ 跟踪 | 源文件，不可重现 |
| `_pipeline_state.json` | 状态 + URL | ✅ 跟踪 | 追踪进度，含分析决策 |
| `_download_manifest.json` | 搜索元数据 | ✅ 跟踪 | 论文来源信息快照 |
| `paper-conversion/` | 引擎原始输出 | ❌ ignore | 可从 PDF 重现 |
| `paper-merged/` | 合并产物 | ❌ ignore | 可从 PDF 重现 |
| `paper-summaries/` | LLM 总结 | ✅ 跟踪 | 含主观分析，每次不同 |

### 图片策略

合并阶段从两引擎复制 **仅 Figure 类型图片** 到 `paper-merged/<stem>/images/`：
- **MinerU**: 解析 `content_list.json`，过滤 `type == "image"` 的真正图片
- **Marker**: 无类型分类信息，全部复制（每篇约 14 张，可接受）

公式/表格图片备份留在 `paper-conversion/` 不复制（LaTeX/HTML 已在 md 中，图片冗余）。

**高分辨率路径（`--figure-source pdf`，issue #16）**：引擎副本之外，可从**源 PDF** 再产出一套印刷可用的图到 `paper-merged/<stem>/figures/`（**不覆盖**引擎副本，两套可比）：

| 情形 | 来源 | 说明 |
|---|---|---|
| 图块区域能被 PDF **内嵌位图**覆盖 ≥50% | 取该位图的**原始字节** | DCTDecode 的 raw stream 就是原 JPEG：**不解码、不重采样**；其它 filter 解成 PNG（无损容器） |
| 其余（矢量图 / 分块图形 / 不可解码的 XObject） | 按 `--figure-dpi`（默认 600）**渲染该页并裁剪** | **像素数增加 ≠ 信息量增加**：600 dpi 只保证印刷采样密度，图印在纸上的物理尺寸没有变 |

- 坐标：MinerU 的 `content_list.bbox` 是 **0–1000 归一化页面框**，不是 PDF 点。该结论是实测的——把两种假设分别与 PDF 自身的图摆放位置比 IoU，归一化假设在 23 篇有内嵌位图的语料上平均高约 4 倍。
- 确定性：同输入两次运行产物**逐字节一致**（渲染参数写死、无时间戳、文件名由 page+block 决定）；每篇附 `figures/_FIGURE_SOURCE.json` 清单（哪张走哪条路、原生尺寸、渲染 DPI）。
- 为什么是组合路径而不是二选一：只用内嵌原图在语料上只覆盖 367 张（1568 张引擎副本里的大多数来自页面渲染/分块），但**当图确实是内嵌位图时，原图显著优于引擎副本**（如 HHASA-RL 内嵌 30/32 达标 vs 副本 12/57）。

## 快速开始

```bash
# === 初始化流水线状态 ===
# 扫描 papers/ 目录，计算 hash，创建 _pipeline_state.json
paper_reader.py papers/ --init

# 从搜索桥接文件自动填入 URL/元数据
paper_reader.py papers/ --init --from-manifest _download_manifest.json

# === 查看流水线状态 ===
paper_reader.py papers/ --status

# === 处理论文 ===
# 基础用法（默认双引擎 + 断点续跑）
paper_reader.py papers/ --batch --resume

# 强制重做某篇
paper_reader.py papers/2605.05208.pdf --force

# 单引擎（快速摘要）
paper_reader.py papers/ --engines marker --resume

# 指定页范围
paper_reader.py papers/2605.05208.pdf --pages 0-4

# 资源限制
paper_reader.py papers/ --batch --resume --max-workers 1 --gpu-fraction 0.5

# 给"已转换过"的语料回填溯源信息（不重跑任何引擎）
paper_reader.py papers/ --backfill-meta
```

## 流水线状态文件

`_pipeline_state.json` 跟踪每篇论文的处理进度。完整 JSON 示例与字段语义见
[`references/state-file.md`](references/state-file.md)（调试状态文件、排查断点续跑时读）。

### 状态字段说明

| 字段 | 值 | 含义 |
|---|---|---|
| `status` | `pending` | 未处理 |
| | `passed` | 预检通过 |
| | `done` | 阶段成功完成 |
| | `degraded` | 部分成功（如单引擎可用） |
| | `skipped` | 因上游失败而跳过 |
| | `failed` | 本阶段执行失败 |
| | `interrupted` | 被 Ctrl+C 中断 |

## 溯源字段（_META.json）

每篇转换完成时写入 `_META.json`，供上层（如 `thesis-writing` 领域画像器）判断**漂移来自哪一版引擎**——缺这些字段时上层只能报"引擎版本未记录"。

| 字段 | 含义 |
|---|---|
| `pdf_sha256` | 源 PDF 完整 SHA-256；算不出写 `null`（原因见 `pdf_sha256_note`） |
| `engine_versions` | 固定含 `marker` / `mineru` / `torch` / `cuda` / `python` 五个键，取不到写 `null`（**不省略键**） |
| `engine_versions_source` | `conversion_time`（转换时实测）\| `current_env_estimate`（回填估计）\| `unavailable`（探测失败） |
| `engine_versions_note` | 失败原因或回填说明；成功为 `null` |
| `pdf_sha256_note` | 源 PDF 定位/失败说明；正常为 `null` |
| `language` | `zh` \| `en` \| `unknown` \| `null`——**与 paper-metrics `detect_language` 同一规则**（issue #10 首步） |
| `cjk_ratio` | 汉字数 /（汉字数 + ASCII 字母 token 数），6 位小数；无文本时为 `null` |
| `lang_source` | 该比率取自哪份产物：`mineru_content_list` \| `merged_markdown` \| `merged_markdown_backfill` \| `null` |

约束：

- 版本探测**每批只做一次并缓存**（探测各引擎 venv 的解释器；WSL 下经桥接取 Windows 侧解释器）。
- 单项失败只留 `null` + note，**绝不抛异常、绝不阻塞转换**（探测有超时上限，超时保留已产出的部分结果）。
- `pdf_sha256` 复用预检阶段的读取，不额外全量读盘。
- **语言三键永不写 0、永不省略**：`null` 是"没测"，`unknown` 是"这份文本没有任何可识别规则"，两者不同。冻结用例见 `scripts/lang_spec_cases.json`——**paper-metrics 的测试断言同一份文件**（`test_text_metrics.py::test_detect_language_matches_paper_reader_shared_spec`），所以两层的语言判断不会各自漂移。

**`pdf_sha256` 的来源等级（可信度分层，issue #7）**——同一个字段可能是三种东西，引用时必须说明是哪一级：

| 等级 | 何时出现 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| **原始投稿 PDF** | 预检时源 PDF 仍在磁盘、可直接读取 | 投稿时那份字节的身份（可用于跨项目比对同一篇 PDF） | 不能证明转换产物与其一致 |
| **引擎侧副本**（`*_origin.pdf`，同时写 `note`） | 原始 PDF 已不在磁盘，只能对引擎保存的副本取哈希 | 只能证明**当前这份产物**的身份（自洽与幂等） | **不能**回指投稿原件；与原件可能不同字节 |
| **`null`** | 上述两者都取不到 | 无 | 无（原因见 `pdf_sha256_note`，不得当 0 或空串处理） |

`engine_versions_source` 同理分三级：`conversion_time`（转换当时写入，可证明转换环境）> `current_env_estimate`（回填估计，**不得当作转换时实测**）> `unavailable`（探测失败）。`--backfill-meta` **只补写缺失字段，绝不覆盖已有 `conversion_time`**（`test_backfill_never_touches_conversion_time_record` 锁定）。实测分布（运筹与管理语料 66 个 `_META.json`）：`conversion_time` 11 篇、`current_env_estimate` 36 篇。

### 回填既有语料（`--backfill-meta`）

已转换过的语料重新转换代价过高（约 6 分钟/篇）。`--backfill-meta` 只补写缺失的溯源字段，**不重跑引擎**：

```bash
paper_reader.py <papers_dir> --backfill-meta
```

- 扫描 `<papers_dir>/paper-analysis/*/_META.json`（v1 语料）与 `<papers_dir>/paper-merged/*/_META.json`（v2 产物）。
- **幂等**：只补缺失字段；已有 `conversion_time` 实测记录一律不动；内容无变化则不重写文件。
- **诚实标注**：回填的版本写 `engine_versions_source = "current_env_estimate"`，note 明说"该论文转换于回填之前，版本为当前环境估计值，非转换时实测"；算不出源 PDF 时 `pdf_sha256 = null` 并在 note 说明。
- **回填会改变语料指纹（预期行为）**：paper-metrics 把 `_META.json` 的 sha256 记为输入产物之一，回填后该文件哈希变化 → 语料 `corpus_id` 变化 → `audit_corpus.py` 报 `inputs_changed`（"输入变了，旧结论作废"）。这是**归因正确**，不是确定性被破坏：指标数值本身不受影响。
- **语言三键**：回填同时补齐 `language / cjk_ratio / lang_source`（值取自已存的 canonical 文本，`lang_source` 标为 `merged_markdown_backfill`）；找不到正文时三键写 `null`——**键永远存在，未测量就是不写 0**。

## 预检（Phase 0：秒级，不启动 GPU）

在处理前自动验证 PDF 有效性，防止坏文件浪费 GPU 资源：

| 检查项 | 检测方式 | 失败动作 |
|---|---|---|
| 文件存在 | `pdf_path.exists()` | SKIP → 标记 `file_missing` |
| 非空文件 | `size ≥ 1KB` | SKIP → 标记 `empty_file` |
| PDF 格式 | magic bytes `%PDF-` | SKIP → 标记 `not_a_pdf` |
| 密码保护 | `pypdf.PdfReader.is_encrypted` | SKIP → 标记 `encrypted` |
| 结构损坏 | `pypdf` 读取抛异常 | SKIP → 标记 `corrupted` |
| 页数超限 | 默认 200 页上限 | SKIP → 标记 `too_large` |
| 扫描件检测 | 前 10 页 text 层检查 | DEGRADE → 警告，建议 MinerU OCR |

## 文本层探针（阶段 1.5：秒级、无模型、无 GPU）

`scripts/textlayer_probe.py` 独立复算 PDF 自带文本层的字符/数字/标点，与 canonical 文本对比，产出 `_textlayer_probe.json`。

**它存在的唯一理由**：`PDF → content_list.json` 这一跳**没有任何独立校验**，而这里会发生两类**静默损坏**（不报错、不告警，直接污染下游指标）。


两段本机实测数据与单独跑命令见 [`references/textlayer-probe.md`](references/textlayer-probe.md)。

### 判定规则（`compare_text_layers`，纯函数、可单测）

| verdict | 条件 | 含义 |
|---|---|---|
| `not_applicable` | 文本层 < 200 字符 | 扫描件，**不是"测到 0"** |
| `pdf_only` | 未提供 canonical 文本 | **没对照过就不算通过**（禁止报 `ok`） |
| `warn` | `digit_loss_rate > 2 %` 或 `unspaced_runs_introduced > 2` | 给出 `DIGIT_LOSS_HIGH` / `UNSPACED_ENGLISH_RUNS` |
| `ok` | 上述都不触发 | — |

关键设计：空格丢失用**两侧之差**判定（`canonical - pdf`），因为"长串数量多"可能只是原文真有长复合词；**差值 > 0 才说明是转换引入的**。

### 红线

- **产物绝不喂给 `canonical_text()`**——旁路证据，`paper-metrics` 的纯 stdlib / 零 LLM / 逐字节契约不受影响（实测：同一英文语料改动前后 `_per_paper_metrics.jsonl` **逐字节相同**）；
- 只用无模型、无 GPU 的宽松许可库（`pdfplumber` MIT）；**不要**在这里引入 PyMuPDF（AGPL）；
- 探针失败**绝不阻塞**转换流水线（意外异常由该层自行捕获降级：**设计内的"不适用"**写 `not_applicable` + 具体告警码如 `TEXT_LAYER_UNREADABLE` / `PDF_NOT_FOUND` / `PROBE_UNAVAILABLE`，**意外崩溃**写 `error` + `PROBE_CRASHED`，见下文"降级也留痕"）；
- 同输入两次运行 JSON **逐字节一致**（键排序、无时间戳、只记文件名不记绝对路径）；
- **比较对象要选指标层读的那份产物**（content_list.json），否则会把已发生的损坏测小一个数量级；
- 源 PDF 已不在盘上时报 `PDF_NOT_FOUND`，缺 `pdfplumber` 时报 `TEXT_LAYER_UNREADABLE`——都是"没测"，不是"通过"。

### 接入点：阶段 1.5 已在流水线内（issue #12）

`process_one` 在合并之后自动跑探针，写入 `paper-merged/<stem>/_textlayer_probe.json`，并把 verdict 汇总到末尾表格的 `probe` 列（`ok` / `WARN` / `pdf_only` / `n/a`）。默认开启，`--no-textlayer-probe` 关闭。

**缓存键 = PDF 内容哈希 + 比较对象标签 + 比较文本哈希 + 探针版本 + pdfplumber 版本。** 五项里任何一项变了都重算（`--force` 越过判断强制重算）。少任何一项都会把"上一次转换的旧证据"当成新证据——尤其**比较文本哈希**：重新转换会在同一文件名下换掉 content_list，只认标签就会复用错的记录。

**降级也留痕，且区分"没测"与"崩了"**：源 PDF 不在（`PDF_NOT_FOUND`）、缺 `pdfplumber`（`TEXT_LAYER_UNREADABLE`）、探针不可用（`PROBE_UNAVAILABLE`）都写 `verdict=not_applicable`（**设计内的"不适用"**）；而**意料之外的崩溃**写 `verdict=error` + `PROBE_CRASHED`，并把 `probe_path` 置空。两者都会落盘——"这一跳没测过"本身就是证据；区分它们是因为下游常见的"`verdict != warn` 即算通过"会把崩溃悄悄扫进通过桶。这类记录没有 PDF 哈希，永远不会成为缓存命中。

**content_list 按 md 同名匹配**：优先精确匹配 `<md_stem>_content_list.json`；精确文件不存在时，**只有该目录里仅有一个 content_list 才接受**，否则一律拒绝（歧义即视为"没有证据"，退回 `_MERGED.md`）。旧实现在本篇 JSON 为空时会退而取"同目录第一个非空文件"，等于**张冠李戴**——把别篇的正文当成本篇证据，而且标签还写着 `mineru_content_list`。

**整段永不抛异常**：探针与语言相关的代码都在 `collect_stage_1_5()` 里，该函数契约为"永不抛异常"——磁盘上一条被手改坏的记录（例如 `"warnings": 123`）绝不能把一篇已经转换成功的论文变成失败的。探针异常一律由它捕获并降级（设计内的不适用 → `not_applicable`；**意外崩溃 → `error`**），**绝不阻塞转换**。

**比较对象必须是本轮**产出**的那个产物**：本轮 MinerU 成功时用**它自己目录下**的 `*_content_list.json`（`canonical_source=mineru_content_list`），否则退回本轮 `_MERGED.md`（`canonical_source=merged_markdown`）。

- "本轮"不是客套：树里可能留着**上一次转换**的 MinerU JSON。marker-only 运行、或本轮 MinerU 失败时如果还去扫树，就会把旧文本当作本次证据（实测：旧中文 JSON + 本轮英文合并稿 → 记录出 `zh`）。所以流水线只在传入了本轮 MinerU markdown 时才读同目录 JSON，扫描整棵树只允许 `--backfill-probe` 这类"没有本轮"的调用，并且 `canonical_source` 会写明用的是哪一份。
- **不要把它说成"指标层读的那份文本"**：这里是**原始** content_list 的全部 text，而 paper-metrics 的 `canonical_text()` 会再丢掉 front_matter / references / keywords 等非正文。所以两层的 `cjk_ratio` **允许不同**，语言标签也不保证一致——paper-metrics 侧为此专门产出 `LANGUAGE_METADATA_MISMATCH` 交叉核对（见其 SKILL）。选 content_list 而不是 markdown 的理由与上面无关，只关乎**能不能看见损坏**：同一篇 PDF 对 `_MERGED.md` 只测出 **2.1%** 数字丢失，对 content_list 是 **55.5%**——markdown 导出保留着 JSON 侧已经丢掉的数字。

### 存量语料：`--backfill-probe`

`--resume` 会把状态文件里"已完成"的论文整篇跳过，所以**接入之前就转换好的语料永远拿不到阶段 1.5 证据**（真重跑转换约 6 分钟/篇）。这个入口只读 PDF 自带文本层：不启动引擎、不碰 GPU。

```bash
cd ~/projects/dc-skills && uv run python paper-reader/scripts/paper_reader.py <papers_dir> --backfill-probe [--force]
```

输出 `scanned/probed/reused/warn/not_measured/no_pdf` 计数：有效记录算 `reused`，源 PDF 找不到的论文计入 `no_pdf` 且不写记录。实测（真实中文语料副本《序定车辆路径问题》）：首次 `probed=1 warn=1`（数字丢失 78.2%），第二次 `reused=1`，`--force` 重新测量。

## 引擎与许可（红线）

| 引擎 | 代码许可 | 权重许可 | 备注 |
|---|---|---|---|
| **MinerU** | Apache-2.0 **+ 附加条款** | **AGPL-3.0**（MinerU2.5-2509-1.2B VLM 权重） | 附加条款：MAU > 1 亿或月营收 > 2000 万美元需商业许可；**提供在线服务须署名 MinerU** |
| **Marker 2.0** | Apache-2.0 | Apache-2.0 | v1.x 曾是 GPL-3.0，2.0 起改为 Apache-2.0 |
| **Surya**（经 Marker 引入） | Apache-2.0 | **modified AI Pubs OpenRAIL-M（非 OSI）** | **免费商用仅在融资/营收低于阈值时成立**；它已随 Marker 2.0（`Requires-Dist: surya-ocr>=0.22.1`）进入流水线，因此**必须**与版本一起记录 |
| **PyMuPDF** | **AGPL-3.0** | — | **本技能不使用**（会污染发布路径）；文本层探针改用 MIT 的 `pdfplumber` |

`_META.json` 里：

- `engine_versions` 含 **`marker / mineru / surya / torch / cuda / python`** 六个键（**取不到写 `null`，不省略键**）；
- `engine_license_ids` 记录上表的许可标识——**版本号本身看不出"Surya 权重是 OpenRAIL-M"**，所以许可与版本同行记录。

### venv 布局

- **在用**：`venvs/marker/`（当前 Marker，surya 0.22.1）、`venvs/mineru/`；
- `venvs/marker-v1.10.2/`：**遗留 pinned venv，仓库代码从未引用**（其中 surya 是 0.17.1）。它与在用 venv 是**两套独立环境**，**不是同一环境里的重复 dist-info**——保留或删除需人工决定，不要当垃圾清理。

## 异常处理（三级响应）

| 级别 | 含义 | 行为 | 示例 |
|---|---|---|---|
| **FATAL** | 环境级，继续无意义 | 终止整个运行 | 磁盘满、输出目录不可写 |
| **SKIP** | 此 PDF 不可处理 | 跳过，标记，继续下一个 | 损坏 PDF、密码保护、两引擎都失败 |
| **DEGRADE** | 部分可用 | 继续下游降级处理 | Marker OK 但 MinerU OOM |

### 各阶段异常表

#### Phase 1: 转换

| 异常 | 级别 | 降级策略 |
|---|---|---|
| Marker 失败（OOM/超时/崩溃） | DEGRADE | 单路 MinerU → merged 直接用 MinerU md |
| MinerU 失败 | DEGRADE | 单路 Marker → merged 直接用 Marker md |
| 两引擎都失败 | SKIP | 标记 failed，跳过此 PDF |
| GPU 预算不足被跳过 | SKIP | 标记 `gpu_budget_unavailable` |

#### Phase 2: 合并

| 异常 | 级别 | 降级策略 |
|---|---|---|
| 仅单引擎可用 | DEGRADE | 直接拷贝该引擎输出作为 merged |
| 归一化崩溃 | SKIP | 保留原始输出，标记 failed |
| 图片复制失败 | DEGRADE | merged md 仍生成，图片回退到相对引用 |
| 写入失败 | FATAL | 磁盘满或其他 I/O 问题 |

## 搜索桥接文件（与 unified-search 联动）

`_download_manifest.json` 存放论文的来源信息，由搜索阶段产生、paper-reader 读取：

```json
{
  "generated_by": "unified-search",
  "query": "MDVRP vehicle routing GPU acceleration",
  "generated_at": "2026-07-18T15:00:00",
  "papers": [
    {
      "filename": "2605.05208.pdf",
      "arxiv_id": "2605.05208",
      "title": "MDVRP: A Multi-Depot Vehicle Routing Problem with...",
      "url": "https://arxiv.org/abs/2605.05208",
      "pdf_url": "https://arxiv.org/pdf/2605.05208",
      "doi": "10.1109/xxx",
      "venue": "Transportation Science",
      "year": 2026,
      "authors": ["Lei, H.", "Smith, J."],
      "source_db": "arxiv"
    }
  ]
}
```

`--init --from-manifest` 按 `filename` 匹配，自动填入 `_pipeline_state.json` 的 `source` 字段。
若论文损坏需要重新下载，`source.url` / `source.pdf_url` 可直接用于定位。

## 文献总结与合并算法（按需查阅）

- Phase 3 文献总结模板全文与期刊等级标注规则： [`references/summary-template.md`](references/summary-template.md)
- 双引擎设计原理、合并策略（_MERGED.md）与 Diff 算法： [`references/merge-algorithm.md`](references/merge-algorithm.md)

## 性能预期

- 40 页论文，双引擎并行：~6 分钟
- 10 页，Marker 单路：~30 秒
- 预检：< 1 秒

## 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `[SKIP] encrypted` | PDF 密码保护 | 用 `source.url` 重新下载未加密版本 |
| `[SKIP] not_a_pdf` | 文件非 PDF 格式 | 检查下载源 |
| `[SKIP] corrupted` | PDF 结构损坏 | 用 `source.pdf_url` 重新下载 |
| `[DEGRADE] MinerU OOM` | 显存不足 | `--max-workers 1 --gpu-fraction 0.5` |
| `[SKIP] gpu_budget_unavailable` | 其他 GPU 任务占满 | 等待或 `--gpu-cap-fraction 0` |
| 批量中断 | Ctrl+C | 已完成的保留，`--resume` 续跑 |

## 资源限制（防 OOM）

默认四层防护：
- ① 单进程 GPU 显存配额 40%
- ② 单进程 CPU 线程上限 6
- ③ 批量串行（1 PDF）
- ④ 设备级协调器（总显存 ≤ 90%）

详见 `wsl-windows-bridge/references/python-channel.md`「GPU 多路并发铁律」。

## 文件

```
paper-reader/
├── SKILL.md                    # 本文
├── scripts/
│   ├── paper_reader.py         # 主脚本（流水线编排 + CLI）
│   └── bootstrap.sh            # 重装 venvs
├── venvs/
│   ├── marker/                 # Marker 独立 Python 环境
│   └── mineru/                 # MinerU 独立 Python 环境
└── data/                       # 预留缓存目录
```
