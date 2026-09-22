#!/usr/bin/env python3
"""M-PAS-09 spot-check sampler and adjudication harness.

Draws a deterministic, reproducible sample of passive candidates from a real
paper-analysis corpus so the M-PAS-09 rule ("be/get/become auxiliary +
optional adverbs + past participle") can be checked phrase by phrase, and
renders a human-adjudicable markdown table plus a machine-readable JSON file.

The tool reuses text_metrics itself (detect_passive_spans and the frozen
auxiliary / adverb / participle tables), so the sampled universe is exactly
what the metric counts - there is no second implementation to drift.

Three strata (this is where the evidence comes from):
  P  auto-POSITIVE : sentences where detect_passive_spans() found a phrase.
                     -> measures PRECISION (are the counted matches real?)
  B  boundary      : sentences with a passive auxiliary that did NOT resolve to
                     a past participle ("is important", "are fundamental").
                     -> measures the copula/adjective boundary, the miss class
                        that the B2 fix targeted.
  N  plain negative: sentences without any passive auxiliary.
                     -> baseline negative control for the annotator.

Sampling rule (deterministic, no RNG):
  * candidates are collected per paper in directory order (paper_key = the
    first path component under --corpus, the same key the profiler uses) and
    sentences in document order;
  * each stratum is sampled systematically: stride = floor(N_stratum / needed),
    taking element 0, stride, 2*stride, ... so samples spread over the corpus;
  * default quotas are 20 / 10 / 10, i.e. 40 candidates with a 50/50 split
    between auto-positive and auto-negative.

Adjudication (--verdicts JSON, optional): {"P01": "passive", "B04": "unclear",
...}; accepted values are passive / not_passive / unclear.  The adjudication
column stays empty without --verdicts so a human can fill it in.

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/pas_spotcheck.py \
        --corpus /path/to/paper-analysis --out /tmp/_pas_spotcheck.md \
        --json /tmp/_pas_spotcheck.json --verdicts /tmp/verdicts.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import text_metrics as tm  # noqa: E402

TOOL_VERSION = "1.0"
MIN_NEGATIVE_TOKENS = 6
VERDICT_PASSIVE = "passive"
VERDICT_NOT_PASSIVE = "not_passive"
VERDICT_UNCLEAR = "unclear"
VERDICTS = (VERDICT_PASSIVE, VERDICT_NOT_PASSIVE, VERDICT_UNCLEAR)
STRATUM_POSITIVE = "auto_passive"
STRATUM_BOUNDARY = "auto_not_passive_with_aux"
STRATUM_PLAIN = "auto_not_passive_no_aux"

CRITERIA = """判定准则（什么算被动，what counts as a passive）：
1. 结构：被动助动词（be / am / is / are / was / were / been / being / get /
   gets / got / gotten / become / becomes / became）+ 可选副词（最多 2 个）+ 过去分词
   （不规则表 >= 100 条，或 -ed 结尾）。
2. 算被动：施事被隐去或由 by 短语引入的动词性被动，例如 "was built"、
   "were carefully collected"、"has been adopted"、"can be mapped"。
3. 不算被动（系表 / 形容词性过去分词）：be + 形容词，例如
   "is complicated"、"were tired"、"is limited"、"is based on"；
   这类词见 text_metrics._PARTICIPIAL_ADJECTIVE_DENYLIST。
4. 不算被动：be + 名词 / be + 地点 / 纯系表判断（"is important"）。
5. 不算被动：无被动助动词的过去分词（限定性/简化关系从句），例如
   "an encoder used in modeling VRPs"、"the prompt described in Section III"。
6. 边界情形按"句子层面是否出现被动动词短语"裁决；同一句有多处被动仍只算 1 次
   （分母口径是句级，见 metric-definitions.md 3.8）。
7. 关键词行、版权/作者须知/附录页、行内公式落入正文块时：其中若不含被动动词短语即
   不判被动；若含则按结构判定，并在备注标出"前置页/非正文"。
"""

LIMITATIONS = """局限声明（必须随结论一起引用）：
- 本抽检的裁决由 **agent 完成，不是专家人工金标准**（no expert gold standard）；
  任何复核者都可以按上面的判定准则推翻单条结论，推翻后精确率/召回率需重算。
- 样本量固定为本次实际裁决的 N 条（见汇总表），且为**系统性抽样**而非随机抽样；
  结论只覆盖这 N 条及其来源句，**不得外推为全语料精确率/召回率**。
- 精确率只在 P 层（auto-POSITIVE）估计；召回率以"漏报率"形式只在 B/N 两层
  （auto-NEGATIVE）估计，它不是全语料 recall —— 未抽到的句子未参与标注。
- 抽检语料文本 = content_list.json 中全部 text 块拼接，**宽于** profiler 的
  分节正文输入（含题名/作者/关键词/参考文献/附录），因此句数大于 profiler 的
  统计口径；指标实现相同，只有输入文本范围不同。
- 该规则是句级、纯 stdlib、无依存分析器；与设计案的子句级 0.18 / 0.42 不可比。
"""


@dataclass(frozen=True)
class Candidate:
    item_id: str
    stratum: str               # P / B / N
    paper_key: str
    sentence_index: int
    sentence: str
    char_offset: int
    line_number: int
    span: tuple[int, int] | None      # phrase span (P) or trigger span (B)
    span_text: str
    auto_verdict: str
    detail: str
    verdict: str = ""
    note: str = field(default="")


def discover_papers(corpus: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in sorted(corpus.glob("*/mineru/*/auto/*_content_list.json")):
        try:
            key = str(path.relative_to(corpus).parts[0])
        except (ValueError, IndexError):
            continue
        found.append((key, path))
    return found


def paper_text(path: Path) -> str:
    try:
        blocks = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("text")
    )


def _trigger(text: str, sentence: tm.Span):
    """First auxiliary (+ <=2 adverbs) in a sentence, and the token after it."""
    tokens = tm._tokens_with_spans(text, sentence.start, sentence.end)
    for position in range(len(tokens)):
        token = tokens[position][0]
        if token not in tm._PASSIVE_AUXILIARIES:
            continue
        cursor = position + 1
        skipped = 0
        while cursor < len(tokens) and skipped < 2 and tokens[cursor][0] in tm._PASSIVE_ADVERBS:
            cursor += 1
            skipped += 1
        following = tokens[cursor][0] if cursor < len(tokens) else ""
        end = tokens[cursor][2] if cursor < len(tokens) else tokens[position][2]
        return {
            "aux": token,
            "following": following,
            "resolved": bool(following) and tm._is_past_participle(following),
            "span": (tokens[position][1], end),
            "text": text[tokens[position][1]:end],
        }
    return None


def collect_candidates(corpus: Path):
    """Return (positives, boundary, plain, n_papers, n_sentences)."""
    positives: list[Candidate] = []
    boundary: list[Candidate] = []
    plain: list[Candidate] = []
    n_papers = 0
    n_sentences = 0
    for paper_key, path in discover_papers(corpus):
        text = paper_text(path)
        if len(text) < 500:
            continue
        n_papers += 1
        sentences = tm.split_sentences(text)
        n_sentences += len(sentences)
        hits, _unresolved = tm._detect_passive(text, sentences)
        by_sentence = {(hit.sentence.start, hit.sentence.end): hit for hit in hits}
        for index, sentence in enumerate(sentences):
            hit = by_sentence.get((sentence.start, sentence.end))
            line_number = text[:sentence.start].count("\n") + 1
            if hit is not None:
                positives.append(Candidate(
                    item_id="", stratum=STRATUM_POSITIVE, paper_key=paper_key,
                    sentence_index=index, sentence=sentence.text,
                    char_offset=sentence.start, line_number=line_number,
                    span=(hit.phrase_start, hit.phrase_end), span_text=hit.phrase,
                    auto_verdict=VERDICT_PASSIVE,
                    detail="aux(+adv)+participle -> participle=" + hit.participle,
                ))
                continue
            if len(tm.tokenize(sentence.text)) < MIN_NEGATIVE_TOKENS:
                continue
            trigger = _trigger(text, sentence)
            if trigger is None:
                plain.append(Candidate(
                    item_id="", stratum=STRATUM_PLAIN, paper_key=paper_key,
                    sentence_index=index, sentence=sentence.text,
                    char_offset=sentence.start, line_number=line_number,
                    span=None, span_text="", auto_verdict=VERDICT_NOT_PASSIVE,
                    detail="no passive auxiliary in sentence",
                ))
            else:
                boundary.append(Candidate(
                    item_id="", stratum=STRATUM_BOUNDARY, paper_key=paper_key,
                    sentence_index=index, sentence=sentence.text,
                    char_offset=sentence.start, line_number=line_number,
                    span=trigger["span"], span_text=trigger["text"],
                    auto_verdict=VERDICT_NOT_PASSIVE,
                    detail="aux=" + trigger["aux"] + " next=" + (trigger["following"] or "-"),
                ))
    return positives, boundary, plain, n_papers, n_sentences


def systematic_sample(items: list[Candidate], count: int) -> list[Candidate]:
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    stride = max(1, len(items) // count)
    picked: list[Candidate] = []
    position = 0
    while len(picked) < count and position < len(items):
        picked.append(items[position])
        position += stride
    position = 0
    while len(picked) < count and position < len(items):
        if items[position] not in picked:
            picked.append(items[position])
        position += 1
    return picked


def _with_ids(items: list[Candidate], prefix: str) -> list[Candidate]:
    out = []
    for number, item in enumerate(items, 1):
        out.append(Candidate(**{**item.__dict__, "item_id": "%s%02d" % (prefix, number)}))
    return out


def _escape(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def apply_verdicts(items: list[Candidate], verdicts: dict[str, str]) -> list[Candidate]:
    out = []
    for item in items:
        verdict = str(verdicts.get(item.item_id, "")).strip().lower()
        if verdict and verdict not in VERDICTS:
            verdict = VERDICT_UNCLEAR
        note = str(verdicts.get(item.item_id + "_note", "")).strip()
        out.append(Candidate(**{**item.__dict__, "verdict": verdict, "note": note}))
    return out


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054):
    """Two-sided 95% Wilson score interval for a proportion (stdlib only).

    Reported next to every point estimate: with N=20 and 0 errors the point
    estimate is 1.0 but the interval lower bound is ~0.84, so the credential
    never overstates what 20 samples can prove.
    """
    if trials <= 0:
        return (None, None)
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (proportion + z * z / (2.0 * trials)) / denominator
    half = (z * math.sqrt(proportion * (1 - proportion) / trials
                          + z * z / (4.0 * trials * trials))) / denominator
    return (round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6))


def _stratum_stats(items: list[Candidate]) -> dict:
    judged = [i for i in items if i.verdict in (VERDICT_PASSIVE, VERDICT_NOT_PASSIVE)]
    return {
        "sampled": len(items),
        "judged": len(judged),
        "unclear": len([i for i in items if i.verdict == VERDICT_UNCLEAR]),
        "judged_passive": len([i for i in judged if i.verdict == VERDICT_PASSIVE]),
        "judged_not_passive": len([i for i in judged if i.verdict == VERDICT_NOT_PASSIVE]),
    }


def summarise(positives: list[Candidate], boundary: list[Candidate],
              plain: list[Candidate]) -> dict:
    pos = _stratum_stats(positives)
    bnd = _stratum_stats(boundary)
    pln = _stratum_stats(plain)
    negatives = boundary + plain
    neg = _stratum_stats(negatives)
    summary = {
        "n_positives_sampled": pos["sampled"],
        "n_boundary_sampled": bnd["sampled"],
        "n_plain_negative_sampled": pln["sampled"],
        "n_negatives_sampled": neg["sampled"],
        "n_positives_judged": pos["judged"],
        "n_negatives_judged": neg["judged"],
        "n_unclear": pos["unclear"] + bnd["unclear"] + pln["unclear"],
        "true_positives": pos["judged_passive"],
        "false_positives": pos["judged_not_passive"],
        "false_negatives_boundary": bnd["judged_passive"],
        "false_negatives_plain": pln["judged_passive"],
        "precision": (pos["judged_passive"] / pos["judged"]) if pos["judged"] else None,
        "miss_rate_boundary": (bnd["judged_passive"] / bnd["judged"]) if bnd["judged"] else None,
        "miss_rate_plain": (pln["judged_passive"] / pln["judged"]) if pln["judged"] else None,
        "miss_rate_negative_half": (neg["judged_passive"] / neg["judged"]) if neg["judged"] else None,
        "recall_estimate_from_negative_half": (
            1 - neg["judged_passive"] / neg["judged"]) if neg["judged"] else None,
    }
    pos_lo, pos_hi = wilson_interval(pos["judged_passive"], pos["judged"])
    neg_lo, neg_hi = wilson_interval(neg["judged_passive"], neg["judged"])
    summary["precision_ci95_low"] = pos_lo
    summary["precision_ci95_high"] = pos_hi
    summary["miss_rate_negative_ci95_low"] = neg_lo
    summary["miss_rate_negative_ci95_high"] = neg_hi
    summary["ci_method"] = "Wilson score interval, z=1.959963984540054"
    return summary


def render_markdown(meta: dict, items: list[Candidate], summary: dict) -> str:
    lines: list[str] = []
    lines.append("# M-PAS-09 被动判定抽检凭证（短语级 span）")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    for key in ("tool_version", "generated_for", "corpus", "n_papers", "n_sentences",
                "n_candidates_positive", "n_candidates_boundary", "n_candidates_plain",
                "n_positives_sampled", "n_boundary_sampled", "n_plain_negative_sampled",
                "sampling_rule", "min_negative_tokens", "adjudicator",
                "adjudicated_on", "command"):
        if key in meta:
            lines.append("| %s | %s |" % (key, _escape(str(meta[key]))))
    lines.append("")
    lines.append("## 1. 判定准则")
    lines.append("")
    lines.append(CRITERIA)
    lines.append("## 2. 汇总")
    lines.append("")
    lines.append("| 量 | 值 |")
    lines.append("|---|---|")
    for key, value in summary.items():
        if isinstance(value, float):
            lines.append("| %s | %.6f |" % (key, value))
        else:
            lines.append("| %s | %s |" % (key, value))
    lines.append("")
    lines.append("## 3. 逐条清单")
    lines.append("")
    lines.append("| # | 层 | paper_key | 句片段 | 命中/触发 span | span 文本 | 自动判定 | 裁决 | char 偏移 | 行号 | 备注 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for item in items:
        span = "[%d, %d]" % item.span if item.span else "-"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %s |" % (
            item.item_id, item.stratum, _escape(item.paper_key),
            _escape(item.sentence[:160]), span, _escape(item.span_text),
            item.auto_verdict, item.verdict or "", item.char_offset, item.line_number,
            _escape(item.note or item.detail),
        ))
    lines.append("")
    lines.append("## 4. 局限声明")
    lines.append("")
    lines.append(LIMITATIONS)
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M-PAS-09 passive spot-check sampler")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="markdown output")
    parser.add_argument("--json", type=Path, help="machine-readable output")
    parser.add_argument("--positives", type=int, default=20)
    parser.add_argument("--boundary", type=int, default=10)
    parser.add_argument("--plain-negatives", type=int, default=10, dest="plain_negatives")
    parser.add_argument("--verdicts", type=Path, help="JSON {item_id: verdict}")
    parser.add_argument("--command", default="", help="reproduction command recorded in the report")
    parser.add_argument("--adjudicator", default="agent (metrics-text), not an expert gold standard",
                        help="who produced the verdicts; recorded in the report")
    parser.add_argument("--adjudicated-on", default="", dest="adjudicated_on",
                        help="date the verdicts were produced")
    args = parser.parse_args(argv)

    positives, boundary, plain, n_papers, n_sentences = collect_candidates(args.corpus)
    n_cand = (len(positives), len(boundary), len(plain))
    positives = _with_ids(systematic_sample(positives, args.positives), "P")
    boundary = _with_ids(systematic_sample(boundary, args.boundary), "B")
    plain = _with_ids(systematic_sample(plain, args.plain_negatives), "N")
    verdicts: dict[str, str] = {}
    if args.verdicts:
        verdicts = json.loads(args.verdicts.read_text(encoding="utf-8"))
    positives = apply_verdicts(positives, verdicts)
    boundary = apply_verdicts(boundary, verdicts)
    plain = apply_verdicts(plain, verdicts)
    items = positives + boundary + plain
    summary = summarise(positives, boundary, plain)
    meta = {
        "tool_version": TOOL_VERSION,
        "generated_for": "M-PAS-09 evidence precision / recall spot-check",
        "corpus": str(args.corpus),
        "n_papers": n_papers,
        "n_sentences": n_sentences,
        "n_candidates_positive": n_cand[0],
        "n_candidates_boundary": n_cand[1],
        "n_candidates_plain": n_cand[2],
        "n_positives_sampled": len(positives),
        "n_boundary_sampled": len(boundary),
        "n_plain_negative_sampled": len(plain),
        "sampling_rule": "systematic stride over document-ordered candidates; no RNG",
        "min_negative_tokens": MIN_NEGATIVE_TOKENS,
        "command": args.command,
        "adjudicator": args.adjudicator,
        "adjudicated_on": args.adjudicated_on,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(meta, items, summary), encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": meta, "criteria": CRITERIA, "limitations": LIMITATIONS,
            "summary": summary,
            "items": [item.__dict__ | {"span": list(item.span) if item.span else None}
                      for item in items],
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                             encoding="utf-8")
    print("[pas_spotcheck] papers=%d sentences=%d candidates(P=%d B=%d N=%d) sampled(P=%d B=%d N=%d)"
          % (n_papers, n_sentences, n_cand[0], n_cand[1], n_cand[2],
             len(positives), len(boundary), len(plain)))
    print("[pas_spotcheck] precision=%s miss_boundary=%s miss_plain=%s recall_est=%s (judged P=%d neg=%d)"
          % (summary["precision"], summary["miss_rate_boundary"], summary["miss_rate_plain"],
             summary["recall_estimate_from_negative_half"],
             summary["n_positives_judged"], summary["n_negatives_judged"]))
    print("[pas_spotcheck] wrote %s%s" % (args.out, " and " + str(args.json) if args.json else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
