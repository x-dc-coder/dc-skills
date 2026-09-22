#!/usr/bin/env python3
"""Tests for text_metrics.py - the rule-based text metrics layer (I4/I5).

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_text_metrics.py -v

Coverage: rule-based sentence splitting (abbreviations / decimals / numbering /
initials must not mis-split), tokenization, MTLD (parameters, short-text
degeneration, bidirectional symmetry), the 13 metric ids and their contract
fields, the M-CONN-30 == sum(components) identity, longest-match / non-overlap
phrase matching, evidence spans that slice back to the original text, the
nominalization denylist, the passive irregular table, tense rules, empty-input
degeneration (value None, never NaN), determinism, plus two environment-
dependent cases: the real lexicon bundle (skipped when lexicon_loader or the
v1 data files are absent) and a real VRP corpus excerpt (skipped when missing).

This module must keep working without lexicon_loader: the only bundle used by
the unit tests is the local FakeBundle below.
"""
from __future__ import annotations

import json
from collections import Counter
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import text_metrics as tm  # noqa: E402

VRP_CORPUS = Path("/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis")

REQUIRED_FIELDS = {
    "value", "n", "denominator", "unit", "state", "method", "metric_spec",
    "evidence", "warnings",
}


# ---------------------------------------------------------------------------
# Minimal fake bundle (duck-typed: only .entries tuples are needed)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FakeLexicon:
    entries: tuple


class FakeBundle:
    """Minimal stand-in for lexicon_loader.LexiconBundle."""

    def __init__(self, *, hedge=("may", "might", "could", "possibly", "it is likely that"),
                 booster=("clearly", "obviously", "must", "demonstrate"),
                 contrastive=("however", "in contrast", "whereas"),
                 causal=("because", "due to"),
                 result=("therefore", "as a result", "consequently"),
                 temporal=("meanwhile", "then", "after that", "subsequently"),
                 condition=("if", "unless", "provided that", "only if"),
                 suffixes=("tion", "sion", "ment", "ness", "ity", "ance", "ence"),
                 verb_bases=("create", "measure", "develop", "apply", "decide", "compute",
                             "inform", "specify", "achieve", "perform", "propose",
                             "demonstrate", "collect", "build", "use", "test", "report"),
                 denylist=("section", "station", "mention", "question", "function"),
                 academic=("method", "system", "result", "analysis", "approach"),
                 stopwords=("the", "of", "and", "in", "to")):
        self.hedge = FakeLexicon(tuple(hedge))
        self.booster = FakeLexicon(tuple(booster))
        self.connectors = {
            "contrastive": FakeLexicon(tuple(contrastive)),
            "causal": FakeLexicon(tuple(causal)),
            "result": FakeLexicon(tuple(result)),
            "temporal": FakeLexicon(tuple(temporal)),
            "condition": FakeLexicon(tuple(condition)),
        }
        self.nominalization_suffixes = FakeLexicon(tuple(suffixes))
        self.nominalization_verb_bases = FakeLexicon(tuple(verb_bases))
        self.nominalization_denylist = FakeLexicon(tuple(denylist))
        self.academic_words = FakeLexicon(tuple(academic))
        self.stopwords = FakeLexicon(tuple(stopwords))


BUNDLE = FakeBundle()

SAMPLE_TEXT = (
    "We propose a method that may improve the system. "
    "However, the results indicate that the model was built on a limited dataset, "
    "and it is likely that the creation of new features is affected by the computation cost. "
    "Therefore, we must measure the improvement carefully. "
    "The system clearly demonstrates the application of the approach."
)


def _all_spans_slice_back(text, metrics):
    problems = []
    for metric_id, record in metrics.items():
        for item in record["evidence"]["sample"]:
            start, end = item["span"]
            if not (0 <= start < end <= len(text)):
                problems.append((metric_id, "span out of range", item["span"]))
                continue
            if text[start:end][:80] != item["excerpt"]:
                problems.append((metric_id, "excerpt mismatch", item["span"]))
            if len(item["excerpt"]) > 80:
                problems.append((metric_id, "excerpt too long", len(item["excerpt"])))
    return problems


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------

def test_tokenize_lowercases_and_keeps_inner_punctuation():
    assert tm.tokenize("State-of-the-art approach's results, 2024.") == [
        "state-of-the-art", "approach's", "results",
    ]
    assert tm.tokenize("") == []
    assert tm.tokenize("1234 --- []") == []


def test_tokenize_matches_frozen_regex():
    # the frozen regex cuts "A1b" into "a" + "b" and "C_d" into "c" + "d"
    assert tm.tokenize("A1b C_d e-f") == ["a", "b", "c", "d", "e-f"]


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------

def test_split_sentences_basic_and_spans_slice_back():
    text = "First one here. Second one! Third one?"
    spans = tm.split_sentences(text)
    assert [s.text for s in spans] == ["First one here.", "Second one!", "Third one?"]
    for span in spans:
        assert text[span.start:span.end] == span.text
        assert span.text == span.text.strip()


def test_split_sentences_does_not_split_abbreviations():
    text = ("See Smith et al. 2020 and Fig. 3 and Eq. 2, i.e. the same idea, "
            "vs. the other one, cf. Sec. 4 and Ref. [12], approx. 20 values, no. 5. Done.")
    spans = tm.split_sentences(text)
    assert len(spans) == 2
    assert spans[-1].text == "Done."


def test_split_sentences_does_not_split_decimals():
    text = "The value is 3.14 and the ratio is 0.5 exactly. Next sentence."
    assert [s.text for s in tm.split_sentences(text)] == [
        "The value is 3.14 and the ratio is 0.5 exactly.", "Next sentence.",
    ]


def test_split_sentences_protects_line_leading_numbering():
    text = "1. Introduction\n2. Method\nWe do this."
    assert len(tm.split_sentences(text)) == 1
    bracketed = "[12]. Related work follows here."
    assert len(tm.split_sentences(bracketed)) == 1


def test_split_sentences_single_letter_initials_chain():
    assert len(tm.split_sentences("J. R. Smith proposed it. We agree.")) == 2
    assert len(tm.split_sentences("A. Method\nWe do this.")) == 1
    # F3 regression: the initial rule keys on the RIGHT side only, so a lowercase
    # introducer ("by", "and") no longer breaks the initial apart.
    assert len(tm.split_sentences(
        "The method was proposed by J. Smith. Later, work by R. Kumar improved it.")) == 2
    assert [s.text for s in tm.split_sentences("A. Smith, B. Jones and C. Lee. Done.")] == [
        "A. Smith, B. Jones and C. Lee.", "Done.",
    ]
    # documented, accepted cost of the frozen right-side rule: a genuine
    # one-letter symbol at a sentence end followed by a capitalised word merges
    assert len(tm.split_sentences("We denote the variable X. We then compute it.")) == 1


def test_split_sentences_single_letter_is_not_over_protected():
    # digits and brackets after a one-letter symbol are real boundaries
    assert len(tm.split_sentences("The variable is X. 25 runs were executed.")) == 2
    assert len(tm.split_sentences("The variable is X. [12] reports it.")) == 2
    # a genuine sentence end after a lowercase word still splits
    assert len(tm.split_sentences("The method works well. The cost is low.")) == 2
    assert len(tm.split_sentences("Results were obtained. they were reproducible.")) == 1  # lowercase follower heuristic
    # the lowercase-follower merge comes from the frozen unknown-abbreviation
    # heuristic, not from the initial rule
    assert len(tm.split_sentences("The limit is Y. however, we proceed.")) == 1


def test_split_sentences_newline_then_digit_or_bracket_still_splits():
    """Regression: the abbreviation probe must not cross a line break.

    Scientific prose frequently starts a sentence with a number, "[12]" or a
    formula; probing past it used to merge two sentences and deflate the
    M-SLEN-01 / M-LSF-16 / M-PAS-09 denominators.
    """
    nl = "\n"
    assert len(tm.split_sentences("Model was trained." + nl + "10 experiments were performed.")) == 2
    assert len(tm.split_sentences("Results follow." + nl + "[12] showed gains.")) == 2
    assert len(tm.split_sentences("We use 3 layers. 4 layers are enough.")) == 2


def test_split_sentences_newline_then_formula_or_lowercase_still_splits():
    nl = "\n"
    # inline/block formula right after the stop
    assert len(tm.split_sentences("The cost is low." + nl + "L = 1/N is the loss.")) == 2
    assert len(tm.split_sentences("The bound holds." + nl + "[a, b] is convex.")) == 2
    # lowercase continuation on the next line is still a new sentence
    assert len(tm.split_sentences("This holds." + nl + "where N is the count.")) == 2


def test_split_sentences_see_fig_three_splits():
    assert [s.text for s in tm.split_sentences("See Fig. 3 for details. The cost is low.")] == [
        "See Fig. 3 for details.", "The cost is low.",
    ]


def test_evidence_rule_is_frozen_on_every_metric():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    for metric_id, record in metrics.items():
        rule = record["evidence_rule"]
        assert "order=document" in rule and "sample=first_5" in rule, metric_id
        assert "excerpt=text[start:end][:80]" in rule, metric_id
    assert "longest_nonoverlapping" in metrics["M-HED-14"]["evidence_rule"]
    assert "words>=40" in metrics["M-LSF-16"]["evidence_rule"]
    assert "passive_phrase_span" in metrics["M-PAS-09"]["evidence_rule"]


def test_split_sentences_empty_and_letterless_input():
    assert tm.split_sentences("") == []
    assert tm.split_sentences("   ") == []
    assert tm.split_sentences("--- 12. [3] (4)") == []


# --- Chinese (zh) sentence splitting: issue #13 --------------------------------

def test_cjk_sentence_terminators_split():
    text = "本文研究车辆路径问题。第二句在这里。第三句！"
    assert [s.text for s in tm.split_sentences(text)] == [
        "本文研究车辆路径问题。", "第二句在这里。", "第三句！"]


def test_cjk_terminator_run_ends_once():
    """…… and ！！ are one terminator run, not one sentence per character."""
    assert [s.text for s in tm.split_sentences("这是一句……然后继续。")] == [
        "这是一句……", "然后继续。"]
    assert len(tm.split_sentences("等等！！后面还有。")) == 2


def test_cjk_closing_quote_stays_inside_the_sentence():
    assert [s.text for s in tm.split_sentences("他说：「好。」然后离开。")] == [
        "他说：「好。」", "然后离开。"]


def test_cjk_semicolon_is_not_a_boundary():
    """Chinese uses ； inside a sentence; splitting on it would fragment prose."""
    assert len(tm.split_sentences("第一点是这个；第二点是那个。")) == 1


def test_cjk_sentences_are_not_dropped_as_letterless_fragments():
    """Regression: the ASCII-only fragment filter used to drop every CJK sentence."""
    spans = tm.split_sentences("本文提出了一种方法。")
    assert len(spans) == 1 and spans[0].text == "本文提出了一种方法。"


def test_mixed_zh_en_splits_on_both_terminator_sets():
    text = "本文提出了一个方法。We propose a method. 结果如下。"
    assert [s.text for s in tm.split_sentences(text)] == [
        "本文提出了一个方法。", "We propose a method.", "结果如下。"]


def test_zh_unit_count_covers_mixed_sentences():
    """A mixed sentence must not be under-counted by ignoring its ASCII terms."""
    text = "采用 K-means 算法求解"
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    assert cjk == 6
    # one ASCII token (the hyphen keeps "K-means" together), so 6 + 1 = 7
    assert tm._zh_cjk_unit_count(text) == cjk + 1 == 7


def test_chinese_abstract_noun_rule():
    """M-NOM-10 for Chinese counts abstract-noun suffixes, not verb derivations.

    Chinese converts verbs to nouns by zero derivation, so the English
    suffix+verb-base rule cannot apply; the recorded variant makes clear that the
    two numbers are different statistics.
    """
    spans = tm.split_sentences("该方法的稳定性和求解效率都很重要。")
    hits = tm._cjk_abstract_noun_hits(spans)
    excerpts = sorted(spans[0].text[start - spans[0].start:end - spans[0].start]
                      for start, end, _ in hits)
    # the span is a window ending at the suffix, so a reviewer sees the word
    assert any(text.endswith("稳定性") for text in excerpts)
    assert any(text.endswith("效率") for text in excerpts)
    # 化 must never count: 优化 / 转化 / 深化 are verbs, not nominalisations
    assert tm._cjk_abstract_noun_hits(tm.split_sentences("我们优化了模型并转化了目标函数。")) == []
    # a bare suffix with too short a prefix is not an abstract noun
    assert tm._cjk_abstract_noun_hits(tm.split_sentences("性 度 率")) == []
    # each hit carries the preceding window so a reviewer sees a real word
    assert all(end - start >= 2 for start, end, _ in hits)


def test_chinese_metric_weights_are_declared():
    assert tm._LONG_SENTENCE_CJK_UNITS == 80
    assert tm._UNIT_CJK_UNITS_PER_SENTENCE == "cjk-units/sentence"
    assert "zh" in tm.SUPPORTED_LANGUAGES


# --- anchor regressions for the keywords / inline-math defect -----------------
# The three patterns below are the ones the issue reproduces on the real corpus
# (Okulewicz keywords line, Chen inline formula, TRACE-VNS formula span).  A
# keywords line or a bare formula must never enter a sentence denominator.

def test_split_sentences_drops_keywords_line_anchor():
    text = ("This paper studies routing.\n"
            "Keywords: Dynamic Vehicle Routing Problem,Particle Swarm Optimization,Hyperheuristic\n"
            "We propose a method.")
    spans = tm.split_sentences(text)
    assert [s.text for s in spans] == ["This paper studies routing.", "We propose a method."]
    assert not any(s.text.lower().startswith("keyword") for s in spans)
    # a keywords line on its own is metadata, not a one-word "sentence"
    assert tm.split_sentences("Keywords: a, b, c") == []
    assert tm.split_sentences("KEY WORDS: routing, scheduling") == []
    assert tm.split_sentences("关键词：车辆路径；粒子群") == []


def test_split_sentences_inline_math_anchor_does_not_become_a_sentence():
    # a formula fragment on its own carries no prose and must be dropped
    assert tm.split_sentences("$c: E \\to \\mathbb{R}$") == []
    assert tm.split_sentences("$x = 1$.") == []
    # a full stop *inside* math must not end a sentence
    text = "The cost is $c_{1} = 0.5$. The second case follows."
    assert [s.text for s in tm.split_sentences(text)] == [
        "The cost is $c_{1} = 0.5$.", "The second case follows.",
    ]
    # display math keeps its surrounding prose split
    nl = "\n"
    assert [s.text for s in tm.split_sentences("Before." + nl + "$$\\sum_{i} x_{i} = 0.$$" + nl + "After it.")] == [
        "Before.", "After it.",
    ]


def test_split_sentences_sub_sup_markup_is_noise():
    text = "Water is H<sub>2</sub>O and x<sup>2</sup> is small. Next one."
    spans = tm.split_sentences(text)
    assert [s.text for s in spans] == ["Water is H<sub>2</sub>O and x<sup>2</sup> is small.", "Next one."]
    assert tm.split_sentences("<sub>2</sub>") == []


def test_split_sentences_span_contract_holds_after_normalisation():
    text = ("Prose before the keywords line." + "\n"
            "Keywords: a, b" + "\n"
            "Prose with $x$ math. And a second one.")
    spans = tm.split_sentences(text)
    assert len(spans) == 3
    for span in spans:
        assert text[span.start:span.end] == span.text
        assert span.text == span.text.strip()
        assert "$" not in span.text or span.text.endswith(".")


# ---------------------------------------------------------------------------
# MTLD
# ---------------------------------------------------------------------------

def test_mtld_short_text_returns_nan():
    assert math.isnan(tm.mtld(["a"] * (2 * tm._MTLD_MIN_FACTOR - 1)))
    assert not math.isnan(tm.mtld(["a"] * (2 * tm._MTLD_MIN_FACTOR)))
    assert math.isnan(tm.mtld([]))


def test_mtld_is_bidirectional_symmetric():
    tokens = ("the quick brown fox jumps over the lazy dog").split() * 3
    assert tm.mtld(tokens) == tm.mtld(list(reversed(tokens)))
    assert tm.mtld(tokens) > 0


def test_mtld_pinned_parameters_are_frozen():
    assert tm._MTLD_TTR_THRESHOLD == 0.720
    assert tm._MTLD_MIN_FACTOR == 10
    assert tm._LONG_SENTENCE_WORDS == 40
    # 1.1: pre-split non-prose masking changed the sentence denominators
    # (issues #2 / #3) -> the version must move with the numbers.
    assert tm.TEXT_METRICS_VERSION == "1.6"


# ---------------------------------------------------------------------------
# compute_text_metrics: contract shape
# ---------------------------------------------------------------------------

def test_every_metric_id_is_present_with_contract_fields():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    assert set(metrics) == set(tm.METRIC_IDS)
    assert len(tm.METRIC_IDS) == 27  # 13 frozen + 4 clause (#22) + 3 stance + 2 PDTB + 3 sentence-pattern + 2 terminology (#23)
    for metric_id, record in metrics.items():
        assert REQUIRED_FIELDS <= set(record), metric_id
        assert record["metric_spec"] == metric_id
        assert record["state"] == "OBSERVED"
        assert record["method"] == "rule"
        assert isinstance(record["n"], int) and isinstance(record["denominator"], int)
        assert isinstance(record["unit"], str) and record["unit"]
        assert len(record["evidence"]["sample"]) <= 5
        assert record["evidence"]["count"] >= 0
        assert isinstance(record["warnings"], list)


def test_evidence_spans_slice_back_to_the_original_text():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    assert _all_spans_slice_back(SAMPLE_TEXT, metrics) == []
    assert any(record["evidence"]["sample"] for record in metrics.values())


def test_evidence_sample_is_capped_at_five():
    text = ("may " * 30).strip() + " and this may happen."
    metrics = tm.compute_text_metrics(text, BUNDLE)
    hedges = metrics["M-HED-14"]
    assert hedges["n"] == 31
    assert len(hedges["evidence"]["sample"]) == 5


def test_json_output_never_contains_nan():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    blob = json.dumps(metrics, sort_keys=True)
    assert "NaN" not in blob
    short = tm.compute_text_metrics("Too short.", BUNDLE)
    assert short["M-MTLD-02"]["value"] is None
    assert "NaN" not in json.dumps(short, sort_keys=True)


def test_empty_text_degrades_to_none_with_warnings():
    metrics = tm.compute_text_metrics("", BUNDLE)
    assert metrics["M-SLEN-01"]["value"] is None
    assert metrics["M-SLEN-01"]["n"] == 0 and metrics["M-SLEN-01"]["denominator"] == 0
    assert metrics["M-LSF-16"]["value"] is None
    assert metrics["M-MTLD-02"]["value"] is None
    assert metrics["M-HED-14"]["value"] is None
    assert metrics["M-CONN-30"]["value"] is None
    assert metrics["M-PAS-09"]["value"] is None
    assert metrics["M-NOM-10"]["value"] is None
    assert metrics["M-TENSE-28"]["value"] is None
    for metric_id, record in metrics.items():
        assert record["warnings"], metric_id


def test_deterministic_across_two_runs():
    first = json.dumps(tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE), sort_keys=True, ensure_ascii=False)
    second = json.dumps(tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE), sort_keys=True, ensure_ascii=False)
    assert first == second


# ---------------------------------------------------------------------------
# Sentence-level metrics
# ---------------------------------------------------------------------------

def test_slen_mean_denominator_and_distribution():
    record = tm.compute_text_metrics("One two three. Four five.", BUNDLE)["M-SLEN-01"]
    assert record["value"] == pytest.approx(2.5)
    assert record["n"] == 2
    assert record["denominator"] == 5
    assert record["unit"] == "words/sentence"
    assert record["distribution"] == {"median": 2.5, "p25": 2.25, "p75": 2.75, "std": 0.5}


def test_lsf_threshold_is_forty_words():
    long_sentence = ("alpha beta gamma delta " * 10).strip()
    text = long_sentence + ". Short one."
    record = tm.compute_text_metrics(text, BUNDLE)["M-LSF-16"]
    assert record["value"] == pytest.approx(0.5)
    assert record["n"] == 1
    assert record["denominator"] == 2
    assert record["long_sentence_threshold"] == 40


# ---------------------------------------------------------------------------
# Lexicon-driven metrics
# ---------------------------------------------------------------------------

def test_denominator_is_the_alpha_token_count():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    expected = len(tm.tokenize(SAMPLE_TEXT))
    for metric_id in ("M-HED-14", "M-BOO-15", "M-AWR-03", "M-NOM-10",
                      "M-CONN-30", "M-CONN-30c", "M-CONN-30k", "M-CONN-30r"):
        assert metrics[metric_id]["denominator"] == expected, metric_id


def test_connector_identity_total_equals_component_sum():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    parts = [metrics["M-CONN-30c"], metrics["M-CONN-30k"], metrics["M-CONN-30r"]]
    total = metrics["M-CONN-30"]
    assert total["value"] == round(sum(part["value"] for part in parts), 6)
    assert abs(total["value"] - sum(part["value"] for part in parts)) < 1e-9
    assert total["n"] == sum(part["n"] for part in parts)
    assert total["unit"] == "per-1000-words"
    assert total["components"]["M-CONN-30c"]["n"] == metrics["M-CONN-30c"]["n"]


def test_connector_density_is_per_thousand_words():
    bundle = FakeBundle(contrastive=("however",), causal=(), result=())
    text = ("word " * 100).strip() + " however"
    record = tm.compute_text_metrics(text, bundle)["M-CONN-30c"]
    assert record["denominator"] == 101
    assert record["value"] == pytest.approx(1 / 101 * 1000)


def test_longest_match_wins_and_hits_do_not_overlap():
    bundle = FakeBundle(hedge=("contrast", "in contrast", "may", "may be"))
    text = "In contrast, results may be biased."
    record = tm.compute_text_metrics(text, bundle)["M-HED-14"]
    assert record["n"] == 2  # "in contrast" + "may be" (never overlapping short forms)
    excerpts = [item["excerpt"] for item in record["evidence"]["sample"]]
    assert excerpts == ["In contrast", "may be"]


def test_phrase_matching_does_not_cross_sentence_boundaries():
    bundle = FakeBundle(hedge=("may be",))
    text = "It may. Be careful."  # "may" + "be" are in different sentences
    assert tm.compute_text_metrics(text, bundle)["M-HED-14"]["n"] == 0
    assert tm.compute_text_metrics("It may be fine.", bundle)["M-HED-14"]["n"] == 1


def test_causal_ambiguous_bare_words_are_not_counted():
    """B3: bare "as"/"since"/"through"/"so"/"still" are not causal connectives."""
    bundle = FakeBundle(causal=("because", "due to", "owing to", "as a result of"),
                        contrastive=("however",), result=("therefore", "so that"))
    text = "We use the GPU as a coprocessor. As shown in Fig. 1, it works."
    assert tm.compute_text_metrics(text, bundle)["M-CONN-30k"]["n"] == 0
    unambiguous = ("The cost is high because of memory limits. "
                   "Due to the batch size, we split the work. "
                   "Owing to the scheduler, runs overlap.")
    record = tm.compute_text_metrics(unambiguous, bundle)["M-CONN-30k"]
    assert record["n"] == 3
    assert record["value"] > 0


def test_academic_word_ratio():
    bundle = FakeBundle(academic=("method", "system"))
    record = tm.compute_text_metrics("The method and the system work.", bundle)["M-AWR-03"]
    assert record["n"] == 2
    assert record["value"] == pytest.approx(2 / 6)


# ---------------------------------------------------------------------------
# Passive
# ---------------------------------------------------------------------------

def test_passive_uses_irregular_participle_table():
    text = ("The model was built by us. The data were collected carefully. "
            "The result was shown in Fig. 1.")
    record = tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]
    assert record["n"] == 3
    assert record["denominator"] == 3
    assert record["value"] == pytest.approx(1.0)
    assert record["n_unresolved"] == 0
    assert len(tm._IRREGULAR_PARTICIPLE) >= 100


def test_passive_copula_is_unresolved_not_passive():
    text = "The system is important. The result was limited. The model was validated."
    record = tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]
    assert record["n"] == 1  # "was validated" is the only real passive
    assert record["n_unresolved"] == 2  # "is important", "was limited"
    assert record["warnings"]


def test_passive_regular_ed_participle_and_adverbs():
    text = "The data were carefully collected and the model was finally tested."
    assert tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]["n"] == 1


def test_pas09_evidence_is_phrase_level_but_value_unchanged():
    text = ("The model was built by us. The data were carefully collected. "
            "The problem is complicated.")
    record = tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]
    # value / n / denominator keep the frozen sentence-level ratio semantics
    assert record["n"] == 2
    assert record["denominator"] == 3
    assert record["value"] == pytest.approx(2 / 3)
    assert record["evidence_target"] == "passive_phrase_span"
    assert [item["excerpt"] for item in record["evidence"]["sample"]] == [
        "was built", "were carefully collected",
    ]
    for item in record["evidence"]["sample"]:
        start, end = item["span"]
        assert text[start:end] == item["excerpt"]


def test_pas09_phrase_span_includes_perfect_and_modal_lead():
    hits, unresolved = tm.detect_passive_spans("The system has been adopted widely.")
    assert [hit.phrase for hit in hits] == ["has been adopted"]
    assert unresolved == 0
    hits, _ = tm.detect_passive_spans("This can be mapped onto a graph.")
    assert [hit.phrase for hit in hits] == ["can be mapped"]
    hits, _ = tm.detect_passive_spans("The data were carefully collected.")
    assert [hit.phrase for hit in hits] == ["were carefully collected"]
    assert hits[0].participle == "collected"
    assert hits[0].sentence.text == "The data were carefully collected."


def test_passive_copula_adjectives_are_not_passive():
    """B2: "is complicated" / "were tired" are copular (主系表), not passive."""
    text = "The problem is complicated. We were tired after the experiment."
    record = tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]
    assert record["n"] == 0
    assert record["value"] == 0.0
    assert record["denominator"] == 2
    assert record["n_unresolved"] == 2
    assert tm._PARTICIPIAL_ADJECTIVE_DENYLIST >= {
        "complicated", "tired", "interested", "excited", "involved",
        "related", "concerned", "limited", "based",
    }


def test_passive_real_constructions_survive_the_denylist():
    """The denylist must not erase genuine agentless passives."""
    # NB: avoid one-letter sentence-final symbols here, they are intentionally
    # merged by the frozen F3 right-side initial rule.
    text = ("The method was proposed by Smith. The results were obtained using a script. "
            "The model was trained on a GPU. The bound is known to be tight. "
            "The GPU is used to accelerate the search.")
    record = tm.compute_text_metrics(text, BUNDLE)["M-PAS-09"]
    assert record["n"] == 5
    assert record["denominator"] == 5
    assert record["value"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tense
# ---------------------------------------------------------------------------

def test_tense_present_past_split_and_unresolved():
    text = "The system achieves good results. It was tested on data. We can improve it."
    record = tm.compute_text_metrics(text, BUNDLE)["M-TENSE-28"]
    assert record["n"] == record["n_present"] >= 1
    assert record["n_past"] >= 1
    assert record["denominator"] == record["n_present"] + record["n_past"]
    assert record["value"] == pytest.approx(record["n_present"] / record["denominator"])
    assert record["n_unresolved"] == 1  # "can"


def test_tense_does_not_count_plural_nouns_as_present():
    # "model"/"result" are verb bases too, but their plural forms are the
    # dominant reading and are excluded by the frozen ambiguity gate.
    bundle = FakeBundle(verb_bases=("model", "result"))
    text = "The models and results matter here."
    record = tm.compute_text_metrics(text, bundle)["M-TENSE-28"]
    assert record["n_present"] == 0
    assert record["n_past"] == 0
    assert {"model", "result"} <= tm._AMBIGUOUS_NOUN_VERBS
    # sanity: without the ambiguity gate the same verb bases would be counted
    assert "models" in tm._build_third_person_index(("model", "result"))


# ---------------------------------------------------------------------------
# Nominalization
# ---------------------------------------------------------------------------

def test_nominalization_denylist_excludes_pseudo_nominalizations():
    bundle = FakeBundle(verb_bases=("create", "measure", "inform"),
                        denylist=("section", "station", "mention", "function"))
    text = "The section and the station and the mention of function follow the creation and information."
    record = tm.compute_text_metrics(text, bundle)["M-NOM-10"]
    hits = [item["excerpt"] for item in record["evidence"]["sample"]]
    assert record["n"] == 2
    assert hits == ["creation", "information"]


def test_nominalization_ation_ition_restoration():
    """-ation / -ition forms must be restored to their verb bases."""
    bundle = FakeBundle(verb_bases=("inform", "describe", "evaluate", "recognize"),
                        suffixes=("tion", "sion"), denylist=())
    text = "The information and the description support the evaluation and the recognition."
    record = tm.compute_text_metrics(text, bundle)["M-NOM-10"]
    assert record["n"] == 4
    assert [item["excerpt"] for item in record["evidence"]["sample"]] == [
        "information", "description", "evaluation", "recognition",
    ]


def test_nominalization_plural_form_is_normalised():
    bundle = FakeBundle(verb_bases=("apply",), suffixes=("tion",))
    assert tm.compute_text_metrics("applications matter.", bundle)["M-NOM-10"]["n"] == 1


def test_nominalization_empty_suffix_lexicon_warns_not_crashes():
    bundle = FakeBundle(suffixes=())
    record = tm.compute_text_metrics("The creation is here.", bundle)["M-NOM-10"]
    assert record["value"] == 0.0
    assert record["warnings"]


# ---------------------------------------------------------------------------
# Language detection and "unsupported means null, never 0" (Round A)
# ---------------------------------------------------------------------------

CHINESE_TEXT = (
    "本文提出了一种基于强化学习的车辆路径问题求解方法。"
    "我们在多个基准算例上验证了该方法的有效性，并与传统启发式算法进行了比较。"
    "实验结果表明，所提方法在求解质量与计算时间上均具有优势。"
)

#: Frozen pre-language-gate values for SAMPLE_TEXT + BUNDLE:
#: metric_id -> [value, n, denominator].  The language gate must not move any
#: English number by even one unit in the last place.
ENGLISH_GOLDEN = {
    "M-AWR-03": [0.074074, 4, 54],
    "M-BOO-15": [0.037037, 2, 54],
    "M-CONN-30": [37.037038, 2, 54],
    "M-CONN-30c": [18.518519, 1, 54],
    "M-CONN-30k": [0.0, 0, 54],
    "M-CONN-30r": [18.518519, 1, 54],
    "M-HED-14": [0.037037, 2, 54],
    "M-LSF-16": [0.0, 0, 4],
    "M-MTLD-02": [54.216, 54, 1],
    "M-NOM-10": [0.055556, 3, 54],
    "M-PAS-09": [0.25, 1, 4],
    "M-SLEN-01": [13.5, 4, 54],
    "M-TENSE-28": [0.428571, 3, 7],
    # Clause layer (issue #22) is zh-only: on English the records exist so the
    # key set stays symmetric, but they are null + CAPABILITY_NOT_SUPPORTED.
    "M-CLS-31": [None, 0, 0],
    "M-CLS-32": [None, 0, 0],
    "M-SLEN-34": [None, 0, 0],
    "M-SLEN-35": [None, 0, 0],
    # Stance layer (issue #23): INFERRED placeholders, null on every language
    # until the calibration set with kappa >= 0.6 is frozen.
    "M-STNC-41": [None, 0, 0],
    "M-STNC-42": [None, 0, 0],
    "M-STNC-43": [None, 0, 0],
    # PDTB temporal/condition (#23): zh-only, so English is suppressed.
    "M-CONN-30t": [None, 0, 0],
    "M-CONN-30q": [None, 0, 0],
    # Sentence-pattern layer (#23): zh-only structural counts.
    "M-SPAT-44": [None, 0, 0],
    "M-SPAT-45": [None, 0, 0],
    "M-SPAT-46": [None, 0, 0],
    # Terminology consistency (#23): zh-only, deterministic clustering.
    "M-TERM-47": [None, 0, 0],
    "M-TERM-48": [None, 0, 0],
}


def test_language_threshold_constant_is_frozen():
    assert tm.LANGUAGE_SUPPORT_CJK_THRESHOLD == 0.10
    assert tm.LANGUAGE_NOT_SUPPORTED == "LANGUAGE_NOT_SUPPORTED"


def test_detect_language_english():
    info = tm.detect_language(SAMPLE_TEXT)
    assert info["language"] == "en"
    assert info["supported"] is True
    assert info["cjk_chars"] == 0
    assert info["cjk_ratio"] == 0.0
    assert info["ascii_alpha_tokens"] == len(tm.tokenize(SAMPLE_TEXT))
    assert info["reason"] is None


def test_detect_language_chinese_is_unsupported_and_loud():
    info = tm.detect_language(CHINESE_TEXT)
    assert info["language"] == "zh"
    assert info["supported"] is False
    assert info["cjk_ratio"] > tm.LANGUAGE_SUPPORT_CJK_THRESHOLD
    assert info["cjk_chars"] > 0
    assert info["ascii_alpha_tokens"] == 0
    assert info["reason"] and "LANGUAGE_NOT_SUPPORTED" in info["reason"]


def test_detect_language_empty_and_non_string():
    empty = tm.detect_language("")
    assert empty == {
        "language": "unknown", "cjk_ratio": 0.0, "cjk_chars": 0,
        "ascii_alpha_tokens": 0, "supported": True, "reason": None,
    }
    # documented: an empty/letter-less text has ratio 0.0, so it is "supported"
    # (nothing is measured anywhere in it) but its language is "unknown"
    assert tm.detect_language(None)["language"] == "unknown"
    assert tm.detect_language(None)["supported"] is True


def test_chinese_measures_the_supported_subset_and_names_the_rest():
    """Chinese is a per-metric capability, not an all-or-nothing language gate.

    Issue #13: the metrics whose pinned rules need no lexicon (sentence length,
    long-sentence ratio) are measured; every other metric is null with
    CAPABILITY_NOT_SUPPORTED, which stays distinguishable from "measured 0".
    """
    metrics = tm.compute_text_metrics(CHINESE_TEXT, BUNDLE)
    assert set(metrics) == set(tm.METRIC_IDS)
    measured: list[str] = []
    for metric_id, record in metrics.items():
        languages = tm._METRIC_LANGUAGE_CAPABILITY.get(
            metric_id, tm._DEFAULT_METRIC_LANGUAGES)
        # OBSERVED rule metrics carry the measurement; the stance layer (#23)
        # is INFERRED and pending its calibration set, so it is exempt from the
        # state assertion but must never carry a value.
        if record["state"] != "INFERRED":
            assert record["state"] == "OBSERVED" and record["method"] == "rule", metric_id
        assert record["metric_spec"] == metric_id
        assert record["unit"], metric_id
        assert record["evidence_rule"], metric_id
        assert record["language"] == "zh"
        assert record["cjk_ratio"] > tm.LANGUAGE_SUPPORT_CJK_THRESHOLD
        if "zh" in languages:
            measured.append(metric_id)
            assert record["value"] is not None, metric_id
            # a measured rate can legitimately be 0 (no variant, no
            # connector): that is an honest zero, not an unmeasured metric,
            # and it carries no warning
            assert record["warnings"] == [], metric_id
            if record["value"] != 0:
                assert record["denominator"] > 0, metric_id
            else:
                # a zero value must still name its denominator so the reader
                # can tell "measured as zero" from "not measured"
                assert record["denominator"] >= 0, metric_id
        else:
            assert record["value"] is None, metric_id
            assert record["n"] == 0 and record["denominator"] == 0, metric_id
            # INFERRED-pending metrics name a different reason than the
            # language-capability ones, so both stay distinguishable downstream
            if record["state"] == "INFERRED":
                assert record["warnings"] == ["NOT_IMPLEMENTED"], metric_id
            else:
                assert record["warnings"] == ["CAPABILITY_NOT_SUPPORTED"], metric_id
                assert record["evidence"] == {"count": 0, "sample": []}, metric_id
    # 12 of the 14 metrics are measurable for Chinese; only the two that need an
    # annotation set (M-NOM-10) or do not exist in the language (M-TENSE-28,
    # Chinese has no tense) stay unsupported.
    # 13 of the 14 metrics are measurable for Chinese: only M-TENSE-28 stays
    # unmeasurable, because Chinese has no tense and a number there would be
    # fabricated. M-NOM-10 is measurable as the abstract-noun-suffix variant.
    # M-TENSE-28 is unmeasurable in Chinese (no tense); the stance layer
    # (#23) is INFERRED and stays null + NOT_IMPLEMENTED until its calibration
    # set lands, so it is excluded from the measured set by capability, not by
    # language.
    _STANCE = {"M-STNC-41", "M-STNC-42", "M-STNC-43"}
    assert set(measured) == set(tm.METRIC_IDS) - {"M-TENSE-28"}
    for mid in _STANCE:
        record = metrics[mid]
        assert record["state"] == "INFERRED", mid
        assert record["method"] == "model_lexicon_presence", mid
        assert record["value"] is not None and record["denominator"] > 0, mid
        # admission evidence travels with the record, never in a gate
        cal = record["calibration"]
        assert cal["annotator_agreement"]["value"] >= 0.6, mid
        assert cal["holdout_macro_f1"] > 0.0 and cal["holdout_accuracy"] > 0.0, mid
        assert record["annotation_set"]["status"] == "frozen", mid
        assert set(record["annotation_set"]["labels"]) == set(tm._STANCE_LABELS), mid
    assert metrics["M-NOM-10"]["variant"] == "cjk-abstract-noun-suffix"
    assert metrics["M-NOM-10"]["unit"] == tm._UNIT_PER_1000_CJK_UNITS
    assert "化" in metrics["M-NOM-10"]["excluded_suffixes"]
    assert metrics["M-SLEN-01"]["unit"] == tm._UNIT_CJK_UNITS_PER_SENTENCE
    assert metrics["M-LSF-16"]["long_sentence_threshold"] == tm._LONG_SENTENCE_CJK_UNITS
    assert metrics["M-CONN-30"]["unit"] == tm._UNIT_PER_1000_CJK_UNITS
    assert metrics["M-MTLD-02"]["params"]["tokenization"] == "cjk-char+ascii-token"
    assert "NaN" not in json.dumps(metrics, sort_keys=True)


def test_english_values_are_bit_identical_to_pre_language_gate():
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, BUNDLE)
    assert set(ENGLISH_GOLDEN) == set(tm.METRIC_IDS)
    for metric_id, (value, n, denominator) in ENGLISH_GOLDEN.items():
        record = metrics[metric_id]
        assert record["value"] == value, metric_id
        assert record["n"] == n, metric_id
        assert record["denominator"] == denominator, metric_id
        assert record["language"] == "en", metric_id
        assert record["cjk_ratio"] == 0.0, metric_id
        assert "LANGUAGE_NOT_SUPPORTED" not in record["warnings"], metric_id


def test_mixed_language_ratio_boundary():
    # 11 / (11 + 89) = 0.11 -> unsupported
    above = tm.detect_language("车" * 11 + " " + "word " * 89)
    assert (above["cjk_chars"], above["ascii_alpha_tokens"]) == (11, 89)
    assert above["cjk_ratio"] == pytest.approx(0.11)
    assert above["supported"] is False and above["language"] == "zh"
    # zh: the surface metrics are measured; the two unmeasurable ones say why
    mixed_zh = tm.compute_text_metrics("车" * 11 + " " + "word " * 89, BUNDLE)
    assert mixed_zh["M-SLEN-01"]["value"] is not None
    assert mixed_zh["M-HED-14"]["value"] is not None
    assert mixed_zh["M-TENSE-28"]["warnings"] == ["CAPABILITY_NOT_SUPPORTED"]
    # 10 / (10 + 90) = 0.10 exactly -> supported (threshold is inclusive)
    exact = tm.detect_language("车" * 10 + " " + "word " * 90)
    assert exact["cjk_ratio"] == pytest.approx(0.10)
    assert exact["supported"] is True and exact["language"] == "en"
    assert tm.compute_text_metrics("车" * 10 + " " + "word " * 90, BUNDLE)["M-SLEN-01"]["value"] is not None
    # 9 / (9 + 91) = 0.09 -> supported
    below = tm.detect_language("车" * 9 + " " + "word " * 91)
    assert below["cjk_ratio"] == pytest.approx(0.09)
    assert below["supported"] is True and below["language"] == "en"


def test_unsupported_language_never_raises_on_empty_bundle():
    """No lexicon bundle must still not raise, and must not fabricate numbers."""
    metrics = tm.compute_text_metrics(CHINESE_TEXT, None)
    # No bundle at all: the lexicon-driven metrics must degrade to empty
    # lexicons (all-zero densities are legitimate here because the lexicon is
    # empty and the warning says so), and nothing may raise.
    for mid, record in metrics.items():
        if record["value"] is None:
            # two legitimate "not measured" reasons: wrong language, or an
            # INFERRED metric whose calibration set is not frozen yet
            assert record["warnings"] in (
                ["CAPABILITY_NOT_SUPPORTED"], ["NOT_IMPLEMENTED"]), mid
        elif mid in {"M-HED-14", "M-BOO-15", "M-CONN-30c", "M-CONN-30k",
                     "M-CONN-30r", "M-AWR-03"}:
            assert any("is empty" in w for w in record["warnings"]), mid
    assert metrics["M-SLEN-01"]["value"] is not None


# ---------------------------------------------------------------------------
# Environment-dependent integration cases
# ---------------------------------------------------------------------------

def test_integration_with_real_lexicon_bundle():
    loader = pytest.importorskip("lexicon_loader")
    try:
        bundle = loader.load_lexicons()
    except Exception as exc:  # data/lexicons/v1 may not have landed yet
        pytest.skip(f"real lexicon data unavailable: {type(exc).__name__}: {exc}")
    metrics = tm.compute_text_metrics(SAMPLE_TEXT, bundle)
    assert set(metrics) == set(tm.METRIC_IDS)
    assert _all_spans_slice_back(SAMPLE_TEXT, metrics) == []
    for metric_id in ("M-HED-14", "M-BOO-15"):
        record = metrics[metric_id]
        assert record["n_lexicon_entries"] >= 40, metric_id
    parts = [metrics["M-CONN-30c"], metrics["M-CONN-30k"], metrics["M-CONN-30r"]]
    assert abs(metrics["M-CONN-30"]["value"] - sum(p["value"] for p in parts)) < 1e-9
    assert metrics["M-CONN-30c"]["n_lexicon_entries"] >= 15
    assert metrics["M-AWR-03"]["n_lexicon_entries"] >= 300
    assert metrics["M-NOM-10"]["n_denylist"] >= 60
    assert metrics["M-SLEN-01"]["denominator"] == len(tm.tokenize(SAMPLE_TEXT))


def test_real_connector_lexicon_excludes_ambiguous_bare_words():
    """Integration: the frozen causal lexicon must not keep bare ambiguous words."""
    loader = pytest.importorskip("lexicon_loader")
    try:
        bundle = loader.load_lexicons()
    except Exception as exc:  # data/lexicons/v1 may not have landed yet
        pytest.skip(f"real lexicon data unavailable: {type(exc).__name__}: {exc}")
    causal = set(bundle.connectors["causal"].entries)
    result = set(bundle.connectors["result"].entries)
    contrastive = set(bundle.connectors["contrastive"].entries)
    assert not ({"as", "since", "through"} & causal), sorted({"as", "since", "through"} & causal)
    assert {"because", "because of", "due to", "owing to", "as a result of"} <= causal
    assert "so" not in result and "so that" in result
    assert not ({"still", "while"} & contrastive)
    repro = "We use the GPU as a coprocessor. As shown in Fig. 1, it works."
    assert tm.compute_text_metrics(repro, bundle)["M-CONN-30k"]["n"] == 0


def _first_real_corpus_text(limit: int = 6000):
    if not VRP_CORPUS.is_dir():
        return None
    for path in sorted(VRP_CORPUS.glob("*/mineru/*/auto/*_content_list.json")):
        try:
            blocks = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunks = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("text")]
        text = "\n".join(chunks)
        if len(text) > 1200:
            return path, text[:limit]
    return None


def test_real_corpus_excerpt_if_available():
    found = _first_real_corpus_text()
    if found is None:
        pytest.skip("real VRP corpus not available")
    path, text = found
    metrics = tm.compute_text_metrics(text, BUNDLE)
    assert set(metrics) == set(tm.METRIC_IDS)
    assert _all_spans_slice_back(text, metrics) == []
    assert metrics["M-SLEN-01"]["n"] >= 3
    assert metrics["M-SLEN-01"]["denominator"] == len(tm.tokenize(text))
    assert metrics["M-MTLD-02"]["value"] is not None
    assert metrics["M-CONN-30"]["value"] is not None
    assert all(0.0 <= metrics[m]["value"] <= 1.0
               for m in ("M-HED-14", "M-BOO-15", "M-AWR-03", "M-NOM-10", "M-PAS-09", "M-TENSE-28"))
    print(f"corpus excerpt: {path.name} chars={len(text)}")


def test_pdtb_connectors_never_leak_into_conn30():
    """M-CONN-30 must stay exactly 30c+30k+30r once temporal/condition exist."""
    # The PDTB groups (issue #23) are matched in the same pass so all five
    # stay exclusive, but the union total must ignore them: a temporal hit
    # that silently inflated M-CONN-30 would break the frozen identity.
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    text = ("首先提出方法。如果约束满足，则可行。与此同时，结果如下。"
            "因此该方法显著有效。随后，实验完成。只有满足条件，才能收敛。")
    metrics = tm.compute_text_metrics(text, zh)
    parts = (metrics["M-CONN-30c"]["value"] + metrics["M-CONN-30k"]["value"]
             + metrics["M-CONN-30r"]["value"])
    assert metrics["M-CONN-30"]["value"] == pytest.approx(parts), \
        "M-CONN-30 must equal 30c+30k+30r exactly"
    assert metrics["M-CONN-30t"]["n"] >= 1, "temporal hits must register"
    assert metrics["M-CONN-30q"]["n"] >= 1, "condition hits must register"
    assert metrics["M-CONN-30t"]["unit"] == tm._UNIT_PER_1000_CJK_UNITS
    assert set(metrics["M-CONN-30"]["components"]) == {
        "M-CONN-30c", "M-CONN-30k", "M-CONN-30r"}, \
        "components must list the frozen three only"
    assert "M-CONN-30t" not in metrics["M-CONN-30"]["components"]

def test_sentence_pattern_counts_are_deterministic_and_traceable():
    """M-SPAT-44/45/46 are OBSERVED structural counts (issue #23)."""
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    text = ("然而，本文提出一种方法。该方法包含三个步骤，且每步都重要。"
            "如果约束满足，则求解可行。简单的单句。因此，该框架有效。")
    metrics = tm.compute_text_metrics(text, zh)
    for m in ("M-SPAT-44", "M-SPAT-45", "M-SPAT-46"):
        rec = metrics[m]
        assert rec["state"] == "OBSERVED", m
        assert rec["method"] == "rule", m
        assert rec["value"] is not None and 0.0 <= rec["value"] <= 1.0, m
        assert rec["evidence"]["count"] == rec["n"], m
        for item in rec["evidence"]["sample"]:
            assert item["excerpt"] in text, m
    # 5 sentences. 4 carry a clause separator; 3 of them split into multiple
    # clauses (the short one does not); 3 open with a connector.
    assert metrics["M-SPAT-44"]["denominator"] == 5
    assert metrics["M-SPAT-44"]["n"] == 4
    assert metrics["M-SPAT-45"]["n"] == 3
    assert metrics["M-SPAT-46"]["n"] == 3


def test_sentence_patterns_never_fabricate_without_sentences():
    """No sentence -> null + a named cause, never 0."""
    empty = tm.compute_text_metrics("   ", BUNDLE)
    for m in ("M-SPAT-44", "M-SPAT-45", "M-SPAT-46"):
        assert empty[m]["value"] is None, m
        assert empty[m]["warnings"] == ["CAPABILITY_NOT_SUPPORTED"], m

def test_terminology_analyzer_detects_real_variants():
    """M-TERM-47/48 detect spelling variants of the same concept (#23)."""
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    # The classic defect: the same concept spelled 库 and 储.
    text = ("本文研究多仓库路径优化问题。多仓储路径优化是关键。"
            "多库路径优化方法如下。该多仓库路径优化模型有效。"
            "实验表明多仓储路径优化可行。多仓库路径优化收敛快。")
    metrics = tm.compute_text_metrics(text, zh)
    rec47 = metrics["M-TERM-47"]
    rec48 = metrics["M-TERM-48"]
    assert rec47["state"] == "OBSERVED" and rec47["method"] == "rule"
    assert rec48["state"] == "OBSERVED"
    # at least the 库/储 pair must surface as one cluster with two spellings
    clusters = rec47["clusters_found"]
    assert len(clusters) >= 1, "the variant pair must be detected"
    spellings = {w for c in clusters for w in c["variants"]}
    assert {"多仓库路径优化", "多仓储路径优化"} <= spellings, spellings
    # M-TERM-48 counts the distinct terms needing harmonisation
    assert rec48["value"] >= 2
    assert rec48["unit"] == "index"
    # every variant the metrics report must actually occur in the text
    for item in rec47["evidence"]["sample"]:
        assert item["term"] in text and item["canonical"] in text


def test_terminology_analyzer_reports_zero_for_a_consistent_text():
    """A text using one spelling only must report 0, not a fabricated defect."""
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    text = ("本文研究多仓库路径优化问题。多仓库路径优化是关键。"
            "该多仓库路径优化模型有效。多仓库路径优化收敛快。")
    metrics = tm.compute_text_metrics(text, zh)
    assert metrics["M-TERM-47"]["value"] == 0.0
    assert metrics["M-TERM-47"]["clusters_found"] == []
    assert metrics["M-TERM-48"]["value"] == 0.0

# ---------------------------------------------------------------------------
# Stance layer (issue #23): INFERRED, calibrated on the frozen annotation set.
# The tests below lock the admission rules - a change to either the frozen
# file or the classifier invalidates them loudly rather than silently
# degrading an INFERRED number.
# ---------------------------------------------------------------------------

STANCE_CALIBRATION = (Path(__file__).resolve().parent.parent / "data"
                      / "stance-calibration-zh.json")


def test_stance_calibration_set_is_frozen_and_consistent():
    """The INFERRED admission rules: labels, kappa >= 0.6, >= 200 sentences."""
    if not STANCE_CALIBRATION.is_file():
        pytest.skip("stance calibration set not shipped")
    data = json.loads(STANCE_CALIBRATION.read_text(encoding="utf-8"))
    assert data["labels"] == list(tm._STANCE_LABELS)
    n = len(data["sentences"])
    assert n >= 200, "INFERRED admission requires >= 200 annotated sentences"
    agreement = data["annotation_protocol"]["agreement"]
    assert agreement["metric"] == "cohens_kappa"
    assert agreement["value"] >= agreement["threshold"] >= 0.6
    assert agreement["n_sentences"] == n
    by_split = Counter(s["split"] for s in data["sentences"])
    assert by_split["train"] + by_split["holdout"] == n
    assert all(s["label"] in tm._STANCE_LABELS for s in data["sentences"])
    assert all(s["source"] in ("agreed", "adjudicated")
               for s in data["sentences"])


def test_stance_calibration_facts_match_the_frozen_set():
    """The shipped constants must be re-derivable from the frozen file."""
    if not STANCE_CALIBRATION.is_file():
        pytest.skip("stance calibration set not shipped")
    data = json.loads(STANCE_CALIBRATION.read_text(encoding="utf-8"))
    facts = tm._STANCE_CALIBRATION_FACTS
    assert facts["annotator_agreement"]["value"] == pytest.approx(
        data["annotation_protocol"]["agreement"]["value"])
    assert facts["annotator_agreement"]["n_sentences"] == len(
        data["sentences"])
    # The shipped classifier runs against the real zh release; the English
    # FakeBundle has empty hedge/booster lists and would mis-score everything.
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    hold = [s for s in data["sentences"] if s["split"] == "holdout"]
    hedge = zh.hedge.entries
    boost = zh.booster.entries
    tp, fp, fn = Counter(), Counter(), Counter()
    for s in hold:
        pred = tm._classify_sentence_stance(s["text"], hedge, boost)
        if pred == s["label"]:
            tp[pred] += 1
        else:
            fp[pred] += 1
            fn[s["label"]] += 1
    f1s = []
    for label in tm._STANCE_LABELS:
        want = facts["holdout_per_label"][label]
        precision = tp[label] / max(1, tp[label] + fp[label])
        recall = tp[label] / max(1, tp[label] + fn[label])
        assert want["precision"] == pytest.approx(precision, abs=2e-3), label
        assert want["recall"] == pytest.approx(recall, abs=2e-3), label
        f1 = 2 * precision * recall / max(1e-9, precision + recall)
        f1s.append(f1)
    assert facts["holdout_macro_f1"] == pytest.approx(sum(f1s) / len(f1s),
                                                       abs=2e-3)
    assert facts["holdout_accuracy"] == pytest.approx(
        sum(tp.values()) / len(hold), abs=2e-3)


def test_stance_labels_partition_the_sentences():
    """The three rates sum to 1: every sentence gets exactly one stance."""
    text = ("本文提出一种方法。该方法显著优于基线。这可能是因为约束更紧。"
            "实验表明结果可靠。定义1给出形式化描述。")
    metrics = tm.compute_text_metrics(text, BUNDLE)
    rates = [metrics[m]["value"] for m in
             ("M-STNC-41", "M-STNC-42", "M-STNC-43")]
    assert sum(rates) == pytest.approx(1.0)
    total = sum(metrics[m]["n"] for m in ("M-STNC-41", "M-STNC-42", "M-STNC-43"))
    assert total == metrics["M-STNC-41"]["denominator"]


def test_stance_never_fabricates_on_empty_or_english_text():
    """Unmeasurable cases name their cause and never fabricate a 0."""
    # A single trigger-free Chinese sentence is a legitimate assertive
    # sentence: hedging/boosting are honestly 0/1, not fabricated, and the
    # record still carries the INFERRED state with its calibration caveat.
    plain = tm.compute_text_metrics("无终止符的标题式短语", BUNDLE)
    assert plain["M-STNC-41"]["value"] == 0.0  # no hedge present
    assert plain["M-STNC-43"]["value"] == 1.0  # the sentence is assertive
    assert plain["M-STNC-41"]["state"] == "INFERRED"
    # English has no calibrated set -> CAPABILITY_NOT_SUPPORTED, and the
    # NOT_IMPLEMENTED placeholder must not resurface on the en path.
    for text in ("", SAMPLE_TEXT):
        en = tm.compute_text_metrics(text, None)
        for m in ("M-STNC-41", "M-STNC-42", "M-STNC-43"):
            assert en[m]["value"] is None, m
            assert en[m]["warnings"] == ["CAPABILITY_NOT_SUPPORTED"], m
            assert "calibration" not in en[m], m


def test_stance_evidence_is_per_sentence_traceable():
    """INFERRED rule 4: the counted sentences must be recoverable."""
    text = "本文方法显著优于基线。这可能是因为约束。实验表明可靠。"
    metrics = tm.compute_text_metrics(text, BUNDLE)
    for m in ("M-STNC-41", "M-STNC-42", "M-STNC-43"):
        rec = metrics[m]
        sample = rec["evidence"]["sample"]
        assert rec["evidence"]["count"] == rec["n"], m
        for item in sample:
            excerpt = item["excerpt"]
            assert excerpt and excerpt in text, m
            assert excerpt.rstrip()[-1:] in tm._CJK_TERMINATORS, m


# ---------------------------------------------------------------------------
# Cross-layer language spec (issue #10 step): the conversion side records
# language/cjk_ratio/lang_source in _META.json; this layer re-detects it.  Both
# must reproduce the frozen shared cases, otherwise the two layers can disagree
# about what language a paper is in.
# ---------------------------------------------------------------------------

PAPER_READER_LANG_SPEC = (Path(__file__).resolve().parents[2] / "paper-reader"
                          / "scripts" / "lang_spec_cases.json")


def test_detect_language_matches_paper_reader_shared_spec():
    if not PAPER_READER_LANG_SPEC.is_file():
        pytest.skip("shared language spec absent (paper-reader not checked out)")
    spec = json.loads(PAPER_READER_LANG_SPEC.read_text(encoding="utf-8"))
    assert spec["threshold"] == tm.LANGUAGE_SUPPORT_CJK_THRESHOLD
    assert spec["round_digits"] == tm._ROUND_DIGITS
    assert [list(r) for r in tm._CJK_RANGES] == spec["cjk_ranges"]
    assert tm._ALPHA_TOKEN_RE.pattern == spec["alpha_token_regex"]
    for case in spec["cases"]:
        record = tm.detect_language(case["text"])
        expected = case["expected"]
        assert record["language"] == expected["language"], case["id"]
        assert record["cjk_ratio"] == expected["cjk_ratio"], case["id"]
        assert record["cjk_chars"] == expected["cjk_chars"], case["id"]
        assert record["ascii_alpha_tokens"] == expected["ascii_alpha_tokens"], case["id"]


def test_hedge_stacking_detection_warning() -> None:
    """Local hedge stacking (>=3 hedges in one sentence) triggers warning."""
    loader = pytest.importorskip("lexicon_loader")
    try:
        zh = loader.load_lexicons(language="zh")
    except Exception as exc:
        pytest.skip("zh lexicon release unavailable: %s: %s" % (type(exc).__name__, exc))
    # In zh lexicon: 可能, 或许, 某种程度上 are real hedge entries
    stacked_text = "该结果可能在某种程度上或许反映了模型的特性。"
    metrics = tm.compute_text_metrics(stacked_text, zh)
    rec = metrics["M-HED-14"]
    warnings = rec.get("warnings", [])
    assert any("HEDGE_STACKING_DETECTED" in w for w in warnings)
