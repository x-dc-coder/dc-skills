#!/usr/bin/env python3
"""TDD tests for validate_draft.py (Issue I9).

Covers the frozen contract in INTERFACES.md section 5:
  1. all-pass -> exit 0; a failing gate clause -> exit 1 (in-process and as a
     real subprocess exit code)
  2. a warn clause outside its band is reported as warn, never as a gate failure
  3. target null / actual null -> status skipped
  4. deviation = distance outside the [lo, hi] band, 0.0 when inside
  5. evidence_lines are draft line numbers mapped from text_metrics char spans
  6. no 0-100 aggregate value anywhere in the machine-readable output
  7. draft metrics come from an injected provider: this module reimplements none
  8. --json writes the machine-readable result and preserves the exit code
  9. bad inputs -> exit 2
 10. end-to-end wiring with the real text_metrics + lexicon_loader (skipped until
     that module lands, so this suite stays green while it is in flight)

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_validate_draft.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

# Real 34-paper VRP corpus (I12 baseline) + the artifact directories that may hold
# the profiler output for it. The real-corpus regression test skips when absent.
REAL_CORPUS = Path("/mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis")
ARTIFACT_DIRS = (REAL_CORPUS, Path("/tmp/pp_out"))

import build_contract as bc  # noqa: E402
import validate_draft as vd  # noqa: E402

FORBIDDEN_KEYS = {
    "score", "total", "total_score", "overall", "overall_score", "grade",
    "rating", "percentage", "percent", "composite", "points",
}
FORBIDDEN_TEXT_RE = re.compile(r"(?i)(score|grade|percent|总分|百分制)")


def _clause(metric: str, level: str, target, clause_id: str | None = None) -> dict:
    return {
        "id": clause_id or f"C-{metric}",
        "metric": metric,
        "level": level,
        "target": target,
        "unit": "ratio",
        "direction": "two_sided",
        "provenance": {
            "source": "corpus_quantile", "n_valid": 34, "iqr": 0.01,
            "generated_by": "build_contract.py",
        },
        "level_reason": None,
    }


def _contract(clauses: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "builder_version": "1.0",
        "generated_from": {
            "corpus_id": "a" * 64,
            "summary_sha256": "b" * 64,
            "source_file": "_corpus_summary.json",
            "n_valid_min": 34,
        },
        "clauses": clauses,
    }


def _metrics(values: dict, evidence: dict | None = None) -> dict:
    out = {}
    for metric_id, value in values.items():
        out[metric_id] = {
            "value": value, "n": 1, "denominator": 10, "unit": "ratio",
            "state": "OBSERVED", "method": "rule", "metric_spec": metric_id,
            "evidence": evidence if evidence is not None else {"count": 0, "sample": []},
            "warnings": [],
        }
    return out


def _compute(values: dict, evidence: dict | None = None):
    payload = _metrics(values, evidence)
    calls: list[str] = []

    def compute(text: str) -> dict:
        calls.append(text)
        return payload

    compute.calls = calls  # type: ignore[attr-defined]
    return compute


def _dump(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _detector(supported: bool = True, language: str = "en", cjk_ratio: float | None = 0.0):
    """Injected language verdict. text_metrics.detect_language owns the real one."""
    def detect(text: str) -> dict:
        return {"language": language, "supported": supported, "cjk_ratio": cjk_ratio}
    return detect


PERMISSIVE_DETECTOR = _detector()
CJK_DETECTOR = _detector(supported=False, language="zh", cjk_ratio=0.9235)


def _write_lang_module(tmp_path: Path, supported: bool = True, language: str = "en",
                       cjk_ratio: float = 0.0, name: str = "fake_lang") -> str:
    """Write an injectable detector module for CLI tests (--language-module)."""
    (tmp_path / f"{name}.py").write_text(
        "def detect_language(text):\n"
        f"    return {{'language': {language!r}, 'supported': {supported!r}, "
        f"'cjk_ratio': {cjk_ratio!r}}}\n",
        encoding="utf-8",
    )
    return name


def _validate(contract, draft, compute=None, **kwargs):
    """validate() with the degenerate-input guard disabled: these fixtures are tiny
    on purpose, and the guard itself is covered by its own tests below."""
    kwargs.setdefault("min_alpha_tokens", 0)
    kwargs.setdefault("min_evaluable_ratio", 0.0)
    kwargs.setdefault("language_detector", PERMISSIVE_DETECTOR)
    return vd.validate(contract, draft, compute, **kwargs)


def _validate_guarded(contract, draft, compute=None, **kwargs):
    """validate() with the real thresholds but a permissive language verdict, so the
    degenerate-input tests never depend on metrics-text's evolving detector."""
    kwargs.setdefault("language_detector", PERMISSIVE_DETECTOR)
    return vd.validate(contract, draft, compute, **kwargs)


# ---------------------------------------------------------------------------
# 1/2. status and exit code
# ---------------------------------------------------------------------------

def test_all_pass_returns_exit_0_and_per_clause_pass():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05]),
                          _clause("M-AWR-03", "warn", [0.05, 0.09])])
    result = _validate(contract, "draft body\n", _compute({"M-HED-14": 0.02, "M-AWR-03": 0.07}),
                         draft_name="draft.md")
    assert [clause["status"] for clause in result["clauses"]] == ["pass", "pass"]
    assert result["exit_code"] == 0
    assert result["summary"] == {
        "n_clauses": 2, "n_pass": 2, "n_fail": 0, "n_warn": 0, "n_warn_failures": 0,
        "n_skipped": 0, "n_gate_clauses": 1, "n_evaluable": 2,
        "gate_mode": "quantile-gate", "gate_failures": [], "warn_failures": [],
        "warnings": [], "note": None,
    }
    assert result["draft_validity"]["status"] == "ok"
    assert result["clauses"][0]["deviation"] == 0.0
    assert result["draft"]["source_file"] == "draft.md"
    assert result["draft"]["n_paragraph_blocks"] == 0
    assert result["contract"]["corpus_id"] == "a" * 64
    assert result["contract"]["summary_sha256"] == "b" * 64


def test_gate_failure_returns_exit_1_and_lists_the_clause():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05]),
                          _clause("M-AWR-03", "gate", [0.05, 0.09])])
    result = _validate(contract, "draft body\n", _compute({"M-HED-14": 0.07, "M-AWR-03": 0.07}))
    statuses = {clause["metric"]: clause["status"] for clause in result["clauses"]}
    assert statuses == {"M-HED-14": "fail", "M-AWR-03": "pass"}
    assert result["exit_code"] == 1
    assert result["summary"]["gate_failures"] == ["C-M-HED-14"]
    assert result["summary"]["n_fail"] == 1


def test_warn_clause_outside_band_does_not_fail_the_gate():
    contract = _contract([_clause("M-MTLD-02", "warn", [60.0, 80.0])])
    result = _validate(contract, "draft body\n", _compute({"M-MTLD-02": 55.0}))
    clause = result["clauses"][0]
    assert clause["status"] == "warn"
    assert clause["deviation"] == 5.0
    assert result["exit_code"] == 0
    assert result["summary"]["gate_failures"] == []


def test_gate_failure_exit_code_is_visible_to_a_real_subprocess(tmp_path):
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(contract), encoding="utf-8")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("hedging draft line\n", encoding="utf-8")
    (tmp_path / "fake_metrics_for_test.py").write_text(
        "def compute(text):\n"
        "    return {'M-HED-14': {'value': 0.9, 'n': 1, 'denominator': 10, 'unit': 'ratio',\n"
        "            'state': 'OBSERVED', 'method': 'rule', 'metric_spec': 'M-HED-14',\n"
        "            'evidence': {'count': 1, 'sample': [{'span': [0, 7], 'excerpt': 'hedging'}]},\n"
        "            'warnings': []}}\n",
        encoding="utf-8",
    )
    lang_module = _write_lang_module(tmp_path, name="fake_lang_en")
    json_path = tmp_path / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_draft.py"),
             "--contract", str(contract_path), "--draft", str(draft_path),
             "--compute-module", "fake_metrics_for_test", "--json", str(json_path), "--quiet",
             "--min-alpha-tokens", "0", "--min-evaluable-ratio", "0",
             "--language-module", lang_module],
            capture_output=True, text=True, env=env, cwd=str(SCRIPTS_DIR),
        )

    completed = run()
    assert completed.returncode == 1, completed.stderr
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 1
    assert payload["summary"]["gate_failures"] == ["C-M-HED-14"]
    assert payload["clauses"][0]["evidence_lines"] == [1]

    (tmp_path / "fake_metrics_for_test.py").write_text(
        "def compute(text):\n"
        "    return {'M-HED-14': {'value': 0.02, 'n': 1, 'denominator': 10, 'unit': 'ratio',\n"
        "            'state': 'OBSERVED', 'method': 'rule', 'metric_spec': 'M-HED-14',\n"
        "            'evidence': {'count': 0, 'sample': []}, 'warnings': []}}\n",
        encoding="utf-8",
    )
    assert run().returncode == 0


# ---------------------------------------------------------------------------
# 3/4. skipped clauses and deviation arithmetic
# ---------------------------------------------------------------------------

def test_null_target_and_missing_actual_are_skipped():
    contract = _contract([_clause("M-MTLD-02", "warn", None),
                          _clause("M-NOM-10", "gate", [0.1, 0.2])])
    result = _validate(contract, "draft body\n", _compute({"M-MTLD-02": 70.0, "M-NOM-10": None}))
    clauses = {clause["metric"]: clause for clause in result["clauses"]}
    assert clauses["M-MTLD-02"]["status"] == "skipped"
    assert clauses["M-MTLD-02"]["actual"] == 70.0
    assert clauses["M-NOM-10"]["status"] == "skipped"
    assert clauses["M-NOM-10"]["deviation"] is None
    # an all-skipped contract is unusable evidence, whatever the threshold flags say
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert any("no evaluable clause" in reason for reason in result["draft_validity"]["reasons"])
    assert result["exit_code"] == 2
    assert result["summary"]["n_skipped"] == 2
    assert any("M-NOM-10" in warning for warning in result["summary"]["warnings"])


def test_actual_nan_and_missing_metric_entry_are_skipped():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05]),
                          _clause("M-AWR-03", "gate", [0.05, 0.09])])
    compute = _compute({"M-HED-14": float("nan")})
    result = _validate(contract, "draft body\n", compute)
    assert [clause["status"] for clause in result["clauses"]] == ["skipped", "skipped"]
    assert "NaN" not in _dump(result)


@pytest.mark.parametrize("actual, expected_deviation, expected_status", [
    (0.02, 0.0, "pass"),
    (0.07, 0.02, "fail"),
    (0.005, 0.005, "fail"),
])
def test_deviation_is_the_distance_outside_the_band(actual, expected_deviation, expected_status):
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    result = _validate(contract, "draft body\n", _compute({"M-HED-14": actual}))
    clause = result["clauses"][0]
    assert clause["deviation"] == expected_deviation
    assert clause["status"] == expected_status


# ---------------------------------------------------------------------------
# 5. evidence lines
# ---------------------------------------------------------------------------

def test_evidence_spans_are_mapped_to_draft_line_numbers():
    draft = "alpha beta\ngamma delta\nepsilon zeta hedge\n"
    evidence = {
        "count": 7,
        "sample": [
            {"span": [24, 29], "excerpt": "hedge"},
            {"span": [0, 5], "excerpt": "alpha"},
            {"span": [12, 17], "excerpt": "gamma"},
            {"span": [30, 34], "excerpt": "he"},
            {"span": [31, 34], "excerpt": "e"},
            {"span": [32, 34], "excerpt": "x"},
        ],
    }
    contract = _contract([_clause("M-HED-14", "gate", [0.0, 1.0])])
    result = _validate(contract, draft, _compute({"M-HED-14": 0.1}, evidence))
    clause = result["clauses"][0]
    assert clause["evidence_lines"] == [1, 2, 3]
    assert [item["excerpt"] for item in clause["evidence"]] == ["alpha", "gamma", "hedge", "he", "e"]
    assert len(clause["evidence"]) == vd.MAX_EVIDENCE


def test_academic_multiword_metrics_have_no_evidence_when_the_provider_gives_none():
    contract = _contract([_clause("M-AWR-03", "warn", [0.0, 1.0])])
    result = _validate(contract, "draft body\n", _compute({"M-AWR-03": 0.5}))
    assert result["clauses"][0]["evidence_lines"] == []
    assert result["clauses"][0]["evidence"] == []


# ---------------------------------------------------------------------------
# 6. no aggregate value
# ---------------------------------------------------------------------------

def _walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def test_output_contains_no_composite_value_or_points_field():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    result = _validate(contract, "draft body\n", _compute({"M-HED-14": 0.9}))
    blob = _dump(result)
    assert FORBIDDEN_TEXT_RE.search(blob) is None
    for path, key, value in _walk(result):
        assert key.lower() not in FORBIDDEN_KEYS, f"forbidden field {path}"
        if key == "aggregate":
            assert value is None, "an aggregate value must never be produced"
    assert result["assessment_policy"] == {"aggregate": None, "policy": "per_clause_only"}


# ---------------------------------------------------------------------------
# 7. metrics are delegated, never reimplemented
# ---------------------------------------------------------------------------

def test_injected_provider_is_called_once_with_the_raw_draft_text():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    compute = _compute({"M-HED-14": 0.02})
    draft = "verbatim draft text\n"
    _validate(contract, draft, compute)
    assert compute.calls == [draft]


def test_validator_owns_only_the_paragraph_metric():
    source = (SCRIPTS_DIR / "validate_draft.py").read_text(encoding="utf-8")
    assert "compute_text_metrics" in source
    assert "def compute_text_metrics" not in source
    for token in ("_ALPHA_TOKEN_RE", "_MTLD_TTR_THRESHOLD", "def split_sentences", "def tokenize"):
        assert token not in source, f"validator must not own metric internals: {token}"
    # the single structure rule this module owns, because text_metrics never emits it
    assert "def compute_paragraph_metric" in source
    for foreign in ("M-SLEN-01", "M-MTLD-02", "M-HED-14", "M-BOO-15", "M-CONN-30",
                    "M-AWR-03", "M-PAS-09", "M-NOM-10", "M-TENSE-28"):
        assert foreign not in source, f"validator must not compute {foreign}"


def test_legacy_contract_corpus_fingerprint_is_still_read():
    contract = _contract([_clause("M-HED-14", "gate", [0.0, 1.0])])
    del contract["generated_from"]["corpus_id"]
    contract["generated_from"]["corpus_fingerprint"] = "c" * 64
    result = _validate(contract, "draft body\n", _compute({"M-HED-14": 0.5}))
    assert result["contract"]["corpus_id"] == "c" * 64


def test_skip_reasons_are_recorded_for_target_and_value_gaps():
    contract = _contract([_clause("M-MTLD-02", "warn", None),
                          _clause("M-NOM-10", "gate", [0.1, 0.2])])
    result = _validate(contract, "draft body\n", _compute({"M-MTLD-02": 70.0}))
    reasons = {clause["metric"]: clause["status_reason"] for clause in result["clauses"]}
    assert reasons == {
        "M-MTLD-02": "target_unavailable",
        "M-NOM-10": "metric_not_produced_by_the_metrics_layer",
    }


# ---------------------------------------------------------------------------
# 9. M-PCNT-25: the paragraph rule this validator owns
# ---------------------------------------------------------------------------

LONG_A = "word " * 40          # 40 words
LONG_B = "token " * 20         # 20 words
SHORT = "short fragment here"  # 3 words

PARAGRAPH_CLAUSE_TARGET = [20.0, 45.0]


def test_paragraph_metric_is_computed_for_the_draft():
    draft = f"# 1 Introduction\n{LONG_A.strip()}\n\n{LONG_B.strip()}\n\n{SHORT}\n"
    contract = _contract([_clause("M-PCNT-25", "gate", PARAGRAPH_CLAUSE_TARGET)])
    result = _validate(contract, draft, _compute({}))
    clause = result["clauses"][0]
    assert clause["actual"] == 30.0            # (40 + 20) / 2
    assert clause["status"] == "pass"
    assert clause["status_reason"] is None
    assert clause["deviation"] == 0.0
    assert clause["evidence_lines"] == [2, 4]
    assert [entry["block_index"] for entry in clause["evidence"]] == [0, 1]
    assert clause["evidence"][0]["excerpt"] == LONG_A.strip()[:80]
    assert result["draft"]["n_paragraph_blocks"] == 3
    assert result["exit_code"] == 0


def test_paragraph_metric_satisfies_the_observed_metric_contract():
    draft = f"# T\n{LONG_A.strip()}\n\n{SHORT}\n"
    metric = vd.compute_paragraph_metric(draft)
    for key in ("value", "n", "denominator", "unit", "state", "method", "metric_spec",
                "evidence", "warnings"):
        assert key in metric
    assert metric["value"] == 40.0 and metric["n"] == 1 and metric["denominator"] == 1
    assert metric["unit"] == "words/paragraph"
    assert metric["state"] == "OBSERVED" and metric["method"] == "rule"
    assert metric["metric_spec"] == "M-PCNT-25"
    assert metric["n_blocks"] == 2
    assert metric["warnings"] == []
    assert metric["evidence"] == {
        "count": 1, "sample": [{"block_index": 0, "excerpt": LONG_A.strip()[:80]}],
    }
    assert metric["distribution"]["median"] == 40.0


def test_paragraph_metric_ignores_headings_short_blocks_and_fenced_code():
    code = "\n".join(["\u0060\u0060\u0060python", "print('x') " * 30, "\u0060\u0060\u0060"])
    draft = f"# Heading\n{SHORT}\n\n{LONG_A.strip()}\n\n{code}\n"
    metric = vd.compute_paragraph_metric(draft)
    assert metric["n_blocks"] == 2          # SHORT + LONG_A; the code block is not prose
    assert metric["n"] == 1 and metric["value"] == 40.0
    assert metric["evidence"]["sample"] == [{"block_index": 1, "excerpt": LONG_A.strip()[:80]}]
    result = _validate(_contract([_clause("M-PCNT-25", "gate", [30.0, 50.0])]), draft, _compute({}))
    assert result["clauses"][0]["status"] == "pass"
    assert result["clauses"][0]["evidence_lines"] == [4]


def test_paragraph_clause_is_skipped_only_when_no_paragraph_reaches_the_minimum():
    draft = "# T\nshort line\n\nanother short block\n"
    contract = _contract([_clause("M-PCNT-25", "gate", PARAGRAPH_CLAUSE_TARGET)])
    result = _validate(contract, draft, _compute({}))
    clause = result["clauses"][0]
    assert clause["status"] == "skipped"
    assert clause["actual"] is None
    assert clause["status_reason"] == "no_paragraphs_above_min_words"
    assert any("M-PCNT-25" in warning and "15 words" in warning
               for warning in result["summary"]["warnings"])
    # nothing evaluable -> exit 2 (never a silent pass), see the all-skipped floor
    assert result["exit_code"] == 2
    assert result["draft_validity"]["status"] == "insufficient_evidence"


def test_provider_value_for_the_paragraph_metric_is_not_overwritten():
    contract = _contract([_clause("M-PCNT-25", "gate", PARAGRAPH_CLAUSE_TARGET)])
    result = _validate(contract, LONG_A.strip() + "\n", _compute({"M-PCNT-25": 22.0}))
    assert result["clauses"][0]["actual"] == 22.0
    assert result["clauses"][0]["status"] == "pass"
    assert result["draft"]["n_paragraph_blocks"] == 1


def test_missing_metrics_layer_is_reported_as_a_validator_error():
    with pytest.raises(vd.ValidatorError) as excinfo:
        vd.load_default_compute("no_such_text_metrics_module_xyz", "no_such_lexicon_module_xyz")
    assert "text_metrics" in str(excinfo.value)


def test_missing_compute_module_is_reported_as_a_validator_error():
    with pytest.raises(vd.ValidatorError):
        vd.load_compute_module("no_such_compute_module_xyz")


# ---------------------------------------------------------------------------
# 8/9. CLI: --json output, exit codes, bad inputs
# ---------------------------------------------------------------------------

def test_main_writes_json_and_returns_gate_failure_exit_code(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(
        _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])), encoding="utf-8")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("hedging draft line\n", encoding="utf-8")
    (tmp_path / "fake_metrics_cli.py").write_text(
        "def compute(text):\n"
        "    return {'M-HED-14': {'value': 0.9, 'n': 1, 'denominator': 10, 'unit': 'ratio',\n"
        "            'state': 'OBSERVED', 'method': 'rule', 'metric_spec': 'M-HED-14',\n"
        "            'evidence': {'count': 0, 'sample': []}, 'warnings': []}}\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        json_path = tmp_path / "report.json"
        lang_module = _write_lang_module(tmp_path, name="fake_lang_cli")
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--compute-module", "fake_metrics_cli", "--json", str(json_path),
                      "--min-alpha-tokens", "0", "--min-evaluable-ratio", "0",
                      "--language-module", lang_module])
    finally:
        sys.path.remove(str(tmp_path))
    assert rc == 1
    stdout = capsys.readouterr().out
    assert "GATE FAILED: C-M-HED-14" in stdout
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 1
    assert payload["clauses"][0]["status"] == "fail"
    assert payload["draft"]["source_file"] == "draft.md"


def test_main_json_dash_writes_machine_readable_stdout(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(
        _contract([_clause("M-HED-14", "warn", [0.01, 0.05])])), encoding="utf-8")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("hedging draft line\n", encoding="utf-8")
    (tmp_path / "fake_metrics_dash.py").write_text(
        "def compute(text):\n"
        "    return {'M-HED-14': {'value': 0.9, 'n': 1, 'denominator': 10, 'unit': 'ratio',\n"
        "            'state': 'OBSERVED', 'method': 'rule', 'metric_spec': 'M-HED-14',\n"
        "            'evidence': {'count': 0, 'sample': []}, 'warnings': []}}\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        lang_module = _write_lang_module(tmp_path, name="fake_lang_dash")
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--compute-module", "fake_metrics_dash", "--json", "-", "--quiet",
                      "--min-alpha-tokens", "0", "--min-evaluable-ratio", "0",
                      "--language-module", lang_module])
    finally:
        sys.path.remove(str(tmp_path))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["clauses"][0]["status"] == "warn"


def test_main_returns_exit_2_on_bad_inputs(tmp_path):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])])),
                             encoding="utf-8")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("text\n", encoding="utf-8")

    assert vd.main(["--contract", str(tmp_path / "nope.yaml"), "--draft", str(draft_path)]) == 2
    assert vd.main(["--contract", str(contract_path), "--draft", str(tmp_path / "nope.md")]) == 2

    broken = tmp_path / "broken.yaml"
    broken.write_text("clauses: [oops\n", encoding="utf-8")
    assert vd.main(["--contract", str(broken), "--draft", str(draft_path)]) == 2


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c.update({"clauses": []}), "non-empty"),
    (lambda c: c.update({"clauses": "nope"}), "non-empty"),
    (lambda c: c["clauses"][0].update({"level": "fatal"}), "level must be"),
    (lambda c: c["clauses"][0].update({"id": ""}), "missing required"),
    (lambda c: c["clauses"][0].update({"target": [0.5, 0.1]}), "lo .* > hi"),
    (lambda c: c["clauses"][0].update({"target": [0.5]}), "null or a \\[lo, hi\\] pair"),
    (lambda c: c["clauses"].append(dict(c["clauses"][0])), "duplicate clause id"),
])
def test_check_contract_rejects_malformed_contracts(mutate, message):
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    mutate(contract)
    with pytest.raises(vd.ValidatorError, match=message):
        vd.check_contract(contract)


def test_validate_wraps_provider_exceptions_as_validator_errors():
    def boom(text: str) -> dict:
        raise RuntimeError("provider exploded")

    with pytest.raises(vd.ValidatorError, match="metrics computation failed"):
        _validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]), "draft\n", boom)


def test_broken_metrics_provider_exits_2_not_1(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(
        _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])), encoding="utf-8")
    draft_path = tmp_path / "draft.md"
    draft_path.write_text("hedging draft line\n", encoding="utf-8")
    (tmp_path / "fake_metrics_broken.py").write_text(
        "def compute(text):\n    raise ValueError('provider exploded')\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        lang_module = _write_lang_module(tmp_path, name="fake_lang_broken")
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--compute-module", "fake_metrics_broken",
                      "--language-module", lang_module])
    finally:
        sys.path.remove(str(tmp_path))
    assert rc == 2
    assert "metrics computation failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 10. integration with the real metrics layer (skipped until it lands)
# ---------------------------------------------------------------------------

def test_integration_with_real_text_metrics_layer():
    pytest.importorskip("lexicon_loader")
    pytest.importorskip("text_metrics")
    contract = _contract([_clause("M-SLEN-01", "gate", [1.0, 999.0]),
                          _clause("M-HED-14", "warn", [0.0, 1.0])])
    sentence = ("The proposed tensor based GPU acceleration framework reduces the wall clock "
                "time of the vehicle routing local search while preserving exactness ")
    draft = (sentence * 5).strip() + ".\n"
    # default guard thresholds: a realistic English draft must still be judged by its gates
    result = _validate_guarded(contract, draft, draft_name="draft.md")
    for clause in result["clauses"]:
        assert clause["status"] in vd.VALID_STATUSES
    assert any(clause["actual"] is not None for clause in result["clauses"])
    assert result["draft_validity"]["status"] == "ok"
    assert result["draft_validity"]["alpha_tokens"] >= vd.MIN_ALPHA_TOKENS
    assert result["exit_code"] in (0, 1)
    assert FORBIDDEN_TEXT_RE.search(_dump(result)) is None


# ---------------------------------------------------------------------------
# 11. degenerate input (P0): an all-skipped draft must never pass
# ---------------------------------------------------------------------------

def _gate_contract(n_clauses: int = 3) -> dict:
    return _contract([_clause(f"M-TEST-{index:02d}", "gate", [0.1, 0.2])
                      for index in range(n_clauses)])


def test_pure_chinese_draft_is_insufficient_evidence():
    contract = _gate_contract()
    draft = "# 1 \u7eea\u8bba\n\n\u7eaf\u4e2d\u6587\u65e0\u82f1\u6587\u5185\u5bb9\n"
    result = _validate_guarded(contract, draft, _compute({}))          # default guard thresholds
    assert [clause["status"] for clause in result["clauses"]] == ["skipped"] * 3
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert result["draft_validity"]["alpha_tokens"] == 0
    assert any("alpha_tokens=0" in reason for reason in result["draft_validity"]["reasons"])
    assert result["exit_code"] == 2                              # never a silent PASS
    assert result["summary"]["gate_failures"] == []


@pytest.mark.parametrize("draft", ["", "   \n\n", "# \u7ae0\u8282\u6807\u9898\n",
                                   "\u0060\u0060\u0060\ncode\n\u0060\u0060\u0060\n"])
def test_empty_and_heading_only_drafts_are_insufficient_evidence(draft):
    result = _validate_guarded(_gate_contract(), draft, _compute({}))
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert result["exit_code"] == 2


def test_low_evaluable_ratio_is_insufficient_evidence_even_with_enough_tokens():
    contract = _gate_contract(4)
    compute = _compute({"M-TEST-00": 0.15})       # only 1 of 4 clauses can be evaluated
    result = _validate_guarded(contract, "word " * 60, compute)
    validity = result["draft_validity"]
    assert validity["status"] == "insufficient_evidence"
    assert validity["n_evaluable"] == 1 and validity["n_clauses"] == 4
    assert any("evaluable_clauses=1/4" in reason for reason in validity["reasons"])
    assert set(validity["skipped_reasons"]) == {"M-TEST-01", "M-TEST-02", "M-TEST-03"}
    assert result["exit_code"] == 2


@pytest.mark.parametrize("actual, expected_exit", [(0.15, 0), (0.9, 1)])
def test_normal_english_draft_keeps_the_gate_verdict(actual, expected_exit):
    contract = _contract([_clause("M-TEST-00", "gate", [0.1, 0.2])])
    result = _validate_guarded(contract, "word " * 60, _compute({"M-TEST-00": actual}))
    assert result["draft_validity"]["status"] == "ok"
    assert result["exit_code"] == expected_exit


def test_cli_reports_insufficient_evidence_and_exits_2(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_gate_contract(2)), encoding="utf-8")
    draft_path = tmp_path / "c.md"
    draft_path.write_text("# \u7eea\u8bba\n\n\u7eaf\u4e2d\u6587\u65e0\u82f1\u6587\u5185\u5bb9\n",
                          encoding="utf-8")
    (tmp_path / "fake_metrics_cjk.py").write_text(
        "def compute(text):\n    return {}\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        lang_module = _write_lang_module(tmp_path, name="fake_lang_ok")
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--compute-module", "fake_metrics_cjk",
                      "--language-module", lang_module,
                      "--json", str(tmp_path / "report.json")])
    finally:
        sys.path.remove(str(tmp_path))
    stdout = capsys.readouterr().out
    assert rc == 2
    assert "INSUFFICIENT EVIDENCE" in stdout
    assert "skipped reasons" in stdout
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["exit_code"] == 2
    assert payload["draft_validity"]["status"] == "insufficient_evidence"


# ---------------------------------------------------------------------------
# 12. warn-only contracts (P1) and NFC normalization
# ---------------------------------------------------------------------------

def _warn_only_contract() -> dict:
    contract = _contract([_clause("M-HED-14", "warn", [0.01, 0.05]),
                          _clause("M-BOO-15", "warn", [0.01, 0.02])])
    contract["generated_from"]["gate_mode"] = "warn-only"
    return contract


def test_warn_only_contract_exits_0_but_reports_warn_failures_loudly():
    contract = _warn_only_contract()
    result = _validate_guarded(contract, "word " * 60, _compute({"M-HED-14": 0.09, "M-BOO-15": 0.015}))
    summary = result["summary"]
    assert result["exit_code"] == 0                      # no gate clause -> no gate failure
    assert summary["n_gate_clauses"] == 0
    assert summary["n_warn_failures"] == 1
    assert summary["warn_failures"] == ["C-M-HED-14"]
    assert summary["gate_mode"] == "warn-only"
    assert "no gate clause" in (summary["note"] or "")
    assert summary["n_fail"] == 0

    report = vd.render_report(result)
    assert "NO GATE CLAUSES (warn-only)" in report
    assert "WARN FAILURES (1" in report
    assert "gate_mode=warn-only" in report


def test_contract_without_gate_mode_field_is_inferred_from_the_clauses():
    contract = _warn_only_contract()
    del contract["generated_from"]["gate_mode"]
    result = _validate_guarded(contract, "word " * 60, _compute({"M-HED-14": 0.02, "M-BOO-15": 0.015}))
    assert result["summary"]["gate_mode"] == "warn-only"
    assert result["summary"]["note"] is not None

    gate_contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    gate_result = _validate_guarded(gate_contract, "word " * 60, _compute({"M-HED-14": 0.02}))
    assert gate_result["summary"]["gate_mode"] == "quantile-gate"
    assert gate_result["summary"]["note"] is None


def test_warn_failures_do_not_change_the_exit_code_but_are_in_the_json():
    contract = _warn_only_contract()
    result = _validate_guarded(contract, "word " * 60, _compute({"M-HED-14": 5.0, "M-BOO-15": 5.0}))
    payload = json.loads(_dump(result))
    assert payload["exit_code"] == 0
    assert payload["summary"]["warn_failures"] == ["C-M-HED-14", "C-M-BOO-15"]
    assert payload["summary"]["n_warn_failures"] == 2


def test_draft_is_nfc_normalized_before_any_metric_math():
    decomposed = "cafe\u0301 re\u0301sume\u0301 " + "word " * 60
    composed = unicodedata.normalize("NFC", decomposed)
    assert decomposed != composed
    compute = _compute({"M-HED-14": 0.02})
    result = _validate_guarded(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]), decomposed, compute)
    assert compute.calls == [composed]                 # the metrics layer sees NFC text
    assert result["draft"]["nfc_normalized"] is True
    assert result["draft"]["n_chars"] == len(composed)


# ---------------------------------------------------------------------------
# 13. real-corpus regression for the warn-only decision (34 published papers)
# ---------------------------------------------------------------------------

def _artifact_dir() -> Path | None:
    for directory in ARTIFACT_DIRS:
        if ((directory / "_corpus_summary.json").is_file()
                and (directory / "_per_paper_metrics.jsonl").is_file()):
            return directory
    return None


@pytest.mark.skipif(not REAL_CORPUS.is_dir(), reason="real VRP corpus not mounted")
def test_real_corpus_warn_only_contract_never_fails_the_gate(tmp_path):
    """Every published paper may miss the middle-50% band, but must never trip a gate."""
    artifacts = _artifact_dir()
    if artifacts is None:
        pytest.skip("no _corpus_summary.json + _per_paper_metrics.jsonl next to the corpus")
    profiler = pytest.importorskip("profile_papers")
    pytest.importorskip("text_metrics")
    pytest.importorskip("lexicon_loader")

    contract_path = tmp_path / "_writing_contract.yaml"
    assert bc.main(["--summary", str(artifacts / "_corpus_summary.json"),
                    "--out", str(contract_path), "--quiet"]) == 0
    contract = vd.load_contract(contract_path)
    assert contract["generated_from"]["gate_mode"] == "warn-only"
    assert all(clause["level"] == "warn" for clause in contract["clauses"])

    compute = vd.load_default_compute()
    papers = profiler.discover_papers(REAL_CORPUS)
    assert len(papers) >= 30
    total_warn_failures = 0
    for paper in papers:
        paper.load_blocks()
        result = _validate_guarded(contract, paper.canonical_text(), compute)
        assert result["exit_code"] == 0, f"{paper.name}: {result['summary']}"
        assert result["summary"]["n_gate_clauses"] == 0
        assert result["summary"]["note"] is not None
        assert result["draft_validity"]["status"] == "ok", paper.name
        total_warn_failures += result["summary"]["n_warn_failures"]
    # the corpus really does fall outside its own p25/p75 band -> warn-only is required
    assert total_warn_failures > 0


# ---------------------------------------------------------------------------
# 13b. Issue #24: Chinese end-to-end profile -> contract -> validate_draft
# ---------------------------------------------------------------------------

#: The registered Chinese baseline (运筹与管理). Local by convention; the test
#: skips cleanly when the drive is not mounted.
ZH_CORPUS = Path("/mnt/e/AllProjects202601/M-PCA/_paper-metrics-run/"
                 "运筹与管理/corpus_ycgl_pdf/paper-conversion")
_ZH_ARTIFACT_DIRS = (Path("/tmp/kzyjc-v6"), Path("/tmp/audit-ycgl2"),
                     Path("/tmp/reg-ycgl6"), Path("/tmp/zh-prof"))
#: The four imported manuscript versions (canonical_import); same local rule.
_MPCA_CORPUS = Path("/tmp/mpca-corpus")
_MPCA_ARTIFACT_DIRS = (Path("/tmp/mpca-v6"),)


def _zh_artifact_dir() -> Path | None:
    for directory in _ZH_ARTIFACT_DIRS:
        if ((directory / "_corpus_summary.json").is_file()
                and (directory / "_per_paper_metrics.jsonl").is_file()):
            return directory
    return None


def _mpca_artifact_dir() -> Path | None:
    for directory in _MPCA_ARTIFACT_DIRS:
        if ((directory / "_corpus_summary.json").is_file()
                and (directory / "_per_paper_metrics.jsonl").is_file()):
            return directory
    return None


@pytest.mark.skipif(_zh_artifact_dir() is None or not ZH_CORPUS.is_dir(),
                    reason="registered zh corpus + profiled artifacts not present")
def test_zh_corpus_contract_is_built_and_validates_a_real_chinese_paper(tmp_path):
    """Issue #24: the Chinese path must work end to end on real prose.

    Builds a contract from the registered Chinese corpus, then validates a real
    Chinese paper through it.  The layer that matters for #22 is checked here:
    the clause metrics must be part of the contract and must be measured on the
    draft, not silently dropped.
    """
    artifacts = _zh_artifact_dir()
    profiler = pytest.importorskip("profile_papers")

    contract_path = tmp_path / "_writing_contract.zh.yaml"
    assert bc.main(["--summary", str(artifacts / "_corpus_summary.json"),
                    "--out", str(contract_path), "--quiet"]) == 0
    contract = vd.load_contract(contract_path)

    clause_metrics = {"M-CLS-31", "M-CLS-32", "M-SLEN-34", "M-SLEN-35"}
    contract_metrics = {clause["metric"] for clause in contract["clauses"]}
    # the clause layer is part of the contract, or the layer is disconnected
    assert clause_metrics <= contract_metrics, (
        sorted(clause_metrics - contract_metrics))

    compute = vd.load_default_compute()
    papers = profiler.discover_papers(ZH_CORPUS)
    assert len(papers) >= 5
    paper = papers[0]
    paper.load_blocks()
    text = paper.canonical_text()
    assert len(text) > 1000

    result = _validate_guarded(contract, text, compute)
    assert result["exit_code"] == 0
    assert result["draft_validity"]["status"] == "ok"
    assert result["summary"]["n_gate_clauses"] == 0
    # a real paper falls outside its own corpus band somewhere
    assert result["summary"]["n_warn_failures"] > 0

    # the clause layer was actually measured on the draft, with cjk units
    produced = {clause["metric"]: clause for clause in result["clauses"]}
    for metric_id in ("M-CLS-31", "M-SLEN-34"):
        record = produced[metric_id]
        assert record["actual"] is not None, metric_id
        assert record["status"] in ("pass", "warn"), metric_id


@pytest.mark.skipif(_zh_artifact_dir() is not None,
                    reason="covered by the real-corpus e2e above; this pins the")
def test_zh_clause_metrics_are_contract_checkable_without_external_data():
    """The clause layer must round-trip through contract + validator on its own.

    Uses the same fake provider as the rest of the suite, so it runs anywhere.
    What is pinned: a zh-only clause metric in a gate contract is checked, its
    cjk unit is carried through, and the validator reports the miss.
    """
    contract = _contract([
        _clause("M-CLS-31", "gate", [3.5, 4.5]),
        _clause("M-CLS-32", "gate", [12.0, 16.0]),
        _clause("M-SLEN-34", "warn", [90.0, 110.0]),
    ])
    # a draft that misses the clauses/sentence band but hits the others
    result = _validate(contract, "正文\n",
                       _compute({"M-CLS-31": 2.0, "M-CLS-32": 14.0,
                                 "M-SLEN-34": 100.0}))
    assert result["exit_code"] == 1
    by_metric = {c["metric"]: c for c in result["clauses"]}
    assert by_metric["M-CLS-31"]["status"] == "fail"
    assert by_metric["M-CLS-32"]["status"] == "pass"
    assert by_metric["M-SLEN-34"]["status"] == "pass"


@pytest.mark.skipif(_mpca_artifact_dir() is None or not _MPCA_CORPUS.is_dir(),
                    reason="imported manuscript corpus + artifacts not present")
def test_zh_imported_draft_validates_against_journal_contract(tmp_path):
    """A canonical_import draft against a journal contract: the rejection case.

    The v1 rejected manuscript under-hedges relative to the journal baseline, so
    the hedge clause must report a warn miss with draft line numbers - the whole
    point of the layer is that a human can open the line and see the sentence.
    """
    artifacts = _mpca_artifact_dir()
    journal = _zh_artifact_dir()
    if journal is None:
        pytest.skip("no journal baseline artifacts")
    profiler = pytest.importorskip("profile_papers")

    contract_path = tmp_path / "_writing_contract.kzyjc.yaml"
    assert bc.main(["--summary", str(journal / "_corpus_summary.json"),
                    "--out", str(contract_path), "--quiet"]) == 0
    contract = vd.load_contract(contract_path)

    compute = vd.load_default_compute()
    papers = profiler.discover_papers(_MPCA_CORPUS)
    by_name = {p.name: p for p in papers}
    v1 = by_name.get("mpca-v1-rejected")
    if v1 is None:
        pytest.skip("run canonical_import for the four manuscript versions first")
    v1.load_blocks()
    text = v1.canonical_text()
    assert len(text) > 1000

    result = _validate_guarded(contract, text, compute)
    assert result["exit_code"] == 0  # warn-only: misses never trip the gate
    hedge = next(c for c in result["clauses"] if c["metric"] == "M-HED-14")
    # the measured direction of the rejection analysis: v1 under-hedges
    assert hedge["actual"] is not None
    assert hedge["actual"] < hedge["target"][0]
    assert hedge["status"] == "warn"
    assert hedge["evidence_lines"], "a warn miss must point at draft lines"


# ---------------------------------------------------------------------------
# 14. F1 regression: derived clauses must be compatible with their components
# ---------------------------------------------------------------------------

def _conn_contract(gate_mode: str = "quantile-gate") -> dict:
    """Component bands summing BELOW the independently sampled total band."""
    def stats(p25: float, p75: float, **over) -> dict:
        base = {"unit": "per-1000-words", "n_valid": 34, "p25": p25, "p75": p75,
                "iqr": round(p75 - p25, 6), "median": p25, "missing_papers": []}
        base.update(over)
        return base

    summary = {
        "schema_version": "2.0", "analysis_unit": "paper", "weight_mode": "equal_paper",
        "n_papers": 34, "corpus_id": "c" * 64, "corpus_warnings": [],
        "metrics": {
            "M-CONN-30": stats(0.014, 0.030),      # independent quantile (incompatible)
            "M-CONN-30c": stats(0.004, 0.009),
            "M-CONN-30k": stats(0.007, 0.014),
            "M-CONN-30r": stats(0.001, 0.003),
        },
    }
    return bc.build_contract(summary, summary_sha256="0" * 64,
                             source_file="_corpus_summary.json", gate_mode=gate_mode)


def test_f1_components_at_their_lower_bounds_pass_the_total_gate():
    contract = _conn_contract()
    by_metric = {clause["metric"]: clause for clause in contract["clauses"]}
    independent_lo = 0.014                      # what an independently sampled p25 gave
    component_sum_lo = 0.012                    # Sigma of the component lower bounds
    assert by_metric["M-CONN-30"]["target"][0] == component_sum_lo
    assert component_sum_lo < independent_lo    # F1 is real: Sigma(p25) < p25(total)

    # a draft whose three components sit exactly on their lower bounds: the total is
    # then at its minimum, and it must NOT be rejected by the total clause
    compute = _compute({"M-CONN-30c": 0.004, "M-CONN-30k": 0.007, "M-CONN-30r": 0.001,
                        "M-CONN-30": 0.012})
    result = _validate_guarded(contract, "word " * 60, compute)
    statuses = {clause["metric"]: clause["status"] for clause in result["clauses"]}
    assert statuses == {"M-CONN-30": "pass", "M-CONN-30c": "pass",
                        "M-CONN-30k": "pass", "M-CONN-30r": "pass"}
    assert result["summary"]["gate_failures"] == []
    assert result["exit_code"] == 0

    # and the same draft against the old independent band would have failed
    legacy = dict(contract)
    legacy["clauses"] = [dict(clause) for clause in contract["clauses"]]
    for clause in legacy["clauses"]:
        if clause["metric"] == "M-CONN-30":
            clause["target"] = [independent_lo, 0.030]
    legacy_result = _validate_guarded(legacy, "word " * 60, compute)
    legacy_status = {clause["metric"]: clause["status"] for clause in legacy_result["clauses"]}
    assert legacy_status["M-CONN-30"] == "fail"
    assert legacy_result["exit_code"] == 1


# ---------------------------------------------------------------------------
# 15. A4: an unsupported language is refused, never pseudo-judged
# ---------------------------------------------------------------------------

CHINESE_DRAFT = "# 绪论\n\n本文研究车辆路径问题的 GPU 加速方法，共 1296 个字。\n"


def test_short_chinese_draft_is_insufficient_evidence_not_unsupported():
    """Issue #13: Chinese is measurable, so a stub draft is refused for being too
    short - not for being the wrong language.

    The historical behavior refused Chinese outright and never called the metrics
    layer. Now the clauses are graded and only the draft size keeps it honest.
    """
    contract = _gate_contract(3)
    compute = _compute({"M-TEST-00": 0.9})
    result = vd.validate(contract, CHINESE_DRAFT, compute, language_detector=CJK_DETECTOR)
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert result["exit_code"] == 2
    assert any("cjk_units=" in reason for reason in result["draft_validity"]["reasons"])
    language = result["draft_language"]
    assert language["cjk_ratio"] == 0.9235
    assert language["language"] == "zh"
    assert compute.calls, "the metrics provider must be called for a Chinese draft"
    assert result["draft_validity"]["cjk_units"] is not None
    report = vd.render_report(result)
    assert "INSUFFICIENT" in report
    assert "UNSUPPORTED LANGUAGE" not in report


def test_language_without_rules_is_refused_before_any_gate_math():
    """The A4 principle survives: a language with no validated rules gets no verdict."""
    no_rules = _detector(supported=False, language="ja", cjk_ratio=None)
    compute = _compute({"M-TEST-00": 0.9})
    result = vd.validate(_gate_contract(1), CHINESE_DRAFT, compute,
                         language_detector=no_rules)
    assert compute.calls == []                      # the provider is never called
    assert result["draft"]["n_paragraph_blocks"] == 0
    assert result["summary"]["n_evaluable"] == 0
    assert result["summary"]["n_skipped"] == 1
    assert {clause["status_reason"] for clause in result["clauses"]} == {"language_unsupported"}
    assert result["draft_validity"]["status"] == "language_unsupported"
    assert result["exit_code"] == 2


def test_chinese_draft_reports_language_supported_in_both_layers():
    """Issue #19: the metadata layer must agree with the clause logic that
    Chinese has validated rules, instead of parroting detect_language()'s
    supported=False (the Round-A English-majority contract)."""
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    result = _validate(contract, CHINESE_DRAFT, _compute({"M-HED-14": 0.02}),
                       language_detector=CJK_DETECTOR)
    assert result["draft_language"]["supported"] is True
    assert result["draft_validity"]["language_supported"] is True
    assert result["draft_validity"]["status"] != "language_unsupported"
    assert result["draft_language"]["supported_languages"] == ["en", "zh"]


def test_detector_supported_flag_is_archived_not_trusted_for_zh():
    verdict = vd.normalize_language_verdict(
        {"language": "zh-Hans", "supported": False, "cjk_ratio": 0.9235}, "fake")
    assert verdict["available"] is True
    assert verdict["supported"] is True
    assert verdict["detector_supported"] is False
    assert verdict["supported_languages"] == ["en", "zh"]


def test_language_without_rules_is_blocked_even_when_detector_says_supported():
    detector = _detector(supported=True, language="ja", cjk_ratio=0.9)
    compute = _compute({"M-TEST-00": 0.9})
    result = vd.validate(_gate_contract(1), CHINESE_DRAFT, compute,
                         language_detector=detector)
    assert result["draft_language"]["supported"] is False
    assert result["draft_validity"]["language_supported"] is False
    assert result["draft_validity"]["status"] == "language_unsupported"
    assert result["exit_code"] == 2
    assert compute.calls == []


def test_english_draft_behavior_is_unchanged():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    inside = vd.validate(contract, "word " * 60, _compute({"M-HED-14": 0.02}),
                         language_detector=PERMISSIVE_DETECTOR)
    assert inside["draft_language"]["supported"] is True
    assert inside["draft_validity"]["status"] == "ok"
    assert inside["exit_code"] == 0
    outside = vd.validate(contract, "word " * 60, _compute({"M-HED-14": 0.9}),
                          language_detector=PERMISSIVE_DETECTOR)
    assert outside["exit_code"] == 1
    assert outside["summary"]["gate_failures"] == ["C-M-HED-14"]


def test_mixed_draft_below_the_cjk_threshold_is_validated_normally():
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    draft = "mixed 中英 draft words more text " * 20      # ~100 alpha tokens
    result = vd.validate(contract, draft, _compute({"M-HED-14": 0.02}),
                         language_detector=_detector(supported=True, cjk_ratio=0.1))
    assert result["draft_language"]["cjk_ratio"] == 0.1
    assert result["draft_validity"]["status"] == "ok"
    assert result["exit_code"] == 0


def test_mixed_draft_above_the_cjk_threshold_is_graded_as_chinese():
    """A mixed draft past the CJK threshold is Chinese for the metrics layer, so it
    is graded against the contract instead of being refused."""
    contract = _contract([_clause("M-HED-14", "gate", [0.01, 0.05])])
    draft = "mixed 中英 draft words more text " * 20
    result = vd.validate(contract, draft, _compute({"M-HED-14": 0.02}),
                         language_detector=_detector(supported=False, language="zh", cjk_ratio=0.11))
    assert result["draft_language"]["cjk_ratio"] == 0.11
    assert result["draft_validity"]["status"] == "ok"
    assert result["exit_code"] == 0


@pytest.mark.parametrize("raw, expected_supported, expected_exit", [
    ("en", True, 0), ("zh", True, 0), (True, True, 0), (False, False, 2)])
def test_simple_language_verdicts_are_accepted(raw, expected_supported, expected_exit):
    result = vd.validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]),
                         "word " * 60, _compute({"M-HED-14": 0.02}),
                         language_detector=lambda text: raw)
    assert result["draft_language"]["available"] is True
    assert result["draft_language"]["supported"] is expected_supported
    # "zh" is supported (the layer has Chinese rules); only a language with no
    # rules at all - here the bare boolean False - is refused.
    assert result["exit_code"] == expected_exit


@pytest.mark.parametrize("raw", [None, 42, {"unexpected": True}, object()])
def test_unusable_language_verdict_is_reported_never_guessed(raw):
    result = vd.validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]),
                         "word " * 60, _compute({"M-HED-14": 0.02}),
                         language_detector=lambda text: raw)
    assert result["draft_language"]["available"] is False
    assert result["draft_language"]["supported"] is None
    assert any("unusable detect_language" in warning
               for warning in result["draft_language"]["warnings"])
    assert any("language:" in warning for warning in result["summary"]["warnings"])
    assert result["exit_code"] == 0                 # English behavior is unchanged


def test_language_detector_unavailable_keeps_validating_and_says_so(monkeypatch):
    monkeypatch.setattr(vd, "load_language_detector", lambda *args, **kwargs: None)
    result = vd.validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]),
                         "word " * 60, _compute({"M-HED-14": 0.02}))
    assert result["draft_language"]["available"] is False
    assert any("language detection unavailable" in warning
               for warning in result["draft_language"]["warnings"])
    assert result["exit_code"] == 0


def test_require_language_detector_refuses_when_it_is_unavailable(monkeypatch):
    monkeypatch.setattr(vd, "load_language_detector", lambda *args, **kwargs: None)
    kwargs = {"language_detector": None, "require_language_detector": True}
    result = vd.validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]),
                         "word " * 60, _compute({"M-HED-14": 0.02}), **kwargs)
    assert result["draft_language"]["available"] is False
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert any("language detection unavailable" in reason
               for reason in result["draft_validity"]["reasons"])
    assert result["exit_code"] == 2
    # opt-out (the default) keeps validating: English behavior is unchanged
    default = vd.validate(_contract([_clause("M-HED-14", "gate", [0.01, 0.05])]),
                          "word " * 60, _compute({"M-HED-14": 0.02}), **{"language_detector": None})
    assert default["exit_code"] == 0


def test_cli_require_language_detector_when_missing(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_gate_contract(1)), encoding="utf-8")
    draft_path = tmp_path / "d.md"
    draft_path.write_text("word " * 60, encoding="utf-8")
    (tmp_path / "fake_lang_absent.py").write_text("OTHER = 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        strict = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                          "--language-module", "fake_lang_absent",
                          "--require-language-detector", "--quiet"])
    finally:
        sys.path.remove(str(tmp_path))
    assert strict == 2                       # --language-module must exist, so this is an error


def test_all_targets_null_and_all_skipped_exits_2_even_with_relaxed_thresholds():
    contract = _contract([_clause("M-HED-14", "gate", None),
                          _clause("M-BOO-15", "warn", None)])
    result = _validate(contract, "word " * 60, _compute({"M-HED-14": 0.02, "M-BOO-15": 0.01}))
    assert {clause["status"] for clause in result["clauses"]} == {"skipped"}
    assert result["draft_validity"]["status"] == "insufficient_evidence"
    assert any("no evaluable clause" in reason for reason in result["draft_validity"]["reasons"])
    assert result["exit_code"] == 2


def test_cli_unsupported_language_exit_2_with_ratio_and_note(tmp_path, capsys):
    """A language with no validated rules is still refused verbatim (issue #13
    narrowed the gate; it did not remove it)."""
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_gate_contract(2)), encoding="utf-8")
    draft_path = tmp_path / "初稿.md"
    draft_path.write_text(CHINESE_DRAFT, encoding="utf-8")
    lang_module = _write_lang_module(tmp_path, supported=False, language="ja",
                                     cjk_ratio=0.9, name="fake_lang_ja")
    sys.path.insert(0, str(tmp_path))
    try:
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--language-module", lang_module,
                      "--json", str(tmp_path / "report.json")])
    finally:
        sys.path.remove(str(tmp_path))
    stdout = capsys.readouterr().out
    assert rc == 2
    assert "UNSUPPORTED LANGUAGE" in stdout
    assert "cjk_ratio=0.9" in stdout
    assert "warn-only" not in stdout
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["draft_validity"]["status"] == "language_unsupported"
    assert payload["draft_language"]["cjk_ratio"] == 0.9
    assert payload["exit_code"] == 2


def test_cli_chinese_draft_is_not_refused_as_a_language(tmp_path, capsys):
    """The same CLI path with the real detector + a Chinese draft: no
    UNSUPPORTED LANGUAGE verdict, because Chinese is measurable now."""
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_gate_contract(2)), encoding="utf-8")
    draft_path = tmp_path / "初稿.md"
    draft_path.write_text("# 绪论\n\n" + "本文研究车辆路径问题的加速方法。" * 40,
                          encoding="utf-8")
    rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                  "--json", str(tmp_path / "report.json")])
    stdout = capsys.readouterr().out
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["draft_language"]["language"] == "zh"
    assert payload["draft_validity"]["status"] != "language_unsupported"
    assert "UNSUPPORTED LANGUAGE" not in stdout
    assert rc in (0, 1, 2)


def test_cli_rejects_a_language_module_without_detect_language(tmp_path, capsys):
    contract_path = tmp_path / "_writing_contract.yaml"
    contract_path.write_text(bc.dump_yaml(_gate_contract(1)), encoding="utf-8")
    draft_path = tmp_path / "d.md"
    draft_path.write_text("word " * 60, encoding="utf-8")
    (tmp_path / "fake_lang_empty.py").write_text("VALUE = 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        rc = vd.main(["--contract", str(contract_path), "--draft", str(draft_path),
                      "--language-module", "fake_lang_empty"])
    finally:
        sys.path.remove(str(tmp_path))
    assert rc == 2
    assert "must expose detect_language" in capsys.readouterr().err


# --- integration with the real detector (skips until metrics-text lands it) ---

def _real_detector():
    text_metrics = pytest.importorskip("text_metrics")
    detector = getattr(text_metrics, "detect_language", None)
    if not callable(detector):
        pytest.skip("text_metrics.detect_language has not landed yet (issue #10)")
    return detector


def test_integration_chinese_draft_is_graded_by_the_real_detector():
    """Real detector + real Chinese text: "zh" is supported, not refused (issue #13/#19)."""
    detector = _real_detector()
    draft = "# 绪论\n\n" + "本文研究车辆路径问题的 GPU 加速方法。" * 30
    verdict = vd.normalize_language_verdict(detector(draft), "real")
    if verdict["language"] != "zh":
        pytest.skip(f"real detector did not classify the draft as zh: {verdict!r}")
    assert verdict["supported"] is True
    result = vd.validate(_gate_contract(2), draft, _compute({}))
    assert result["draft_language"]["language"] == "zh"
    assert result["draft_validity"]["status"] != "language_unsupported"


def test_integration_mixed_drafts_around_the_real_cjk_threshold():
    detector = _real_detector()
    mostly_english = "the proposed method improves vehicle routing results " * 40 + "中"
    mostly_chinese = "the proposed method improves results 中文中文中文中文中文中文 " * 40
    low = vd.normalize_language_verdict(detector(mostly_english), "real")
    high = vd.normalize_language_verdict(detector(mostly_chinese), "real")
    assert low["available"] and high["available"]
    assert low["cjk_ratio"] is not None and high["cjk_ratio"] is not None
    assert low["cjk_ratio"] <= 0.10 < high["cjk_ratio"]
    # both languages have rules, so both are supported even across the CJK
    # threshold that flips detect_language()'s own flag
    assert low["supported"] is True and high["supported"] is True
