# paper-metrics 指标定义文档（I7）

> **地位**：本文件是 OBSERVED 层指标的**人读定义事实源**；机器读事实源是 `paper-metrics/scripts/text_metrics.py` 的常量与 `data/lexicons/v1/*.json` 的词表（含 `sha256`）。
> **唯一规范**：`/mnt/e/Google_Download/Paper-Reader_设计审查/_impl/INTERFACES.md`（冻结版 v1）。与之冲突时以 INTERFACES.md 为准，并在此文件的 §7 记录差异。
> **设计依据**：`00-审查报告.md` §7-§8（I1-I9 + I12）；文献依据见同目录 `03-工具与依据.md`。
> **版本**：文档随 `metric_spec_version` 冻结。改定义必须同时改 `metric_spec_version`；只改阈值/词表必须同时更新词表 `sha256`。

---

## 0. 适用范围与不适用边界（E5）

| 维度 | 本指标体系成立 | 不成立 / 禁止使用 |
|---|---|---|
| **语言（可执行的失败契约，不只是声明）** | **仅英文**（`SUPPORTED_METRIC_LANGUAGES = ("en",)`）。判定量 `cjk_ratio = 汉字数 / (汉字数 + ASCII 字母 token 数)`，**阈值 0.10**：`cjk_ratio <= 0.10` 才判定为支持。语料级 `language_supported` 为**三态**：true（全篇正面判定受支持）/ false（存在不支持）/ **null = 未判定且不是「受支持」** | **`cjk_ratio > 0.10` ⇒ 不支持，必须显式失败**：① 该篇**每一个指标**输出 `value = null`、`n = 0`、`warnings = ["LANGUAGE_NOT_SUPPORTED"]`；② 计入该指标的 `n_missing`（整语料不支持时 `n_missing == n_papers`）；③ 语料级 `_corpus_summary.json` 顶层 `languages` / `language_supported` 记录语言分布，并追加告警 **`CORPUS_LANGUAGE_UNSUPPORTED`**；④ 草稿校验 `validate_draft.py` **退出码 2（language_unsupported）且不输出任何通过/不通过结论**。**红线：任何情况下不得用 `0` 代替「未测量」**——`0` 只能是「测到了，值为 0」，未测量必须是 `null` |
| 输入 | MinerU `*_content_list.json`（Canonical Document，冻结 + sha256） | PDF 直读；`marker/*.md` 只参与 `M-CITSTYLE-50` / `M-CONTRIB-52` 两条，不参与句/词级指标 |
| 层 | **仅 OBSERVED**（确定性程序产出） | 本文件不定义任何 INFERRED / RECOMMENDED 指标；语步（move）、引用功能（citation function）、论证图一律不在本层，本期不实现 |
| 依赖 | 纯 stdlib（json/re/pathlib/statistics/collections），无模型、无 NLP 库、零 LLM 调用 | 引入 spaCy/torch/numpy 后本文件的确定性承诺失效，必须重新立项并记录模型 `sha256` |
| 可复现承诺边界 | **Canonical Document 到 OBSERVED 指标**（逐字节可重放） | 「PDF 到 Canonical」是上游引擎漂移，单独计量，不混进本承诺 |
| 比较域 | 同一 `metric_spec_version` + 同一词表 `fingerprint()` 的产物之间 | 跨词表版本、跨 `metric_spec_version`、跨 01-指标契约.md 的原始示例数（0.18/0.21/0.094/0.109 等）**一律不可比**，见 §7 |
| V7 引擎漂移对照 | — | **未实现**：profile 主链不含任何 drift 数值；marker_markdown 的 sha256 虽被记录但**无任何指标读取它**；仅有 `ENGINE_VERSION_NOT_RECORDED` 告警（漂移可发现、不可归因）。可选加分项见 §5.2 |
| **M-PAS-09 短语级精确率** | — | **已完成（N=40，agent 裁决）**：evidence span 已改为**被动短语 span**（助动词+可选副词+过去分词，含 has been / can be 前置），抽检**精确率 20/20 = 100%，95% Wilson CI 下限 0.839**；负例半边（10 条"有助动词但未判定"边界句 + 10 条无助动词句）漏报 **0/20**。**限制：agent 裁决非专家金标准，结论只覆盖这 40 条**，见 impl-baseline/_pas_spotcheck.md |
| **正文块内关键词行 / 行内数学式** | — | **已修（2026-09-13，issue #2）**：分句前对**等长副本**做非正文掩码（原文 offset 不变，span 仍切片回原文）——① 关键词行（`Keywords:` / `Key words:` / `关键词：`，含行内其余内容）；② 行内与行间数学 `$...$` / `$$...$$`（不跨行）；③ MinerU 的 `<sub>`/`<sup>` 标签。掩码后**不含字母的片段直接丢弃**。两个复现锚点已从 evidence.sample 消失（实测 34 篇：含 `eyword` 的样本 M-SLEN-01 3→0、M-LSF-16 2→0；锚点 [901,1091] / [323,469] 均不再出现）。语料级差值见〈正文口径与关键词行过滤〉 |
| 统计效力 | `n_valid >= 5` 才允许讨论分布 | `n_valid < 5` 时 `_corpus_summary.json` 记 `N_LT_5` 告警；`n_valid == 0` 时统计置 `null`，**禁止**输出期刊级结论 |

### 0.1 V7（PDF→Canonical 引擎漂移）现状：**未实现**

本轮**没有**实现 Marker 与 MinerU 的双链指标漂移对照。profile 侧只有两件事：① 每篇输入产物的 sha256（可寻址）；② `ENGINE_VERSION_NOT_RECORDED` 语料告警（暴露 paper-reader 的 `_META.json` 未记引擎版本）。因此「PDF→Canonical」的漂移**既没有数值、也无法归因到引擎版本**——§0 第 5 行的确定性承诺边界依然只覆盖 Canonical→指标。

**可选加分项**：`baseline_eval.py --drift-probe N`（默认关闭）提供文本层三项指标的探针，实测与局限见 §5.2。**未做部分**：结构类指标（章节骨架/图表公式落位）在 Marker 侧无对应物；按引擎版本分层的漂移；PDF 原点到 Canonical 的端到端漂移。

### 正文口径与关键词行过滤（issue #3 / #2，2026-09-13）

**正文口径（可执行的过滤规则）**：`canonical_text()` 只保留 `type == "text"`、非标题、且**不属于非正文 section** 的块；非正文 section 集合为
`{references, appendix, acknowledgments, keywords, front_matter}`；**首个二级标题之前的所有块 = `front_matter`**。本次新增第二条规则：**块文本以关键词标记开头**（`Keywords:` / `Key words:` / `关键词：`，正则只匹配行首标记）时，即使它落在正文 section 内也丢弃——期刊常把关键词排在 `A R T I C L E I N F O` 这类未被识别的标题下（实测 7 篇命中）。

**丢弃量（34 篇语料，直接取自 `_corpus_summary.json` 的 `non_prose_dropped`）**：

| 原因 | 篇数 | 块数 | 词数 |
|---|---:|---:|---:|
| `front_matter`（题名/作者/机构/邮箱） | 34 | 149 | 3 257 |
| `references` | 4 | 18 | 564 |
| `acknowledgments` | 10 | 11 | 409 |
| `appendix` | 4 | 15 | 308 |
| `keywords`（section 已被识别） | 3 | 3 | 35 |
| `keywords_line`（本次新增规则） | 7 | 7 | 93 |
| **合计** | — | **203** | **4 666** |

**误删自证**：抽验 `2020 - PSO Hyper-heuristic for Dynamic VRP [Okulewicz-Mandziuk]`，其 `front_matter` 的 9 个块恰为标题 / 作者 / 机构 / 邮箱，无正文段落；`abstract` 是独立 section 且被完整保留（`canonical_text()` 开头即摘要正文）。

**修正前后语料级差值**（同一语料 `corpus_id = a95e790e…`，34 篇；修前 / 修后为同机同参数两次运行）：

| 指标 | 修前 | 修后 | Δ |
|---|---:|---:|---:|
| M-SLEN-01（words/sentence） | 28.9662 | 26.2047 | **−9.53 %** |
| M-LSF-16（长句占比） | 0.1129 | 0.1172 | **+3.83 %** |
| M-PAS-09（被动率） | 0.2834 | 0.2861 | **+0.97 %** |
| M-MTLD-02（词汇多样性） | 72.9721 | 72.9654 | −0.01 % |
| M-CONN-30（连接词密度） | 5.9684 | 5.9712 | +0.05 % |
| M-PCNT-25（段长） | 89.9890 | 89.9890 | 0.00 % |

句数（M-LSF-16 / M-PAS-09 的分母）合计 **10 002 → 10 127（+125）**。变化来自两个方向，都可复现：① 关键词行与纯公式片段被丢弃 → 句数减少；② 句号后**紧跟数学或 `<sub>` 标签**时，修前因「句号后必须是空白或收尾符」而**不判句界**（把两句并成一句），掩码后正确判界 → 句数增加。净额为 +125，属修正而非口径漂移。

### 中文（zh）指标口径（issue #13）

**能力矩阵**（唯一事实源：`text_metrics._METRIC_LANGUAGE_CAPABILITY`）：

| 可测（12） | 口径 |
|---|---|
| `M-SLEN-01` | 单位 **`cjk-units/sentence`**：汉字数 + ASCII 字母 token 数（混合句"采用 K-means 算法"不漏计） |
| `M-LSF-16` | 长句阈值 **80 cjk-units**（英文是 40 词，不可直接套用） |
| `M-MTLD-02` | **字符级**（`params.tokenization = "cjk-char+ascii-token"`）。**与英文的词级 MTLD 不是同一个统计量，禁止跨语言比较** |
| `M-HED-14` / `M-BOO-15` / `M-AWR-03` | 命中数 / cjk-units，单位 `ratio`，附 `denominator_unit="cjk-units"` |
| `M-CONN-30c/k/r` | 命中数 / cjk-units × 1000，单位 `per-1000-cjk-units` |
| `M-CONN-30` | = 30c + 30k + 30r（三组**联合最长匹配**保证互斥，恒等式严格成立） |
| `M-PAS-09` | 分母 = 句数；被动 = 标记词（`被/受到/得到/加以/予以`）后**紧跟汉字**才算（裸标记不算，"由"与裸"为"因歧义过大不参与） |
| `M-PCNT-25` | 单位 **`cjk-units/paragraph`**，段落下限 **40 单位**（15 个汉字的块是图注不是段落） |

| 可测（第 13 条） | 口径 |
|---|---|
| `M-NOM-10` | **`variant = "cjk-abstract-noun-suffix"`**：中文动→名是**零派生**（优化/评估/分析本身即可作名词），英文的"后缀 + 动词基"规则无从下手；中文可稳定检测的信号是**抽象名词后缀**（`性 / 度 / 率`）→ 命中数 / cjk-units × 1000，单位 `per-1000-cjk-units`。**`化` 被显式排除**（优化/转化/深化 是动词，计入会测成别的东西，记录在 `excluded_suffixes`）。**与英文同名指标不是同一个统计量，禁止跨语言比较** |

| 不可测（1） | 原因 |
|---|---|
| `M-TENSE-28` | **中文没有时态**。给数字就是编造，故恒为 `null` + `CAPABILITY_NOT_SUPPORTED` |

两者都**不是 0**：`CAPABILITY_NOT_SUPPORTED` 明确表示"这门语言还没测这一条"，与"测到 0"可区分；草稿校验会把它们逐条跳过并记录原因。

**分句规则**：终止符 `。！？…`；`……`/`！！` 连写**只结一次**（否则切成单字片段）；引号/书名号内的句号保留在句中；**`；` 不切句**（中文在句内使用分号）；片段过滤从"无 ASCII 字母即丢弃"改为"**无字母且无 CJK** 才丢弃"——此前整句中文会被静默丢掉（不是记 0）。

**词表 provenance**：`data/lexicons/v2-zh/`，release 版本 `2.0-zh`。**中文无公开可再分发的 Hyland 对应资源**（issue #11 B 组结论），因此这是**策展词表（curated-original）**：来源与构建方法写在每个文件的 `source` 字段里（中文写作规范 + 通用虚词表 + 真实语料抽样人工确认），条目一律 ≥2 字以避免单字虚词误命中。升级路径：用人工标注集校准（κ ≥ 0.6）后 bump release 版本。

**跨语言不可比**：句长单位、密度分母、MTLD 口径在 en 与 zh 之间都不同，因此**禁止**把中文 draft 的数值直接比到英文语料的契约区间；契约必须与语料同语言（`build_contract` 用哪份 `_corpus_summary.json` 就继承哪份口径）。

### INFERRED 层准入检查表（红线，issue #9）

本层**只做 OBSERVED**（确定性规则、零 LLM）。语步（move）、引用功能（citation function）、论证图等语义指标**未实现**，且在下列 5 条**同时**落地之前不得引入（落地第 1 条即可开启评估，未落地时 `state` 必须保持 `OBSERVED`、`method` 必须保持 `rule`，由 `test_profile_papers.py` 与 `test_determinism.py` 断言守线）：

1. **标签集冻结为仓库内文件**（含版本号与 `sha256`）；
2. 建立**人工标注集**（建议 ≥ 200 句）并报告 **κ ≥ 0.6**，未达标不得写入 profile；
3. 在**留出集**上报告 precision / recall / F1；
4. 每句输出 `{sid, label, confidence_method, model_id, prompt_sha256, seed, temperature}`，且 `method ∈ {model, human}`；
5. 产出**不得进入任何 gate**，也不得带小数形式的"自报置信度"（未校准则只能给 `score_raw`）。

验收基准 V6 在本层为 **N/A**（无 INFERRED 指标）——N/A **不是及格**。



### 0.2 语言不支持：从「声明」到「可执行的失败契约」（A 轮）

**契约要素（冻结）**

- **判定量**：`cjk_ratio = 汉字数 / (汉字数 + ASCII 字母 token 数)`；**阈值 0.10**——纯英文学术文本通常 ≈ 0，中文文本通常 ≈ 0.5-0.96。唯一事实源：`text_metrics.detect_language()`。
- **单篇**：不支持篇目的**每一条指标** = `value: null`、`n: 0`、`warnings: ["LANGUAGE_NOT_SUPPORTED"]`，并计入该指标的 `n_missing`。因此「整语料语言不支持」的判据是 **`n_missing == n_papers`**。
- **语料**：`_corpus_summary.json` 顶层新增 `languages`（语言 → 篇数）与 `language_supported`（布尔）；不支持时追加语料告警 **`CORPUS_LANGUAGE_UNSUPPORTED`**。
- **草稿**：`validate_draft.py` 遇到不支持语言直接 **exit 2（language_unsupported）**，**不输出任何 pass/fail 结论**（不再依赖 `alpha_tokens >= 50` 这类与语言无关的门槛）。
- **红线**：**不得用 `0` 代替「未测量」**。`0` 只能表示「测到了，值为 0」；未测量必须是 `null`。历史缺陷正是把「中文测不出」静默写成 0，使产物看起来像合法结果。
- **三态语义（`language_supported`，冻结）**：该字段**不是布尔**，必须按三态解读；**null ≠ 通过**。

| `language_supported` | 含义 | 触发条件 | 语料告警码 |
|---|---|---|---|
| `true` | **每篇都被正面判定为受支持** | 全部篇目 `cjk_ratio <= 0.10` 且探测层可用 | 无 |
| `false` | **存在明确不支持的篇目** | 至少一篇 `cjk_ratio > 0.10` | `CORPUS_LANGUAGE_UNSUPPORTED` |
| **`null`** | **未判定**（**不是**「受支持」） | 存在未判定篇目且无明确 `false`：探测层不可用、文本无法判定、或空文本 | **`LANGUAGE_NOT_ASSESSED`** |

- **消费方规则（红线）**：**`null` 不得被当作语言已校验**。看到 `null` 时必须按「无语言结论」处理——不得据此解读任何指标数值，也不得在报告里写成「语言已通过」。`LANGUAGE_NOT_ASSESSED` 的 detail 已显式写明「null 意为 not assessed，不是 supported」。

**实测对照（真实中文语料，10 篇可 profile）**

语料：`/mnt/e/AllProjects202601/M-PCA/_paper-metrics-run/运筹与管理/corpus_ycgl_pdf/paper-conversion`（11 个目录，10 篇可 profile、1 篇跳过）

命令：

    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/profile_papers.py --corpus '/mnt/e/AllProjects202601/M-PCA/_paper-metrics-run/运筹与管理/corpus_ycgl_pdf/paper-conversion' --out /tmp/cn

| 观测量 | **修复前**（A 轮前，实跑） | **修复后**（探测层落地后，同一命令实跑） |
|---|---|---|
| M-SLEN-01 逐篇句数 | `1,1,1,2,2,2,3,7,7,9` → **中位 2** | 指标整体置空：`n_valid 0` / **`n_missing 10`** / median `null` |
| `text_metrics` 的 **13** 个指标（M-SLEN-01 / M-LSF-16 / M-MTLD-02 / M-HED-14 / M-BOO-15 / M-CONN-30 及三分量 / M-AWR-03 / M-PAS-09 / M-NOM-10 / M-TENSE-28） | median **0.0**、mean **0.0**、**n_missing 0** | 单篇 `value: null`、`n: 0`、`denominator: 0`、`warnings: ["LANGUAGE_NOT_SUPPORTED"]`；语料 `n_valid 0` / **`n_missing 10`** / median `null` |
| **M-PCNT-25（段落指标）** | median **0.0**、**n_missing 0**（曾被当作「计数仍可用」的例外） | **整条抑制**：`value: null`、`distribution: null`、`n: 0`、`denominator: 0`，并附 `note` 说明原因 |
| M-BOO-15 / M-CONN-30（30c/30k/30r）/ M-PAS-09 / M-NOM-10 / M-LSF-16 / M-MTLD-02 / M-AWR-03 / M-TENSE-28 | 全部 0.0 或极小值、n_missing 0 | 同 M-HED-14：**全部置空 + `LANGUAGE_NOT_SUPPORTED`** |
| `citation_style` | `unknown`（numeric 0 / author-year 0） | **仍为 `unknown`** —— 该条**不在语言门覆盖范围**（属引用/结构抽取问题），**不得**因语言契约生效就宣称它已修好 |
| `reference_count` | median **0**、`n_papers_with_references 0` | **仍为 0** —— 同上，属独立缺陷 |
| `corpus_warnings` | **仅 `SECTION_SKEW`**，无任何语言条目 | **只保留 `CORPUS_LANGUAGE_UNSUPPORTED` + `SECTION_SKEW`**——13×`NO_VALID_VALUES` / 13×`N_LT_5` / 13×`N_VALID_LT_3` 的噪声已**收敛**为这一条语言告警（否则 40+ 条重复告警会淹没真正的成因） |
| `languages` / `language_supported` | `{"unknown": 10}` / **`true`（旧默认：未判定被读成已通过）** | **`{"zh": 10}` / `false`** |
| 单篇语言记录 | 无 | `{language: "zh", cjk_ratio: 0.955892, language_supported: false}` |
| 草稿校验 `validate_draft.py` | 靠 `alpha_tokens >= 50` 与语言无关的门槛，**静默给结论** | **exit 2（`language_unsupported`）**：14 条条款全部 `skipped`、`pass=0 fail=0 warn=0`，**不输出任何通过/不通过结论** |
| 语料指纹 | `corpus_id = da93384bfdbef458…` | 同（语言字段不改变输入 hash） |

**历史记录 —— 过渡期实测（探测层落地前，docs-baseline 第二次实跑）**：`language_supported = null`、`languages = {"unknown": 10}`、告警出现 **`LANGUAGE_NOT_ASSESSED`**（detail 原文：`10/10 paper(s) have no language verdict ... language_supported is null meaning "not assessed", not "supported".`），单篇记录为 `{language: "unknown", cjk_ratio: null, language_supported: null}`。
**但同一跑里指标仍为 0.0 且 n_missing = 0**（如 M-HED-14 n_valid=10 / n_missing=0 / median 0.0）——即**「语言裁决」已修好，「指标置空」尚未生效**。两者必须同时到位才算契约生效：只修前者，产物仍会给出一堆 0.0；只修后者而无裁决，则无从说明为何置空。

**为什么连「段落计数」也不可信（必须写清楚，避免误以为计数可用）**：M-PCNT-25 的段落过滤器是「**空白分词 >= 15 词**」。中文没有词间空格，整段汉字会被当成**一个** token ⇒ ① 真实段落常被整段过滤掉；② 侥幸留下的段落报出的是**汉字串长度**而非词数（实测中文语料报出约 **62 "词"/段**）。因此**计数类指标同样不能被当作语言无关**，最终实现选择**整条抑制**并附 `note: "suppressed: paragraph segmentation depends on whitespace tokenisation, which CJK text does not provide (issue #10)"`。**结论：不受支持语言下 14/14 指标（13 条 text_metrics + M-PCNT-25）全部为 `null`。**

**草稿校验的失败契约（validate_draft）**：语言不支持 ⇒ `language_unsupported` + **exit 2**，**不调用任何 provider、不输出通过/不通过**；另设硬下限「**可评估条款数为 0 → exit 2**」（实测：14 条条款全部 `skipped`、`n_evaluable = 0`）。若检测器本身不可用，则 `available = false` 并在 warnings 明示「语言范围未检查」，可用 `--require-language-detector` 切换为 fail-closed。

**独立旁证（CJK 规模，来自 `evidence/lang_failure_probe.out`）**：CN-1 / CN-2 / CN-3 的汉字数分别为 6745 / 7276 / 7327，ASCII token 仅 268 / 1203 / 934 ⇒ `cjk_ratio` 约 **0.96 / 0.86 / 0.89**，**远超 0.10 阈值**，即当前语料必然落入「不支持」分支；对照英文篇 EN-1 的汉字数 = 0。

> **关于 `null` 的规则与三态演变（探测层已落地）**：`null` 表示**未判定**，只应出现在两种场景——**文本无法判定**（既无足够汉字也无足够 ASCII 字母）或**空文本**；此时按「无语言结论」处理，**不得**解读任何指标数值。三态演变可作回归锚点：① 早期版本对未判定回退默认 `true`（**已修缺陷**：未判定被读成已通过）；② 探测层落地前，`language_supported` 已为 `null` + `LANGUAGE_NOT_ASSESSED`（见上「历史记录」）；③ 探测层落地后，中文语料给出 `false` + `CORPUS_LANGUAGE_UNSUPPORTED`（见上表「修复后」列）。**任何版本下，`null` 都不得被当作语言已校验。**」

---

## 1. 读法：每个 OBSERVED 数值的固定契约

text_metrics.compute_text_metrics(text, bundle) 的返回值为 {metric_id: metric_dict}，metric_dict **必须**含下列字段（缺一即契约违规）：

```json
{
  "value": 0.0184,
  "n": 14,
  "denominator": 761,
  "unit": "ratio",
  "state": "OBSERVED",
  "method": "rule",
  "metric_spec": "M-HED-14",
  "evidence": {
    "count": 14,
    "sample": [{"span": [1024, 1031], "excerpt": "suggests"}]
  },
  "warnings": []
}
```

- `value`：唯一数值。为 NaN 时必须输出 `null` 并在 `warnings` 写明原因（JSON 中不得出现 NaN）。浮点统一 `round(x, 6)`。
- `n`：分子的原始计数（整数）。`denominator`：分母的原始计数（整数）。
- `state` 恒为 `"OBSERVED"`；`method` 恒为 `"rule"`（本阶段无统计/模型路径）。
- `evidence.count`：命中总数，**等于** `n`（ratio/密度类）。`evidence.sample` 最多 5 条；`excerpt` 最多 80 字符。
- **span 坐标系**：`[start, end]` 是相对**本次入参 text**（单篇论文全文拼接串）的 char 偏移，不是文件偏移、不是块内偏移。第三方回指原文的方法：把该篇 `_per_paper_metrics.jsonl` 对应行的 `inputs[].artifact` 原文按同一条拼接规则（按块序、块间以换行连接）重建字符串，再按下面的截断规则切片比对。
- `warnings`：例如 `mtld_short_text`、`no_evidence`、`unresolved_tense`。告警**不改变** `value`，只声明解释边界。
- **分层值（`_corpus_summary.json` → `metrics.<id>.by_section.<section>`）不携带证据指针**：每个分层显式带 `"evidence": null`（issue #6——缺省键与「没有证据」无法区分，故改为显式）。逐条回指原文请用 `_per_paper_metrics.jsonl`：分层只是把同一批 per-paper 值按 section 重新聚合，样本仍在该文件的 `metrics.<id>.evidence.sample`。
- **`mean` 的聚合口径（2026-09-15，issue #18-5）**：`_corpus_summary.json` 里 `metrics.<id>.mean` 是**逐篇均值的均值**（`weight_mode = "equal_paper"`、`mean_basis = "mean_of_per_paper_means"`），**不是**把各篇原始计数池化后的比值。两者会差：S-REF-14 的 mean-of-means 与池化值实测相差 11%。本文件里凡标"池化"的实测数字都**不可**直接与产物 `mean` 比较；比较时必须说明用的是哪一种。
- **混合单位护栏（2026-09-15，issue #18-7）**：同一指标在同一语料里出现**多于一个** `unit`（只统计**有值**的记录，未测量记录的占位 unit 不计）时，`_corpus_summary.json` 的 `mean` 置 `null` + 告警 **`MIXED_UNIT_AGGREGATION`**，并列出 `units_measured`。理由：`S-TBL-09` 英文是 per-1000-words、中文是 per-1000-cjk-units，混合语料直接平均等于把两种量纲相加；单语言语料各自运行时不触发。
- **`class`：作者风格 vs 工具链缺陷（2026-09-15，issue #18-9）**：每条指标记录带机器可读 `class`——`author_style`（反映作者/期刊的写作选择）或 `toolchain_defect`（由转换链造成：LaTeX 残留、表格正文缺失、抽取图分辨率不足）。`_domain_profile.md` 按 class 分表渲染并标注红线；**契约层（`_writing_contract.yaml`）永不接受 `toolchain_defect` 类指标**——工具链缺陷不是可学习、可模仿的写作规范。实现上：`build_contract._is_toolchain_defect()` 令这类指标`_is_draft_checkable` 直接返回 False，它们从 clauses 中剔除并给 **`TOOLCHAIN_DEFECT_METRICS_EXCLUDED`** 告警（与"草稿没有块结构"的 `NON_PROSE_METRICS_EXCLUDED` 分开：后者是能力边界，前者是红线）；若调用方`--metrics`**显式点名**一个 `toolchain_defect` 指标则直接报错，不做静默剔除。

### 1.1 证据抽样与复现规则（冻结，v1.1）

- **抽样规则**：单篇论文内**按文档序取前 5 个命中 span**（`_EVIDENCE_SAMPLE_MAX = 5`）；基线核验器（`baseline_eval.py`）再从中**每指标最多取 3 条**（`MAX_EVIDENCE_PER_METRIC = 3`）做回指。抽样只依赖内容，**不含随机数、不含时间**，故同一输入必得同一 Evidence 列表。
- **匹配规则**：多词词条**最长匹配、不重叠**，单个 span 记 1 次；全部小写匹配；同一 span 不被重复计数（hedge/booster 交集为空、connectors 三组互斥，均由 `load_lexicons` 硬校验）。
- **截断规则（易错点）**：`excerpt == text[start:end][:80]`，即**先切片、后截断 80 字符**。**不得**用 `text[start:end] == excerpt` 全切片比较——长 span 会被合法截断，全切片比较会把正确数据判成错误（曾导致回指率被误报为 0.769）。
- **块索引型证据**（`M-PCNT-25`）：形态为 `{block_index, section, words, excerpt}`，**没有 span**；核验方式 = 该论文 content_list.json 的 `blocks[block_index]` 存在且其文本以 `excerpt` 开头。两种形态**合并**计算回指率。
- **证据本身必须可复现**：`evidence.count == n`，且每条证据必须能由原文逐字符重放（span 型用上面的截断规则，块索引型用块文本前缀）。**无法回指的数值不得通过 OBSERVED 层验收**：基线报告以回指率为 gate（目标 >= 0.95，真实语料实测 1.000）。
- **`inputs` 现在含图片哈希（2026-09-15，issue #18-6）**：每篇论文的 `inputs` 除 content_list / marker markdown / paper_reader meta 外，还逐张登记 `figure_image:<img_path>` 的 sha256（文件集取自 `doc_model` 的 FIGURES 流，即 `S-SIZ-04` 实际打开的那批）。原因是该指标读图片字节：图片不进 `inputs`，换一张图会改变数值却不改变 `corpus_id`，审计只能判 `unexplained_drift`——而那是留给"确定性被破坏"的判决。**副作用**：`corpus_id` 因此变化，旧记录必须重登记（`audit_corpus.py --corpus <path>` 会打印新身份）。
- **语言自查（先做这一步，再看任何数值）**：唯一事实源是 `text_metrics.detect_language(text)`，返回 `{language, cjk_ratio, supported}`；`cjk_ratio > 0.10` ⇒ 不支持。复算命令：
  `cd ~/projects/dc-skills && uv run python -c "import sys; sys.path.insert(0,'paper-metrics/scripts'); import text_metrics as t; print(t.detect_language(open('<canonical_text.txt>').read()))"`
  语言不支持时**不要解读任何数值**：依 §0 契约，此时每指标为 `null` + `LANGUAGE_NOT_SUPPORTED`、计入 `n_missing`，语料级报 `CORPUS_LANGUAGE_UNSUPPORTED`。契约与中文语料实测对照见 **§0.2**。
- **第三方复算命令**：
  `cd ~/projects/dc-skills && uv run python paper-metrics/scripts/baseline_eval.py --corpus <corpus> --out <out>`
  报告 `_baseline_report.json` 的 `evidence_samples[]` 逐条给出 `span` / `block_index`、`source_slice`（实测切片）与 `verified` 布尔值。

### 1.2 Canonical 文本重建规则（完整、可执行 —— 回指的前置条件）

**为什么必须写死**：evidence 的 `span` 是相对 `canonical_text()` 的 char 偏移。若第三方按更朴素的规则重建（例如「所有 text 块按块序用换行拼接」），坐标系会整体错位——同一篇论文官方 **5831** token，朴素重建得 **6115** token（+284），回指必然全错。**回指率只有在重建规则完全一致时才有意义。**

**规则（逐条，按此顺序）**

1. 读该篇 `mineru/**/*_content_list.json`（排除文件名含 `_v2` 者；有多份时取排序后第一份），**按文件顺序**遍历块。
2. **标题块**：`type == "text"` 且 `text_level` 为整数且 >= 1 ⇒ 该块是标题，**永不计入正文**；其中 `text_level == 2` 的标题会**切换当前章节**。
3. **running head**：`type == "header"` 的块也**永不计入正文**；仅当其标题解析为 references / bibliography 时，把当前章节切到 references（MinerU 会用 header 型块输出参考文献标题）。
4. **正文块**：`type == "text"`，且 `text.strip()` 非空。
5. **章节归属**：当前章节 = 最近的前置 level-2 标题解析出的 canonical 标签；第一个 level-2 标题**之前**的所有块归属 `front_matter`。
6. **排除非正文章节**：canonical 标签属于 {references, appendix, acknowledgments, keywords, front_matter} 的块**一律丢弃**。`front_matter` 在排除集内 ⇒ 标题页、作者行、未打标题的摘要都**不进入**正文。
7. **canonical 标签解析顺序（关键）**：① 精确匹配表（`_CANONICAL_MAP`）→ ② **有序**关键词表（`_SECTION_KEYWORD_PATTERNS`）取**首个**命中 → ③ 否则保留归一化标题本身。**正文类关键词一律排在非正文关键词之前**，所以标题同时含两类词时按**正文**处理：例如 "Related Work and References" 解析为 `related_work`（正文），**不得**因为含 "reference" 就把整节丢掉。
8. **标题归一化**：剥掉编号前缀（`1` / `1.2` / `Section 4:` / `I.` / `第X章` / `(1)`）与尾部标点，然后转小写。
9. **拼接**：保留下来的块用**两个换行符**（LF x 2）连接。
10. **NFC 归一**：对拼接**之后**的整串做 `unicodedata.normalize("NFC", ...)`，恰好一次（拼接前归一化会得到不同结果）。
11. **分词**：取全部匹配 `[A-Za-z][A-Za-z'-]*` 的子串并转小写；其个数即 `M-SLEN-01.denominator`。

**最小示例**：见随交付附带的独立实现 `impl-baseline/_rebuild_check.py` 的 `rebuild()` —— 概括即「遍历块 → level-2 标题切章节 → 跳过标题块与非正文块 → 两个换行符连接 → NFC」。

**独立可执行核对**（该脚本**不 import 仓库任何模块**，故为真正的第二条路径）：

    cd ~/projects/dc-skills && uv run python '<交付目录>/_rebuild_check.py' --corpus '/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis' --jsonl /tmp/prof/_per_paper_metrics.jsonl

**自检点（冻结值，终检实测）**

- 全语料 **34/34 篇 token 数逐篇精确相等**（脚本 exit 0）。
- 单篇锚点：`2018 - GPU Ising Computing for CO [Cook et al]` → 重建 token 数 **5831**，等于官方 `M-SLEN-01.denominator`；重建文本 **35,867** 字符。
- **反例警戒**：漏掉第 6 条（不排除 `front_matter`）时该篇重建得 **6115** token（+284）—— 这正是「按文档复算却对不上」的成因。
- 一处必须照抄的实现细节：某篇的标题 "6 Acknowledge" 解析为标签 `acknowledge`（**不在**排除集内），故该节**被计入正文**。独立重建器若不照此处理，就会出现 -47 token 的偏差。

---

## 2. 冻结常量与版本总表

| 常量 / 文件 | 冻结值 | 版本 | 依据 |
|---|---|---|---|
| `TEXT_METRICS_VERSION` | `"1.0"` | - | INTERFACES.md §3 |
| `LEXICON_VERSION` | `"1.0"` → 现行 **1.1**（connectors 去歧义：剔除 as / so / still / since） | 词表 8 文件 | INTERFACES.md §2 |
| connectors.json sha256 | `c12461c58d0f75ab523c410547e90affb2a6553bc198343682767cc7b735b19b` | v1.1 | 终锁实测 |
| `schema_version`（profile 契约） | `"2.0"` | - | INTERFACES.md §4.1 |
| `_LONG_SENTENCE_WORDS` | `40` | - | 01-指标契约 M-LSF-16 |
| `_MTLD_TTR_THRESHOLD` | `0.720` | - | McCarthy & Jarvis 2010；TAALED 源码硬编码 |
| `_MTLD_MIN_FACTOR` | `10` | - | 同上 |
| `_ALPHA_TOKEN_RE` | `[A-Za-z][A-Za-z'-]*`（小写化后匹配） | - | INTERFACES.md §3 |
| CI95 临界值 | `n-1=1..30` 内置 t 表；`n>=30` 用 `1.96` | - | INTERFACES.md §4.4 |
| **语料级分位口径** | **nearest-rank，`idx = round(p*(n-1))`，不插值**；偶数 n 的 median 取两个中间观测的**较小者**（n=34、p=0.5 时 `round(16.5)=16` → v[16]） | 随 `schema_version 2.0`（顶层字段 `quantile_method = nearest_rank_no_interpolation`） | 冻结实现 `profile_papers._percentile`；**与 statistics.median / numpy 默认线性插值不可互换**，引用 median 必须注明口径 |
| 词表 hedge / booster | 各 >= 40 条，**交集必须为空** | v1 | Hyland 1998 / 2005 |
| 词表 connectors | contrastive / causal / result 三组各 >= 15 条，**两两交集为空** | v1 | PDTB 2.0 Annotation Manual Appendix A |
| `nominalization_suffixes` | `tion sion ment ness ity ance ence ancy ency ism ist`（11 个，不带连字符） | v1 | INTERFACES.md §2 |
| `nominalization_verb_bases` | >= 150 条动词词基 | v1 | Biber & Gray 2011 |
| `nominalization_denylist` | >= 60 条伪派生名词（section/station/mention/action/position/condition/mission/version/question/function 等） | v1 | 03-工具与依据.md §3.2 |
| `academic_words` | >= 300 条（AWL 代表性子集） | v1 | Coxhead 2000 |
| `stopwords` | >= 120 条英文功能词 | v1 | 通用 |

**词表指纹**：`LexiconBundle.fingerprint()` = 对全部 `(name, sha256)` 排序后拼接再取 sha256；写入 `_domain_profile.json#/lexicons`。**指纹变化则全部词表类数值不可与旧产物比较**。

---

## 3. §3 表内 11 个 metric_id 的逐条定义

每条固定 9 项：定义 / 公式 / 分子-分母-单位 / 依赖与版本 / 反映什么写作行为 / 不能推断什么 / 已知失效场景 / 复算路径 / 依据文献。

> **语料级分位口径（全节适用）**：本节各指标的语料级 p25 / median / p75 一律采用 **nearest-rank、不插值**（见 §2 总表对应行）。第三方若用 `statistics.median` 或 numpy 默认线性插值重算，**14/14 的 median 都会对不上**（均值不受影响）。引用 median 必须注明此口径。

### 3.1 `M-SLEN-01` 句长均值

- **定义**：以规则分句 `split_sentences()` 切出的合格句为单位，统计句内 alpha token 数的均值；同时输出分布 `{median, p25, p75, std}`（均 round 6）。
- **公式**：value = 词数 / 句数 = denominator / n
- **分子 / 分母 / 单位**：分子 = 词数（记为字段 `denominator`）；分母 = 合格句数（记为字段 `n`）；单位 `words/sentence`。
- **分位口径**：语料级 p25 / median / p75 用 nearest-rank、不插值（§2 总表）；n=34 时 median = v[16]（下中位数），**不是** `statistics.median` 的插值结果。
- **口径陷阱（必读）**：本条是本契约中**唯一倒置**（value = denominator / n）的指标——字段语义被冻结为 n = 句数、denominator = 词数，故复算必须用 denominator/n。第三方若按「分子除以分母」的直觉读 n/denominator，会得到句/词（约 0.02 而非约 50）。这是 INTERFACES.md §3 表列的既定语义，不在实现层纠正，只在此显式声明。另有两类指标同样**不满足** value == n/denominator：**计数/分位型**（`S-CAPL-05` 字符中位、`S-TBL-06` 列数中位、`S-REF-14` 被引深度中位——没有"分子"）与**按每千单位缩放型**（`S-TBL-09`，value = n/denominator × 1000），详见 §4.6 的恒等式适用范围。
- **依赖与版本**：`split_sentences()`（缩写保护表：et al. / Fig. / Eq. / i.e. / e.g. / vs. / cf. / approx. / no. / Sec. / Ref.、方括号编号、小数、单字母缩写）+ `tokenize()`；二者随 `TEXT_METRICS_VERSION` 冻结。丢弃无字母 token 的碎片。
- **反映什么写作行为**：句子平均信息打包量，以及长短交替的节奏（配合 std 与 p75-p25 的离散度一起看）。
- **不能推断什么**：不能推断句子更难/更易读（句长与难度非线性）；不能推断作者水平；不能跨语言或跨领域直接比较（术语长度天然不同）。
- **已知失效场景**：① 公式块/化学式被误分句会人为拉长句；② 双栏 PDF 阅读顺序错乱导致跨栏拼接成超长「句」；③ 表格题注被当正文句；④ Sec. 未进缩写保护表时会把「Sec. 4 shows」切成两句。
- **复算路径**：对 evidence 抽样句人工数词数，与 value x n 一致；或对同一拼接串数总词数后除以 n。
- **依据文献**：01-指标契约 M-SLEN-01（设计案行121-127）；句长属描述性计量，无独立效度主张；其解释边界受 Gibson 1998（DOI `10.1016/s0010-0277(98)00034-1`）关于句法加工负荷的限定。

### 3.2 `M-LSF-16` 长句占比（>= 40 词）

- **定义**：词数 >= `_LONG_SENTENCE_WORDS`（=40）的合格句占合格句总数的比例。
- **公式**：value = 长句数 / 句数 = n / denominator
- **分子 / 分母 / 单位**：分子 = 长句数（n）；分母 = 合格句总数（denominator，与 M-SLEN-01 同一集合）；单位 `ratio`。
- **依赖与版本**：与 M-SLEN-01 **共用同一分句/分词实现**（禁止两套代码，否则两条指标会自相矛盾）；阈值 40 随 `TEXT_METRICS_VERSION` 冻结，改动必须 bump 版本。
- **反映什么写作行为**：超长句的占比，即「一句塞入过多信息」的倾向；与句长均值互补（均值相同、长尾可不同）。
- **不能推断什么**：不能推断可读性变差（长句可以是精确的条件句）；不能推断句法复杂度（本指标只看长度，不看嵌套）。
- **已知失效场景**：① 阈值漂移（改成 35/45 会显著改变数值）；② M-SLEN-01 与 M-LSF-16 分句口径不一致时会出现「均值很小但长句很多」的矛盾产物，属契约违规；③ 参考文献条目被误当句子时会产生大量伪长句（由章节切分规避）。
- **分位口径**：语料级 median 用 nearest-rank、不插值（§2 总表）；n=34 时取 v[16]。
- **复算路径**：从逐句 token 数序列直接过滤 >= 40 再除以句数，无需任何词表；抽检 20 句。
- **依据文献**：01-指标契约 M-LSF-16（设计案行989）。

### 3.3 `M-MTLD-02` MTLD 词汇多样性

- **定义**：Measure of Textual Lexical Diversity：按 token 顺序扫描，类型-形符比（TTR）降到阈值 0.720 以下即记一个「因子」；末尾不足一个因子的残余按比例计分。取**正向与反向的均值**（双向平均）。
- **公式**：value = (正向 MTLD + 反向 MTLD) / 2，其中单向 MTLD = 总 token 数 / (完整因子数 + 尾因子比例)
- **分子 / 分母 / 单位**：分子 = token 数（n）；分母 = 1（denominator = 1 为**非比率占位**，不表示分母为 1）；单位 `index`（无量纲）。
- **依赖与版本**：`_MTLD_TTR_THRESHOLD = 0.720`、`_MTLD_MIN_FACTOR = 10`；bidirectional = True；分词用 `tokenize()`（小写 surface form，**不做 lemma**）。三者随 `TEXT_METRICS_VERSION` 冻结。
- **短文本规则**：token 数 < 2 x `_MTLD_MIN_FACTOR` = 20 时 value = null 并附 warning（不得照报数值）。
- **反映什么写作行为**：用词复用程度与术语重复策略——同一长度下，类型分布越慢饱和，值越高。
- **不能推断什么**：**不能**把高 MTLD 读作「词汇丰富 = 写作好」（它只测类型分布，不测用词恰当性）；不能跨文本长度直接比较（MTLD 对长度敏感）；不能反映句法复杂度。
- **已知失效场景**：① 未剔引文标记/图表题注则虚高；② 大小写与连字符口径不同则出现百分之几量级的漂移（本实现固定小写 + 保留内部连字符与撇号）；③ 只跑单向时值系统性偏高；④ 短于 20 token 的 section 不得输出数值。
- **复算路径**：第三方用 `lexicalrichness`（pin 版本）或 R `mtldr` 复算，差异应为 0 或可归因于双向/单向与阈值定义；抽检 3 篇手工重算因子数。
- **依据文献**：**McCarthy & Jarvis 2010**, *Behavior Research Methods* 42(2):381-392, DOI `10.3758/brm.42.2.381`（一手实现证据：TAALED 源码硬编码 0.720 / min=10 / 双向均值）。

### 3.4 `M-HED-14` hedge（模糊限制语）span 占比

- **定义**：命中**冻结 hedge 清单**的 span 数占 alpha token 数的比例。多词短语**最长匹配、不重叠、单 span 记 1 次**；全部小写匹配。
- **公式**：value = hedge span 数 / alpha token 数 = n / denominator（**ratio，不乘 1000**）
- **分子 / 分母 / 单位**：分子 = hedge span 数（n）；分母 = alpha token 数（denominator）；单位 **ratio**（与 INTERFACES §3 表一致；实测产物即此口径，例如某篇 value 0.007031 = n 41 / denominator 5831）。
- **依赖与版本**：`data/lexicons/v1/hedge.json`（>= 40 条，source 必填、记 Hyland 出处与 sha256）；匹配规则随 `TEXT_METRICS_VERSION`。**清单不得由 LLM 扩充**。
- **反映什么写作行为**：作者对断言强度与不确定性的**显性标示策略**——即「把话说满还是留余地」在词面上的痕迹。
- **不能推断什么**：不能推断作者性格谨慎与否；不能推断研究可信度；不能把「hedge 少」读作「作者自信」（这是写作教学的流行误说）；本实现是 per-token ratio，与文献报告的 0.021 **同口径、可直接比较量级**（但仍不可跨词表比较）。
- **已知失效场景**：① may 的「五月/人名」歧义、about 的介词义产生少量假阳性（本实现不做 POS 消歧，该风险由精确率抽检暴露）；② 多词 hedge 漏匹配导致漏计；③ 词表版本不明时数值不可复核（本实现强制 sha256 入产物）；④ hedge 与 booster 清单若重叠会双计（由 `load_lexicons` 的交集为空校验兜住）。
- **复算路径**：仅凭 `evidence.sample[].span` 切片原文即可核对每一次命中；另可用 grep -o -i 逐词表条目复算；抽检 50 span 计算精确率（要求 >= 0.9，否则调词表并 bump 版本）。
- **依据文献**：**Hyland 1998**, "Boosting, hedging and the negotiation of academic knowledge", *Text* 18(3), DOI `10.1515/text.1.1998.18.3.349`；**Hyland 2005**, "Stance and engagement", *Discourse Studies*, DOI `10.1177/1461445605050365`；Hyland 2005 *Metadiscourse*（章节 DOI `10.5040/9781350063617.0011`，附录词表）。03-工具与依据.md §3.1 判定：**仅可复现**，故必须随产物发布词表 sha256 + 匹配规则 + 分母口径。

### 3.5 `M-BOO-15` booster（强势断言）span 占比

- **定义**：命中**冻结 booster 清单**的 span 数占 alpha token 数的比例；与 hedge 完全对称（同样最长匹配、不重叠）。
- **公式**：value = booster span 数 / alpha token 数 = n / denominator（**ratio，不乘 1000**）
- **分子 / 分母 / 单位**：分子 = booster span 数（n）；分母 = alpha token 数（denominator）；单位 `ratio`（与 M-HED-14 同为 per-token ratio，可直接与文献 0.013 比较量级）。
- **依赖与版本**：`data/lexicons/v1/booster.json`（>= 40 条，含 source 与 sha256）；**与 hedge 集合交集必须为空**（`load_lexicons` 硬校验，违反抛 `LexiconError`）。匹配规则随 `TEXT_METRICS_VERSION`。
- **反映什么写作行为**：作者强化断言的显性策略（clearly / obviously / must / always / indeed / demonstrate / establish），与 hedge 合看可刻画「立场强度带」。
- **不能推断什么**：不能论断正确性；不能推断作者自信程度；**不能**说「booster 多 = 差」（无任何效度依据支持该方向性结论）；不能把 hedge/booster 相除当「谨慎指数」。
- **已知失效场景**：① must 的义务义与推断义不分；② clearly / obviously 的语用差异在词表层面不可分；③ 与 hedge 未互斥时同一 span 双计（由交集校验兜住）；④ 词表来源缺失时数值不可复核（故 source 为必填字段）。
- **复算路径**：同 M-HED-14（char offset 切片 + 独立 grep）；另跑 hedge 与 booster 交集为空断言。
- **依据文献**：**Hyland 1998**, DOI `10.1515/text.1.1998.18.3.349`（boosting 与 hedging 并列研究）；**Hyland 2005**, DOI `10.1177/1461445605050365`。03-工具与依据.md §3.1。

### 3.6 `M-CONN-30` 连接词总密度（派生量）

- **定义**：三类连接词（对比 + 因果 + 结果）命中总数占 alpha token 数的比例，换算为每千词密度。**总密度必须由三分量重算，不得独立统计**。
- **公式**：value = (N_contrast + N_causal + N_result) / alpha token 数 x 1000
- **恒等式（测试必须断言）**：value(M-CONN-30) 等于 value(30c) + value(30k) + value(30r)，容差 1e-6。
- **舍入不闭合（已知，属契约的有意选择）**：总密度 = **三个已 round(6) 的分量之和再 round(6)**，并非由原始计数 n 直接算 ×1000。因此少数论文会出现 value(M-CONN-30) 与 n/denominator×1000 的微小差异（实测 34 篇中 2 篇，最大 1.33e-6）。**恒等式优先于「由原始计数直接算」**：第三方复算请**以分量之和为准**，不要用 n/denominator×1000 去卡 1e-6 容差。复算命令：`uv run python -c "import json;d=json.load(open('<out>/_corpus_summary.json'));m=d['metrics'];print(m['M-CONN-30c']['value']+m['M-CONN-30k']['value']+m['M-CONN-30r']['value']==m['M-CONN-30']['value'])"`（应输出 True）。
- **分子 / 分母 / 单位**：分子 = 三类命中总数（n，三清单互斥故不重复计数）；分母 = alpha token 数（denominator）；单位 `per-1000-words`。
- **依赖与版本**：`data/lexicons/v1/connectors.json`（groups 恰含 contrastive / causal / result 三组，各 >= 15 条，两两交集为空；loader 拆为 connectors/contrastive 等三个 Lexicon）；多词连接词最长匹配；source 记 PDTB 2.0。
- **反映什么写作行为**：篇章衔接的**显性化密度**——作者把转折/因果/结果关系写成连接词，而非留给读者推断的倾向。
- **不能推断什么**：不能推断逻辑连贯性（显性连接词多不等于论证好，也可能只是关系简单）；不能推断因果关系真实存在；不能推断读者理解度。
- **已知失效场景**：① since 的时间义被计为因果；② as a result 归 result 而非 causal（若归错会破坏互斥）；③ thus / therefore 在 Method 中的过渡用法被计为结果标记；④ 原设计案示例数值本身不自洽（0.042+0.038+0.029 = 0.109，而总密度写 0.094），**禁止**用该示例数当基线，以本实现产物为准。
- **召回下界（issue #5，选项 ②，2026-09-13 实测）**：词表 v1.1 删除了 9 个无法词法消歧的裸词，因此 `M-CONN-30k`（因果）与 `M-CONN-30` 应读作**下界**，不是无偏估计。代价量级（34 篇 / 10 127 句，按当前版本分句与 tokenize 统计"句中出现该词"）：`as` 1 525 句、`while` 416、`through` 192、`so` 132、`since` 74、`still` 72、`yet` 37（合计 2 448 句，占全部句子 **24.2 %**，其中句首 325 句）——这些句子里**真实**的因果/转折用法一律不再进入分子。复算命令：`uv run python /tmp/verify_recall_dc.py`（脚本：遍历语料 `canonical_text()` → `split_sentences()` → `tokenize()` 计数；数字随 #2 的分句修正而变化，修前口径为 2 212 句 / 22.1 %）。多词变体（`as a result of` / `as a consequence` / `so that` / `because of` / `due to` …）仍保留并按最长匹配优先，故下界只在"裸词"这一层成立。**口径待统一**：issue 正文记裸 `as` 占修复前因果分量 **77.6 %**，`connectors.json` 的 `notes` 记 **73.5 %**（语料篇数不同）——两者不可同时引用，引用时请注明语料与版本。多词变体（`as a result of` / `as a consequence` / `so that` / `because of` / `due to` …）仍保留并按最长匹配优先，故下界只在"裸词"这一层成立。**口径待统一**：issue 正文记裸 `as` 占修复前因果分量 **77.6 %**，`connectors.json` 的 `notes` 记 **73.5 %**（语料篇数不同）——两者不可同时引用，引用时请注明语料与版本。
- **复算路径**：核验恒等式（容差 1e-6）；按 `evidence.sample[].span` 切片抽检 30 个 span；三清单互斥性由单元测试保障。
- **依据文献**：**PDTB 2.0 Annotation Manual**（免费 PDF：https://catalog.ldc.upenn.edu/docs/LDC2008T05/manual/pdtb-annotation-manual.pdf ；数据本体 LDC2008T05 属 LDC User Agreement、不可再分发；手册 Appendix A 明列 100 型 explicit connectives / 18459 tokens / 111 senses）。03-工具与依据.md §1.2(10)：这是**零成本获得可验证连接词表**的正解，不必购买 LDC 数据。

#### 3.6.1 `M-CONN-30c` 对比类分量

- **定义**：contrastive 组连接词命中数密度（however / whereas / in contrast / on the other hand / although 等）。
- **公式**：value = N_contrast / alpha token 数 x 1000；分子 n = N_contrast，分母 = alpha token 数，单位 per-1000-words。
- **依赖与版本**：同 M-CONN-30（共享 connectors.json 的 contrastive 组与同一最长匹配器）。
- **反映**：论证转折的显性化程度。**不能推断**逻辑严密性。**失效**：句首与句中的 however 未区分（本实现不区分，属已声明边界）。
- **复算路径**：按 span 切片抽检；核对三分量之和恒等式。
- **依据文献**：PDTB 2.0 Manual Appendix A（转折类 sense 组）；01-指标契约 M-CCR-11。

#### 3.6.2 `M-CONN-30k` 因果类分量

- **定义**：causal 组连接词命中数密度（because / since / due to / owing to）。
- **公式**：value = N_causal / alpha token 数 x 1000
- **分子 / 分母 / 单位**：分子 = causal 组命中数（n = N_causal）；分母 = alpha token 数（denominator）；单位 `per-1000-words`。
- **依赖与版本**：同 M-CONN-30（共享 connectors.json 的 causal 组与同一最长匹配器）。
- **反映**：**显性因果衔接标记（explicit causal connective）的密度**——作者把因果/条件关系写成显式标记的习惯，**不代表**因果推理的质量。
- **措辞修正（基于实测）**：本指标**不得**读作「因果衔接密度」或「因果推理强度」。① 早期词表（connectors v1.0）含高歧义词 **as / so / still / since**，其中 as 占抽样命中的绝大多数，会把条件、时间、方式等非因果用法一并计入；② v1.1 已剔除该歧义子集（因果组现 18 条：as a result of、because、due to、in that、given that、owing to、seeing that、now that 等）。
- **前后数值对比（真实 34 篇语料，official 路径逐篇 n 求和）**：

| 组 | v1.0（含歧义项） | v1.1（已去歧义） | 变化 |
|---|---|---|---|
| causal（M-CONN-30k） | 2228 | **209** | -90.6% |
| contrastive（M-CONN-30c） | 1336 | **810** | -39.4% |
| result（M-CONN-30r） | 502 | **376** | -25.1% |
| 合计（M-CONN-30） | 4066 | **1395** | -65.7% |

  → 早期 2228 次 causal 命中里绝大部分并非因果标记：引用 v1.0 时代的连接词数值必须先换算或作废。实测 v1.1 因果组中歧义子集 {as, so, still, since} 命中 = **0**。
- **直接证据（对 v1.0 产物复算）**：v1.0 运行的 M-CONN-30k evidence 抽样共 170 条，其中 `as` 114 条 + `As` 11 条 = **125 条（73.5%）**，另有 `through` 17 条——**独立复现了评审给出的 73.5%**，证明当时的「因果密度」主要由非因果用法构成。
- **不能推断**因果性真实存在或论证有效。**失效**：即便去歧义后，in that / now that / given that 仍有语用歧义；连接词密度高也可能只是关系简单。
- **复算路径**：按 span 切片抽检；核对恒等式。
- **依据文献**：PDTB 2.0 Manual（Contingency 类）；01-指标契约 M-CAUS-12。

#### 3.6.3 `M-CONN-30r` 结果类分量

- **定义**：result 组连接词命中数密度（therefore / thus / hence / as a result / consequently）。
- **公式**：value = N_result / alpha token 数 x 1000
- **分子 / 分母 / 单位**：分子 = result 组命中数（n = N_result）；分母 = alpha token 数（denominator）；单位 `per-1000-words`。
- **依赖与版本**：同 M-CONN-30（共享 connectors.json 的 result 组与同一最长匹配器）。
- **反映**：结论标记与推论显性化程度。**不能推断**结论正确性。**失效**：Method 节的过渡用法被计为结果标记。
- **复算路径**：按 span 切片抽检；核对恒等式。
- **依据文献**：PDTB 2.0 Manual（Contingency: Result）；01-指标契约 M-RCR-13。

### 3.7 `M-AWR-03` 学术词占比

- **定义**：命中冻结学术词表（AWL 代表性子集 >= 300 条）的 token 数占 alpha token 数的比例。**surface form 精确匹配**（本阶段无 lemma 化，见 §7）。
- **公式**：value = 命中数 / alpha token 数 = n / denominator
- **分子 / 分母 / 单位**：分子 = 命中数（n）；分母 = alpha token 数（denominator）；单位 `ratio`。
- **依赖与版本**：`data/lexicons/v1/academic_words.json`（source = Coxhead (2000) AWL，>= 300 条，sha256 入产物）。
- **反映什么写作行为**：文本落在「一般学术词汇」与「领域专用词汇」之间的取位。
- **不能推断什么**：不能推断写作专业性；AWL 自身有领域偏差（对工程/数学论文的覆盖率低于人文社科）；**不能**与「好/坏写作」挂钩。
- **已知失效场景**：① 词表版本不一致导致大幅漂移；② 未 lemma 化时屈折形式（时态/复数变体）漏计，属系统性低估；③ 多词学术短语未定义；④ 公式符号污染分母（alpha token 规则已排除纯符号，但 log / norm 类仍会计入）。
- **复算路径**：用词表集合 + 计数器独立复算命中数；给 20 个 token 的抽检清单人工判定；词表 sha256 必须与产物记录一致。
- **依据文献**：**Coxhead 2000** "A New Academic Word List", *TESOL Quarterly*, DOI `10.2307/3587951`；03-工具与依据.md §3.9（AWL 覆盖率是通行操作化）。

### 3.8 `M-PAS-09` 被动句占比

- **定义**：含被动标记的**句子**占合格句总数的比例。被动标记 = (be / is / are / was / were / been / being / get / gets / got / become / becomes / became) + 可选副词 + 过去分词（不规则表 >= 100 条，或 -ed 结尾）。
- **公式**：value = 被动句数 / 句数 = n / denominator
- **分子 / 分母 / 单位**：分子 = 被动句数（n，同一句内多处被动只记 1）；分母 = 合格句总数（denominator）；单位 `ratio`。
- **evidence span 口径（v1.0，已实现）**：`evidence.sample` 的 span 指向**被动短语**（助动词 + 最多 2 个副词 + 过去分词；紧邻的 has/have/had 或情态动词并入，例如 `has been adopted`、`can be mapped`、`were carefully collected`），**不再指向整句**；`excerpt == text[start:end][:80]` 不变。句子 span 只用于分母。metric 顶层新增 `evidence_target = "passive_phrase_span"`。**value / n / denominator 口径未变**（语料级数值与改前逐位一致）。
- **额外顶层字段**：`n_unresolved` = 分母内无法判定语态的边缘句数（如 is important 这类系表结构被规则跳过）。缺失它则分母不透明，属契约违规。
- **分位口径**：语料级 median 用 nearest-rank、不插值（§2 总表）；n=34 时取 v[16]。
- **与 01 契约卡的差异**：01-指标契约 M-PAS-09 的口径是**有限子句级**（需依存分析器）；本阶段纯 stdlib 无 parser，INTERFACES.md §3 冻结为**句级**。因此本数值**不得**与设计案的 0.18（子句级）或 0.42（子句级互斥）比较，见 §7。
- **依赖与版本**：不规则过去分词表（>= 100 条）+ 规则后缀 -ed（二者为逻辑或）；随 `TEXT_METRICS_VERSION` 冻结。
- **反映什么写作行为**：施事显隐与客观化的句法选择（把「谁做的」写进句子里，还是隐去）。
- **不能推断什么**：**不能**推断学术规范优劣（被动不是缺陷）；不能推断作者身份；不能以被动比例推断「客观性」——这是写作教学的流行误说，无同行评议依据。
- **偏差方向（已修正措辞）**：**未定，必须人工抽检**——本规则同时存在**漏判**（get 被动 / 被动不定式 / 含插入语的被动）与**误报**（be + 过去分词若实为形容词；关键词行或行内数学式落入正文块）两条渠道，因此**不得**声称「系统性偏低」。
- **形容词 denylist（已落地）**：被动判定用 `text_metrics._PARTICIPIAL_ADJECTIVE_DENYLIST`（27 条：complicated / tired / interested / excited / involved / related / concerned / limited / based / detailed / advanced 等）抑制 be + 形容词误报；时态计数另用 `_ED_ADJECTIVE_DENYLIST`。**宁缺毋滥**：语料实测 copula+-ed 高频词（used 161 / defined 89 / applied 58 等）绝大多数是真被动，故 used / designed / known / left 等**不**排除。实例实测（本机真实运行）：`The problem is complicated.` 与 `The results are interesting.` 均得 `n = 0` 且 `n_unresolved = 1`（计入未判定，而非被动）；对照 `The system is analyzed.` 与 `This approach is widely used.` 均得 `n = 1`（正确计为被动）。证明该反向误报在句型层面已被抑制。
- **残余污染渠道（实测发现，未修复）**：落在正文块内的关键词行与行内数学式仍参与分句，例如某篇的 `Keywords: Dynamic Vehicle Routing Problem,...` 与行内公式片段出现在 M-PAS-09 的 evidence 抽样中；这会同时影响 `M-SLEN-01` 与 `M-LSF-16`。
- **抽检精确率（已完成，N=40，agent 裁决）**：工具 paper-metrics/scripts/pas_spotcheck.py，语料 = 34 篇 VRP（paper-analysis），抽样 = 文档序系统性步长（无 RNG），凭证 = impl-baseline/_pas_spotcheck.md 与 .json（含逐条清单与裁决列）。
  - **P 层（自动判被动，N=20）：精确率 20/20 = 100%，95% Wilson CI [0.839, 1.000]**。区间下限说明：20 条样本只能排除 >16% 的误报率，**不得**据此主张「接近完美」。
  - **B 层（有被动助动词但未判定，N=10）+ N 层（无助动词，N=10）**：漏报 0/20，漏报率 0，95% Wilson CI [0.000, 0.161]。B 层正是系表/形容词边界（is similar / is a critical step / are fundamental / is admissible），全部裁决为非被动，与 B2 修复方向一致。
  - **明确局限**：① 裁决由 **agent 完成，非专家人工金标准**；② 系统性抽样非随机抽样，**结论只覆盖这 40 条**，不得外推为全语料精确率/召回率；③ 召回率只在负例半边以「漏报率」形式估计，未覆盖 get 被动、被动不定式、无助动词分词（referred to as / extracted tensors，按准则 5 本就不计）；④ 抽检语料文本为 content_list 全部 text 块拼接（10498 句），**宽于** profiler 的分节正文口径，指标实现相同、输入范围不同。
- **已知失效场景**：① 系表结构/形容词性过去分词误判（is interested / is determined 类；已由形容词 denylist 大幅抑制，但词表外的形容词仍可能漏网）；② get 被动、含插入语的被动、被动不定式**漏判**；③ 无 by 短语时与系表不可分；④ 公式行、关键词行落入正文块时污染**分母**（见上「残余污染渠道」）；⑤ 与 01 契约卡不同口径（句级 vs 子句级）造成的不可比。
- **复算路径**：第三方可用同一词表与正则复算；evidence span 现为被动短语 span，可直接逐条核对 text[start:end] 是否确为被动短语；uv run python paper-metrics/scripts/pas_spotcheck.py --corpus <paper-analysis> --out <md> --json <json> [--verdicts <json>] 可重跑本次抽检并复现精确率/召回率；核验 n <= denominator 且 n_unresolved 已输出。
- **依据文献**：**PassivePy**（Sepehri et al. 2024）, *Journal of Consumer Psychology*, DOI `10.1002/jcpy.1377`（报告了与人工标注的比对验证）。**注意**：03-工具与依据.md §5 明确「Paquet 等被动语态研究」**未核实**，不得引用。

### 3.9 `M-NOM-10` 名词化占比

- **⚠ 口径前提（读本指标前必读）**：本阶段**无 POS 标注器**，分母是 **alpha token 数**（**不是**内容词）。因此本数值**与文献/契约卡的 0.21 不可比**，也**不可**与名词密度（M-ND-06）互推。判定只用「后缀 ∩ 词基动词表 ∩ denylist」三层规则，属后缀法近似。
- **定义**：命中名词化的 token 数占 alpha token 数的比例。判定 = token 以后缀集之一结尾 **且** 去掉后缀后的词基属于冻结动词表 **且** 全词不在伪名词化 denylist。
- **公式**：value = 命中数 / alpha token 数 = n / denominator
- **分子 / 分母 / 单位**：分子 = 命中数（n）；分母 = alpha token 数（denominator）；单位 `ratio`。
- **与 01 契约卡的差异**：01 契约卡分母为「内容词（NOUN/PROPN/VERB/ADJ/ADV）」需 POS 标注器，本阶段冻结为 alpha token 数（见本节开头的口径前提与 §7 差异表）。
- **依赖与版本**：`nominalization_suffixes`（11 个）+ `nominalization_verb_bases`（>= 150 条）+ `nominalization_denylist`（>= 60 条），三文件 sha256 均入产物。缺任一条即为「不可复现」。
- **反映什么写作行为**：信息压缩与过程物化的倾向（Biber 式名词化：把「we analyze」写成「the analysis of」）。
- **不能推断什么**：不能推断文本质量；不能等同于「抽象度」；不能跨领域直接比较（领域术语本身大量名词化）；名词密度不等于名词化（本指标已用词基表区分，但仍是后缀规则的近似）。
- **已知失效场景**：① 纯后缀匹配不做词基校验会误收 section / station / mention / action / position / condition / mission / version / question / function，denylist 正是为此存在，覆盖不足时精确率下降；② 词基表缺失导致假阴性（如 implementation 需 implement 在表中才计）；③ -ing 动名词与分词不计入（**已声明**，会系统性低估）；④ 未剔领域术语表时工程类论文虚高。
- **复算路径**：第三方**无需模型**，仅用三张词表 + 正则即可复算得到同一数值；再用 WordNet 派生动词校验一遍并报告假阳性率；抽检 50 token，要求精确率 >= 0.9，否则调词表并 bump 版本。
- **依据文献**：**Biber & Gray 2011** "Grammatical change in the noun phrase", *English Language and Linguistics*, DOI `10.1017/s1360674311000025`；TAASSC 的 phrasal elaboration 框架（03-工具与依据.md §1.2(6)：**许可证自相矛盾 GPL-3.0 vs CC BY-NC-SA 4.0，仅作框架参考、不作为依赖**）；03-工具与依据.md §3.2（标准做法 vs 常见误算）。

### 3.10 `M-TENSE-28` 现在时占比

- **定义**：现在时命中数 /（现在时命中 + 过去时命中）。现在时 = -s / -es / -ies 三人称单数后缀 + 不规则动词表现在时列（>= 80 条）；过去时 = -ed + 不规则动词表过去时列（>= 80 条）。
- **公式**：value = 现在时命中 / (现在时命中 + 过去时命中) = n / denominator
- **分子 / 分母 / 单位**：分子 = 现在时命中（n）；分母 = 现在时 + 过去时命中（denominator，**不含**无法判定项）；单位 `ratio`。
- **额外顶层字段**：`n_unresolved` = 无法判定的有限动词数（情态动词、完成时、无时态项）。必须输出，否则分母不透明。
- **与 01 契约卡的差异**：01 契约卡是「时态分布（多值：present/past + unresolved）」；INTERFACES.md §3 冻结为**单值「现在时占比」**。故本契约**没有** past 值，过去时占比 = 1 - value（仅在 n_unresolved = 0 时成立）。
- **依赖与版本**：不规则动词表现在时/过去时两列（各 >= 80 条）+ 后缀规则；随 `TEXT_METRICS_VERSION` 冻结。
- **反映什么写作行为**：章节写作惯例（Method 用过去时叙述已做实验，或用现在时陈述一般事实）。
- **不能推断什么**：不能推断学术规范遵循（惯例随领域变化，无唯一正确答案）；不能推断作者水平；不能推断时态「一致性」（本指标只在有限动词集合上统计，不判句内一致性）。
- **已知失效场景**：① 情态动词与完成时归并口径不一，导致 n_unresolved 膨胀、分母缩小；② 被动结构中的时态判定（was analyzed 的 analyzed 是否计入）；③ 规则后缀法对不规则动词覆盖不足时系统性偏低；④ 名词与动词同形（results / processes）被误判为现在时三人称。
- **复算路径**：核对 n + 过去时命中数 + n_unresolved = 有限动词识别总数；抽检 50 个有限动词人工判定；第三方可用同一不规则表 + 后缀规则复算。
- **依据文献**：01-指标契约 M-TENSE-28（设计案行326-329）。本指标无独立效度文献，属**描述性惯例统计**，解释时不得越界为规范主张。

---

### 3.11 中文分句层 `M-CLS-31` / `M-CLS-32` / `M-SLEN-34` / `M-SLEN-35`（issue #22）

四个**仅中文**的指标，随 `TEXT_METRICS_VERSION 1.5` 冻结。英文记录以 `null` + `CAPABILITY_NOT_SUPPORTED` 出现（键集保持对称，消费者能把"该语言不可测"与"键缺失"区分开）。

**分句规则**（`split_clauses_zh`）：在 `split_sentences_zh` 产出的每个句子内，按全角 `，；：`（及混排 ASCII `,;:`）切分；**括号与引号内部的分隔符不切分**（`_mask_brackets` 把括号/引号对内部替换为空白）；数学式与关键词行仍走 `_mask_non_prose`。分隔符随其所在分句输出（与"句末标点随句子"的既有约定一致）。跨度一律从原文切片，可逐条回指。

| 指标 | 定义 | 分子 n | 分母 | 单位 |
|---|---|---|---|---|
| `M-CLS-31` | 平均每句分句数 | 分句总数 | 句数 | `clauses/sentence` |
| `M-CLS-32` | 分句平均长度 | 全部分句的 cjk-units 之和 | 分句数 | `cjk-units/clause` |
| `M-SLEN-34` | 句长 P90 | — | 句数 | `cjk-units/sentence` |
| `M-SLEN-35` | 句长 P95 | — | 句数 | `cjk-units/sentence` |

- `M-CLS-32` 的 `n` 遵循 `_rate` 约定存"总 cjk-units"（而非分句数），因此语料层可用 `sum(n)/sum(denominator)` 直接重算语料级均值。
- `M-SLEN-34/35` 用本模块 `_percentile`（**linear interpolation**，与 numpy 默认一致）；语料级聚合层另用其自己的分位数约定（已在 summary 的 `quantile_method` 声明，与 median 的既有差异同源）。
- **冻结用例**：`data/clause_spec_cases.json`（11 条，全部人工核对）：括号内逗号不切分 / 引号内逗号随引语归属 / 省略号运行只在末字符终结 / 未闭合括号降级为"正常切分"而不是吞掉文档剩余部分。
- **分词**：`tokenize_zh` 用 jieba 精确模式 + HMM，确定性、无进程间学习状态；jieba 是工具链的一部分，其版本变化会显式体现在 toolchain 指纹里（与词表版本同机制）。
- **反映什么写作行为**：`M-CLS-31` 低 = 短句多、句式简单；高 = 长复句多。`M-CLS-31` 相同而 `M-CLS-32` 不同，是**不同的**写作画像（同分句数、每段更长）。`M-SLEN-34/35` 刻画句长分布的**尾部**——两本期刊可以有相同的均值（`M-SLEN-01`）而在最长 10% 的句子上差异巨大。
- **不能推断什么**：不能推断"复句多 = 质量差"（中文复句是常态，分句密度高本身不是缺陷）；不能推断作者水平；不能推断语法正确性。
- **已知失效场景**：① 表格单元格内的逗号（表格走 `table_body` 流，不进正文，本指标不读）；② 未闭合括号后的切分（已按可预测方式降级）；③ 极短文本（无句子时 `M-CLS-31` 为 `null` + 原因码，绝不报 0）。
- **复算路径**：`cd ~/projects/dc-skills && uv run python -m pytest paper-metrics/scripts/test_clause_layer.py -q` 跑冻结用例；或对任意文本调 `split_clauses_zh` 手工核对每条跨度。

### 3.12 issue #23 五层：stance / PDTB 连接词 / 句式模式 / 术语一致性

issue #23 的八条新指标分为两类：**一条 INFERRED**（stance，需要标注集）与**三条 OBSERVED**（PDTB 连接词、句式模式、术语一致性——纯结构/串距统计，**无需任何标注**）。全部**仅中文**，英文记录一律 `null` + `CAPABILITY_NOT_SUPPORTED`（键集对称）。

#### 3.12.1 `M-STNC-41` / `M-STNC-42` / `M-STNC-43`（stance，**INFERRED**）

| 指标 | 定义 | 分子 n | 分母 | 单位 |
|---|---|---|---|---|
| `M-STNC-41` | hedging 句占比 | hedging 句数 | 句数 | `ratio` |
| `M-STNC-42` | boosting 句占比 | boosting 句数 | 句数 | `ratio` |
| `M-STNC-43` | assertive 句占比 | assertive 句数 | 句数 | `ratio` |

三者构成 1.0 的划分（每句恰好一个标签）。

- **分类器**：词表**存在性**规则——统计句中 hedge / booster 词表条目的**出现种类数**（不是次数：一句一个立场，重复触发是同一主张说两遍，不是更强）。boosting > hedging → boosting；反之 → hedging；并列或无触发 → **assertive**（残差类）。此规则在冻结标注集上调优，改动会作废记录里的留出集数字。
- **INFERRED 准入（本层独有的硬门槛）**：① 标签集仓库内冻结（`assertive` / `hedging` / `boosting`）；② **220 句双标注者独立标注，Cohen κ=0.7584 ≥ 0.6**（角色互斥提示词：词表规则标注者 vs 审稿人角色标注者；15 处分歧由第三轮裁决）；③ 66 句分层留出集的 **P/R/F1 随记录下发**（macro-F1 0.5415，accuracy 0.7273；assertive F1=0.837、boosting 0.455、hedging 0.333）；④ 逐句可追溯（evidence 的跨度就是被判定的句子本身）；⑤ **永不进入 gate**，也不自报置信度。
- **诚实报告弱类**：学术中文里 hedging 句极少（220 句中仅 12 句），其 precision 仅 0.25。记录里的 calibration 块**不隐藏**这一点——消费者据此判断该信多少。这也是 INFERRED 与 OBSERVED 的本质区别：OBSERVED 的零是事实，INFERRED 的值是模型输出，必须自带质量标签。
- **不能推断什么**：不能推断"hedging 低 = 论文差"。stance 只是断言强度的指纹；审稿人感受到的"过度断言"是断言与证据强度**不匹配**的结果，这个匹配需要人判断。本层只做方向性提示（如"v1→v4 的 boosting 句占比从 19.8% 降到 13.4%"这种**差值**用法，比绝对值可信得多）。

#### 3.12.2 `M-CONN-30t` / `M-CONN-30q`（PDTB 时序/条件连接词，OBSERVED）

| 指标 | 定义 | 分子 n | 分母 | 单位 |
|---|---|---|---|---|
| `M-CONN-30t` | temporal 连接词密度 | temporal 命中数 | cjk-units×1000 | `per-1000-cjk-units` |
| `M-CONN-30q` | condition 连接词密度 | condition 命中数 | cjk-units×1000 | `per-1000-cjk-units` |

词表是 `connectors.json` 的 temporal / condition 组（PDTB 2.0 Annotation Manual Appendix A 的 temporal / condition sense 对应中文连接词）。与三组在**同一趟互斥匹配**里跑（最长匹配、不跨句），但**不进入 `M-CONN-30` 的并集**——`M-CONN-30 = 30c + 30k + 30r` 的恒等式严格保持，`components` 块也只列冻结三组。一条 temporal 命中永远不会悄悄抬高 `M-CONN-30`（有测试钉死）。

#### 3.12.3 `M-SPAT-44` / `M-SPAT-45` / `M-SPAT-46`（句式模式，OBSERVED）

| 指标 | 定义 | 分子 n | 分母 | 单位 |
|---|---|---|---|---|
| `M-SPAT-44` | 分句起始标点/句 | 带标点的分句起始符总数 | 句数 | `clauses/sentence` |
| `M-SPAT-45` | 多分句句占比 | 多分句句数 | 句数 | `ratio` |
| `M-SPAT-46` | 连接词开头句占比 | 以连接词开头的句数 | 句数 | `ratio` |

全部在 `split_clauses_zh` 的输出上算，确定性、无模型。`M-SPAT-45` 是 `M-LSF-16` 的**结构侧孪生**（同一长句现象，一个从长度看、一个从形状看）；`M-SPAT-46` 量化论证的显式标注程度（高 = 读者被时刻告知逻辑关系；低 = 平铺并列，读者要自己理因果）。

#### 3.12.4 `M-TERM-47` / `M-TERM-48`（术语一致性，OBSERVED）

| 指标 | 定义 | 分子 n | 分母 | 单位 |
|---|---|---|---|---|
| `M-TERM-47` | 变体簇内术语出现占比 | 非规范写法的出现次数 | recurring 术语总出现数 | `ratio` |
| `M-TERM-48` | 需统一术语数 | 变体簇内不同写法总数 | 同左 | `index` |

- **聚类规则**：2–8 字 CJK 子串，出现 ≥2 次的为候选；**只保留 maximal 候选**（是另一 recurring 词的子串者丢弃——滑窗会把同一拼写按更长词的片段重复产出）；共享前缀 ≥3 字的候选聚为一簇，规范形取最长者。检出"多仓库路径优化 / 多仓储路径优化"这类同义写法分歧，`clusters_found` 里列出规范形 + 全部变体 + 各自频次。
- **阈值为什么是 2 而不是 3**：变体**天然稀有**——同一概念两种写法把出现次数对半分，要求每种 3 次恰好抹掉本层要找的信号。2 次/拼写是"作者两种都用过"的最低证据。
- **不能推断什么**：不能推断"变体多 = 论文差"。它给的是**统一清单**（哪些术语需要全文统一），不是质量评分；同一术语的多种写法在某些期刊风格里甚至是可接受的。

#### 3.12.5 复算路径

```bash
cd ~/projects/dc-skills && uv run python -m pytest paper-metrics/scripts/test_text_metrics.py -q -k 'stance or pdtb or sentence_pattern or terminology'
```

校准集自检（κ 与留出集 P/R/F1 从冻结文件**重算**，与代码内常数必须一致）：`-k 'calibration'`。对任意文本手算可用 `tm._classify_sentence_stance` / `tm._term_clusters`。

## 4. 复用类指标（profile 级，非 text_metrics 产出）

本节 5 条由 `profile_papers.py` 直接在语料块序列上计算（**零新增开发**，只修遥测字段与已知缺陷），输出落在 `_domain_profile.json`。

### 4.1 `M-SECSKEL-48` 章节骨架

- **定义**：把每个章节标题归一化（剥编号前缀与尾部标点）后映射到 canonical 标签（精确匹配、关键词子串、原样保留三段解析），统计每个 canonical 章节的出现频次、原始标题变体、中位相对位置与中位词占比。
- **公式**：frequency = 该 canonical 章节出现的论文数；median_position = median(块序 / 总块数)；median_word_share = median(该章词数 / 全篇词数)
- **分子 / 分母 / 单位**：分子 = 出现篇数；分母 = 有可解析块的论文数（meta.paper_count）；单位 = 篇（计数），位置为 [0,1] 相对位置，词占比为比率。
- **依赖与版本**：`profile_papers.py` 的 `_CANONICAL_MAP` 与 `_SECTION_KEYWORD_PATTERNS`（顺序敏感：ablation 必须先于 experiment）；输入 MinerU 中 text_level == 2 的块；随 `PROFILER_VERSION` 冻结。
- **反映什么写作行为**：该领域论文的章节组织惯例（哪些章几乎必写、写在相对什么位置、占多少篇幅）。
- **不能推断什么**：不能推断期刊强制模板（只是本语料的经验分布）；不能推断「缺某章 = 论文差」；标题归一化可能把自定义章节归错类。
- **已知失效场景**：① `_CANONICAL_MAP` 覆盖不足时，未见章节以原始小写串出现，分散成大量 frequency=1 的键；② MinerU 缺 text_level 字段则整篇无章节起点、被静默跳过；③ 关键词子串顺序错会让 ablation study 落入分类之外（已在模式表中固定顺序规避）；④ 中英文标题混排时子串模式需分别覆盖。
- **2026-09-14 映射扩展与不变性**：补充了中文正文类标题（数学建模/建模/算子/分段函数/数值实验/结果对比/案例/算例/问题描述/假设/参数说明/变量定义…）与英文 `problem description|setting(s)`、`sensitivity analysis`、`case study`、`computational results` 等。**映射变更只允许影响章节派生字段**（`section_skeleton`、`by_section`、`S-TBL-10`）；在真实语料上用"非章节派生字段指纹"验证：改前改后 `n_tokens`、`non_prose_dropped`、`block_census` 与**所有 M-* 指标值零变动**（S-TBL-10 的 4 处中文变动属设计如此）。
- **刻意未做**：英文 `a./b.` 附录式子标题未映射为 `appendix`——那会把**附录文本移出正文流**（实测占英文语料正文+附录字符的 **10.79%**，203 块 97694 字符、影响 7/34 篇），使所有正文指标变动，必须走一次显式重登记。见 issue #17。
- **不能推断什么（补充）**：`references`/`appendix` 这类**非正文**标签决定哪些文本被丢弃，因此**它们的中英文映射不得随手改**——改了就是改数值，必须 bump 版本并重锁基线。
- **复算路径**：第三方直接数 `_content_list.json` 中 text_level == 2 的块，用同一 canonical 映射与模式表复算；抽检 5 篇。
- **依据文献**：01-指标契约 M-SECSKEL-48；**Kanoksilapatham 2005** "Rhetorical structure of biochemistry research articles", *English for Specific Purposes*, DOI `10.1016/j.esp.2004.08.003`（结构描述的标准范例）；**Crookes 1986**, DOI `10.1093/applin/7.1.57`。

### 4.2 `M-ASSET-49` 图表公式落位

- **定义**：把 figure / table / equation 块归属到其**最近的前置章节**，按 (section, sub_type) 统计频次。子类型由「所在章节 x 块类型」启发式判定：Method 内的 image 记 framework-overview；Experiments 内的 chart 记 data-plot、table 记 benchmark-comparison；Preliminaries 内的 equation 记 problem-definition、method 内的 equation 记 method-formulation；其余归 chart-other / image-other / table-other / equation-other。
- **公式**：frequency(section, sub_type) = 命中的该 (section, sub_type) 资产数；比例 = 该子类频次 / 同类型资产总数（图/表/公式分别求分母）
- **分子 / 分母 / 单位**：分子 = 该 (section, sub_type) 计数；分母 = 该类型（图/表/公式）资产总数；单位 = 计数（比例可派生）。
- **依赖与版本**：`extract_asset_patterns()` 与 `_classify_figure` / `_classify_table` / `_classify_equation`；块序依赖 MinerU 阅读顺序；随 `PROFILER_VERSION` 冻结。
- **反映什么写作行为**：图表在论证中的**布置惯例**——方法图放 Method、结果表放 Experiments、问题定义公式放 Preliminaries。
- **不能推断什么**：不能推断图表质量、必要性或数量是否恰当；不能推断期刊排版要求。
- **已知失效场景**：① MinerU 升级改变 type 命名（image / chart）则整类资产漏计；② 图放在章节标题之前则归属到上一节；③ 跨栏/跨页资产被拆成两块则双计；④ 表格题注与表格体分块导致计数膨胀；⑤ 首节前的资产归入 front_matter（非论文实体章节）。
- **复算路径**：直接对 `_content_list.json` 的 type 与 text_level 分组计数，与 figure_placement_patterns / table_placement_patterns / equation_placement_patterns 三个数组对拍；抽检 3 篇人工核对归属。
- **依据文献**：01-指标契约 M-ASSET-49；03-工具与依据.md §3.8（结构类描述必须与「语步/修辞」分开，后者属 INFERRED）。

### 4.3 `M-CITSTYLE-50` 引文风格判定

- **定义**：用两条正则统计方括号数字式 [N] 与作者-年份式（括注式 + 叙述式）引用出现次数，按比值给出 ieee-numeric / author-year / mixed 标签。
- **公式**：ratio = N_bracket_numeric / (N_bracket_numeric + N_author_year)；ratio >= 0.85 判 ieee-numeric，ratio <= 0.15 判 author-year，否则 mixed；分离度 = max(ratio, 1 - ratio)
- **分子 / 分母 / 单位**：分子 = 方括号数字匹配数；分母 = 数字式 + 作者-年份式（含叙述式）匹配总数；单位 = 比率 [0,1] 加类别标签。
- **命名红线**：confidence 只是 max(ratio, 1 - ratio)，是**启发式分离度**，不是校准概率。**禁止**把它读作「有 92% 把握是 IEEE 风格」。**已落地**（对齐 01-指标契约 M-CITSTYLE-50）：产物现同时输出 `separation`（正式口径）与 `confidence`（legacy 兼容，附 note 声明其非校准概率），二者数值相同；读取方应以 `separation` 为准，不得与 M-CONF-44 的校准置信度混用。真实语料实测 separation = 0.965（numeric 2947 / author-year 108，n = 3055，判 ieee-numeric）。
- **依赖与版本**：`profile_papers.py` 的三条正则（`_BRACKET_NUMERIC_RE` / `_AUTHOR_YEAR_PAREN_RE` / `_NARRATIVE_RE`）；输入为 `marker/*.md`（缺失时回退为文本块拼接）；随 `PROFILER_VERSION` 冻结。
- **反映什么写作行为**：领域/期刊的引用**著录惯例**（数字编号 vs 作者-年份）。
- **不能推断什么**：不能推断期刊要求（同一期刊可能接受两种）；不能推断作者是否遵守规范；正则无法识别复杂作者-年份变体（团体作者、多作者缩写、机构作者）。
- **已知失效场景**：① Marker MD 中数学模式内的引用标记丢失；② (Smith et al., 2020; Jones, 2021) 这类多引文**只计一次**（低估 author-year）；③ [12, 13] 合并编号只匹配到 [12]（低估 numeric）；④ 正则在参考文献表内也会命中导致虚高（属已知边界）；⑤ 该指标是 **pooled 口径**（全语料求和后算比值），会被长文/高引论文支配，故必须同时给 evidence 计数以便独立复核。
- **复算路径**：用 grep 计数方括号数字模式即可复核分子；抽检 5 篇；分母可与 `evidence` 的 author_year_matches 对拍。
- **依据文献**：01-指标契约 M-CITSTYLE-50（设计案行1103-1115）。著录惯例本身无「对错」文献依据，故本指标只作惯例描述、不作规范判定。

### 4.4 `M-REFCNT-51` 参考文献数量分位

- **定义**：在 references / bibliography 章节内按条目起始模式计数，得到每篇条目数，再给出语料级 median / p25 / p75（离散分位，整数）。
- **公式**：count_per_paper，再取 median / p25 / p75（离散分位：idx = round(p x (n-1))）
- **与全局口径一致（已核对）**：本条早期版本用 `int(statistics.median)`，现已统一为与其它 13 个指标相同的 **nearest-rank / 不插值** 实现（§2 总表该行）。**数值未变**：真实语料 median 仍为 **42**（p25 = 32、p75 = 60，34/34 篇有计数）。
- **分子 / 分母 / 单位**：分子 = 每篇参考文献条目数；分母 = 不适用（分布统计）；单位 = 条。
- **依赖与版本**：`profile_papers.py` 的 `_count_references()`（章节识别依赖 text_level == 2 的 References/Bibliography 标题）；输入 MinerU `_content_list.json`；随 `PROFILER_VERSION` 冻结。
- **本条是历史缺陷位（I2，已修复并实测）**：旧实现只认 [N] 与 N. 两种起始格式，**且只看 type == "text" 的块**；在真实 36 篇语料上恒为 median = 0（34 篇实测），而 Mode B 已用它生成「参考文献数量目标」——这是曾经正在生效的错误。
- **实测根因（三处，与最初假设不同）**：① MinerU 把 References 标题输出为 type == "header"（而非带 text_level 的 heading），导致整个参考文献区未被识别；② 条目块被 MinerU 标为 sub_type == "ref_text"，旧逻辑不认；③ 有 1 篇使用 [Author et al., 2016] 括注式著录，旧正则未覆盖。修复即对应这三处（识别 header 型章节标题 + 尊重 ref_text 标记 + 括注式模式）。
- **修复后实测**：34/34 篇全部有计数（此前 30/34，4 篇为 0），median = 42、p25 = 32、p75 = 60（中位数由 44 降至 42，因新纳入的 4 篇计数较低）；语料指纹 corpus_id = 09c68608…。**验收：真实语料 reference_count.median > 0 已达标。**
- **反映什么写作行为**：领域文献密度的量级（一篇论文通常引多少条）。
- **不能推断什么**：不能推断文献质量或影响力；**条目数不等于被引次数**；不能推断引用是否恰当。
- **已知失效场景**：① author-year 语料（无编号）返回全 0，若被读作「该期刊参考文献极少」即误判；② MinerU 把多条参考文献合并为一个块则漏计；③ 参考文献章节标题未被识别为 text_level == 2 则整天不计数；④ 附录/补充材料中的扩展文献表被计入或漏计。
- **复算路径**：用 grep 按行首方括号数字计数复核编号型；author-year 型需另建规则（按「年份 + 句号/换行」计数）并在报告中说明；抽检 5 篇人工数条目。
- **依据文献**：01-指标契约 M-REFCNT-51；数值属描述性计量。

### 4.5 `M-CONTRIB-52` 贡献声明句式

- **定义**：用 5 条冻结正则从全文（marker MD 或文本块拼接）抽出贡献声明片段。正则集覆盖：our (main|key|primary)? contributions? (are|is)；in this paper,? we (propose|present|introduce)；we (propose|present|introduce|develop|design) (a|an|the)?；the (main|key)? contributions? of this (paper|work|article)；this paper (makes|presents|proposes) (the following|several)? (main|key)? contributions?。
- **公式**：当前输出为**去重排序的字面串列表**；可派生覆盖率 ratio = N_paper(含贡献句) / N_papers
- **分子 / 分母 / 单位**：分子 = 命中冻结正则的片段（去重）；分母 = N_papers（用于覆盖率）；单位 = 字符串列表 / 覆盖率比率。
- **已知缺陷（建议补，属复用类增强）**：输出**没有篇级来源与 char offset**，第三方无法回指到具体论文与位置；且丢失频次 tf 与语料分布 df。建议升级为 {phrase, tf, paper_refs[], spans[]}；在此之前，本条**只能**作为句式参考，**不得**作为「覆盖率」类统计结论使用。
- **依赖与版本**：`_CONTRIBUTION_PHRASES` 与 `_CONTRIBUTION_RE`（硬编码于脚本，随 `PROFILER_VERSION` 冻结）。
- **反映什么写作行为**：论文自我陈述贡献的惯用句式（可支撑「仿写」时的句式选择）。
- **不能推断什么**：**不能**推断贡献真实大小或创新性；正则命中不等于存在有价值的贡献；未命中不等于没有贡献。
- **已知失效场景**：① 正则漏掉变体（We make three contributions / This work offers several contributions）导致假阴性；② 同一句被多条正则命中并去重为一条，tf 丢失；③ 在 Related Work 中引述他人贡献句时误命中；④ 只输出去重字符串，无法判断某句式是 1 篇还是 20 篇在用。
- **复算路径**：用同一正则 grep -in 复核片段；抽检 10 条回指原文；若按覆盖率使用，必须先补 paper_refs。
- **依据文献**：01-指标契约 M-CONTRIB-52（设计案行1103-1115）；CARS 贡献陈述传统见 **Swales 1990** *Genre Analysis*（**一手链接/ISBN 未核实**，03-工具与依据.md §5 已标注），**不得**把 move 级结论写进本 OBSERVED 指标。

---

### 4.6 非正文流指标 `S-CAP-01` / `S-NUM-02` / `S-REF-03` / `S-SIZ-04` / `S-CAPL-05`（表格 / 图表，2026-09-14）

**口径先于公式**：这三条**不读 prose canonical**，只读 `doc_model` 的非正文流，产物里 `scope = ["figures","tables"]` 已写明。理由与证据见 SKILL〈指标基座〉：同一篇论文的"数字丢失"可以是 2.1%/25%/55.5%/78.2%，取决于拿什么跟什么比；把表格塞进正文会污染句长/密度类指标，而"看不见表格"又让损坏无法归因。
**草稿契约不含这三条**：Markdown 草稿没有块结构，无法从草稿复算；`build_contract` 会以 `NON_PROSE_METRICS_EXCLUDED` 明确说明"留在画像里、不进契约"。
**恒等式适用范围（2026-09-15，issue #18-3）**：本节**比值型**指标必须满足 `value == n / denominator`（`n` = 分子计数，`denominator` = 分母计数）；**计数/分位型**（`S-CAPL-05` 字符中位数、`S-TBL-06` 列数中位数、`S-REF-14` 被引深度中位数）恒等式**不适用**（其 `n`/`denominator` 只是参与统计的样本数）；**按每千单位缩放型**（`S-TBL-09`）为 `value == n / denominator × 1000`。除这三类外，任何 `value ≠ n/denominator` 的读数都是契约违规（此前 8 个比值指标的 `n` 误填分母，其中 S-TBL-07 连分母都是表数而非单元格数，issue #18-3）。

**引用键的表示（2026-09-15，issue #18-8）**：`declared` / `referenced` / `dangling` / `uncited` / `depths` 的键现在是**字符串**（JSON 对象键本就只能是字符串；此前内部用 int 再转字符串，现在是显式字符串键，`"007"` 与 `"7"`、`"a1"` 与 `"A1"` 归一为同一键）。引用解析新增：**复数**（`Figures` / `Tables`）、**数字区间**（`Tables 13-15`→`"13","14","15"`）、**字母前缀编号**（`Figure A1`→`"A1"`），并用显式 lookaround 做词边界（`tablet`、`figure of merit` 不误命中）。单段区间超过 **50** 个键时整段丢弃并给 `REFERENCE_RANGE_TOO_LARGE`（宁可少计且可见，不静默截断）。搜索空间仍限 prose 流。**仍不支持**：后缀字母子图（`Figure 6a` 折叠为 `"6"` 与父图同键）、`and` 枚举（`Figures 4 and 5` 只取 `"4"`）、全角字母。

#### `S-CAP-01` 图表题注覆盖率

- **公式**：`(图+表块数 - 无题注块数) / (图+表块数)`；单位 `ratio`。
- **分子 / 分母**：分子 = 有非空题注的图/表块数；分母 = `figures`+`tables` 流中的块数（`image`/`chart`/`table`）。
- **证据坐标**：`evidence.count` = 参与统计的块数；`evidence.uncaptioned` = 按**流内序号**列出的缺失项；`evidence.sample[].block_index` / `field` / `excerpt` 指向 content_list 的具体块与题注键（form B 可回指；基线验证器已支持"按字段校验"）。
- **不能推断什么**：无题注 ≠ 该图表无意义，也不代表正文没有引用它（那是 `S-REF-03`）。
- **实测（中文语料 10 篇）**：均值 **0.9667**（n_valid=10）。

#### `S-NUM-02` 图表编号一致性

- **公式**：`value = (声明编号总数 - 重复出现次数) / 声明编号总数`；单位 `ratio`。
- **分母**：题注中解析出的编号总数（`Figure N` / `Fig. N` / `图 N` / `Table N` / `表 N`；解析前把全角数字归一为 ASCII）。
- **证据坐标**：`declared`（逐流编号，**字符串键**）/ `gaps`（1..max 中缺失，只在纯数字键上算——`"A1"` 没有隐含前驱）/ `duplicates`（重复）/ `sample`。
- **不能推断什么**：编号连续 ≠ 图表被正确引用。前缀字母编号（`Figure A1`）已支持且不进 1..max 的 gaps 扫描；**后缀**字母子图（`Figure 6a`）仍折叠为父编号 `"6"`（与父图同键合并），`and` 枚举（`Figures 4 and 5`）只取第一个——这两类漏计会同时抬高本指标的 seeming 一致性与 `S-REF-03` 的 dangling，见 §4.6 引用键说明。
- **实测**：均值 **0.98**。

#### `S-REF-03` 正文引用一致性

- **公式**：`|declared ∩ referenced| / |declared ∪ referenced|`（**Jaccard**，1.0 = 完全一致），按 figures/tables 分别求交后汇总；单位 `ratio`。
  **2026-09-14 修正（交叉审查 blocker B2）**：旧分母 `|declared| + |referenced|` **理论上限恒为 0.5**，中文均值 0.4829 曾被文档反读成"约一半对不上"——实际是 **96.6% 重合**。已改 Jaccard，并令 `n = 交集编号数`、`denominator = |并集|`，使契约的 `value = n / denominator` 成立。
- **分子 / 分母**：分子 = 既被声明（有题注编号）又被正文引用的编号数；分母 = **并集** `|declared ∪ referenced|`（= 声明数 + 引用数 − 交集），故 `value = n / denominator` 成立。**2026-09-15 修正**：本行此前仍写"分母 = 声明编号数 + 引用编号数"（旧口径，理论上限 0.5），与上方的 Jaccard 公式自相矛盾（issue #20-1）。
- **引用只在 prose 流里搜**：表格单元格里的 `Figure 2` 不会被当作引用（有测试锁定）。
- **证据坐标**：`declared` / `referenced` / `dangling`（引了不存在）/ `uncited`（声明了没引）/ `sample`，全部是**字符串键**；另有 `unit_basis_text`(`canonical_body` / `prose_stream_incl_headings_and_references`)——搜索空间不是 canonical 正文时给 `UNIT_BASIS_NOT_CANONICAL` 告警，与 `S-TBL-09` 同形（issue #20 顺带项）。
- **不能推断什么**：低值既可能来自 MinerU 漏题注，也可能来自作者书写不规范——必须同时看 `dangling`/`uncited` 与 `S-CAP-01` 才能归因，**不得**单凭本条断言"该刊图表管理混乱"。
- **实测（Jaccard 口径，2026-09-14 修正）**：中文 mean **0.9417**（median 1.0）、英文 mean **0.7331**（median 0.7391）。旧口径的 0.4829 曾被读成「约一半对不上」，实际是 96.6% 重合——差异请读 dangling/uncited，不要反推。

#### `S-SIZ-04` 图像分辨率充裕度

- **公式**：`达到阈值的可测图像数 / 可测图像数`；单位 `ratio`；阈值 `_MIN_IMAGE_WIDTH = 800 px`（≈300 dpi 下单栏图 6.8 cm，低于此值不宜上印刷）。
- **分母**：**可测**的图块数（能打开文件头并读到尺寸的）。**读不了的图像不计入分母**，而是列入 `evidence.unreadable` 并给出 `IMAGE_UNREADABLE` 告警——"读不了"是未测量，不能算达标也不能算不达标。
- **测量方式**：只读文件头（PNG IHDR / JPEG SOFn / GIF LSD），**纯 stdlib、无解码器、无第三方**；已用系统 `file` 命令独立交叉核验（如 634x353 / 487x40 / 440x65 与解析值完全一致）。WebP 等未支持格式返回不可读，不猜。
- **证据坐标**：`evidence.images[]` 含 `block_index`/`field="img_path"`/`excerpt`/`img_path`/`width`/`height`/`aspect_ratio`；另有 `median_width` 与 `min_width_required`。无图块时同样给出这两个键（值为 null / 阈值），保持证据形状稳定。
- **不能推断什么**：这不是论文**原图**的分辨率，而是**抽取副本**的分辨率；低于阈值只说明"这份副本不适合直接复用"，不能推断原作者绘图质量差，也不能推断图片内容/可编辑性。
- **启用 `--figure-source pdf` 后读数不可与上面直接比（issue #16）**：该路径的产物分两类——**内嵌原图**（原生分辨率、不重采样）与**渲染副本**（按 DPI 渲染，像素数由 DPI 决定，不由原图信息量决定）。本指标两类都能测，但**"渲染副本达标"不等于"原图有那么多细节"**；跨路径比较前先确认产物来自 `images/`（引擎副本）还是 `figures/`（PDF 路径），并在报告里写清用的是哪一套。
- **实测（2026-09-14）**：英文语料 34 篇 / 424 张图，**合并达标率 28.3%**（33 篇有值）；中文语料 10 篇 / 15 张图，**合并达标率 6.7%**（仅 6 篇有图块，其余 4 篇为 `null` 而非 0）。逐篇中位宽跨度 317–1226 px。

#### `S-CAPL-05` 题注长度（中位数）

- **公式**：题注**字符数**的中位数（nearest-rank：偶数样本取靠前者，与语料级分位口径一致）；单位 `characters`。
- **分母**：非空题注数（图与表合并）。
- **证据坐标**：`evidence.lengths[]`（逐条 `block_index`/`field`/`excerpt`/`chars`）+ `min`/`median`/`max` + `sample`。
- **不能推断什么**：**字符数不可跨语言比较**（同为 N 个字符，中文承载的信息多于英文）；"长"也不等于信息量大或写得规范；无题注时给 `null` + `NO_CAPTIONS`，而不是 0。
- **实测**：中文语料 10 篇均值 **18.0 字符**（中位 17）。

#### `S-TBL-06` 表格声明列数（中位）

- **公式**：表**声明列数**（逐表计算：该表每一行的 `colspan` 之和取最大）的中位数（nearest-rank）；单位 `columns`。
- **为什么按 colspan 求和**：真实语料里 `colspan/rowspan` 极常见（英文 265 个表共约 4954 次 colspan），只数 `<td>` 会把宽表系统性低估。
- **rowspan 的边界**：只**计数**不回填（不把一个 rowspan 单元格带入后续行）——所以这是"该行自己声明的跨度"，**不是重建后的渲染网格**；本指标不声称是渲染列数。
- **分母**：有正文的表格数；**正文为空的表不计入分母**，而是列入 `evidence.empty_bodies` + `TABLE_BODY_EMPTY` 告警（不可测量的表不能把列数拉向 0）。
- **证据坐标**：`evidence.tables[]` 逐表给 `block_index`/`field="table_body"`/`excerpt`/`rows`/`cells`/`declared_columns`/`colspan_merges`/`rowspan_merges`/`empty_cells`；异常用 `block_index` 点名（可回开 content_list 的坐标）。
- **不能推断什么**：列数多 ≠ 信息量大或设计差；也不能据此断言"该刊偏好宽表"（列数与论文长度相关，产物会自动给 `LENGTH_CORR` 警示）。
- **实测（2026-09-14）**：中文 30 个表 min2/p25 5/**median 7**/p75 8/max 13，逐篇中位 8；英文 265 个表 min1/p25 4/**median 6**/p75 9/**max 26**。

#### `S-TBL-07` 空单元格占比

- **公式**：该篇所有表**池化**后的 `空单元格数 / 单元格总数`；单位 `ratio`。"空"= 去标签后无文本（复用 `doc_model.html_to_text`，与普查同一口径）。
- **分母**：该篇有正文表格的单元格总数；空正文的表不计入。
- **不能推断什么**：空单元格多可能是"表格设计留白（如对照表）"，也可能是抽取缺字；**必须结合 `S-TBL-08`` 一起看**才能归因，不得单条断言"数据缺失严重"。
- **实测**：中文 逐篇 min0/median 0.033/max 0.085；英文 min0/median 0.047/**max 0.46**（有论文近一半单元格为空）。

#### `S-TBL-08` 表格正文缺失率

- **公式**：`正文无单元格的表块数 / 表块总数`；单位 `ratio`。
- **语义**：这是**数据缺失**，不是"某表 0 个单元格"。缺哪些表用 `block_index` 点名（`evidence.missing`）。
- **不能推断什么**：缺失可能来自引擎未抽取正文（图片式表格），也可能是原表就是图形——归因需人工抽检，本指标只负责"让它可见"。
- **实测**：中文 **0/30**；英文 **8/273 = 2.93%**（34 篇里 32 篇有表，另 2 篇为 `null`）。

#### `S-TBL-09` 表格密度（跨流：prose + tables）

- **公式**：`表格块数 / prose 单位数 × 1000`；单位随语言：英文 `per-1000-words`、中文 `per-1000-cjk-units`。
- **分母是 canonical 正文**（调用方传入 `Paper.canonical_text()`）；证据以 `unit_basis_text` 写明基准，回退 prose 流时给 `UNIT_BASIS_NOT_CANONICAL` 告警。
  **2026-09-14 修正（交叉审查 H3）**：此前分母是 `model.text(PROSE)`（**全部** text 块，含标题/前置页/参考文献），实测中文语料比 canonical 正文**多 49.8% 字符**，密度被系统性低估且中英偏差不同。
- **分母来自冻结分词器**：复用 `text_metrics.detect_language` 的 `cjk_chars`/`ascii_alpha_tokens`，**不另写一套分词**（两套规则会漂移，密度取决于哪套先跑）。
- **scope = ["prose","tables"]（首个跨流指标）**，带来一条契约后果：**跨流指标同样不进草稿契约**（Markdown 草稿没有表格清单），`build_contract` 只在 scope **恰好为 prose** 时才生成 clause——已收紧并有测试锁定。
- **证据坐标**：`evidence.sample[].block_index`/`field="table_body"`/`excerpt` + `prose_units` + `unit_basis` + `language`；无 prose 单位时给 `null` + `NO_PROSE_UNITS`（不编 0）。
- **不能推断什么**：**单位不同，中英数值不可直接比较**（中文按 cjk-units、英文按词）；密度高≠写得差，它只描述"该刊用表的频率"。
- **实测**：中文 **0.530 / 千字**（逐篇 0.124–1.043，n=10）；英文 **1.201 / 千词**（逐篇 0.092–3.675，n=32）。

#### `S-TBL-10` 表格章节落位（集中度）

- **公式**：`该篇表最多的那个章节的表数 / 有章节归属的表数`；单位 `ratio`。
- **为什么用"最多章节"而不是"结果段占比"**：本指标上线时中文语料里有大量表落在**无法解析成 canonical 标签的子标题**下（如 `3．2 决策者偏好的影响`、`数值实验`），若只认 `experiments/results/discussion` 会**系统性低估**且低估量未知。所以值取稳健统计量，同时把 `results_share` 作为**保守下界**放进证据并标注说明。
  **2026-09-14 补映射后**：中文正文类子标题（数学建模/数值实验/结果对比/案例/问题描述/参数说明…）与英文 `problem description`/`sensitivity analysis` 等已能解析，**中文语料表格落在结果类章节的池化比例由 40.0% 升至 73.3%**（英文 20.9% → 22.0%，英文剩余缺口主要是 `a./b.` 附录式子标题，见 issue #17）。因此该指标仍按"保守下界"表述——但下界的偏低程度已可量化。
- **证据坐标**：`evidence.sections`（章节 → 表数）、`top_section`、`top_share`、`results_share`、`results_share_note`、`sample[]`（含 `section`，可回指块）。
- **章节标签来源**：调用方传入 `block_index → canonical 标签` 映射（来自 `labelled_blocks()`）；**没有映射时给 `null` + `SECTIONS_UNAVAILABLE`**，不猜位置。
- **不能推断什么**：集中度高只是"表集中在少数章节"，不等于该刊规定如此；章节标签无法解析时该表不计入任何章节（已在证据里可见）。
- **实测**：中文 mean 0.727（median 0.667），落位 discussion 8 / method 6 / experiments 4 + 7 个未解析子标题共 9 表；英文 mean 0.442（median 0.5），落位 experiments 41 / discussion 16 / method 10 / appendix 8。两语料的 `S-TBL-10` 都带 `LENGTH_CORR`（集中度与篇幅相关，不可当独立风格证据）。

#### `S-TBL-11` 数值单元格占比（数据表度）

- **公式**：`严格数值单元格 / 单元格总数`；单位 `ratio`。判定：把单元格文本去标签、**全角数字归一**、去掉千分位逗号与空格后，仅剩"数字 + 可选范围 + 可选短单位（≤6 个字母/百分号）"才算严格数值。
- **同时给两个读数**：`value` 是**严格**口径；证据里给 `numeric_bearing_share`（只要含数字）。二者差距是本语料的真实属性（`G13` 这类实例名、`522(90.0%)` 这类复合结果），只报一个必误导其中一类读者。
- **证据坐标**：`evidence.tables[]` 逐表给 `block_index`/`field="table_body"`/`excerpt`/`rows`/`cells`/`numeric_cells`/`numeric_bearing_cells`/`numeric_rows`/`latex_cells`；`denominator` == 单元格数、`n` == **严格数值单元格数**（故 value = n / denominator）。
- **不能推断什么**：数值占比高 ≠ 表格质量高（也可能是"表格里只剩数字、文字被抽掉了"）；**必须与 `S-TBL-13` 联看**——LaTeX 残留会把本该是数字的单元格算成非数值，从而**拉低**本指标。
- **实测**：中文 池化 **77.9%**（逐篇 0.563–0.936）；英文 池化 **49.7%**（逐篇 0.0–0.789）；含数字口径 中文 85.3% / 英文 72.6%。

#### `S-TBL-12` 数值行占比

- **公式**：`数值行 / 行数`，其中"数值行"= 该行**至少一半**单元格是严格数值（至少 1 个）；单位 `ratio`。
- **为什么按行而不是列**：真实语料 `colspan/rowspan` 极普遍，重建列网格是猜测；而一行的单元格是确定的，所以按行统计是**稳健**的。
- **证据**：`evidence.numeric_rows` + 逐表计数；`denominator` == 行数、`n` == **数值行数**（故 value = n / denominator）。
- **不能推断什么**：表头行天然不是数值行，所以本指标**上限低于 1** 且随表头行数变化；跨语言比较时要注意表头结构差异。
- **实测**：中文 池化均值 **0.778**；英文 **0.538**。

#### `S-TBL-13` 表格 LaTeX 残留率

- **公式**：`含 "$" 的单元格 / 单元格总数`；单位 `ratio`。
- **语义**：这是**抽取缺陷**而非作者选择——MinerU 会把公式塞进表格单元格（如 `$r _ { c e n } = 4 . 5$`），使该单元格对任何数值消费者都不可用。
- **证据**：逐表 `latex_cells` + 样本；有残留时给 `LATEX_IN_CELLS` 告警（`S-TBL-11` 也会带同一告警）。
- **不能推断什么**：残留率低不等于表格可安全复用（还有图片式表格、缺正文等形态，见 `S-TBL-08`）。
- **实测**：中文 **1.51%**（38/2511 单元格，10/30 表）；英文 **2.29%**（698/30504，67/265 表）；**单篇最高 25.8%**。

#### `S-REF-14` 表格被引深度

- **公式**：`median(每张声明表在正文被提及的次数)`（长尾分布，均值退居证据字段 `mean_depth`）；单位 `citations-per-declared-table`。声明表 = 题注里解析出编号的表；提及次数只统计 **prose 流**（单元格里的"表 2"不算引用，否则表格会自我引用）。
- **证据坐标**：`evidence.depths`（表号 → 次数，JSON 键为字符串）、`median_depth`、`max_depth`、`single_mention_share`（恰好 1 次）、`multi_mention_share`（≥2 次）、`uncited`（为 0 的表号），样本指向**题注字段**（表号是从题注读出来的，证据就指那里）。
- **与 `S-REF-03` 的分工**：S-REF-03 回答"引用的编号和题注编号**对得上吗**"（集合口径）；本指标回答"对得上的那些表**被讨论了几次**"。未被引用的表在两者中都会出现，这是刻意的重复可见性。
- **scope = ["prose","tables"]（跨流）** → 与 S-TBL-09 一样**不进草稿契约**（草稿没有表格清单）。
- **不能推断什么**：被引次数少 ≠ 表没用（附录/数据集表本就少被正文提及）；也不能只凭本指标判断"作者偷懒"，需结合表的**落位章节**（`S-TBL-10`）看。
- **实测（2026-09-14）**：中文 29 张声明表，**仅 1 张（3.4%）未被引用**、37.9% 被引 ≥2 次（逐篇均值 1.0–2.33，中位 1.5）；英文 167 张声明表，**43 张（25.7%）从未被引用**、仅 16.2% 被引 ≥2 次（逐篇均值 0.4–3.0，中位 1.0）。英文的低引用率与其大量附录/数据集表（见 issue #17）方向一致。

**变更控制**：十四条的取数口径（哪些块进分母、引用模式、题注键集合、分辨率阈值、colspan 求和规则、密度单位与落位统计量、数值单元格判定与阈值、被引次数的统计范围）属定义变更，按 §10 第 1 条 bump `metric_spec_version`；阈值/口径调整必须同步本节与 SKILL 里的实测数字。

---

## 5. 关联指标（本节不在 §3 表内，但与 §3 同期交付）

### 5.1 `M-PCNT-25` 段落数与段长

- **定义**：section 内段落数与段落词数分布（min / median / max）。段落边界来自 MinerU `_content_list.json` 的文本块序列，合并规则必须 pin。
- **公式**：段落数 = 段落块计数；段长 = 该段 token 数（分布统计）
- **分子 / 分母 / 单位**：分子 = 段落数 / 每段 token 数；分母 = 不适用（计数与分布），可另给 section 总词数作分母；单位 = 段、词/段。
- **依赖与版本**：MinerU 块序列（text 与 text_level 字段）；连续 text 块是否合并为一段的规则随 `PROFILER_VERSION` 冻结。
- **反映什么写作行为**：信息打包粒度（主题段 vs 长段）。
- **不能推断什么**：不能推断论证结构（「一段等于一个 move」是无依据假设）；不能推断可读性。
- **已知失效场景**：双栏/跨页段落被切分；图表题注被计为段落；MinerU 版本升级改变分段。
- **复算路径**：直接从 `_content_list.json` 计数复算；抽检 2 篇人工核对段落边界。
- **语言边界（红线，见 §0.2）**：在**不受支持语言**（`cjk_ratio > 0.10`）下本条**整条抑制**——`value: null`、`distribution: null`、`n: 0`、`denominator: 0`，并附 `note`。**原因：段落过滤器是「空白分词 >= 15 词」，中文无词间空格 ⇒ 真实段落被整段过滤、留下的报出汉字串长度（实测约 62 "词"/段）**；因此本条的「计数」**不是**语言无关量，不得在中文语料上引用。
- **依据文献**：01-指标契约 M-PCNT-25（设计案行303）；MVP 推荐理由：段长上下界是写作契约的可判定子项，**必须**用计数实现而非 LLM 判断。

### 5.2 V7 可选探针：MinerU vs Marker 文本层对照（默认关闭，属加分项）

- **触发**：`uv run python paper-metrics/scripts/baseline_eval.py --corpus <corpus> --out <out> --drift-probe N`；产出 `_engine_drift.json`，并在 `_baseline_report.md` 的「引擎漂移（V7 探针）」节渲染。
- **方法**：同一篇论文上分别对 MinerU `canonical_text()` 与 Marker markdown（经 `markdown_to_text`：去围栏/行内代码/图片与链接目标/标题标记/强调/HTML，并在**文档后半段**的首个 References/Bibliography/Appendix/Acknowled\* 标题处截断，以对齐 `_NON_PROSE_SECTIONS`）计算 `M-SLEN-01` / `M-LSF-16` / `M-PAS-09`，逐篇记录 MinerU/Marker 值与 signed/abs/rel 差值。
- **终锁实测（10 篇有 Marker 输出的论文；与 impl-baseline 的 golden 同一次运行）**：平均绝对差 M-SLEN-01 **2.222705** 词/句（max 5.961942）、M-LSF-16 **0.021556**（max 0.044584）、M-PAS-09 **0.039863**（max 0.091302，相对 -11.7%）。→ 漂移量与指标同阶，**不可忽略**；这正是不能把 PDF→Canonical 混进确定性承诺的理由。
- **残余不对称（必须随数值一起引用）**：Marker 侧保留图表题注与摘要（MinerU 侧 `canonical_text()` 丢弃 `front_matter`）；截断规则是启发式。故这些差值是**指示性**的（既可能高估也可能低估），不是引擎漂移的计量学测定；且因引擎版本未记录，**不可归因到具体引擎版本**。
- **未实现**：结构类指标对照；按引擎版本分层的漂移；PDF 原点到 Canonical 的端到端漂移。

---

### 5.3 `M-REFAGE-53` 参考文献**近端集中度**（原称"时效性"，2026-09-14 改名）

- **公式**：`近 N 年文献数 / 有年份的文献数`（N = `_RECENT_REFERENCE_YEARS` = 5）；单位 `ratio`。
- **"近"的基准**：以**该篇最新的参考文献年份**为锚（−4 年），**不是**当前年份或论文年份——论文自身年份在语料里不可靠，且锚在自身能使跨语料/跨年可比。该自指性质已在此写明，属于口径的一部分。
- **每篇文献只取一个年份**：取**首个**年份样式的串并剥掉 LNCS 式后缀（`2020a` 记 2020）；否则一条文献里的卷/页码年份会被重复计数——**本指标第一版就报出过 100.8% 的"覆盖率"**（不可能值），被真语料运行抓到后修正。
- **证据坐标**：`evidence.sample[]` 给 `block_index` + `field`（`text`/`list_items`）+ 块前缀 `excerpt` + 解析出的 `entry`（form B 可回指）；另有 `year_coverage`、`newest_year`、`oldest_year`、`median_year`、`recent_entries`。
- **名不副实的历史（交叉审查 M3）**：该值以**该篇最新文献年**为锚，测的是"列表近端集中度"，**不是绝对新旧**。所以：① 改名"近端集中度"；② 语料摘要新增 `reference_freshness`（锚 = 语料最大文献年，给**绝对**近 5 年占比）；③ `evidence.anchor` 写明 `self_newest`、`evidence.years` 供第三方换锚复算。
- **不能推断什么**：近端集中度低 ≠ 该刊保守；**跨语料比较前必须先看 `reference_freshness` 的绝对读数**；`year_coverage < 1` 时给 `REFERENCE_YEARS_INCOMPLETE` 告警，此时该值只覆盖可解析部分。
- **实测（2026-09-14）**：中文 10 篇 mean **0.618**（median 0.625，年份可解析率 **100%**）；英文 34 篇 mean **0.490**（median 0.429，可解析率 99.0%）。
- **锚年对单条误解析年份没有防护（2026-09-15 实测）**：`reference_freshness.anchor_year` = 语料内**所有**文献年份的**最大值**。vrp-en 里 2023 Parallel MCTS 一篇有 1 条被解析成 **2041**，锚年因此变成 2041，`absolute_recent_share` 从真实量级塌到 **0.000731（0.07%）**；同一规则在 ycgl-zh 上锚年 2024、读数 **0.452703**。**跨语料比较前必须核对 `anchor_year`**：大于该语料发表年的锚年应视为可疑。本版**未修复**该锚点规则（属新缺陷，见 §10 第 6 条待办）。

### 5.4 `M-REFLINK-54` 引用 ↔ 列表双向一致性

- **公式**：`|正文引用编号 ∩ 1..N| / |正文引用编号 ∪ 1..N|`（**Jaccard**；N = 参考文献条目数，与 `S-REF-03` 同形，便于对照阅读）；单位 `ratio`。**2026-09-15 修正**：本行此前写 `/(|正文引用编号| + N)`（旧口径，理论上限 0.5），实现早已改为 Jaccard，纯属文档滞后（issue #20-2）。
- **引用来源 = canonical text（正文）**：参考文献段本身被 `_NON_PROSE_SECTIONS` 排除，所以"条目自带的 [N]"不会自证被引用。
- **数学区间不算引用**：`[0,1]`、`[-1,1]` 在本领域满篇都是。规则：**单编号括号一律算**（越界正是要抓的 dangling），**区间/列表只有全部编号落在 1..N 内才算**（因此 `[0,1]` 被拒、`[3-5]` 在 N=5 时被接受）。
- **引文风格分流（关键）**：风格只在**正文**上判定（若用全文，编号式的参考文献列表会让每篇都"看起来是数字制"）。作者-年份制（如 `Desaulniers et al. (2018)`）下本指标**给 `null` + `CITATION_STYLE_NOT_NUMERIC`**——否则会报出**假的 100% 未引用**（英文语料实测有 11/34 篇属于这一类，见 `HIGH_MISSING` 汇总告警）。
- **证据坐标**：`evidence.sample[]` 用 **form A**（正文引用的精确 `span` + `excerpt`），无引用时回落到参考文献的块坐标（form B）；另有 `cited_count`、`cited`、`dangling`、`uncited`（截断 50）、`uncited_count`、`citation_style` 与 `unit_basis_text`（搜索空间基准，非 canonical 时给 `UNIT_BASIS_NOT_CANONICAL`）。
- **不能推断什么**：`uncited` 多也可能是"作者-年份制论文被误读成数字制"（本文档的规则已尽量排除，但仍建议先看 `citation_style`）；"未引用"也不等于该文献不重要（可能是背景综述式引用）。
- **实测（Jaccard 口径，2026-09-15 复核）**：中文 **10/10 篇可测，mean 0.984127**（median 1.0，`IQR_ZERO`）；英文 **23/34 篇可测**（11 篇作者-年份制报未测量，`HIGH_MISSING`），**mean 0.781811**（median 0.935484）。旧文档写的 0.496 / 0.419 是 Jaccard 之前的旧口径值，不得再引用（issue #20-3）。

**变更控制**：这两条的口径（"近"的锚点、每年取首个年份、区间/列表的接受规则、风格判定只读正文）属定义变更，按 §10 第 1 条 bump `metric_spec_version`。其 `scope = ["prose","references"]` 虽然不是恰好 prose，但**草稿里同样有正文段与参考文献段，可以从 Markdown 复算**，因此自 2026-09-15 起按**逐指标显式声明**进入草稿契约（issue #18-10）；真正的非正文流指标（`scope` 含 figures/tables 的 S-*）仍不进契约。草稿侧正文↔文献表双向核验目前由 thesis-writing 的 `check_markdown_spec` 覆盖，本层不重复实现。

## 6. 恒等式与交叉校验清单（测试必须断言）

| # | 断言 | 容差 | 违反后果 |
|---|---|---|---|
| C1 | value(M-CONN-30) = value(30c) + value(30k) + value(30r) | 1e-6 | 连接词口径漂移（原设计案 0.042+0.038+0.029 不等于 0.094 一类错误复发） |
| C2 | hedge 词表条目集合 与 booster 词表条目集合 交集为空 | 精确 | 同一 span 双计 |
| C3 | connectors 三组两两交集为空 | 精确 | 分量之和大于总量 |
| C4 | M-SLEN-01：value = denominator / n（**注意倒置**，见 §3.1） | 1e-6 | 第三方复算得到句/词 |
| C5 | M-LSF-16 与 M-PAS-09：n <= denominator | 精确 | 计数越界 |
| C6 | ratio 类 value 属于 [0,1]；per-1000-words 类 value >= 0 | 精确 | 单位混用 |
| C7 | evidence.count = n（span 类指标） | 精确 | 证据与数值脱钩 |
| C8 | 全部 state == "OBSERVED" 且 method == "rule" | 精确 | 三态红线（OBSERVED 层混入模型判定） |
| C9 | 任一 NaN 必须以 value = null + warning 输出，JSON 中无 NaN 字面量 | 精确 | 产物不可被标准 JSON 解析 |
| C10 | 同一语料两次运行，`_domain_profile.json` / `_per_paper_metrics.jsonl` / `_corpus_summary.json` **逐字节相同**（`_run_meta.json` 除外） | 逐字节 | 违反 I3 确定性承诺 |
| C11 | 指纹范围内 JSON 不含时间戳/绝对路径/主机名/耗时 | 精确 | 换路径或换机器即不可复现 |
| C12 | `M-REFCNT-51.median > 0`（真实 36 篇语料） | 严格 | I2 未修复（当前恒为 0） |
| C13 | hedge/booster 各 >= 40 条；connectors 各组 >= 15 条；verb_bases >= 150；denylist >= 60；academic_words >= 300；stopwords >= 120 | 精确 | 词表未达冻结规模 |
| C14 | 全部 dict 以 sort_keys=True 输出、浮点 round(x, 6)、不依赖文件系统枚举顺序（一律 sorted()） | 精确 | 非确定性 |

---

## 7. 与 `01-指标契约.md` 的已冻结差异（**解释与比较前必须先读本节**）

INTERFACES.md §3 为适应「纯 stdlib、无 NLP 依赖」，对 01-指标契约卡的部分口径做了**冻结降级**。**未列入本表的指标与契约卡同口径**（例如 M-HED-14 / M-BOO-15 保持 per-token ratio，未降级）；列入本表的按下表逐条核对。设计案/契约卡的多数示例数字（0.18、0.21、0.094、0.109）与本阶段产物**不可比**，但 **0.021 / 0.013（hedge/booster）是同口径的例外**，可直接比较量级。

| metric_id | 01 契约卡口径 | INTERFACES §3 冻结口径 | 直接后果 |
|---|---|---|---|
| `M-PAS-09` | 有限**子句**级（需 parser），报 0.18 / 0.42 | **句级**（be + 过去分词规则），报 ratio 与 n_unresolved | 与 0.18/0.42 不可比；**偏差方向未定**（既有漏判也有误报渠道），须人工抽检，不得假定「系统性偏低」 |
| `M-NOM-10` | 分母 = **内容词**（需 POS），报 0.21 | 分母 = **alpha token**（无 POS），后缀 且 词基 且 denylist | 与 0.21 不可比；与名词密度分母不同，二者不可互推 |
| `M-HED-14` 与 `M-BOO-15` | 每词 ratio，报 0.021 / 0.013 | **ratio（未降级，与契约卡同口径）** | 与文献 0.021 / 0.013 **同口径、可直接比较量级，无需 ×1000 换算**；per-1000-words 只出现在 M-CONN-30 系列 |
| `M-CONN-30` 与三分量 | 每词 ratio | **per-1000-words**（value = n / denominator **× 1000**），且总密度必须由三分量重算 | 与 0.094/0.109 不可比（该示例本身还不自洽）；换算系数 1000 **只适用于这一族** |
| `M-MTLD-02` | 单向、分母为「因子数」 | **双向平均**，denominator = 1 占位 | 数值略高于单向；分母字段无比率含义 |
| `M-AWR-03` | lemma 化命中 | **surface form** 精确匹配 | 屈折形式漏计，系统性低于 lemma 口径 |
| `M-TENSE-28` | 时态**分布**（多值） | **单值「现在时占比」** 与 n_unresolved | 无 past 字段；过去时占比 = 1 - value（仅当 unresolved = 0） |
| `M-SLEN-01` | 分母「不适用」（分布） | n = 句数、denominator = 词数，故 value = denominator / n | 唯一倒置指标，见 §3.1 |
| `M-CITSTYLE-50` | 字段名 confidence | 语义为**分离度**，建议改名 separation 或 margin | 不得读作校准概率 |
| `M-REFCNT-51` | 只认方括号数字与 N. 的 text 块 | 增加 list 块、author-year、detected_format 与告警 | 修复前真实语料 median 恒为 0（I2 缺陷） |
| `M-*` 全文口径（issue #17） | —（契约卡未定义附录处理） | **附录子节（`A.` / `A.1` / `B.1.` 等）随其所属附录一起移出正文**（附录上下文继承，见 §10.6） | vrp-en **9/34 篇正文变短**（合计 **−57,714 字符**），14 条登记指标全部小幅移动 → 已重登记；ycgl-zh **0/10 篇变化**。仅按字母编号判定附录的旧提案**已否决**：它会把 IEEE 正文子节（`A. Accuracy study`）一并移出正文 |

---

## 8. 解释边界速查（一页版）

| 指标 | **可以这样解释** | **禁止这样解释** |
|---|---|---|
| M-SLEN-01 | 句子平均信息量、长短节奏 | 句子难易、作者水平、跨领域优劣 |
| M-LSF-16 | 超长句占比 | 「长句多 = 写得差」 |
| M-MTLD-02 | 用词复用程度 | 「词汇丰富 = 写作好」；跨长度比较 |
| M-HED-14 | 断言不确定性显性化程度 | 作者心理谨慎度、研究可信度、「hedge 少 = 自信」 |
| M-BOO-15 | 断言强化显性策略 | 「booster 多 = 差」、论断正确性 |
| M-CONN-30 / 30c / 30k / 30r | 篇章显性衔接密度 | 逻辑严密性、因果真实存在、结论正确性 |
| M-AWR-03 | 一般学术词与领域词的取位 | 专业性强弱、写作好坏 |
| M-PAS-09 | 施事显隐的句法选择 | 学术规范优劣、客观性、「被动 = 不好」 |
| M-NOM-10 | 信息压缩/过程物化倾向 | 文本质量、抽象度 |
| M-TENSE-28 | 章节时态惯例 | 规范遵循度、作者水平 |
| M-SECSKEL-48 | 章节组织惯例 | 期刊强制模板、「缺章 = 差」 |
| M-ASSET-49 | 图表布置惯例 | 图表质量或必要性 |
| M-CITSTYLE-50 | 著录惯例 | 期刊要求、规范遵守度 |
| M-REFCNT-51 | 文献密度量级 | 文献质量/影响力、条目数等于被引数 |
| M-CONTRIB-52 | 贡献陈述惯用句式 | 贡献真实大小与创新性 |
| M-PCNT-25 | 信息打包粒度 | 论证结构、「一段等于一个 move」 |

**三态红线**：以上全部条目 state == "OBSERVED" 且 method == "rule"。任何由 LLM 产生的数字/标签**不得**出现在这些字段中；LLM 的输出只能落 RECOMMENDED，且**不得带小数、不得进入任何 gate**。

---

## 9. 依据文献

**同行评议 / DOI 可解析（B 级）**

| 文献 | 出处 | DOI / 链接 | 本文件中的用途 |
|---|---|---|---|
| Hyland 1998, "Boosting, hedging and the negotiation of academic knowledge" | *Text* 18(3) | `10.1515/text.1.1998.18.3.349` | hedge/booster 操作化依据（§3.4/§3.5） |
| Hyland 2005, "Stance and engagement" | *Discourse Studies* | `10.1177/1461445605050365` | 同上 |
| Hyland 2005, *Metadiscourse: Exploring Interaction in Writing* | 书（章节 DOI） | `10.5040/9781350063617.0011` | 词表出处标注 |
| McCarthy & Jarvis 2010 | *Behavior Research Methods* 42(2):381-392 | `10.3758/brm.42.2.381` | MTLD 阈值 0.720 / min 10 / 双向均值（§3.3） |
| PDTB 2.0 Annotation Manual | LDC2008T05（手册免费；数据本体付费、不可再分发） | 手册 PDF：https://catalog.ldc.upenn.edu/docs/LDC2008T05/manual/pdtb-annotation-manual.pdf | 连接词表来源（§3.6；Appendix A 明列 100 型） |
| Biber & Gray 2011, "Grammatical change in the noun phrase" | *English Language and Linguistics* | `10.1017/s1360674311000025` | 名词化操作化（§3.9） |
| Coxhead 2000, "A New Academic Word List" | *TESOL Quarterly* | `10.2307/3587951` | 学术词表来源（§3.7） |
| Cohan et al. 2019 (SciCite) | NAACL | `10.18653/v1/n19-1361` | 引用功能体系（**INFERRED 层，本阶段未实现**，仅作边界说明） |
| Jurgens et al. 2018 (ACL-ARC) | TACL | `10.1162/tacl_a_00028` | 同上 |
| Teufel et al. 2006 | SIGdial | `10.3115/1654595.1654612` | 引用功能标签集（同上） |
| Sepehri et al. 2024 (PassivePy) | *Journal of Consumer Psychology* | `10.1002/jcpy.1377` | 被动语态自动识别的公开验证（§3.8） |
| Gibson 1998, "Linguistic complexity: locality of syntactic dependencies" | *Cognition* | `10.1016/s0010-0277(98)00034-1` | 句长解释边界的限定（§3.1） |
| Kanoksilapatham 2005 | *English for Specific Purposes* | `10.1016/j.esp.2004.08.003` | 章节结构描述范例（§4.1） |
| Crookes 1986 | *Applied Linguistics* | `10.1093/applin/7.1.57` | 结构分析效度（§4.1） |
| Davison & Kantor 1982 | *Reading Research Quarterly* | `10.2307/747483` | 可读性公式失效依据（本文件**不含**可读性指标，列入以说明为何排除） |

**工程实现证据（A 级，用于 pin 参数）**

- TAALED（kristopherkyle/taaled）源码：TTR 阈值硬编码 0.720、min = 10、双向均值。
- MinerU `_content_list.json` 块契约（type / text_level / text）；Marker MD 作为引文与贡献句式输入的降级路径。
- `/mnt/e/Google_Download/Paper-Reader_设计审查/03-工具与依据.md`：§3.1-§3.9 的标准做法 vs 常见误算；§4.2 判定 hedge/booster、nominalization、connector（私有词表时）、academic_word_ratio 为「仅可复现」，达标条件是**词表/规则/版本/分母四件套随产物发布**——本文件与 `data/lexicons/v1/*.json` 的 sha256 即为兑现该条件。

**未核实（不得当作已核实依据引用；与 03-工具与依据.md §5 一致）**

1. 「Paquet 等」被动语态研究——多轮检索未见，疑为 PassivePy 或 Magali Paquot 的误记。
2. Swales 1990 *Genre Analysis* 的一手链接/ISBN。
3. Halliday/Ure 的 lexical density 一手定义来源（故本阶段**不实现** lexical_density）。
4. Hyland 词表的具体条目数与书内附录页码（本实现取「基于 Hyland 的常用子集」，条目数由词表文件与 sha256 固定，**不主张**它是 Hyland 原表）。
5. SciCite 数据集上的 F1 具体数值（仅核实 ACL-ARC 的 67.9%）。

---

## 10. 变更控制

1. **改定义**（公式、分母、单位、分子语义）必须 bump `metric_spec_version`，并在本文件 §7 追加差异行。历史产物不得与新产物混合聚合。
2. **改阈值/词表条目**（40、0.720、后缀集、hedge/booster 增删）必须更新词表文件，使其 sha256 变化，进而使 `LexiconBundle.fingerprint()` 变化；不必 bump `metric_spec_version`，但**必须**在报告中声明「词表指纹已变，数值与旧产物不可比」。
3. **改匹配规则**（最长匹配策略、大小写归一、缩写保护表）视为定义变更，按第 1 条处理。
4. **禁止**在任何指标定义中引入 LLM 产出的词表、标签或数值；一经发现，该指标必须从 OBSERVED 层移除。
5. **禁止**新增第三方依赖而不重新立项（违反 INTERFACES.md §0 红线 1）。
6. **2026-09-15（issue #17）正文口径加入"附录上下文继承"**：`labelled_blocks()` 在显式附录标题之后，把 `A.` / `A.1` / `B.1.` 形式的子标题归入 `appendix`；遇到非正文段（references/acknowledgments/…）或规范标题即退出该上下文。这是**测量范围变更**而非标签修正，故按第 1 条在 §7 追加了差异行，并 bump `profiler_version` 2.4 → 2.5、重登记 vrp-en 的 14 条期望值。
   **待办（本批未做）**：`reference_freshness` 的锚年取全语料文献年份最大值，对单条误解析年份无防护（vrp-en 实测被 1 条 2041 拉到 0.07%）；修它属定义变更，需另开 issue 走第 1 条流程。
7. **2026-09-15（issue #18 + #20）审计与契约一致性批次**：审计范围扩到 `corpus_warnings`（按 code 计数）、`language_supported`、`by_section`、`section_skeleton`（在此之前只比 14 条指标均值——语料语言告警正是从这条缝里漏过去的）；比值型指标的 `n` 改为**分子**并新增恒等式测试；`_corpus_summary.json` 增加 `mean_basis`；新增 `MIXED_UNIT_AGGREGATION` 护栏；指标记录增加 `class` 并在 md 分表渲染；`M-REFAGE-53`/`M-REFLINK-54` 改为可进草稿契约；文档侧修正 `S-REF-03`/`M-REFLINK-54` 的旧口径残留与 `M-REFLINK-54` 的实测值。**未改任何指标公式**，故不 bump `metric_spec_version`。
