#!/usr/bin/env python3
"""Tests for baseline_eval.py (I12).

Two groups:

  * Pure-function tests (no corpus needed): hashing, two-run comparison,
    fingerprint-scope leak scan, evidence trace-back, metric contract check,
    acceptance logic, golden table extraction, markdown rendering.
  * One end-to-end test that runs the real 36-paper corpus twice and asserts the
    full INTERFACES sec.6 acceptance set. It is skipped (never failed) when the
    corpus is absent or the sibling profiler modules have not landed yet, so the
    unit suite stays green during parallel development.

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_baseline_eval.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import baseline_eval as be  # noqa: E402

REAL_CORPUS = Path(os.environ.get(
    "BASELINE_CORPUS",
    "/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis"))


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def _metric(mid: str, value=0.5, n=3, denominator=10, unit="ratio",
            sample=None, state="OBSERVED") -> dict:
    return {
        "value": value, "n": n, "denominator": denominator, "unit": unit,
        "state": state, "method": "rule", "metric_spec": mid,
        "evidence": {"count": n, "sample": sample if sample is not None else []},
        "warnings": [],
    }


def _record(paper_key: str = "paper-a", metrics=None, text: str = "") -> dict:
    return {
        "paper_key": paper_key,
        "inputs": [{"artifact": "mineru_content_list", "sha256": "0" * 64}],
        "sections": ["introduction", "method"],
        "metrics": metrics if metrics is not None else {"M-HED-14": _metric("M-HED-14")},
        "warnings": [],
    }


def _summary(metrics: dict | None = None) -> dict:
    return {
        "schema_version": "2.0",
        "analysis_unit": "paper",
        "weight_mode": "equal_paper",
        "quantile_method": "nearest_rank_no_interpolation",
        "n_papers": 2,
        "metrics": metrics or {
            "M-HED-14": {
                "unit": "per-1000-words", "n_valid": 2, "n_missing": 0,
                "mean": 0.0184, "sd": 0.001, "median": 0.018, "p25": 0.017,
                "p75": 0.019, "iqr": 0.002, "min": 0.017, "max": 0.019,
                "ci95_low": 0.01, "ci95_high": 0.02, "by_section": {},
                "warnings": [],
            }
        },
        "corpus_warnings": [],
    }


def _profile() -> dict:
    return {
        "schema_version": "2.0",
        "metric_spec_version": "1.0",
        "toolchain": {"lexicon_fingerprint": "abc123", "lexicon_version": "1.0"},
        "meta": {"profiler_version": "2.0", "paper_count": 2},
        "corpus": {"id": "corpus-hash", "n_papers": 2, "skipped": []},
        "reference_count": {"median": 41, "p25": 30, "p75": 55},
    }


def _write(dirpath: Path, name: str, text: str) -> Path:
    p = dirpath / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Hashing / IO
# ---------------------------------------------------------------------------

def test_sha256_file_missing_returns_none(tmp_path):
    assert be.sha256_file(tmp_path / "nope.json") is None


def test_sha256_file_is_stable_and_content_sensitive(tmp_path):
    a = _write(tmp_path, "a.json", "hello")
    b = _write(tmp_path, "b.json", "hello")
    c = _write(tmp_path, "c.json", "hello!")
    assert be.sha256_file(a) == be.sha256_file(b)
    assert be.sha256_file(a) != be.sha256_file(c)
    assert len(be.sha256_file(a)) == 64


def test_load_jsonl_skips_blank_lines(tmp_path):
    p = _write(tmp_path, "x.jsonl", '{"a": 1}\n\n{"a": 2}\n')
    assert be.load_jsonl(p) == [{"a": 1}, {"a": 2}]


# ---------------------------------------------------------------------------
# Two-run comparison
# ---------------------------------------------------------------------------

def test_compare_fingerprints_identical_dirs(tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    d1.mkdir(); d2.mkdir()
    for name in be.FINGERPRINTED_FILES:
        _write(d1, name, "same-bytes")
        _write(d2, name, "same-bytes")
    result = be.compare_fingerprints(d1, d2)
    assert set(result) == set(be.FINGERPRINTED_FILES)
    for info in result.values():
        assert info["byte_identical"] is True
        assert info["sha256_run1"] == info["sha256_run2"]


def test_compare_fingerprints_detects_divergence(tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    d1.mkdir(); d2.mkdir()
    for name in be.FINGERPRINTED_FILES:
        _write(d1, name, "run1")
        _write(d2, name, "run2")
    result = be.compare_fingerprints(d1, d2)
    assert all(v["byte_identical"] is False for v in result.values())


def test_compare_fingerprints_missing_file_not_identical(tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    d1.mkdir(); d2.mkdir()
    _write(d1, "_domain_profile.json", "{}")
    result = be.compare_fingerprints(d1, d2)
    info = result["_domain_profile.json"]
    assert info["present_run1"] is True and info["present_run2"] is False
    assert info["byte_identical"] is False


# ---------------------------------------------------------------------------
# Fingerprint-scope leak scan
# ---------------------------------------------------------------------------

def test_scan_volatile_leaks_flags_absolute_corpus_path(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    out = tmp_path / "out"; out.mkdir()
    _write(out, "_domain_profile.json", '{"p": "%s"}' % corpus.resolve())
    leaks = be.scan_volatile_leaks(out, corpus)
    assert any(l["code"] == "corpus_absolute_path" for l in leaks)


def test_scan_volatile_leaks_flags_timestamp_and_host(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    out = tmp_path / "out"; out.mkdir()
    _write(out, "_corpus_summary.json", '{"t": "2026-09-12T08:00:00"}')
    _write(out, "_per_paper_metrics.jsonl", '{"h": "some-host"}')
    leaks = be.scan_volatile_leaks(out, corpus)
    assert any(l["code"] == "iso_timestamp" for l in leaks)


def test_scan_volatile_leaks_clean_output(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    out = tmp_path / "out"; out.mkdir()
    for name in be.FINGERPRINTED_FILES:
        _write(out, name, '{"value": 0.5}')
    assert be.scan_volatile_leaks(out, corpus) == []


# ---------------------------------------------------------------------------
# Metric contract compliance
# ---------------------------------------------------------------------------

def test_check_metric_contract_compliant_records():
    res = be.check_metric_contract([_record()])
    assert res["violation_count"] == 0
    assert res["states_seen"] == ["OBSERVED"]
    assert res["metric_dicts_checked"] == 1


def test_check_metric_contract_flags_missing_field():
    bad = _metric("M-HED-14")
    del bad["denominator"]
    res = be.check_metric_contract([_record(metrics={"M-HED-14": bad})])
    assert res["violation_count"] == 1
    assert "denominator" in res["violations"][0]["missing"]


def test_check_metric_contract_flags_metric_spec_mismatch():
    bad = _metric("M-HED-14")
    bad["metric_spec"] = "M-BOO-15"
    res = be.check_metric_contract([_record(metrics={"M-HED-14": bad})])
    assert res["violation_count"] == 1
    assert res["violations"][0]["detail"] == "metric_spec != metric_id"


def test_check_metric_contract_reports_non_observed_state():
    bad = _metric("M-HED-14", state="INFERRED")
    res = be.check_metric_contract([_record(metrics={"M-HED-14": bad})])
    assert res["states_seen"] == ["INFERRED"]
    assert res["violation_count"] == 0  # field set is complete; state is caught by A-observed-only


# ---------------------------------------------------------------------------
# D4: quantile recomputation under the frozen nearest-rank convention
# ---------------------------------------------------------------------------

def test_nearest_rank_even_n_takes_lower_middle():
    vals = sorted([23.333333, 23.230769])          # n=2, p=0.5 -> idx round(0.5)=0
    assert be.nearest_rank(vals, 0.5) == 23.230769
    # and that is NOT the interpolated median
    assert be.nearest_rank(vals, 0.5) != (vals[0] + vals[1]) / 2


def test_nearest_rank_34_case_matches_lower_middle():
    vals = sorted(float(i) for i in range(34))     # 0..33, n=34
    assert be.nearest_rank(vals, 0.5) == vals[16]  # int(round(16.5)) == 16
    assert be.nearest_rank(vals, 0.25) == vals[round(0.25 * 33)]
    assert be.nearest_rank(vals, 0.75) == vals[round(0.75 * 33)]


def test_nearest_rank_empty_is_none():
    assert be.nearest_rank([], 0.5) is None


def _records(values: dict) -> list[dict]:
    keys = max(len(v) for v in values.values())
    out = []
    for i in range(keys):
        metrics = {}
        for mid, vals in values.items():
            metrics[mid] = {"value": vals[i] if i < len(vals) else None}
        out.append({"paper_key": "p%02d" % i, "metrics": metrics})
    return out


def test_recompute_quantiles_matches_declared_summary():
    vals = [1.0, 2.0, 3.0, 4.0]
    recs = _records({"M-X": vals})
    summary = {"quantile_method": be.NEAREST_RANK_METHOD,
               "metrics": {"M-X": {"p25": be.nearest_rank(sorted(vals), 0.25),
                                   "median": be.nearest_rank(sorted(vals), 0.5),
                                   "p75": be.nearest_rank(sorted(vals), 0.75)}}}
    res = be.recompute_quantiles(recs, summary)
    assert res["all_match"] is True
    assert res["quantiles_checked"] == 3
    assert res["declaration_ok"] is True


def test_recompute_quantiles_flags_interpolated_summary():
    # An interpolated median (what statistics.median would report) must be caught.
    vals = [1.0, 2.0, 3.0, 4.0]
    interpolated = (vals[1] + vals[2]) / 2
    recs = _records({"M-X": vals})
    summary = {"quantile_method": be.NEAREST_RANK_METHOD,
               "metrics": {"M-X": {"p25": be.nearest_rank(sorted(vals), 0.25),
                                   "median": interpolated,
                                   "p75": be.nearest_rank(sorted(vals), 0.75)}}}
    res = be.recompute_quantiles(recs, summary)
    assert res["all_match"] is False
    assert any(m["quantile"] == "median" for m in res["mismatches"])


def test_recompute_quantiles_flags_missing_method_declaration():
    recs = _records({"M-X": [1.0, 2.0]})
    summary = {"metrics": {"M-X": {"p25": 1.0, "median": 1.0, "p75": 2.0}}}
    res = be.recompute_quantiles(recs, summary)
    assert res["declaration_ok"] is False
    assert any(m["quantile"] == "quantile_method" for m in res["mismatches"])


def test_recompute_quantiles_skips_all_null_metric():
    recs = _records({"M-X": [None, None]})
    summary = {"quantile_method": be.NEAREST_RANK_METHOD,
               "metrics": {"M-X": {"p25": None, "median": None, "p75": None}}}
    res = be.recompute_quantiles(recs, summary)
    assert res["quantiles_checked"] == 0


# ---------------------------------------------------------------------------
# Evidence trace-back
# ---------------------------------------------------------------------------

def _ctx(text: str = "", blocks=None, sha_match=True) -> dict:
    """Synthetic verification context (as build_paper_contexts produces)."""
    if blocks is None:
        blocks = []
    return {"paper-a": {
        "text": text,
        "blocks": blocks,
        "content_list_path": "/synthetic/content_list.json",
        "expected_sha256": "a" * 64,
        "actual_sha256": "a" * 64,
        "sha256_match": sha_match,
    }}


def test_collect_evidence_verifies_span_slices():
    # INTERFACES sec.7 form A: excerpt == text[start:end][:80]
    text = "We suggest that the method may improve accuracy."
    metric = _metric("M-HED-14", sample=[{"span": [3, 10], "excerpt": "suggest"},
                                         {"span": [27, 30], "excerpt": "may"}])
    ev = be.collect_evidence([_record(metrics={"M-HED-14": metric})], _ctx(text))
    assert ev["samples_total"] == 2
    assert ev["samples_matched"] == 2
    assert ev["traceback_rate"] == 1.0
    assert ev["form_counts"]["span"] == {"checked": 2, "matched": 2}


def test_collect_evidence_accepts_legitimately_truncated_long_span():
    # A span longer than 80 chars is only required to match its first 80 chars;
    # comparing the full slice (the old bug) would wrongly flag it as a miss.
    long_word = "a" * 120
    text = f"prefix {long_word} suffix"
    metric = _metric("M-HED-14", sample=[{"span": [7, 127], "excerpt": "a" * 80}])
    ev = be.collect_evidence([_record(metrics={"M-HED-14": metric})], _ctx(text))
    assert ev["traceback_rate"] == 1.0
    assert ev["samples"][0]["verified"] is True
    assert ev["samples"][0]["source_slice"] == "a" * 80


def test_collect_evidence_detects_bad_excerpt():
    text = "We suggest that the method may improve accuracy."
    metric = _metric("M-HED-14", sample=[{"span": [3, 10], "excerpt": "WRONG"}])
    ev = be.collect_evidence([_record(metrics={"M-HED-14": metric})], _ctx(text))
    assert ev["traceback_rate"] == 0.0
    assert ev["samples"][0]["verified"] is False
    assert ev["samples"][0]["source_slice"] == "suggest"


def test_collect_evidence_caps_samples_per_metric():
    samples = [{"span": [0, 1], "excerpt": "a"} for _ in range(10)]
    metric = _metric("M-HED-14", sample=samples)
    ev = be.collect_evidence([_record(metrics={"M-HED-14": metric})], _ctx("abc"))
    assert ev["samples_total"] == be.MAX_EVIDENCE_PER_METRIC


def test_collect_evidence_verifies_block_index_form():
    # INTERFACES sec.7 form B: blocks[i].text starts with excerpt (M-PCNT-25).
    blocks = [{"type": "text", "text": "Introduction body."},
              {"type": "text", "text": "We propose a GPU kernel that scales."}]
    metric = _metric("M-PCNT-25", sample=[{"block_index": 1, "section": "method",
                                           "words": 7,
                                           "excerpt": "We propose a GPU kernel"}])
    ev = be.collect_evidence([_record(metrics={"M-PCNT-25": metric})], _ctx("", blocks))
    assert ev["form_counts"]["block"] == {"checked": 1, "matched": 1}
    assert ev["samples"][0]["form"] == "block"
    assert ev["samples"][0]["verified"] is True


def test_collect_evidence_rejects_out_of_range_block_index():
    blocks = [{"type": "text", "text": "only block"}]
    metric = _metric("M-PCNT-25", sample=[{"block_index": 9, "excerpt": "only"}])
    ev = be.collect_evidence([_record(metrics={"M-PCNT-25": metric})], _ctx("", blocks))
    assert ev["samples"][0]["verified"] is False
    assert ev["traceback_rate"] == 0.0


def test_collect_evidence_counts_opaque_sample_as_miss():
    metric = _metric("M-X-99", sample=[{"excerpt": "no coordinates at all"}])
    ev = be.collect_evidence([_record(metrics={"M-X-99": metric})], _ctx("whatever"))
    assert ev["form_counts"]["opaque"]["checked"] == 1
    assert ev["samples_missed"] == 1
    assert ev["traceback_rate"] == 0.0


def test_collect_evidence_merges_both_forms_into_one_rate():
    text = "We suggest the method may help."
    blocks = [{"type": "text", "text": "Method paragraph starts here."}]
    metrics = {
        "M-HED-14": _metric("M-HED-14", sample=[{"span": [3, 10], "excerpt": "suggest"}]),
        "M-PCNT-25": _metric("M-PCNT-25", sample=[{"block_index": 0,
                                                   "excerpt": "Method paragraph"}]),
    }
    ev = be.collect_evidence([_record(metrics=metrics)], _ctx(text, blocks))
    assert ev["samples_total"] == 2
    assert ev["samples_matched"] == 2
    assert ev["traceback_rate"] == 1.0
    assert ev["form_counts"]["span"]["checked"] == 1
    assert ev["form_counts"]["block"]["checked"] == 1


def test_collect_evidence_records_input_sha256_provenance():
    metric = _metric("M-HED-14", sample=[{"span": [0, 1], "excerpt": "a"}])
    good = be.collect_evidence([_record(metrics={"M-HED-14": metric})], _ctx("a"))
    assert good["provenance"] == {"papers_checked": 1, "sha256_matched": 1}
    bad = be.collect_evidence([_record(metrics={"M-HED-14": metric})],
                              _ctx("a", sha_match=False))
    assert bad["provenance"] == {"papers_checked": 1, "sha256_matched": 0}


# ---------------------------------------------------------------------------
# Acceptance logic
# ---------------------------------------------------------------------------

def _inputs(**over) -> dict:
    base = {
        "determinism": {name: {"byte_identical": True, "sha256_run1": "x",
                               "sha256_run2": "x", "present_run1": True,
                               "present_run2": True}
                        for name in be.FINGERPRINTED_FILES},
        "fingerprint_scope": [],
        "evidence_verification": {
            "samples_total": 12, "samples_matched": 12, "samples_missed": 0,
            "traceback_rate": 1.0, "forms_verifiable": 12,
            "form_counts": {"span": {"checked": 10, "matched": 10},
                            "block": {"checked": 2, "matched": 2},
                            "opaque": {"checked": 0, "matched": 0}},
            "provenance": {"papers_checked": 34, "sha256_matched": 34},
            "min_samples_required": be.MIN_EVIDENCE_SAMPLES,
            "target_rate": be.MIN_TRACEBACK_RATE},
        "reference_count": {"median": 41, "p25": 30, "p75": 55},
        "contract_compliance": {"metric_dicts_checked": 100, "violation_count": 0,
                                "violations": [], "states_seen": ["OBSERVED"]},
        "quantile_recompute": {"method_declared": be.NEAREST_RANK_METHOD,
                               "method_expected": be.NEAREST_RANK_METHOD,
                               "declaration_ok": True, "quantiles_checked": 42,
                               "mismatches": [], "mismatch_count": 0,
                               "all_match": True},
        "corpus": {"n_profiled": 34, "n_skipped": 2},
    }
    base.update(over)
    return base


def test_build_acceptance_all_pass():
    checks = be.build_acceptance(_inputs())
    assert len(checks) == 8
    assert all(c["pass"] for c in checks)


def test_build_acceptance_fails_on_quantile_mismatch():
    qr = dict(_inputs()["quantile_recompute"])
    qr["all_match"] = False
    qr["mismatch_count"] = 1
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(quantile_recompute=qr))}
    assert checks["A-quantile-recompute"]["pass"] is False


def test_build_acceptance_fails_on_determinism_mismatch():
    det = _inputs()["determinism"]
    det["_corpus_summary.json"]["byte_identical"] = False
    checks = {c["id"]: c for c in be.build_acceptance(_inputs(determinism=det))}
    assert checks["A-determinism"]["pass"] is False


def test_build_acceptance_fails_on_zero_reference_count():
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(reference_count={"median": 0}))}
    assert checks["A-reference-count"]["pass"] is False


def test_build_acceptance_fails_on_imperfect_traceback():
    ev = dict(_inputs()["evidence_verification"])
    ev["traceback_rate"] = 0.769231  # the regression the Lead observed
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(evidence_verification=ev))}
    assert checks["A-evidence-traceback"]["pass"] is False


def test_build_acceptance_fails_when_nothing_is_verifiable():
    ev = dict(_inputs()["evidence_verification"])
    ev["forms_verifiable"] = 0
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(evidence_verification=ev))}
    assert checks["A-evidence-traceback"]["pass"] is False


def test_build_acceptance_fails_when_no_evidence_samples():
    ev = dict(_inputs()["evidence_verification"])
    ev["samples_total"] = 3
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(evidence_verification=ev))}
    assert checks["A-evidence-traceback"]["pass"] is False


def test_build_acceptance_fails_on_path_leak():
    checks = {c["id"]: c for c in be.build_acceptance(
        _inputs(fingerprint_scope=[{"file": "_domain_profile.json",
                                    "code": "corpus_absolute_path",
                                    "needle": "/abs/path"}]))}
    assert checks["A-fingerprint-scope"]["pass"] is False


def test_build_acceptance_fails_on_inferred_state():
    cc = dict(_inputs()["contract_compliance"])
    cc["states_seen"] = ["INFERRED", "OBSERVED"]
    checks = {c["id"]: c
              for c in be.build_acceptance(_inputs(contract_compliance=cc))}
    assert checks["A-observed-only"]["pass"] is False


# ---------------------------------------------------------------------------
# Golden table + markdown
# ---------------------------------------------------------------------------

def test_build_golden_extracts_locked_fields():
    golden = be.build_golden(_summary(), _profile())
    assert golden["metric_spec_version"] == "1.0"
    # The quantile convention must travel with the values, so a third party
    # never has to guess whether these medians are statistics.median values.
    assert golden["quantile_method"] == "nearest_rank_no_interpolation"
    assert golden["lexicon_fingerprint"] == "abc123"
    assert golden["corpus_id"] == "corpus-hash"
    g = golden["metrics"]["M-HED-14"]
    assert g["unit"] == "per-1000-words"
    assert g["median"] == 0.018
    assert g["ci95_low"] == 0.01
    assert "recompute_hint" in golden


def test_build_golden_is_json_serialisable():
    payload = json.dumps(be.build_golden(_summary(), _profile()), sort_keys=True)
    assert "M-HED-14" in payload


def test_render_markdown_documents_quantile_convention():
    md = be.render_markdown(_report_with(
        quantile_method="nearest_rank_no_interpolation"))
    assert "nearest_rank_no_interpolation" in md
    assert "不插值" in md
    assert "下中位数" in md


def test_render_markdown_contains_required_sections():
    inputs = _inputs()
    report = {
        "baseline": {"baseline_version": be.BASELINE_VERSION,
                     "schema_version": "2.0", "metric_spec_version": "1.0",
                     "profiler_version": "2.0", "lexicon_fingerprint": "abc123"},
        "corpus": {"corpus_id": "corpus-hash", "n_profiled": 34, "n_skipped": 2,
                   "skipped": []},
        "acceptance": be.build_acceptance(inputs),
        "determinism": inputs["determinism"],
        "golden": be.build_golden(_summary(), _profile()),
        "evidence_verification": inputs["evidence_verification"],
        "evidence_samples": [{"metric": "M-HED-14", "paper_key": "paper-a",
                              "form": "span", "span": [3, 10],
                              "excerpt": "suggest", "verified": True}],
        "gaps": [{"code": "N_LT_5", "metric": "M-X-01", "detail": "n_valid=3"}],
    }
    md = be.render_markdown(report)
    for needle in ("# 指标基线评测报告", "## 1. 验收检查", "## 2. 两次运行逐字节一致性",
                   "## 3. 逐指标数值", "## 4. evidence 抽样回指",
                   "M-HED-14", "PASS", "corpus-hash"):
        assert needle in md
    assert "不参与指纹" in md


def _repo_modules_ready() -> bool:
    """The profiler's sibling modules must exist before any real run is possible."""
    return ((_SCRIPTS_DIR / "text_metrics.py").exists()
            and (_SCRIPTS_DIR / "lexicon_loader.py").exists())


# ---------------------------------------------------------------------------
# V7 engine-drift probe
# ---------------------------------------------------------------------------

def test_markdown_to_text_strips_syntax():
    tick = chr(96)
    md = ("# Intro\n\nSome **bold** text with " + tick + "code" + tick
          + " and [link](http://x).\n\n![img](y.png)\n\n<html>x</html>\n\n"
          + tick * 3 + "\nraw code block\n" + tick * 3 + "\n")
    out = be.markdown_to_text(md)
    assert "Intro" in out
    assert "bold" in out and "link" in out
    assert "#" not in out
    assert "**" not in out
    assert "http://x" not in out
    assert "y.png" not in out
    assert "raw code block" not in out
    assert "code" not in out


def test_markdown_to_text_collapses_blank_runs():
    assert "\n\n\n" not in be.markdown_to_text("a\n\n\n\n\nb")


def _synthetic_corpus(tmp_path: Path, papers) -> Path:
    """papers: list of (name, has_marker) -- each gets a mineru content_list."""
    root = tmp_path / "corpus"
    for name, has_marker in papers:
        mineru = root / name / "mineru"
        mineru.mkdir(parents=True)
        # A level-2 heading is required: canonical_text() drops front_matter, so
        # a paper whose only blocks precede any heading yields empty prose.
        blocks = [{"type": "text", "text": "1 Introduction", "text_level": 2},
                  {"type": "text",
                   "text": "This body sentence is long enough to count as prose. " * 4}]
        (mineru / (name + "_content_list.json")).write_text(
            json.dumps(blocks), encoding="utf-8")
        if has_marker:
            marker = root / name / "marker"
            marker.mkdir(parents=True)
            (marker / (name + ".md")).write_text(
                "# Introduction\n\nMarker prose sentence goes here. " * 4,
                encoding="utf-8")
    return root


@pytest.mark.skipif(not _repo_modules_ready(),
                    reason="sibling profiler modules not landed yet")
def test_probe_engine_drift_pairs_papers_and_records_skips(tmp_path):
    root = _synthetic_corpus(tmp_path, [("p1", True), ("p2", False)])
    drift = be.probe_engine_drift(root, 5)
    assert drift["n_papers_compared"] == 1
    assert drift["n_papers_requested"] == 5
    assert {"paper_key": "p2", "reason": "no_marker_md"} in drift["skipped"]
    assert set(drift["summary"]) == set(be.DRIFT_METRICS)
    assert drift["summary"]["M-SLEN-01"]["n_pairs"] == 1
    row = drift["papers"][0]
    assert row["mineru_tokens"] > 0 and row["marker_tokens"] > 0
    m = row["metrics"]["M-SLEN-01"]
    assert m["mineru"] is not None and m["marker"] is not None
    assert m["delta"] == round(m["marker"] - m["mineru"], 6)
    # scope must state the text-layer limit and the missing engine attribution
    assert "text layer only" in drift["scope"]
    assert "NOT available" in drift["scope"]


@pytest.mark.skipif(not _repo_modules_ready(),
                    reason="sibling profiler modules not landed yet")
def test_probe_engine_drift_respects_paper_limit(tmp_path):
    root = _synthetic_corpus(tmp_path, [("p1", True), ("p2", True)])
    drift = be.probe_engine_drift(root, 1)
    assert drift["n_papers_compared"] == 1


def _report_with(**over) -> dict:
    inputs = _inputs()
    report = {
        "baseline": {"baseline_version": be.BASELINE_VERSION, "schema_version": "2.0",
                     "metric_spec_version": "1.0", "profiler_version": "2.0",
                     "lexicon_fingerprint": "abc123"},
        "corpus": {"corpus_id": "corpus-hash", "n_profiled": 34, "n_skipped": 2,
                   "skipped": []},
        "acceptance": be.build_acceptance(inputs),
        "determinism": inputs["determinism"],
        "golden": be.build_golden(_summary(), _profile()),
        "evidence_verification": inputs["evidence_verification"],
        "evidence_samples": [], "gaps": [],
    }
    report.update(over)
    return report


def test_render_markdown_documents_unimplemented_v7_status():
    md = be.render_markdown(_report_with(
        unicode_norm="NFC",
        upstream={"papers_with_paper_reader_meta": 34, "engine_versions_recorded": 0},
        engine_drift=None))
    assert "## 5. 上游输入与归一化" in md
    assert "NFC" in md
    assert "engine_versions_recorded: 0" in md
    assert "ENGINE_VERSION_NOT_RECORDED" in md
    assert "## 6. 引擎漂移" in md
    # V7 must be stated as NOT implemented on the profile main chain, with the
    # two specific symptoms, so a reader cannot mistake it for covered.
    assert "V7 状态：主链未实现" in md
    assert "不含任何 drift 数值" in md
    assert "没有任何指标读取它" in md


def test_render_markdown_shows_drift_table_when_probed():
    drift = {
        "scope": "text layer only; NOT available",
        "normalisation": "markdown_to_text",
        "n_papers_requested": 5, "n_papers_compared": 1, "skipped": [],
        "summary": {"M-SLEN-01": {"n_pairs": 1, "mean_signed_delta": 1.5,
                                  "mean_abs_delta": 1.5, "max_abs_delta": 1.5,
                                  "mean_rel_delta": 0.05}},
        "papers": [{"paper_key": "p1", "mineru_tokens": 100, "marker_tokens": 110,
                    "metrics": {m: {"mineru": 20.0, "marker": 21.5}
                                for m in be.DRIFT_METRICS}}],
    }
    md = be.render_markdown(_report_with(engine_drift=drift))
    assert "| M-SLEN-01 | 1 | 1.5 |" in md
    assert "20/21.5" in md
    # Enabling the optional probe must NOT read as "V7 now covered".
    assert "V7 状态：主链未实现" in md
    assert "探针结果不代表主链已覆盖 V7" in md


# ---------------------------------------------------------------------------
# End-to-end: real corpus (skipped when unavailable)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_CORPUS.is_dir(),
                    reason=f"real corpus not available: {REAL_CORPUS}")
@pytest.mark.skipif(not _repo_modules_ready(),
                    reason="sibling profiler modules (text_metrics/lexicon_loader) not landed yet")
def test_real_corpus_baseline_passes_and_is_reproducible(tmp_path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    r1 = be.run_baseline(REAL_CORPUS, out1, runs=2)
    r2 = be.run_baseline(REAL_CORPUS, out2, runs=2)

    # sec.6.2 real corpus: 36 dirs, 34 profiled
    assert r1["corpus"]["n_profiled"] >= 30
    assert r1["corpus"]["n_profiled"] == r2["corpus"]["n_profiled"]
    # sec.6.3 determinism proven inside the harness
    assert all(v["byte_identical"] for v in r1["determinism"].values())
    # sec.6.4 reference_count no longer silently zero
    assert r1["reference_count"]["median"] > 0
    # sec.6.5 traceability of sampled values
    ev = r1["evidence_verification"]
    assert ev["samples_total"] >= be.MIN_EVIDENCE_SAMPLES
    assert ev["traceback_rate"] >= be.MIN_TRACEBACK_RATE
    assert ev["forms_verifiable"] > 0
    assert ev["form_counts"]["opaque"]["checked"] == 0
    assert ev["provenance"]["sha256_matched"] == ev["provenance"]["papers_checked"]
    # sec.6.6 contract completeness on every metric dict
    assert r1["contract_compliance"]["violation_count"] == 0
    assert r1["contract_compliance"]["metric_dicts_checked"] > 0
    # The report's own acceptance set (also surfaced by the CLI exit code) checks
    # INTERFACES sec.0.3 too: no absolute path inside the fingerprinted JSON.
    # profile_papers.py currently keeps meta.corpus_path there, which sec.4.1
    # says must move to _run_meta.json (sec.4.2). That is a known cross-scope
    # deviation owned by the Lead, so this test asserts the substantive set and
    # pins the deviation to its exact shape: it stays green if the leak is fixed
    # and fails loudly if the deviation widens.
    # STRICT: the fingerprint scope must be completely clean. An earlier version
    # of this test tolerated the known corpus_absolute_path leak, which meant our
    # own CI could no longer catch it (the cross-review correctly called that out).
    assert r1["acceptance_all_pass"] is True, [
        c for c in r1["acceptance"] if not c["pass"]]
    assert r1["fingerprint_scope_leaks"] == [], r1["fingerprint_scope_leaks"]

    # the report itself is deterministic for a fixed corpus + spec + lexicons
    for name in ("_baseline_report.json", "_baseline_golden.json",
                 "_baseline_report.md"):
        assert be.sha256_file(out1 / name) == be.sha256_file(out2 / name), name
