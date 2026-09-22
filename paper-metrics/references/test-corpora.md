# 测试语料登记（可审计）

> 机器可读的登记表在 `data/test-corpora.json`，本文件是它的人读说明与获取方式。
> 审计入口：`uv run python paper-metrics/scripts/audit_corpus.py --name <name>`

指标值只有在能说清"**哪份输入 + 哪版代码**产生的"时才可审计。登记表因此记录三样东西：

| 记录项 | 含义 | 判读方式 |
|---|---|---|
| `corpus_id` | 语料全部输入产物的内容哈希 | **不同 = 输入变了**，其余比较全部失效，必须重录 |
| `expected_metrics` | 录制时已验证的语料均值 | 用于检出数值漂移 |
| `recorded_with` | profiler / schema / 指标层 / 词表 release 的版本与指纹 | 用于**解释**漂移：代码或词表变了 |

```bash
cd ~/projects/dc-skills
uv run python paper-metrics/scripts/audit_corpus.py --name vrp-en --out /tmp/audit-en     # 英文语料
uv run python paper-metrics/scripts/audit_corpus.py --name ycgl-zh --out /tmp/audit-zh    # 中文语料
```

退出码 0 = 逐项 match；1 = drift。drift 时报告会给出**归因**：

| cause | 含义 | 该怎么办 |
|---|---|---|
| `inputs_changed` | `corpus_id` 变了 | 语料被动过；旧结论作废，重新录制 |
| `code_or_word_list_changed` | 语料没变，但版本/词表指纹变了 | 数值移动是被解释的；确认是有意改动后更新登记表 |
| `unexplained_drift` | 语料与版本都没变，数值却变了 | **确定性被破坏**，必须查（这是审计真正要找的情况） |

---

## 1. `vrp-en` — 英文基线语料

| 项 | 值 |
|---|---|
| 路径 | `/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis` |
| 语言 / 篇数 | en / 34 篇（跳过 2 篇无 `content_list.json`） |
| 来源 | `paper-reader` 用 **MinerU + Marker 双引擎**转换 |
| `corpus_id` | `a95e790e481b01e9cb6bca83a80bcf020f02929c630f3c77cf1ae1673950dfb3` |
| 用途 | ① 英文基线自证（`baseline_eval` 8/8 PASS）② **英文逐字节零回归对照**：中文化改造后 `_per_paper_metrics.jsonl` 必须与登记值一致 |

**已知缺陷**：语料目录内**不含源 PDF**（只有 `marker/` 与 `mineru/` 产物）→ 无法在本语料上跑阶段 1.5 文本层探针；2 篇缺 `content_list.json`。

**复现**：
```bash
uv run python paper-metrics/scripts/profile_papers.py --corpus "<path>" --out <out>
uv run python paper-metrics/scripts/baseline_eval.py --corpus "<path>" --out <out>
```

## 2. `ycgl-zh` — 中文语料（《运筹与管理》10 篇）

| 项 | 值 |
|---|---|
| 路径 | `/mnt/e/AllProjects202601/M-PCA/_paper-metrics-run/运筹与管理/corpus_ycgl_pdf/paper-conversion` |
| 语料根 | `…/运筹与管理/`（`corpus_ycgl_pdf/paper-merged/*/_META.json` 记录各类映射） |
| 语言 / 篇数 | zh / 10 篇（跳过 1 篇无 `content_list.json`） |
| 来源 | CNKI 下载 PDF → **仅 MinerU** 转换 |
| `corpus_id` | `da93384bfdbef4583a32dcf4ad39104175fe9156d974a646d2fcbf2420f4d750` |
| 词表 release | `2.1-zh`，指纹 `cb0634e2…` |
| 用途 | ① 中文 13/14 条指标的基准 ② 中文写作契约（p25/p75）③ 词表校准与效度检验的取样池 |

**已知缺陷（都是实测，不是推测）**：

1. **只跑了 MinerU**：各篇 `_META.json` 的 `engines="mineru"`，没有 Marker 对照 → 本语料上**引擎漂移（V7）是 N/A**，不是"已测量"；
2. **CNKI 数字/标点静默丢失 73%–90%**（10/11 篇，`textlayer_probe.py` 实测；MinerU issue #5330 至今 open）→ 数字类解读要打折扣；
3. **英文段空格丢失**：canonical 侧 58 条 15+ 字母长串、PDF 侧 0 条 → 是**转换引入**的（8 篇命中）；
4. `corpus_ycgl/` 是**空目录**；实际语料在 `corpus_ycgl_pdf/paper-conversion/`；
5. 同目录下的 `_draft_validation*.json`、`_writing_contract.cn.yaml` 是**历史产物**，口径早于 2026-09-13 的中文化改造，**不可当基线**。

**复现**：
```bash
uv run python paper-metrics/scripts/profile_papers.py --corpus "<path>" --out <out>
uv run python paper-metrics/scripts/build_contract.py --summary "<out>/_corpus_summary.json" --out "<out>/_writing_contract.zh.yaml"
uv run python paper-metrics/scripts/validate_draft.py --contract "<out>/_writing_contract.zh.yaml" --draft "<draft.md>"
uv run python paper-metrics/scripts/lexicon_calibration.py validate --corpus "<path>" --out "<out>" --lexicon hedge
```

> 中文数值**不可与英文语料的数值比较**：句长单位（cjk-units/sentence）、密度分母（cjk-units）、MTLD 口径（字符级）、名词化口径（抽象名词后缀）都不同。

## 3. 草稿样本

| 名称 | 位置 | 用途 |
|---|---|---|
| `zh-draft-real` | `/tmp/zh_draft_real.md`（易失） | 验证 `validate_draft` 在真实中文正文上的 pass/warn/skip 分布与证据行号 |

**重建配方**：取 `ycgl-zh` 排序第一的论文，取其 `canonical_text()` 的前 6000 字，按空行分段后再用空行连接（`/tmp/make_zh_draft.py` 是当时用的脚本，逻辑即此）。

参考结果（词表 release `2.1-zh`、`TEXT_METRICS_VERSION 1.4`）：契约 14 条 → **pass=9 / warn=4 / skipped=1**，退出码 0；4 条 warn 为 `C-M-AWR-03`、`C-M-HED-14`、`C-M-NOM-10`、`C-M-PCNT-25`。

---

## 4. 登记一个新语料

```bash
# 1) 先探明身份（打印 corpus_id / 篇数 / 工具链 / 期望指标，可直接粘进登记表）
uv run python paper-metrics/scripts/audit_corpus.py --corpus "<new-corpus>" --out /tmp/new --json /tmp/new.json
# 2) 把结果写进 data/test-corpora.json 的 corpora[]，并在本文件补一节
# 3) 立刻跑一次审计确认 match
uv run python paper-metrics/scripts/audit_corpus.py --name <new-name> --out /tmp/audit-new
```

**规则**：`recorded_with` 必须用**当次**跑出来的版本号；非英文语料要同时记 `lexicon_release_version`（`toolchain.lexicon_releases[lang].version`）与 `lexicon_fingerprint`——注意 `toolchain.lexicon_version` 这个历史字段记的**是英文 release**，两者不要混。
