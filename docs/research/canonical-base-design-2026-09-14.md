# 指标基座优化设计（content_list.json → 分层文档模型）

> 基线：commit `48231e6`；配套：issue #15（人读基座 md 的有损问题）
> 事实来源：21 篇真实语料（中文 11 + 英文 12）的 `content_list.json` 全块型普查
> 状态：**设计草案，未实现**（评审后再开 issue 排期）

## 0. 结论（TL;DR）

**指标基座今天是一个"+''"扁平字符串"，只覆盖 75.7% 字符、20.7% 数字**：

- 当前 `canonical_text()` 只取 `type=="text"` 的块；而**表格占全部数字的 72%**、公式占 79k 字符、题注 129 块、列表 73 块、图/图 114 个 —— **全部不可见**。
- 三个根因：① **范围是隐式的**（哪些块算"文档"从未写明，导致我之前把"范围差异"误报成"数字丢失 41.8–78.2%"）；② **字段异构**（不同块型内容散在 `text` / `table_body` / `list_items` / `img_path+题注` 里，单一字符串装不下）；③ **标记污染**（表格 HTML 有 **51.8% 是标签**，还贡献 **2600 个假数字**）。
- 设计：把基座升级为**分层文档模型** L0 raw → L1 类型化块清单 → L2 分流（prose / tables / captions / equations / footnotes / lists / inventory）→ L3 **每个指标声明 scope**。
- **关键性质：prose 流语义与代码路径不变 → 现有指标数值不变、指纹不变、两个已登记语料无需重登记**（可验证，见 §5）；新增的是**追加字段 + 新指标**。

## 1. 事实：块型普查（21 篇实测）

| type | blocks | chars | digits | 当前是否进 canonical |
|---|---:|---:|---:|---|
| `text` | 2032 | **580668** | **9825** | ✅ 是（唯一） |
| `table` | 81 | 95180 * | **34064** | ❌ 否（数字大头在这里） |
| `equation` | 488 | 79053 | 1764 | ❌ 否 |
| `header` | 183 | 6545 | 705 | ❌ 否（跑页眉） |
| `page_footnote` | 44 | 3854 | 489 | ❌ 否 |
| `aside_text` | 14 | 1285 | 197 | ❌ 否 |
| `page_number` | 259 | 534 | 465 | ❌ 否（页码） |
| `footer` | 2 | 87 | 5 | ❌ 否 |
| `list` | 73 | **19237** ‡ | **2175** ‡ | ❌ 否（内容在 `list_items`） |
| `image` / `chart` | 52 / 62 | 0 † | 0 † | ❌ 否（在 `img_path`+题注） |
| `code` | 19 | 0 † | 0 † | ❌ 否 |

\* 表格字符为**去标签后**；原始 HTML 字符串 197569 → 去标签 95180，**markup 占 51.8%**。
† 这些类型的文本不在 `text` 字段（图在 `img_path`+题注）→ 说明"字段异构"。
‡ 修正（2026-09-14 实现时发现）：本表初版把 `list` 记成 0 字符，是因为抽样脚本读了 `text` 字段；真实内容在 `list_items`。**中文语料 10 篇实测：lists 10 块 / 19237 字符 / 2175 数字**——整个流此前不但指标没读，连普查都漏了。同一轮还发现题注有**四个**键（`image_/table_/chart_/code_caption`），初版只认两个。

**合计**：chars 767206、digits 47514 → 当前 canonical 覆盖 **chars 75.7%、digits 20.7%**。
**表格 markup 的假数字**：raw 36664 → 去标签 34064（**2600 个数字是 HTML 属性/样式，不是内容**）。
**题注**：129 块（含数字 197）；**带 `text_level` 的标题块**：442（章节骨架的现有依据）。

## 2. 三个问题（都有证据）

### 2.1 范围隐式 → 口径不可审计（已经害过我两次）
- 第一次：对 `_MERGED.md` 测 2.1%、对 content_list 测 55.5%（换比较对象差一个数量级）；
- 第二次：把 "10 篇数字丢失 41.8–78.2%" 写进提交与 SKILL，实测**主因是"表格数字不在 prose 流里"**（含表后 0–50.5%）。
- 结论：**每个指标必须自带 scope**，否则同类误判会反复发生。

### 2.2 字段异构 → 单一扁平字符串装不下
`table → table_body`（HTML）、`list → list_items`、`image/chart → img_path + img_caption`、`equation → text 里的 LaTeX`、题注在 `table_caption`/`img_caption` 数组里。普查里 `list`=73 块、`image`=52、`chart`=62 在 `text` 字段都是 0 字符 —— 现有代码**完全看不见**它们。

### 2.3 标记污染 → 直接计数会错
- 表格：raw 里 51.8% 是标签；不减掉会**凭空多出 2600 个数字**并让字符量翻倍；
- 公式：488 块 79053 字符（≈162 字符/块），绝大多数是 LaTeX 命令 → 直接进词/句统计会污染句长与密度类指标。

## 3. 设计：分层文档模型（L0–L3）

### L0 — 原始层（现状，不动）
`mineru/<id>/auto/<id>_content_list.json` + sha256（已进 `_per_paper_metrics.jsonl.inputs`）。

### L1 — 类型化块清单（新，纯追加）
每个块产出一条规范化记录，**字段名与来源字段随类型而变**：

```json
{
  "block_id": "b0042",
  "type": "table",              // text|table|equation|caption|list|figure|header|footnote|pagenum|code
  "section": "method",           // 复用现有 labelled_blocks 的章节归属
  "level": null,                 // text 块的 text_level；标题才有
  "text_plain": "…",             // 规范化后的纯文本（表格=去标签单元格；公式=去命令保留变量/数字）
  "raw_chars": 2441, "plain_chars": 1180,
  "digits_plain": 312,
  "provenance": {"engine": "mineru", "file_sha256": "…"}
}
```

规范化器（每种一个，各自单测）：
- **HTML**（table_body）：去标签/属性/注释、解码实体、`<br>` 与 `</td>` → 分隔符、`colspan/rowspan` 不重复计数；
- **LaTeX**（equation / 行内）：剥离 `\begin{}\,\command{}\,{` 框架，保留变量名与数字；是否参与文本类指标由 scope 决定；
- **list**：`list_items` 逐项拼接（现状未读）；
- **caption**：`table_caption` / `img_caption` 数组拼接，并与所在图/表块**配对**（这是 D-2 编号一致性校验的基础）。

### L2 — 分流（新，只读不合并）
| 流 | 来源 | 用途 |
|---|---|---|
| `prose` | **现状 `canonical_text()` 原样** | 全部现有指标（句长/hedge/被动/连接词/名词化…） |
| `tables` | L1 的 table 块 `text_plain` | 表格密度、数字保真、表格结构类指标 |
| `captions` | L1 的 caption 块（含配对 id） | D-2 图注/表注配对与编号一致性 |
| `equations` | L1 的 equation 块 | 公式计数/密度（**不**参与句长） |
| `footnotes` | page_footnote | 待拍板（见 §7 Q1） |
| `inventory` | 图/图/表/列表的计数与题注 | D-1 图表结构确定性指标 |

### L3 — 指标声明 scope（新，防回归的关键）
每个指标在定义里带 `scope`，产物里随之输出：

```json
"M-SLEN-01": { "value": 26.2, "scope": ["prose"], … },
"D-TBL-01":  { "value": 6.0,  "scope": ["tables"], … },   // 新指标（示意）
"D-CAP-02":  { "value": 0.92, "scope": ["captions","inventory"], … }
```

**缺 `scope` 视为错误**（断言），否则"隐式口径"会以新形式回来。

### 四、产物字段（追加，不改现有含义）
- `_per_paper_metrics.jsonl`：新增 `block_census`（各类型 blocks/chars/digits/是否被 drop）与 `streams`（各流字符数）；
- `_domain_profile.json`：指标定义处新增 `scope`；新增 `base_artifact`（canonical 的产物名 + sha256 + **范围声明**）；
- 这份 `base_artifact` 正是 **#8 要的"唯一基座产物 + 哈希 + 范围明文"**。

## 4. 与 #8 / #15 的推进顺序

| 步骤 | 内容 | 是否改指标数值 | 成本 |
|---|---|---|---|
| 1 | L1 普查（`block_census`）：只统计、不改任何流 | ❌ | ~1 天 |
| 2 | L1 规范化器 + L2 分流 + L3 scope 字段（现存指标全部声明 `prose`） | ❌（可证明） | ~1–2 天 |
| 3 | D-1/D-2 新指标建立在 `tables/captions/inventory` 流上 | ❌（纯新增） | 见 #1 排期 |
| 4 | 若要"指标含表格"等口径变更 → schema bump + 重登记 | ✅ | 独立一次 |

**#15（人读基座）与本设计并行不冲突**：#15 改 md 合并；本设计改 json 消费方式。

## 5. 验收判据

1. **prose 流不变**：433 测试全绿；`audit_corpus ycgl-zh` 仍 23/23、`vrp-en` 仍 22/22，且 `corpus_id` 与 `expected_metrics` **不变**（这是"没动数值"的硬证据）；
2. `block_census` 可由 JSON 复算：同输入两次运行逐字节一致；
3. 表格规范化有单测（标签/属性/实体/colspan）＋在真实语料上抽查：strip 后数字与人工核对一致（给误差带）；
4. **每个指标都带 `scope`**，缺失即测试失败；
5. 新流不得影响任何现存指标（用"改动前后 `_per_paper_metrics.jsonl` 逐字节一致"证明）。

## 6. 明确不做

- ❌ 不把表格塞进 `prose` 一起算句长/密度（表格不是行文，会污染 style 指标——这是现在 prose-only 的**正当理由**，不是缺陷）；
- ❌ 不做 LaTeX 语义解析（只做命令剥离 + 变量/数字保留）；
- ❌ 不在本设计里做**块级双引擎合并**（那是 P2，需要 per-block provenance 才可审计，另开设计）；
- ❌ 不为"数字好看"调整任何现有指标阈值。

## 7. 需要你拍板的四个问题

| # | 问题 | 我的倾向 |
|---|---|---|
| Q1 | `page_footnote`（脚注）算不算正文？ | 独立流，默认**不计入** prose，但进 census（脚注常含数据来源，将来可能需要） |
| Q2 | 表格数字要不要进"信息密度/数字保真"指标？ | 要，但作为**新指标**（不改现有）；这也能把 #12 探针的"损坏"判定改成"分口径" |
| Q3 | 默认 scope 是否保持 `prose-only`？ | 保持（否则所有历史结论作废）；含表口径作为可选开关 + 显式版本 |
| Q4 | 现在就建 `inventory/captions` 流，还是等 #1 的 MVP 范围定了再做？ | 现在建（成本低、且 D-1/D-2 是调研给出的 #1 MVP 起点） |
