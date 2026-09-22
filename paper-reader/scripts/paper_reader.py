#!/usr/bin/env python3
"""paper-reader: Marker + MinerU  PDF   +

v2    -

: paper_reader.py <pdf_path_or_dir> [--engines both|marker|mineru]
                  [--pages 0-9] [--batch] [--init] [--status] [--resume] [--force]
                  [--backfill-meta]
                  [--from-manifest PATH] [--import-urls PATH] [--max-pages N]
"""

from __future__ import annotations

import argparse
import base64
import difflib
import enum
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
MARKER_BIN = SKILL_ROOT / "venvs" / "marker" / "bin" / "marker_single"
MINERU_BIN = SKILL_ROOT / "venvs" / "mineru" / "bin" / "mineru"

# ── WSL → Windows   ────────────────────────────────────────────────
_WSL = "microsoft" in os.uname().release.lower()
_WSL_MARKER_PY = r"E:\venvs\marker\Scripts\python.exe"
_WSL_MINERU_PY = r"E:\venvs\mineru\Scripts\python.exe"

# ── GPU    ⭐  OOM   ───────────────────────────────────
_BRIDGE_SCRIPTS = (Path.home() / ".claude" / "skills" / "wsl-windows-bridge" / "scripts")
if str(_BRIDGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_SCRIPTS))
from gpu_safe_subprocess import (  # noqa: E402
    GpuLimits, build_gpu_env, GpuGovernor, GpuLease, InsufficientGpuBudget,
)

_GPU_FRACTION: float = 0.4
_CPU_THREADS: int = 6
_GPU_GOVERNOR: GpuGovernor | None = None
_GPU_WAIT_TIMEOUT: float = 600.0

# ──    ─────────────────────────────────────────────────
_PIPELINE_VERSION = "2.0"
_DEFAULT_MAX_PAGES = 200


# ═══════════════════════════════════════════════════════════════════════════════
#   Engine / source provenance (→ _META.json)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Upper-layer metrics must be able to attribute drift to a concrete engine build
# (which marker/MinerU/torch/CUDA/python produced this content_list.json).
# We probe each engine venv's interpreter once per process (memoized) and record
# the result in _META.json.
#
# Hard constraints:
#   * never raises  — any failure degrades to null + engine_versions_note
#   * never blocks  — bounded subprocess timeouts, at most one probe per batch
#   * works through the WSL→Windows bridge (E:\venvs\<engine>\Scripts\python.exe)

_ENGINE_PROBE_TIMEOUT = 45.0
_ENGINE_VENV_NAMES = ("marker", "mineru")
# Frozen _META.json contract (agreed with the metrics layer): exactly these keys,
# always present, null when the probe could not determine a value.
_ENGINE_CONTRACT_KEYS = ("marker", "mineru", "surya", "torch", "cuda", "python")

#: License identity of every engine whose code or weights we actually run.
#: Recorded next to the versions because a version number alone cannot tell a
#: downstream consumer that, e.g., the Surya *weights* are OpenRAIL-M (non-OSI,
#: free commercial use only under the revenue/funding threshold) even though the
#: Marker code that pulls them in is Apache-2.0. Static facts, so no probing.
_ENGINE_LICENSE_IDS = {
    "marker": {"code": "Apache-2.0", "weights": "Apache-2.0"},
    "mineru": {
        "code": "Apache-2.0",
        "code_note": "additional terms: commercial license required above 100M MAU or USD 20M monthly revenue; online services must credit MinerU",
        "weights": "AGPL-3.0 (MinerU2.5-2509-1.2B VLM weights)",
    },
    "surya": {
        "code": "Apache-2.0",
        "weights": "modified AI Pubs OpenRAIL-M (non-OSI; free commercial use only below the funding/revenue threshold)",
    },
    "torch": {"code": "BSD-3-Clause", "weights": None},
    "pdfplumber": {"code": "MIT", "weights": None},
    # Explicitly NOT used: AGPL would contaminate the published pipeline.
    "pymupdf": {"code": "AGPL-3.0 (NOT USED by design)", "weights": None},
}
_ENGINE_VERSIONS_CACHE: dict | None = None
_ENGINE_VERSIONS_LOCK = threading.Lock()

# Executed inside the target interpreter. The fast line is flushed before the
# (slow) torch import, so a timeout still leaves us with usable data.
_ENGINE_VERSION_PROBE_CODE = r"""
import json, sys
out = {"python": sys.version.split()[0]}
try:
    import importlib.metadata as md
except Exception:
    md = None
for key, pkg in (("marker", "marker-pdf"), ("mineru", "mineru"),
                 ("surya", "surya-ocr"), ("torch", "torch")):
    if md is None:
        out[key] = None
        continue
    try:
        out[key] = md.version(pkg)
    except Exception:
        out[key] = None
sys.stdout.write("<<<FAST>>>" + json.dumps(out) + "\n")
sys.stdout.flush()
try:
    import torch
    cuda = getattr(getattr(torch, "version", None), "cuda", None)
except Exception:
    cuda = None
sys.stdout.write("<<<CUDA>>>" + json.dumps({"cuda": cuda}) + "\n")
sys.stdout.flush()
"""

_FAST_MARK = "<<<FAST>>>"
_CUDA_MARK = "<<<CUDA>>>"


def _sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> tuple[str | None, str | None]:
    """Streaming SHA-256 of a file → (hex_digest|None, note|None). Never raises."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest(), None
    except Exception as e:
        return None, f"pdf_sha256 unavailable: {type(e).__name__}: {e}"


def _engine_probe_command(engine: str) -> list[str]:
    """Build the argv that runs the version probe in *engine*'s interpreter."""
    payload = base64.b64encode(_ENGINE_VERSION_PROBE_CODE.encode("utf-8")).decode("ascii")
    launcher = "import base64;exec(base64.b64decode('%s').decode('utf-8'))" % payload
    if _WSL:
        win_py = _WSL_MARKER_PY if engine == "marker" else _WSL_MINERU_PY
        return ["cmd.exe", "/c", win_py, "-c", launcher]
    return [str(SKILL_ROOT / "venvs" / engine / "bin" / "python"), "-c", launcher]


def _run_probe_command(cmd: list[str], timeout: float) -> tuple[str, str | None]:
    """Run a probe command → (stdout, error_note|None). Never raises."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:]
            return proc.stdout or "", f"exit {proc.returncode}: {tail[0][:200] if tail else ''}"
        return proc.stdout or "", None
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out, f"timeout after {timeout:.0f}s"
    except Exception as e:  # defensive: a broken bridge must not break a conversion
        return "", f"{type(e).__name__}: {e}"


def _parse_probe_stdout(stdout: str) -> dict:
    """Extract the <<<FAST>>> / <<<CUDA>>> JSON payloads from probe stdout."""
    payload: dict = {}
    for line in stdout.splitlines():
        for mark in (_FAST_MARK, _CUDA_MARK):
            if line.startswith(mark):
                try:
                    payload.update(json.loads(line[len(mark):]))
                except json.JSONDecodeError:
                    pass
    return payload


def _probe_one_env(engine: str) -> dict:
    """Probe one engine venv. Returns a dict; failure → {"_error": ...}. Never raises."""
    cmd = _engine_probe_command(engine)
    if not _WSL and not Path(cmd[0]).exists():
        return {"_error": f"interpreter not found: {cmd[0]}"}
    stdout, err = _run_probe_command(cmd, _ENGINE_PROBE_TIMEOUT)
    data = _parse_probe_stdout(stdout)
    if err:
        data["_error"] = err
    if not data:
        data["_error"] = "no probe output"
    return data


def _reset_engine_version_cache() -> None:
    """Test hook: drop the memoized engine-version probe result."""
    global _ENGINE_VERSIONS_CACHE
    _ENGINE_VERSIONS_CACHE = None


def _collect_engine_version_provenance() -> dict:
    """Uncached probe pass over both engine venvs. Never raises."""
    per_env: dict[str, dict] = {}
    errors: list[str] = []
    for engine in _ENGINE_VENV_NAMES:
        try:
            data = _probe_one_env(engine)
        except Exception as e:  # defensive: probe bugs must not abort a run
            data = {"_error": f"{type(e).__name__}: {e}"}
        per_env[engine] = data
        if data.get("_error"):
            errors.append(f"{engine}: {data['_error']}")

    def _first(key: str) -> str | None:
        for engine in _ENGINE_VENV_NAMES:
            value = (per_env.get(engine) or {}).get(key)
            if value:
                return str(value)
        return None

    # No host-interpreter fallback: the 'python' field must describe the *engine*
    # venv. Reporting the runner's python when the probe failed would silently
    # misattribute the environment, so a failed probe leaves it null + a note.
    versions = {
        "python": _first("python"),
        "marker": _first("marker"),
        "mineru": _first("mineru"),
        "surya": _first("surya"),
        "torch": _first("torch"),
        "cuda": _first("cuda"),
    }
    missing = [k for k, v in versions.items() if not v]
    if missing:
        errors.append("missing fields: " + ",".join(missing))
    mode = "wsl-bridge" if _WSL else "native"
    note = None
    if errors:
        note = (
            f"engine version probe degraded (mode={mode}): "
            + "; ".join(errors)
            + "; per_env=" + json.dumps(per_env, ensure_ascii=False)
        )
    return {"versions": versions, "note": note, "mode": mode}


def _engine_version_snapshot() -> dict:
    """Frozen _META.json provenance contract — memoized, never raises.

    Returns exactly:
      {
        "engine_versions": {"marker": …, "mineru": …, "torch": …, "cuda": …, "python": …},
        "engine_versions_source": "conversion_time" | "unavailable",
        "engine_versions_note": str | None
      }

    The five keys are always present (null when unknown) so the metrics layer can
    read them without .get() gymnastics. The probe runs at most once per process,
    so an N-paper batch pays for it exactly once; failures degrade to null + note.
    """
    global _ENGINE_VERSIONS_CACHE
    if _ENGINE_VERSIONS_CACHE is not None:
        return _ENGINE_VERSIONS_CACHE
    with _ENGINE_VERSIONS_LOCK:
        if _ENGINE_VERSIONS_CACHE is None:
            try:
                snap = _collect_engine_version_provenance()
            except Exception as e:  # last-resort guard: never abort a conversion
                snap = {
                    "versions": {},
                    "note": f"engine version probe crashed: {type(e).__name__}: {e}",
                    "mode": "wsl-bridge" if _WSL else "native",
                }
            versions = {k: snap.get("versions", {}).get(k) for k in _ENGINE_CONTRACT_KEYS}
            _ENGINE_VERSIONS_CACHE = {
                "engine_versions": versions,
                # Which license each engine's code and weights carry. A version
                # number alone cannot tell a consumer that the Surya weights are
                # OpenRAIL-M, so the identity is recorded with the version.
                # Full static policy record, not filtered by what happened to be
                # detected: it also documents the license we deliberately avoid
                # (PyMuPDF, AGPL) so a consumer cannot re-add it by accident.
                "engine_license_ids": {k: _ENGINE_LICENSE_IDS[k]
                                       for k in sorted(_ENGINE_LICENSE_IDS)},
                "engine_versions_source": (
                    "conversion_time" if any(versions.values()) else "unavailable"
                ),
                "engine_versions_note": snap.get("note"),
            }
    return _ENGINE_VERSIONS_CACHE


# ═══════════════════════════════════════════════════════════════════════════════
#   Stage 1.5 side-band: text-layer probe + language record
# ═══════════════════════════════════════════════════════════════════════════════
#
# The PDF -> content_list.json hop has no independent check of its own, and two
# classes of damage happen inside it *silently* (measured 2026-09-14 on the Chinese
# corpus, content_list basis: 10/10 papers lose 41.8-78.2% of the digits, and 75-104
# unspaced English runs appear).  scripts/textlayer_probe.py measures the hop; this
# block is the
# pipeline call site (issue #12 follow-up), so a converted corpus carries the
# evidence instead of needing a separate manual run per paper.
#
# Hard constraints (same style as the engine-provenance block above):
#   * never raises  — any failure degrades to a not_applicable record + note
#   * never blocks  — the probe is side-band; a broken probe must not fail a run
#   * never feeds canonical text — paper-metrics keeps its pure-stdlib / zero-LLM /
#     byte-reproducible contract, so nothing here may become a metric input
#   * byte-stable   — sorted keys, no timestamps, file name only (no abs paths)
#
# The language record exists because two layers must not drift apart: the metrics
# layer detects the language itself (text_metrics.detect_language) while this layer
# now records the same quantity per converted paper.  Both sides use the SAME
# character classes and threshold; the frozen cases live in
# scripts/lang_spec_cases.json and are asserted by BOTH test suites.
_TEXTLAYER_PROBE_FILENAME = "_textlayer_probe.json"

#: CJK ideograph ranges — MUST stay identical to text_metrics._CJK_RANGES.
_LANG_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
_LANG_CJK_RE = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi) for lo, hi in _LANG_CJK_RANGES) + "]")

#: Alpha token — MUST stay identical to text_metrics._ALPHA_TOKEN_RE: an internal
#: apostrophe or hyphen stays inside the token, and digits split words apart.
_LANG_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")

#: MUST stay identical to text_metrics.LANGUAGE_SUPPORT_CJK_THRESHOLD.
LANGUAGE_CJK_THRESHOLD = 0.10
_LANG_ROUND_DIGITS = 6

#: Which artifact a recorded cjk_ratio was computed from.  The metrics layer reads
#: the MinerU content_list.json, so that is the preferred basis here as well; the
#: merged markdown is only a fallback for marker-only runs.  The *rule* is shared, so
#: the language labels must agree even when the two ratios differ a little.
_LANG_SOURCE_CONTENT_LIST = "mineru_content_list"
_LANG_SOURCE_MERGED = "merged_markdown"
_LANG_SOURCE_BACKFILL = "merged_markdown_backfill"

#: The frozen _META.json language triple (agreed with the metrics layer).
_LANG_META_KEYS = ("language", "cjk_ratio", "lang_source")


def detect_language_record(text: str | None,
                           source: str | None = None) -> dict[str, Any]:
    """language / cjk_ratio / lang_source — same rule as paper-metrics.

    Mirrors text_metrics.detect_language exactly:

        cjk_ratio = cjk_chars / (cjk_chars + ascii_alpha_tokens)   0.0 if empty
        language  = "zh" if cjk_ratio > LANGUAGE_CJK_THRESHOLD else
                    ("en" if ascii_alpha_tokens else "unknown")

    text=None means "there is no artifact to measure" and is reported as nulls,
    never as "unknown": "zh/en", "no rule for this text" and "not measured" are
    three different states.  A missing measurement is never written as 0.
    """
    if text is None:
        return {"language": None, "cjk_ratio": None, "lang_source": None,
                "cjk_chars": None, "ascii_alpha_tokens": None}
    cjk_chars = len(_LANG_CJK_RE.findall(text))
    ascii_alpha_tokens = len(_LANG_ALPHA_TOKEN_RE.findall(text))
    denominator = cjk_chars + ascii_alpha_tokens
    cjk_ratio = (cjk_chars / denominator) if denominator else 0.0
    if cjk_ratio > LANGUAGE_CJK_THRESHOLD:
        language = "zh"
    elif ascii_alpha_tokens > 0:
        language = "en"
    else:
        language = "unknown"
    return {
        "language": language,
        "cjk_ratio": round(cjk_ratio, _LANG_ROUND_DIGITS),
        "lang_source": source,
        "cjk_chars": cjk_chars,
        "ascii_alpha_tokens": ascii_alpha_tokens,
    }


def read_text_or_none(path: Path | None) -> str | None:
    """Text of *path*, or None when it is absent or unreadable.  Never raises."""
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def resolve_canonical_md(paper_dir: Path) -> Path | None:
    """Canonical markdown of one converted paper, or None.

    Covers both on-disk layouts: the v2 pipeline writes _MERGED.md next to
    _META.json, v1 corpora keep a differently named markdown.  Falling back to the
    largest .md keeps the choice deterministic (no RNG, no mtime).
    """
    merged = paper_dir / "_MERGED.md"
    if merged.is_file():
        return merged
    candidates: list[tuple[int, str, Path]] = []
    for path in paper_dir.glob("*.md"):
        if path.name.startswith("_"):
            # Internal artifacts (_DIFF.md and friends) are not the paper's text:
            # the "largest .md" fallback must never return a diff file.
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 0:
            candidates.append((-size, path.name, path))
    return sorted(candidates)[0][2] if candidates else None


def resolve_metrics_canonical(conversion_dir: Path, *,
                              mineru_md_path: Path | None = None,
                              allow_tree_scan: bool = False
                              ) -> tuple[str | None, str | None]:
    """(text, source) of the artifact the metrics layer actually reads.

    mineru_md_path is this run's MinerU markdown: when it is given, only the
    content_list sitting next to it counts, so a JSON left behind by an earlier
    conversion can never be reported as this run's evidence.  allow_tree_scan=True
    is for callers that have no "this run" at all (the backfill walk over an
    existing corpus); the pipeline never uses it, because after a marker-only run
    a stale Chinese JSON would otherwise become the canonical side of the record.

    paper-metrics builds its canonical document from the MinerU
    content_list.json, NOT from the merged markdown, and that difference decides
    whether the probe sees the damage at all.  Measured on the Chinese corpus: the
    same PDF shows 2.1% digit loss against _MERGED.md but 55.5% against the
    content_list, because the markdown export keeps digits the JSON side dropped.
    Preferring the metrics artifact is therefore the point, not a detail.
    """
    if mineru_md_path is not None:
        # Exact stem first: a directory could hold more than one content_list, and
        # then "the first non-empty one" would be another paper's text while the
        # record still claims mineru_content_list.  The glob stays as a fallback for
        # layouts that name the JSON differently from the markdown.
        md = Path(mineru_md_path)
        exact = md.parent / f"{md.stem}_content_list.json"
        if exact.is_file():
            candidates = [exact]
        else:
            # Fallback for layouts that name the JSON differently - but only when
            # the directory is unambiguous.  With several candidates we cannot tell
            # which one belongs to this run, and guessing is exactly how another
            # paper's text ends up labelled as this one's canonical.
            globbed = sorted(md.parent.glob("*_content_list.json"))
            candidates = globbed if len(globbed) == 1 else []
    elif allow_tree_scan:
        candidates = sorted(conversion_dir.glob("mineru/*/auto/*_content_list.json"))
    else:
        # No MinerU output from this run and no permission to scan: the caller falls
        # back to its own merged markdown rather than trusting whatever JSON an
        # earlier run happened to leave in the tree.
        return None, None
    for path in candidates:
        try:
            blocks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            # RecursionError: a pathological nesting depth must skip this file, not
            # blow up the conversion that already succeeded.
            continue
        if not isinstance(blocks, list):
            continue
        parts = [str(b.get("text") or "") for b in blocks
                 if isinstance(b, dict) and b.get("text")]
        if parts:
            return "\n".join(parts), _LANG_SOURCE_CONTENT_LIST
    return None, None


def run_textlayer_probe(pdf: Path, paper_dir: Path, canonical_text: str | None,
                        *, force: bool = False,
                        canonical_source: str | None = None) -> dict[str, Any]:
    """Run stage 1.5 for one paper and write _textlayer_probe.json. Never raises.

    An existing record is reused unless *force* — but only after it proves to belong
    to this very PDF and comparison side (see _cached_record_matches): given the same
    inputs the probe is deterministic, so re-reading a 200-page PDF on every resume
    would buy nothing, while a record about a *different* hop would be worse than no
    record at all.  The caller reads verdict and warnings back.
    """
    out_path = paper_dir / _TEXTLAYER_PROBE_FILENAME
    if not force and _probe_cache_hit(out_path, pdf, canonical_source, canonical_text):
        cached = _read_probe_record(out_path)
        if cached is not None:
            return cached
    if not pdf.is_file():
        # The text layer exists only inside the source PDF: when the file itself is
        # gone the hop cannot be measured at all (this is what a backfill over a
        # corpus whose PDFs were cleaned up hits).  Reported, never raised.
        return _store_probe_record(
            out_path, _probe_unavailable(f"source PDF not found: {pdf.name}",
                                         warning="PDF_NOT_FOUND"))
    try:
        import textlayer_probe  # lazy: keeps the pure part dependency-free
    except Exception as exc:  # noqa: BLE001 - a probe bug must not stop a run
        return _store_probe_record(
            out_path,
            _probe_unavailable(f"import textlayer_probe: {type(exc).__name__}: {exc}"))
    try:
        record = textlayer_probe.probe_pdf(pdf, canonical_text, canonical_source)
    except Exception as exc:  # noqa: BLE001
        return _store_probe_record(out_path, _probe_unavailable(f"{type(exc).__name__}: {exc}"))
    return _store_probe_record(out_path, record, render=textlayer_probe.render_json)


def _store_probe_record(out_path: Path, record: dict[str, Any], render=None) -> dict[str, Any]:
    """Write the record and return it; a write failure only costs the artifact.

    Degraded records are stored too, on purpose: "this hop was never measured" is
    itself evidence, and leaving it off disk would contradict the probe contract
    that a scanned PDF is recorded as not_applicable rather than as a pass."""
    try:
        if render is None:
            payload = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                 indent=2) + "\n"
        else:
            payload = render(record)
        out_path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        print(f"  [probe] could not write {out_path.name}: {exc}", file=sys.stderr)
    return record


def _probe_cache_hit(out_path: Path, pdf: Path, canonical_source: str | None,
                     canonical_text: str | None) -> bool:
    """True when the stored record may be returned as-is.

    One predicate for two callers: the probe itself (reuse or re-measure) and the
    backfill (report honestly whether a paper was measured or reused).
    """
    if not out_path.is_file():
        return False
    cached = _read_probe_record(out_path)
    return bool(cached is not None and _cached_record_matches(
        cached, pdf, canonical_source, canonical_text))


def _cached_record_matches(record: dict[str, Any], pdf: Path,
                           canonical_source: str | None,
                           canonical_text: str | None = None) -> bool:
    """Is a stored record still about the same PDF and the same comparison side?

    Reuse is safe only when the record provably describes the current input.  The
    source PDF can be replaced under the same stem (corrected upload, re-download),
    and the comparison side can appear or change (a content_list.json arriving after
    a marker-only run); reusing then would leave evidence about a different hop.  A
    record written before canonical_source existed carries no label and is therefore
    refreshed once instead of being trusted.
    """
    if record.get("canonical_source") != canonical_source:
        return False
    # The label is not the content: a re-conversion rewrites content_list.json
    # under the same name, and the same PDF would then be compared against a
    # different text.  Hash the text itself so that cannot pass as a cache hit.
    expected_canonical = (
        hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        if isinstance(canonical_text, str) else None
    )
    if record.get("canonical_sha256") != expected_canonical:
        return False
    probe_version, pdfplumber_version = _current_probe_versions()
    if record.get("probe_version") != probe_version:
        return False
    if record.get("pdfplumber_version") != pdfplumber_version:
        return False
    recorded = record.get("pdf_sha256")
    if not isinstance(recorded, str) or not recorded:
        return False
    digest, _note = _sha256_of_file(pdf)
    return digest == recorded


def _probe_unavailable(detail: str, warning: str = "PROBE_UNAVAILABLE") -> dict[str, Any]:
    """Degraded probe record: an unmeasured hop, never a silent pass."""
    return {
        "verdict": "not_applicable",
        "warnings": [warning],
        "error": detail,
    }


def _current_probe_versions() -> tuple[str | None, str | None]:
    """(probe version, pdfplumber version) of the code that would run right now.

    Both are recorded in every probe result, so a stale record can be detected
    instead of trusted: a probe-rule change or a pdfplumber upgrade moves the
    text-layer extraction, which is exactly what the record claims to describe.
    Returns (None, None) when the module cannot be imported at all; a record that
    carries real versions then never matches, which is the safe direction.
    """
    try:
        import textlayer_probe
        return (getattr(textlayer_probe, "PROBE_VERSION", None),
                textlayer_probe.pdfplumber_version())
    except Exception:  # noqa: BLE001
        return None, None


def collect_stage_1_5(pdf: Path, merged_dir: Path, conversion_dir: Path, stem: str,
                      *, force: bool, enabled: bool,
                      result: PaperResult,
                      mineru_md_path: Path | None = None) -> dict[str, Any]:
    """Run the whole side-band stage and return the language record.

    Everything here is evidence, never an input, so this function swallows every
    exception it can meet (recording a degraded verdict) instead of letting one
    escape into process_one.  A corrupt stored record must never turn a paper that
    already converted successfully into a failed one, which is exactly the
    "never blocks" red line.
    """
    # A literal, not a call: nothing here may raise before the try, so the
    # "never blocks a conversion" contract does not depend on another function's
    # behaviour.  The except branch returns whatever was established meanwhile.
    lang_record: dict[str, Any] = {"language": None, "cjk_ratio": None,
                                   "lang_source": None, "cjk_chars": None,
                                   "ascii_alpha_tokens": None}
    try:
        canonical_text, canonical_source = resolve_metrics_canonical(
            conversion_dir, mineru_md_path=mineru_md_path)
        if canonical_text is None:
            # No MinerU JSON (a marker-only run): fall back to paper-reader's own
            # merged markdown, labelled as such so the weaker basis stays visible.
            canonical_text = read_text_or_none(resolve_canonical_md(merged_dir))
            canonical_source = (_LANG_SOURCE_MERGED if canonical_text is not None
                                else None)
        lang_record = detect_language_record(canonical_text, canonical_source)
        if not enabled:
            return lang_record
        probe_record = run_textlayer_probe(pdf, merged_dir, canonical_text,
                                           force=force,
                                           canonical_source=canonical_source)
        probe_file = merged_dir / _TEXTLAYER_PROBE_FILENAME
        result.probe_path = str(probe_file) if probe_file.is_file() else None
        result.probe_verdict = probe_record.get("verdict")
        # Defensive: the record may come from disk, so warnings is whatever the file
        # said (a hand-edited "warnings": 123 must not iterate).
        raw_warnings = probe_record.get("warnings")
        result.probe_warnings = ([str(w) for w in raw_warnings]
                                 if isinstance(raw_warnings, list) else [])
        if result.probe_verdict == "warn":
            # Loud on purpose: this is the point of the stage - a conversion that
            # dropped 55% of the digits used to look exactly like a good one.
            print(f"  [{stem}] TEXTLAYER WARN: {', '.join(result.probe_warnings)} "
                  f"(digit_loss_rate={probe_record.get('digit_loss_rate')}, "
                  f"unspaced_runs_introduced="
                  f"{probe_record.get('unspaced_runs_introduced')})",
                  file=sys.stderr)
        return lang_record
    except Exception as exc:  # noqa: BLE001 - side-band evidence must never fail a run
        print(f"  [{stem}] text-layer probe failed "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        # "error" and not "not_applicable": a crash is not a designed non-case, and
        # downstream "verdict != warn means fine" logic must not sweep it into the
        # passing bucket.  A half-written record must not look like evidence either.
        result.probe_verdict = "error"
        result.probe_warnings = ["PROBE_CRASHED"]
        result.probe_path = None
        return lang_record


def _read_probe_record(path: Path) -> dict[str, Any] | None:
    """Parse an existing probe record; None when unusable (caller re-probes)."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


# ═══════════════════════════════════════════════════════════════════════════════
#   Enums
# ═══════════════════════════════════════════════════════════════════════════════

class ErrorType(str, enum.Enum):
    """.  enum  JSON ."""
    # 0:
    FILE_MISSING = "file_missing"
    EMPTY_FILE = "empty_file"
    NOT_A_PDF = "not_a_pdf"
    ENCRYPTED = "encrypted"
    CORRUPTED = "corrupted"
    TOO_LARGE = "too_large"
    PRECHECK_CRASHED = "precheck_crashed"
    # 1:
    CUDA_OOM = "cuda_oom"
    TIMEOUT = "timeout"
    ENGINE_CRASH = "engine_crash"
    SILENT_CRASH = "silent_crash"
    NO_OUTPUT = "no_output"
    GPU_BUDGET_UNAVAILABLE = "gpu_budget_unavailable"
    # 2:
    BOTH_ENGINES_FAILED = "both_engines_failed"
    NORMALIZE_CRASH = "normalize_crash"
    IMG_COPY_FAILED = "img_copy_failed"
    WRITE_FAILED = "write_failed"
    DISK_FULL = "disk_full"
    #
    WSL_BRIDGE_FAILED = "wsl_bridge_failed"
    PERMISSION_DENIED = "permission_denied"
    INTERRUPTED = "interrupted"

    def __str__(self) -> str:
        return self.value


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

_SEVERITY_EMOJI = {
    "fatal":    "💥",
    "skip":     "⏭️",
    "degrade":  "⚠️",
    "ok":       "✅",
    "pending":  "⏳",
}

_PHASE_NAMES = {
    "precheck": "PRECHECK",
    "phase1_converted": "CONVERTED",
    "phase2_merged": "MERGED",
    "phase3_summarized": "SUMMARIZED",
}


def _precheck_emoji(status: str) -> str:
    """    emoji ."""
    if status == "passed":
        return _SEVERITY_EMOJI["ok"]
    if status == "pending":
        return _SEVERITY_EMOJI["pending"]
    return _SEVERITY_EMOJI["skip"]


def _engine_pair_emoji(phase: dict) -> str:
    """ marker/mineru   ."""
    status = phase.get("status", "pending")
    if status == "done":
        return f"{_SEVERITY_EMOJI['ok']} {_SEVERITY_EMOJI['ok']}"
    if status == "degraded":
        m = _SEVERITY_EMOJI["ok"] if phase.get("marker_ok") else _SEVERITY_EMOJI["skip"]
        u = _SEVERITY_EMOJI["ok"] if phase.get("mineru_ok") else _SEVERITY_EMOJI["skip"]
        err = ""
        for eng in ("marker", "mineru"):
            if not phase.get(f"{eng}_ok"):
                etype = phase.get(f"{eng}_error_type", "unknown")
                err = f"💥{ErrorType(etype).value[:6]}" if etype != "unknown" else "💥err"
        return f"{m} {u}{err}"
    if status == "failed":
        return "❌ ❌"
    if status == "skipped":
        return "⏭️"
    return _SEVERITY_EMOJI["pending"]


def _phase_emoji(phase: dict, key: str) -> str:
    """    emoji."""
    if not phase:
        return _SEVERITY_EMOJI["pending"]
    status = phase.get("status", "pending")
    if status == "done":
        return _SEVERITY_EMOJI["ok"]
    if status == "degraded":
        return _SEVERITY_EMOJI["degrade"]
    if status in ("failed", "skipped"):
        return _SEVERITY_EMOJI["skip"]
    return _SEVERITY_EMOJI["pending"]


def _severity_emoji(status: str) -> str:
    """    -> emoji."""
    return _SEVERITY_EMOJI.get(status, "  ")


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PrecheckResult:
    """PDF   ."""
    ok: bool
    status: str = "pending"          # passed | failed | skipped
    reason: str = ""                 # ErrorType value
    detail: str = ""
    page_count: int = 0
    pdf_hash: str = ""
    pdf_sha256: str = ""             # full source-PDF SHA-256 (provenance)
    scan_warning: bool = False       # True =

    @classmethod
    def failed(cls, reason: ErrorType, detail: str = "",
               pdf_sha256: str = "") -> "PrecheckResult":
        return cls(ok=False, status="failed", reason=reason.value, detail=detail,
                   pdf_sha256=pdf_sha256)

    @classmethod
    def skipped(cls, reason: ErrorType, detail: str = "",
                pdf_sha256: str = "") -> "PrecheckResult":
        return cls(ok=False, status="skipped", reason=reason.value, detail=detail,
                   pdf_sha256=pdf_sha256)

    def to_record(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "page_count": self.page_count,
            "pdf_hash": self.pdf_hash,
            "pdf_sha256": self.pdf_sha256,
            "scan_warning": self.scan_warning,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

def run_precheck(pdf: Path, max_pages: int = _DEFAULT_MAX_PAGES) -> PrecheckResult:
    """PDF    ——   GPU ,  CPU   ."""

    if not pdf.exists():
        return PrecheckResult.skipped(ErrorType.FILE_MISSING, str(pdf))

    size = pdf.stat().st_size
    if size < 1024:
        return PrecheckResult.skipped(ErrorType.EMPTY_FILE, f"{size} bytes")

    # Magic bytes
    try:
        with open(pdf, "rb") as f:
            header = f.read(5)
    except (OSError, IOError) as e:
        return PrecheckResult.failed(ErrorType.PERMISSION_DENIED, str(e))

    if not header.startswith(b"%PDF-"):
        return PrecheckResult.skipped(
            ErrorType.NOT_A_PDF,
            detail=f"magic bytes: {header[:10]!r}",
        )

    # Source-PDF SHA-256 (provenance for _META.json). One streaming pass replaces
    # the previous full read_bytes(); failure is non-fatal (field stays empty).
    pdf_sha256, _sha_note = _sha256_of_file(pdf)
    pdf_hash = (pdf_sha256 or "")[:16]

    # pypdf
    try:
        from pypdf import PdfReader
    except ImportError:
        # pypdf   →   +
        return PrecheckResult(
            ok=True, status="passed", page_count=-1, pdf_hash=pdf_hash,
            pdf_sha256=pdf_sha256 or "",
            reason="", detail="pypdf not installed, skipped deep check",
        )

    try:
        reader = PdfReader(str(pdf))
    except Exception as e:
        return PrecheckResult.skipped(ErrorType.CORRUPTED, str(e)[:500],
                                      pdf_sha256=pdf_sha256 or "")

    if reader.is_encrypted:
        return PrecheckResult.skipped(ErrorType.ENCRYPTED, "PDF is password-protected",
                                      pdf_sha256=pdf_sha256 or "")

    page_count = len(reader.pages)
    if page_count > max_pages:
        return PrecheckResult.skipped(
            ErrorType.TOO_LARGE,
            detail=f"{page_count} pages > max {max_pages}",
            pdf_sha256=pdf_sha256 or "",
        )

    #   :  10  text
    text_pages = 0
    for page in reader.pages[:10]:
        try:
            text = page.extract_text()
            if text and len(text.strip()) > 50:
                text_pages += 1
        except Exception:
            pass
    scan_warning = text_pages < 2

    return PrecheckResult(
        ok=True, status="passed",
        page_count=page_count, pdf_hash=pdf_hash,
        pdf_sha256=pdf_sha256 or "",
        scan_warning=scan_warning,
        detail="",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

class PipelineState:
    """_pipeline_state.json  CRUD  ."""

    def __init__(self, state_path: Path):
        self._path = state_path
        self._data: dict = {"pipeline_version": _PIPELINE_VERSION, "last_updated": "", "papers": {}}
        self._dirty = False
        if state_path.exists():
            self._load()
        else:
            self._dirty = True

    def _load(self) -> None:
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            #
            if "papers" not in self._data:
                self._data["papers"] = {}
            self._data.setdefault("pipeline_version", _PIPELINE_VERSION)
        except (json.JSONDecodeError, OSError):
            self._data = {"pipeline_version": _PIPELINE_VERSION, "last_updated": "", "papers": {}}
            self._dirty = True

    def save(self) -> None:
        """."""
        import datetime
        self._data["last_updated"] = datetime.datetime.now().isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False

    # ──   ──────────────────────────────────────────────────────────

    def has(self, stem: str) -> bool:
        return stem in self._data.get("papers", {})

    def get(self, stem: str) -> dict:
        return self._data.get("papers", {}).get(stem, {})

    def ensure_entry(self, stem: str) -> dict:
        """   ,  ."""
        papers = self._data.setdefault("papers", {})
        if stem not in papers:
            papers[stem] = {
                "source": {},
                "pdf_hash": None,
                "pdf_sha256": None,
                "precheck": {"status": "pending"},
                "phase1_converted": {"status": "pending"},
                "phase2_merged": {"status": "pending"},
                "phase3_summarized": {"status": "pending"},
            }
            self._dirty = True
        return papers[stem]

    def set_precheck(self, stem: str, result: PrecheckResult) -> None:
        entry = self.ensure_entry(stem)
        entry["precheck"] = result.to_record()
        entry["pdf_hash"] = result.pdf_hash
        if result.pdf_sha256:
            entry["pdf_sha256"] = result.pdf_sha256
        self._dirty = True

    def set_pdf_sha256(self, stem: str, digest: str) -> None:
        """Persist the full source-PDF SHA-256 (provenance for _META.json)."""
        if not digest:
            return
        entry = self.ensure_entry(stem)
        if entry.get("pdf_sha256") != digest:
            entry["pdf_sha256"] = digest
            self._dirty = True

    def set_source(self, stem: str, source_info: dict) -> None:
        entry = self.ensure_entry(stem)
        existing = entry.get("source") or {}
        for k, v in source_info.items():
            if v:  #
                existing[k] = v
        entry["source"] = existing
        self._dirty = True

    def set_phase(self, stem: str, phase: str, record: dict) -> None:
        entry = self.ensure_entry(stem)
        entry[phase] = record
        self._dirty = True

    def set_phase_from_error(self, stem: str, phase: str, etype: ErrorType, msg: str) -> None:
        self.set_phase(stem, phase, {
            "status": "failed",
            "error_type": etype.value,
            "error": msg[:500],
        })

    # ──   ──────────────────────────────────────────────────────────

    def is_fully_done(self, stem: str) -> bool:
        """          done  degraded."""
        entry = self.get(stem)
        if not entry:
            return False
        # phase2_merged belongs here: a paper whose merge failed is NOT done, yet
        # --resume used to skip it forever (the failed merge was never retried and,
        # since stage 1.5 runs after the merge, it never produced probe evidence
        # either).  Retrying costs a re-conversion, which is the honest price of
        # having called it finished.
        for phase in ("precheck", "phase1_converted", "phase2_merged"):
            s = (entry.get(phase) or {}).get("status", "pending")
            if s not in ("done", "degraded", "passed"):
                return False
        return True

    def needs_precheck(self, stem: str) -> bool:
        e = self.get(stem)
        return (e.get("precheck") or {}).get("status") not in ("passed",)

    def needs_phase1(self, stem: str) -> bool:
        e = self.get(stem)
        return (e.get("phase1_converted") or {}).get("status") not in ("done", "degraded")

    def needs_phase2(self, stem: str) -> bool:
        e = self.get(stem)
        return (e.get("phase2_merged") or {}).get("status") not in ("done", "degraded")

    # ──  /  ─────────────────────────────────────────────────────

    def init_from_pdfs(self, pdfs: list[Path], max_pages: int) -> list[str]:
        """ PDF   (   )."""
        new_stems = []
        for pdf in pdfs:
            stem = pdf.stem
            if self.has(stem):
                continue
            precheck = run_precheck(pdf, max_pages)
            self.set_precheck(stem, precheck)
            if not precheck.ok:
                print(f"  [{stem}] {precheck.reason}: {precheck.detail[:80]}", file=sys.stderr)
            else:
                print(f"  [{stem}] ✅ {precheck.page_count} ", file=sys.stderr)
            new_stems.append(stem)
        if new_stems:
            self.save()
        return new_stems

    def import_manifest(self, manifest: dict) -> int:
        """  _download_manifest.json  source  .  filename ."""
        papers_in_manifest = manifest.get("papers", [])
        matched = 0
        for paper in papers_in_manifest:
            filename = paper.get("filename", "")
            stem = Path(filename).stem if filename else ""
            if not stem or not self.has(stem):
                continue
            source_info = {
                k: v for k, v in paper.items()
                if k != "filename" and v
            }
            if source_info:
                self.set_source(stem, source_info)
                matched += 1
        if matched:
            self.save()
            print(f"   {matched}  PDF  source ", file=sys.stderr)
        else:
            print(f"  :    PDF  _pipeline_state.json ", file=sys.stderr)
        return matched

    def papers_summary(self) -> list[dict]:
        """    ."""
        return [
            {"stem": stem, **entry}
            for stem, entry in sorted(self._data.get("papers", {}).items())
        ]


# ═══════════════════════════════════════════════════════════════════════════════
#   GPU
# ═══════════════════════════════════════════════════════════════════════════════

def _build_gpu_env(gpu_fraction: float, cpu_threads: int) -> dict[str, str]:
    limits = GpuLimits(
        gpu_memory_fraction=gpu_fraction if gpu_fraction > 0 else 1.0,
        cpu_threads=cpu_threads,
    )
    env = build_gpu_env(limits)
    if gpu_fraction <= 0:
        env.pop("PYTORCH_CUDA_ALLOC_CONF", None)
        wslenv = env.get("WSLENV", "")
        env["WSLENV"] = ":".join(
            x for x in wslenv.split(":") if not x.startswith("PYTORCH_CUDA_ALLOC_CONF")
        )
    return env


def _wsl_to_win(path: Path) -> str:
    r = subprocess.run(["wslpath", "-w", str(path)],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _run_ps(py_exe: str, module: str, args: list[str], timeout: int,
            job_name: str = "gpu-task") -> subprocess.CompletedProcess:
    cli_map = {
        "marker.scripts.convert_single": (
            "from marker.scripts.convert_single import convert_single_cli; "
            "import sys; sys.exit(convert_single_cli())"
        ),
        "mineru.cli.client": (
            "from mineru.cli.client import main; "
            "import sys; sys.exit(main())"
        ),
    }
    code = cli_map.get(module, f"import {module}")
    cmd = ["cmd.exe", "/c", py_exe, "-c", code] + args
    env = _build_gpu_env(_GPU_FRACTION, _CPU_THREADS)

    if _GPU_GOVERNOR is not None and _GPU_FRACTION > 0:
        budget_mb = int(_GPU_FRACTION * _GPU_GOVERNOR.total_vram_mb)
        try:
            with _GPU_GOVERNOR.acquire(
                budget_mb=budget_mb, job_name=job_name,
                timeout=_GPU_WAIT_TIMEOUT,
            ):
                return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                      encoding="utf-8", errors="replace",
                                      cwd="/mnt/e/temp", env=env)
        except InsufficientGpuBudget:
            return subprocess.CompletedProcess(
                args=cmd, returncode=124,
                stdout="", stderr=f"GPU budget unavailable for '{job_name}'; skipped\n",
            )
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", cwd="/mnt/e/temp", env=env)


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_engine_error(result: subprocess.CompletedProcess) -> ErrorType:
    stderr = (result.stderr or "").lower()
    if "cuda" in stderr and ("out of memory" in stderr or "oom" in stderr):
        return ErrorType.CUDA_OOM
    if result.returncode == 124 or "gpu budget unavailable" in stderr:
        return ErrorType.GPU_BUDGET_UNAVAILABLE
    if "no output" in stderr or "produced no output" in stderr:
        return ErrorType.NO_OUTPUT
    if not stderr.strip():
        return ErrorType.SILENT_CRASH
    return ErrorType.ENGINE_CRASH


# ═══════════════════════════════════════════════════════════════════════════════
#   —
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EngineResult:
    engine: str
    ok: bool
    elapsed_sec: float
    md_path: str | None = None
    img_count: int = 0
    error: str | None = None
    error_type: str | None = None
    img_breakdown: dict = field(default_factory=dict)


@dataclass
class PaperResult:
    pdf_path: str
    stem: str
    marker: EngineResult | None = None
    mineru: EngineResult | None = None
    diff_path: str | None = None
    diff_line_count: int = 0
    merged_path: str | None = None
    merged_supplement_count: int = 0
    precheck: PrecheckResult | None = None
    phases_done: list[str] = field(default_factory=list)
    #: Stage 1.5 side-band evidence (issue #12 follow-up).  Kept on the result so
    #: the end-of-run summary can surface a silent corruption that would otherwise
    #: only sit in a JSON file nobody opens.
    probe_path: str | None = None
    probe_verdict: str | None = None
    probe_warnings: list[str] = field(default_factory=list)


def run_marker(pdf: Path, out_dir: Path, pages: str | None) -> EngineResult:
    """ marker_single  PDF."""
    marker_out = out_dir / "marker"
    marker_out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        if _WSL:
            win_pdf = _wsl_to_win(pdf)
            win_out = _wsl_to_win(marker_out)
            args = [win_pdf, "--output_dir", win_out]
            if pages:
                args += ["--page_range", pages]
            r = _run_ps(_WSL_MARKER_PY, "marker.scripts.convert_single", args,
                        timeout=1800, job_name=f"marker:{pdf.name}")
        else:
            cmd = [str(MARKER_BIN), str(pdf), "--output_dir", str(marker_out)]
            if pages:
                cmd += ["--page_range", pages]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        elapsed = time.time() - t0
        if r.returncode != 0:
            etype = _classify_engine_error(r)
            err = r.stderr[-2000:] if r.stderr else "unknown error"
            return EngineResult("marker", False, elapsed, error=err,
                               error_type=etype.value)
        stem = pdf.stem
        md = marker_out / stem / f"{stem}.md"
        if not md.exists():
            mds = list(marker_out.rglob("*.md"))
            md = mds[0] if mds else None
        if md is None:
            etype = ErrorType.NO_OUTPUT
            err = "no md output; " + (r.stderr.strip()[:500] if r.stderr.strip() else "marker produced no output")
            return EngineResult("marker", False, elapsed, error=err,
                               error_type=etype.value)
        img_dir = md.parent
        img_count = sum(1 for _ in img_dir.glob("*.jpeg"))
        img_count += sum(1 for _ in img_dir.glob("*.png"))
        return EngineResult("marker", True, elapsed, str(md), img_count)
    except subprocess.TimeoutExpired:
        return EngineResult("marker", False, time.time() - t0,
                           error="timeout 30min",
                           error_type=ErrorType.TIMEOUT.value)
    except Exception as e:
        return EngineResult("marker", False, time.time() - t0,
                           error=str(e),
                           error_type=ErrorType.ENGINE_CRASH.value)


def run_mineru(pdf: Path, out_dir: Path, pages: str | None,
               method: str, backend: str, lang: str | None) -> EngineResult:
    """ mineru  PDF."""
    mineru_out = out_dir / "mineru"
    mineru_out.mkdir(parents=True, exist_ok=True)

    if _WSL:
        win_pdf = _wsl_to_win(pdf)
        win_out = _wsl_to_win(mineru_out)
        args = ["-p", win_pdf, "-o", win_out, "-b", backend, "-m", method]
    t0 = time.time()
    try:
        if _WSL:
            if lang:
                args += ["-l", lang]
            if pages:
                try:
                    if "-" in pages:
                        s, e = pages.split("-", 1)
                        args += ["-s", str(int(s)), "-e", str(int(e))]
                    else:
                        args += ["-s", str(int(pages)), "-e", str(int(pages))]
                except ValueError:
                    pass
            if backend == "hybrid-engine":
                args += ["--effort", "high"]
            r = _run_ps(_WSL_MINERU_PY, "mineru.cli.client", args,
                        timeout=1800, job_name=f"mineru:{pdf.name}")
        else:
            cmd = [str(MINERU_BIN), "-p", str(pdf), "-o", str(mineru_out),
                   "-b", backend, "-m", method]
            if lang:
                cmd += ["-l", lang]
            if pages:
                try:
                    if "-" in pages:
                        s, e = pages.split("-", 1)
                        cmd += ["-s", str(int(s)), "-e", str(int(e))]
                    else:
                        cmd += ["-s", str(int(pages)), "-e", str(int(pages))]
                except ValueError:
                    pass
            if backend == "hybrid-engine":
                cmd += ["--effort", "high"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        elapsed = time.time() - t0
        if r.returncode != 0:
            etype = _classify_engine_error(r)
            err = r.stderr[-2000:] if r.stderr else "unknown error"
            return EngineResult("mineru", False, elapsed, error=err,
                               error_type=etype.value)
        stem = pdf.stem
        md = mineru_out / stem / method / f"{stem}.md"
        if not md.exists():
            mds = list(mineru_out.rglob("*.md"))
            md = mds[0] if mds else None
        if md is None:
            return EngineResult("mineru", False, elapsed,
                               error_type=ErrorType.NO_OUTPUT.value,
                               error="no md output")
        img_dir = md.parent / "images"
        img_count = 0
        img_breakdown = {}
        if img_dir.exists():
            img_count = sum(1 for _ in img_dir.glob("*.jpg"))
            img_count += sum(1 for _ in img_dir.glob("*.png"))
            content_list_path = md.parent / f"{stem}_content_list.json"
            if content_list_path.exists():
                try:
                    cl = json.loads(content_list_path.read_text(encoding="utf-8"))
                    from collections import Counter
                    types = Counter()
                    for item in cl:
                        if "img_path" in item:
                            types[item.get("type", "unknown")] += 1
                    img_breakdown = dict(types)
                except Exception:
                    pass
        result = EngineResult("mineru", True, elapsed, str(md), img_count)
        result.img_breakdown = img_breakdown
        return result
    except subprocess.TimeoutExpired:
        return EngineResult("mineru", False, time.time() - t0,
                           error_type=ErrorType.TIMEOUT.value,
                           error="timeout 30min")
    except Exception as e:
        return EngineResult("mineru", False, time.time() - t0,
                           error_type=ErrorType.ENGINE_CRASH.value,
                           error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#   Worker  ProcessPoolExecutor
# ═══════════════════════════════════════════════════════════════════════════════

def _marker_worker(pdf_str: str, out_str: str, pages: str | None) -> EngineResult:
    return run_marker(Path(pdf_str), Path(out_str), pages)


def _mineru_worker(pdf_str: str, out_str: str, pages: str | None,
                   method: str, backend: str, lang: str | None) -> EngineResult:
    return run_mineru(Path(pdf_str), Path(out_str), pages, method, backend, lang)


# ═══════════════════════════════════════════════════════════════════════════════
#   Diff + Merge    APIdiff & merge logic (preserved from v1; nearly unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

_HEADING_RE = re.compile(r"^#{1,6}\s")
_LATEX_BLOCK_RE = re.compile(r"^\s*\$\$", re.MULTILINE)
_SUPERSCRIPT_RE = re.compile(r"<sup>([^<]*)</sup>")
_LATEX_INLINE_RE = re.compile(r"\$([^$]+)\$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LIST_BULLET_RE = re.compile(r"^[•·\-\*]\s+")
_HTML_TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)
_MD_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
_REF_ITEM_RE = re.compile(r"^\[\d+\]\s")

_META_LINE_RE = re.compile(
    r"^(\^[\*∗]\^\s*)?(Email addresses|Corresponding author|"
    r"https?://|doi:|DOI:|ORCID|Received|Accepted|Published)",
    re.IGNORECASE,
)

_OCR_FIXES = {
    "ofspring": "offspring", "diferent": "different", "eficiency": "efficiency",
    "efective": "effective", "efectively": "effectively", "efectiveness": "effectiveness",
    "ofers": "offers", "ofered": "offered", "ofset": "offset", "ofen": "often",
    "afect": "affect", "afected": "affected", "aford": "afford",
    "eiciency": "efficiency", "fective": "ffective",
}

_LATEX_SPACED_LETTERS_RE = re.compile(
    r"\\mathrm\s*\{\s*((?:[A-Za-z]\s+){2,}[A-Za-z]?)\s*\}"
)


def _normalize_for_diff(text: str) -> list[str]:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')

    lines = text.splitlines()
    n = len(lines)
    normalized_paragraphs: list[str] = []
    current_para: list[str] = []
    i = 0

    while i < n:
        stripped = lines[i].strip()

        if not stripped:
            if current_para:
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            i += 1
            continue

        if _HEADING_RE.match(stripped):
            if current_para:
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            content = re.sub(r"^#{1,6}\s+", "## ", stripped)
            normalized_paragraphs.append(content)
            i += 1
            continue

        if stripped.startswith("$$"):
            if current_para:
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            block_lines = [stripped]
            j = i + 1
            if not (stripped.endswith("$$") and len(stripped) > 2):
                while j < n:
                    next_stripped = lines[j].strip()
                    if not next_stripped:
                        break
                    block_lines.append(next_stripped)
                    if next_stripped == "$$" or next_stripped.endswith("$$"):
                        j += 1
                        break
                    j += 1
            normalized_paragraphs.append(" ".join(block_lines))
            i = j + 1 if not (stripped.endswith("$$") and len(stripped) > 2) else i + 1
            continue

        if _IMAGE_RE.match(stripped):
            if current_para:
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            normalized_paragraphs.append("[IMAGE]")
            i += 1
            continue

        if _MD_TABLE_ROW_RE.match(stripped):
            if current_para and not _MD_TABLE_ROW_RE.match(current_para[-1] if current_para else ""):
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            current_para.append(stripped)
            i += 1
            continue

        line_norm = _SUPERSCRIPT_RE.sub(r"^\1^", stripped)
        line_norm = _LATEX_INLINE_RE.sub(r"\1", line_norm)
        line_norm = _IMAGE_RE.sub("[IMAGE]", line_norm)
        line_norm = _LIST_BULLET_RE.sub("", line_norm)
        line_norm = _HTML_TABLE_RE.sub("[TABLE]", line_norm)
        line_norm = re.sub(r"\s+", " ", line_norm).strip()

        if _META_LINE_RE.match(line_norm):
            if current_para:
                normalized_paragraphs.append(" ".join(current_para))
                current_para = []
            i += 1
            continue

        current_para.append(line_norm)
        i += 1

    if current_para:
        normalized_paragraphs.append(" ".join(current_para))

    merged: list[str] = []
    for p in normalized_paragraphs:
        if not p:
            continue
        if merged:
            prev = merged[-1]
            prev_ends_lower = prev[-1:].islower() or prev.endswith((",", ";", ":", "-"))
            curr_starts_lower = p[:1].islower() or p.startswith(
                ("and ", "the ", "but ", "which ", "where ", "with ", "for ", "in "))
            if (prev_ends_lower and curr_starts_lower
                    and not _HEADING_RE.match(p) and not p.startswith("$$")):
                merged[-1] = prev + " " + p
                continue
        merged.append(p)

    return merged


def make_diff(marker_md: Path | None, mineru_md: Path | None,
              out_path: Path, similarity_threshold: float = 0.85) -> tuple[int, int]:
    if not marker_md or not marker_md.exists():
        out_path.write_text("# Diff Skipped\n\nMarker output missing.\n", encoding="utf-8")
        return 0, 0
    if not mineru_md or not mineru_md.exists():
        out_path.write_text("# Diff Skipped\n\nMinerU output missing.\n", encoding="utf-8")
        return 0, 0

    m_paras = _normalize_for_diff(
        marker_md.read_text(encoding="utf-8", errors="replace"))
    u_paras = _normalize_for_diff(
        mineru_md.read_text(encoding="utf-8", errors="replace"))

    sm = difflib.SequenceMatcher(a=m_paras, b=u_paras, autojunk=False)

    diff_lines: list[str] = [
        "#         +   \n",
        f"- Marker: {len(m_paras)} ",
        f"- MinerU: {len(u_paras)} ",
        f"- similarity: {similarity_threshold}",
        "",
        " ：",
        "  [SAME]          >=   （      ）",
        "  [DIFF]          <   （    ，    ）",
        "  [ONLY-M]   Marker    ",
        "  [ONLY-U]   MinerU    ",
        "",
    ]

    real_diff_count = 0
    char_diff_count = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue

        m_block = m_paras[i1:i2] if i1 < i2 else []
        u_block = u_paras[j1:j2] if j1 < j2 else []

        if not m_block and u_block:
            for para in u_block:
                diff_lines.append(f"## [ONLY-U] {j1}-{j2}")
                preview = para[:500] + ("..." if len(para) > 500 else "")
                diff_lines.append(f"+ {preview}")
                diff_lines.append("")
                real_diff_count += 1
            continue

        if m_block and not u_block:
            for para in m_block:
                diff_lines.append(f"## [ONLY-M] {i1}-{i2}")
                preview = para[:500] + ("..." if len(para) > 500 else "")
                diff_lines.append(f"- {preview}")
                diff_lines.append("")
                real_diff_count += 1
            continue

        used_u: set[int] = set()
        for m_idx, m_p in enumerate(m_block):
            best_u_idx = -1
            best_ratio = 0.0
            for u_idx, u_p in enumerate(u_block):
                if u_idx in used_u:
                    continue
                ratio = difflib.SequenceMatcher(None, m_p, u_p).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_u_idx = u_idx

            if best_u_idx < 0:
                diff_lines.append(f"## [ONLY-M] {i1+m_idx}")
                diff_lines.append(f"- {m_p[:500]}")
                diff_lines.append("")
                real_diff_count += 1
                continue

            used_u.add(best_u_idx)
            u_p = u_block[best_u_idx]

            if best_ratio >= similarity_threshold:
                char_diffs = _extract_char_diffs(m_p, u_p)
                if char_diffs:
                    diff_lines.append(
                        f"## [SAME] {i1+m_idx}/{j1+best_u_idx} (  {best_ratio:.2f})"
                    )
                    for cd in char_diffs[:5]:
                        diff_lines.append(f"  Marker: ...{cd['m']}...")
                        diff_lines.append(f"  MinerU: ...{cd['u']}...")
                    char_diff_count += 1
                    diff_lines.append("")
            else:
                diff_lines.append(
                    f"## [DIFF] {i1+m_idx}/{j1+best_u_idx} (  {best_ratio:.2f})"
                )
                diff_lines.append(f"- {m_p[:500]}")
                diff_lines.append(f"+ {u_p[:500]}")
                diff_lines.append("")
                real_diff_count += 1

        for u_idx, u_p in enumerate(u_block):
            if u_idx not in used_u:
                diff_lines.append(f"## [ONLY-U] {j1+u_idx}")
                diff_lines.append(f"+ {u_p[:500]}")
                diff_lines.append("")
                real_diff_count += 1

    diff_lines.append("---")
    diff_lines.append(f"        [DIFF/ONLY-*]: {real_diff_count}")
    diff_lines.append(f"        [SAME]: {char_diff_count}")
    diff_lines.append(f"  : {max(len(m_paras), len(u_paras))}")

    out_path.write_text("\n".join(diff_lines), encoding="utf-8")
    return real_diff_count, max(len(m_paras), len(u_paras))


def _extract_char_diffs(s1: str, s2: str, context: int = 20) -> list[dict]:
    sm = difflib.SequenceMatcher(None, s1, s2)
    diffs: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        m_ctx_start = max(0, i1 - context)
        m_ctx_end = min(len(s1), i2 + context)
        u_ctx_start = max(0, j1 - context)
        u_ctx_end = min(len(s2), j2 + context)
        m_frag = s1[m_ctx_start:m_ctx_end]
        u_frag = s2[u_ctx_start:u_ctx_end]
        if m_frag != u_frag:
            diffs.append({"m": m_frag, "u": u_frag})
    return diffs


def _fix_ocr_errors(text: str) -> str:
    for wrong, right in _OCR_FIXES.items():
        text = re.sub(r"\b" + re.escape(wrong) + r"\b", right, text)
    return text


def _fix_latex_spacing(latex: str) -> str:
    def fix_mathrm(m):
        inner = m.group(1)
        if " " in inner and len(inner.replace(" ", "")) >= 2:
            compact = inner.replace(" ", "")
            if compact.isalpha():
                return f"\\mathrm{{{compact}}}"
        return m.group(0)

    latex = _LATEX_SPACED_LETTERS_RE.sub(fix_mathrm, latex)
    latex = re.sub(r"\\mathcal\s*\{\s*([^}]+?)\s*\}", r"\\mathcal{\1}", latex)
    latex = re.sub(r"\\mathbf\s*\{\s*([^}]+?)\s*\}", r"\\mathbf{\1}", latex)
    latex = re.sub(r"\\operatorname\*?\s*\{\s*([^}]+?)\s*\}", r"\\operatorname{\1}", latex)
    latex = re.sub(r"\s*\\\\\s*", r" \\\\ ", latex)
    return latex


def _fix_html_tables(md: str) -> str:
    def html_to_md_table(match):
        html = match.group(0)
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        if not rows:
            return html
        md_rows = []
        for i, row in enumerate(rows):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
            cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
            if not cells:
                continue
            md_rows.append("| " + " | ".join(cells) + " |")
            if i == 0:
                md_rows.append("|" + "---|" * len(cells))
        return "\n".join(md_rows) if md_rows else html

    return re.sub(r"<table>.*?</table>", html_to_md_table, md, flags=re.DOTALL)


def _extract_metadata(md_text: str, stem: str) -> dict:
    lines = md_text.splitlines()
    title = ""
    authors = ""
    abstract = ""
    sections: list[dict] = []
    in_abstract = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped == "---":
            continue
        if not title:
            m = re.match(r"^#{1,3}\s+(.+)$", stripped)
            if m and not re.match(r"^(Abstract|Keywords|Introduction)", m.group(1), re.IGNORECASE):
                content = m.group(1)
                if len(content) > 10 and not content.startswith("$"):
                    title = content
                    continue
            elif not title and i < 3 and not stripped.startswith("#") and len(stripped) > 15:
                if not re.match(r"^(Abstract|Keywords|\\\[)", stripped, re.IGNORECASE):
                    title = stripped
                    continue
        if not authors and title and stripped != title and not stripped.startswith("#"):
            if i < 8 and any(c.isalpha() for c in stripped):
                if "@" in stripped or "Universit" in stripped or any(name in stripped for name in [",", " and "]):
                    if not stripped.startswith("Abstract") and not stripped.startswith("Keywords"):
                        authors = stripped
                        continue
        if re.match(r"^#{1,6}\s*Abstract", stripped, re.IGNORECASE) or (
            stripped.lower() == "abstract" and not abstract
        ):
            in_abstract = True
            continue
        if in_abstract:
            if re.match(r"^#{1,6}\s", stripped) or re.match(
                r"^(Keywords|1\s+Introduction)", stripped, re.IGNORECASE
            ):
                in_abstract = False
            elif stripped and not stripped.startswith("<!--") and not stripped.startswith("---"):
                abstract = (abstract + " " + stripped).strip()
        m = re.match(r"^(#{1,6})\s+(\d+(?:\.\d+)*\.?)\s+(.+)$", stripped)
        if m:
            sections.append({
                "level": len(m.group(1)),
                "num": m.group(2).rstrip("."),
                "title": m.group(3).lstrip("| -").strip(),
            })

    return {
        "title": title[:300],
        "authors": authors[:300],
        "abstract": abstract[:1500],
        "sections": sections[:30],
        "section_count": len(sections),
        "total_lines": len(lines),
    }


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_TABLE_OPEN_RE = re.compile(r"<table\b", re.IGNORECASE)


@dataclass
class _Block:
    kind: str
    raw: str
    norm: str


def _norm_line(s: str) -> str:
    s = _SUPERSCRIPT_RE.sub(r"^\1^", s)
    s = _LATEX_INLINE_RE.sub(r"\1", s)
    s = _IMAGE_RE.sub("[IMAGE]", s)
    s = _LIST_BULLET_RE.sub("", s)
    s = _HTML_TABLE_RE.sub("[TABLE]", s)
    return re.sub(r"\s+", " ", s).strip()


def _heading_norm(s: str) -> str:
    return "## " + re.sub(r"^#{1,6}\s+", "", s).strip()


def _formula_norm(raw: str) -> str:
    return "$$ " + re.sub(r"\s+", " ", raw).strip() + " $$"


def _table_norm(raw: str) -> str:
    cells: list[str] = []
    for ln in raw.splitlines():
        cells.extend(c.strip() for c in ln.strip().strip("|").split("|"))
    return "[TABLE] " + " ".join(c for c in cells if c)


def _image_norm(s: str) -> str:
    m = re.match(r"!\[([^\]]*)\]", s)
    alt = m.group(1) if m else ""
    return "[IMAGE] " + re.sub(r"\s+", " ", alt).strip()


def _text_norm(raw: str) -> str:
    return _norm_line(re.sub(r"\s+", " ", raw))


def _split_blocks(text: str) -> list[_Block]:
    """Split raw markdown into structural blocks, keeping each block's verbatim
    text so the merge can emit it without flattening lists or multi-line formulas."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    lines = text.splitlines()
    n = len(lines)
    blocks: list[_Block] = []
    i = 0
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("<!--"):
            raw_lines = []
            while i < n:
                raw_lines.append(lines[i])
                if "-->" in lines[i]:
                    i += 1
                    break
                i += 1
            blocks.append(_Block("comment", "\n".join(raw_lines), ""))
            continue
        if _HEADING_RE.match(s):
            blocks.append(_Block("heading", line, _heading_norm(s)))
            i += 1
            continue
        if s.startswith("$$"):
            raw_lines = [line]
            if not (s.endswith("$$") and len(s) > 2):
                j = i + 1
                while j < n:
                    raw_lines.append(lines[j])
                    nxt = lines[j].strip()
                    if nxt == "$$" or nxt.endswith("$$"):
                        j += 1
                        break
                    j += 1
                i = j
            else:
                i += 1
            raw = "\n".join(raw_lines)
            blocks.append(_Block("formula", raw, _formula_norm(raw)))
            continue
        if _MD_TABLE_ROW_RE.match(s):
            raw_lines = []
            while i < n and _MD_TABLE_ROW_RE.match(lines[i].strip()):
                raw_lines.append(lines[i])
                i += 1
            raw = "\n".join(raw_lines)
            blocks.append(_Block("table", raw, _table_norm(raw)))
            continue
        if _HTML_TABLE_OPEN_RE.search(s):
            raw_lines = []
            while i < n:
                raw_lines.append(lines[i])
                if "</table>" in lines[i].lower():
                    i += 1
                    break
                i += 1
            raw = "\n".join(raw_lines)
            blocks.append(_Block("table", raw, _table_norm(raw)))
            continue
        if _IMAGE_RE.fullmatch(s):
            blocks.append(_Block("image", line, _image_norm(s)))
            i += 1
            continue
        if _META_LINE_RE.match(_norm_line(s)):
            blocks.append(_Block("meta", line, ""))
            i += 1
            continue
        raw_lines = []
        while i < n:
            cur = lines[i].strip()
            if not cur:
                break
            if (_HEADING_RE.match(cur) or cur.startswith("$$")
                    or _MD_TABLE_ROW_RE.match(cur) or _HTML_TABLE_OPEN_RE.search(cur)
                    or _IMAGE_RE.fullmatch(cur) or cur.startswith("<!--")
                    or _META_LINE_RE.match(_norm_line(cur))):
                break
            raw_lines.append(lines[i])
            i += 1
        raw = "\n".join(raw_lines)
        blocks.append(_Block("text", raw, _text_norm(raw)))
    return blocks


def _content_digits(text: str) -> int:
    return sum(1 for c in _HTML_TAG_RE.sub("", text) if c.isdigit())


def _info_score(text: str) -> tuple[int, int, int, int]:
    """Lexicographic info score: digits > formula markers > non-ASCII > length.

    Recomputed from the raw text so a conflict's preferred variant is reproducible."""
    digits = _content_digits(text)
    formulas = text.count("$$") // 2 + len(_LATEX_INLINE_RE.findall(text))
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (digits, formulas, non_ascii, len(text))


def _info_class_counts(text: str) -> dict[str, int]:
    """Six info classes used by the lossless-merge regression check.

    images counts every ![](...) marker; digits counts digit characters in
    content blocks (text/heading/formula/table), not image paths or boilerplate."""
    blocks = _split_blocks(text)
    counts = {
        "headings": 0, "paragraphs": 0, "tables": 0,
        "display_formulas": 0, "images": 0, "digits": 0,
    }
    for b in blocks:
        if b.kind == "heading":
            counts["headings"] += 1
        elif b.kind == "table":
            counts["tables"] += 1
        elif b.kind == "formula":
            counts["display_formulas"] += 1
        elif b.kind == "text":
            counts["paragraphs"] += 1
        if b.kind in ("text", "heading", "formula", "table"):
            counts["digits"] += _content_digits(b.raw)
    counts["images"] = len(_IMAGE_RE.findall(text))
    return counts


def _merge_bodies(m_raw: str, u_raw: str) -> tuple[str, dict]:
    """Lossless merge of two raw markdown bodies; returns (body_text, report).

    Every content block appears at least once: equal/near-equal blocks collapse to
    the higher-info variant (tie -> MinerU), conflict blocks (0.3 <= ratio < 0.85)
    keep BOTH variants tagged by source, and only meta boilerplate is dropped (and
    counted in the report)."""
    m_all = _split_blocks(m_raw)
    u_all = _split_blocks(u_raw)
    m_blocks = [b for b in m_all if b.kind != "meta"]
    u_blocks = [b for b in u_all if b.kind != "meta"]
    m_norms = [b.norm for b in m_blocks]
    u_norms = [b.norm for b in u_blocks]

    sm = difflib.SequenceMatcher(a=m_norms, b=u_norms, autojunk=False)

    parts: list[str] = []
    report = {
        "dropped_meta": {"marker": 0, "mineru": 0},
        "supplement_count": 0,
        "kept_both": 0,
        "conflicts": [],
    }

    def emit(b: _Block) -> None:
        parts.append(b.raw)

    def emit_conflict(mb: _Block, ub: _Block, ratio: float) -> None:
        ms = _info_score(mb.raw)
        us = _info_score(ub.raw)
        preferred = "mineru" if us >= ms else "marker"
        parts.append(
            f"<!-- MERGE-CONFLICT ratio={ratio:.2f} preferred={preferred} "
            f"score_marker={ms[0]}/{ms[1]}/{ms[2]}/{ms[3]} "
            f"score_mineru={us[0]}/{us[1]}/{us[2]}/{us[3]} -->"
        )
        parts.append("**Marker**")
        parts.append("")
        parts.append(mb.raw)
        parts.append("")
        parts.append("**MinerU**")
        parts.append("")
        parts.append(ub.raw)
        report["kept_both"] += 1
        report["conflicts"].append({
            "ratio": round(ratio, 4),
            "preferred": preferred,
            "score_marker": list(ms),
            "score_mineru": list(us),
            "marker_norm": mb.norm[:120],
            "mineru_norm": ub.norm[:120],
        })

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for idx in range(j1, j2):
                emit(u_blocks[idx])
            continue
        m_blk = m_blocks[i1:i2]
        u_blk = u_blocks[j1:j2]
        if not m_blk:
            for b in u_blk:
                emit(b)
            continue
        if not u_blk:
            for b in m_blk:
                emit(b)
            report["supplement_count"] += len(m_blk)
            continue
        local_used: set[int] = set()
        for mb in m_blk:
            best = -1
            best_ratio = 0.0
            for ui, ub in enumerate(u_blk):
                if ui in local_used:
                    continue
                ratio = difflib.SequenceMatcher(None, mb.norm, ub.norm).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = ui
            if best < 0:
                emit(mb)
                report["supplement_count"] += 1
                continue
            local_used.add(best)
            ub = u_blk[best]
            if best_ratio >= 0.85:
                ms = _info_score(mb.raw)
                us = _info_score(ub.raw)
                emit(mb if ms > us else ub)
            elif best_ratio < 0.3:
                emit(mb)
                emit(ub)
            else:
                emit_conflict(mb, ub, best_ratio)
        for ui, ub in enumerate(u_blk):
            if ui not in local_used:
                emit(ub)

    report["dropped_meta"]["marker"] = sum(1 for b in m_all if b.kind == "meta")
    report["dropped_meta"]["mineru"] = sum(1 for b in u_all if b.kind == "meta")

    body = "\n\n".join(parts)
    body = _fix_html_tables(body)
    body = _fix_ocr_errors(body)
    body = _fix_latex_spacing(body)
    report["body_segments"] = len(parts)
    return body, report


def _render_merge_report(report: dict) -> str:
    lines = ["## Merge Report", ""]
    dm = report["dropped_meta"]
    lines.append(
        f"- Dropped meta lines (reason: front-matter boilerplate "
        f"DOI/URL/email/Received/Accepted): marker {dm['marker']}, "
        f"mineru {dm['mineru']}"
    )
    lines.append(f"- Marker-only blocks added: {report['supplement_count']}")
    lines.append(f"- Kept-both conflicts (source-tagged in body): {report['kept_both']}")
    if report["conflicts"]:
        lines.append("")
        lines.append("Conflict decisions (preferred = higher info score, "
                     "digits > formulas > non-ASCII > length):")
        for c in report["conflicts"][:50]:
            lines.append(
                f"- ratio={c['ratio']:.2f} preferred={c['preferred']} "
                f"marker=({','.join(map(str, c['score_marker']))}) "
                f"mineru=({','.join(map(str, c['score_mineru']))})"
            )
        if len(report["conflicts"]) > 50:
            lines.append(f"- ... and {len(report['conflicts']) - 50} more")
    return "\n".join(lines)


def merge_md(marker_md: Path | None, mineru_md: Path | None,
             out_path: Path) -> tuple[int, int]:
    if not mineru_md or not mineru_md.exists():
        if marker_md and marker_md.exists():
            shutil.copy(marker_md, out_path)
            return 0, 0
        out_path.write_text("# Merge Skipped\n\nBoth outputs missing.\n", encoding="utf-8")
        return 0, 0
    if not marker_md or not marker_md.exists():
        shutil.copy(mineru_md, out_path)
        return 0, 0

    m_raw = marker_md.read_text(encoding="utf-8", errors="replace")
    u_raw = mineru_md.read_text(encoding="utf-8", errors="replace")

    body_text, report = _merge_bodies(m_raw, u_raw)

    stem = (mineru_md or marker_md).stem
    meta = _extract_metadata(body_text, stem)

    title = meta["title"] or stem
    authors = meta["authors"] or ""
    abstract = meta["abstract"] or ""

    header_lines = [
        "---",
        "title: |",
        f"  {title}",
        "authors: |",
        f"  {authors}",
        f'source_pdf: "{stem}"',
        "engines: MinerU + Marker",
        f'sections: {meta["section_count"]}',
        f'total_lines: {meta["total_lines"]}',
        "---",
        "",
        f"# {title}",
        "",
    ]
    if authors:
        header_lines.append(f"**Authors:** {authors}")
        header_lines.append("")
    if abstract:
        header_lines.append("## Abstract")
        header_lines.append("")
        header_lines.append(abstract[:800] + ("..." if len(abstract) > 800 else ""))
        header_lines.append("")
    if meta["sections"]:
        header_lines.append("## Table of Contents")
        header_lines.append("")
        for s in meta["sections"][:20]:
            indent = "  " * (s["level"] - 1)
            header_lines.append(f"{indent}- {s['num']} {s['title']}")
        header_lines.append("")
    header_lines.append("---")
    header_lines.append("")

    final_text = "\n".join(header_lines) + "\n" + body_text

    image_list: list[str] = []
    for img_pattern, source in [(m_raw, "Marker"), (u_raw, "MinerU")]:
        for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", img_pattern):
            alt, path = match.group(1), match.group(2)
            image_list.append(f"- {source}: {path}" + (f" (alt: {alt})" if alt else ""))

    if image_list:
        seen = set()
        unique_images = []
        for img in image_list:
            path_part = img.split(": ")[1] if ": " in img else img
            if path_part not in seen:
                seen.add(path_part)
                unique_images.append(img)
        final_text += "\n\n---\n\n## Images Index\n\n"
        final_text += f"  {len(unique_images)} images (Marker + MinerU)\n\n"
        for img in unique_images:
            final_text += img + "\n"

    final_text += "\n\n---\n\n" + _render_merge_report(report) + "\n"

    out_path.write_text(final_text, encoding="utf-8")
    return report["body_segments"], report["supplement_count"]


# ═══════════════════════════════════════════════════════════════════════════════
#      Figure
# ═══════════════════════════════════════════════════════════════════════════════

def _get_mineru_content_list(mineru_md_path: Path) -> list[dict]:
    """ MinerU  content_list.json ."""
    cl_path = mineru_md_path.parent / f"{mineru_md_path.stem}_content_list.json"
    if not cl_path.exists():
        return []
    try:
        return json.loads(cl_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _get_mineru_figure_images(mineru_md_path: Path) -> set[str]:
    """ MinerU  Figure (type='image')  ."""
    content_list = _get_mineru_content_list(mineru_md_path)
    figures = set()
    for item in content_list:
        if item.get("type") == "image" and "img_path" in item:
            img_path = item["img_path"]
            #   images/xxx.jpg
            if not img_path.startswith("images/"):
                figures.add(f"images/{Path(img_path).name}")
            else:
                figures.add(img_path)
    return figures


def _get_marker_images(marker_md_path: Path) -> set[str]:
    """ Marker   ."""
    # Marker        md
    md_dir = marker_md_path.parent
    images = set()
    for ext in ("*.jpeg", "*.jpg", "*.png"):
        for img in md_dir.glob(ext):
            images.add(img.name)
    return images


def copy_figure_images(
    stem: str,
    marker_md: Path | None,
    mineru_md: Path | None,
    merged_dir: Path,
) -> int:
    """     Figure     merged/<stem>/images/.

    MinerU:  content_list.json  type=='image'
    Marker:      ( , ~14 )
    """
    if not marker_md and not mineru_md:
        return 0

    images_dir = merged_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    # MinerU  Figure
    if mineru_md and mineru_md.exists():
        figure_names = _get_mineru_figure_images(mineru_md)
        mineru_img_dir = mineru_md.parent / "images"
        if mineru_img_dir.exists():
            for fname in figure_names:
                src = mineru_img_dir / Path(fname).name
                if src.exists():
                    dst = images_dir / src.name
                    if not dst.exists():
                        shutil.copy2(src, dst)
                        copied += 1

    # Marker
    if marker_md and marker_md.exists():
        marker_img_dir = marker_md.parent
        marker_names = _get_marker_images(marker_md)
        for fname in marker_names:
            src = marker_img_dir / fname
            if src.exists():
                dst = images_dir / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)
                    copied += 1

    return copied


# ═══════════════════════════════════════════════════════════════════════════════
#     Figure  —     PDF (issue #16)
# ═══════════════════════════════════════════════════════════════════════════════

#: MinerU's content_list bbox is normalized to a 0-1000 page box, NOT PDF points:
#: scoring both hypotheses against the PDF's own image placements gives the
#: normalized one ~4x the IoU on 22 of the 23 corpus papers that have any embedded
#: bitmap at all (measured 2026-09-15).  points = bbox / 1000 * page size.
_BBOX_SCALE: Final = 1000.0
#: Below this an XObject is a mask/noise tile rather than a figure.
_MIN_EMBEDDED_PX: Final = 8
#: An embedded bitmap is accepted as THE figure only when it covers this share of
#: the block region: a partial overlap would crop the figure or pull in a neighbour.
_EMBED_OVERLAP_MIN: Final = 0.5
_FIGURE_DPI_DEFAULT: Final = 600


def _rect_coverage(block: tuple[float, float, float, float],
                   cand: tuple[float, float, float, float]) -> float:
    """Share of 'block' covered by 'cand' (asymmetric on purpose)."""
    ix0, iy0 = max(block[0], cand[0]), max(block[1], cand[1])
    ix1, iy1 = min(block[2], cand[2]), min(block[3], cand[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    area = (block[2] - block[0]) * (block[3] - block[1])
    return ((ix1 - ix0) * (iy1 - iy0) / area) if area else 0.0


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    return (w, h) if w and h else None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h) if w and h else None
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def _image_size(data: bytes) -> tuple[int, int] | None:
    return _png_size(data) or _jpeg_size(data)


def _embedded_bytes(stream, attrs: dict) -> tuple[bytes, str] | None:
    """Original bytes of an embedded XObject, or None when it cannot be read losslessly.

    DCTDecode is the interesting case: the raw stream IS the original JPEG, so the
    figure is taken without ever re-encoding it.  Other filters are decoded and
    re-encoded as PNG (lossless container, no pixel resampling).
    """
    from pdfminer.pdftypes import resolve1  # noqa: PLC0415 - optional dependency
    filt = str(resolve1(attrs.get("Filter")) or "").upper()
    raw = getattr(stream, "rawdata", b"") or b""
    if "DCT" in filt and _jpeg_size(raw):
        return raw, "jpg"
    try:
        import io  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        width = int(resolve1(attrs.get("Width")) or 0)
        height = int(resolve1(attrs.get("Height")) or 0)
        if width <= _MIN_EMBEDDED_PX or height <= _MIN_EMBEDDED_PX:
            return None
        colorspace = str(resolve1(attrs.get("ColorSpace")) or "").upper()
        mode = "RGB" if "RGB" in colorspace else ("L" if "G" in colorspace else "RGB")
        image = Image.frombytes(mode, (width, height), stream.get_data())
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), "png"
    except Exception:  # noqa: BLE001 - an undecodable XObject falls back to rendering
        return None


def _page_embedded_figures(pdf_path: Path) -> dict[int, list[dict]]:
    """{page_index: [{"rect", "data", "ext", "size"}]} for the real embedded bitmaps."""
    import pdfplumber  # noqa: PLC0415 - optional dependency (skills venv)

    out: dict[int, list[dict]] = {}
    with pdfplumber.open(pdf_path) as doc:
        for index, page in enumerate(doc.pages):
            found = []
            for item in page.images:
                src = item.get("srcsize") or (0, 0)
                if src[0] <= _MIN_EMBEDDED_PX or src[1] <= _MIN_EMBEDDED_PX:
                    continue
                stream = item.get("stream")
                attrs = getattr(stream, "attrs", {}) or {}
                decoded = _embedded_bytes(stream, attrs)
                if decoded is None:
                    continue
                data, ext = decoded
                size = _image_size(data)
                if size is None:
                    continue
                found.append({"rect": (item["x0"], item["top"], item["x1"], item["bottom"]),
                              "data": data, "ext": ext, "size": size})
            if found:
                out[index] = found
    return out


def extract_pdf_figures(
    pdf_path: Path,
    content_list_path: Path,
    out_dir: Path,
    *,
    dpi: int = _FIGURE_DPI_DEFAULT,
) -> dict:
    """Write print-usable figure copies from the SOURCE pdf (issue #16).

    Combination of the issue's paths (a) and (b): an embedded bitmap that already
    covers the block region is written at its NATIVE resolution (no resampling);
    everything else (vector figures, tiled graphics, undecodable XObjects) is
    rendered from the page at 'dpi' and cropped to the block region.

    Rendering raises the PIXEL count, not the information content: the figure was
    printed at that physical size, so a 600 dpi render only guarantees print
    sampling density.  The manifest and SKILL.md say so, because more pixels is not
    more detail.

    Returns the manifest, which is also written to _FIGURE_SOURCE.json.
    """
    import json as _json  # noqa: PLC0415

    blocks = _json.loads(content_list_path.read_text(encoding="utf-8"))
    figures = [b for b in blocks
               if isinstance(b, dict) and b.get("type") in ("image", "chart") and b.get("bbox")]
    out_dir.mkdir(parents=True, exist_ok=True)
    embedded = _page_embedded_figures(pdf_path)

    import pypdfium2 as pdfium  # noqa: PLC0415 - optional dependency
    pdf = pdfium.PdfDocument(str(pdf_path))
    page_sizes = [tuple(pdf[i].get_size()) for i in range(len(pdf))]
    by_page: dict[int, list[tuple[int, dict]]] = {}
    for order, block in enumerate(figures):
        page_index = int(block.get("page_idx") or 0)
        if page_index < len(page_sizes):
            by_page.setdefault(page_index, []).append((order, block))

    records = []
    # One page bitmap at a time, released before the next page: a 600 dpi letter page
    # is ~134 MB in memory, so caching every figure page of a paper swaps before the
    # paper finishes (measured: the first full-corpus run stalled).
    for page_index in sorted(by_page):
        bitmap = None
        page = None
        for order, block in by_page[page_index]:
            width_pt, height_pt = page_sizes[page_index]
            x0, y0, x1, y1 = (float(v) for v in block["bbox"])
            region = (x0 / _BBOX_SCALE * width_pt, y0 / _BBOX_SCALE * height_pt,
                      x1 / _BBOX_SCALE * width_pt, y1 / _BBOX_SCALE * height_pt)
            candidates = embedded.get(page_index, [])
            best = max(candidates, key=lambda c: _rect_coverage(region, c["rect"]), default=None)
            name = f"fig-{page_index + 1:03d}-{order:03d}-{block.get('type', 'image')}"
            record = {"figure_id": name, "page": page_index + 1, "block_index": order,
                      "region_pt": [round(v, 2) for v in region], "rendered_dpi": None}
            if best is not None and _rect_coverage(region, best["rect"]) >= _EMBED_OVERLAP_MIN:
                target = out_dir / f"{name}-embedded.{best['ext']}"
                target.write_bytes(best["data"])
                record.update({"method": "embedded", "path": target.name,
                               "width": best["size"][0], "height": best["size"][1]})
            else:
                if bitmap is None:
                    page = pdf[page_index]          # keep the page alive for the bitmap
                    bitmap = page.render(scale=dpi / 72.0)
                scale = dpi / 72.0
                box = (max(0, int(region[0] * scale)), max(0, int(region[1] * scale)),
                       min(bitmap.width, int(region[2] * scale)),
                       min(bitmap.height, int(region[3] * scale)))
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                image = bitmap.to_pil().crop(box)
                target = out_dir / f"{name}-render-{dpi}dpi.png"
                image.save(target, format="PNG")
                record.update({"method": "render", "path": target.name,
                               "width": image.width, "height": image.height,
                               "rendered_dpi": dpi})
            records.append(record)
        bitmap = None

    manifest = {
        "schema_version": "1.0",
        "source_pdf": pdf_path.name,
        "figure_source": "pdf",
        "dpi": dpi,
        "n_figures": len(records),
        "n_embedded": sum(1 for r in records if r["method"] == "embedded"),
        "n_rendered": sum(1 for r in records if r["method"] == "render"),
        "note": ("embedded = the PDF's own bitmap at native resolution (no resampling); "
                 "render = page rendered at the requested dpi and cropped - more pixels, "
                 "not more detail than the PDF holds"),
        "figures": sorted(records, key=lambda r: r["figure_id"]),
    }
    (out_dir / "_FIGURE_SOURCE.json").write_text(
        _json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    return manifest


# ═══════════════════════════════════════════════════════════════════════════════
#      — v2   (  /  /  )
# ═══════════════════════════════════════════════════════════════════════════════

def _derive_output_dirs(papers_dir: Path) -> dict[str, Path]:
    """      ."""
    return {
        "conversion": papers_dir / "paper-conversion",
        "merged": papers_dir / "paper-merged",
        "summaries": papers_dir / "paper-summaries",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   _META.json — provenance payload + backfill
# ═══════════════════════════════════════════════════════════════════════════════

def build_meta_record(
    result: PaperResult,
    *,
    engines: str,
    pages: str | None,
    images_copied: int,
    pdf_sha256: str | None,
    pdf_sha256_note: str | None,
    engine_provenance: dict,
    language: dict | None = None,
) -> dict:
    """Assemble the _META.json payload (pure function → unit-testable, no IO).

    Frozen contract consumed by the metrics layer:
      pdf_sha256               full source-PDF SHA-256, or null
      engine_versions          {marker, mineru, torch, cuda, python} — all five keys
      engine_versions_source   "conversion_time" | "current_env_estimate" | "unavailable"
      engine_versions_note     optional explanation (failure / estimate caveat)
      language / cjk_ratio / lang_source
                               language record (issue #10 step), the SAME rule as
                               text_metrics.detect_language; null when there was no
                               canonical text to measure
    """
    prov_versions = engine_provenance.get("engine_versions") or {}
    lang = language or {}
    meta = {
        "pdf_path": result.pdf_path,
        "stem": result.stem,
        "pdf_sha256": pdf_sha256 or None,
        "pdf_sha256_note": pdf_sha256_note,
        "engine_versions": {k: prov_versions.get(k) for k in _ENGINE_CONTRACT_KEYS},
        "engine_versions_source": engine_provenance.get("engine_versions_source"),
        "engine_versions_note": engine_provenance.get("engine_versions_note"),
        # Language triple: always present, null when unmeasured (never 0 / never
        # omitted), so a consumer can tell "not measured" from "measured as 0".
        "language": lang.get("language"),
        "cjk_ratio": lang.get("cjk_ratio"),
        "lang_source": lang.get("lang_source"),
        "engines": engines,
        "pages": pages,
        "marker": asdict(result.marker) if result.marker else None,
        "mineru": asdict(result.mineru) if result.mineru else None,
        "diff_path": result.diff_path,
        "diff_line_count": result.diff_line_count,
        "merged_path": result.merged_path,
        "merged_supplement_count": result.merged_supplement_count,
        "images_copied": images_copied,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if result.precheck:
        meta["precheck"] = result.precheck.to_record()
    return meta


def _write_meta_json(meta_path: Path, meta: dict) -> str | None:
    """Write _META.json; return an error note instead of raising."""
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"


_BACKFILL_HONESTY = (
    "backfilled after conversion (not measured then): engine_versions are "
    "current-environment estimates, not a conversion-time measurement"
)


def _meta_candidates(papers_dir: Path) -> list[Path]:
    """Existing _META.json files to backfill.

    Covers both on-disk layouts: v1 corpus (<papers_dir>/paper-analysis/<paper>/)
    and v2 pipeline output (<papers_dir>/paper-merged/<stem>/), plus the case
    where papers_dir *is* the paper-analysis directory.
    """
    found: list[Path] = []
    for pattern in ("paper-analysis/*/_META.json",
                    "paper-merged/*/_META.json",
                    "*/_META.json"):
        found.extend(sorted(papers_dir.glob(pattern)))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _resolve_source_pdf(
    meta: dict, meta_path: Path, papers_dir: Path,
) -> tuple[Path | None, str | None, str | None]:
    """Locate the source PDF for an existing _META.json → (path, kind, digest).

    Never raises.  kind is "source" (the paper's own file) or "engine_copy" (a copy
    an engine kept, which may differ in bytes from what was submitted).
    Preference order:
      1. meta["pdf_path"] (absolute, or relative to paper dir / papers_dir)
      2. <papers_dir>/papers/<stem>.pdf, <papers_dir>/<stem>.pdf, <paper_dir>/<stem>.pdf
      3. first <stem>.pdf found under papers_dir
      4. engine-side copy (<paper_dir>/mineru/**/*_origin.pdf) — explicitly labelled
    """
    stem = str(meta.get("stem") or meta_path.parent.name)
    candidates: list[Path] = []

    raw = meta.get("pdf_path")
    if isinstance(raw, str) and raw.strip():
        p = Path(raw)
        candidates.append(p)
        if not p.is_absolute():
            candidates.extend([meta_path.parent / p, papers_dir / p,
                               papers_dir / "papers" / p])
    for base in (papers_dir / "papers", papers_dir, meta_path.parent,
                 meta_path.parent.parent):
        candidates.append(base / f"{stem}.pdf")

    def _usable(cand: Path) -> bool:
        try:
            if not cand.is_file():
                return False
        except Exception:
            return False
        digest, _ = _sha256_of_file(cand)
        return bool(digest)

    for cand in candidates:
        if _usable(cand):
            return cand, "source", _sha256_of_file(cand)[0]

    try:
        hits = sorted(p for p in papers_dir.rglob(f"{stem}.pdf") if p.is_file())
    except Exception:
        hits = []
    for cand in hits:
        if _usable(cand):
            return cand, "source", _sha256_of_file(cand)[0]

    for cand in sorted(meta_path.parent.rglob("*_origin.pdf")):
        if _usable(cand):
            return cand, "engine_copy", _sha256_of_file(cand)[0]

    return None, None, None


def _resolve_source_pdf_sha256(
    meta: dict, meta_path: Path, papers_dir: Path,
) -> tuple[str | None, str | None]:
    """Hash the located source PDF → (digest|None, note|None).  Never raises.

    The note states the credibility tier (issue #7): an original source PDF versus
    an engine-side copy that may differ in bytes.
    """
    stem = str(meta.get("stem") or meta_path.parent.name)
    path, kind, digest = _resolve_source_pdf(meta, meta_path, papers_dir)
    if path is None or not digest:
        return None, f"pdf_sha256 unavailable: no source PDF found for stem '{stem}'"
    if kind == "engine_copy":
        return digest, (
            "pdf_sha256 computed from an engine-side copy, not the original "
            f"source PDF (may differ in bytes): {path}"
        )
    return digest, f"pdf_sha256 computed from source PDF: {path}"


def _backfill_one(
    meta: dict, meta_path: Path, papers_dir: Path, snapshot: dict,
    with_versions: bool = True,
) -> tuple[bool, str]:
    """Fill missing provenance fields in-place → (changed, sha_state).

    Only *missing* fields are written; existing measured values are preserved.
    All derived text is deterministic (no timestamps), so a second run is a no-op.
    with_versions=False fills the language triple only, leaving a conversion-time
    provenance record untouched: its versions were measured then, not now.
    """
    changed = False
    existing_note = meta.get("engine_versions_note")
    sha_state = "present" if meta.get("pdf_sha256") else "missing"

    # --- pdf_sha256: fill only when missing; never overwrite an existing digest ---
    if with_versions and not meta.get("pdf_sha256"):
        resolved, sha_note = _resolve_source_pdf_sha256(meta, meta_path, papers_dir)
        sha_state = "filled" if resolved else "null"
        if "pdf_sha256" not in meta or resolved != meta.get("pdf_sha256"):
            meta["pdf_sha256"] = resolved
            changed = True
        if sha_note and meta.get("pdf_sha256_note") != sha_note:
            meta["pdf_sha256_note"] = sha_note
            changed = True
    elif with_versions and "pdf_sha256_note" not in meta:
        meta["pdf_sha256_note"] = None
        changed = True

    # --- engine_versions: keep measured values, fill only the nulls ---
    if with_versions:
        existing_versions = meta.get("engine_versions")
        merged = dict(existing_versions) if isinstance(existing_versions, dict) else {}
        for key in _ENGINE_CONTRACT_KEYS:
            if not merged.get(key):
                value = (snapshot.get("engine_versions") or {}).get(key)
                if value:
                    merged[key] = value
            if key not in merged:
                merged[key] = None
        if merged != existing_versions:
            meta["engine_versions"] = merged
            changed = True

        source = ("current_env_estimate"
                  if any(merged.get(k) for k in _ENGINE_CONTRACT_KEYS) else "unavailable")
        if meta.get("engine_versions_source") != source:
            meta["engine_versions_source"] = source
            changed = True

        # Deterministic note (no timestamps → repeated runs stay byte-identical).
        # The PDF path/derivation detail lives in pdf_sha256_note, not here, so
        # that a record whose digest already exists produces the same note.
        parts = [_BACKFILL_HONESTY]
        if (isinstance(existing_note, str) and existing_note.strip()
                and _BACKFILL_HONESTY not in existing_note):
            parts.append(f"original conversion note: {existing_note}")
        stale = snapshot.get("engine_versions_note")
        if source == "unavailable" and stale:
            parts.append(stale)
        desired_note = "; ".join(parts)
        if meta.get("engine_versions_note") != desired_note:
            meta["engine_versions_note"] = desired_note
            changed = True

    # --- language record (issue #10 step): same rule as detect_language ---------
    # Measured now from the stored canonical markdown, hence the explicit
    # *_backfill source label.  A value that is already known is never downgraded
    # to null, so a second run is a no-op (idempotent by construction).
    # "0.0 is a value, not a hole": a real cjk_ratio of 0.0 (an all-English paper)
    # must not make every backfill re-read and re-parse the markdown.
    if any(key not in meta or meta.get(key) is None for key in _LANG_META_KEYS):
        backfill_text = read_text_or_none(resolve_canonical_md(meta_path.parent))
        record = detect_language_record(
            backfill_text,
            _LANG_SOURCE_BACKFILL if backfill_text is not None else None,
        )
        for key in _LANG_META_KEYS:
            value = record.get(key)
            # A missing key is always added (null when unmeasured: the triple is
            # never omitted), an existing value is only ever upgraded.
            if key not in meta:
                meta[key] = value
                changed = True
            elif meta.get(key) is None and value is not None:
                meta[key] = value
                changed = True

    return changed, sha_state


def backfill_probe(papers_dir: Path, force: bool = False) -> dict:
    """Run stage 1.5 for papers that already converted, without re-converting.

    --resume skips every paper the state file calls finished, so a corpus converted
    before the probe was wired in would never gain the evidence through the normal
    path, and re-converting to obtain it costs minutes per paper.  This walks the
    existing _META.json files instead and writes _textlayer_probe.json next to each
    one, using the same never-raising probe and the same cache rules.
    """
    stats = {"scanned": 0, "probed": 0, "reused": 0, "warn": 0,
             "not_measured": 0, "no_pdf": 0}
    for meta_path in _meta_candidates(papers_dir):
        stats["scanned"] += 1
        paper_dir = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(meta, dict):
            continue
        stem = str(meta.get("stem") or paper_dir.name)

        # Canonical side: the artifact the metrics layer reads, looked up in the
        # conversion tree first (a paper-merged dir has no mineru tree of its own).
        canonical_text, canonical_source = None, None
        # A caller may point straight at <root>/paper-merged, in which case the
        # conversion tree is a sibling, not a child: without that candidate the
        # content_list is never found and the probe silently degrades to the
        # markdown, hiding exactly the damage it exists to measure.
        # The parent candidate exists for callers that point at <root>/paper-merged
        # or <root>/paper-analysis; gating it on those names keeps it from escaping
        # to the parent of a corpus root, where an unrelated corpus could hold a
        # same-stem paper and hand back its evidence.
        candidates = [papers_dir / "paper-conversion" / stem]
        if papers_dir.name in ("paper-merged", "paper-analysis"):
            candidates.append(papers_dir.parent / "paper-conversion" / stem)
        candidates.append(paper_dir)
        for candidate in candidates:
            # A backfill has no "this run": the newest tree is all it can use, and
            # the recorded canonical_source tells a reader which artifact that was.
            canonical_text, canonical_source = resolve_metrics_canonical(
                candidate, allow_tree_scan=True)
            if canonical_text is not None:
                break
        if canonical_text is None:
            canonical_text = read_text_or_none(resolve_canonical_md(paper_dir))
            canonical_source = (_LANG_SOURCE_MERGED if canonical_text is not None
                                else None)

        pdf_path, _kind, _digest = _resolve_source_pdf(meta, meta_path, papers_dir)
        out_path = paper_dir / _TEXTLAYER_PROBE_FILENAME
        if pdf_path is None:
            stats["no_pdf"] += 1
            continue
        hit = (not force) and _probe_cache_hit(out_path, pdf_path,
                                               canonical_source, canonical_text)
        record = run_textlayer_probe(pdf_path, paper_dir, canonical_text,
                                     force=force,
                                     canonical_source=canonical_source)
        verdict = record.get("verdict")
        if verdict == "warn":
            stats["warn"] += 1
        if verdict == "not_applicable":
            stats["not_measured"] += 1
        stats["reused" if hit else "probed"] += 1
    return stats


def backfill_meta(papers_dir: Path) -> dict:
    """Fill provenance fields into existing _META.json files WITHOUT re-converting.

    Idempotent by construction:
      * records already carrying engine_versions_source == "conversion_time" are
        never touched;
      * only missing/null fields are filled;
      * a record whose desired content equals its current content is not rewritten
        (the note text is deterministic — no timestamps).

    Honesty: versions detected now are stamped "current_env_estimate", never
    "conversion_time", because the paper was converted before this backfill.
    """
    metas = _meta_candidates(papers_dir)
    stats = {
        "scanned": len(metas), "updated": 0, "unchanged": 0,
        "skipped_conversion_time": 0, "pdf_sha256_filled": 0,
        "pdf_sha256_null": 0, "unreadable": 0,
    }
    if not metas:
        return stats
    snapshot = _engine_version_snapshot()
    for meta_path in metas:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[backfill-meta] WARN: unreadable {meta_path}: {e}", file=sys.stderr)
            stats["unreadable"] += 1
            continue
        if not isinstance(meta, dict):
            print(f"[backfill-meta] WARN: not a JSON object: {meta_path}", file=sys.stderr)
            stats["unreadable"] += 1
            continue
        # A conversion_time record must keep its measured versions, but the language
        # triple is a different question and used to be lost with it: every record
        # old enough to carry conversion_time also predates the language keys, so
        # skipping the file outright left them permanently absent.
        measured_versions = meta.get("engine_versions_source") == "conversion_time"
        if measured_versions:
            stats["skipped_conversion_time"] += 1

        changed, sha_state = _backfill_one(meta, meta_path, papers_dir, snapshot,
                                           with_versions=not measured_versions)
        if changed:
            error = _write_meta_json(meta_path, meta)
            if error:
                print(f"[backfill-meta] ERROR: cannot write {meta_path}: {error}",
                      file=sys.stderr)
                stats["unreadable"] += 1
                continue
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
        if sha_state == "filled":
            stats["pdf_sha256_filled"] += 1
        if not meta.get("pdf_sha256"):
            stats["pdf_sha256_null"] += 1
    return stats


def process_one(
    pdf: Path,
    papers_dir: Path,
    engines: str,
    pages: str | None,
    method: str,
    backend: str,
    lang: str | None,
    max_workers: int = 2,
    state: PipelineState | None = None,
    force: bool = False,
    textlayer_probe: bool = True,
    figure_source: str = "engine",
    figure_dpi: int = _FIGURE_DPI_DEFAULT,
) -> PaperResult:
    """   PDF  ."""
    stem = pdf.stem
    dirs = _derive_output_dirs(papers_dir)
    conversion_dir = dirs["conversion"] / stem
    merged_dir = dirs["merged"] / stem
    summaries_dir = dirs["summaries"]

    conversion_dir.mkdir(parents=True, exist_ok=True)
    merged_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    result = PaperResult(pdf_path=str(pdf), stem=stem)

    # ── Phase 0:   ────────────────────────────────────────────────
    skip_precheck = state and not force and state.has(stem) and not state.needs_precheck(stem)
    if not skip_precheck:
        max_pages = _DEFAULT_MAX_PAGES
        if state:
            #  TODO:    _pipeline_state.json
            pass
        precheck = run_precheck(pdf)
        result.precheck = precheck
        if state:
            state.set_precheck(stem, precheck)

        if not precheck.ok:
            error_type = ErrorType(precheck.reason) if precheck.reason else ErrorType.PRECHECK_CRASHED
            if state:
                for phase in ("phase1_converted", "phase2_merged", "phase3_summarized"):
                    state.set_phase(stem, phase, {"status": "skipped"})
                state.save()
            return result
    elif state:
        result.precheck = PrecheckResult(
            ok=True, status="passed",
            pdf_hash=state.get(stem).get("pdf_hash", ""),
            pdf_sha256=state.get(stem).get("pdf_sha256", "") or "",
        )

    # ── Phase 1:   ────────────────────────────────────────────────
    if engines == "both":
        workers = max(1, min(max_workers, 2))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {
                "marker": ex.submit(
                    _marker_worker, str(pdf), str(conversion_dir), pages,
                ),
                "mineru": ex.submit(
                    _mineru_worker, str(pdf), str(conversion_dir), pages,
                    method, backend, lang,
                ),
            }
            for key, fut in futs.items():
                try:
                    er = fut.result()
                except Exception as e:
                    er = EngineResult(key, False, 0,
                                     error=str(e),
                                     error_type=ErrorType.ENGINE_CRASH.value)
                setattr(result, key, er)
    elif engines == "marker":
        result.marker = run_marker(pdf, conversion_dir, pages)
    elif engines == "mineru":
        result.mineru = run_mineru(pdf, conversion_dir, pages, method, backend, lang)
    else:
        raise ValueError(f"unknown engines: {engines}")

    #   Phase 1
    marker_ok = result.marker and result.marker.ok
    mineru_ok = result.mineru and result.mineru.ok

    #        : --engines marker  marker  mineru  None
    if engines == "both":
        both_failed = not marker_ok and not mineru_ok
    elif engines == "marker":
        both_failed = not marker_ok
    elif engines == "mineru":
        both_failed = not mineru_ok
    else:
        both_failed = not marker_ok and not mineru_ok

    if both_failed:
        #    → SKIP
        phase1_record = {
            "status": "failed",
            "marker_ok": marker_ok if (engines in ("both", "marker")) else None,
            "mineru_ok": mineru_ok if (engines in ("both", "mineru")) else None,
            "marker_error": (result.marker.error if result.marker and not result.marker.ok else "") if engines in ("both", "marker") else None,
            "mineru_error": (result.mineru.error if result.mineru and not result.mineru.ok else "") if engines in ("both", "mineru") else None,
        }
        phase1_record["error_type"] = (
            ErrorType.BOTH_ENGINES_FAILED.value if engines == "both"
            else (result.marker.error_type if engines == "marker" else result.mineru.error_type)
        )
        if state:
            state.set_phase(stem, "phase1_converted", phase1_record)
            state.set_phase(stem, "phase2_merged", {"status": "skipped"})
            state.set_phase(stem, "phase3_summarized", {"status": "skipped"})
            state.save()
        return result

    phase1_status = "degraded" if not (marker_ok and mineru_ok) else "done"
    # Single-engine runs leave the other EngineResult as None, so every field must
    # be guarded on the result itself (previously "--engines marker" crashed here
    # with AttributeError: 'NoneType' object has no attribute 'error').
    phase1_record = {
        "status": phase1_status,
        "marker_ok": marker_ok if engines in ("both", "marker") else None,
        "mineru_ok": mineru_ok if engines in ("both", "mineru") else None,
        "marker_error": (result.marker.error
                         if result.marker and not result.marker.ok else None),
        "mineru_error": (result.mineru.error
                         if result.mineru and not result.mineru.ok else None),
        "marker_error_type": (result.marker.error_type
                              if result.marker and not result.marker.ok else None),
        "mineru_error_type": (result.mineru.error_type
                              if result.mineru and not result.mineru.ok else None),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if state:
        state.set_phase(stem, "phase1_converted", phase1_record)
    result.phases_done.append("phase1")

    # ── Phase 2:   ────────────────────────────────────────────────
    diff_path = merged_dir / "_DIFF.md"
    merged_path = merged_dir / "_MERGED.md"
    meta_path = merged_dir / "_META.json"

    try:
        marker_md_p = (Path(result.marker.md_path)
                       if result.marker and result.marker.md_path else None)
        mineru_md_p = (Path(result.mineru.md_path)
                       if result.mineru and result.mineru.md_path else None)

        #   (diff + merge)
        if marker_md_p and mineru_md_p:
            diff_count, total = make_diff(marker_md_p, mineru_md_p, diff_path)
            result.diff_path = str(diff_path)
            result.diff_line_count = diff_count

            merged_total, merged_supp = merge_md(
                marker_md_p, mineru_md_p, merged_path,
            )
            result.merged_path = str(merged_path)
            result.merged_supplement_count = merged_supp

        elif marker_md_p:
            #   Marker →
            shutil.copy(marker_md_p, merged_path)
            result.merged_path = str(merged_path)
            diff_path.write_text("# Diff Skipped\n\nMinerU output missing.\n", encoding="utf-8")

        elif mineru_md_p:
            #   MinerU
            shutil.copy(mineru_md_p, merged_path)
            result.merged_path = str(merged_path)
            diff_path.write_text("# Diff Skipped\n\nMarker output missing.\n", encoding="utf-8")

        #   Figure
        try:
            images_copied = copy_figure_images(
                stem, marker_md_p, mineru_md_p, merged_dir,
            )
        except Exception as e:
            #   → DEGRADE, merged
            print(f"  [{stem}] ⚠️    : {e}", file=sys.stderr)
            images_copied = -1

        source_figures = None
        if figure_source == "pdf" and mineru_md_p:
            # Issue #16: keep the engine copies AND add a print-usable set from the
            # source PDF, so the two can be compared instead of one silently
            # replacing the other.
            content_list_p = mineru_md_p.parent / f"{mineru_md_p.stem}_content_list.json"
            if not content_list_p.exists():
                fallback = sorted(mineru_md_p.parent.glob("*_content_list.json"))
                content_list_p = fallback[0] if fallback else content_list_p
            try:
                source_figures = extract_pdf_figures(
                    pdf, content_list_p, merged_dir / "figures", dpi=figure_dpi)
            except Exception as exc:  # noqa: BLE001 - a figure path must never fail the paper
                print(f"  [{stem}] source-PDF figures failed: {exc}", file=sys.stderr)
                source_figures = None

        phase2_status = "degraded" if not (marker_ok and mineru_ok) else "done"
        phase2_record = {
            "status": phase2_status,
            "diff_paragraphs": result.diff_line_count,
            "images_copied": images_copied,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if source_figures is not None:
            phase2_record["source_pdf_figures"] = {
                key: source_figures[key]
                for key in ("n_figures", "n_embedded", "n_rendered", "dpi")}
        if images_copied < 0:
            phase2_record["img_copy_error"] = str(e) if 'e' in dir() else "unknown"

        if state:
            state.set_phase(stem, "phase2_merged", phase2_record)

        result.phases_done.append("phase2")

    except Exception as e:
        #   → SKIP
        print(f"  [{stem}] ❌   : {e}", file=sys.stderr)
        if state:
            state.set_phase_from_error(stem, "phase2_merged", ErrorType.NORMALIZE_CRASH, str(e))
        return result

    # ──    _META.json （   +   ） ──────────────────────
    # Source-PDF hash: reuse the precheck pass when available (no extra full
    # read); otherwise fall back to the pipeline state, then to a streaming read.
    pdf_sha256 = ""
    pdf_sha256_note = None
    if result.precheck and result.precheck.pdf_sha256:
        pdf_sha256 = result.precheck.pdf_sha256
    else:
        cached = state.get(stem).get("pdf_sha256", "") if state else ""
        if cached:
            pdf_sha256 = cached
        else:
            digest, pdf_sha256_note = _sha256_of_file(pdf)
            pdf_sha256 = digest or ""
    if pdf_sha256 and state:
        state.set_pdf_sha256(stem, pdf_sha256)

    # ── Stage 1.5 (side-band): text-layer probe + language record ─────────────
    # Runs after the merge because the merged markdown / content_list is the
    # canonical side of the comparison.  Calls one wrapper that is contractually
    # unable to raise: nothing in this stage may fail a conversion that worked.
    mineru_md = (Path(result.mineru.md_path)
                 if result.mineru and result.mineru.ok and result.mineru.md_path
                 else None)
    lang_record = collect_stage_1_5(pdf, merged_dir, conversion_dir, stem,
                                    force=force, enabled=textlayer_probe,
                                    result=result, mineru_md_path=mineru_md)

    meta = build_meta_record(
        result,
        engines=engines,
        pages=pages,
        images_copied=images_copied if 'images_copied' in dir() else 0,
        pdf_sha256=pdf_sha256,
        pdf_sha256_note=pdf_sha256_note,
        engine_provenance=_engine_version_snapshot(),
        language=lang_record,
    )
    meta_error = _write_meta_json(meta_path, meta)
    if meta_error:
        #   → FATAL?
        print(f"  [{stem}] 💥    _META.json: {meta_error}", file=sys.stderr)

    #   Phase 3
    if state:
        state.set_phase(stem, "phase3_summarized", {"status": "pending"})

    if state:
        state.save()

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════════════════════

def find_pdfs(target: Path) -> list[Path]:
    if target.is_file() and target.suffix.lower() == ".pdf":
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*.pdf") if not p.name.startswith("."))
    return []


def print_summary(results: list[PaperResult]) -> None:
    header = f"{'stem':<40} {'marker':>8} {'mineru':>8} {'diff':>6} {'probe':>10}"
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    labels = {"ok": "ok", "warn": "WARN", "pdf_only": "pdf_only",
              "not_applicable": "n/a", "error": "ERROR"}
    tally: dict[str, int] = {}
    for r in results:
        m_t = (f"{r.marker.elapsed_sec:.0f}s"
               if r.marker and r.marker.ok else
               ("FAIL" if r.marker else "-"))
        u_t = (f"{r.mineru.elapsed_sec:.0f}s"
               if r.mineru and r.mineru.ok else
               ("FAIL" if r.mineru else "-"))
        d = str(r.diff_line_count) if r.diff_path else "-"
        probe = labels.get(r.probe_verdict or "", r.probe_verdict or "-")
        if r.probe_verdict:
            tally[r.probe_verdict] = tally.get(r.probe_verdict, 0) + 1
        print(f"{r.stem:<40} {m_t:>8} {u_t:>8} {d:>6} {probe:>10}")
    print("=" * len(header))
    if tally:
        # "not measured" (n/a, pdf_only) is reported next to the verdicts on
        # purpose: a pass rate that silently folded them into "ok" would be a lie.
        print("text-layer probe: "
              + ", ".join(f"{k}={tally[k]}" for k in sorted(tally)))


def print_status(state: PipelineState) -> None:
    """   ."""
    papers = state.papers_summary()
    if not papers:
        print("  _pipeline_state.json    .", file=sys.stderr)
        print("  paper_reader.py <papers_dir/> --init ", file=sys.stderr)
        return

    #
    COLUMNS = [
        ("STEM", 28),
        ("SOURCE", 8),
        ("PRECHECK", 10),
        ("CONVERTED", 18),
        ("MERGED", 8),
        ("SUMMARIZED", 10),
        ("TIER", 8),
    ]

    header = "".join(name.ljust(w + 1) for name, w in COLUMNS)
    print(header)
    print("-" * len(header))

    stats = {"total": 0, "ok": 0, "degraded": 0, "skipped": 0, "pending": 0}
    skip_reasons: dict[str, int] = {}

    for p in papers:
        stem = p["stem"]
        stats["total"] += 1

        source_db = (p.get("source") or {}).get("source_db", "-")
        tier = (p.get("source") or {}).get("venue", "") or "-"

        precheck = p.get("precheck", {})
        pc_status = precheck.get("status", "pending")
        pc_emoji = _precheck_emoji(pc_status)
        pc_reason = precheck.get("reason", "")
        if pc_status not in ("passed", "pending"):
            pc_emoji += f"{pc_reason[:8]}" if pc_reason else "❌"
            skip_reasons[pc_reason or "unknown"] = skip_reasons.get(pc_reason or "unknown", 0) + 1

        phase1 = p.get("phase1_converted", {})
        phase2 = p.get("phase2_merged", {})
        phase3 = p.get("phase3_summarized", {})

        p1_emoji = _engine_pair_emoji(phase1)
        p2_emoji = _phase_emoji(phase2, "phase2_merged")
        p3_emoji = _phase_emoji(phase3, "phase3_summarized")

        #    precheck passed + phase1 done  = ok
        if pc_status == "passed" and phase1.get("status") in ("done", "degraded"):
            stats["ok" if phase1.get("status") == "done" else "degraded"] += 1
        elif pc_status != "passed" and pc_status != "pending":
            stats["skipped"] += 1
        elif phase1.get("status") == "failed":
            stats["skipped"] += 1
        elif pc_status == "pending":
            stats["pending"] += 1
        else:
            stats["pending"] += 1

        print(
            f"{stem:<{COLUMNS[0][1]+1}}"
            f"{source_db:<{COLUMNS[1][1]+1}}"
            f"{pc_emoji:<{COLUMNS[2][1]+1}}"
            f"{p1_emoji:<{COLUMNS[3][1]+1}}"
            f"{p2_emoji:<{COLUMNS[4][1]+1}}"
            f"{p3_emoji:<{COLUMNS[5][1]+1}}"
            f"{tier[:8]:<{COLUMNS[6][1]+1}}"
        )

    print("-" * len(header))
    summary_parts = [f"  : {stats['total']}"]
    if stats["ok"]:
        summary_parts.append(f"  : {stats['ok']}")
    if stats["degraded"]:
        summary_parts.append(f"  : {stats['degraded']}")
    if stats["skipped"]:
        summary_parts.append(f"  : {stats['skipped']}")
    if stats["pending"]:
        summary_parts.append(f"  : {stats['pending']}")
    print("  ".join(summary_parts))
    if skip_reasons:
        reason_summary = ", ".join(f"{r}={c}" for r, c in skip_reasons.items())
        print(f"  : {reason_summary}")


# ═══════════════════════════════════════════════════════════════════════════════
#   CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Paper Reader v2 —        +   ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf", type=Path,
                    help="PDF         （  PDF       ）")
    ap.add_argument("-e", "--engines", choices=["both", "marker", "mineru"],
                    default="both", help="    （  both）")
    ap.add_argument("-p", "--pages", help="   '0-9'  '5'（0-based）")
    ap.add_argument("-b", "--batch", action="store_true",
                    help="        PDF    ")

    # v2
    ap.add_argument("--init", action="store_true",
                    help="   _pipeline_state.json（ PDF  ）")
    ap.add_argument("--status", action="store_true",
                    help="     ")
    ap.add_argument("--backfill-meta", action="store_true",
                    help="backfill provenance (pdf_sha256 + engine_versions) into "
                         "existing _META.json without re-converting")
    ap.add_argument("--resume", action="store_true",
                    help="       （  --batch  ）")
    ap.add_argument("--backfill-probe", action="store_true",
                    help="run the stage 1.5 text-layer probe for already-converted "
                         "papers (no engines, no GPU): writes _textlayer_probe.json "
                         "next to each existing _META.json")
    ap.add_argument("--figure-source", choices=["engine", "pdf"], default="engine",
                    help="engine = keep the Marker/MinerU image copies (default); "
                         "pdf = additionally extract print-usable figures from the "
                         "source PDF (embedded bitmaps at native size, the rest "
                         "rendered at --figure-dpi)")
    ap.add_argument("--figure-dpi", type=int, default=_FIGURE_DPI_DEFAULT, metavar="N",
                    help=f"DPI used by --figure-source pdf when a figure is rendered "
                         f"(default {_FIGURE_DPI_DEFAULT})")
    ap.add_argument("--no-textlayer-probe", dest="textlayer_probe",
                    action="store_false",
                    help="skip the stage 1.5 text-layer probe for this run "
                         "(default: run it; the probe is side-band evidence and "
                         "never blocks the conversion)")
    ap.add_argument("--force", action="store_true",
                    help="    ，     ")
    ap.add_argument("--from-manifest", type=Path, default=None, metavar="PATH",
                    help="  _download_manifest.json  source ")
    ap.add_argument("--import-urls", type=Path, default=None, metavar="PATH",
                    help=" --from-manifest  （    URL  ）")
    ap.add_argument("--max-pages", type=int, default=_DEFAULT_MAX_PAGES,
                    help=f"PDF     SKIP（  {_DEFAULT_MAX_PAGES}）")

    # MinerU
    ap.add_argument("--mineru-method", choices=["auto", "ocr", "txt"],
                    default="auto", help="MinerU    ")
    ap.add_argument("--mineru-backend", choices=["pipeline", "hybrid-engine"],
                    default="pipeline", help="MinerU  （hybrid     ）")
    ap.add_argument("-l", "--lang", help="PDF  （mineru pipeline  ）")

    # GPU
    ap.add_argument("--max-workers", type=int, default=2,
                    help="  PDF       （  2 = marker+mineru   ）")
    ap.add_argument("--gpu-fraction", type=float, default=0.4,
                    help="  Windows GPU       （0-1   0.4）")
    ap.add_argument("--cpu-threads", type=int, default=6,
                    help="  GPU       CPU    （  6）")
    ap.add_argument("--max-concurrent-pdfs", type=int, default=1,
                    help="        PDF （  1 =  ）")
    ap.add_argument("--gpu-cap-fraction", type=float, default=0.9,
                    help="   GPU      （0-1   0.9=90%%）")
    ap.add_argument("--gpu-wait-timeout", type=float, default=600.0,
                    help="   GPU         （   600=10 ）")

    args = ap.parse_args()

    # ── --backfill-meta ──────────────────────────────────────────────
    # Provenance-only pass over already-converted corpora: no engines are run and
    # no GPU budget is touched, so this returns before GPU governor setup.
    # Versions detected now are stamped "current_env_estimate" (never
    # "conversion_time"); idempotent and limited to missing fields.
    if args.backfill_meta:
        target = args.pdf.resolve()
        backfill_root = target if target.is_dir() else target.parent
        stats = backfill_meta(backfill_root)
        if stats["scanned"] == 0:
            print(f"ERROR: no _META.json found under {backfill_root}", file=sys.stderr)
            print("  expected <papers_dir>/paper-analysis/*/_META.json or "
                  "<papers_dir>/paper-merged/*/_META.json", file=sys.stderr)
            return 1
        print(
            "[backfill-meta] scanned={scanned} updated={updated} "
            "unchanged={unchanged} skipped_conversion_time={skipped_conversion_time} "
            "pdf_sha256_filled={pdf_sha256_filled} pdf_sha256_null={pdf_sha256_null} "
            "unreadable={unreadable}".format(**stats)
        )
        return 0

    # ── --backfill-probe ─────────────────────────────────────────────
    # Stage 1.5 over an already-converted corpus: reads the PDF's own text layer,
    # runs no engine and touches no GPU.  --resume would skip these papers forever,
    # so this is the only way an old corpus gains the evidence.
    if args.backfill_probe:
        target = args.pdf.resolve()
        probe_root = target if target.is_dir() else target.parent
        if not _meta_candidates(probe_root):
            print(f"ERROR: no _META.json found under {probe_root}", file=sys.stderr)
            print("  expected <papers_dir>/paper-analysis/*/_META.json or "
                  "<papers_dir>/paper-merged/*/_META.json", file=sys.stderr)
            return 1
        stats = backfill_probe(probe_root, force=args.force)
        print("[backfill-probe] scanned={scanned} probed={probed} reused={reused} "
              "warn={warn} not_measured={not_measured} "
              "no_pdf={no_pdf}".format(**stats))
        return 0

    # ── GPU    ──────────────────────────────────────────────────────
    global _GPU_FRACTION, _CPU_THREADS, _GPU_GOVERNOR, _GPU_WAIT_TIMEOUT
    _GPU_FRACTION = args.gpu_fraction
    _GPU_THREADS = args.cpu_threads
    _GPU_WAIT_TIMEOUT = args.gpu_wait_timeout

    if 0.0 < args.gpu_fraction <= 1.0:
        print(f"GPU  :   {args.gpu_fraction*100:.0f}%  ，"
              f"{args.cpu_threads} CPU  ", file=sys.stderr)
    elif args.gpu_fraction != 0:
        ap.error(
            f"--gpu-fraction     (0, 1]     0（   ），  "
            f"{args.gpu_fraction}"
        )
    else:
        print("GPU  :    （  CPU    ）", file=sys.stderr)

    if 0.0 < args.gpu_cap_fraction <= 1.0:
        _GPU_GOVERNOR = GpuGovernor(cap_fraction=args.gpu_cap_fraction)
        print(f"GPU   :     {args.gpu_cap_fraction*100:.0f}%，"
              f"    {args.gpu_wait_timeout:.0f}s", file=sys.stderr)
    elif args.gpu_cap_fraction != 0:
        ap.error(
            f"--gpu-cap-fraction     (0, 1]     0（  ），  "
            f"{args.gpu_cap_fraction}"
        )
    else:
        print("GPU   :    （    ，      ）", file=sys.stderr)

    # ──    ──────────────────────────────────────────────────
    #   v2:    papers_dir   PDF  /
    papers_dir = args.pdf.resolve()
    if papers_dir.is_file():
        papers_dir = papers_dir.parent

    state_path = papers_dir / "_pipeline_state.json"

    # ── --status ─────────────────────────────────────────────────────
    if args.status:
        if not state_path.exists():
            print(f"  _pipeline_state.json    : {state_path}", file=sys.stderr)
            print("  paper_reader.py papers/ --init ", file=sys.stderr)
            return 1
        state = PipelineState(state_path)
        print_status(state)
        return 0

    # ── --init ───────────────────────────────────────────────────────
    if args.init:
        state = PipelineState(state_path)
        pdfs = find_pdfs(args.pdf)
        if not pdfs:
            print(f"ERROR:    PDF: {args.pdf}", file=sys.stderr)
            return 1
        print(f"  {len(pdfs)}  PDF,     ...", file=sys.stderr)
        new_stems = state.init_from_pdfs(pdfs, args.max_pages)
        print(f"   : {len(new_stems)}   PDF  _pipeline_state.json", file=sys.stderr)
        state.save()

        #   --from-manifest
        manifest_path = args.from_manifest or args.import_urls
        if manifest_path and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                state.import_manifest(manifest)
            except (json.JSONDecodeError, OSError) as e:
                print(f"ERROR:    manifest: {e}", file=sys.stderr)

        print_status(state)
        return 0

    # ── --import-urls (no --init) ───────────────────────────────────
    manifest_path = args.from_manifest or args.import_urls
    if manifest_path and not args.init:
        if not state_path.exists():
            print(f"ERROR: _pipeline_state.json    : {state_path}", file=sys.stderr)
            print("  paper_reader.py papers/ --init ", file=sys.stderr)
            return 1
        if not manifest_path.exists():
            print(f"ERROR: manifest    : {manifest_path}", file=sys.stderr)
            return 1
        state = PipelineState(state_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            state.import_manifest(manifest)
            return 0
        except (json.JSONDecodeError, OSError) as e:
            print(f"ERROR:    manifest: {e}", file=sys.stderr)
            return 1

    # ──     ────────────────────────────────────────────────────

    #
    if args.engines in ("both", "marker") and not MARKER_BIN.exists():
        print(f"ERROR: marker venv  : {MARKER_BIN}", file=sys.stderr)
        print("  : bash ~/projects/dc-skills/paper-reader/scripts/bootstrap.sh",
              file=sys.stderr)
        return 2
    if args.engines in ("both", "mineru") and not MINERU_BIN.exists():
        print(f"ERROR: mineru venv  : {MINERU_BIN}", file=sys.stderr)
        print("  : bash ~/projects/dc-skills/paper-reader/scripts/bootstrap.sh",
              file=sys.stderr)
        return 2

    #   /  _pipeline_state.json
    state = PipelineState(state_path) if state_path.exists() else None

    pdfs = find_pdfs(args.pdf)
    if not pdfs:
        print(f"ERROR:    PDF: {args.pdf}", file=sys.stderr)
        return 1

    #   PDF   /init
    if state and state.has(pdfs[0].stem):
        pass  #   state     process_one  resume
    elif len(pdfs) > 1 and not state:
        print("  :  _pipeline_state.json    .", file=sys.stderr)
        print("  paper_reader.py papers/ --init ", file=sys.stderr)

    dirs = _derive_output_dirs(papers_dir)
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    print(f"   {len(pdfs)}  PDF", file=sys.stderr)
    print(f"  : {args.engines}     : {args.pages or ' '}", file=sys.stderr)
    print(f"  PDF  : {args.max_workers}   |    : {args.max_concurrent_pdfs} PDF", file=sys.stderr)

    # ──     ──────────────────────────────────────────────
    results: list[PaperResult] = []

    def _should_skip(stem: str) -> bool:
        """ resume   force ."""
        if not args.resume or not state:
            return False
        if args.force:
            return False
        if not state.has(stem):
            return False
        return state.is_fully_done(stem)

    if args.max_concurrent_pdfs <= 1 or len(pdfs) == 1:
        #
        for i, pdf in enumerate(pdfs, 1):
            stem = pdf.stem
            if _should_skip(stem):
                print(f"[{i}/{len(pdfs)}] {pdf.name} ⏭️   ", file=sys.stderr)
                #        state
                entry = state.get(stem) if state else {}
                results.append(PaperResult(pdf_path=str(pdf), stem=stem))
                continue

            print(f"\n[{i}/{len(pdfs)}] {pdf.name}", file=sys.stderr)
            try:
                r = process_one(
                    pdf, papers_dir, args.engines, args.pages,
                    args.mineru_method, args.mineru_backend, args.lang,
                    max_workers=args.max_workers,
                    state=state,
                    force=args.force,
                    textlayer_probe=args.textlayer_probe,
                    figure_source=args.figure_source,
                    figure_dpi=args.figure_dpi,
                )
                results.append(r)
            except KeyboardInterrupt:
                print("\n  。", file=sys.stderr)
                if state:
                    state.set_phase(stem, "phase1_converted",
                                    {"status": "interrupted"})
                    state.save()
                break
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                if state:
                    state.set_phase(stem, "phase1_converted",
                                    {"status": "failed", "error": str(e)[:500]})
                    state.save()
                results.append(PaperResult(pdf_path=str(pdf), stem=stem))
    else:
        #
        total_procs = args.max_concurrent_pdfs * args.max_workers
        if 0.0 < args.gpu_fraction and total_procs * args.gpu_fraction > 1.0 + 1e-6:
            print(
                f"⚠  : {total_procs}   × {args.gpu_fraction*100:.0f}%   = "
                f"{total_procs*args.gpu_fraction*100:.0f}% > 100%，   OOM！",
                file=sys.stderr,
            )
            print(f"    ：   --max-concurrent-pdfs   --gpu-fraction",
                  file=sys.stderr)

        with ThreadPoolExecutor(max_workers=args.max_concurrent_pdfs) as tex:
            indexed = list(enumerate(pdfs, 1))

            def _run_one(idx_pdf):
                i, pdf = idx_pdf
                stem = pdf.stem
                if _should_skip(stem):
                    print(f"[{i}/{len(pdfs)}] {pdf.name} ⏭️   ", file=sys.stderr)
                    return PaperResult(pdf_path=str(pdf), stem=stem)
                print(f"\n[{i}/{len(pdfs)}] {pdf.name}", file=sys.stderr)
                try:
                    return process_one(
                        pdf, papers_dir, args.engines, args.pages,
                        args.mineru_method, args.mineru_backend, args.lang,
                        max_workers=args.max_workers,
                        state=state,
                        force=args.force,
                        textlayer_probe=args.textlayer_probe,
                        figure_source=args.figure_source,
                        figure_dpi=args.figure_dpi,
                    )
                except Exception as e:
                    print(f"  ERROR: {e}", file=sys.stderr)
                    return PaperResult(pdf_path=str(pdf), stem=stem)

            try:
                for r in tex.map(_run_one, indexed):
                    results.append(r)
            except KeyboardInterrupt:
                print("\n  （   PDF  ）。", file=sys.stderr)

    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
