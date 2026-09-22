#!/usr/bin/env python3
"""One command from PDFs to writing-feature metrics (closes R5).

This is a thin orchestrator that chains two skills without duplicating either:

  1. paper-reader   PDF -> <papers>/paper-conversion/<stem>/{marker,mineru}
                    + <papers>/paper-merged/<stem>/{_MERGED.md,_META.json}
                    (GPU-bound, minutes per paper, resumable)
  2. this metric layer   canonical corpus -> the five deterministic metric artifacts
                    (pure stdlib, seconds per corpus)

The canonical corpus directory is DETECTED, not assumed: the v1 user layout is
<papers>/paper-analysis/, paper-reader v2 writes <papers>/paper-conversion/
(see paper_reader.py::_derive_output_dirs), and a third-party conversion can
live anywhere. Order: --analysis-dir -> <papers>/paper-analysis ->
<papers>/paper-conversion -> structural scan for <paper>/mineru/**/*_content_list.json.

Why it lives next to the metric layer rather than inside paper-reader: the
metric layer's input contract is "a canonical corpus directory", not "a PDF".
Anyone who already has a converted corpus (any directory layout) can run
profile_papers.py directly and never touch this file.

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/run_pipeline.py \\
        --papers /path/to/pdfs --out /path/to/metrics-out [--analysis-dir DIR] \\
        [--engines both|marker|mineru] [--no-resume] [--skip-convert] [--verify] [--dry-run]

Exit codes:
    0  success
    1  a downstream stage failed (paper-reader or the profiler)
    2  bad input (no PDFs found, missing dependency script, ...)

Reproducibility note: the bit-level guarantee still belongs to the metric layer
(Canonical Document -> metrics). The PDF -> Canonical stage is paper-reader's and
is attributed, not reproduced: its engine versions and the PDF hash are recorded
in each paper's _META.json when paper-reader supports it.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_READER = REPO_ROOT / "paper-reader" / "scripts" / "paper_reader.py"
PROFILER = SCRIPT_DIR / "profile_papers.py"

# Engine-side copies that paper-reader leaves inside the canonical corpus.
# They are conversion intermediates, not submitted papers.
_INTERMEDIATE_PDF_SUFFIXES = ("_origin.pdf",)

# Canonical-corpus directory names, in detection order:
#   paper-analysis   — v1 corpus layout (what the reference project on disk uses)
#   paper-conversion — what paper-reader v2 actually writes (_derive_output_dirs)
CORPUS_DIRNAMES = ("paper-analysis", "paper-conversion")
ENGINE_OUTPUT_DIRNAME = "paper-conversion"


def find_pdfs(papers_dir: Path, exclude_dir: Path | None = None) -> list[Path]:
    """Source PDFs under papers_dir, in a stable order.

    Two exclusions, both learned the hard way:
      * anything inside an engine-output tree (paper-analysis/ or
        paper-conversion/...) — those are engine outputs/origin copies, and
        counting them as "papers" inflates the corpus and re-converts the same
        document;
      * engine-intermediate names such as <stem>_origin.pdf.

    Pass exclude_dir to exclude an explicit corpus location (e.g. --analysis-dir
    pointing somewhere else under papers_dir).
    """
    if not papers_dir.is_dir():
        return []
    excluded_root = None
    if exclude_dir is not None:
        try:
            exclude_dir.resolve().relative_to(papers_dir.resolve())
            excluded_root = exclude_dir.resolve()
        except ValueError:
            excluded_root = None
    out: list[Path] = []
    for p in papers_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        parts = p.relative_to(papers_dir).parts[:-1]
        if any(name in parts for name in CORPUS_DIRNAMES):
            continue
        if excluded_root is not None:
            try:
                p.resolve().relative_to(excluded_root)
                continue
            except ValueError:
                pass
        if p.name.lower().endswith(_INTERMEDIATE_PDF_SUFFIXES):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.relative_to(papers_dir).as_posix())


def _scan_for_corpus_dirs(papers_dir: Path) -> list[Path]:
    """Corpus dirs discovered by structure: a dir holding <paper>/mineru/**/*_content_list.json.

    This is what makes the pipeline layout-agnostic: any directory whose papers
    carry MinerU content lists is a valid canonical corpus, whatever its name.
    Shallow paths win (deterministic ordering: depth, then path).
    """
    found: list[Path] = []
    if not papers_dir.is_dir():
        return found
    root = papers_dir.resolve()
    for content_list in sorted(papers_dir.rglob("*_content_list.json")):
        for ancestor in content_list.parents:
            if ancestor.name != "mineru":
                continue
            # Layout is <corpus>/<paper>/mineru/<id>/... so the corpus root is
            # two levels above the paper dir that owns the mineru/ tree.
            corpus = ancestor.parent.parent
            try:
                corpus.resolve().relative_to(root)
            except ValueError:
                break  # above --papers: not a usable corpus root
            if corpus.is_dir() and corpus not in found:
                found.append(corpus)
            break
    return sorted(found, key=lambda p: (len(p.parts), str(p)))


def detect_analysis_dir(papers_dir: Path, explicit: Path | None = None,
                        allow_prediction: bool = True) -> dict:
    """Resolve the canonical corpus directory without assuming its name.

    Detection order:
      1. --analysis-dir (explicit always wins)
      2. <papers>/paper-analysis   (v1 corpus layout)
      3. <papers>/paper-conversion (what paper-reader v2 writes)
      4. structural scan: any dir containing <paper>/mineru/**/*_content_list.json
      5. prediction: <papers>/paper-conversion, i.e. the dir the upcoming
         conversion will create (only when allow_prediction, i.e. not
         --skip-convert); otherwise None -> caller exits 2 with the candidates.

    Returns {"path": Path|None, "how": str, "candidates": list[Path],
             "warning": str|None}.
    """
    named = [papers_dir / name for name in CORPUS_DIRNAMES]
    candidates: list[Path] = ([explicit.resolve()] if explicit is not None else []) + named

    if explicit is not None:
        return {"path": explicit.resolve(), "how": "explicit (--analysis-dir)",
                "candidates": candidates, "warning": None}

    existing = [c for c in named if c.is_dir()]
    warning = None
    if len(existing) > 1:
        warning = ("both " + " and ".join(str(c) for c in existing)
                   + f" exist; using {existing[0]} — pass --analysis-dir to choose")
    if existing:
        return {"path": existing[0], "how": f"found {existing[0]}",
                "candidates": candidates, "warning": warning}

    scanned = _scan_for_corpus_dirs(papers_dir)
    if scanned:
        return {"path": scanned[0],
                "how": "structural scan (*/mineru/**/*_content_list.json)",
                "candidates": candidates + scanned, "warning": None}

    if allow_prediction:
        predicted = papers_dir / ENGINE_OUTPUT_DIRNAME
        return {"path": predicted, "how": "predicted (paper-reader will create it)",
                "candidates": candidates, "warning": None}
    return {"path": None, "how": "not found", "candidates": candidates,
            "warning": None}


def _print_candidates(detection: dict) -> None:
    """List every location that was checked, with its existence state."""
    print("[run_pipeline] candidates checked:", file=sys.stderr)
    for cand in detection["candidates"]:
        print(f"[run_pipeline]   - {cand} ({'exists' if cand.is_dir() else 'missing'})",
              file=sys.stderr)


def build_commands(papers_dir: Path, analysis_dir: Path, out_dir: Path,
                   engines: str = "both", resume: bool = True,
                   skip_convert: bool = False, verify: bool = False
                   ) -> list[list[str]]:
    """Return the exact command lines to run, in order (pure, testable)."""
    cmds: list[list[str]] = []
    if not skip_convert:
        convert = [sys.executable, str(PAPER_READER), str(papers_dir),
                   "--batch", "--engines", engines]
        if resume:
            convert.append("--resume")
        cmds.append(convert)
    profile = [sys.executable, str(PROFILER), "--corpus", str(analysis_dir),
               "--out", str(out_dir)]
    if verify:
        profile.append("--verify")
    cmds.append(profile)
    return cmds


def _run(cmd: list[str], dry_run: bool) -> int:
    printable = " ".join(cmd)
    print(f"[run_pipeline] $ {printable}", flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=str(REPO_ROOT)).returncode


def main() -> int:
    ap = argparse.ArgumentParser(
        description="PDF directory -> canonical corpus (auto-detected) -> "
                    "writing-feature metrics.",
    )
    ap.add_argument("--papers", required=True, type=Path,
                    help="directory containing the source PDFs")
    ap.add_argument("--out", "--output", dest="out", required=True, type=Path,
                    help="output directory for the five metric artifacts")
    ap.add_argument("--analysis-dir", type=Path, default=None,
                    help="canonical corpus dir; overrides auto-detection "
                         "(default order: <papers>/" + CORPUS_DIRNAMES[0] + ", "
                         "<papers>/" + CORPUS_DIRNAMES[1] + ", then any directory "
                         "containing <paper>/mineru/**/*_content_list.json)")
    ap.add_argument("--engines", choices=["both", "marker", "mineru"], default="both",
                    help="paper-reader engines (default: both)")
    ap.add_argument("--no-resume", dest="resume", action="store_false",
                    help="reconvert everything instead of resuming")
    ap.add_argument("--skip-convert", action="store_true",
                    help="skip paper-reader and profile an existing canonical corpus")
    ap.add_argument("--verify", action="store_true",
                    help="after profiling, re-run and assert the fingerprint files are identical")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without executing them")
    args = ap.parse_args()

    papers_dir = args.papers.resolve()
    out_dir = args.out.resolve()

    # Detect the canonical corpus instead of assuming its name. --skip-convert
    # forbids predicting a directory that does not exist yet (nothing will create
    # it), so prediction is only allowed on the converting path.
    detection = detect_analysis_dir(papers_dir, args.analysis_dir,
                                    allow_prediction=not args.skip_convert)
    analysis_dir = detection["path"]
    if detection["warning"]:
        print(f"[run_pipeline] WARNING: {detection['warning']}", file=sys.stderr)
    if analysis_dir is None:
        print("[run_pipeline] ERROR: no canonical corpus directory found and "
              "--skip-convert forbids predicting one.", file=sys.stderr)
        _print_candidates(detection)
        print("[run_pipeline] hint: pass --analysis-dir <DIR> pointing at the corpus "
              "produced by paper-reader (paper-analysis/ or paper-conversion/).",
              file=sys.stderr)
        return 2
    if detection["how"].startswith("predicted"):
        print(f"[run_pipeline] canonical  : {analysis_dir} "
              f"[{detection['how']}]", file=sys.stderr)

    if not PROFILER.is_file():
        print(f"[run_pipeline] ERROR: metric layer not found at {PROFILER}", file=sys.stderr)
        return 2
    if not args.skip_convert and not PAPER_READER.is_file():
        print(f"[run_pipeline] ERROR: paper-reader not found at {PAPER_READER}; "
              f"use --skip-convert if the corpus is already converted", file=sys.stderr)
        return 2

    if args.skip_convert:
        # --skip-convert exists precisely for "the corpus is already converted
        # and the submission PDFs are gone". Demanding PDFs here would defeat it.
        if not analysis_dir.is_dir():
            print(f"[run_pipeline] ERROR: --skip-convert needs an existing canonical "
                  f"corpus at {analysis_dir}", file=sys.stderr)
            _print_candidates(detection)
            print("[run_pipeline] hint: pass --analysis-dir <DIR> pointing at the corpus "
                  "produced by paper-reader (paper-analysis/ or paper-conversion/).",
                  file=sys.stderr)
            return 2
        pdfs = []
        excluded_n = 0
        print(f"[run_pipeline] using existing corpus: {analysis_dir}")
    else:
        all_pdfs = [p for p in papers_dir.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".pdf"] if papers_dir.is_dir() else []
        pdfs = find_pdfs(papers_dir, exclude_dir=analysis_dir)
        excluded_n = len(all_pdfs) - len(pdfs)
        if excluded_n:
            print(f"[run_pipeline] excluded {excluded_n} engine/intermediate PDF(s) "
                  f"(inside {'/ or '.join(CORPUS_DIRNAMES)}/, or *_origin.pdf)")
        if not pdfs:
            print(f"[run_pipeline] ERROR: no source PDFs under {papers_dir}", file=sys.stderr)
            if excluded_n:
                print(f"[run_pipeline] hint: all {excluded_n} PDF(s) found were engine "
                      f"intermediates inside {'/ or '.join(CORPUS_DIRNAMES)}/, "
                      f"or named *_origin.pdf. "
                      f"The submission PDFs are not in this directory — if the corpus is "
                      f"already converted, re-run with --skip-convert.", file=sys.stderr)
            return 2
    if not shutil.which(sys.executable) and not Path(sys.executable).exists():
        print(f"[run_pipeline] ERROR: interpreter not executable: {sys.executable}",
              file=sys.stderr)
        return 2

    if pdfs:
        print(f"[run_pipeline] papers     : {papers_dir} ({len(pdfs)} PDFs)")
    print(f"[run_pipeline] canonical  : {analysis_dir}  [{detection['how']}]")
    print(f"[run_pipeline] metrics out: {out_dir}")
    if args.skip_convert:
        print("[run_pipeline] stage 1 skipped (--skip-convert)")

    cmds = build_commands(papers_dir, analysis_dir, out_dir, engines=args.engines,
                          resume=args.resume, skip_convert=args.skip_convert,
                          verify=args.verify)
    for i, cmd in enumerate(cmds, start=1):
        rc = _run(cmd, args.dry_run)
        if rc != 0:
            stage = "paper-reader" if i == 1 and not args.skip_convert else "profiler"
            print(f"[run_pipeline] FAILED: stage {i} ({stage}) exited with {rc}",
                  file=sys.stderr)
            return rc if rc > 0 else 1

    if not args.dry_run:
        print("[run_pipeline] done. Artifacts:")
        for name in ("_domain_profile.json", "_domain_profile.md", "_corpus_summary.json",
                     "_per_paper_metrics.jsonl", "_run_meta.json"):
            path = out_dir / name
            print(f"  {'OK ' if path.exists() else 'MISSING '} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
