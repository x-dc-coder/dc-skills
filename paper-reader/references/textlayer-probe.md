# 文本层探针（阶段 1.5）实测记录与离线复核

> 从 SKILL.md 拆出（2026-09-24，B7a）。两段本机实测数据（历史证据，回答"这跳损坏到底多严重"）与单独跑命令；判定规则与红线仍在 SKILL.md 正文。

### 本机实测（《运筹与管理》10 篇中文核心期刊，2026-09-13）

| 损坏类型 | 实测结果 |
|---|---|
| **数字丢失（CNKI 全角字体）** | **10/11 篇命中，丢失率 73 %–90 %**（此前估计 ~52 %，实测更严重）。CNKI 把数字编码成全角 `１２.７３`，PDF 文本层里有、转换后消失（MinerU issue #5330） |
| **英文段空格丢失** | **8 篇命中**：PDF 文本层 **0** 条 15+ 字母长串，canonical 侧 **58** 条 → **空格是转换过程弄丢的**，不是 PDF 本身的问题 |

### 本机实测（2026-09-14，中文语料 10 篇，比较对象 = content_list）

| 结论 | 数值 |
|---|---|
| verdict | **10/10 篇 `warn`**（`DIGIT_LOSS_HIGH` + `UNSPACED_ENGLISH_RUNS`） |
| 数字丢失率 | **41.8% – 78.2%**（PDF 侧 1094 个数字**全是全角**，CNKI 自定义字体编码） |
| 空格引入 | 每篇 75–104 条 15+ 字母长串 |
| 确定性 | 同输入两次运行 JSON **逐字节一致**（10/10） |
| 与指标层一致 | 语言标签与 `text_metrics.detect_language` **10/10 一致** |

### 单独跑（离线复核用）

```bash
cd ~/projects/dc-skills && uv run python paper-reader/scripts/textlayer_probe.py \
    --pdf paper.pdf --canonical canonical.txt --canonical-source mineru_content_list \
    --out _textlayer_probe.json
```
