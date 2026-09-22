#!/usr/bin/env python3
"""Tests for paper-reader paper_reader.py module — non-GPU paths only.

Covers:
  1. _derive_output_dirs — output path derivation from PDF dir
  2. PipelineState — state file JSON serialization roundtrip
  3. _normalize_for_diff — text normalization for dual-engine merge
  4. engine version provenance — frozen _META.json contract, memoized probe,
     failure degrades to null + note (never raises, never blocks)
  5. _META.json provenance payload — pdf_sha256 + engine_versions, JSON parseable
  6. backfill — idempotent, never overwrites a conversion_time record

Does NOT test the Marker/MinerU engines (needs GPU).

Run:
    cd ~/projects/dc-skills && uv run pytest paper-reader/scripts/test_paper_reader.py -v
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Mock the GPU bridge module before importing paper_reader ──────────
# paper_reader does `from gpu_safe_subprocess import ...` at module level.
# This mock satisfies the import without touching real GPU resources.

_mock_gpu = MagicMock()
_mock_gpu.GpuLimits = MagicMock()
_mock_gpu.build_gpu_env = MagicMock(return_value={})
_mock_gpu.GpuGovernor = MagicMock()
_mock_gpu.GpuLease = MagicMock()
_mock_gpu.InsufficientGpuBudget = type("InsufficientGpuBudget", (Exception,), {})
sys.modules["gpu_safe_subprocess"] = _mock_gpu

import paper_reader as pr  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: _derive_output_dirs
# ---------------------------------------------------------------------------

def test_derive_output_dirs_from_pdf_dir() -> None:
    """_derive_output_dirs returns a dict with expected conversion/merged/summaries keys."""
    papers_dir = Path("/papers")
    dirs = pr._derive_output_dirs(papers_dir)

    assert isinstance(dirs, dict)
    assert dirs["conversion"] == papers_dir / "paper-conversion"
    assert dirs["merged"] == papers_dir / "paper-merged"
    assert dirs["summaries"] == papers_dir / "paper-summaries"


def test_derive_output_dirs_with_subpath() -> None:
    """_derive_output_dirs works with nested paths."""
    papers_dir = Path("/home/user/research/papers")
    dirs = pr._derive_output_dirs(papers_dir)

    assert dirs["conversion"] == papers_dir / "paper-conversion"
    assert dirs["merged"].parent == papers_dir


# ---------------------------------------------------------------------------
# Test 2: PipelineState serialization roundtrip
# ---------------------------------------------------------------------------

def test_pipeline_state_empty_roundtrip(tmp_path: Path) -> None:
    """New PipelineState saves minimal structure; re-reading returns same data."""
    state_path = tmp_path / "_pipeline_state.json"
    state = pr.PipelineState(state_path)
    assert not state.has("nonexistent")
    state.save()

    # Re-read
    state2 = pr.PipelineState(state_path)
    papers = state2._data.get("papers", {})
    assert isinstance(papers, dict)
    assert len(papers) == 0
    assert state2._data.get("pipeline_version") == "2.0"


def test_pipeline_state_with_entries_roundtrip(tmp_path: Path) -> None:
    """PipelineState with paper entries survives JSON roundtrip accurately."""
    state_path = tmp_path / "_pipeline_state.json"

    # Create and populate
    state = pr.PipelineState(state_path)
    entry = state.ensure_entry("test-paper")
    entry["source"] = {"title": "Test Paper", "doi": "10.1234/test"}
    entry["precheck"] = {"status": "passed", "page_count": 10, "pdf_hash": "abc123"}
    entry["phase1_converted"] = {"status": "done", "marker_ok": True, "mineru_ok": True}
    state.save()

    # Re-read
    state2 = pr.PipelineState(state_path)
    assert state2.has("test-paper")

    paper = state2.get("test-paper")
    assert paper["source"]["title"] == "Test Paper"
    assert paper["source"]["doi"] == "10.1234/test"
    assert paper["precheck"]["status"] == "passed"
    assert paper["precheck"]["page_count"] == 10
    assert paper["precheck"]["pdf_hash"] == "abc123"
    assert paper["phase1_converted"]["status"] == "done"
    assert paper["phase1_converted"]["marker_ok"] is True


def test_pipeline_state_ensure_entry_idempotent(tmp_path: Path) -> None:
    """Calling ensure_entry twice returns same entry and keeps existing data."""
    state = pr.PipelineState(tmp_path / "_pipeline_state.json")
    e1 = state.ensure_entry("my-paper")
    e1["source"] = {"title": "Original"}
    e2 = state.ensure_entry("my-paper")
    assert e2 is e1
    assert e2["source"]["title"] == "Original"


# ---------------------------------------------------------------------------
# Test 3: _normalize_for_diff (7-step normalization)
# ---------------------------------------------------------------------------

def test_normalize_plain_text_paragraph_merge() -> None:
    """Adjacent text lines merge into a single normalized paragraph."""
    raw = "This is line one.\nThis is the continuation.\n\nNew paragraph."
    result = pr._normalize_for_diff(raw)
    assert "This is line one. This is the continuation." in result
    assert "New paragraph." in result


def test_normalize_strips_headings_to_hash_hash() -> None:
    """Headings normalize to ## regardless of original depth."""
    raw = "# Title\n## Section\n### Subsection\n#### Deep"
    result = pr._normalize_for_diff(raw)
    assert "## Title" in result
    assert "## Section" in result
    assert "## Subsection" in result
    assert "## Deep" in result


def test_normalize_replaces_images_with_placeholder() -> None:
    """Image markdown is replaced with [IMAGE] placeholder."""
    raw = "Text before.\n![alt text](path/to/img.png)\nText after."
    result = pr._normalize_for_diff(raw)
    joined = " ".join(result)
    assert "[IMAGE]" in joined
    assert "alt text" not in joined
    assert "path/to/img.png" not in joined


def test_normalize_strips_superscript_tags() -> None:
    """<sup>text</sup> normalizes to ^text^ caret notation."""
    raw = "This is the 1<sup>st</sup> attempt."
    result = pr._normalize_for_diff(raw)
    joined = " ".join(result)
    # <sup>st</sup> → ^st^, so result is "1^st^"
    assert "1^st^" in joined


def test_normalize_preserves_markdown_table_rows() -> None:
    """Markdown table rows stay in their own normalized paragraph group."""
    raw = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    result = pr._normalize_for_diff(raw)
    # Tables should appear as joined row strings
    joined_all = " ".join(result)
    assert "A" in joined_all
    assert "B" in joined_all


def test_normalize_latex_blocks_preserved() -> None:
    """LaTeX $$ blocks are kept as single normalized units."""
    raw = "Preamble.\n$$ f(x) = x^2 $$\nAftermath."
    result = pr._normalize_for_diff(raw)
    joined = " ".join(result)
    assert "$$" in joined
    assert "f(x)" in joined


def test_normalize_meta_lines_are_removed() -> None:
    """Lines matching DOI/URL/Email-addresses/etc. meta patterns are stripped."""
    raw = "DOI: 10.1234/test\nReal content here."
    result = pr._normalize_for_diff(raw)
    joined = "\n".join(result)
    assert "DOI:" not in joined
    assert "Real content here" in joined

# ---------------------------------------------------------------------------
# Test 4: engine version provenance (frozen _META.json contract)
# ---------------------------------------------------------------------------

_FAKE_SNAPSHOT = {
    # surya joined the contract in issue #14: it reaches the pipeline through
    # Marker 2.0, and its *weights* are OpenRAIL-M, so the version has to be
    # recorded alongside the code that pulls it in.
    "engine_versions": {"marker": "9.9.9", "mineru": "8.8.8", "surya": "0.22.1",
                        "torch": "7.7.7", "cuda": "6.6", "python": "3.10.12"},
    "engine_license_ids": {"marker": {"code": "Apache-2.0", "weights": "Apache-2.0"}},
    "engine_versions_source": "conversion_time",
    "engine_versions_note": None,
}


def test_engine_version_snapshot_contract() -> None:
    """Snapshot exposes the frozen version keys, license ids and a source marker."""
    pr._reset_engine_version_cache()
    snap = pr._engine_version_snapshot()
    assert set(snap) == {"engine_versions", "engine_license_ids",
                         "engine_versions_source", "engine_versions_note"}
    assert list(snap["engine_versions"]) == list(pr._ENGINE_CONTRACT_KEYS)
    assert "surya" in snap["engine_versions"], "issue #14: surya is a contract key"
    # A version number alone cannot tell a consumer that the Surya weights are
    # OpenRAIL-M, so the license identity ships next to the version.
    licenses = snap["engine_license_ids"]
    assert licenses["surya"]["weights"].startswith("modified AI Pubs OpenRAIL-M")
    assert "additional terms" in licenses["mineru"]["code_note"]
    assert licenses["pymupdf"]["code"].startswith("AGPL-3.0 (NOT USED")
    assert snap["engine_versions_source"] in {"conversion_time", "unavailable"}
    assert snap["engine_versions_note"] is None or isinstance(
        snap["engine_versions_note"], str)


def test_engine_version_probe_runs_once_per_process(monkeypatch) -> None:
    """The probe hits both venvs exactly once, then is memoized for the batch."""
    pr._reset_engine_version_cache()
    calls: list[str] = []

    def _fake(engine: str) -> dict:
        calls.append(engine)
        return {"python": "3.10.12", "marker": "9.9.9", "mineru": None,
                "torch": "7.7.7", "cuda": "6.6"}

    monkeypatch.setattr(pr, "_probe_one_env", _fake)
    first = pr._engine_version_snapshot()
    second = pr._engine_version_snapshot()

    assert calls == ["marker", "mineru"]
    assert first is second
    assert first["engine_versions"]["marker"] == "9.9.9"
    assert first["engine_versions"]["mineru"] is None
    assert first["engine_versions_source"] == "conversion_time"


def test_engine_version_probe_failure_never_raises(monkeypatch) -> None:
    """A probe that raises must not abort the run: nulls + note + unavailable."""
    pr._reset_engine_version_cache()

    def _boom(engine: str) -> dict:
        raise RuntimeError(f"probe exploded: {engine}")

    monkeypatch.setattr(pr, "_probe_one_env", _boom)
    snap = pr._engine_version_snapshot()  # must not raise

    assert snap["engine_versions"] == {k: None for k in pr._ENGINE_CONTRACT_KEYS}
    assert snap["engine_versions_source"] == "unavailable"
    assert snap["engine_versions_note"]
    assert "marker" in snap["engine_versions_note"]


def test_run_probe_command_swallows_missing_binary(monkeypatch) -> None:
    """A missing cmd.exe/python degrades to an error note instead of raising."""
    def _raise(*args, **kwargs):
        raise FileNotFoundError("no such file: cmd.exe")

    monkeypatch.setattr(pr.subprocess, "run", _raise)
    out, err = pr._run_probe_command(["definitely-not-a-binary"], 5.0)
    assert out == ""
    assert err and "FileNotFoundError" in err


def test_run_probe_command_keeps_output_on_timeout(monkeypatch) -> None:
    """On timeout the already-flushed fast line is still used."""
    def _timeout(*args, **kwargs):
        raise pr.subprocess.TimeoutExpired(
            cmd="probe", timeout=1.0,
            output='<<<FAST>>>{"python": "3.10.12"}\n')

    monkeypatch.setattr(pr.subprocess, "run", _timeout)
    out, err = pr._run_probe_command(["probe"], 1.0)
    assert "<<<FAST>>>" in out
    assert err and "timeout" in err


# ---------------------------------------------------------------------------
# Test 5: _META.json provenance payload
# ---------------------------------------------------------------------------

def test_precheck_record_includes_full_sha256() -> None:
    """PrecheckResult carries both the short legacy hash and the full digest."""
    rec = pr.PrecheckResult(ok=True, status="passed", pdf_hash="a" * 16,
                            pdf_sha256="c" * 64).to_record()
    assert rec["pdf_hash"] == "a" * 16
    assert rec["pdf_sha256"] == "c" * 64


def test_sha256_of_file_matches_hashlib_and_never_raises(tmp_path: Path) -> None:
    target = tmp_path / "sample.bin"
    target.write_bytes(b"hello world")
    digest, note = pr._sha256_of_file(target)
    assert digest == hashlib.sha256(b"hello world").hexdigest()
    assert note is None

    missing, err = pr._sha256_of_file(tmp_path / "nope.bin")
    assert missing is None
    assert err and "pdf_sha256 unavailable" in err


def test_build_meta_record_contract_and_json_roundtrip(tmp_path: Path) -> None:
    """_META.json carries pdf_sha256 + the five version keys and stays parseable."""
    result = pr.PaperResult(pdf_path="/p/x.pdf", stem="x")
    result.precheck = pr.PrecheckResult(ok=True, status="passed",
                                        pdf_hash="a" * 16, pdf_sha256="b" * 64)

    meta = pr.build_meta_record(
        result, engines="both", pages=None, images_copied=3,
        pdf_sha256=result.precheck.pdf_sha256, pdf_sha256_note=None,
        engine_provenance=dict(_FAKE_SNAPSHOT),
    )

    assert meta["pdf_sha256"] == "b" * 64
    assert list(meta["engine_versions"]) == list(pr._ENGINE_CONTRACT_KEYS)
    assert meta["engine_versions"]["marker"] == "9.9.9"
    assert meta["engine_versions_source"] == "conversion_time"
    assert meta["engine_versions_note"] is None
    assert meta["precheck"]["pdf_sha256"] == "b" * 64

    meta_path = tmp_path / "_META.json"
    assert pr._write_meta_json(meta_path, meta) is None
    parsed = json.loads(meta_path.read_text(encoding="utf-8"))
    assert parsed["pdf_sha256"] == "b" * 64
    assert parsed["engine_versions"]["mineru"] == "8.8.8"


def test_build_meta_record_always_emits_five_version_keys() -> None:
    """Keys are never omitted — unknown values are null, with a note."""
    result = pr.PaperResult(pdf_path="/p/x.pdf", stem="x")
    meta = pr.build_meta_record(
        result, engines="marker", pages="0-3", images_copied=0,
        pdf_sha256=None, pdf_sha256_note="no source pdf",
        engine_provenance={},
    )
    assert meta["pdf_sha256"] is None
    assert meta["pdf_sha256_note"] == "no source pdf"
    assert meta["engine_versions"] == {k: None for k in pr._ENGINE_CONTRACT_KEYS}


def test_write_meta_json_survives_unwritable_path(tmp_path: Path) -> None:
    target = tmp_path / "missing_dir" / "_META.json"
    err = pr._write_meta_json(target, {"a": 1})
    assert err and not target.exists()


# ---------------------------------------------------------------------------
# Test 6: --backfill-meta (provenance for already-converted corpora)
# ---------------------------------------------------------------------------

def _backfill_corpus(tmp_path: Path) -> tuple[Path, dict]:
    """3-paper v1 corpus: fresh / already-measured / no-source-pdf."""
    papers_dir = tmp_path
    corpus = papers_dir / "paper-analysis"

    paper_a = corpus / "PaperA"
    paper_a.mkdir(parents=True)
    (paper_a / "src.pdf").write_bytes(b"%PDF-1.4\n" + b"A" * 4096)
    (paper_a / "_META.json").write_text(
        json.dumps({"pdf_path": "src.pdf", "stem": "PaperA"}), encoding="utf-8")

    paper_b = corpus / "PaperB"
    paper_b.mkdir()
    (paper_b / "_META.json").write_text(json.dumps({
        "stem": "PaperB", "pdf_sha256": "f" * 64,
        "engine_versions_source": "conversion_time",
        "engine_versions_note": None,
        "engine_versions": dict(_FAKE_SNAPSHOT["engine_versions"]),
    }), encoding="utf-8")

    paper_c = corpus / "PaperC"
    paper_c.mkdir()
    (paper_c / "_META.json").write_text(json.dumps({"stem": "PaperC"}),
                                        encoding="utf-8")

    paths = {
        "a": paper_a / "_META.json",
        "b": paper_b / "_META.json",
        "c": paper_c / "_META.json",
    }
    return papers_dir, paths


def _digests(paths: dict) -> dict:
    return {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}


def test_backfill_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))
    papers_dir, paths = _backfill_corpus(tmp_path)

    first = pr.backfill_meta(papers_dir)
    after_first = _digests(paths)
    second = pr.backfill_meta(papers_dir)
    after_second = _digests(paths)

    assert first["scanned"] == 3
    # PaperA + PaperC (versions + hash + language) and PaperB, which keeps its
    # measured versions but still gains the language triple (issue #10 step).
    assert first["updated"] == 3
    assert first["skipped_conversion_time"] == 1
    assert second["updated"] == 0         # nothing left to fill
    assert second["unchanged"] == 3
    assert after_first == after_second    # byte-identical: no rewrite


def test_backfill_never_touches_conversion_time_record(tmp_path: Path, monkeypatch) -> None:
    """A measured record keeps every measured field.  The language triple is a
    different question and is still added: the oldest records are exactly the ones
    that predate it, so skipping the file outright left the keys permanently absent."""
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))
    papers_dir, paths = _backfill_corpus(tmp_path)
    before = json.loads(paths["b"].read_text(encoding="utf-8"))

    pr.backfill_meta(papers_dir)
    after_first = paths["b"].read_bytes()
    pr.backfill_meta(papers_dir)

    record = json.loads(paths["b"].read_text(encoding="utf-8"))
    assert record["engine_versions_source"] == "conversion_time"
    assert record["pdf_sha256"] == "f" * 64
    assert record["engine_versions"] == before["engine_versions"]
    assert record["engine_versions_note"] == before.get("engine_versions_note")
    # Only the language triple may appear; with no markdown to measure it is null.
    assert [k for k in pr._LANG_META_KEYS if k not in record] == []
    assert [record[k] for k in pr._LANG_META_KEYS] == [None, None, None]
    assert paths["b"].read_bytes() == after_first, "a second pass is a no-op"


def test_backfill_fills_missing_hash_and_marks_estimate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))
    papers_dir, paths = _backfill_corpus(tmp_path)

    stats = pr.backfill_meta(papers_dir)
    record = json.loads(paths["a"].read_text(encoding="utf-8"))
    expected = hashlib.sha256((paths["a"].parent / "src.pdf").read_bytes()).hexdigest()

    assert record["pdf_sha256"] == expected
    assert stats["pdf_sha256_filled"] == 1
    # Backfill must fill every contract key (including surya), never leave a
    # hole: a missing key is indistinguishable from "unknown".
    assert record["engine_versions"] == _FAKE_SNAPSHOT["engine_versions"]
    assert set(record["engine_versions"]) == set(pr._ENGINE_CONTRACT_KEYS)
    # Honesty: never claim conversion-time measurement for a backfilled record.
    assert record["engine_versions_source"] == "current_env_estimate"
    assert "not a conversion-time measurement" in record["engine_versions_note"]


def test_backfill_missing_pdf_writes_null(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))
    papers_dir, paths = _backfill_corpus(tmp_path)

    stats = pr.backfill_meta(papers_dir)
    record = json.loads(paths["c"].read_text(encoding="utf-8"))

    assert "pdf_sha256" in record and record["pdf_sha256"] is None
    assert record["pdf_sha256_note"]
    assert stats["pdf_sha256_null"] == 1
    assert record["engine_versions_source"] == "current_env_estimate"


def test_backfill_without_meta_files_reports_zero(tmp_path: Path) -> None:
    stats = pr.backfill_meta(tmp_path)
    assert stats["scanned"] == 0
    assert stats["updated"] == 0


def test_pipeline_state_persists_full_sha256(tmp_path: Path) -> None:
    """State carries the full digest so a resumed run can reuse it (no re-read)."""
    state_path = tmp_path / "_pipeline_state.json"
    state = pr.PipelineState(state_path)
    result = pr.PrecheckResult(ok=True, status="passed", pdf_hash="a" * 16,
                               pdf_sha256="d" * 64)
    state.set_precheck("paper-x", result)
    state.save()

    reloaded = pr.PipelineState(state_path)
    assert reloaded.get("paper-x")["pdf_sha256"] == "d" * 64
    assert reloaded.get("paper-x")["precheck"]["pdf_sha256"] == "d" * 64


def test_process_one_writes_provenance_meta(tmp_path: Path, monkeypatch) -> None:
    """End-to-end write path (engines + precheck mocked, no GPU): _META.json is
    produced with a correct pdf_sha256 and the five version keys."""
    body = b"%PDF-1.4\n" + b"P" * 4096
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()

    monkeypatch.setattr(
        pr, "run_precheck",
        lambda p, max_pages=pr._DEFAULT_MAX_PAGES: pr.PrecheckResult(
            ok=True, status="passed", page_count=1, pdf_hash=digest[:16],
            pdf_sha256=digest))
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))

    def _fake_marker(pdf_path, out_dir, pages):
        md_dir = Path(out_dir) / "marker" / Path(pdf_path).stem
        md_dir.mkdir(parents=True, exist_ok=True)
        md = md_dir / f"{Path(pdf_path).stem}.md"
        md.write_text("# Title\n\nBody text.\n", encoding="utf-8")
        return pr.EngineResult("marker", True, 0.1, md_path=str(md), img_count=0)

    monkeypatch.setattr(pr, "run_marker", _fake_marker)

    pr.process_one(pdf, tmp_path, "marker", None, "auto", "pipeline", None)

    meta_path = tmp_path / "paper-merged" / "paper" / "_META.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["pdf_sha256"] == digest
    assert list(meta["engine_versions"]) == list(pr._ENGINE_CONTRACT_KEYS)
    assert meta["engine_versions"]["marker"] == "9.9.9"
    assert meta["engine_versions_source"] == "conversion_time"
    # Stage 1.5 wiring: the conversion leaves side-band evidence behind and the
    # language triple is written next to the provenance fields (issue #12/#10).
    assert (tmp_path / "paper-merged" / "paper"
            / pr._TEXTLAYER_PROBE_FILENAME).is_file()
    assert [k for k in pr._LANG_META_KEYS if k not in meta] == []
    assert meta["language"] == "en"
    assert meta["lang_source"] == pr._LANG_SOURCE_MERGED


# ---------------------------------------------------------------------------
# Stage 1.5 side-band wiring: text-layer probe + language record (issue #12/#10)
# ---------------------------------------------------------------------------

LANG_SPEC = Path(__file__).resolve().parent / "lang_spec_cases.json"


def _lang_spec() -> dict:
    return json.loads(LANG_SPEC.read_text(encoding="utf-8"))


def test_language_spec_cases_match_shared_fixture() -> None:
    """The frozen shared spec is the contract: reproduce it exactly.

    The same file is asserted by paper-metrics' suite, so the conversion layer and
    the metrics layer cannot drift apart about what language a paper is in.
    """
    spec = _lang_spec()
    assert spec["threshold"] == pr.LANGUAGE_CJK_THRESHOLD
    assert spec["round_digits"] == pr._LANG_ROUND_DIGITS
    assert [list(r) for r in pr._LANG_CJK_RANGES] == spec["cjk_ranges"]
    assert pr._LANG_ALPHA_TOKEN_RE.pattern == spec["alpha_token_regex"]
    for case in spec["cases"]:
        record = pr.detect_language_record(case["text"], "test")
        expected = case["expected"]
        assert record["language"] == expected["language"], case["id"]
        assert record["cjk_ratio"] == expected["cjk_ratio"], case["id"]
        assert record["cjk_chars"] == expected["cjk_chars"], case["id"]
        assert record["ascii_alpha_tokens"] == expected["ascii_alpha_tokens"], case["id"]


def test_stage_1_5_never_fails_a_conversion(tmp_path: Path, monkeypatch) -> None:
    """The side-band stage is evidence only: even a broken probe must leave a
    finished conversion finished, with _META.json written and the language triple
    present.  This is the "never blocks" red line, tested instead of asserted."""
    body = b"%PDF-1.4\n" + b"P" * 4096
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()

    monkeypatch.setattr(
        pr, "run_precheck",
        lambda p, max_pages=pr._DEFAULT_MAX_PAGES: pr.PrecheckResult(
            ok=True, status="passed", page_count=1, pdf_hash=digest[:16],
            pdf_sha256=digest))
    monkeypatch.setattr(pr, "_engine_version_snapshot", lambda: dict(_FAKE_SNAPSHOT))

    def _fake_marker(pdf_path, out_dir, pages):
        md_dir = Path(out_dir) / "marker" / Path(pdf_path).stem
        md_dir.mkdir(parents=True, exist_ok=True)
        md = md_dir / f"{Path(pdf_path).stem}.md"
        md.write_text("# Title\n\nBody text.\n", encoding="utf-8")
        return pr.EngineResult("marker", True, 0.1, md_path=str(md), img_count=0)

    monkeypatch.setattr(pr, "run_marker", _fake_marker)

    # (1) a probe that raises for reasons nobody predicted
    def _boom(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(pr, "run_textlayer_probe", _boom)
    result = pr.process_one(pdf, tmp_path, "marker", None, "auto", "pipeline", None)
    # A crash is its own verdict, not "not_applicable": a downstream rule like
    # "verdict != warn means fine" must not sweep an unmeasured hop into "fine".
    assert result.probe_verdict == "error"
    assert result.probe_warnings == ["PROBE_CRASHED"]
    assert result.probe_path is None, "a crashed probe must not claim a record"
    meta = json.loads((tmp_path / "paper-merged" / "paper" / "_META.json")
                      .read_text(encoding="utf-8"))
    assert [k for k in pr._LANG_META_KEYS if k not in meta] == []

    # (2) a stored/foreign record whose warnings field is not a list
    monkeypatch.setattr(pr, "run_textlayer_probe",
                        lambda *_args, **_kwargs: {"verdict": "warn", "warnings": 123})
    second = pr.process_one(pdf, tmp_path, "marker", None, "auto", "pipeline", None)
    assert second.probe_verdict == "warn"
    assert second.probe_warnings == [], "a non-list warnings field must not iterate"


def test_backfill_adds_the_language_triple_even_without_text(tmp_path: Path) -> None:
    """The triple is never omitted: with no markdown to measure it is filled with
    nulls (not measured), not left out."""
    merged_dir = tmp_path / "paper-merged" / "orphan"
    merged_dir.mkdir(parents=True)
    meta_path = merged_dir / "_META.json"
    meta: dict = {"stem": "orphan"}
    snapshot = {"engine_versions": {}, "engine_versions_source": "unavailable",
                "engine_versions_note": None}

    changed, _ = pr._backfill_one(meta, meta_path, tmp_path, dict(snapshot))
    assert changed is True
    assert [meta.get(k) for k in pr._LANG_META_KEYS] == [None, None, None]
    assert [k for k in pr._LANG_META_KEYS if k not in meta] == []


def test_resolve_canonical_md_ignores_internal_artifacts(tmp_path: Path) -> None:
    """A large _DIFF.md must never be mistaken for the paper's own text."""
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "_DIFF.md").write_text("x" * 200000, encoding="utf-8")
    (paper_dir / "paper.md").write_text("real body", encoding="utf-8")
    assert pr.resolve_canonical_md(paper_dir).name == "paper.md"


def test_backfill_probe_covers_already_converted_papers(tmp_path: Path) -> None:
    """--resume skips finished papers forever, so an old corpus needs its own entry
    point to gain the stage 1.5 evidence without a minute-per-paper re-conversion."""
    corpus = tmp_path / "paper-analysis"
    auto = corpus / "paper-x" / "mineru" / "paper-x" / "auto"
    auto.mkdir(parents=True)
    (auto / "paper-x_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "Body 12.73% 本文方法。"}]),
        encoding="utf-8")
    pdf = tmp_path / "elsewhere" / "paper-x.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"not a real pdf")
    meta_path = corpus / "paper-x" / "_META.json"
    meta_path.write_text(json.dumps({"stem": "paper-x", "pdf_path": str(pdf)}),
                         encoding="utf-8")

    stats = pr.backfill_probe(corpus)
    assert stats == {"scanned": 1, "probed": 1, "reused": 0, "warn": 0,
                     "not_measured": 1, "no_pdf": 0}
    record = json.loads((corpus / "paper-x" / pr._TEXTLAYER_PROBE_FILENAME)
                        .read_text(encoding="utf-8"))
    assert record["canonical_source"] == pr._LANG_SOURCE_CONTENT_LIST
    assert record["canonical_sha256"]

    # A second pass reuses the valid record instead of re-reading the PDF.
    again = pr.backfill_probe(corpus)
    assert again["reused"] == 1
    assert again["probed"] == 0

    # A paper whose PDF is gone is counted, never fatal.
    meta_path.write_text(json.dumps({"stem": "paper-x",
                                     "pdf_path": str(tmp_path / "gone.pdf")}),
                         encoding="utf-8")
    third = pr.backfill_probe(corpus)
    assert third["no_pdf"] == 1


def test_backfill_probe_finds_the_conversion_tree_when_pointed_at_merged(tmp_path: Path) -> None:
    """Users point at <root>/paper-merged too, and there the conversion tree is a
    sibling: missing it would silently degrade the comparison to the markdown."""
    root = tmp_path / "corpus"
    auto = root / "paper-conversion" / "paper-x" / "mineru" / "paper-x" / "auto"
    auto.mkdir(parents=True)
    (auto / "paper-x_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "正文 12.73% 本文方法。"}]),
        encoding="utf-8")
    merged_root = root / "paper-merged"
    merged = merged_root / "paper-x"
    merged.mkdir(parents=True)
    (merged / "_MERGED.md").write_text("仅合并稿正文\n", encoding="utf-8")
    pdf = tmp_path / "paper-x.pdf"
    pdf.write_bytes(b"not a real pdf")
    (merged / "_META.json").write_text(
        json.dumps({"stem": "paper-x", "pdf_path": str(pdf)}), encoding="utf-8")

    # Point the CLI straight at <root>/paper-merged: _meta_candidates accepts that
    # layout, so the conversion tree has to be found as a sibling.
    stats = pr.backfill_probe(merged_root)
    assert stats["scanned"] == 1 and stats["no_pdf"] == 0
    record = json.loads((merged / pr._TEXTLAYER_PROBE_FILENAME)
                        .read_text(encoding="utf-8"))
    assert record["canonical_source"] == pr._LANG_SOURCE_CONTENT_LIST, \
        "the sibling paper-conversion tree must be used, not the markdown"


def test_language_survives_a_probe_failure(tmp_path: Path, monkeypatch) -> None:
    """The language is computed before the probe runs; a probe failure must not
    also erase a fact this stage had already established."""
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    conversion_dir.mkdir(parents=True)
    merged_dir = tmp_path / "paper-merged" / "paper"
    merged_dir.mkdir(parents=True)
    (merged_dir / "_MERGED.md").write_text("This run is marker only.\n",
                                           encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("later failure")

    monkeypatch.setattr(pr, "run_textlayer_probe", _boom)
    result = pr.PaperResult(pdf_path="/tmp/paper.pdf", stem="paper")
    record = pr.collect_stage_1_5(tmp_path / "paper.pdf", merged_dir, conversion_dir,
                                  "paper", force=False, enabled=True, result=result)
    assert record["language"] == "en", "a probe crash must not null the language"
    assert record["lang_source"] == pr._LANG_SOURCE_MERGED
    assert result.probe_verdict == "error"
    assert result.probe_warnings == ["PROBE_CRASHED"]


def test_canonical_json_must_match_the_markdown_stem(tmp_path: Path) -> None:
    """A shared directory must not hand back another paper's content_list.

    If this paper's own JSON carries no text blocks, the old glob would continue
    and return the first non-empty file in the same directory - another paper's
    text, still labelled mineru_content_list.
    """
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    auto = conversion_dir / "mineru" / "paper" / "auto"
    auto.mkdir(parents=True)
    md = auto / "paper.md"
    md.write_text("# t\n", encoding="utf-8")
    (auto / "paper_content_list.json").write_text(
        json.dumps([{"type": "image", "img_path": "scan.jpg"}]), encoding="utf-8")
    (auto / "other_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "别篇的正文"}]), encoding="utf-8")

    text, source = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=md)
    assert (text, source) == (None, None), \
        "another paper's JSON must never be returned as this paper's canonical"

    # With a usable JSON of its own the exact match is used.
    (auto / "paper_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "Body 12.73%"}]), encoding="utf-8")
    text2, source2 = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=md)
    assert source2 == pr._LANG_SOURCE_CONTENT_LIST and text2 == "Body 12.73%"


def test_backfill_never_escapes_above_a_corpus_root(tmp_path: Path) -> None:
    """The parent candidate is for <root>/paper-merged style callers only: from a
    corpus root it would reach the parent directory, where an unrelated corpus
    holding a same-stem paper could hand back its evidence."""
    outer = tmp_path / "outer"
    root = outer / "my-corpus"
    # Decoy: a same-stem paper in the parent directory's conversion tree.
    decoy = outer / "paper-conversion" / "paper-x" / "mineru" / "paper-x" / "auto"
    decoy.mkdir(parents=True)
    (decoy / "paper-x_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "别库的证据"}]), encoding="utf-8")
    # The corpus's own tree, with its own JSON.
    own = root / "paper-conversion" / "paper-x" / "mineru" / "paper-x" / "auto"
    own.mkdir(parents=True)
    (own / "paper-x_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "本库正文"}]), encoding="utf-8")
    merged = root / "paper-merged" / "paper-x"
    merged.mkdir(parents=True)
    (merged / "_MERGED.md").write_text("md\n", encoding="utf-8")
    pdf = tmp_path / "paper-x.pdf"
    pdf.write_bytes(b"not a pdf")
    (merged / "_META.json").write_text(
        json.dumps({"stem": "paper-x", "pdf_path": str(pdf)}), encoding="utf-8")

    pr.backfill_probe(root)
    record = json.loads((merged / pr._TEXTLAYER_PROBE_FILENAME)
                        .read_text(encoding="utf-8"))
    assert record["canonical_source"] == pr._LANG_SOURCE_CONTENT_LIST
    assert record["canonical_sha256"] == hashlib.sha256(
        "本库正文".encode("utf-8")).hexdigest(), "the corpus's own JSON must be used"


def test_canonical_fallback_accepts_only_an_unambiguous_renamed_json(tmp_path: Path) -> None:
    """The exact match is the rule; the fallback exists for layouts that name the
    JSON differently, and it may only fire when there is nothing to guess between."""
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    auto = conversion_dir / "mineru" / "paper" / "auto"
    auto.mkdir(parents=True)
    md = auto / "paper.md"

    # exact missing + exactly one renamed JSON -> accepted
    renamed = auto / "renamed_content_list.json"
    renamed.write_text(json.dumps([{"type": "text", "text": "Body 12.73%"}]), encoding="utf-8")
    text, source = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=md)
    assert source == pr._LANG_SOURCE_CONTENT_LIST and text == "Body 12.73%"

    # exact missing + two candidates -> ambiguous, nothing is returned
    (auto / "second_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "别篇"}]), encoding="utf-8")
    assert pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=md) == (None, None)

    # the exact file always wins over the others
    (auto / "paper_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "本篇"}]), encoding="utf-8")
    text3, source3 = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=md)
    assert source3 == pr._LANG_SOURCE_CONTENT_LIST and text3 == "本篇"


def test_is_fully_done_requires_the_merge_phase(tmp_path: Path) -> None:
    """A failed merge is not done: --resume used to skip the paper forever, so
    neither the merge nor the stage that runs after it was ever retried."""
    state = pr.PipelineState(tmp_path / "_pipeline_state.json")
    state.set_phase("paper", "precheck", {"status": "passed"})
    state.set_phase("paper", "phase1_converted", {"status": "done"})
    state.set_phase("paper", "phase2_merged", {"status": "failed",
                                              "error_type": "normalize_crash"})
    assert state.is_fully_done("paper") is False

    state.set_phase("paper", "phase2_merged", {"status": "done"})
    assert state.is_fully_done("paper") is True

    # degraded still counts as finished: a single-engine run is not a failure
    state.set_phase("paper", "phase2_merged", {"status": "degraded"})
    assert state.is_fully_done("paper") is True


def test_backfill_meta_fills_language_for_conversion_time_records(tmp_path: Path) -> None:
    """Integration: a record measured at conversion time keeps its versions but
    must still gain the language triple.  Skipping the file outright (the old
    behaviour) left the keys permanently absent on exactly the oldest records."""
    merged_dir = tmp_path / "paper-merged" / "paper"
    merged_dir.mkdir(parents=True)
    (merged_dir / "_MERGED.md").write_text("This paper compares three baselines.",
                                           encoding="utf-8")
    meta_path = merged_dir / "_META.json"
    measured = {"marker": "9.9.9", "mineru": "8.8.8", "surya": None,
                "torch": "2.5.1", "cuda": "12.4", "python": "3.12.0"}
    meta_path.write_text(json.dumps({
        "stem": "paper", "engine_versions": measured,
        "engine_versions_source": "conversion_time",
    }), encoding="utf-8")

    stats = pr.backfill_meta(tmp_path)
    assert stats["scanned"] == 1
    assert stats["skipped_conversion_time"] == 1, "the versions are not re-stamped"
    assert stats["updated"] == 1

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["language"] == "en"
    assert meta["cjk_ratio"] == 0.0
    assert meta["lang_source"] == pr._LANG_SOURCE_BACKFILL
    assert meta["engine_versions"] == measured, "measured versions must survive"
    assert meta["engine_versions_source"] == "conversion_time"


def test_stage_1_5_refuses_a_stale_content_list_from_an_earlier_run(tmp_path: Path) -> None:
    """A marker-only run (or a failed MinerU) over a tree that still holds an old
    Chinese content_list must not record that old text as this run's canonical."""
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    auto = conversion_dir / "mineru" / "paper" / "auto"
    auto.mkdir(parents=True)
    (auto / "paper_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "旧的中文正文，来自上一次转换。"}]),
        encoding="utf-8")
    merged_dir = tmp_path / "paper-merged" / "paper"
    merged_dir.mkdir(parents=True)
    (merged_dir / "_MERGED.md").write_text("# Title\n\nThis run is marker only.\n",
                                           encoding="utf-8")

    # (1) the pipeline knows this run produced no MinerU result -> no tree scan
    result = pr.PaperResult(pdf_path="/tmp/paper.pdf", stem="paper")
    record = pr.collect_stage_1_5(tmp_path / "paper.pdf", merged_dir, conversion_dir,
                                  "paper", force=True, enabled=False,
                                  result=result, mineru_md_path=None)
    assert record["language"] == "en", "the stale Chinese JSON must not be used"
    assert record["lang_source"] == pr._LANG_SOURCE_MERGED
    assert pr.resolve_metrics_canonical(conversion_dir) == (None, None)

    # (2) a JSON next to THIS run's MinerU markdown is accepted as usual
    fresh_md = auto / "paper.md"
    fresh_md.write_text("# x\n", encoding="utf-8")
    text, source = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=fresh_md)
    assert source == pr._LANG_SOURCE_CONTENT_LIST
    assert "旧的中文正文" in text

    # (3) the backfill has no "this run", so it may scan the tree and label it
    text2, source2 = pr.resolve_metrics_canonical(conversion_dir, allow_tree_scan=True)
    assert source2 == pr._LANG_SOURCE_CONTENT_LIST
    assert "旧的中文正文" in text2


def test_resolve_metrics_canonical_survives_pathological_json(tmp_path: Path) -> None:
    """A pathological content_list must be skipped, not crash the conversion."""
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    auto = conversion_dir / "mineru" / "paper" / "auto"
    auto.mkdir(parents=True)
    (auto / "paper_content_list.json").write_text(
        '{"a":' * 2000 + "1" + "}" * 2000, encoding="utf-8")
    assert pr.resolve_metrics_canonical(conversion_dir) == (None, None)


def test_degraded_probe_record_is_persisted_and_retried(tmp_path: Path) -> None:
    """ "This hop was never measured" is evidence too: it is written to disk, and
    because a degraded record carries no pdf_sha256 it can never become a cache hit."""
    pdf = tmp_path / "missing.pdf"
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()

    record = pr.run_textlayer_probe(pdf, paper_dir, "body")
    assert record["verdict"] == "not_applicable"
    assert record["warnings"] == ["PDF_NOT_FOUND"]
    out = paper_dir / pr._TEXTLAYER_PROBE_FILENAME
    assert out.is_file(), "a degraded attempt must still leave a trace"
    assert json.loads(out.read_text(encoding="utf-8"))["warnings"] == ["PDF_NOT_FOUND"]

    again = pr.run_textlayer_probe(pdf, paper_dir, "body")
    assert again["warnings"] == ["PDF_NOT_FOUND"], "degraded records must not cache"


def test_corrupt_probe_json_is_replaced(tmp_path: Path, monkeypatch) -> None:
    """Unparseable stored JSON must be overwritten by a fresh measurement."""
    import textlayer_probe

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 junk")
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    out = paper_dir / pr._TEXTLAYER_PROBE_FILENAME
    out.write_text("{not json at all", encoding="utf-8")

    calls: list[int] = []

    def _fake_probe(pdf_path, canonical_text, canonical_source=None):
        calls.append(1)
        return {"verdict": "warn", "pdf_sha256": pr._sha256_of_file(Path(pdf_path))[0],
                "probe_version": pr._current_probe_versions()[0],
                "pdfplumber_version": pr._current_probe_versions()[1],
                "canonical_sha256": (hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
                                     if canonical_text is not None else None),
                "canonical_source": canonical_source}

    monkeypatch.setattr(textlayer_probe, "probe_pdf", _fake_probe)
    record = pr.run_textlayer_probe(pdf, paper_dir, "body",
                                    canonical_source=pr._LANG_SOURCE_CONTENT_LIST)
    assert record["verdict"] == "warn"
    assert len(calls) == 1, "a corrupt record must be re-measured"
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "warn"


def test_resolve_metrics_canonical_prefers_the_artifact_metrics_read(tmp_path: Path) -> None:
    """The comparison side must be the text paper-metrics actually measures.

    Real measurement on the Chinese corpus: 2.1% digit loss against the merged
    markdown, 55.5% against the content_list.json — choosing the markdown hides
    the damage the probe exists to find.
    """
    conversion_dir = tmp_path / "paper-conversion" / "paper"
    auto = conversion_dir / "mineru" / "paper" / "auto"
    auto.mkdir(parents=True)
    (auto / "paper_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "Body 12.73%"},
                    {"type": "text", "text": "Second 4"},
                    {"type": "image", "img_path": "x.jpg"}]),
        encoding="utf-8")
    # The pipeline must name this run's MinerU markdown; only then is the JSON
    # next to it accepted.  Without that argument there is nothing to trust.
    assert pr.resolve_metrics_canonical(conversion_dir) == (None, None)
    fresh_md = auto / "paper.md"
    fresh_md.write_text("# x\n", encoding="utf-8")
    text, source = pr.resolve_metrics_canonical(conversion_dir, mineru_md_path=fresh_md)
    assert source == pr._LANG_SOURCE_CONTENT_LIST
    assert text == "Body 12.73%\nSecond 4"

    # A backfill has no "this run", so it may scan the tree (and says so).
    scanned_text, scanned_source = pr.resolve_metrics_canonical(
        conversion_dir, allow_tree_scan=True)
    assert scanned_source == pr._LANG_SOURCE_CONTENT_LIST
    assert scanned_text == text

    # No JSON at all: the caller falls back to its own markdown.
    empty_dir = tmp_path / "paper-conversion" / "nojson"
    empty_dir.mkdir(parents=True)
    assert pr.resolve_metrics_canonical(empty_dir, allow_tree_scan=True) == (None, None)


def test_probe_cache_is_invalidated_when_the_input_changes(
        tmp_path: Path, monkeypatch) -> None:
    """A stored record must not be reused for a different PDF or comparison side.

    Reuse is the whole point of the cache, so the guard has to be tested: a record
    about a *different* hop is worse than no record at all.
    """
    import textlayer_probe

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 first")
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    digest, _ = pr._sha256_of_file(pdf)
    probe_version, pdfplumber_version = pr._current_probe_versions()
    canonical_digest = hashlib.sha256(b"body").hexdigest()
    out = paper_dir / pr._TEXTLAYER_PROBE_FILENAME

    def _seed() -> None:
        out.write_text(textlayer_probe.render_json(
            {"verdict": "ok", "pdf_sha256": digest,
             "probe_version": probe_version,
             "pdfplumber_version": pdfplumber_version,
             "canonical_sha256": canonical_digest,
             "canonical_source": pr._LANG_SOURCE_CONTENT_LIST}), encoding="utf-8")

    _seed()

    calls: list[str | None] = []

    def _fake_probe(pdf_path, canonical_text, canonical_source=None):
        calls.append(canonical_source)
        return {"verdict": "ok", "pdf_sha256": digest,
                "canonical_source": canonical_source}

    monkeypatch.setattr(textlayer_probe, "probe_pdf", _fake_probe)

    # 1) same PDF + same comparison side -> reuse (the probe must not re-read it)
    pr.run_textlayer_probe(pdf, paper_dir, "body",
                           canonical_source=pr._LANG_SOURCE_CONTENT_LIST)
    assert calls == [], "an up-to-date record must be reused"

    # 2) same stem, new bytes -> the record describes a different PDF
    pdf.write_bytes(b"%PDF-1.4 second")
    pr.run_textlayer_probe(pdf, paper_dir, "body",
                           canonical_source=pr._LANG_SOURCE_CONTENT_LIST)
    assert len(calls) == 1, "a record about another PDF must not be reused"

    # 3) different comparison side -> also a different hop
    pr.run_textlayer_probe(pdf, paper_dir, "body",
                           canonical_source=pr._LANG_SOURCE_MERGED)
    assert len(calls) == 2, "a record about another comparison side is invalid"

    # 4) a legacy record without the label is refreshed, never trusted
    out.write_text(textlayer_probe.render_json({"verdict": "ok"}), encoding="utf-8")
    pr.run_textlayer_probe(pdf, paper_dir, "body",
                           canonical_source=pr._LANG_SOURCE_MERGED)
    assert len(calls) == 3, "an unlabelled record must be refreshed"

    # 5) same PDF + same label, but the comparison TEXT changed (a re-conversion
    #    rewrites content_list.json under the same name) -> still a stale record
    _seed()
    pr.run_textlayer_probe(pdf, paper_dir, "a different body",
                           canonical_source=pr._LANG_SOURCE_CONTENT_LIST)
    assert len(calls) == 4, "the canonical text itself must be part of the cache key"


def test_detect_language_record_null_when_unmeasured() -> None:
    """No artifact to measure means nulls — never "unknown", never 0.

    "zh/en", "no rule for this text" and "not measured" are three states.
    """
    record = pr.detect_language_record(None)
    assert record["language"] is None
    assert record["cjk_ratio"] is None
    assert record["lang_source"] is None


def test_meta_record_always_carries_the_language_triple() -> None:
    """Frozen contract: the three keys are always present, null when unmeasured."""
    provenance = {"engine_versions": {}, "engine_versions_source": "unavailable"}

    def _meta(language=None):
        return pr.build_meta_record(
            pr.PaperResult(pdf_path="/tmp/p.pdf", stem="p"), engines="marker",
            pages=None, images_copied=0, pdf_sha256=None, pdf_sha256_note=None,
            engine_provenance=provenance, language=language,
        )

    bare = _meta()
    assert [bare[k] for k in pr._LANG_META_KEYS] == [None, None, None]

    record = pr.detect_language_record("本文提出一种方法。", pr._LANG_SOURCE_MERGED)
    full = _meta(record)
    assert full["language"] == "zh"
    assert full["cjk_ratio"] == record["cjk_ratio"]
    assert full["lang_source"] == pr._LANG_SOURCE_MERGED


def test_resolve_canonical_md_prefers_merged_and_is_deterministic(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "aaa.md").write_text("x" * 10, encoding="utf-8")
    (paper_dir / "bbb.md").write_text("x" * 5000, encoding="utf-8")
    # No _MERGED.md: the largest markdown wins, not the alphabetically first one.
    assert pr.resolve_canonical_md(paper_dir).name == "bbb.md"

    merged = paper_dir / "_MERGED.md"
    merged.write_text("short", encoding="utf-8")
    assert pr.resolve_canonical_md(paper_dir) == merged

    (paper_dir / "empty.md").write_text("", encoding="utf-8")
    assert pr.resolve_canonical_md(tmp_path / "missing") is None


def test_run_textlayer_probe_degrades_and_stays_byte_stable(tmp_path: Path) -> None:
    """An unreadable PDF degrades to not_applicable (never raises, never a pass),
    and re-running the probe on the same input reproduces the same bytes."""
    pytest.importorskip("pdfplumber")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"not a pdf at all")
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()

    first = pr.run_textlayer_probe(pdf, paper_dir, "Body text.", force=True)
    assert first["verdict"] == "not_applicable"
    assert "TEXT_LAYER_UNREADABLE" in first["warnings"]
    out = paper_dir / pr._TEXTLAYER_PROBE_FILENAME
    assert out.is_file(), "the walk-away evidence must still be written"
    before = out.read_bytes()

    second = pr.run_textlayer_probe(pdf, paper_dir, "Body text.", force=True)
    assert second["verdict"] == first["verdict"]
    assert out.read_bytes() == before


def test_run_textlayer_probe_reuses_a_matching_cached_record_unless_forced(
        tmp_path: Path, monkeypatch) -> None:
    """Reuse requires the record to prove it belongs to this PDF and this side."""
    import textlayer_probe

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 junk")
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    digest, _ = pr._sha256_of_file(pdf)
    probe_version, pdfplumber_version = pr._current_probe_versions()
    (paper_dir / pr._TEXTLAYER_PROBE_FILENAME).write_text(
        textlayer_probe.render_json({"verdict": "warn", "cached": True,
                                     "pdf_sha256": digest,
                                     "canonical_source": None,
                                     "probe_version": probe_version,
                                     "pdfplumber_version": pdfplumber_version}),
        encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise AssertionError("a matching cached record must not re-read the PDF")

    monkeypatch.setattr(textlayer_probe, "probe_pdf", _boom)
    assert pr.run_textlayer_probe(pdf, paper_dir, None)["cached"] is True

    forced = pr.run_textlayer_probe(pdf, paper_dir, None, force=True)
    assert forced["verdict"] == "not_applicable"
    assert "PROBE_UNAVAILABLE" in forced["warnings"]


def test_backfill_fills_language_triple_idempotently(tmp_path: Path) -> None:
    merged_dir = tmp_path / "paper-merged" / "paper"
    merged_dir.mkdir(parents=True)
    (merged_dir / "_MERGED.md").write_text("本文提出一种方法。", encoding="utf-8")
    meta_path = merged_dir / "_META.json"
    meta = {"stem": "paper", "pdf_sha256": None}
    snapshot = {"engine_versions": {}, "engine_versions_source": "unavailable",
                "engine_versions_note": None}

    changed, _ = pr._backfill_one(meta, meta_path, tmp_path, dict(snapshot))
    assert changed is True
    assert meta["language"] == "zh"
    assert meta["lang_source"] == pr._LANG_SOURCE_BACKFILL

    changed_again, _ = pr._backfill_one(meta, meta_path, tmp_path, dict(snapshot))
    assert changed_again is False, "a second backfill must be a no-op"


def test_cli_help_exits_zero_and_documents_backfill() -> None:
    proc = subprocess.run(
        [sys.executable, str(Path(pr.__file__)), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "--backfill-meta" in proc.stdout



# ── Lossless merge (issue #15) ────────────────────────────────────────────────

_MERGE_MARKER = (
    "# Title\n\nShort intro.\n\n"
    "We evaluate the method on twelve benchmark instances and report the mean accuracy over five seeds.\n\n"
    "$$a = 1$$\n\n| A | B |\n|---|---|\n| 1 | 7 |\n\n![fig1](images/1.png)\n"
)
_MERGE_MINERU = (
    "# Title\n\nShort intro.\n\n"
    "We evaluate the method on twelve benchmark instances and report the average precision across five random seeds.\n\n"
    "$$a = 2$$\n\n| A | B |\n|---|---|\n| 2 | 8 |\n\n![fig2](images/2.png)\n"
)


def _merge(tmp_path: Path, marker: str, mineru: str) -> str:
    m = tmp_path / "marker.md"
    u = tmp_path / "mineru.md"
    m.write_text(marker, encoding="utf-8")
    u.write_text(mineru, encoding="utf-8")
    pr.merge_md(m, u, tmp_path / "MERGED.md")
    return (tmp_path / "MERGED.md").read_text(encoding="utf-8")


def test_merged_never_loses_an_info_class(tmp_path: Path) -> None:
    """Issue #15: the merge used to be lossy (merged smaller than either engine on
    digits).  The contract is "merged >= the better engine per info class"; a
    regression in the conflict rule breaks it silently, which is why this is the
    regression test the issue asks for."""
    merged = _merge(tmp_path, _MERGE_MARKER, _MERGE_MINERU)
    got = pr._info_class_counts(merged)
    marker = pr._info_class_counts(_MERGE_MARKER)
    mineru = pr._info_class_counts(_MERGE_MINERU)
    for klass in ("headings", "paragraphs", "tables", "display_formulas", "images", "digits"):
        assert got[klass] >= max(marker[klass], mineru[klass]), klass


def test_merge_is_byte_identical_across_runs_and_carries_no_timestamp(tmp_path: Path) -> None:
    """Acceptance 2: a wall-clock merged_at in the header made two runs of the same
    inputs differ, so the merged product could never be the one canonical deliverable."""
    first = _merge(tmp_path, _MERGE_MARKER, _MERGE_MINERU)
    second = _merge(tmp_path, _MERGE_MARKER, _MERGE_MINERU)
    assert first == second
    assert "merged_at" not in first


def test_conflict_blocks_keep_both_variants_and_report_their_scores(tmp_path: Path) -> None:
    """Acceptance 3+4: nothing is dropped silently - a conflict keeps BOTH variants with
    a recomputable reason (ratio + per-engine info scores + which one was preferred)."""
    merged = _merge(tmp_path, _MERGE_MARKER, _MERGE_MINERU)
    assert "MERGE-CONFLICT" in merged, merged[-2000:]
    assert "**Marker**" in merged and "**MinerU**" in merged
    assert "preferred=" in merged and "score_marker=" in merged and "score_mineru=" in merged
    assert "conflict" in merged[-4000:].lower() or "merge" in merged[-4000:].lower()


# ── Source-PDF figure path (issue #16) ────────────────────────────────────────

def _tiny_jpeg(tmp_path: Path, width: int, height: int) -> bytes:
    from PIL import Image
    import io as _io
    buffer = _io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _minimal_pdf(tmp_path: Path, jpeg: bytes, image_size: tuple[int, int],
                 place: tuple[float, float, float, float]) -> Path:
    """One page, one JPEG XObject drawn at 'place' (PDF points, top-left origin)."""
    page_w, page_h = 612.0, 792.0
    x0, top, x1, bottom = place
    w_pt, h_pt = x1 - x0, bottom - top
    content = (f"q {w_pt:.2f} 0 0 {h_pt:.2f} {x0:.2f} {page_h - bottom:.2f} cm /Im0 Do Q"
               ).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
         f"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>").encode("ascii"),
        (f"<< /Type /XObject /Subtype /Image /Width {image_size[0]} "
         f"/Height {image_size[1]} /ColorSpace /DeviceRGB /BitsPerComponent 8 "
         f"/Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode("ascii")
        + jpeg + b"\nendstream",
        (f"<< /Length {len(content)} >>\nstream\n").encode("ascii") + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref_at}\n%%EOF\n").encode("ascii")
    path = tmp_path / "source.pdf"
    path.write_bytes(bytes(out))
    return path


def _figure_content_list(tmp_path: Path, blocks: list[dict]) -> Path:
    path = tmp_path / "p1_content_list.json"
    path.write_text(json.dumps(blocks), encoding="utf-8")
    return path


def test_source_pdf_figures_prefer_the_native_embedded_bitmap(tmp_path: Path) -> None:
    """Issue #16 path (b): an embedded bitmap is written at its NATIVE size - the file
    bytes are the PDF's own JPEG, never re-encoded or resampled.  Path (a) rendering
    only runs where nothing usable is embedded."""
    jpeg = _tiny_jpeg(tmp_path, 1200, 800)
    place = (100.0, 100.0, 400.0, 300.0)
    pdf = _minimal_pdf(tmp_path, jpeg, (1200, 800), place)
    # bbox is the 0-1000 normalized box MinerU writes, derived from the placement
    x0, top, x1, bottom = place
    block = {"type": "image", "page_idx": 0, "img_path": "images/a.jpg",
             "bbox": [x0 / 612 * 1000, top / 792 * 1000, x1 / 612 * 1000, bottom / 792 * 1000]}
    cl = _figure_content_list(tmp_path, [block])
    out = tmp_path / "figures"
    manifest = pr.extract_pdf_figures(pdf, cl, out, dpi=600)
    assert manifest["n_figures"] == 1
    record = manifest["figures"][0]
    assert record["method"] == "embedded"
    assert (record["width"], record["height"]) == (1200, 800)
    assert (out / record["path"]).read_bytes() == jpeg, "the original bytes must survive"


def test_source_pdf_figures_render_vector_regions_at_the_requested_dpi(tmp_path: Path) -> None:
    """Path (a): nothing embedded over the region -> render the page at the requested
    DPI and crop.  More pixels, not more detail; the pixel width follows the region."""
    jpeg = _tiny_jpeg(tmp_path, 1200, 800)
    pdf = _minimal_pdf(tmp_path, jpeg, (1200, 800), (100.0, 100.0, 400.0, 300.0))
    region = (72.0, 400.0, 360.0, 600.0)          # empty area -> render path
    block = {"type": "chart", "page_idx": 0, "img_path": "images/b.jpg",
             "bbox": [region[0] / 612 * 1000, region[1] / 792 * 1000,
                      region[2] / 612 * 1000, region[3] / 792 * 1000]}
    out = tmp_path / "figures"
    manifest = pr.extract_pdf_figures(pdf, _figure_content_list(tmp_path, [block]),
                                      out, dpi=600)
    record = manifest["figures"][0]
    assert record["method"] == "render" and record["rendered_dpi"] == 600
    expected = (region[2] - region[0]) * 600 / 72
    assert abs(record["width"] - expected) <= 2, (record["width"], expected)


def test_source_pdf_figures_are_deterministic_and_timestamp_free(tmp_path: Path) -> None:
    jpeg = _tiny_jpeg(tmp_path, 600, 400)
    pdf = _minimal_pdf(tmp_path, jpeg, (600, 400), (100.0, 100.0, 300.0, 200.0))
    block = {"type": "image", "page_idx": 0, "img_path": "images/a.jpg",
             "bbox": [100 / 612 * 1000, 100 / 792 * 1000, 300 / 612 * 1000, 200 / 792 * 1000]}
    cl = _figure_content_list(tmp_path, [block])
    first = pr.extract_pdf_figures(pdf, cl, tmp_path / "one")
    second = pr.extract_pdf_figures(pdf, cl, tmp_path / "two")
    assert first == second
    key = first["figures"][0]["path"]
    assert (tmp_path / "one" / key).read_bytes() == (tmp_path / "two" / key).read_bytes()
    import re as _re
    payload = json.dumps(first)
    assert not _re.search(r"\b\d{4}-\d{2}-\d{2}", payload), "no wall-clock date"
    assert "timestamp" not in payload and "merged_at" not in payload
