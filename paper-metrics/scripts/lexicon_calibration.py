#!/usr/bin/env python3
"""Chinese lexicon calibration harness (issue #13, step 1).

The Chinese word lists in data/lexicons/v2-zh/ are **curated**: there is no
public, redistributable Hyland-equivalent resource for Chinese, so they were
built from writing norms plus manual confirmation on real papers.  A curated
list is a hypothesis, not a fact.  This tool turns it into a measurable claim:

  * 'sample' draws a deterministic, stratified sample of sentences and writes a
    sheet with EMPTY annotation columns, so a human can label them;
  * 'score'  turns filled sheets into precision / false-negative rate / Cohen
    kappa, with Wilson intervals;
  * 'mine'   lists frequent CJK n-grams that are NOT in any lexicon, as
    candidate additions (machine proposes, human confirms).

Strata (where the evidence comes from)
--------------------------------------
  P  lexicon positive : the sentence matched an entry.  Measures PRECISION
                        (are the counted matches real hedges/boosters/...?).
  N  control negative : the sentence matched nothing.  A systematic sample of
                        these estimates the FALSE-NEGATIVE rate: labelling a
                        control sentence as positive means the lexicon missed it.

Sampling is deterministic (no RNG): candidates are collected per paper in
directory order and per sentence in document order, then each stratum is sampled
systematically with stride = floor(n_stratum / needed).  The same corpus and the
same quotas produce the same sheet, and the lexicon fingerprint is recorded so a
calibration can never be attributed to the wrong word list.

Honesty rules
-------------
* The adjudicator method (--method human|model) is recorded.  A model pass is a
  pre-annotation, not a gold standard: the report says so and refuses to claim
  kappa, which needs two independent human passes.
* Nothing here changes a lexicon.  Raising a release version is a reviewed
  change (source field + LEXICON_VERSIONS bump), never a side effect of scoring.

Usage
-----
    cd ~/projects/dc-skills
    # 1) draw sheets (120 positives + 80 controls by default)
    uv run python paper-metrics/scripts/lexicon_calibration.py sample --corpus <corpus> --out /tmp/calib --lexicon hedge
    # 2) a human fills annotator_a (and annotator_b for a second pass) in the TSV
    # 3) score
    uv run python paper-metrics/scripts/lexicon_calibration.py score --sheets /tmp/calib/_calibration_hedge.tsv --out /tmp/calib
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import text_metrics as tm  # noqa: E402

#: Lexicons that can be calibrated this way.  The nominalization_* files are
#: placeholders for Chinese (M-NOM-10 is not enabled) and stopwords carry no
#: judgement, so they are deliberately absent.
CALIBRATABLE = ("hedge", "booster", "academic_words", "connectors")
CONNECTOR_GROUPS = ("contrastive", "causal", "result")
#: A maximal run of CJK characters (candidate mining slides over these).
CJK_RUN_RE = __import__("re").compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,}")
POSITIVE_LABELS = {"y", "yes", "1", "true", "hit"}
NEGATIVE_LABELS = {"n", "no", "0", "false", "miss"}


@dataclass
class Candidate:
    item_id: str
    stratum: str
    paper_key: str
    sentence: str
    matched: list = field(default_factory=list)


def entries_for(bundle, lexicon: str) -> dict:
    """{group_name: entries} for one calibratable lexicon."""
    if lexicon == "connectors":
        return {group: bundle.connectors[group].entries for group in CONNECTOR_GROUPS}
    return {lexicon: getattr(bundle, lexicon).entries}


def lexicon_fingerprint(bundle, lexicon: str) -> str:
    """sha256 over the entries actually used, so a score names its word list."""
    payload = json.dumps({name: list(entries)
                          for name, entries in sorted(entries_for(bundle, lexicon).items())},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect(corpus: Path, lexicon: str, bundles: dict):
    """Deterministic (positives, negatives) candidate lists for one lexicon."""
    import profile_papers as pp

    bundle = bundles["zh"]
    table = entries_for(bundle, lexicon)
    flat = [entry for entries in table.values() for entry in entries]
    positives: list = []
    negatives: list = []
    for paper_dir in sorted(corpus.iterdir()):
        content_lists = sorted(paper_dir.glob("mineru/**/*content_list.json"))
        if not content_lists:
            continue
        paper = pp.Paper(name=paper_dir.name, content_list_path=content_lists[0],
                         paper_key=paper_dir.name)
        text = paper.canonical_text()
        if tm.detect_language(text)["language"] != "zh":
            continue
        for span in tm.split_sentences(text):
            hits = tm._match_cjk_entries([span], flat)
            matched = sorted({span.text[start - span.start:end - span.start]
                              for start, end, _ in hits})
            candidate = Candidate(item_id="", stratum="", paper_key=paper_dir.name,
                                  sentence=span.text[:400], matched=matched)
            (positives if hits else negatives).append(candidate)
    return positives, negatives, table


def systematic_sample(items: list, count: int) -> list:
    """stride sampling: element 0, stride, 2*stride, ... (no RNG)."""
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    stride = max(1, len(items) // count)
    return items[::stride][:count]


def write_sheets(items: list, prefix: str, out_dir: Path) -> Path:
    path = out_dir / ("_calibration_%s.tsv" % prefix)
    lines = ["item_id\tstratum\tmatched_entries\tsentence\tannotator_a\tannotator_b"]
    for item in items:
        sentence = item.sentence.replace("\t", " ").replace("\n", " ")
        lines.append("%s\t%s\t%s\t%s\t\t" % (item.item_id, item.stratum,
                                                ",".join(item.matched), sentence))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_sheets(path: Path) -> list:
    rows: list = []
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        rows.append(dict(zip(header, cells + [""] * (len(header) - len(cells)))))
    return rows


def _label(value: str):
    text = (value or "").strip().lower()
    if text in POSITIVE_LABELS:
        return "positive"
    if text in NEGATIVE_LABELS:
        return "negative"
    return None


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054):
    """95% Wilson score interval (same statistic the M-PAS-09 spot-check uses)."""
    if trials <= 0:
        return None, None
    phat = successes / trials
    denominator = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denominator
    margin = (z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials))
              / denominator)
    return round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)


def cohen_kappa(pairs: list):
    """Cohen kappa over two annotators' labels (both filled rows only)."""
    if not pairs:
        return None
    labels = sorted({label for pair in pairs for label in pair})
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    expected = 0.0
    for label in labels:
        share_a = sum(1 for a, _ in pairs if a == label) / n
        share_b = sum(1 for _, b in pairs if b == label) / n
        expected += share_a * share_b
    if expected >= 1.0:
        return 1.0 if observed == 1.0 else None
    return round((observed - expected) / (1 - expected), 6)


def score(rows: list, method: str, adjudicator: str, lexicon: str) -> dict:
    positives = [row for row in rows if row.get("stratum") == "P"]
    negatives = [row for row in rows if row.get("stratum") == "N"]
    a_positive = [row for row in positives if _label(row.get("annotator_a", "")) == "positive"]
    n_false_negative = [row for row in negatives
                        if _label(row.get("annotator_a", "")) == "positive"]
    pairs = [(label_a, label_b) for row in rows
             if (label_a := _label(row.get("annotator_a", "")))
             and (label_b := _label(row.get("annotator_b", "")))]
    low, high = wilson_interval(len(a_positive), len(positives))
    fn_low, fn_high = wilson_interval(len(n_false_negative), len(negatives))
    kappa = cohen_kappa(pairs)
    report = {
        "lexicon": lexicon,
        "method": method,
        "adjudicator": adjudicator,
        "strata": {
            "P": {"sampled": len(positives), "marked_positive": len(a_positive),
                  "precision": (round(len(a_positive) / len(positives), 6) if positives else None),
                  "precision_ci95": [low, high]},
            "N": {"sampled": len(negatives), "marked_positive": len(n_false_negative),
                  "false_negative_rate": (round(len(n_false_negative) / len(negatives), 6)
                                          if negatives else None),
                  "false_negative_ci95": [fn_low, fn_high]},
        },
        "n_rows": len(rows),
        "n_double_labelled": len(pairs),
        "cohen_kappa": kappa,
        "acceptance": {"kappa_min": 0.6,
                       "kappa_ok": (kappa is not None and kappa >= 0.6
                                    if len(pairs) >= 200 else None)},
        "caveats": [],
    }
    if method != "human":
        report["caveats"].append(
            "adjudicator method=%s: this is a pre-annotation, not a gold standard; "
            "kappa is only meaningful for two independent human passes" % method)
        report["acceptance"]["kappa_ok"] = None
    if len(pairs) < 200:
        report["caveats"].append(
            "only %d double-labelled rows; the admission checklist asks for >=200" % len(pairs))
    return report


def mine(corpus: Path, bundles: dict, min_count: int, top: int) -> list:
    """Frequent CJK n-grams (2+ chars) that are in NO lexicon, as candidates."""
    import profile_papers as pp

    bundle = bundles["zh"]
    known = set()
    for lexicon in ("hedge", "booster", "academic_words"):
        known.update(getattr(bundle, lexicon).entries)
    for group in CONNECTOR_GROUPS:
        known.update(bundle.connectors[group].entries)

    # Chinese is not word-segmented, so candidates are contiguous CJK n-grams
    # (2..4 characters) rather than "tokens": a hedge/connective candidate is a
    # short character run, and the human reviewer sees the exact string.
    stop_chars = set("的了和与及或在是有对为以被由从到中上下等这那其该各就也都"
                     "很更最将已则即使得地着过之所")
    counts: dict = {}
    for paper_dir in sorted(corpus.iterdir()):
        content_lists = sorted(paper_dir.glob("mineru/**/*content_list.json"))
        if not content_lists:
            continue
        paper = pp.Paper(name=paper_dir.name, content_list_path=content_lists[0],
                         paper_key=paper_dir.name)
        text = paper.canonical_text()
        if tm.detect_language(text)["language"] != "zh":
            continue
        for run in CJK_RUN_RE.finditer(text):
            sig = run.group(0)
            for size in (2, 3, 4):
                for start in range(0, len(sig) - size + 1):
                    ngram = sig[start:start + size]
                    if ngram in known:
                        continue
                    # A candidate that begins or ends with a function character is
                    # almost always a fragment of a longer phrase, not a term.
                    if ngram[0] in stop_chars or ngram[-1] in stop_chars:
                        continue
                    counts[ngram] = counts.get(ngram, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"ngram": ngram, "count": count} for ngram, count in ranked[:top]
            if count >= min_count]


def audit(corpus: Path, bundles: dict, lexicon: str) -> dict:
    """Per-entry usage of one lexicon across a corpus (deterministic).

    A curated entry that never fires on real papers is dead weight: it inflates
    the list and makes the release look richer than the evidence supports.  This
    report names the backbone entries, the never-fired ones, and the sentences
    behind the top entries, so pruning or keeping is a reviewed decision rather
    than a guess.
    """
    import profile_papers as pp

    bundle = bundles["zh"]
    table = entries_for(bundle, lexicon)
    hits: dict = {entry: 0 for entries in table.values() for entry in entries}
    example: dict = {}
    n_sentences = 0
    doc_hits: dict = {entry: 0 for entry in hits}
    for paper_dir in sorted(corpus.iterdir()):
        content_lists = sorted(paper_dir.glob("mineru/**/*content_list.json"))
        if not content_lists:
            continue
        paper = pp.Paper(name=paper_dir.name, content_list_path=content_lists[0],
                         paper_key=paper_dir.name)
        text = paper.canonical_text()
        if tm.detect_language(text)["language"] != "zh":
            continue
        seen_here: set = set()
        for span in tm.split_sentences(text):
            n_sentences += 1
            for entry, entries in table.items():
                matched = tm._match_cjk_entries([span], entries)
                if not matched:
                    continue
                for start, end, _ in matched:
                    text_hit = span.text[start - span.start:end - span.start]
                    if text_hit in hits:
                        hits[text_hit] += 1
                        seen_here.add(text_hit)
                        if text_hit not in example:
                            example[text_hit] = span.text[:80].replace("\t", " ")
        for entry in seen_here:
            doc_hits[entry] += 1
    never = sorted(entry for entry, count in hits.items() if count == 0)
    ranked = sorted(((entry, count) for entry, count in hits.items() if count),
                    key=lambda item: (-item[1], item[0]))
    return {
        "lexicon": lexicon,
        "lexicon_version": bundle.version,
        "lexicon_fingerprint": lexicon_fingerprint(bundle, lexicon),
        "n_sentences": n_sentences,
        "n_entries": len(hits),
        "n_entries_fired": len(hits) - len(never),
        "n_entries_never_fired": len(never),
        "never_fired": never,
        "top_entries": [{"entry": entry, "hits": count, "papers": doc_hits[entry]}
                        for entry, count in ranked[:20]],
        "examples": {entry: example.get(entry, "") for entry, _ in ranked[:10]},
    }


def validate(corpus: Path, bundles: dict, lexicon: str,
             leave_out_pct: int = 20, subsets: int = 5) -> dict:
    """Zero-annotation validity evidence for one lexicon.

    Three checks that need no human labels, because for a *relative* use
    (same lexicon on the corpus and on the draft) the question is not "is every
    entry right" but "is the number stable and does it discriminate":

    1. concentration - the share of all hits that comes from the busiest 20% of
       entries. A lexicon whose number is dominated by one or two entries is
       degenerate: the metric stops being about the category.
    2. sensitivity   - drop a stride-selected `leave_out_pct` of the entries,
       recompute the corpus mean, repeat `subsets` times. A small spread means
       the number does not hinge on a handful of word choices.
    3. discrimination - the same entries measured on introduction / method /
       experiments sections. If a genre-sensitive category (hedging, boosting)
       shows no separation at all, the list is not measuring that category.
    """
    import profile_papers as pp

    bundle = bundles["zh"]
    table = entries_for(bundle, lexicon)
    flat = [entry for entries in table.values() for entry in entries]
    scale = 1000.0 if lexicon == "connectors" else 1.0

    hits_by_entry: dict = {entry: 0 for entry in flat}
    papers: list = []
    for paper_dir in sorted(corpus.iterdir()):
        content_lists = sorted(paper_dir.glob("mineru/**/*content_list.json"))
        if not content_lists:
            continue
        paper = pp.Paper(name=paper_dir.name, content_list_path=content_lists[0],
                         paper_key=paper_dir.name)
        text = paper.canonical_text()
        if tm.detect_language(text)["language"] != "zh":
            continue
        papers.append((paper_dir.name, text, paper.section_texts()))
        for span in tm.split_sentences(text):
            for start, end, _ in tm._match_cjk_entries([span], flat):
                token = span.text[start - span.start:end - span.start]
                if token in hits_by_entry:
                    hits_by_entry[token] += 1

    def ratio(entries: list, text: str):
        units = tm._zh_cjk_unit_count(text)
        if not units:
            return None
        hits = len(tm._match_cjk_entries(tm.split_sentences(text), entries))
        return hits / units * scale

    baseline = [value for _, text, _ in papers if (value := ratio(flat, text)) is not None]
    baseline_mean = statistic_mean(baseline)

    # 1) concentration
    ranked = sorted(hits_by_entry.items(), key=lambda item: (-item[1], item[0]))
    total_hits = sum(count for _, count in ranked)
    top_slice = max(1, int(round(len(ranked) * 0.2)))
    top_hits = sum(count for _, count in ranked[:top_slice])

    # 2) sensitivity: stride-selected leave-out subsets, no RNG
    spread: list = []
    for subset_index in range(subsets):
        kept = [entry for index, entry in enumerate(sorted(flat))
                if (index + subset_index) % max(2, int(round(100 / leave_out_pct))) != 0]
        values = [value for _, text, _ in papers if (value := ratio(kept, text)) is not None]
        if values:
            spread.append(statistic_mean(values))

    # 3) discrimination across the stratified sections of the same papers
    sections: dict = {}
    for _, _, section_texts in papers:
        for name in ("introduction", "method", "experiments"):
            text = section_texts.get(name)
            if not text:
                continue
            value = ratio(flat, text)
            if value is not None:
                sections.setdefault(name, []).append(value)
    section_means = {name: statistic_mean(values) for name, values in sections.items()}
    ordered = sorted(section_means.items(), key=lambda item: item[1])
    return {
        "lexicon": lexicon,
        "lexicon_version": bundle.version,
        "n_entries": len(flat),
        "n_papers": len(papers),
        "corpus_mean": baseline_mean,
        "concentration": {
            "top20pct_entries": top_slice,
            "top20pct_hit_share": (round(top_hits / total_hits, 6) if total_hits else None),
            "strongest": [{"entry": entry, "hits": count} for entry, count in ranked[:5]],
        },
        "sensitivity": {
            "leave_out_pct": leave_out_pct,
            "subsets": len(spread),
            "means": [round(value, 8) for value in spread],
            "relative_spread": (round((max(spread) - min(spread)) / baseline_mean, 6)
                                if spread and baseline_mean else None),
        },
        "discrimination": {
            "section_means": {name: round(value, 8) for name, value in sorted(section_means.items())},
            "min_section": ordered[0][0] if ordered else None,
            "max_section": ordered[-1][0] if ordered else None,
            "max_over_min": (round(ordered[-1][1] / ordered[0][1], 4)
                             if ordered and ordered[0][1] else None),
        },
    }


def statistic_mean(values: list):
    if not values:
        return None
    return sum(values) / len(values)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Chinese lexicon calibration harness")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="draw annotation sheets")
    sample.add_argument("--corpus", required=True, type=Path)
    sample.add_argument("--out", required=True, type=Path)
    sample.add_argument("--lexicon", default="hedge", choices=CALIBRATABLE)
    sample.add_argument("--positives", type=int, default=120)
    sample.add_argument("--negatives", type=int, default=80)

    score_cmd = sub.add_parser("score", help="score filled sheets")
    score_cmd.add_argument("--sheets", required=True, type=Path)
    score_cmd.add_argument("--out", required=True, type=Path)
    score_cmd.add_argument("--lexicon", default="hedge")
    score_cmd.add_argument("--method", default="human", choices=("human", "model"))
    score_cmd.add_argument("--adjudicator", default="")

    audit_cmd = sub.add_parser("audit", help="per-entry usage across a corpus")
    audit_cmd.add_argument("--corpus", required=True, type=Path)
    audit_cmd.add_argument("--out", required=True, type=Path)
    audit_cmd.add_argument("--lexicon", default="hedge", choices=CALIBRATABLE)

    validate_cmd = sub.add_parser("validate", help="zero-annotation validity evidence")
    validate_cmd.add_argument("--corpus", required=True, type=Path)
    validate_cmd.add_argument("--out", required=True, type=Path)
    validate_cmd.add_argument("--lexicon", default="hedge", choices=CALIBRATABLE)
    validate_cmd.add_argument("--leave-out-pct", type=int, default=20, dest="leave_out_pct")
    validate_cmd.add_argument("--subsets", type=int, default=5)

    mine_cmd = sub.add_parser("mine", help="list frequent n-grams missing from every lexicon")
    mine_cmd.add_argument("--corpus", required=True, type=Path)
    mine_cmd.add_argument("--out", required=True, type=Path)
    mine_cmd.add_argument("--min-count", type=int, default=20, dest="min_count")
    mine_cmd.add_argument("--top", type=int, default=200)

    args = parser.parse_args(argv)
    import lexicon_loader as ll

    bundles = {language: ll.load_lexicons(language=language)
               for language in ll.SUPPORTED_LEXICON_LANGUAGES}

    if args.command == "sample":
        args.out.mkdir(parents=True, exist_ok=True)
        positives, negatives, table = collect(args.corpus, args.lexicon, bundles)
        positives_picked = systematic_sample(positives, args.positives)
        negatives_picked = systematic_sample(negatives, args.negatives)
        for index, item in enumerate(positives_picked):
            item.item_id = "P%03d" % (index + 1)
            item.stratum = "P"
        for index, item in enumerate(negatives_picked):
            item.item_id = "N%03d" % (index + 1)
            item.stratum = "N"
        sheet = write_sheets(positives_picked + negatives_picked, args.lexicon, args.out)
        meta = {
            "lexicon": args.lexicon,
            "corpus": str(args.corpus),
            "lexicon_fingerprint": lexicon_fingerprint(bundles["zh"], args.lexicon),
            "lexicon_version": bundles["zh"].version,
            "stratum_pool": {"P": len(positives), "N": len(negatives)},
            "stratum_sampled": {"P": len(positives_picked), "N": len(negatives_picked)},
            "sheet": sheet.name,
            "instructions": ("fill annotator_a (and annotator_b for an independent "
                             "second pass) with y/n per row, then run 'score'"),
        }
        (args.out / ("_calibration_%s_meta.json" % args.lexicon)).write_text(
            json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        print("[calibration] sheet: %s" % sheet)
        print("[calibration] pool P=%d N=%d; sampled P=%d N=%d"
              % (len(positives), len(negatives), len(positives_picked), len(negatives_picked)))
        print("[calibration] lexicon fingerprint: %s" % meta["lexicon_fingerprint"][:16])
        return 0

    if args.command == "score":
        args.out.mkdir(parents=True, exist_ok=True)
        rows = read_sheets(args.sheets)
        report = score(rows, args.method, args.adjudicator, args.lexicon)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        (args.out / ("_calibration_%s_report.json" % args.lexicon)).write_text(
            payload, encoding="utf-8")
        print(payload)
        return 0

    if args.command == "audit":
        args.out.mkdir(parents=True, exist_ok=True)
        report = audit(args.corpus, bundles, args.lexicon)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        (args.out / ("_audit_%s.json" % args.lexicon)).write_text(payload, encoding="utf-8")
        print("[audit] %s: %d entries, %d fired, %d never fired, over %d sentences"
              % (args.lexicon, report["n_entries"], report["n_entries_fired"],
                 report["n_entries_never_fired"], report["n_sentences"]))
        print("[audit] top: " + ", ".join(
            "%s(%d)" % (row["entry"], row["hits"]) for row in report["top_entries"][:8]))
        print("[audit] never fired: " + ", ".join(report["never_fired"][:15]))
        return 0

    if args.command == "validate":
        args.out.mkdir(parents=True, exist_ok=True)
        report = validate(args.corpus, bundles, args.lexicon,
                          args.leave_out_pct, args.subsets)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        (args.out / ("_validity_%s.json" % args.lexicon)).write_text(payload, encoding="utf-8")
        print("[validate] %s: corpus_mean=%s over %d papers, %d entries"
              % (args.lexicon, report["corpus_mean"], report["n_papers"], report["n_entries"]))
        print("[validate] concentration: top20%% entries hold %s of hits; strongest=%s"
              % (report["concentration"]["top20pct_hit_share"],
                 ", ".join("%s(%d)" % (row["entry"], row["hits"])
                           for row in report["concentration"]["strongest"])))
        print("[validate] sensitivity: leave-out %d%% -> relative spread %s"
              % (args.leave_out_pct, report["sensitivity"]["relative_spread"]))
        print("[validate] discrimination: %s (max/min=%s)"
              % (report["discrimination"]["section_means"],
                 report["discrimination"]["max_over_min"]))
        return 0

    if args.command == "mine":
        args.out.mkdir(parents=True, exist_ok=True)
        ranked = mine(args.corpus, bundles, args.min_count, args.top)
        path = args.out / "_candidate_ngrams.tsv"
        path.write_text("ngram\tcount\n" + "".join(
            "%s\t%d\n" % (row["ngram"], row["count"]) for row in ranked), encoding="utf-8")
        print("[calibration] candidates: %s (%d rows)" % (path, len(ranked)))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
