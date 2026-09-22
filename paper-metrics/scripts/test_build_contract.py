#!/usr/bin/env python3
"""TDD tests for build_contract.py (Issue I8).

Covers the frozen contract in INTERFACES.md section 5:
  1. target = [p25, p75] taken straight from _corpus_summary.json quantiles
  2. iqr == 0 -> clause downgraded to warn and target null
  3. n_valid < min -> downgraded to warn (target kept); null stats -> target null
  4. inverted quantiles -> downgraded instead of emitting a broken band
  5. provenance {source, n_valid, iqr, generated_by} on every clause
  6. deterministic bytes: no timestamp / absolute path / host metadata
  7. the emitted YAML round-trips through the bundled parser and PyYAML
  8. CLI: writes the file, returns 0, and returns 2 on every bad input
  9. OBSERVED layer imports no LLM or network module

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_build_contract.py -v
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import build_contract as bc  # noqa: E402

OTHER_SCRIPTS = ("build_contract.py", "validate_draft.py")
FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(openai|anthropic|requests|httpx|urllib|socket|http|"
    r"transformers|torch|spacy|numpy|pandas|litellm|dashscope|zhipuai)\b",
    re.MULTILINE,
)


def _metric_stats(**over) -> dict:
    base = {
        "unit": "ratio", "n_valid": 34, "n_missing": 0, "missing_papers": [],
        "mean": 0.02, "sd": 0.01, "median": 0.02, "p25": 0.012, "p75": 0.031,
        "iqr": 0.019, "min": 0.0, "max": 0.05, "ci95_low": 0.016, "ci95_high": 0.024,
        "by_section": {}, "warnings": [],
    }
    base.update(over)
    return base


def _summary(metrics: dict | None = None, **over) -> dict:
    summary = {
        "schema_version": "2.0", "analysis_unit": "paper", "weight_mode": "equal_paper",
        "n_papers": 34,
        "corpus_id": "1" * 64,
        "metrics": metrics if metrics is not None else {"M-HED-14": _metric_stats()},
        "corpus_warnings": [],
    }
    summary.update(over)
    return summary


def _write_summary(tmp_path: Path, summary: dict, name: str = "_corpus_summary.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
    return path


def _build(summary: dict, **kwargs) -> dict:
    kwargs.setdefault("summary_sha256", "0" * 64)
    kwargs.setdefault("source_file", "_corpus_summary.json")
    return bc.build_contract(summary, **kwargs)


# ---------------------------------------------------------------------------
# 1. quantile-derived gate clause
# ---------------------------------------------------------------------------

def test_gate_clause_is_derived_from_corpus_quantiles():
    # level=gate is an explicit opt-in (--gate-mode quantile-gate); the default is warn-only
    contract = _build(_summary(), gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    assert contract["schema_version"] == "1.0"
    clause = contract["clauses"][0]
    assert clause["id"] == "C-M-HED-14"
    assert clause["metric"] == "M-HED-14"
    assert clause["target"] == [0.012, 0.031]
    assert clause["level"] == "gate"
    assert clause["unit"] == "ratio"
    assert clause["direction"] == "two_sided"
    assert clause["level_reason"] is None
    assert clause["provenance"] == {
        "source": "corpus_quantile", "n_valid": 34, "iqr": 0.019, "generated_by": "build_contract.py",
    }
    generated = contract["generated_from"]
    assert generated["n_papers"] == 34
    assert generated["gate_mode"] == "quantile-gate"
    assert generated["n_valid_min"] == 34 and generated["n_valid_max"] == 34
    # corpus_id (content identity) and summary_sha256 (file identity) stay separate
    assert generated["corpus_id"] == "1" * 64
    assert generated["summary_sha256"] == "0" * 64
    assert "corpus_fingerprint" not in generated


def test_generated_by_override_is_recorded_in_provenance():
    contract = _build(_summary(), generated_by="build_contract.py@contract-tools")
    assert contract["clauses"][0]["provenance"]["generated_by"] == "build_contract.py@contract-tools"


# ---------------------------------------------------------------------------
# 2. iqr == 0 downgrade
# ---------------------------------------------------------------------------

def test_iqr_zero_downgrades_to_warn_and_null_target():
    summary = _summary({"M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0)})
    contract = _build(summary)
    clause = contract["clauses"][0]
    assert clause["level"] == "warn"
    assert clause["target"] is None
    assert clause["level_reason"] == "iqr_zero"
    assert clause["provenance"]["n_valid"] == 34
    assert clause["provenance"]["iqr"] == 0.0
    assert [w["code"] for w in contract["contract_warnings"]] == ["IQR_ZERO"]


# ---------------------------------------------------------------------------
# 3. sample size and missing statistics
# ---------------------------------------------------------------------------

def test_low_n_valid_downgrades_but_keeps_the_target_band():
    summary = _summary(
        {"M-AWR-03": _metric_stats(n_valid=3)},
        corpus_warnings=[{"code": "N_LT_5", "metric": "M-AWR-03", "detail": "n_valid=3 < 5"}],
    )
    contract = _build(summary)
    clause = contract["clauses"][0]
    assert clause["level"] == "warn"
    assert clause["target"] == [0.012, 0.031]
    assert clause["level_reason"] == "n_valid_below_min"
    assert [w["code"] for w in contract["contract_warnings"]] == ["N_LT_5"]
    assert contract["corpus_warnings"] == [
        {"code": "N_LT_5", "metric": "M-AWR-03", "detail": "n_valid=3 < 5"},
    ]


def test_min_n_valid_override_promotes_small_samples_back_to_gate():
    summary = _summary({"M-AWR-03": _metric_stats(n_valid=3)})
    contract = _build(summary, min_n_valid=3, gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    assert contract["clauses"][0]["level"] == "gate"
    assert contract["contract_warnings"] == []
    assert contract["generated_from"]["min_n_valid"] == 3


def test_null_statistics_produce_warn_with_null_target():
    summary = _summary({"M-PAS-09": _metric_stats(n_valid=0, p25=None, p75=None, iqr=None, median=None)})
    contract = _build(summary)
    clause = contract["clauses"][0]
    assert clause["level"] == "warn"
    assert clause["target"] is None
    assert clause["level_reason"] == "stats_null"
    assert clause["provenance"]["iqr"] is None
    assert [w["code"] for w in contract["contract_warnings"]] == ["STATS_NULL", "N_LT_5"]


def test_inverted_quantiles_are_rejected_instead_of_emitting_a_broken_band():
    summary = _summary({"M-HED-14": _metric_stats(p25=0.9, p75=0.1, iqr=-0.8)})
    contract = _build(summary)
    clause = contract["clauses"][0]
    assert clause["target"] is None
    assert clause["level"] == "warn"
    assert clause["level_reason"] == "inverted_quantiles"
    assert [w["code"] for w in contract["contract_warnings"]] == ["INVERTED_QUANTILES"]


def test_missing_unit_is_reported_as_a_warning():
    summary = _summary({"M-HED-14": _metric_stats(unit=None)})
    contract = _build(summary)
    assert contract["clauses"][0]["unit"] is None
    assert [w["code"] for w in contract["contract_warnings"]] == ["UNIT_MISSING"]


# ---------------------------------------------------------------------------
# 4. determinism / metadata hygiene
# ---------------------------------------------------------------------------

def test_output_is_byte_deterministic_and_free_of_host_metadata():
    summary = _summary({
        "M-HED-14": _metric_stats(),
        "M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0),
    })
    first = bc.dump_yaml(_build(summary))
    second = bc.dump_yaml(_build(summary))
    assert first == second
    assert hashlib.sha256(first.encode("utf-8")).hexdigest() == hashlib.sha256(second.encode("utf-8")).hexdigest()
    for token in ("/mnt/", "/home/", "generated_at", "elapsed", "hostname", "\\"):
        assert token not in first
    assert not re.search(r"\d{4}-\d{2}-\d{2}", first)


def test_clause_order_is_sorted_by_metric_id():
    summary = _summary({mid: _metric_stats() for mid in ("M-SLEN-01", "M-AWR-03", "M-HED-14")})
    ids = [clause["metric"] for clause in _build(summary)["clauses"]]
    assert ids == ["M-AWR-03", "M-HED-14", "M-SLEN-01"]


def test_metrics_filter_selects_and_orders_clauses():
    summary = _summary({mid: _metric_stats() for mid in ("M-SLEN-01", "M-AWR-03")})
    contract = _build(summary, metrics=["M-SLEN-01"])
    assert [clause["metric"] for clause in contract["clauses"]] == ["M-SLEN-01"]
    with pytest.raises(bc.BuildContractError):
        _build(summary, metrics=["M-NOPE-99"])


# ---------------------------------------------------------------------------
# 5. YAML round trip and interoperability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("summary, kwargs", [
    (_summary(), {}),
    (_summary({"M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0)}), {}),
    (_summary({"M-AWR-03": _metric_stats(n_valid=3)}), {"min_n_valid": 2}),
])
def test_emitted_yaml_round_trips_through_the_bundled_parser(summary, kwargs):
    contract = _build(summary, **kwargs)
    text = bc.dump_yaml(contract)
    assert bc.load_yaml(text) == contract


def test_emitted_yaml_is_loadable_by_pyyaml():
    yaml = pytest.importorskip("yaml")
    contract = _build(_summary({
        "M-HED-14": _metric_stats(),
        "M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0),
    }))
    text = bc.dump_yaml(contract)
    assert yaml.safe_load(text) == contract


def test_parser_rejects_malformed_input():
    with pytest.raises(bc.YamlError):
        bc.load_yaml("clauses:\n  - id: \"a\"\n   bad: \"indent\"\n")


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------

def test_main_writes_contract_file_and_returns_zero(tmp_path, capsys):
    summary_path = _write_summary(tmp_path, _summary())
    out_path = tmp_path / "out" / "_writing_contract.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(out_path)]) == 0
    stdout = capsys.readouterr().out
    assert "gate_mode=warn-only" in stdout and "0 gate / 1 warn" in stdout
    data = bc.load_yaml(out_path.read_text(encoding="utf-8"))
    assert data["clauses"][0]["metric"] == "M-HED-14"
    assert data["generated_from"]["source_file"] == "_corpus_summary.json"
    assert data["generated_from"]["summary_sha256"] == hashlib.sha256(
        summary_path.read_bytes()).hexdigest()
    assert data["generated_from"]["corpus_id"] == "1" * 64
    assert data["generated_from"]["gate_mode"] == "warn-only"
    assert data["clauses"][0]["level"] == "warn"
    assert data["clauses"][0]["level_reason"] == "gate_mode_warn_only"
    assert data["clauses"][0]["target"] == [0.012, 0.031]      # numbers unchanged


def test_main_gate_mode_quantile_gate_opt_in(tmp_path, capsys):
    summary_path = _write_summary(tmp_path, _summary())
    out_path = tmp_path / "_writing_contract.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(out_path),
                    "--gate-mode", "quantile-gate"]) == 0
    assert "gate_mode=quantile-gate" in capsys.readouterr().out
    data = bc.load_yaml(out_path.read_text(encoding="utf-8"))
    assert data["generated_from"]["gate_mode"] == "quantile-gate"
    assert data["clauses"][0]["level"] == "gate"


def test_main_quiet_suppresses_stdout_but_still_writes(tmp_path, capsys):
    summary_path = _write_summary(tmp_path, _summary())
    out_path = tmp_path / "_writing_contract.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(out_path), "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    assert out_path.is_file()


def test_main_returns_exit_2_on_every_bad_input(tmp_path):
    good = _write_summary(tmp_path, _summary())
    out_path = tmp_path / "_writing_contract.yaml"

    assert bc.main(["--summary", str(tmp_path / "missing.json"), "--out", str(out_path)]) == 2

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert bc.main(["--summary", str(broken), "--out", str(out_path)]) == 2

    no_metrics = _write_summary(tmp_path, _summary(), name="no_metrics.json")
    no_metrics.write_text(json.dumps({"schema_version": "2.0"}), encoding="utf-8")
    assert bc.main(["--summary", str(no_metrics), "--out", str(out_path)]) == 2

    assert bc.main(["--summary", str(good), "--out", str(out_path), "--metrics", "M-NOPE-9"]) == 2
    assert bc.main(["--summary", str(good), "--out", str(out_path), "--min-n-valid", "0"]) == 2


# ---------------------------------------------------------------------------
# 7. red line: no LLM / network import in the OBSERVED layer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", OTHER_SCRIPTS)
def test_observable_scripts_import_no_llm_or_network_module(name):
    source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    offenders = sorted({match.group(1) for match in FORBIDDEN_IMPORT_RE.finditer(source)})
    assert offenders == [], f"{name} imports non-observable modules: {offenders}"


def test_contract_contains_no_composite_value_field():
    contract = _build(_summary())
    blob = json.dumps(contract)
    for forbidden in ("total_score", "overall", "grade", "percentage", "composite"):
        assert forbidden not in blob


# ---------------------------------------------------------------------------
# 8. corpus identity: corpus_id (content) vs summary_sha256 (source file)
# ---------------------------------------------------------------------------

def test_corpus_id_and_summary_sha256_are_distinct_identities():
    contract = _build(_summary(), summary_sha256="2" * 64)
    generated = contract["generated_from"]
    assert generated["corpus_id"] == "1" * 64
    assert generated["summary_sha256"] == "2" * 64
    assert generated["corpus_id"] != generated["summary_sha256"]
    assert "corpus_fingerprint" not in generated


def test_missing_corpus_id_falls_back_to_null_and_warns():
    summary = _summary()
    del summary["corpus_id"]
    contract = _build(summary)
    assert contract["generated_from"]["corpus_id"] is None
    assert contract["contract_warnings"][0]["code"] == "MISSING_CORPUS_ID"
    assert contract["contract_warnings"][0]["metric"] is None
    assert "corpus_id" in contract["contract_warnings"][0]["detail"]
    # the file hash is still reported, so the two identities never collapse
    assert contract["generated_from"]["summary_sha256"] == "0" * 64


def test_blank_corpus_id_is_treated_as_missing():
    contract = _build(_summary(corpus_id="   "))
    assert contract["generated_from"]["corpus_id"] is None
    assert [w["code"] for w in contract["contract_warnings"]] == ["MISSING_CORPUS_ID"]


def test_iqr_zero_survives_the_cli_round_trip_as_warn_with_null_target(tmp_path):
    summary = _summary({"M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0)})
    summary_path = _write_summary(tmp_path, summary)
    out_path = tmp_path / "_writing_contract.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(out_path), "--quiet"]) == 0
    data = bc.load_yaml(out_path.read_text(encoding="utf-8"))
    clause = data["clauses"][0]
    assert (clause["level"], clause["target"], clause["level_reason"]) == ("warn", None, "iqr_zero")
    assert data["generated_from"]["corpus_id"] == "1" * 64


# ---------------------------------------------------------------------------
# 9. --holdout: leave-one-out contracts (baseline leakage guard)
# ---------------------------------------------------------------------------

def _linear_corpus() -> dict[str, float]:
    """p01..p12 -> 0.01..0.12 plus one extreme paper (5.0). n=13."""
    values = {f"p{i:02d}": round(0.01 * i, 6) for i in range(1, 13)}
    values["p-extreme"] = 5.0
    return values


def _write_jsonl(tmp_path: Path, values: dict[str, float],
                 name: str = "_per_paper_metrics.jsonl") -> Path:
    path = tmp_path / name
    rows = []
    for key in sorted(values):
        rows.append(json.dumps({
            "paper_key": key,
            "metrics": {"M-HED-14": {
                "unit": "ratio", "value": values[key], "n": 1, "denominator": 2,
                "state": "OBSERVED", "method": "rule", "metric_spec": "M-HED-14",
            }},
        }, sort_keys=True))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _full_corpus_summary() -> dict:
    # quantiles of _linear_corpus(): sorted[3] = 0.04, sorted[9] = 0.10
    return _summary({"M-HED-14": _metric_stats(n_valid=13, p25=0.04, p75=0.10, iqr=0.06)},
                    n_papers=13)


def test_no_holdout_declares_the_full_corpus_provenance():
    contract = _build(_full_corpus_summary())
    generated = contract["generated_from"]
    assert generated["holdout"] == []
    assert generated["summary_source"] == "corpus_summary"
    assert generated["n_papers"] == 13 and generated["n_papers_after_holdout"] == 13
    assert generated["per_paper_jsonl_sha256"] is None
    assert contract["clauses"][0]["target"] == [0.04, 0.10]


def test_holdout_interval_excludes_the_held_out_paper(tmp_path):
    pytest.importorskip("profile_papers")
    values = _linear_corpus()
    summary_path = _write_summary(tmp_path, _full_corpus_summary())
    jsonl_path = _write_jsonl(tmp_path, values)
    out_path = tmp_path / "_writing_contract.yaml"

    assert bc.main(["--summary", str(summary_path), "--out", str(out_path),
                    "--holdout", "p-extreme", "--gate-mode", "quantile-gate"]) == 0
    data = bc.load_yaml(out_path.read_text(encoding="utf-8"))
    clause = data["clauses"][0]
    # 12 papers left: sorted[3] = 0.04, sorted[8] = 0.09  (independent recomputation)
    assert clause["target"] == [0.04, 0.09]
    assert clause["target"] != [0.04, 0.10]          # the exclusion really moved the band
    assert not (clause["target"][0] <= values["p-extreme"] <= clause["target"][1])
    assert clause["level"] == "gate"
    assert clause["provenance"]["n_valid"] == 12

    generated = data["generated_from"]
    assert generated["holdout"] == ["p-extreme"]
    assert generated["summary_source"] == "per_paper_jsonl"
    assert generated["n_papers"] == 13 and generated["n_papers_after_holdout"] == 12
    assert generated["per_paper_jsonl_sha256"] == hashlib.sha256(jsonl_path.read_bytes()).hexdigest()

    # deterministic: a second run over the same inputs is byte-identical
    second = tmp_path / "second.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(second),
                    "--holdout", "p-extreme", "--gate-mode", "quantile-gate",
                    "--quiet"]) == 0
    assert second.read_bytes() == out_path.read_bytes()


def test_stream_metrics_are_excluded_from_draft_contracts() -> None:
    """A Markdown draft has no figure/table inventory, so a stream metric could only
    ever come out "skipped": it stays in the profile, out of the contract, and the
    exclusion is reported rather than silent."""
    summary = _summary(metrics={
        "M-HED-14": _metric_stats(scope=["prose"]),
        "S-CAP-01": _metric_stats(scope=["figures", "tables"]),
    })
    contract = _build(summary)
    assert [c["metric"] for c in contract["clauses"]] == ["M-HED-14"]
    codes = [w["code"] for w in contract["contract_warnings"]]
    assert codes == ["NON_PROSE_METRICS_EXCLUDED"]


def test_legacy_summary_without_scope_keeps_every_clause() -> None:
    """Summaries written before the scope field existed must stay buildable: an
    absent (or empty) scope is 'unspecified', which the profile flags separately."""
    contract = _build(_summary(metrics={"M-HED-14": _metric_stats()}))
    assert [c["metric"] for c in contract["clauses"]] == ["M-HED-14"]
    assert contract["contract_warnings"] == []


def test_mixed_scope_metrics_are_excluded_from_draft_contracts() -> None:
    """A prose+tables metric (table density) is NOT draft-checkable either: a Markdown
    draft carries no tables, so its clause could only ever come out "skipped"."""
    summary = _summary(metrics={
        "M-HED-14": _metric_stats(scope=["prose"]),
        "S-TBL-09": _metric_stats(scope=["prose", "tables"], unit="per-1000-words"),
    })
    contract = _build(summary)
    assert [c["metric"] for c in contract["clauses"]] == ["M-HED-14"]
    codes = [w["code"] for w in contract["contract_warnings"]]
    assert codes == ["NON_PROSE_METRICS_EXCLUDED"]


def test_holdout_metadata_is_wired_through_build_contract():
    filtered = _full_corpus_summary()
    filtered["n_papers"] = 12          # what apply_holdout() hands over
    contract = bc.build_contract(
        filtered, summary_sha256="0" * 64, source_file="_corpus_summary.json",
        holdout=["p01"], summary_source=bc.SUMMARY_SOURCE_JSONL,
        per_paper_jsonl_sha256="d" * 64, n_papers_total=13,
    )
    generated = contract["generated_from"]
    assert generated["holdout"] == ["p01"]
    assert generated["summary_source"] == "per_paper_jsonl"
    assert generated["n_papers"] == 13
    assert generated["n_papers_after_holdout"] == 12
    assert generated["per_paper_jsonl_sha256"] == "d" * 64


def test_repeated_holdout_flags_accumulate_and_dedupe(tmp_path):
    pytest.importorskip("profile_papers")
    summary_path = _write_summary(tmp_path, _full_corpus_summary())
    _write_jsonl(tmp_path, _linear_corpus())
    out_path = tmp_path / "_writing_contract.yaml"
    assert bc.main(["--summary", str(summary_path), "--out", str(out_path),
                    "--holdout", "p01", "--holdout", "p02", "--holdout", "p01", "--quiet"]) == 0
    generated = bc.load_yaml(out_path.read_text(encoding="utf-8"))["generated_from"]
    assert generated["holdout"] == ["p01", "p02"]
    assert generated["n_papers_after_holdout"] == 11


def test_holdout_without_the_per_paper_jsonl_fails_instead_of_leaking(tmp_path):
    summary_path = _write_summary(tmp_path, _full_corpus_summary())
    out_path = tmp_path / "_writing_contract.yaml"
    rc = bc.main(["--summary", str(summary_path), "--out", str(out_path), "--holdout", "p01"])
    assert rc == 2
    assert not out_path.exists()
    with pytest.raises(bc.BuildContractError, match="_per_paper_metrics.jsonl"):
        bc.apply_holdout(_full_corpus_summary(), summary_path, ["p01"])


def test_holdout_rejects_an_unknown_paper_key(tmp_path, capsys):
    pytest.importorskip("profile_papers")
    summary_path = _write_summary(tmp_path, _full_corpus_summary())
    _write_jsonl(tmp_path, _linear_corpus())
    out_path = tmp_path / "_writing_contract.yaml"
    rc = bc.main(["--summary", str(summary_path), "--out", str(out_path),
                  "--holdout", "p-does-not-exist"])
    assert rc == 2
    assert not out_path.exists()
    assert "not present" in capsys.readouterr().err


def test_thin_holdout_sample_downgrades_to_warn_and_never_backfills(tmp_path):
    pytest.importorskip("profile_papers")
    values = _linear_corpus()
    summary_path = _write_summary(tmp_path, _full_corpus_summary())
    _write_jsonl(tmp_path, values)
    out_path = tmp_path / "_writing_contract.yaml"
    held = [f"p{i:02d}" for i in range(4, 13)]      # leave p01(0.01), p02(0.02), p03(0.03), extreme(5.0)
    args = ["--summary", str(summary_path), "--out", str(out_path), "--quiet"]
    for key in held:
        args += ["--holdout", key]
    assert bc.main(args) == 0
    data = bc.load_yaml(out_path.read_text(encoding="utf-8"))
    clause = data["clauses"][0]
    # n_valid = 4: sorted[1] = 0.02, sorted[2] = 0.03 -- the filtered band, not the full one
    assert clause["target"] == [0.02, 0.03]
    assert clause["target"] != [0.04, 0.10]
    assert clause["level"] == "warn"
    assert clause["level_reason"] == "n_valid_below_min"
    assert "N_LT_5" in [w["code"] for w in data["contract_warnings"]]
    assert data["generated_from"]["n_papers_after_holdout"] == 4


# ---------------------------------------------------------------------------
# 10. --gate-mode: warn-only by default, quantile-gate on request
# ---------------------------------------------------------------------------

def test_default_gate_mode_is_warn_only_and_keeps_the_numbers():
    summary = _summary({
        "M-HED-14": _metric_stats(),
        "M-MTLD-02": _metric_stats(unit="index", p25=70.0, p75=70.0, iqr=0.0),
    })
    default = _build(summary)
    opt_in = _build(summary, gate_mode=bc.GATE_MODE_QUANTILE_GATE)

    assert default["generated_from"]["gate_mode"] == "warn-only"
    assert opt_in["generated_from"]["gate_mode"] == "quantile-gate"
    assert all(clause["level"] == "warn" for clause in default["clauses"])
    assert [clause["target"] for clause in default["clauses"]] ==            [clause["target"] for clause in opt_in["clauses"]]
    assert [clause["provenance"] for clause in default["clauses"]] ==            [clause["provenance"] for clause in opt_in["clauses"]]
    assert default["clauses"][0]["level_reason"] == "gate_mode_warn_only"
    assert opt_in["clauses"][0]["level"] == "gate"
    assert opt_in["clauses"][0]["level_reason"] is None
    # an IQR-zero clause keeps its data reason in either mode
    assert default["clauses"][1]["level_reason"] == "iqr_zero"
    assert opt_in["clauses"][1]["level_reason"] == "iqr_zero"


def test_invalid_gate_mode_is_rejected():
    with pytest.raises(bc.BuildContractError, match="gate_mode must be one of"):
        _build(_summary(), gate_mode="strict")
    with pytest.raises(SystemExit):
        bc.main(["--summary", "x", "--out", "y", "--gate-mode", "strict"])


# ---------------------------------------------------------------------------
# 11. derived metrics: component-sum targets (F1: quantiles are not additive)
# ---------------------------------------------------------------------------

def _conn_summary(**overrides) -> dict:
    """Component bands whose sum is BELOW the independently sampled total band."""
    metrics = {
        "M-CONN-30": _metric_stats(unit="per-1000-words", p25=0.014, p75=0.030, iqr=0.016),
        "M-CONN-30c": _metric_stats(unit="per-1000-words", p25=0.004, p75=0.009, iqr=0.005),
        "M-CONN-30k": _metric_stats(unit="per-1000-words", p25=0.007, p75=0.014, iqr=0.007),
        "M-CONN-30r": _metric_stats(unit="per-1000-words", p25=0.001, p75=0.003, iqr=0.002),
    }
    metrics.update(overrides)
    return _summary(metrics)


def test_derived_metric_table_matches_the_frozen_identity():
    assert bc.DERIVED_METRICS == {"M-CONN-30": ("M-CONN-30c", "M-CONN-30k", "M-CONN-30r")}


def test_derived_target_is_the_sum_of_component_targets():
    contract = _build(_conn_summary(), gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    by_metric = {clause["metric"]: clause for clause in contract["clauses"]}
    total = by_metric["M-CONN-30"]
    components = [by_metric[mid] for mid in bc.DERIVED_METRICS["M-CONN-30"]]

    # components sum to [0.012, 0.026]; the independent quantile was [0.014, 0.030]
    assert [component["target"] for component in components] == [[0.004, 0.009], [0.007, 0.014], [0.001, 0.003]]
    assert sum(component["target"][0] for component in components) == 0.012
    assert total["target"] == [0.012, 0.026]
    assert total["target"] != [0.014, 0.030]
    assert total["target"][0] < 0.014                      # F1: Sigma(p25) < own p25
    assert total["level"] == "gate" and total["level_reason"] is None
    assert total["provenance"]["source"] == "derived_sum_of_components"
    assert total["provenance"]["derived_from"] == ["M-CONN-30c", "M-CONN-30k", "M-CONN-30r"]
    assert total["provenance"]["n_valid"] == 34            # corpus stats are still reported
    assert total["derivation"]["rule"] == "sum_of_components"
    assert total["derivation"]["check"] == "ok"
    assert total["derivation"]["expected_target"] == [0.012, 0.026]
    assert total["derivation"]["component_targets"] == {
        "M-CONN-30c": [0.004, 0.009], "M-CONN-30k": [0.007, 0.014], "M-CONN-30r": [0.001, 0.003],
    }
    assert "not additive" in contract["derivation"]["note"]
    assert contract["derivation"]["rules"] == [{
        "metric": "M-CONN-30",
        "components": ["M-CONN-30c", "M-CONN-30k", "M-CONN-30r"],
        "source": "derived_sum_of_components",
        "expected_target": [0.012, 0.026],
        "check": "ok",
    }]


def test_derived_metric_degrades_when_a_component_has_no_spread():
    # M-CONN-30k has iqr == 0 -> target null -> the derived clause must degrade too
    contract = _build(_conn_summary(
        **{"M-CONN-30k": _metric_stats(unit="per-1000-words", p25=0.007, p75=0.007, iqr=0.0)}),
        gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    total = {clause["metric"]: clause for clause in contract["clauses"]}["M-CONN-30"]
    assert total["target"] is None
    assert total["level"] == "warn"
    assert total["level_reason"] == "derived_component_degraded"
    assert total["derivation"]["check"] == "not_applicable"
    assert any(w["code"] == "IQR_ZERO" for w in contract["contract_warnings"])


def test_derived_metric_degrades_when_a_component_is_warn_but_keeps_the_sum():
    # n_valid=3 -> that component is warn (target kept) -> derived target is still
    # the sum of the component bands, but the derived clause is warn as well
    contract = _build(_conn_summary(
        **{"M-CONN-30c": _metric_stats(unit="per-1000-words", n_valid=3, p25=0.004, p75=0.009,
                                       iqr=0.005)}),
        gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    by_metric = {clause["metric"]: clause for clause in contract["clauses"]}
    assert by_metric["M-CONN-30c"]["level"] == "warn"
    total = by_metric["M-CONN-30"]
    assert total["target"] == [0.012, 0.026]
    assert total["level"] == "warn"
    assert total["level_reason"] == "derived_component_degraded"


def test_derived_metric_without_its_components_never_keeps_an_independent_band():
    contract = _build(_conn_summary(), metrics=["M-CONN-30"],
                      gate_mode=bc.GATE_MODE_QUANTILE_GATE)
    assert [clause["metric"] for clause in contract["clauses"]] == ["M-CONN-30"]
    total = contract["clauses"][0]
    assert total["target"] is None
    assert total["level"] == "warn"
    assert total["level_reason"] == "derived_component_missing"
    assert total["derivation"]["check"] == "not_applicable"
    assert "DERIVED_COMPONENT_MISSING" in [w["code"] for w in contract["contract_warnings"]]


def test_warn_only_mode_still_sums_the_components():
    contract = _build(_conn_summary())                     # default: warn-only
    total = {clause["metric"]: clause for clause in contract["clauses"]}["M-CONN-30"]
    assert total["target"] == [0.012, 0.026]
    assert total["level"] == "warn"
    assert total["level_reason"] == "gate_mode_warn_only"  # policy, not data degradation
    assert total["derivation"]["check"] == "ok"


def test_contract_yaml_header_documents_non_additivity():
    contract = _build(_conn_summary())
    text = bc.render_contract(contract)
    assert text.startswith("# _writing_contract.yaml")
    assert "NOT additive" in text
    assert bc.load_yaml(text) == contract                  # comments do not disturb parsing
    try:
        import yaml
    except ImportError:  # pragma: no cover
        return
    assert yaml.safe_load(text) == contract


# ---------------------------------------------------------------------------
# 12. #18.10 draft-checkability is declared per metric, not inferred from scope
# ---------------------------------------------------------------------------

def test_reference_metrics_are_draft_checkable() -> None:
    """M-REFAGE-53 / M-REFLINK-54 read prose + references.  A Markdown draft HAS a
    reference section, so they are recomputable even though their scope is not exactly
    prose (issue #18.10); a true non-prose metric (S-SIZ-04, figures) stays excluded."""
    summary = _summary(metrics={
        "M-REFAGE-53": _metric_stats(scope=["prose", "references"]),
        "M-REFLINK-54": _metric_stats(scope=["prose", "references"]),
        "S-SIZ-04": _metric_stats(scope=["figures"]),
    })
    contract = _build(summary)
    assert [c["metric"] for c in contract["clauses"]] == ["M-REFAGE-53", "M-REFLINK-54"]
    codes = [w["code"] for w in contract["contract_warnings"]]
    assert codes == ["NON_PROSE_METRICS_EXCLUDED"]


def test_draft_checkable_is_declared_per_metric_not_inferred_from_scope() -> None:
    """The allow-list decides: reference metrics are draft-checkable despite a
    non-prose scope, and a figures-only metric is not despite having a scope field."""
    assert bc._is_draft_checkable("M-REFAGE-53", {"scope": ["prose", "references"]})
    assert bc._is_draft_checkable("M-REFLINK-54", {"scope": ["prose", "references"]})
    assert bc._is_draft_checkable("M-HED-14", {"scope": ["prose"]})
    assert not bc._is_draft_checkable("S-SIZ-04", {"scope": ["figures"]})
    assert not bc._is_draft_checkable("S-TBL-09", {"scope": ["prose", "tables"]})



def test_toolchain_defect_metrics_are_never_accepted_by_a_contract() -> None:
    """class=toolchain_defect measures the extraction chain, not the writing, so a
    clause built from it would tell the author to imitate our parser (issue #18-9)."""
    summary = _summary({
        "M-HED-14": _metric_stats(),
        "S-TBL-13": _metric_stats(scope=["prose"], **{"class": "toolchain_defect"}),
    })
    contract = _build(summary)
    assert [c["metric"] for c in contract["clauses"]] == ["M-HED-14"]
    assert [w["code"] for w in contract["contract_warnings"]] == [
        "NON_PROSE_METRICS_EXCLUDED", "TOOLCHAIN_DEFECT_METRICS_EXCLUDED"]
    assert not bc._is_draft_checkable(
        "S-TBL-13", {"scope": ["prose"], "class": "toolchain_defect"})
    with pytest.raises(bc.BuildContractError):
        _build(summary, metrics=["S-TBL-13"])


def test_adaptive_widening_for_low_sample_size() -> None:
    summary = _summary({
        "M-AWR-03": _metric_stats(n_valid=3, p25=0.012, p75=0.031, iqr=0.019),
    })
    contract_default = _build(summary, adaptive_widening=False)
    assert contract_default["clauses"][0]["target"] == [0.012, 0.031]
    assert not any(w["code"] == "CONTRACT_LOW_SAMPLE_WARNING" for w in contract_default["contract_warnings"])

    contract_widened = _build(summary, adaptive_widening=True)
    target = contract_widened["clauses"][0]["target"]
    assert target is not None
    # Widened band should be wider than original [0.012, 0.031]
    assert target[0] <= 0.012
    assert target[1] >= 0.031
    assert any(w["code"] == "CONTRACT_LOW_SAMPLE_WARNING" for w in contract_widened["contract_warnings"])
