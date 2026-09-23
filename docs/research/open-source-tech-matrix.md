# Open-source Technology Matrix（Issue #11 产出）

> **范围决定（用户 2026-09-13）**：只做 **中文 + 英文**。其他语种不作为目标；"支持更多语种"本身不算加分项。
> 调研日期：2026-09-13 ｜ 产出人：CC-Skills 维护会话 ｜ 状态：A/B/C/D 四组并行调研已启动，本文件为汇总入口。

---

## 0. 先定约束：什么才算"能结合进我们的 SKILL"

选型不是比 star 数，而是比**能不能进现有架构而不破坏已有承诺**。下表是硬条件，任何候选项目先过这一关：

| 约束 | 出处 | 对选型的含义 |
|---|---|---|
| **paper-metrics 是纯 stdlib、零 LLM、逐字节可复现** | `paper-metrics/SKILL.md` 可复现契约 | 概率模型 / 重型 NLP 库**不得进主链**；只能做**可选 Adapter**，且缺能力时必须输出 `null` + 告警，禁止用 0 代替"未测量" |
| **环境分 A/B/C 类**（A=共享轻 venv，B=独立重型 venv，C=系统 Python） | `SKILL-AUTHORING-RULES` §1.1 | 重型依赖（torch / transformers / 模型权重）→ 必须新开 **B 类** skill 或 B 类 `venvs/`，并进 `.gitignore`；共享 venv 只加轻量依赖 |
| **共享 venv 现状**（实测 2026-09-13） | `uv run python -c "import …"` | 已有：Pillow / sqlglot / python-docx / psycopg2 / cryptography / pymysql / requests / bs4 / openai / httpx / pyyaml / feedparser / playwright。**没有**：torch / transformers / spacy / stanza / hanlp / ltp / jieba / docling / sentence-transformers / pdfplumber / camelot / fitz / layoutparser / paddleocr |
| **硬件** | 本机实测 | RTX 5060 Ti **16 GB**（WSL 侧可用），CPU 亦可跑轻量模型 |
| **现有文档链路** | `paper-reader` | MinerU + Marker 双引擎 → `content_list.json` → `canonical_text()`；质量缺陷已在 #2/#3 修过一轮 |
| **诚实性红线** | #10 评论缺口 2 | 每个 Adapter 必须发布 **capabilities 清单**；缺能力 → 显式失败（`LANGUAGE_NOT_SUPPORTED` / `CAPABILITY_NOT_SUPPORTED`），下游 `exit 2` |
| **语言范围** | 用户决定 | 只需 **zh + en**；Language Adapter 只需两个实现 |

---

## 1. 推荐等级与用法定义

| 标记 | 含义 |
|---|---|
| **S** | 现在就该接，且是"不做会卡住"的那一环 |
| **A** | 值得接，但要单独立项 / 独立 venv |
| **B** | 参考其数据模型或算法，不引入运行时依赖 |
| **C** | 不用（能力不匹配 / License 风险 / 维护停滞 / 与纯 stdlib 冲突 / 只服务非 zh-en 语种） |

| 用法 | 含义 |
|---|---|
| **直接依赖** | 进 `pyproject.toml`（轻量）或 B 类 venv（重型），成为主链一部分 |
| **Adapter** | 可选增强，缺它主链仍能跑；必须能力自述 + 显式降级 |
| **参考** | 只抄数据模型 / 接口设计 / 评测方法，不引依赖 |

---

## 2. 分组调研

> 四组并行调研（Document Parsing / 中文 NLP 与篇章 / 引用-术语-风格计量 / 图表与检索）的逐项结果在下方各节展开。每项按 Issue #11 §8 的 21 个维度记录：项目 · GitHub · License · 活跃度 · 最近更新 · 主要能力 · 中文支持 · 英文支持 · Scientific Text · 本地部署 · GPU 需求 · 输入 · 输出 · API · 数据集 · 模型 · 维护状态 · 二次开发难度 · 与 CC-Skills 兼容度 · 推荐等级 · 建议用途。

### 2.A Scientific PDF / Document Parsing 与 Layout

> 调研范围：Marker、MinerU、Docling、GROBID、Nougat、Unstructured、LayoutParser、PaddleOCR/PP-StructureV3（+PaddleOCR-VL）、Surya、PaperMage、PyMuPDF、pdfplumber、Camelot，补充 pdftext、GLM-OCR、dots.ocr、olmOCR、img2table、UniMERNet、Table Transformer 等，共 24 项逐条记录了 21 个维度。

#### ① 一个被纠正的前提（最重要）

**"MinerU/Marker 对中文 front_matter / 关键词 / 标题识别有缺陷"——方向错了。**
本机实测《运筹与管理》10 篇中文核心期刊（MinerU 3.4.5）：中文题名 / 作者 / 单位 / 摘要 / 关键词 / 中图分类号 / 双栏阅读顺序**全部正确**。
真正的根因是：**两个引擎都只输出版面类型 + `text_level`，不输出任何 `abstract`/`keywords`/`references` 语义标签**。所以 Issue #3（前置页混入正文）与 #2（关键词行参与分句）是 **canonical 层的语义分段缺失**，**换引擎解决不了**（GROBID 有语义标签但仅对英文有效）。
→ 结论：**不要为了这两个 bug 换引擎，要做的是在 canonical 层补语义分段规则。**

#### ② 真实缺陷是两处"静默损坏"（有硬证据）

| 缺陷 | 证据 | 影响 |
|---|---|---|
| **英文段空格丢失** | 本机 10 篇中文语料实测：`Keywords：vehicleroutingproblem；truckloadpickupanddelivery`、`HeterogeneousVehicleRoutinProblem`、`VehicleRoutingProblem`。**我按"≥18 字符连续英文串"独立复算：全语料 25 处**（A 组按更宽口径报 77–96 处/篇，**两者口径需对齐**，但现象确认） | 词数 / 句长 / 被动率等**英文类指标在中文语料上不可信** |
| **CNKI PDF 数字/标点静默丢失 ~52%** | MinerU issue #5330（2026-07-26 创建、**至今 open**）：CNKI 自定义字体把数字编码成全角 `０. ７３`，pipeline 与 vlm 后端均复现，`--ocr` 可完全修复 | 数字类指标被污染**且不报错** |

（另：discussion #4402 的"本地中文解析失败"实为模型下载源问题，`--source modelscope` 即解决，**不是中文能力问题**。）

#### ③ 许可（直接影响能否写进公开仓）

| 项目 | 代码许可 | 权重许可 | 结论 |
|---|---|---|---|
| **MinerU** | **Apache-2.0 + 附加条款**（MAU>1 亿或月收入>2000 万美元需商业许可；对外在线服务需署名）——**已不是 AGPL** | VLM 权重 `MinerU2.5-2509-1.2B` = **AGPL-3.0** | 可用，但**权重需显式声明** |
| **Marker 2.0 / Surya** | **已改 Apache-2.0**（v1.x 才是 GPL-3.0） | `surya_layout2` = OpenRAIL；`ocr_error_detection` = **CC-BY-NC-SA-4.0（非商用）** | 可锁版本，**权重注明非商用** |
| **PyMuPDF** | **AGPL-3.0 / 商业双许可** | — | **不进发布路径**，用 MIT 的 `pdfplumber` 替代 |
| **PaddleOCR-VL-1.6** | Apache-2.0 | Apache-2.0 | 中文最强 + 许可最干净 |
| **GLM-OCR 0.9B** | Apache-2.0 | **MIT**（本组唯一"代码与权重都宽松"的高分中文 VLM） | 优先候选 |

⚠️ **CC-Skills 是 public 且没有 LICENSE 文件**（`license=None`）——引入任何依赖前需要先声明许可口径，否则派生项目会误标（已有先例：OpenDataLoader 自评表仍标 `marker=GPL-3.0`、`mineru=AGPL-3.0`，**口径已过期**，其"#1"结论不可直接引用）。

#### ④ 中文质量有量化依据（须标时效）

- 第三方 EN/ZH 对照（PP-StructureV3 官方，**2025 年版**）：中文正文 edit 距离 **PP-StructureV3 0.088 < MinerU-1.3.11 0.200 < Marker-1.2.3 0.315 < Docling 0.987 < Nougat 0.998**；**所有 pipeline 的中文都明显差于英文**（Marker 中文约为英文的 4 倍 edit）。
- 当前（2026-09）**OmniDocBench v1.6** 榜：PaddleOCR-VL-1.6 **96.34** / MinerU2.5-Pro 95.75 / GLM-OCR 95.22 / MinerU-2.5 93.04 / MinerU-Pipeline 86.47 / **Marker 78.44（表格 TEDS 仅 65.77）**。**v1.6 起不再分中英分组** → 判断中文必须自己按语言切片，不能直接引用总榜。

#### ⑤ 与现有双引擎的关系（含一处必须纠正的事实）

- **保持互补**：MinerU = 中文主力，Marker = 英文主力（建议 1.10.2 → 2.0.0 并锁版本；本机 venv 已就绪，`paper-reader/venvs/` 现为 11 GB）。
- ⚠️ **本机中文语料的 `_META.json` 显示 `engines='mineru'`（我独立复核：`paper-merged/*/_META.json` 前 4 篇全部如此）→ 中文这批根本没有跑 Marker 对照**，所以 Issue #8 的双链 drift 在中文语料上是 **N/A**，不能被当作"已测量"。
- **不做第三引擎，先做校验层**（最高性价比）。

#### ⑥ 建议立刻转成实现 Issue 的三项

| 编号 | 内容 | 工时 | 判据 |
|---|---|---|---|
| **A1（最高优先）** | paper-reader 新增**阶段 1.5「文本层探针」**：用 `pdfplumber`(MIT) / `pdftext`(Apache) 独立复算字符 / 数字 / 标点 / 空格，产出 `_textlayer_probe.json`；**不进 `canonical_text()` → 不破 paper-metrics 的纯 stdlib / 零 LLM / 逐字节契约** | 1–2 天 | 数字丢失率 >2% 或连续长英文串 >5 处 → warn；扫描件标 `not_applicable`，**禁止用 0 代替未测量** |
| **A2** | **Canonical 层 front_matter 语义分段规则**（期刊版 + 学位论文版），前提写明"引擎不提供语义标签，这是 canonical 职责"——根治 #3、#2 | 2–3 天 | 关键词行 / 题名页块不再进入指标分母（已有 `non_prose_dropped` 计数可复用） |
| **A3** | **中文第三引擎 A/B**：PaddleOCR-VL-1.6 / GLM-OCR vs MinerU 3.4.5，固定 10 中文 + 5 英文（含本次两类失败样本） | 2–4 天 | 含"同输入两次运行产物哈希一致" |

#### ⑦ 明确不做（附理由）

Nougat（中文 edit 0.998 + 停滞）、LayoutParser（停滞 2 年 + Detectron2）、PaperMage（停滞 1.5 年，仅参考数据模型）、Unstructured（重依赖 + 表格弱 + 与 word-extractor 重叠）、olmOCR（7B 重 + 中文弱 + 停滞 5 个月）、Table Transformer（停滞）、GROBID（本期：无中文模型 + 需 Java 服务，留待将来做英文引用语义时启用）、PyMuPDF（AGPL）、dots.ocr / Chandra / UniMERNet / img2table（本期只登记）。

#### ⑧ 未核实（不得当结论用）

PaddleOCR-VL 的纯中文切片分数；榜上 TeleOCR/OvisOCR2（96.91/96.47）**找不到官方仓库，存疑**；Nougat / dots.ocr / olmOCR 权重许可；Chandra 本地部署；pdf-craft 底层模型与许可；GLM-OCR 活跃度口径矛盾；MinerU 子模型逐项许可；**中文学位论文实测（本机无样本）**。

### 2.B 中文 NLP 与中英文 Discourse / Rhetorical

#### ① 中文侧最小可行技术栈 = **纯 stdlib（零第三方依赖）**，且**分词不是必需的**

| 能力 | 最小可行实现 | 依赖 | 破坏"纯 stdlib / 逐字节"红线？ |
|---|---|---|---|
| 分句 | 自建规则式 `split_sentences_zh`：终止符扩为 `。！？…`、`；` 可配置、`\n` 硬边界、引号《》「」（）内标点保护、小数/编号保护；"无字母片段丢弃"改为"无字母**且无 CJK** 片段丢弃" | 0 | ❌ |
| 术语一致性 | **字面级**：2–6 字连续汉字候选串 + 人工变体表 + 位置/共现计数（**不需要分词**） | 0 | ❌ |
| 句法复杂度（表层） | 汉字句长、分句数、逗号/顿号分句长度、标点密度、长句占比、括号嵌套深度 | 0 | ❌ |
| 连接词 / hedge / booster / 学术词密度 | **字面串匹配**（最长优先 + 去重叠 + 最小 2 字避免单字虚词误命中），与英文侧 lexicon 规则同构 | 0 | ❌ |
| 词汇多样性 | 自建最大匹配分词 + **项目自有词典**（版本化冻结，与 `data/lexicons/v1/*.json` 同构） | 0（词典是数据） | ❌ |
| 段落/字数口径 | `len(cjk_chars)` 替代 `len(text.split())` | 0 | ❌ |
| 依存级复杂度 / 被动式 / 名词化 | spaCy `zh_core_web_sm`（MIT，含 senter+tagger+parser，CPU）或 Stanza zh-hans | 重（torch 系） | ✅ **只能做可选 Adapter** |
| 篇章 RST / PDTB | isanlp_rst / DMRST / t2d | 重 + 语料受限 | ✅ **只能做可选 Adapter / 参考** |

**结论：不需要 HanLP / LTP 的任何东西。** HanLP 模型是 **CC BY-NC-SA（非商用）**、LTP 是**研究免费/商用付费**（非 OSI 许可）→ 两者**明确不作为依赖**；jieba（MIT、0 依赖、2020 冻结、无随机源）仅作"确需词级指标"时的可选 Adapter；spaCy zh（MIT，唯一开箱即用的中文分句器成品）是可选探针首选。

#### ② 篇章/修辞层：**没有确定性收益** → 不进核心

四条机制性理由（同时违反 paper-metrics 的 pinned rules）：① 权重不透明，无法"凭规则复算"；② GPU/浮点路径不保证逐字节（与 `test_determinism` 直接冲突）；③ checkpoint 漂移；④ **关系标签本身无金标准**——中文 RST 全树 F1 实测仅 **39.9–44.6**（EDU 分割 93–95，即"分割可信、关系不可信"），且训练域是新闻/通用文本，与学术论文域不匹配。

→ 处置：**不进核心；最多照抄仓库既有 `--drift-probe → _engine_drift.json` 模式做一个默认关闭的 `--discourse-probe → _discourse_probe.json`**（不进指纹/契约/gate，`state=UNAVAILABLE` 而非 0）；其余只做参考。
**唯一有确定性收益的"篇章相关物"是显性连接词的字面计数**——那是词表匹配，本来就在核心范式内。

#### ③ 中文分句器缺失**不会**卡住中文写作指标

分句是纯规则问题：扩展 `split_sentences()` 约 **120–200 行 + 单测、零依赖**。**反过来说，为"解决分句"而引入 HanLP/LTP 才是真正会同时破坏契约与许可安全的路径。**

真正的瓶颈排序（成本从低到高）：语言门与段落口径（~1 天）→ 中文分句器 + CJK token 口径（1–2 天）→ **中文词表自建**（3–5 天，**没有可直接下载的公开中文学术词表**）→ **中文指标定义与语料校准**（按词还是按字？中文被动规则？中文无时态 → **必须显式 N/A，不得算 0**）→ 篇章层（可选、最低）。

#### ④ 读码事实（已定位行号，说明现状）

- `text_metrics.py:136` `_ALPHA_TOKEN_RE=[A-Za-z]…` → CJK 永不成 token；`:225` `_TERMINATORS=".!?"` → `。！？` 不切句；`:369` 分句末尾按"无字母片段"**丢弃整句**（纯中文句被静默丢弃）。
- `profile_papers.py`：`SUPPORTED_METRIC_LANGUAGES=("en",)`、段落过滤 `len(text.split())>=15` 对中文失效、M-PCNT-25 全量置空。**好消息：现状是"显式未测量"而不是 0——这半边已经做对了。**

#### ⑤ S/A 级与建议立刻转 Issue 的三项

| 编号 | 内容 | 关键判据 |
|---|---|---|
| **Issue-A（最高优先，纯 stdlib）** | `feat(paper-metrics): 中文写作指标 v1`：CJK 分句扩展 + 片段过滤改判 + 段落 CJK 口径 + 把 `SUPPORTED_METRIC_LANGUAGES` 从语言布尔改为**逐指标能力矩阵** | ① 英文输出与当前版本**逐字节零回归** ② `--verify` 连跑逐字节一致 ③ 不支持指标仍 `null` |
| **Issue-B** | `feat(paper-metrics): 中文词表 v1 + 字面匹配规则`（`data/lexicons/v2-zh/`，每表带 `source/license_status/frozen_at`） | 词表候选挖掘脚本 + 输入 sha256 入库，保证可复算 |
| **Issue-C** | `feat(paper-reader): canonical 落盘 language/cjk_ratio/cjk_chars/lang_source` | 与 paper-metrics `detect_language` 同字符类同阈值（避免两套语言判断漂移） |

**S 级（规格蓝本，零运行时依赖）**：pysbd 中文规则（MIT，可自研复刻）、DISRPT 字段规范与中英 treebank 清单（中文侧 zho.dep.scidtb / zho.pdtb.cdtb / zho.rst.gcdt / zho.rst.sctb）。

#### ⑥ 明确不做

HanLP 2.1、LTP 4 作为依赖（许可）；所有神经篇章解析器进核心（t2d / GAN_DP / DMRST / rstfinder / DPLP / PDTB 系统）；CDTB·PDTB·RST-DT·SciDTB·UnifiedDep 作为可分发数据（LDC / 无许可）；pkuseg / THULAC / LAC（停更 4–6 年）；MODDP（对话域）、SCTB（中西配对，不符合只要中英）。

#### ⑦ 找不到（明确结论，别再去搜）

- **中文学位论文语料**：找不到（只检索到方法论文，无可下载数据）；
- **中文 citation intent / 引文功能标注集**：找不到；
- **可公开下载的中文学术语表**（连接词/hedge/booster）：**不存在**，必须自建。

#### ⑧ 未核实

Chinese-DiMLex 许可、isanlp_rst_v3 权重许可、Stanza 逐模型许可、spaCy zh md/trf 是否仍发布（`spacy-models shortcuts-v3.json` 已 404）、jieba 词典跨平台一致性、`WING-NUS/rstparser`（GitHub 404，任务书提到的实现未能核实）、**中文校准语料 `运筹与管理/corpus_ycgl/` 是空目录**（实际可用语料在 `corpus_ycgl_pdf/paper-conversion/`，10 篇 + 1 失败样本，已在本会话确认为可用回归集）。

### 2.C Citation / CSL · 术语 · Stylometry · 生成与校验

#### ⚠️ 时效性发现（C 组中期，已独立复核）：GB/T 7714-2025 已生效

| 事实 | 证据 |
|---|---|
| **GB/T 7714-2025《信息与文献 参考文献著录规则》发布 2025-12-02，实施 2026-07-01，全部代替 GB/T 7714-2015** | 多来源一致：全国标准信息公共服务平台、武汉大学（起草单位）公告、CNKI 实施通知、多家高校图书馆 2026-05/06 公告（闽江大学、中南财经政法大学）、期刊社实施通知（中国新药与临床杂志） |
| CSL 样式侧两版都在 | `citation-style-language/styles` 同时存在 2015 / 2025 风格 |
| LaTeX 侧已有 2025 支持 | CTAN `biblatex-gb7714-2015` v1.1x（2026-06-10）已覆盖 2025 |

**我们受影响的位置（实测 grep，全部硬编码 2015）**：

- `thesis-writing/SKILL.md`：L26（"GB/T 7714-2015 强制"）、L342（"Mode A 用 GB/T 7714-2015"）
- `thesis-writing/references/writing-norms.md` L35、`journal-writing-norms.md` L36、`undergrad-template.md` L223
- `md-to-thesis-latex/references/conversion-rules.md` L186（`\bibliographystyle{bib/gbt7714-numerical}`）
- `Words-Production`：`config/schools/journal_paper.yaml`、`config/example.yaml`、`CHANGELOG.md`

**建议（待用户拍板）**：**双版本可切换 + 新作业默认 2025**。理由：标准虽已于 2026-07-01 实施，但各校模板与期刊《投稿须知》切换时间不一（学位论文尤其滞后），写死任一版本都会在某一侧不合规。实现上把版本做成配置项（`gbt_version: 2015|2025`），渲染器按版本选 CSL。

#### 引用渲染：pandoc + CSL 已在本机验证可零新增依赖确定性化

- 本机 `pandoc 3.6.4`（`~/.local/bin/pandoc`，Words-Production 的 md→docx 本来就走 pandoc）自带 `--citeproc`（引擎 jgm/citeproc，BSD-2）。
- 实测：`pandoc --citeproc --csl=china-national-standard-gb-t-7714-2015-numeric.csl --bibliography=refs.json` → 中文条目渲染正确、4 作者→"等"、连续 3 条→`[1–3]`、正文自动上标，**全程离线**（GB/T 样式内嵌 zh locale）。
- **两个必须消解的冲突**：① CSL 的 collapse 用 **en dash**，而 Words-Production `citation_marks.py` 的 `format_gbt_group` 用**半角连字符**——两个归一化器会互相打架，必须指定唯一归属；② pandoc citeproc 的 docx 产物**有上标、无 hyperlink、无 REF 域**，所以 WP 的 REF 域层**不能删，只能重定基**。
- **分工结论**：**CSL 只解决"渲染"，不解决"抽取"**——paper-reader 目前没有结构化参考文献抽取，paper-metrics 只有启发式 `reference_count`/`citation_style`。因此不重复造轮子：**CSL 管渲染 / WP 管 docx 域 / paper-reader+metrics 管抽取**。

#### 引用渲染落点：把「条目文本」的所有权从 LLM 交给 CSL

- **CSL 能渲染、不能抽取**（两半答案相反）：paper-reader 脚本层**没有任何参考文献抽取**；paper-metrics 只有启发式 `reference_count`/`citation_style`；CSL 是渲染格式不是抽取器。
- **职责划界**：Words-Production 的 `citation_marks.py`（WP-07）是**形态修复器 + docx 域注入器**（把已编号 markdown 归一为 `[1,2]/[1-3]`、整组上标、REF 域）；CSL 是**条目生成器**。⇒ 落点 = **条目文本所有权交给 CSL 样式**，WP 域层继续管 docx 可跳转。
- **两个冲突必须消解**：(a) CSL collapse 用 **en dash `–`**，WP `format_gbt_group` 用**半角 `-`** → 必须指定唯一归属；(b) pandoc citeproc 的 docx 产物实测**有上标 / 有条目书签 / 0 hyperlink / 0 REF 域** → WP 域层仍必要，**只能重定基不能替换**。

#### CSL 样式的中文覆盖与许可

- 全仓 **2,862 个 `.csl`**，文件名匹配 `china/chinese/gb-t/7714` = **20 个**（≈13 个默认 zh-CN）。**GB/T 全家可白嫖**（1987/2005/2015/2025 × numeric/author-date/note），另有 Chinese Medical Journal、Science China×4、Chinese Journal of Aeronautics 等少数；**《计算机学报》《软件学报》《自动化学报》等绝大多数中文期刊无样式** → 长尾需自研（改 XML，中低难度）。
- **许可：styles 与 locales 均 CC BY-SA 3.0**（README + 每个 `.csl` 内 `<rights>`）。仓库**没有 LICENSE 文件**（GitHub SPDX 显示 NONE）——**不要据此判为无许可**。文件级 share-alike，**不传染我们的 Python 代码**。
- **两版输出确有差异，不可混用**：2025 用**全角逗号** + 英文作者**保留原大小写**；2015 用**半角** + 姓氏**全大写**。

#### 术语一致性与文体指标（确定性优先）

- **判据 = 冻结术语表 + 规则匹配**（全半角/繁简/大小写/中英混排归一）；候选发现：中文用 **jieba**（MIT、纯 Python、离线，钉版本 + 词典 sha256），英文用正则 + 词表；**C-value / NC-value / Weirdness 是纯统计公式**，参考 pyate（MIT）**自实现进 stdlib**。embedding/LLM 相似度只能进 INFERRED（不做）。
- **英文 hedge/booster 已经解决**：paper-metrics 的 `hedge.json`(109) / `booster.json`(70) 已是 Hyland(1998/2005) 派生且带 `source` 字段 → **不要再引入外部英文词表**（此前已判定外部手搓词表不可与 Hyland 混用）。
- **最大缺口 = 词表本身**：中文 hedging / 学术词表 / 术语表**没有可再分发的开放资源** → 必须自建 + 来源登记 + 版本冻结。
- **文体指标可复用来源**：**LexicalRichness**（MIT、**零依赖纯 Python**，MTLD/HD-D/TTR）→ 作为**公式复刻来源**进 OBSERVED 层。
- ⚠️ **textstat 只能当英文公式参考**：实查其 `resources` **只有 en/es，不支持中文**——用它算中文会产出"看似合法实际无意义"的数值，**与语言红线同类风险**。
- **现状事实（我复核确认）**：`thesis-ref-check` 只有 `SKILL.md`，`scripts/`、`references/`、`evals/` **全是空目录** → 它是**纯 LLM 工作流**，没有任何确定性实现。

#### 建议立刻转实现的 Issue（C 组）

| 编号 | 内容 | 验收 |
|---|---|---|
| **C-1（最高优先·跨仓，被 GB/T 版本决策阻塞）** | **引用从"生成式书写"切到"确定性渲染"**：锁标准版本 → vendor `.csl`（+ sha256/许可声明）→ WP `pandoc_export.py` 接 `--citeproc --csl --bibliography` → 职责划界 → 正文 `[n]`→`[@key]` 迁移 + 无 `refs.json` 时的降级路径 | 同一 `refs.json` + `.csl` + pandoc 版本 ⇒ 参考文献区**逐字节一致**；4 作者出"等"；连续 3 条成区间；docx 保留可跳转；黄金样本进 CI |
| **C-2（paper-metrics·纯 stdlib）** | **中文引用与参考文献确定性解析层**（**注：基础识别已于 2026-09-13 随 `98fa6af` 落地**——全角 `［N］`、无空格条目、GB/T 7714 形状；本条是**更深一层**）：条目结构化切分（`[J]/[M]/[D]` 字段）、正文↔文献表**双向核验**（悬空引用 / 未被引用）、结构化中间表示 + evidence span | 在真实语料（运筹与管理 10 篇）上条目数/引用数与人工计数一致（给误差带），同输入逐字节复现，不支持则显式失败 |
| **C-3（thesis-ref-check / paper-metrics）** | **确定性术语一致性检查器**：冻结术语表（TSV/TBX）+ 规则匹配 → 归一化链 → 变体聚类（编辑距离/前后缀/中英对照）→ 报告（术语×变体×位置×建议；**LLM 只解释不判定**） | 真实中文论文上同输入同输出的报告，每条建议带位置证据 |
| C-4（可并入 C-2） | 文体指标扩展（MTLD/HD-D/分布/虚词密度） | 纯 stdlib，逐字节可复现 |

#### 明确不做（C 组）

CSL-M（zh+en 用 CSL 1.0.2 实测足够）；其他语种；KeyBERT/embedding（只能进 INFERRED）；SciCite/ACL-ARC/S2 的推理落地（HF 卡 `license=unknown` + 仅英文 + 中文无标注集 + S2 在线违反零网络）；stylo(R/GPL-3)、pystylometry、LFTK（**无 LICENSE**）；Coh-Metrix / TAACO / CRIE（闭源或不可核实）；**textstat 用于中文**；Termolator 原版（无 LICENSE）；LTP（无 LICENSE）/NLPIR（商业）/THULAC/pkuseg/ATR4S/TermSuite/TERMINE（申请制在线）；Academic Phrasebank（© Manchester，不可入库）；TextRank4ZH（与 jieba.analyse 重叠）；wordfreq（数据许可混杂）；refextract（GPL-2.0+HEP）。
**Anansi / AutoTerm：术语抽取语境下未找到可用实现**——Anansi 唯一同名项目是 BBC 的 Linked Open Data 爬虫，已标「未核实（疑似误记）」。

#### 不确定项（13 条，勿当事实）

citeproc-js 许可文本；Zotero 主仓 SPDX（社区通行 AGPL-3.0 **未核实**）；ACL-ARC/S2 intents；TermSuite 中文支持；TextDescriptives 是否含中文；pyate / LexicalRichness 公式细节；AlphaReadabilityChinese / python-readability-cn 算法与依赖；"中文样式 ≈13" 为 GitHub code search 近似口径。

### 2.D Figure / Table Understanding · Embedding / Retrieval

#### ① Figure 分析不破坏红线的路线 = **三层**

| 层 | 归属 | 内容 | 是否进 gate |
|---|---|---|---|
| **L0 确定性层** | `paper-metrics`（纯 stdlib） | 只读 `_content_list.json` + 图片文件头（PNG/JPEG 尺寸、宽高比）→ 结构类指标 | ✅ |
| **L1 Adapter** | `paper-reader` 侧模型推理 | 产物**冻结成带 `sha256` 的 JSON**，被 L0 当**外部输入**消费（不是运行时依赖） | 可进（输入可寻址） |
| **L2 INFERRED** | 可选实验 | 图表语义理解 | ❌ **禁止进 gate** |

**关键实测（改变 #1 的发力点）**：本机 **MinerU 3.4.5 的 BlockType 已内置 `CHART` / `CHART_CAPTION` / `TABLE_CAPTION` / `IMAGE_FOOTNOTE`**，`content_list` **已经产出** `image_caption` / `table_caption` / `chart_caption` / `table_footnote` + `table_body`（HTML，含 `colspan`/`rowspan`）+ `sub_type`；版面模型 PP-DocLayoutV2 的标签集也已含 `chart`(label3) / `figure_title`(label7) / `vision_footnote`(label24)。
→ **Issue #1 的 Inventory / 类型 / Caption-Context 三个 Phase，输入数据已经存在；缺的是"读取 + 指标化"，不是模型。** 应复用既有 `M-ASSET-49`（figure/table/equation 章节归属）的读取路径，新指标命名 `M-FIG-*` / `M-TAB-*`。

#### ② 中文图表支持（结论明确）

- **端到端图表理解模型全部英文专用**：DePlot / UniChart / ChartGemma / MatCha / Pix2Struct 的训练与评测（PlotQA / ChartQA / 英文 C4 截图）**无中文评测**。
- **但中文零障碍的是图注/表注**——它们是**文本**，MinerU 已直接给出 → 结构类指标不受影响。
- **图内文字**需 PaddleOCR（PP-OCRv6，中英日 + 46 拉丁）或 Surya（91 语）。
- **中文图表语义**只能走通用中文 VLM，**且必须落 L2**（不进 gate）。

#### ③ 检索 MVP：先纯 stdlib，再可选 embedding

| 阶段 | 方案 | 依赖 | 说明 |
|---|---|---|---|
| **一（答案主体）** | **BM25 / 字符 n-gram** | **零依赖、逐字节可复现** | 目标期刊语料检索的第一阶段 |
| 二（可选） | **BGE-M3**（MIT，8192 token，dense+sparse+colbert，权重 ~2.27 GB）或 **Qwen3-Embedding-0.6B**（Apache-2.0，32k ctx，~1.2 GB） | torch | 本机 **RTX 5060 Ti 16GB + torch 2.13.0+cu130 已就绪，无需新硬件** |
| reranker（可选） | `answerai-colbert-small-v1`（Apache-2.0，134 MB） | torch | 仅在选择二阶段时才需要 |

**架构约束**：检索必须做成**独立 skill（B 类环境）**，并输出**自描述 `_retrieval_manifest.json`**（模型 id / 权重 sha256 / top-k / 分数）→ **paper-metrics 零改动**。

#### ④ S / A 清单

- **S**：MinerU 既有 `caption`/`chart`/`table` 字段的确定性消费、自研 BM25 基线、`pdfplumber`(MIT)、`BGE-M3`(MIT)。
- **A**：Qwen3-Embedding-0.6B、PaddleOCR PP-StructureV3 / PaddleOCR-VL、Docling(MIT)、Camelot 2.0(MIT)、Surya（已在链路上）、`answerai-colbert-small-v1`、GTE-multilingual-base。

#### ⑤ ⚠️ 两个合规发现（必须处理，非技术问题）

1. **Surya 权重 = modified AI Pubs OpenRAIL-M（非 OSI；< 5M 融资/营收才免费商用），而它已经通过 Marker 2.0（`Requires-Dist: surya-ocr>=0.22.1`）进入我们的流水线**——但 `paper-reader` 的 `_META.json` 的 `engine_versions` **只记 marker/mineru/torch/cuda/python，未记 surya 及其权重许可** → 建议 **0.5 小时内补披露**。
2. **MinerU 是 Apache-2.0 + 附加条款**（>1 亿 MAU 或 >$20M 月营收需商业许可；提供在线服务须署名 MinerU），现有文档未提及。
3. 附：本机 venv 内**同时存在 `surya_ocr` 0.17.1 与 0.22.1 的 dist-info（环境不洁）**，建议清理。

#### ⑥ 建议立刻转实现的 Issue

1. **Figure / Table 结构确定性指标**（1–2 天，**纯 stdlib**，只读 `_content_list.json` + PNG/JPEG 文件头）；
2. **图注/表注配对 + 编号引用一致性校验**（1–2 天，可作为 `validate_draft.py` 的新 gate）；
3. **目标期刊语料检索 MVP**（先 BM25，后可选 embedding）。

前两项**正是 Issue #1 MVP 的真正起点**。

#### ⑦ 明确不做

端到端图表 VLM 进主链（英文专用 + 停更 + 许可混杂）；**UniChart**（权重 GPL-3.0）；**ChartQA** 数据（GPL-3.0）；**ChartGemma**（基座 Gemma Terms 与卡片 MIT 冲突）；**Table Transformer 作为新依赖**（2024-06 停更、112 open issues，已被 Camelot ML / Docling TableFormer 覆盖）；**SciNCL / SPECTER2 用于中文**（`language: en`）；`jina-colbert-v2`（CC-BY-NC-4.0）；PyMuPDF 直连（AGPL-3.0）。

#### ⑧ 未核实（12 条，勿当事实）

Qwen3-Embedding 仓库 license 字段为空；DePlot 卡片 `language` 元数据与训练数据矛盾；Surya / PaddleOCR-VL / TableFormer **中文准确率无公开数据**；等。

---

## 3. 已知结论（本地实测，不依赖外部调研）

| 结论 | 证据 |
|---|---|
| **中文元数据层可以零依赖解决**，不需要 HanLP/LTP | 提交 `98fa6af`：全角 `［N］` + 无空格中文条目 + GB/T 7714 形状 → `citation_style` unknown → **ieee-numeric**、`reference_count` median **0 → 15**、10/10 篇有引用、条目识别 **0/152 → 148/152**。用的是 Python `re`，无新依赖 |
| **中文写作指标（14 条）的真正门槛不是工具，是词表 + 标注集** | 14 条指标依赖 8 个英文词表与英文分句；中文侧没有 Hyland 式现成体系，直译英文表会重演"看起来科学"的问题（#10 缺口 5、#11 准入检查表） |
| **重型 NLP 不应进主链** | paper-metrics 的确定性契约 + A 类共享 venv 的轻依赖现状 |

---

## 4. 与现有 skill 的接入点

```
PDF ──► paper-reader（B 类 GPU venv：MinerU/Marker）
          │  输出 content_list.json + _META.json（含 pdf_sha256 / engine_versions）
          ▼
        canonical_text()（分节正文，非正文块已计数丢弃）
          │
          ├──► paper-metrics（A 类纯 stdlib）：14 条 OBSERVED 指标 + 引用/参考文献元数据
          │       └─ 红线：逐字节可复现；任何模型只能做可选 Adapter
          │
          ├──► thesis-writing / 写作契约（消费 profile）
          └──► thesis-export / Words-Production（引用渲染：CSL 相关，见 2.C）
```

---

---

## 5. 四组共识：能结合进 SKILL 的只有三类归宿

| 归宿 | 内容 | 判据 |
|---|---|---|
| **直接进核心（纯 stdlib、逐字节可复现）** | 自建 CJK 分句与字面词表、pandoc + citeproc + CSL 样式、LexicalRichness 的**公式复刻**、BM25 检索基线、MinerU 既有 caption/chart/table 字段的确定性消费、Figure/Table 结构指标 | 零新依赖、确定性、可复算 |
| **可选 Adapter（默认关闭、能力自述、显式降级）** | 文本层探针（pdfplumber/pdftext）、spaCy-zh / jieba、`--discourse-probe`、L1 冻结 JSON 形式的图表模型产物、embedding 检索第二阶段 | 产物可寻址（sha256），缺能力输出 `null` 而非 0，不进 gate |
| **只做参考 / 明确不做** | HanLP（权重非商用）、LTP（研究许可）、全部神经篇章解析器、PyMuPDF（AGPL）、textstat 中文、UniChart/ChartQA（GPL）、ChartGemma、TATR 新依赖 | 许可或契约不容 |

**三条跨组结论（最重要）**：

1. **不需要换文档引擎**：中文版面解析本身没问题（A 组实测），#2/#3 的根因是 **canonical 层缺语义分段**；引擎也不提供语义标签 → 这是我们的职责。
2. **不需要重型中文 NLP**：中文侧最小可行栈是**纯 stdlib**（B 组三个问题的完整论证）；引进 HanLP/LTP 反而会同时破坏契约与许可安全。
3. **唯一"必须自建、无从下载"的硬缺口**：**中文学术词表 + 中文指标定义与标注集**（B 组 + C 组独立得出同一结论）。这才是中文能力从 1 到 10 的真实成本。

**两处静默损坏必须尽快可见化**（A 组）：英文段空格丢失、CNKI PDF 数字/标点丢失 ~52%——都不报错，会污染指标。

**两处合规欠披露**（D 组）：Surya 权重许可未记进 `_META.json`；MinerU 的 Apache-2.0 附加条款未写进文档。

---

## 6. 待拆分的高价值实现项（Issue #11 DoD 的最后一项）

| 来源 | Issue | 工时 | 是否阻塞 |
|---|---|---|---|
| A | **A1** paper-reader 文本层探针（pdfplumber/pdftext，不进 `canonical_text`） | 1–2 天 | 无 |
| A | **A2** canonical 层 front_matter 语义分段规则（根治 #2/#3） | 2–3 天 | 无 |
| A | **A3** 中文第三引擎 A/B（PaddleOCR-VL-1.6 / GLM-OCR vs MinerU 3.4.5） | 2–4 天 | 无 |
| B | **B-A** `paper-metrics` 中文写作指标 v1（**纯 stdlib**，验收含英文输出逐字节零回归） | 3–5 天 | 无 |
| B | **B-B** 中文词表 v1 + 字面匹配（`data/lexicons/v2-zh/`） | 3–5 天 | 无 |
| B | **B-C** paper-reader 落盘 `language/cjk_ratio/lang_source` | 0.5 天 | 无 |
| C | **C-1** 引用从"生成式书写"切到"确定性渲染"（pandoc `--citeproc`） | 2–3 天 | ⚠️ **被 GB/T 版本决策阻塞** |
| C | **C-2** 中文引用与参考文献结构化解析层（基础识别已随 `98fa6af` 落地） | 2–3 天 | 无 |
| C | **C-3** 确定性术语一致性检查器（LLM 只解释不判定） | 2–3 天 | 无 |
| D | **D-1** Figure/Table 结构确定性指标（纯 stdlib） | 1–2 天 | 无 |
| D | **D-2** 图注/表注配对 + 编号引用一致性校验（新 gate） | 1–2 天 | 无 |
| D | **D-3** 目标期刊语料检索 MVP（先 BM25） | 2–3 天 | 无 |
| D | **D-4** 合规披露补丁：`_META.json` 记 surya 与权重许可；文档写明 MinerU 附加条款；清理 venv 内重复 dist-info | 0.5 天 | 无 |

> 建议最小启动集：**A1 + B-A + D-4**（一个让静默损坏可见、一个把中文指标从 0 推到 1、一个 0.5 小时消掉合规欠披露）。
