#!/usr/bin/env python3
"""Tests for run_pipeline.py — the one-command PDF -> metrics orchestrator (R5).

paper-reader cannot be exercised here (GPU, minutes per paper), so the tests pin
the *command construction* and the input validation, which is where the real
contract lives. --dry-run is used as the end-to-end smoke path.

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_run_pipeline.py -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import run_pipeline as rp  # noqa: E402

SCRIPT = SCRIPTS_DIR / "run_pipeline.py"


def _make_pdfs(root: Path, names=("a.pdf", "b.PDF", "notes.txt")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n).write_bytes(b"%PDF-1.4 fake")
    return root


def test_find_pdfs_is_case_insensitive_and_ordered(tmp_path: Path) -> None:
    papers = _make_pdfs(tmp_path / "papers")
    found = rp.find_pdfs(papers)
    assert [p.name for p in found] == ["a.pdf", "b.PDF"]


def test_find_pdfs_recurses_and_ignores_non_pdf(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    (papers / "sub").mkdir(parents=True)
    (papers / "sub" / "deep.pdf").write_bytes(b"%PDF")
    (papers / "x.txt").write_text("nope")
    assert [p.name for p in rp.find_pdfs(papers)] == ["deep.pdf"]


def test_find_pdfs_excludes_canonical_corpus_and_origin_copies(tmp_path: Path) -> None:
    """Engine intermediates must not be mistaken for submitted papers.

    Regression: pointing --papers at a project root used to pick up the
    paper-analysis/ tree, counting every *_origin.pdf as a source paper.
    """
    papers = _make_pdfs(tmp_path / "project", names=("real.pdf",))
    inner = papers / "paper-analysis" / "P1" / "mineru" / "p1" / "auto"
    inner.mkdir(parents=True)
    (inner / "p1_origin.pdf").write_bytes(b"%PDF")
    (inner / "p1.pdf").write_bytes(b"%PDF")
    assert [p.name for p in rp.find_pdfs(papers)] == ["real.pdf"]


def test_find_pdfs_excludes_origin_suffix_outside_corpus(tmp_path: Path) -> None:
    papers = tmp_path / "p"
    papers.mkdir()
    (papers / "paper.pdf").write_bytes(b"%PDF")
    (papers / "paper_origin.pdf").write_bytes(b"%PDF")
    assert [p.name for p in rp.find_pdfs(papers)] == ["paper.pdf"]


def test_find_pdfs_explicit_exclude_dir(tmp_path: Path) -> None:
    papers = tmp_path / "p"
    (papers / "corpus").mkdir(parents=True)
    (papers / "a.pdf").write_bytes(b"%PDF")
    (papers / "corpus" / "b.pdf").write_bytes(b"%PDF")
    assert [p.name for p in rp.find_pdfs(papers, exclude_dir=papers / "corpus")] == ["a.pdf"]


def test_find_pdfs_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert rp.find_pdfs(tmp_path / "empty") == []
    assert rp.find_pdfs(tmp_path / "does-not-exist") == []


def test_build_commands_full_pipeline(tmp_path: Path) -> None:
    cmds = rp.build_commands(tmp_path / "p", tmp_path / "a", tmp_path / "o")
    assert len(cmds) == 2
    convert, profile = cmds
    assert convert[1].endswith("paper_reader.py")
    assert "--batch" in convert and "--resume" in convert and "--engines" in convert
    assert profile[1].endswith("profile_papers.py")
    assert "--corpus" in profile and "--out" in profile


def test_build_commands_skip_convert_and_verify(tmp_path: Path) -> None:
    cmds = rp.build_commands(tmp_path / "p", tmp_path / "a", tmp_path / "o",
                             skip_convert=True, verify=True)
    assert len(cmds) == 1
    assert "--verify" in cmds[0]


def test_build_commands_no_resume(tmp_path: Path) -> None:
    cmds = rp.build_commands(tmp_path / "p", tmp_path / "a", tmp_path / "o", resume=False)
    assert "--resume" not in cmds[0]


def test_cli_help_exits_zero() -> None:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "--analysis-dir" in proc.stdout


def test_cli_no_pdfs_returns_2(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert proc.returncode == 2
    assert "no source PDFs" in proc.stderr


def test_skip_convert_does_not_require_source_pdfs(tmp_path: Path) -> None:
    """--skip-convert must work when the submission PDFs are gone.

    Regression: validation demanded source PDFs even in --skip-convert mode,
    which is exactly the "corpus already converted, PDFs deleted" situation.
    """
    analysis = tmp_path / "corpus"
    paper = analysis / "P1" / "mineru" / "p1" / "auto"
    paper.mkdir(parents=True)
    (paper / "p1_content_list.json").write_text(
        '[{"type": "text", "text_level": 2, "text": "1 Introduction"}, '
        '{"type": "text", "text": "The method is evaluated on benchmarks."}]',
        encoding="utf-8")
    out = tmp_path / "o"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(tmp_path / "nowhere"),
                           "--analysis-dir", str(analysis), "--out", str(out),
                           "--skip-convert"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (out / "_corpus_summary.json").exists()


def test_skip_convert_without_corpus_returns_2(tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(tmp_path),
                           "--analysis-dir", str(tmp_path / "nope"),
                           "--out", str(tmp_path / "o"), "--skip-convert"],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert "existing canonical corpus" in proc.stderr


# ---------------------------------------------------------------------------
# Test: canonical-corpus auto-detection (A5/G7 — default must not assume a name)
# ---------------------------------------------------------------------------

def _make_corpus(root: Path, name: str = "paper-analysis", paper: str = "P1") -> Path:
    """Create <root>/<name>/<paper>/mineru/<id>/auto/<id>_content_list.json."""
    d = root / name / paper / "mineru" / paper.lower() / "auto"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{paper.lower()}_content_list.json").write_text(
        '[{"type": "text", "text_level": 2, "text": "1 Introduction"}, '
        '{"type": "text", "text": "The method is evaluated on benchmarks."}]',
        encoding="utf-8")
    return root / name


def test_detect_explicit_analysis_dir_wins(tmp_path: Path) -> None:
    _make_corpus(tmp_path, "paper-analysis")
    explicit = tmp_path / "elsewhere"
    d = rp.detect_analysis_dir(tmp_path, explicit)
    assert d["path"] == explicit.resolve()
    assert d["how"] == "explicit (--analysis-dir)"


def test_detect_prefers_paper_analysis_over_paper_conversion(tmp_path: Path) -> None:
    _make_corpus(tmp_path, "paper-analysis")
    _make_corpus(tmp_path, "paper-conversion")
    d = rp.detect_analysis_dir(tmp_path)
    assert d["path"] == tmp_path / "paper-analysis"
    assert "paper-analysis" in d["how"]
    assert d["warning"], "an ambiguity between both corpus dirs must be reported"


def test_detect_falls_back_to_paper_conversion(tmp_path: Path) -> None:
    _make_corpus(tmp_path, "paper-conversion")
    d = rp.detect_analysis_dir(tmp_path)
    assert d["path"] == tmp_path / "paper-conversion"
    assert d["warning"] is None


def test_detect_structural_scan_for_unconventional_name(tmp_path: Path) -> None:
    """The name is not assumed: structure alone identifies a canonical corpus."""
    corpus = _make_corpus(tmp_path, "third-party-export")
    d = rp.detect_analysis_dir(tmp_path)
    assert d["path"] == corpus
    assert d["how"].startswith("structural scan")


def test_detect_predicts_paper_conversion_when_converting(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    d = rp.detect_analysis_dir(tmp_path, allow_prediction=True)
    assert d["path"] == tmp_path / "paper-conversion"
    assert d["how"].startswith("predicted")


def test_detect_returns_none_when_prediction_is_forbidden(tmp_path: Path) -> None:
    d = rp.detect_analysis_dir(tmp_path, allow_prediction=False)
    assert d["path"] is None
    assert d["how"] == "not found"
    assert {c.name for c in d["candidates"]} >= {"paper-analysis", "paper-conversion"}


def test_cli_skip_convert_auto_detects_without_analysis_dir(tmp_path: Path) -> None:
    """A5/G7 regression: the quick-start default used to fail unless the caller
    hand-wrote --analysis-dir."""
    papers = tmp_path / "project"
    _make_corpus(papers, "paper-analysis")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(out), "--skip-convert"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (out / "_corpus_summary.json").exists()
    assert "found" in proc.stdout


def test_cli_skip_convert_auto_detects_paper_conversion(tmp_path: Path) -> None:
    """paper-reader v2 writes paper-conversion/ — no --analysis-dir required."""
    papers = tmp_path / "project"
    _make_corpus(papers, "paper-conversion")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(out), "--skip-convert"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (out / "_corpus_summary.json").exists()


def test_cli_skip_convert_missing_corpus_lists_candidates_exit2(tmp_path: Path) -> None:
    papers = tmp_path / "project"
    papers.mkdir()
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(tmp_path / "o"), "--skip-convert"],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    err = proc.stderr
    assert "paper-analysis" in err and "paper-conversion" in err
    assert "candidates checked" in err
    assert "--analysis-dir" in err
    assert "paper_reader.py" not in proc.stdout  # nothing was executed


def test_cli_default_dry_run_targets_detected_corpus(tmp_path: Path) -> None:
    papers = tmp_path / "project"
    _make_corpus(papers, "paper-conversion")
    (papers / "a.pdf").write_bytes(b"%PDF")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(tmp_path / "out"), "--dry-run"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert f"--corpus {papers / 'paper-conversion'}" in proc.stdout


def test_find_pdfs_excludes_paper_conversion_intermediates(tmp_path: Path) -> None:
    """paper-reader v2 output dir must not be mistaken for source PDFs either."""
    papers = _make_pdfs(tmp_path / "project", names=("real.pdf",))
    inner = papers / "paper-conversion" / "P1" / "mineru" / "p1" / "auto"
    inner.mkdir(parents=True)
    (inner / "p1_origin.pdf").write_bytes(b"%PDF")
    (inner / "p1.pdf").write_bytes(b"%PDF")
    assert [p.name for p in rp.find_pdfs(papers)] == ["real.pdf"]


def test_cli_dry_run_prints_both_stages_without_executing(tmp_path: Path) -> None:
    papers = _make_pdfs(tmp_path / "papers")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(out), "--dry-run"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "paper_reader.py" in proc.stdout
    assert "profile_papers.py" in proc.stdout
    assert not out.exists()  # nothing was produced


def test_cli_skip_convert_profiles_existing_corpus(tmp_path: Path) -> None:
    """End-to-end smoke: --skip-convert on a synthetic canonical corpus."""
    papers = tmp_path / "papers"
    papers.mkdir()
    paper_dir = papers / "paper-analysis" / "P1" / "mineru" / "p1" / "auto"
    paper_dir.mkdir(parents=True)
    (paper_dir / "p1_content_list.json").write_text(
        '[{"type": "text", "text_level": 1, "text": "Title"}, '
        '{"type": "text", "text_level": 2, "text": "1 Introduction"}, '
        '{"type": "text", "text": "We propose a method. The method is evaluated."}]',
        encoding="utf-8")
    (papers / "dummy.pdf").write_bytes(b"%PDF-1.4")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--papers", str(papers),
                           "--out", str(out), "--skip-convert"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (out / "_corpus_summary.json").exists()
    assert "done." in proc.stdout
