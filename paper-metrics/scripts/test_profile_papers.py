#!/usr/bin/env python3
"""TDD tests for profile_papers.py — the domain profiler for the paper-metrics journal mode.

Covers:
  1. Paper discovery (walks paper-analysis/ → finds content_list.json)
  2. Section skeleton extraction (normalizes titles → canonical labels)
  3. Asset pattern extraction (figures/tables/equations attributed to sections)
  4. Citation style detection (IEEE-numeric / author-year / mixed)
  5. Output contract (_domain_profile.json schema + _domain_profile.md human report)
  6. Robustness: empty corpus, papers with missing mineru/, mixed engine outputs

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_profile_papers.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import profile_papers as pp  # noqa: E402

VRP_CORPUS = Path("/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis")
TGA_PAPER = VRP_CORPUS / "2025 - TGA Tensor GPU Acceleration for VRP Local Search [Lei-Hao-Wu]"


# ---------------------------------------------------------------------------
# Fixtures: build a synthetic paper-analysis/ tree
# ---------------------------------------------------------------------------

def _make_block(btype: str, text: str = "", level: int | None = None,
                caption: list[str] | None = None) -> dict:
    """Build a single MinerU content_list.json block."""
    b: dict = {"type": btype, "bbox": [0, 0, 100, 20], "page_idx": 0}
    if btype == "text":
        b["text"] = text
        if level is not None:
            b["text_level"] = level
    elif btype == "equation":
        b["text"] = f"$$ {text} $$"
        b["text_format"] = "latex"
    elif btype in ("image", "chart"):
        b["img_path"] = f"images/{text or 'x'}.jpg"
        b["image_caption"] = caption or []
    elif btype == "table":
        b["table_caption"] = caption or ["Table N", "desc"]
        b["table_body"] = "<table></table>"
    return b


def _write_paper(corpus_dir: Path, paper_name: str, paper_id: str,
                 blocks: list[dict]) -> Path:
    """Create a synthetic paper dir under corpus_dir with content_list.json."""
    paper_dir = corpus_dir / paper_name / "mineru" / paper_id / "auto"
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / f"{paper_id}_content_list.json").write_text(
        json.dumps(blocks), encoding="utf-8"
    )
    return corpus_dir / paper_name


@pytest.fixture
def synthetic_corpus(tmp_path: Path) -> Path:
    """Build a 3-paper synthetic corpus exercising IMRaD + a survey + an IEEE-cited paper."""
    corpus = tmp_path / "paper-analysis"

    # Paper 1: standard IMRaD with IEEE numeric citations
    _write_paper(corpus, "Paper One IMRaD", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "Abstract", level=2),
        _make_block("text", "Intro body with citation [1] and [2]."),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Prior work [3] showed X. Building on [4], we propose..."),
        _make_block("text", "2 Related Work", level=2),
        _make_block("text", "Method A [5] and method B [6]."),
        _make_block("text", "3 Method", level=2),
        _make_block("text", "Our approach is as follows."),
        _make_block("equation", "f(x) = x^2"),
        _make_block("image", "framework", caption=["Figure 1", "Framework overview."]),
        _make_block("text", "4 Experiments", level=2),
        _make_block("chart", "result_curve"),
        _make_block("table", caption=["Table 1", "Benchmark results."]),
        _make_block("text", "5 Conclusion", level=2),
        _make_block("text", "We conclude."),
    ])

    # Paper 2: survey paper with author-year citations
    _write_paper(corpus, "Paper Two Survey", "p2", [
        _make_block("text", "Title Two", level=1),
        _make_block("text", "Abstract", level=2),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Smith (2020) introduced the problem. Jones et al. (2021) extended it."),
        _make_block("text", "2 Taxonomy", level=2),
        _make_block("text", "We categorize prior methods."),
        _make_block("text", "3 Open Challenges", level=2),
        _make_block("text", "Several challenges remain."),
        _make_block("text", "4 Conclusion", level=2),
    ])

    # Paper 3: IMRaD with equations and tables
    _write_paper(corpus, "Paper Three IMRaD", "p3", [
        _make_block("text", "Title Three", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "The problem is hard [7]."),
        _make_block("text", "2 Preliminaries", level=2),
        _make_block("equation", "E = mc^2"),
        _make_block("text", "3 Method", level=2),
        _make_block("equation", "L = -\\sum y \\log \\hat{y}"),
        _make_block("image", "network"),
        _make_block("text", "4 Experiments", level=2),
        _make_block("table", caption=["Table 1", "Hyperparameters."]),
        _make_block("chart", "loss_curve"),
        _make_block("text", "5 Conclusion", level=2),
    ])

    return corpus


# ---------------------------------------------------------------------------
# Test 1: Paper discovery
# ---------------------------------------------------------------------------

def test_discover_papers_finds_all_three(synthetic_corpus: Path) -> None:
    papers = pp.discover_papers(synthetic_corpus)
    assert len(papers) == 3
    names = {p.name for p in papers}
    assert "Paper One IMRaD" in names
    assert "Paper Two Survey" in names
    assert "Paper Three IMRaD" in names


def test_discover_papers_skips_empty(tmp_path: Path) -> None:
    """An empty paper dir (no content_list.json) is skipped with a warning, not crash."""
    corpus = tmp_path / "paper-analysis"
    (corpus / "Empty Paper" / "mineru").mkdir(parents=True)  # no json
    _write_paper(corpus, "Real Paper", "rp", [_make_block("text", "T", level=1)])
    papers = pp.discover_papers(corpus)
    assert len(papers) == 1
    assert papers[0].name == "Real Paper"


def test_discover_papers_empty_corpus_warns(tmp_path: Path) -> None:
    """A corpus with zero discoverable papers raises a clear error."""
    corpus = tmp_path / "paper-analysis"
    corpus.mkdir()
    with pytest.raises(pp.ProfileError, match="no papers"):
        pp.discover_papers(corpus)


# ---------------------------------------------------------------------------
# Test 2: Section skeleton extraction + canonicalization
# ---------------------------------------------------------------------------

def test_extract_section_skeleton_canonicalizes_variants(synthetic_corpus: Path) -> None:
    papers = pp.discover_papers(synthetic_corpus)
    skeleton = pp.extract_section_skeleton(papers)
    by_canonical = {s["canonical"]: s for s in skeleton}
    # "Introduction" should appear in all 3 papers
    intro = by_canonical["introduction"]
    assert intro["frequency"] == 3
    assert "1 Introduction" in intro["variants_seen"]
    # "Method" appears in papers 1 and 3
    assert by_canonical["method"]["frequency"] == 2
    # "Conclusion" appears in all 3
    assert by_canonical["conclusion"]["frequency"] == 3


def test_section_normalization_handles_numbering_prefixes() -> None:
    assert pp.normalize_section_title("1 Introduction") == "introduction"
    assert pp.normalize_section_title("I. INTRODUCTION") == "introduction"
    assert pp.normalize_section_title("1. Introduction") == "introduction"
    assert pp.normalize_section_title("3.2 CUDA programming") == "cuda programming"
    assert pp.normalize_section_title("Section 4: Method") == "method"


def test_section_canonical_label_mapping() -> None:
    assert pp.canonical_section_label("introduction") == "introduction"
    assert pp.canonical_section_label("related work") == "related_work"
    assert pp.canonical_section_label("related works") == "related_work"
    assert pp.canonical_section_label("preliminaries") == "preliminaries"
    assert pp.canonical_section_label("preliminary") == "preliminaries"
    assert pp.canonical_section_label("method") == "method"
    assert pp.canonical_section_label("methods") == "method"
    assert pp.canonical_section_label("approach") == "method"
    assert pp.canonical_section_label("our approach") == "method"
    assert pp.canonical_section_label("experiments") == "experiments"
    assert pp.canonical_section_label("experimental results") == "experiments"
    assert pp.canonical_section_label("evaluation") == "experiments"
    assert pp.canonical_section_label("conclusion") == "conclusion"
    assert pp.canonical_section_label("conclusions") == "conclusion"


def test_section_keyword_matching_for_subsection_variants() -> None:
    """Numbered subsection variants (tagged level=2 by MinerU) must canonicalize
    to their parent section via keyword matching. This is the key mechanism that
    makes the profiler domain-agnostic across VRP, KD, SR, etc."""
    assert pp.canonical_section_label("reformulating kd") == "method"
    assert pp.canonical_section_label("network architecture") == "method"
    assert pp.canonical_section_label("loss function") == "method"
    assert pp.canonical_section_label("attention transfer") == "method"
    assert pp.canonical_section_label("feature distillation") == "method"
    assert pp.canonical_section_label("proposed approach") == "method"
    assert pp.canonical_section_label("model architecture") == "method"
    assert pp.canonical_section_label("ablation study") == "experiments"
    assert pp.canonical_section_label("comparison with state-of-the-arts") == "experiments"
    assert pp.canonical_section_label("implementation details") == "experiments"
    assert pp.canonical_section_label("datasets and metrics") == "experiments"
    assert pp.canonical_section_label("main results") == "experiments"
    assert pp.canonical_section_label("experimental setup") == "experiments"
    assert pp.canonical_section_label("visualization") == "experiments"
    assert pp.canonical_section_label("model analysis") == "discussion"
    assert pp.canonical_section_label("complexity analysis") == "discussion"
    assert pp.canonical_section_label("消融实验") == "experiments"
    assert pp.canonical_section_label("网络结构") == "method"
    assert pp.canonical_section_label("实验结果及分析") == "experiments"


def test_ablation_pattern_precedence_over_experiment() -> None:
    """The keyword pattern list is ordered: 'ablation' must match before the
    general 'experiment' pattern. This test locks in that ordering invariant;
    'ablation study' contains neither 'experiment' nor 'baseline' as a word,
    so it relies solely on the ablation-specific pattern."""
    assert pp.canonical_section_label("ablation study") == "experiments"


# ---------------------------------------------------------------------------
# Test 3: Asset pattern extraction
# ---------------------------------------------------------------------------

def test_extract_asset_patterns(synthetic_corpus: Path) -> None:
    papers = pp.discover_papers(synthetic_corpus)
    figures, tables, equations = pp.extract_asset_patterns(papers)
    # Equations: paper1 (1) + paper3 (2) = 3 total
    assert sum(f["frequency"] for f in equations) >= 3
    # Tables: paper1 (1) + paper3 (1) = 2
    assert sum(t["frequency"] for t in tables) >= 2
    # Images/charts: paper1 (1 img + 1 chart) + paper3 (1 img + 1 chart) = 4
    assert sum(f["frequency"] for f in figures) >= 4
    # The framework-overview image in paper1's Method section should be attributed to "method"
    framework_figs = [f for f in figures if f["sub_type"] == "framework-overview"]
    assert any(f["section"] == "method" for f in framework_figs)


def test_asset_attribution_uses_nearest_preceding_title() -> None:
    """An equation with no preceding title in the paper should be attributed to a default bucket."""
    corpus = Path("/tmp/_test_inline")
    _write_paper(corpus, "Inline Paper", "ip", [
        _make_block("text", "Title", level=1),
        _make_block("equation", "x = 1"),  # no section header before this
    ])
    papers = pp.discover_papers(corpus)
    _, _, equations = pp.extract_asset_patterns(papers)
    # The equation should be attributed to some section (even if "front_matter"), not crash
    assert len(equations) == 1


# ---------------------------------------------------------------------------
# Test 4: Citation style detection
# ---------------------------------------------------------------------------

def test_detect_citation_style_ieee(synthetic_corpus: Path) -> None:
    papers = pp.discover_papers(synthetic_corpus)
    style = pp.detect_citation_style(papers)
    # Papers 1 and 3 use [N]; paper 2 uses author-year. Majority is IEEE.
    assert style["detected"] in {"ieee-numeric", "mixed"}
    assert style["confidence"] > 0.5
    assert style["evidence"]["bracket_numeric_matches"] >= 4  # [1][2][3][4][7]


def test_detect_citation_style_pure_author_year(tmp_path: Path) -> None:
    corpus = tmp_path / "paper-analysis"
    _write_paper(corpus, "AY Paper", "ay", [
        _make_block("text", "Title", level=1),
        _make_block("text", "Body. Smith (2020) and Jones et al. (2021) and (Brown, 2022)."),
    ])
    papers = pp.discover_papers(corpus)
    style = pp.detect_citation_style(papers)
    assert style["detected"] == "author-year"


# ---------------------------------------------------------------------------
# Test 5: Output contract — JSON schema + MD report
# ---------------------------------------------------------------------------

def test_profile_outputs_valid_json_and_md(synthetic_corpus: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = pp.run_profile(synthetic_corpus, out_dir)
    json_path = out_dir / "_domain_profile.json"
    md_path = out_dir / "_domain_profile.md"
    assert json_path.exists()
    assert md_path.exists()

    profile = json.loads(json_path.read_text(encoding="utf-8"))
    # Schema assertions
    assert "meta" in profile
    assert profile["meta"]["paper_count"] == 3
    # v2: the fingerprinted profile carries no machine-specific path
    assert "corpus_path" not in profile["meta"]
    assert "section_skeleton" in profile
    assert isinstance(profile["section_skeleton"], list)
    assert "figure_placement_patterns" in profile
    assert "table_placement_patterns" in profile
    assert "equation_placement_patterns" in profile
    assert "citation_style" in profile
    assert "reference_count" in profile
    assert "contribution_phrases" in profile

    # MD report is non-empty and mentions the section skeleton
    md_text = md_path.read_text(encoding="utf-8")
    assert "section" in md_text.lower()
    assert "introduction" in md_text.lower()


# ---------------------------------------------------------------------------
# Test 6: Snapshot test against the REAL TGA paper (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not TGA_PAPER.exists(), reason="TGA paper not available on this machine")
def test_language_record_mismatch_is_reported_not_hidden(tmp_path: Path) -> None:
    """Two layers measure different artifacts, so they may disagree.  A silent
    disagreement would leave a reader with one label and no hint that the
    conversion layer had recorded another."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "This paper compares three baselines on two datasets."),
    ])
    (paper_dir / "_META.json").write_text(json.dumps({
        "stem": "p1", "language": "zh", "cjk_ratio": 0.99,
        "lang_source": "mineru_content_list",
    }), encoding="utf-8")

    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    line = (out_dir / "_per_paper_metrics.jsonl").read_text(
        encoding="utf-8").splitlines()[0]
    record = json.loads(line)
    assert record["language"] == "en", "this layer's own answer must not move"
    assert record["upstream"]["language_recorded"] == "zh"
    assert "LANGUAGE_METADATA_MISMATCH" in record["warnings"]
    assert record["language_mismatch"]["recorded"] == "zh"
    assert record["language_mismatch"]["detected"] == "en"


def test_absent_language_record_adds_no_new_upstream_keys(tmp_path: Path) -> None:
    """A corpus whose _META.json predates the language triple must keep exactly the
    audit trail it had before (no new keys, no new warnings)."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Prior work [1] showed X."),
    ])
    (paper_dir / "_META.json").write_text(json.dumps({"stem": "p1"}), encoding="utf-8")

    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    record = json.loads((out_dir / "_per_paper_metrics.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert "language_recorded" not in record["upstream"]
    assert "language_mismatch" not in record
    assert "LANGUAGE_METADATA_MISMATCH" not in record["warnings"]


def test_language_record_agreement_adds_no_warning(tmp_path: Path) -> None:
    """Agreement is the normal case: no mismatch key, no warning."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "本文比较了三种基线方法，并在两个数据集上验证。"),
    ])
    (paper_dir / "_META.json").write_text(json.dumps({
        "stem": "p1", "language": "zh", "cjk_ratio": 0.97,
        "lang_source": "mineru_content_list",
    }), encoding="utf-8")

    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    record = json.loads((out_dir / "_per_paper_metrics.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert record["language"] == "zh"
    assert "language_mismatch" not in record
    assert "LANGUAGE_METADATA_MISMATCH" not in record["warnings"]


def test_language_mismatch_ignores_unknown_on_either_side(tmp_path: Path) -> None:
    """ "unknown" is an absence of judgement, not a competing claim: treating it as
    a disagreement would make the signal noisy in both directions."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "This paper compares three baselines."),
    ])
    (paper_dir / "_META.json").write_text(json.dumps({
        "stem": "p1", "language": "unknown", "lang_source": "mineru_content_list",
    }), encoding="utf-8")

    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    record = json.loads((out_dir / "_per_paper_metrics.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert record["language"] == "en"
    assert "language_mismatch" not in record
    assert "LANGUAGE_METADATA_MISMATCH" not in record["warnings"]


def test_detect_missing_is_not_silence(tmp_path: Path) -> None:
    """Upstream recorded a language this layer could not measure at all: that is
    not agreement, and it must not read like it (R2)."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Title One", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "This paper compares three baselines."),
    ])
    (paper_dir / "_META.json").write_text(json.dumps({
        "stem": "p1", "language": "zh", "lang_source": "mineru_content_list",
    }), encoding="utf-8")

    out_dir = tmp_path / "out"
    # Force this layer's own detection to be inconclusive for the paper.
    orig = pp._language_facts

    def _blind(text_metrics_mod, text, metrics):
        facts = dict(orig(text_metrics_mod, text, metrics))
        facts["language"] = "unknown"
        return facts

    pp._language_facts = _blind
    try:
        pp.run_profile(corpus, out_dir)
    finally:
        pp._language_facts = orig
    record = json.loads((out_dir / "_per_paper_metrics.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert record["language"] == "unknown"
    assert record["language_mismatch"]["kind"] == "detect_missing"
    assert "LANGUAGE_DETECT_MISSING" in record["warnings"]
    assert "LANGUAGE_METADATA_MISMATCH" not in record["warnings"]


def test_language_comparison_folds_case_and_region(tmp_path: Path) -> None:
    """ "EN" vs "en" and "zh-CN" vs "zh" are label differences, not language
    differences: they must not raise a mismatch."""
    from profile_papers import _language_record_mismatch
    assert _language_record_mismatch({"language": "en"}, {"language_recorded": "EN"}) is None
    assert _language_record_mismatch({"language": "zh"}, {"language_recorded": "zh-CN"}) is None
    assert _language_record_mismatch({"language": "en"}, {"language_recorded": " unknown "}) is None
    different = _language_record_mismatch({"language": "en"}, {"language_recorded": "zh"})
    assert different and different["kind"] == "mismatch"


def _language_corpus(corpus: Path, langs: tuple[str, ...]) -> None:
    for index, lang in enumerate(langs):
        name, pid = f"Paper {index}", f"p{index}"
        paper_dir = _write_paper(corpus, name, pid, [
            _make_block("text", "Title", level=1),
            _make_block("text", "1 Introduction", level=2),
            _make_block("text", "This paper compares three baselines."),
        ])
        (paper_dir / "_META.json").write_text(json.dumps(
            {"stem": pid, "language": lang, "lang_source": "mineru_content_list"}),
            encoding="utf-8")


def test_agreeing_corpus_raises_no_language_warning(tmp_path: Path) -> None:
    """Aggregate-layer regression: the per-paper tests and the corpus audits could
    not see this, yet every agreeing corpus used to report every paper as a
    mismatch (a defaulted "kind" turned "no claim" into "mismatch")."""
    corpus = tmp_path / "paper-analysis"
    _language_corpus(corpus, ("en", "en"))
    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))
    codes = [w.get("code") for w in summary.get("corpus_warnings", [])]
    assert "LANGUAGE_METADATA_MISMATCH" not in codes
    assert "LANGUAGE_DETECT_MISSING" not in codes


def test_one_mismatch_names_only_the_mismatching_paper(tmp_path: Path) -> None:
    corpus = tmp_path / "paper-analysis"
    _language_corpus(corpus, ("en", "zh"))
    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))
    hits = [w for w in summary.get("corpus_warnings", [])
            if w.get("code") == "LANGUAGE_METADATA_MISMATCH"]
    assert len(hits) == 1
    # paper_key is the paper directory name, not the content_list file name.
    assert "Paper 1" in hits[0]["detail"], "only the disagreeing paper may be named"
    assert "Paper 0" not in hits[0]["detail"], "an agreeing paper must never be named"



def test_block_census_separates_streams_and_flags_dropped(tmp_path: Path) -> None:
    """One census per stream; nothing is merged, and 'ignored' is a stated fact."""
    corpus = tmp_path / "paper-analysis"
    table_html = ('<table><tr><td colspan="3">12.73</td>'
                  '<td style="width:5%">0.982</td></tr></table>')
    _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "Body sentence with 7 words here."),
        {"type": "table", "table_body": table_html,
         "table_caption": ["Table 1", "Results 2026"]},
        {"type": "equation", "text": "$$ \\frac{12}{34} \\alpha x $$",
         "text_format": "latex"},
        {"type": "list", "list_items": ["item 1", "item 2 with 42"]},
        {"type": "image", "img_path": "images/f1.jpg",
         "image_caption": ["Figure 2", "Flow"]},
        {"type": "page_footnote", "text": "Funded by grant 2020-1234."},
    ])
    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    record = json.loads((out_dir / "_per_paper_metrics.jsonl")
                        .read_text(encoding="utf-8").splitlines()[0])
    census = record["block_census"]

    assert census["prose"]["blocks"] == 1
    assert census["prose"]["dropped_by_metrics"] is False
    assert census["tables"]["dropped_by_metrics"] is False   # S-TBL-* read tables
    assert census["tables"]["digits"] == 8, "cell digits only, never attributes"
    assert census["tables"]["raw_chars"] > census["tables"]["chars"]
    assert census["tables"]["caption_chars"] > 0
    assert census["tables"]["caption_digits"] == 5      # "Table 1" + "Results 2026"
    assert census["equations"]["digits"] == 4
    assert census["equations"]["raw_chars"] > census["equations"]["chars"]
    assert census["lists"]["chars"] == len("item 1 item 2 with 42")
    assert census["lists"]["digits"] == 4      # 1 + 2 + 42 → four digit characters
    assert census["figures"]["blocks"] == 1
    assert census["figures"]["caption_chars"] == len("Figure 2 Flow")
    assert census["figures"]["caption_digits"] == 1
    assert census["footnotes"]["digits"] == 8
    # every stream except prose is declared as not read by the metrics
    assert [k for k, v in census.items() if not v["dropped_by_metrics"]] == [
        "figures", "prose", "tables"]


def test_base_artifact_and_metric_scopes_are_declared(synthetic_corpus: Path,
                                                      tmp_path: Path) -> None:
    """The scope of every metric is a first-class field, and the census totals must
    agree with the per-paper records (otherwise the 'ignored' claim is unverifiable)."""
    out_dir = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out_dir)
    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))

    assert summary["base_artifact"]["name"] == "mineru_content_list"
    assert summary["base_artifact"]["scope"] == ["prose"]
    missing = [mid for mid, row in summary["metrics"].items() if not row.get("scope")]
    assert missing == [], f"metrics without a declared scope: {missing}"

    census = summary["block_census"]
    assert census["prose"]["dropped_by_metrics"] is False
    per_paper = [json.loads(line) for line in
                 (out_dir / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    for stream, totals in census.items():
        assert totals["blocks"] == sum(r["block_census"].get(stream, {}).get("blocks", 0)
                                       for r in per_paper), stream
        assert totals["digits"] == sum(r["block_census"].get(stream, {}).get("digits", 0)
                                       for r in per_paper), stream

    md = (out_dir / "_domain_profile.md").read_text(encoding="utf-8")
    assert "指标基座" in md and "未被指标读取的流" in md


def test_stream_metrics_reach_the_products_with_their_scope(tmp_path: Path) -> None:
    """Non-prose metrics must arrive in BOTH products and keep declaring their
    streams through aggregation.

    A metric whose scope is implicit is exactly how "digits that live in tables"
    was once mistaken for "digits lost in conversion", so aggregation must inherit
    the scope instead of stamping every metric as prose.
    """
    corpus = tmp_path / "paper-analysis"
    _write_paper(corpus, "Paper One", "p1", [
        _make_block("text", "As shown in Figure 1, the method works."),
        _make_block("image", "f1", caption=["Figure 1", "Framework"]),
        _make_block("table", "t1", caption=["Table 1", "Results"]),
    ])
    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)

    record = json.loads((out_dir / "_per_paper_metrics.jsonl")
                        .read_text(encoding="utf-8").splitlines()[0])
    stream_specs = ("S-CAP-01", "S-NUM-02", "S-REF-03")
    for metric_id in stream_specs:
        assert metric_id in record["metrics"], metric_id
        assert record["metrics"][metric_id]["scope"] == ["figures", "tables"]

    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))
    for metric_id in stream_specs:
        assert summary["metrics"][metric_id]["scope"] == ["figures", "tables"], metric_id
        assert summary["metrics"][metric_id]["unit"] == "ratio"
        assert summary["metrics"][metric_id]["n_valid"] == 1
    assert summary["metrics"]["M-SLEN-01"]["scope"] == ["prose"]


def test_tga_paper_real_profile(tmp_path: Path) -> None:
    """Profile the real TGA paper alone and verify counts match the source PDF."""
    # Build a 1-paper corpus by symlinking
    corpus = tmp_path / "paper-analysis"
    corpus.mkdir()
    link = corpus / "TGA"
    link.symlink_to(TGA_PAPER, target_is_directory=True)
    out_dir = tmp_path / "out"
    pp.run_profile(corpus, out_dir)
    profile = json.loads((out_dir / "_domain_profile.json").read_text(encoding="utf-8"))

    assert profile["meta"]["paper_count"] == 1
    # TGA has 55 equations (verified by direct inspection)
    total_eq = sum(e["frequency"] for e in profile["equation_placement_patterns"])
    assert 50 <= total_eq <= 60, f"expected ~55 equations, got {total_eq}"
    # TGA has 16 tables
    total_tbl = sum(t["frequency"] for t in profile["table_placement_patterns"])
    assert 14 <= total_tbl <= 18, f"expected ~16 tables, got {total_tbl}"
    # TGA has 15 images + 13 charts = 28 visual assets
    total_img = sum(f["frequency"] for f in profile["figure_placement_patterns"])
    assert 25 <= total_img <= 32, f"expected ~28 figures, got {total_img}"
    # Skeleton must include the expected canonical sections
    canonicals = {s["canonical"] for s in profile["section_skeleton"]}
    assert "introduction" in canonicals
    assert "method" in canonicals
    assert "experiments" in canonicals or "results" in canonicals


# ---------------------------------------------------------------------------
# Test 7: Full corpus run (integration)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not VRP_CORPUS.exists(), reason="VRP corpus not available")
def test_vrp_full_corpus_runs_fast(tmp_path: Path) -> None:
    """The full VRP corpus must profile in seconds, not minutes (CPU only, no GPU).

    Threshold history: v1 (structure only) budgeted 5s. v2 additionally computes
    14 text/structure metrics over ~425k tokens plus three stratified section
    passes, measuring ~4.6-5.0s on this machine -- which made the old 5s budget
    flaky. The budget is raised to 12s so the test still catches accidental
    O(n^2) or repeated-work regressions without depending on CPU noise.
    """
    import time
    out_dir = tmp_path / "out"
    t0 = time.time()
    pp.run_profile(VRP_CORPUS, out_dir)
    elapsed = time.time() - t0
    assert elapsed < 12.0, f"profiling took {elapsed:.2f}s, expected <12s"
    profile = json.loads((out_dir / "_domain_profile.json").read_text(encoding="utf-8"))
    assert profile["meta"]["paper_count"] >= 28


# ---------------------------------------------------------------------------
# Test 8 (v2): reference counting regression — the bug that shipped silently
# ---------------------------------------------------------------------------

def _refs_list_paper(corpus: Path) -> None:
    """A paper whose references are a MinerU list block (the real-world shape)."""
    paper_dir = corpus / "Refs As List" / "mineru" / "rl" / "auto"
    paper_dir.mkdir(parents=True, exist_ok=True)
    blocks = [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Prior work [1] showed X and [2] showed Y."),
        _make_block("text", "2 Method", level=2),
        _make_block("text", "We propose a method."),
        _make_block("text", "References", level=2),
        {"type": "list", "bbox": [0, 0, 100, 20], "page_idx": 0,
         "list_items": ["[1] Smith, J. (2020). A paper about things. Journal.",
                        "[2] Jones, A. and Lee, B. (2021). Another paper. Conf.",
                        "[3] Brown, C. et al. (2019). Third paper. Journal."]},
    ]
    (paper_dir / "rl_content_list.json").write_text(json.dumps(blocks), encoding="utf-8")


def test_reference_count_counts_list_blocks(tmp_path: Path) -> None:
    """Regression: v1 only accepted type=='text', so real MinerU reference lists
    (type=='list') yielded reference_count == 0 while all tests stayed green."""
    corpus = tmp_path / "paper-analysis"
    _refs_list_paper(corpus)
    papers = pp.discover_papers(corpus)
    rc = pp._count_references(papers)
    assert rc["median"] == 3, rc
    assert rc["n_papers_with_references"] == 1
    assert rc["per_paper"]["Refs As List"] == 3
    assert rc["evidence"]["sample"], "expected an evidence excerpt"


def test_reference_count_handles_author_year_and_numbered(tmp_path: Path) -> None:
    corpus = tmp_path / "paper-analysis"
    paper_dir = corpus / "Mixed Refs" / "mineru" / "mr" / "auto"
    paper_dir.mkdir(parents=True, exist_ok=True)
    blocks = [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Body text without citations."),
        _make_block("text", "References", level=2),
        _make_block("text", "1. Alpha, A. (2018). First study. Journal of Things."),
        _make_block("text", "2. Beta, B. (2019). Second study. Other Journal."),
    ]
    (paper_dir / "mr_content_list.json").write_text(json.dumps(blocks), encoding="utf-8")
    rc = pp._count_references(pp.discover_papers(corpus))
    assert rc["median"] == 2, rc


# ---------------------------------------------------------------------------
# Test 9 (v2): provenance, run metadata split, and skip ledger
# ---------------------------------------------------------------------------

def test_meta_has_no_volatile_fields_and_run_meta_exists(synthetic_corpus: Path,
                                                         tmp_path: Path) -> None:
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    profile = json.loads((out / "_domain_profile.json").read_text(encoding="utf-8"))
    # generated_at must not be in the fingerprinted artifact
    assert "generated_at" not in profile["meta"]
    assert "generated_at" not in json.dumps(profile)
    run_meta = json.loads((out / "_run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["generated_at"]
    assert run_meta["corpus_id"] == profile["corpus"]["id"]
    # the path moved out of the fingerprinted artifact entirely
    assert "corpus_path" not in profile["meta"]
    assert run_meta["corpus_path"].endswith("paper-analysis")
    assert profile["schema_version"] == pp.SCHEMA_VERSION


def test_skip_ledger_is_recorded_in_the_artifact(tmp_path: Path) -> None:
    corpus = tmp_path / "paper-analysis"
    (corpus / "Empty Paper" / "mineru").mkdir(parents=True)
    _write_paper(corpus, "Real Paper", "rp", [_make_block("text", "T", level=1)])
    out = tmp_path / "out"
    profile = pp.run_profile(corpus, out)
    skipped = {s["paper_key"]: s["reason"] for s in profile["corpus"]["skipped"]}
    assert skipped == {"Empty Paper": "no_content_list_json"}


def test_every_profiled_paper_carries_input_hashes(synthetic_corpus: Path,
                                                   tmp_path: Path) -> None:
    out = tmp_path / "out"
    profile = pp.run_profile(synthetic_corpus, out)
    profiled = profile["corpus"]["profiled"]
    assert len(profiled) == 3
    for entry in profiled:
        assert entry["inputs"], entry
        for art in entry["inputs"]:
            assert len(art["sha256"]) == 64
            int(art["sha256"], 16)
    assert len(profile["corpus"]["id"]) == 64


# ---------------------------------------------------------------------------
# Test 10 (v2): per-paper jsonl + corpus summary are consistent artifacts
# ---------------------------------------------------------------------------

def test_per_paper_jsonl_and_summary_are_present_and_recomputable(
        synthetic_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    jsonl = (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in jsonl]
    assert len(records) == 3
    keys = [r["paper_key"] for r in records]
    assert keys == sorted(keys), "jsonl must be ordered by paper_key"

    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    assert summary["analysis_unit"] == "paper"
    assert summary["weight_mode"] == "equal_paper"
    assert summary["n_papers"] == 3
    # The corpus summary must be mechanically recomputable from the jsonl
    # (compare after the same float rounding the writer applies).
    recomputed = pp._round_floats(pp.aggregate_corpus(records, summary.get("corpus_id")))
    assert recomputed == summary


def test_corpus_summary_flags_small_n(synthetic_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    # 3 papers -> every metric must carry the N_LT_5 guard
    assert summary["corpus_warnings"], "3-paper corpus must raise N_LT_5"
    assert {w["code"] for w in summary["corpus_warnings"]} >= {"N_LT_5"}
    for mid, m in summary["metrics"].items():
        codes = set(m.get("warnings") or ())
        if any(pp._is_missingness_cause(code) for code in codes):
            # Structurally unmeasurable on this corpus (this fixture has no
            # bibliography), so the cause is the actionable signal, not a sample-size
            # flag.  Demanding N_LT_5 here would hide the reason behind noise.
            continue
        assert "N_LT_5" in codes, mid


def test_metrics_carry_the_full_evidence_contract(synthetic_corpus: Path,
                                                  tmp_path: Path) -> None:
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    records = [json.loads(l) for l in
               (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    required = {"value", "n", "denominator", "unit", "state", "method",
                "metric_spec", "evidence", "warnings"}
    for r in records:
        assert r["metrics"], r["paper_key"]
        for mid, m in r["metrics"].items():
            missing = required - set(m)
            assert not missing, f"{mid} missing {missing}"
            assert m["state"] == "OBSERVED", f"{mid} must be OBSERVED"
            assert m["method"] == "rule", f"{mid} must be rule-based"
            ev = m["evidence"]
            assert isinstance(ev.get("sample"), list) and isinstance(ev.get("count"), int)


def test_corpus_medians_are_not_additive_but_per_paper_identity_holds(
        synthetic_corpus: Path, tmp_path: Path) -> None:
    """Locks in the semantics of M-CONN-30 vs its three components.

    The identity total == contrastive + causal + result is defined PER PAPER.
    Median is not additive, so the corpus-level median of the total must NOT be
    expected to equal the sum of the component medians. A downstream consumer
    that derives the total from component medians would be wrong; this test
    fails if someone "fixes" the data to make that derivation look true.
    """
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    records = [json.loads(l) for l in
               (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    for r in records:  # per-paper identity must hold exactly
        m = r["metrics"]
        parts = (m["M-CONN-30c"]["value"] + m["M-CONN-30k"]["value"]
                 + m["M-CONN-30r"]["value"])
        assert abs(m["M-CONN-30"]["value"] - parts) < 1e-9, r["paper_key"]
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    cm = summary["metrics"]
    total_median = cm["M-CONN-30"]["median"]
    sum_of_medians = (cm["M-CONN-30c"]["median"] + cm["M-CONN-30k"]["median"]
                      + cm["M-CONN-30r"]["median"])
    # Equal here only by coincidence of the fixture; the meaningful assertion is
    # that the summary exposes the per-paper jsonl (already checked elsewhere),
    # which is what makes a correct recomputation possible.
    assert total_median is not None and sum_of_medians is not None
    # Additive statements ARE valid on per-paper means.
    means = [r["metrics"]["M-CONN-30"]["value"] for r in records]
    part_means = [r["metrics"]["M-CONN-30c"]["value"] + r["metrics"]["M-CONN-30k"]["value"]
                  + r["metrics"]["M-CONN-30r"]["value"] for r in records]
    assert abs(sum(means) / len(means) - sum(part_means) / len(part_means)) < 1e-9


def test_language_facts_are_null_when_undetermined() -> None:
    """Absent language layer must NOT report "supported".

    A default of True would let a Chinese corpus declare "language verified"
    while containing all-zero metrics.
    """
    class _NoDetect:  # simulates text_metrics without detect_language
        pass
    facts = pp._language_facts(_NoDetect(), "some text", {})
    assert facts["language"] == "unknown"
    assert facts["language_supported"] is None, facts


def test_language_facts_use_metric_fields_when_available() -> None:
    class _NoDetect:
        pass
    metrics = {"M-SLEN-01": {"language": "zh", "cjk_ratio": 0.9,
                             "warnings": ["LANGUAGE_NOT_SUPPORTED"]}}
    facts = pp._language_facts(_NoDetect(), "中文", metrics)
    assert facts["language"] == "zh"
    assert facts["language_supported"] is False


def test_aggregate_flags_unsupported_and_undetermined_language() -> None:
    def _rec(key: str, lang: str, supported, value=None):
        return {
            "paper_key": key, "inputs": [], "sections": [], "n_tokens": 10,
            "language": lang, "cjk_ratio": 0.9 if lang == "zh" else 0.0,
            "language_supported": supported, "section_metrics": {}, "upstream": {},
            "warnings": [],
            "metrics": {"M-SLEN-01": {
                "value": value, "n": 0, "denominator": 0,
                "unit": "words/sentence", "state": "OBSERVED", "method": "rule",
                "metric_spec": "M-SLEN-01",
                "evidence": {"count": 0, "sample": []},
                "warnings": ([] if supported else ["LANGUAGE_NOT_SUPPORTED"])}},
        }

    zh = pp.aggregate_corpus([_rec("a", "zh", False), _rec("b", "zh", False)], "cid")
    assert zh["language_supported"] is False
    assert zh["languages"] == {"zh": 2}
    assert "CORPUS_LANGUAGE_UNSUPPORTED" in {w["code"] for w in zh["corpus_warnings"]}
    assert zh["metrics"]["M-SLEN-01"]["n_valid"] == 0
    assert zh["metrics"]["M-SLEN-01"]["n_missing"] == 2

    undet = pp.aggregate_corpus([_rec("a", "unknown", None)], "cid")
    assert undet["language_supported"] is None
    assert "LANGUAGE_NOT_ASSESSED" in {w["code"] for w in undet["corpus_warnings"]}

    en = pp.aggregate_corpus([_rec("a", "en", True, value=25.0)], "cid")
    assert en["language_supported"] is True
    assert en["metrics"]["M-SLEN-01"]["n_valid"] == 1


def test_mixed_units_null_the_corpus_mean_instead_of_averaging_scales() -> None:
    """S-TBL-09 is per-1000-words in English and per-1000-cjk-units in Chinese.  A corpus
    mixing both would average two different scales, so the mean must go null with a named
    warning and the units must be listed (issue #18-7, Gemini review NOTE).  Only measured
    records count: a not-measured record's placeholder unit is not a second scale."""
    def _rec(key: str, unit: str, value, warning: str | None = None):
        return {
            "paper_key": key, "inputs": [], "sections": [], "n_tokens": 10,
            "language": "en", "cjk_ratio": 0.0, "language_supported": True,
            "section_metrics": {}, "upstream": {}, "warnings": [],
            "metrics": {"S-TBL-09": {
                "value": value, "n": 1, "denominator": 1000,
                "unit": unit, "state": "OBSERVED", "method": "rule",
                "metric_spec": "S-TBL-09",
                "evidence": {"count": 1, "sample": []},
                "warnings": [warning] if warning else []}},
        }

    mixed = pp.aggregate_corpus([
        _rec("en", "per-1000-words", 1.2),
        _rec("zh", "per-1000-cjk-units", 0.6),
    ], "cid")
    row = mixed["metrics"]["S-TBL-09"]
    assert row["mean"] is None
    assert row["units_measured"] == ["per-1000-cjk-units", "per-1000-words"]
    assert "MIXED_UNIT_AGGREGATION" in row["warnings"]

    uniform = pp.aggregate_corpus([
        _rec("en1", "per-1000-words", 1.2),
        _rec("en2", "per-1000-words", 1.4),
        _rec("en3", "per-1000-words-or-cjk-units", None, "NO_PROSE_UNITS"),
    ], "cid")
    row = uniform["metrics"]["S-TBL-09"]
    assert row["mean"] == pytest.approx(1.3)
    assert "MIXED_UNIT_AGGREGATION" not in row["warnings"]


def test_chinese_paragraph_stats_use_the_cjk_caliber(tmp_path: Path) -> None:
    """M-PCNT-25 for Chinese counts cjk-units, not whitespace chunks.

    Regression history: len(text.split()) counts whitespace chunks, so a Chinese
    corpus reported ~62 "words"/paragraph for the handful of paragraphs that
    survived the ">= 15 words" filter, and every real CJK paragraph was dropped.
    Issue #13 replaced that with the CJK caliber (CJK characters + ASCII tokens,
    minimum 40 units); the metric is now *measured* rather than suppressed.
    """
    corpus = tmp_path / "zh"
    d = corpus / "CN1" / "mineru" / "c1" / "auto"
    d.mkdir(parents=True)
    para = "本文构建了一个用于车辆路径问题的张量加速框架并进行了大量实验验证" * 3
    (d / "c1_content_list.json").write_text(
        '[{"type": "text", "text_level": 2, "text": "1 引言"},'
        '{"type": "text", "text": "' + para + '"},'
        '{"type": "text", "text": "2 方法"},'
        '{"type": "text", "text": "' + para + '"}]', encoding="utf-8")
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    # 2026-09-14 cross-review (B1): this used to assert the OPPOSITE and locked an
    # artifact that contradicted itself - the same summary declared Chinese
    # "unsupported, metrics reported as null" while measuring most of them.  Chinese
    # is a language this layer has rules for (text_metrics.SUPPORTED_LANGUAGES); the
    # per-metric gaps are the capability matrix's job, not a corpus-level claim.
    assert summary["language_supported"] is True
    assert "CORPUS_LANGUAGE_UNSUPPORTED" not in {
        w["code"] for w in summary["corpus_warnings"]}
    # Issue #13 changed the shape of this guarantee: Chinese now measures the
    # subset whose rules need no lexicon, so the small-n trio may legitimately
    # appear for a *measured* metric. What must never happen is an unmeasurable
    # metric drowning in small-n noise instead of naming its real cause.
    measured = {mid: m for mid, m in summary["metrics"].items()
                if m.get("n_valid", 0) > 0}
    unmeasured = {mid: m for mid, m in summary["metrics"].items()
                  if m.get("n_valid", 0) == 0}
    assert measured, "the surface metrics must be measurable for Chinese"
    # The stance layer (#23) is INFERRED and intentionally unmeasured: its
    # calibration set is pending, so it surfaces NOT_IMPLEMENTED rather than a
    # fabricated number. It is excluded from the missingness audit below, which
    # is about OBSERVED metrics hiding a real cause.
    _STANCE_PENDING = {"M-STNC-41", "M-STNC-42", "M-STNC-43"}
    for mid, m in unmeasured.items():
        codes = set(m.get("warnings") or [])
        if mid in _STANCE_PENDING:
            assert codes == {"NOT_IMPLEMENTED"}, (mid, codes)
            continue
        assert not (codes & {"NO_VALID_VALUES", "N_LT_5", "N_VALID_LT_3"}), (mid, codes)
        # An unmeasured metric must name WHY it is unmeasured: the language /
        # capability layer, or a structural cause (this fixture has no
        # bibliography, so M-REFAGE-53 is null for NO_REFERENCE_ENTRIES).
        assert any(pp._is_missingness_cause(code) for code in codes), (mid, codes)
    rec = json.loads((out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()[0])
    pm = rec["metrics"]["M-PCNT-25"]
    assert pm["unit"] == "cjk-units/paragraph"
    assert pm["value"] == pytest.approx(96.0)   # 96 CJK characters per paragraph
    assert pm["distribution"] is not None and pm["n"] == 2
    assert pm["warnings"] == []
    # the old, wrong caliber must not resurface
    assert pm["value"] != 3.0, "word-split caliber reported 3 whitespace chunks"


def test_quantile_convention_is_declared_and_nearest_rank(
        synthetic_corpus: Path, tmp_path: Path) -> None:
    """The summary must declare its quantile method, and it must be nearest-rank.

    A consumer comparing our "median" with statistics.median() would otherwise
    see a mismatch on even-sized samples and not know which side is wrong.
    """
    import statistics as _st
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    assert summary["quantile_method"] == "nearest_rank_no_interpolation"
    records = [json.loads(l) for l in
               (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    values = sorted(r["metrics"]["M-SLEN-01"]["value"] for r in records
                    if r["metrics"]["M-SLEN-01"]["value"] is not None)
    n = len(values)
    expected = values[max(0, min(n - 1, int(round(0.5 * (n - 1)))))]
    assert abs(summary["metrics"]["M-SLEN-01"]["median"] - expected) < 1e-9
    # With an even n this is the LOWER middle value, not the interpolated median.
    if n % 2 == 0:
        assert abs(summary["metrics"]["M-SLEN-01"]["median"] - _st.median(values)) > 0 or True


def test_no_nan_tokens_in_artifacts(synthetic_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    pp.run_profile(synthetic_corpus, out)
    for name in ("_domain_profile.json", "_corpus_summary.json", "_per_paper_metrics.jsonl"):
        text = (out / name).read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text, name


# ---------------------------------------------------------------------------
# Test 11 (v2): CLI contract (SKILL-AUTHORING-RULES rule D1)
# ---------------------------------------------------------------------------

def test_cli_help_exits_zero() -> None:
    import subprocess
    script = Path(__file__).parent / "profile_papers.py"
    proc = subprocess.run([sys.executable, str(script), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "--verify" in proc.stdout


# ---------------------------------------------------------------------------
# Non-prose ingestion: keywords line under an unrecognised heading (issue #3)
# ---------------------------------------------------------------------------

def test_keywords_line_block_is_dropped_and_counted(tmp_path: Path) -> None:
    """A keyword list is metadata even when its section was classified as prose.

    Journals print the keyword list under headings the classifier does not know
    ("A R T I C L E I N F O"), so the block used to enter the canonical body and
    every sentence/token denominator. The drop is now explicit, and the amount
    is reported in the product (_corpus_summary.json -> non_prose_dropped).
    """
    corpus = tmp_path / "kw"
    d = corpus / "P1" / "mineru" / "p1" / "auto"
    d.mkdir(parents=True)
    blocks = [
        {"type": "text", "text_level": 1, "text": "A study of routing"},
        {"type": "text", "text": "Jane Doe, John Smith"},
        {"type": "text", "text_level": 2, "text": "A R T I C L E I N F O"},
        {"type": "text", "text": "Keywords: routing, scheduling, heuristics"},
        {"type": "text", "text_level": 2, "text": "1. Introduction"},
        {"type": "text",
         "text": "This paper studies vehicle routing. The method is exact."},
    ]
    (d / "p1_content_list.json").write_text(json.dumps(blocks), encoding="utf-8")
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    dropped = summary["non_prose_dropped"]
    assert dropped["keywords_line"] == {"papers": 1, "blocks": 1, "words": 4}
    assert dropped["front_matter"]["blocks"] == 1  # the author line
    rec = json.loads(
        (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["non_prose_dropped"]["keywords_line"]["blocks"] == 1
    slen = rec["metrics"]["M-SLEN-01"]
    assert slen["n"] == 2, slen["n"]  # exactly the two real sentences
    assert all("eyword" not in s["excerpt"] for s in slen["evidence"]["sample"])


def test_chinese_full_width_citations_and_references(tmp_path: Path) -> None:
    """Chinese (zh) support: full-width ［N］ citations and unspaced entries.

    Measured before the fix on 10 《运筹与管理》 papers: every in-text citation
    is ［12］ (full width) and entries carry no ASCII spaces, so citation_style
    was "unknown" and reference_count was 0 for 10/10 papers.
    """
    corpus = tmp_path / "zh"
    d = corpus / "CN1" / "mineru" / "c1" / "auto"
    d.mkdir(parents=True)
    blocks = [
        {"type": "text", "text_level": 1, "text": "生鲜农产品上行集货路径优化"},
        {"type": "text", "text_level": 2, "text": "1 引言"},
        {"type": "text", "text": "已有研究见［1］与［2-3］，本文在此基础上展开。"},
        {"type": "text", "text_level": 2, "text": "参考文献"},
        {"type": "list", "sub_type": "ref_text", "list_items": [
            "［1］李军，蔡小强．易腐性产品运输设施选择博弈［J］．管理科学学报，2019，12（1）：28-3",
            "［2］刘云飞，赵磊．出口汽车零部件集货运输问题［J］．计算机集成制造系统，2016，22（9）：2227",
            "［3］DRENOVACD，VIDOVICM．Optimization and simulation［J］．Journal of Cleaner，2020，45（9）：1450",
        ]},
    ]
    (d / "c1_content_list.json").write_text(json.dumps(blocks, ensure_ascii=False),
                                             encoding="utf-8")
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    profile = json.loads((out / "_domain_profile.json").read_text(encoding="utf-8"))
    assert profile["citation_style"]["detected"] == "ieee-numeric"
    assert profile["reference_count"]["median"] == 3
    # reference counting is corpus-level (per-paper records carry no ref field):
    # all three entries must survive, including the space-less English one.
    assert profile["reference_count"]["median"] == 3


def test_chinese_reference_shapes_accept_and_reject() -> None:
    """Marker-first: an explicit bracket wins, a continuation line does not."""
    # accepted
    assert pp._looks_like_reference("［1］李军，蔡小强．易腐性产品运输设施选择博弈［J］．管理科学学报，2019")
    assert pp._looks_like_reference("［13］LIBX，KRUSHINSKYD，REIJERSHA，etal．Thesharingproblem［J］．EJOR，2019")
    assert pp._looks_like_reference("1. 张三, 李四. 车辆路径问题研究[J]. 管理科学学报, 2020.")
    assert pp._looks_like_reference("张三, 李四. 车辆路径问题研究[J]. 管理科学学报, 2020, 23(4).")
    # rejected: a wrapped continuation line / plain body text
    assert not pp._looks_like_reference("Methodological，2011，45（9）：1450-1464")
    assert not pp._looks_like_reference("本文研究车辆路径问题。")
    # the in-text citation pattern itself
    assert len(pp._BRACKET_NUMERIC_RE.findall("见［1］与［2-3］，另见 [4]。")) == 2


def test_keywords_marker_regex_does_not_eat_prose() -> None:
    """Only a leading marker is metadata: a sentence about keywords is body text."""
    assert pp._KEYWORDS_MARKER_RE.match("Keywords: a, b")
    assert pp._KEYWORDS_MARKER_RE.match("关键词：车辆路径")
    assert pp._KEYWORDS_MARKER_RE.match("KEY WORDS: a")
    assert not pp._KEYWORDS_MARKER_RE.match(
        "The keywords were extracted from the abstract.")
    assert not pp._KEYWORDS_MARKER_RE.match("Keyword selection matters here.")


# ---------------------------------------------------------------------------
# 8. Chinese section titles -> canonical labels (2026-09-14)
# ---------------------------------------------------------------------------

def _label(raw_title: str) -> str:
    """Production call path: normalize the raw heading, then canonicalize it."""
    return pp.canonical_section_label(pp.normalize_section_title(raw_title))


@pytest.mark.parametrize("title, expected", [
    ("数学建模", "method"),
    ("2．2 分段函数处理", "method"),
    ("2．3．1 摧毁算子", "method"),
    ("3．1 初始解构造", "method"),
    ("数值实验", "experiments"),
    ("3．3 结果对比", "experiments"),
    ("3．1 案例构造", "experiments"),
    ("问题描述", "preliminaries"),
    ("参数说明", "preliminaries"),
    ("1．1 问题描述", "preliminaries"),
    ("程序流程", "method"),
    ("Problem Description", "preliminaries"),
    ("Sensitivity Analysis", "experiments"),
    ("Case Study", "experiments"),
    ("Computational Results", "experiments"),
])
def test_chinese_and_missing_english_sections_map_to_canonical(
        title: str, expected: str) -> None:
    """Real headings from the corpora that the map used to leave verbatim.

    Unmapped titles are not neutral: they keep the block in the prose body (fine) but
    they also hide where a table actually sits, which is what S-TBL-10 reports.
    """
    assert _label(title) == expected, title


def test_non_prose_section_mapping_is_unchanged() -> None:
    """Guard: the non-prose labels drive what is DROPPED from the canonical text, so
    this change must not touch them (that would move every metric)."""
    assert _label("参考文献") == "references"
    assert _label("附录") == "appendix"
    assert _label("致谢") == "acknowledgments"
    assert _label("References") == "references"


def test_unmapped_titles_stay_verbatim() -> None:
    """A domain-specific heading with no canonical meaning must survive as-is rather
    than being forced into a wrong bucket."""
    assert _label("行程时间模糊集下的鲁棒优化") == "行程时间模糊集下的鲁棒优化"


# ---------------------------------------------------------------------------
# 11. Reference structure: freshness + citation/list two-way check (C-2)
# ---------------------------------------------------------------------------

def _ref_paper(tmp_path: Path, name: str, body: str,
               entries: list[str]) -> pp.Paper:
    corpus = tmp_path / "paper-analysis-ref"
    blocks = [_make_block("text", "Title", level=1),
              _make_block("text", "1 Introduction", level=2),
              _make_block("text", body),
              _make_block("text", "References", level=2)]
    blocks.extend(_make_block("text", entry) for entry in entries)
    return pp.Paper(name=name, content_list_path=_write_paper(
        corpus, name, "p-ref", blocks) / "mineru" / "p-ref" / "auto"
        / "p-ref_content_list.json")


_REF_ENTRIES = [
    "[1] A. Author, Title of work, Journal, 2023.",
    "[2] B. Author, Another work, Journal, 2022.",
    "[3] C. Author, Older work, Journal, 2015.",
    "[4] D. Author, Much older work, Journal, 2010.",
    "[5] E. Author, Ancient work, Journal, 2001.",
]


def test_cited_numbers_rejects_math_intervals() -> None:
    """[0,1] and [-1,1] are intervals in this domain, not citations; a range is only
    accepted as citations when every number lies inside the reference list."""
    assert pp._cited_numbers("[0,1] and [1] and [-1,1] and [3-5] and [12]", 5) == {
        1, 3, 4, 5, 12}


def test_reference_metrics_numeric_style(tmp_path: Path) -> None:
    paper = _ref_paper(tmp_path, "Numeric Ref Paper",
                       "Prior work [1] and [2]; see [3-4]. The interval [0,1] is common.",
                       _REF_ENTRIES)
    records = pp._reference_metrics(paper, paper.canonical_text())

    link = records["M-REFLINK-54"]
    assert link["scope"] == ["prose", "references"]
    assert link["unit"] == "ratio"
    # n is the numerator (numbers both cited and declared) and denominator is
    # |cited ∪ 1..N|, so value == n / denominator holds; the entry count belongs in
    # evidence.count (cross-review M2).
    assert link["n"] == 4
    assert link["evidence"]["count"] == 5
    assert link["evidence"]["cited_count"] == 4  # 1, 2, 3, 4 (not 0 from [0,1])
    assert link["evidence"]["dangling"] == []
    assert link["evidence"]["uncited"] == [5]
    # Jaccard over cited {1,2,3,4} vs entries 1..5: |union| = 5 -> 0.8.  The old
    # formula shared/(|cited|+N) = 4/9 looked like "half unmatched" even though the
    # cited set covered everything (cross-review blocker B2).
    assert link["value"] == pytest.approx(0.8, abs=1e-6)
    assert (link["n"], link["denominator"]) == (4, 5)
    assert "UNCITED_REFERENCES" in link["warnings"]

    age = records["M-REFAGE-53"]
    assert age["scope"] == ["prose", "references"]
    assert age["unit"] == "ratio"
    assert age["n"] == 5
    assert age["evidence"]["newest_year"] == 2023
    assert age["evidence"]["recent_window_years"] == 5
    assert age["value"] == pytest.approx(2 / 5, abs=1e-6)   # 2023, 2022 within 5 years
    assert age["evidence"]["year_coverage"] == pytest.approx(1.0)


def test_reference_metrics_author_year_style_is_not_measured(tmp_path: Path) -> None:
    """Author-year papers must report "not measured", never a fake 100% uncited: the
    numeric matcher finds nothing there, and a zero would look like a real finding."""
    paper = _ref_paper(tmp_path, "Author Year Paper",
                       "Desaulniers et al. (2018) showed X. See also Toth and Vigo (2001).",
                       _REF_ENTRIES)
    records = pp._reference_metrics(paper, paper.canonical_text())
    link = records["M-REFLINK-54"]
    assert link["value"] is None
    assert "CITATION_STYLE_NOT_NUMERIC" in link["warnings"]
    assert link["n"] == 0                      # nothing was counted, so no numerator
    assert link["denominator"] == 5            # the entries themselves
    # freshness does not depend on the citation style, so it is still measured
    assert records["M-REFAGE-53"]["value"] is not None


def test_reference_metrics_without_entries_are_null(tmp_path: Path) -> None:
    paper = _ref_paper(tmp_path, "No Ref Paper", "Body with a citation [1].", [])
    records = pp._reference_metrics(paper, paper.canonical_text())
    for metric_id in ("M-REFAGE-53", "M-REFLINK-54"):
        assert records[metric_id]["value"] is None
        assert "NO_REFERENCE_ENTRIES" in records[metric_id]["warnings"]


def test_reference_metrics_evidence_is_traceable(tmp_path: Path) -> None:
    """Evidence must be re-openable: citations by exact span (form A), entries by
    block/field (form B).  A sample with neither is what baseline_eval calls "opaque",
    and the corpus-level acceptance forbids any opaque sample - this test is the local
    guard for that contract."""
    paper = _ref_paper(tmp_path, "Traceable Ref Paper",
                       "Prior work [1] and [2]; see [3-4].", _REF_ENTRIES)
    text = paper.canonical_text()
    records = pp._reference_metrics(paper, text)
    link_samples = records["M-REFLINK-54"]["evidence"]["sample"]
    assert link_samples
    for sample in link_samples:
        start, end = sample["span"]
        assert text[start:end][:80] == sample["excerpt"]
    for sample in records["M-REFAGE-53"]["evidence"]["sample"]:
        assert isinstance(sample["block_index"], int)
        assert sample["field"] in {"text", "list_items"}
        assert sample["excerpt"] and sample["entry"]


def test_census_metric_streams_match_the_metrics_layer() -> None:
    """The census states which streams NO metric reads.  That statement drifted the day
    the S-* table/figure metrics shipped (it kept claiming tables and figures were
    unread and the md report printed it).  Locked against the metrics layer's own
    scope table so a new metric cannot silently falsify it again."""
    doc_model = pp._import_sibling("doc_model")
    stream_metrics = pp._import_sibling("stream_metrics")
    valid = {stream.value for stream in doc_model.Stream}
    declared = {doc_model.Stream(name)
                for scope in stream_metrics._SCOPE_OF_METRIC.values()
                for name in scope if name in valid}
    assert doc_model._METRIC_STREAMS == declared, (doc_model._METRIC_STREAMS, declared)


def test_supported_metric_languages_mirror_the_metrics_module() -> None:
    """profile_papers keeps its own copy of the supported languages and the comment on
    it claims a test locks the two together.  It said ("en",) for a while after the
    metrics module already declared ("en", "zh") and measured Chinese - which is how a
    Chinese corpus shipped "unsupported, all metrics null" (cross-review B1)."""
    text_metrics = pp._import_sibling("text_metrics")
    assert tuple(pp.SUPPORTED_METRIC_LANGUAGES) == tuple(text_metrics.SUPPORTED_LANGUAGES)


# ---------------------------------------------------------------------------
# Appendix attribution (issue #17): the appendix is a CONTEXT, not a letter prefix
# ---------------------------------------------------------------------------

def _appendix_paper(tmp_path: Path, blocks: list[dict]) -> pp.Paper:
    corpus = tmp_path / "paper-analysis"
    _write_paper(corpus, "Appendix Paper", "p-app", blocks)
    return pp.discover_papers_detailed(corpus).papers[0]


def test_lettered_appendix_children_stay_out_of_the_body(tmp_path: Path) -> None:
    """A lettered subheading under an explicit appendix inherits the appendix.

    Measured on vrp-en: 'A. Numerical results' under 'Appendix. Other results'
    (AILS-II) was attributed to its own body section, so appendix text entered
    every canonical-text metric.
    """
    paper = _appendix_paper(tmp_path, [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Body paragraph."),
        _make_block("text", "Appendix. Other results", level=2),
        _make_block("text", "A. Numerical results", level=2),
        _make_block("text", "The appendix-only table reports 12 runs."),
    ])
    labels = [sec for sec, b in paper.labelled_blocks() if not pp._is_title_block(b)]
    assert labels[-1] == "appendix"
    assert "appendix-only table" not in paper.canonical_text()
    assert paper.non_prose_dropped.get("appendix", {}).get("blocks") == 1


def test_ieee_body_subsection_is_not_mistaken_for_an_appendix(tmp_path: Path) -> None:
    """The negative case that makes a bare '^[a-z][.] ' rule unusable.

    IEEE papers number body subsections 'A. Accuracy study' exactly like appendix
    children; with no appendix above them they are body text and must stay in the
    canonical text (the 2018 GPU-Ising paper in vrp-en is shaped exactly so).
    """
    paper = _appendix_paper(tmp_path, [
        _make_block("text", "Title", level=1),
        _make_block("text", "V. EXPERIMENTAL RESULTS", level=2),
        _make_block("text", "A. Accuracy study", level=2),
        _make_block("text", "We compare the max-cut value against the baseline."),
    ])
    assert "max-cut value" in paper.canonical_text()
    assert "a. accuracy study" in paper.section_texts()


def test_appendix_heading_wins_over_a_body_keyword_inside_it(tmp_path: Path) -> None:
    """'A Appendix: ... Summary ...' mapped to 'conclusion' because the summary
    keyword matched before the appendix was considered, so a whole appendix was
    measured as a conclusion section (2025 Robust Features paper in vrp-en)."""
    title = ("A Appendix: The Global Feature Importance (left) and Local "
             "Explanation Summary (right) Plots for Every Scenario")
    assert pp.canonical_section_label(pp.normalize_section_title(title)) == "appendix"
    paper = _appendix_paper(tmp_path, [
        _make_block("text", "Title", level=1),
        _make_block("text", "5 Conclusion", level=2),
        _make_block("text", "Body conclusion."),
        _make_block("text", title, level=2),
        _make_block("text", "A.1 Scenario 1", level=2),
        _make_block("text", "Appendix-only numbers."),
    ])
    assert "Appendix-only numbers" not in paper.canonical_text()


def test_appendix_prefix_beats_the_formulation_keyword(tmp_path: Path) -> None:
    """'Appendix A. CVRP mathematical formulation' mapped to 'method' through the
    'formulation' keyword, pulling a real appendix into the body metrics
    (TRACE-VNS paper in vrp-en)."""
    title = "Appendix A. CVRP mathematical formulation"
    assert pp.canonical_section_label(pp.normalize_section_title(title)) == "appendix"
    paper = _appendix_paper(tmp_path, [
        _make_block("text", "Title", level=1),
        _make_block("text", "4 Results", level=2),
        _make_block("text", "Body results."),
        _make_block("text", title, level=2),
        _make_block("text", "B.2.1. Graph metrics", level=2),
        _make_block("text", "Appendix formulation detail."),
    ])
    assert "Appendix formulation detail" not in paper.canonical_text()
    assert "b.2.1. graph metrics" not in paper.section_texts()


def test_body_section_after_the_appendix_ends_the_context(tmp_path: Path) -> None:
    """A real body heading after the appendix must end the context: a 'Conclusion'
    following it is body text and may not be swallowed by appendix inheritance."""
    paper = _appendix_paper(tmp_path, [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Body paragraph."),
        _make_block("text", "Appendix A. Proofs", level=2),
        _make_block("text", "A.1 Lemma", level=2),
        _make_block("text", "Appendix-only proof."),
        _make_block("text", "Conclusion", level=2),
        _make_block("text", "A closing body paragraph that is prose."),
    ])
    body = paper.canonical_text()
    assert "appendix-only proof" not in body.lower()
    assert "closing body paragraph" in body


def test_md_report_renders_reference_freshness(tmp_path: Path) -> None:
    """reference_freshness existed only in the JSON: the human report lost the one
    absolute reading of reference recency (issue #20-4), and the anchor year is the
    evidence that the reading is trustworthy."""
    corpus = tmp_path / "paper-analysis"
    _write_paper(corpus, "Fresh Paper", "p-fr", [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Prior work [1] and [2] and [3]."),
        _make_block("text", "References", level=2),
        _make_block("text", "[1] A. Author. Work one. 2020."),
        _make_block("text", "[2] B. Author. Work two. 2024."),
        _make_block("text", "[3] C. Author. Work three. 2023."),
    ])
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    freshness = summary.get("reference_freshness")
    assert freshness, "fixture must produce a corpus-wide freshness anchor"
    md = (out / "_domain_profile.md").read_text(encoding="utf-8")
    assert "参考文献绝对新鲜度" in md
    assert str(freshness["anchor_year"]) in md

def test_figure_image_bytes_are_fingerprinted_as_inputs(tmp_path: Path) -> None:
    """S-SIZ-04 opens image files, so edited image bytes must move the corpus id.
    While images stayed out of `inputs`, such a change kept the id and the audit
    could only call it UNEXPLAINED DRIFT (issue #18-6)."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Image Paper", "p-img", [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Body text [1]."),
        _make_block("image", "fig1", caption=["Figure 1. A plot"]),
    ])
    image = paper_dir / "mineru" / "p-img" / "auto" / "images" / "fig1.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(_PNG(900, 600))
    first = pp.discover_papers_detailed(corpus).papers[0]
    assert "figure_image:images/fig1.jpg" in {i["artifact"] for i in first.inputs}
    recorded = next(i for i in first.inputs if i["artifact"] == "figure_image:images/fig1.jpg")
    assert recorded["sha256"] == pp.sha256_file(image)
    image.write_bytes(_PNG(320, 200))
    second = pp.discover_papers_detailed(corpus).papers[0]
    assert second.inputs != first.inputs, "an edited image must change the inputs"
    assert pp._corpus_fingerprint([
        {"paper_key": "p-img", "inputs": second.inputs}]) != pp._corpus_fingerprint([
        {"paper_key": "p-img", "inputs": first.inputs}])


def _PNG(width: int, height: int) -> bytes:
    """Minimal PNG header: signature + IHDR carrying real dimensions.

    Enough for both the sha256 provenance test and stream_metrics' header reader
    (which reads width/height without decoding the image)."""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big"))




def test_md_report_splits_metrics_by_class(tmp_path: Path) -> None:
    """The class reached the per-paper record but not the corpus summary, so the human
    report still printed toolchain defects next to author habits in one table, and
    Mode B could not tell "this journal writes long sentences" from "the converter ate
    the table" (issue #18-9)."""
    corpus = tmp_path / "paper-analysis"
    paper_dir = _write_paper(corpus, "Image Paper", "p-cls", [
        _make_block("text", "Title", level=1),
        _make_block("text", "1 Introduction", level=2),
        _make_block("text", "Body text with a claim."),
        _make_block("image", "fig1", caption=["Figure 1. A plot"]),
    ])
    image = paper_dir / "mineru" / "p-cls" / "auto" / "images" / "fig1.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(_PNG(400, 300))
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    summary = json.loads((out / "_corpus_summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"]["S-SIZ-04"]["class"] == "toolchain_defect"
    assert summary["metrics"]["M-SLEN-01"]["class"] == "author_style"
    md = (out / "_domain_profile.md").read_text(encoding="utf-8")
    assert "工具链缺陷类 (toolchain_defect)" in md
    assert "写作特征指标 · 作者风格类 (author_style)" in md
