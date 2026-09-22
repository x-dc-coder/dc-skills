#!/usr/bin/env python3
"""Determinism / reproducibility contract tests for profile_papers.py (v2).

The profiler promises a bit-level guarantee for the OBSERVED layer:
    Canonical Document -> _domain_profile.json / _per_paper_metrics.jsonl /
    _corpus_summary.json
_run_meta.json is explicitly OUTSIDE the guarantee (wall-clock, host, paths).

Test map (T1-T10, mirroring the review report section 3.5):
    T1  same corpus, two runs in one process   -> byte-identical
    T2  volatile-field audit                   -> generated_at only in _run_meta.json
    T3  PYTHONHASHSEED invariance              -> separate processes, different seeds
    T4  path invariance                        -> spaces / CJK / trailing slash
    T5  directory enumeration order            -> different creation order
    T6  locale invariance                      -> LANG=C vs C.UTF-8
    T7  JSON canonicalisation idempotence      -> fixed point, no NaN/Infinity
    T8  no LLM and no network in OBSERVED path -> static source check
    T9  cross-process reproducibility          -> separate interpreter, same bytes
    T10 _run_meta.json is the only volatile carrier

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_determinism.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import profile_papers as pp  # noqa: E402

SCRIPT = SCRIPTS_DIR / "profile_papers.py"
REPO_ROOT = SCRIPTS_DIR.parent.parent

FINGERPRINTED = pp.FINGERPRINTED_FILES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _block(btype: str, text: str = "", level: int | None = None) -> dict:
    b: dict = {"type": btype, "bbox": [0, 0, 100, 20], "page_idx": 0}
    if btype == "text":
        b["text"] = text
        if level is not None:
            b["text_level"] = level
    return b


def _write_paper(corpus: Path, name: str, author: str, cites: str) -> None:
    d = corpus / name / "mineru" / author / "auto"
    d.mkdir(parents=True, exist_ok=True)
    blocks = [
        _block("text", f"Title of {name}", level=1),
        _block("text", "Abstract", level=2),
        _block("text", f"Abstract body for {name}."),
        _block("text", "1 Introduction", level=2),
        _block("text", f"Previous studies {cites} reported gains. "
                       "However, they do not consider GPU acceleration, which is "
                       "important for large instances. This finding suggests that "
                       "a tensor formulation may substantially reduce the cost."),
        _block("text", "2 Method", level=2),
        _block("text", "We propose a tensor based local search. The method was "
                       "implemented in CUDA and evaluated on standard benchmarks. "
                       "The results indicate a significant speedup. It is possible "
                       "that further gains are available, although the analysis is "
                       "limited to small instances."),
        _block("text", "3 Experiments", level=2),
        _block("text", "We compare against the baselines. The proposed method "
                       "outperforms them on most instances. Nevertheless, the "
                       "variance is large, and the effect is not significant for "
                       "the largest instances."),
        _block("text", "4 Conclusion", level=2),
        _block("text", "We conclude that the approach is effective."),
        _block("text", "References", level=2),
        {"type": "list", "bbox": [0, 0, 100, 20], "page_idx": 0,
         "list_items": ["[1] Smith, J. (2020). A study of routing. Journal A.",
                        "[2] Jones, A. and Lee, B. (2021). Another routing study. Conf B."]},
    ]
    (d / f"{author}_content_list.json").write_text(json.dumps(blocks), encoding="utf-8")


def _make_corpus(root: Path, names=("Paper Alpha", "Paper Beta", "Paper Gamma")) -> Path:
    corpus = root / "paper-analysis"
    cites = "[1] and [2]"
    for i, n in enumerate(names):
        _write_paper(corpus, n, f"p{i}", cites)
    return corpus


def _read_fingerprints(out: Path) -> dict:
    return {name: (out / name).read_bytes() for name in FINGERPRINTED}


def _normalized_profile(out: Path) -> str:
    """Profile JSON, with any legacy machine-specific field dropped.

    v2 keeps paths out of the fingerprinted artifact, so this is now a no-op for
    current output; the pop keeps the helper honest if a path field ever
    reappears (the test would then fail loudly rather than silently).
    """
    data = json.loads((out / "_domain_profile.json").read_text(encoding="utf-8"))
    data["meta"].pop("corpus_path", None)
    return pp.canonical_json_text(data)


# ---------------------------------------------------------------------------
# T1 - same corpus, two runs, byte-identical
# ---------------------------------------------------------------------------

def test_t1_two_runs_are_byte_identical(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    pp.run_profile(corpus, out1)
    pp.run_profile(corpus, out2)
    first, second = _read_fingerprints(out1), _read_fingerprints(out2)
    for name in FINGERPRINTED:
        assert first[name] == second[name], f"{name} differs between runs"


def test_t1b_verify_determinism_helper_reports_ok(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    result = pp.verify_determinism(corpus, out)
    assert result["ok"] is True, result["diffs"]
    assert set(result["checked"]) == set(FINGERPRINTED)


# ---------------------------------------------------------------------------
# T2 / T10 - volatile fields live only in _run_meta.json
# ---------------------------------------------------------------------------

def test_t2_fingerprint_has_no_volatile_fields(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    raw = (out / "_domain_profile.json").read_text(encoding="utf-8")
    assert "generated_at" not in raw
    assert "elapsed_ms" not in raw
    assert "host" not in raw
    meta = json.loads(raw)["meta"]
    # exactly three keys, none of them machine-specific
    assert set(meta) == {"profiler_version", "schema_version", "paper_count"}


def test_t10_run_meta_carries_the_volatile_fields(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    run_meta = json.loads((out / "_run_meta.json").read_text(encoding="utf-8"))
    for key in ("generated_at", "corpus_path", "out_dir", "elapsed_ms", "host",
                "python_version", "corpus_id"):
        assert key in run_meta, key


# ---------------------------------------------------------------------------
# T3 / T9 - seed independence and cross-process reproducibility
# ---------------------------------------------------------------------------

def _run_in_subprocess(corpus: Path, out: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(corpus), "--out", str(out),
         "--no-section-metrics"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )


def test_t3_pythonhashseed_invariance(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    outs = []
    for seed in ("0", "1", "424242"):
        out = tmp_path / f"seed_{seed}"
        proc = _run_in_subprocess(corpus, out, {"PYTHONHASHSEED": seed})
        assert proc.returncode == 0, proc.stderr
        outs.append(_read_fingerprints(out))
    for name in FINGERPRINTED:
        assert outs[0][name] == outs[1][name] == outs[2][name], \
            f"{name} depends on PYTHONHASHSEED (a set/dict iteration leak)"


def test_t9_cross_process_reproducibility(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    a, b = tmp_path / "pa", tmp_path / "pb"
    assert _run_in_subprocess(corpus, a, {}).returncode == 0
    assert _run_in_subprocess(corpus, b, {}).returncode == 0
    fa, fb = _read_fingerprints(a), _read_fingerprints(b)
    for name in FINGERPRINTED:
        assert fa[name] == fb[name], name


# ---------------------------------------------------------------------------
# T4 - path invariance (spaces / CJK / trailing slash)
# ---------------------------------------------------------------------------

def test_t4_path_invariance(tmp_path: Path) -> None:
    plain = _make_corpus(tmp_path / "plain")
    weird_root = tmp_path / "带 空格 的 目录"
    weird = _make_corpus(weird_root)
    out_a, out_b = tmp_path / "oa", tmp_path / "ob"
    pp.run_profile(plain, out_a)
    pp.run_profile(weird, out_b)
    assert _normalized_profile(out_a) == _normalized_profile(out_b)
    for name in FINGERPRINTED:
        if name == "_domain_profile.json":
            continue  # normalised separately above
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


def test_t4b_trailing_slash_is_harmless(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    before = _read_fingerprints(out)
    pp.run_profile(Path(str(corpus) + "/"), out)
    after = _read_fingerprints(out)
    for name in FINGERPRINTED:
        assert before[name] == after[name], name


# ---------------------------------------------------------------------------
# T5 - directory enumeration order must not matter
# ---------------------------------------------------------------------------

def test_t5_creation_order_invariance(tmp_path: Path) -> None:
    names = ("Paper Alpha", "Paper Beta", "Paper Gamma")
    forward = tmp_path / "fwd" / "paper-analysis"
    for i, n in enumerate(names):
        _write_paper(forward, n, f"p{i}", "[1] and [2]")
    reverse = tmp_path / "rev" / "paper-analysis"
    for i in list(range(len(names)))[::-1]:
        _write_paper(reverse, names[i], f"p{i}", "[1] and [2]")

    out_a, out_b = tmp_path / "oa", tmp_path / "ob"
    pp.run_profile(forward, out_a)
    pp.run_profile(reverse, out_b)
    assert _normalized_profile(out_a) == _normalized_profile(out_b)
    for name in FINGERPRINTED:
        if name == "_domain_profile.json":
            continue
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


# ---------------------------------------------------------------------------
# T6 - locale invariance
# ---------------------------------------------------------------------------

def test_t6_locale_invariance(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    outs = []
    for lang in ("C", "C.UTF-8"):
        out = tmp_path / ("loc_" + lang.replace(".", "_"))
        proc = _run_in_subprocess(corpus, out, {"LANG": lang, "LC_ALL": lang})
        assert proc.returncode == 0, proc.stderr
        outs.append(_read_fingerprints(out))
    for name in FINGERPRINTED:
        assert outs[0][name] == outs[1][name], name


# ---------------------------------------------------------------------------
# T7 - JSON canonicalisation is a fixed point and never emits NaN
# ---------------------------------------------------------------------------

def test_t7_canonical_json_is_idempotent(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    data = json.loads((out / "_domain_profile.json").read_text(encoding="utf-8"))
    once = pp.canonical_json_text(data)
    twice = pp.canonical_json_text(json.loads(once))
    assert once == twice
    assert "NaN" not in once and "Infinity" not in once


def test_t7b_round_floats_kills_nan_and_inf() -> None:
    payload = {"a": float("nan"), "b": float("inf"), "c": [1.00000049, 2.5]}
    cleaned = pp._round_floats(payload)
    assert cleaned["a"] is None and cleaned["b"] is None
    assert cleaned["c"][0] == 1.0


# ---------------------------------------------------------------------------
# T8 - the OBSERVED path must not call an LLM or the network
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(openai|anthropic|requests|urllib|socket|http\b|"
    r"numpy|spacy|transformers|torch|pandas)\b",
    re.MULTILINE,
)


def test_t8_no_llm_or_network_imports() -> None:
    for name in ("profile_papers.py", "text_metrics.py", "lexicon_loader.py"):
        path = SCRIPTS_DIR / name
        if not path.exists():
            pytest.skip(f"{name} not present yet")
        src = path.read_text(encoding="utf-8")
        found = _FORBIDDEN_IMPORT_RE.findall(src)
        assert not found, f"{name} imports forbidden modules: {found}"
        for token in ("openai", "anthropic", "api_key", "chat.completions"):
            assert token not in src.lower(), f"{name} mentions {token}"


def test_t8b_metrics_declare_observed_state(tmp_path: Path) -> None:
    """Red line: nothing in the OBSERVED layer may claim model provenance."""
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    pp.run_profile(corpus, out)
    records = [json.loads(line) for line in
               (out / "_per_paper_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records
    for rec in records:
        for mid, metric in rec["metrics"].items():
            assert metric["state"] == "OBSERVED", (mid, metric["state"])
            assert metric["method"] in {"rule", "deterministic"}, (mid, metric["method"])
