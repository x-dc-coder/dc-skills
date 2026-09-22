#!/usr/bin/env python3
"""Draft validator against a writing contract (INTERFACES.md v1 section 5, I9).

Consumes _writing_contract.yaml (built by build_contract.py from corpus
quantiles) and a draft (Markdown), then reports, per clause:

    {id, metric, actual, target, deviation, level, status, evidence_lines}

Design rules (frozen):
  * Pure stdlib, no LLM / network on the OBSERVED path (red line 1, 3).
  * The draft metrics are **not** recomputed here. They are produced by
    text_metrics.compute_text_metrics(text, bundle) with a LexiconBundle from
    lexicon_loader.load_lexicons() (INTERFACES.md section 3). The computation is
    injectable so the comparison logic is testable without that module and so no
    second metrics implementation can grow here.
  * status in {pass, fail, warn, skipped}; level in {gate, warn}.
    A clause outside its target band is "fail" when level==gate and "warn"
    otherwise. Actual value or target unavailable -> "skipped".
  * No 0-100 aggregate value is produced: assessment is strictly per clause.
  * Language scope comes first, and it is two questions - both answered in
    text_metrics: WHICH language the draft is in (detect_language) and WHICH
    languages the rules cover (SUPPORTED_LANGUAGES, read through _rule_languages()).
    A language with no rules is refused: draft_validity.status=language_unsupported,
    every clause skipped without any gate/warn arithmetic (no metric is even
    computed), exit code 2. The detector's own supported flag is the Round-A
    "English-majority" contract, NOT the capability verdict; reading it as the scope
    is how a Chinese draft came to report language_supported=false while its clauses
    were graded normally (issue #19). An unavailable or unusable verdict is reported
    as such, never guessed.
  * Exit code: 2 when the draft is not usable evidence (unsupported language, fewer
    than MIN_ALPHA_TOKENS alpha tokens, no evaluable clause, or fewer than
    MIN_EVALUABLE_RATIO of the clauses evaluable), 1 when a gate clause fails, 0
    otherwise. Degenerate input can never pass: an empty / heading-only draft
    reports draft_validity.status=insufficient_evidence instead of a silent
    all-skipped 0, and an all-skipped contract always exits 2.
  * A warn-only contract (no gate clause) exits 0 but says so loudly: passing a
    contract that has no gate clause is not evidence that the baseline is met.
  * The draft is NFC-normalized once before any metric or offset math, mirroring
    Paper.canonical_text() on the corpus side (same characters -> same tokens).
  * Exactly one metric is owned by this module: M-PCNT-25 (paragraph mean length).
    text_metrics is a prose layer and never emits it, while the corpus target for
    M-PCNT-25 is derived by profile_papers from content_list.json block structure.
    The draft rule mirrors the profiler definition (blank-line-separated prose
    blocks of >= 15 words, headings and fenced code blocks excluded), so actual
    and target share one definition; evidence uses INTERFACES.md section 7 shape B
    ({block_index, excerpt}); all other metrics keep coming from the provider.

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/validate_draft.py \
        --contract <dir>/_writing_contract.yaml --draft draft.md [--json <out.json>]
"""
from __future__ import annotations

import argparse
import bisect
import importlib
import json
import math
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:  # executed as a script: script dir is sys.path[0]; under pytest we add it
    import build_contract
except ImportError:  # pragma: no cover - defensive
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_contract

VALIDATOR_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "1.0"
VALID_LEVELS = ("gate", "warn")
VALID_STATUSES = ("pass", "fail", "warn", "skipped")
MAX_EVIDENCE = 5

# Degenerate-input guard: below these thresholds a draft may not be reported as
# passing, because "every clause skipped" is not evidence of compliance.
MIN_ALPHA_TOKENS = 50
#: Chinese drafts have no ASCII words at all, so the alpha-token floor would
#: always read "insufficient evidence". The Chinese floor is expressed in
#: cjk-units (CJK characters + ASCII tokens) instead (issue #13).
MIN_CJK_UNITS = 150
MIN_EVALUABLE_RATIO = 0.5

# Fallback only: the live language set comes from text_metrics.SUPPORTED_LANGUAGES
# via _rule_languages().
SUPPORTED_METRIC_LANGUAGES = ("en",)
# The note strings are kept distinct (unsupported / unavailable / undecidable) so
# the JSON never conflates "no rules for this language" with "scope not checked".
UNSUPPORTED_LANGUAGE_NOTE = "该语言无已验证指标规则"
LANGUAGE_DETECTION_UNAVAILABLE_NOTE = "语言检测不可用，未检查指标语言范围"
LANGUAGE_UNDECIDABLE_NOTE = "无法判定语言，未检查指标语言范围"
# Only used when text_metrics (the frozen tokenizer) is unavailable; it never
# produces a metric value, it only keeps the guard from silently disappearing.
_FALLBACK_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
EXIT_OK = 0
EXIT_GATE_FAIL = 1
EXIT_ERROR = 2

_DEFAULT_METRICS_MODULE = "text_metrics"
_DEFAULT_LEXICON_MODULE = "lexicon_loader"

# Structure metric owned by the validator (see the module docstring).
PARAGRAPH_METRIC_ID = "M-PCNT-25"
MIN_PARAGRAPH_WORDS = 15
#: Chinese paragraph floor, in cjk-units: a 15-character Chinese block is a
#: caption, not a paragraph (issue #13).
MIN_PARAGRAPH_CJK_UNITS = 40
PARAGRAPH_EVIDENCE_SAMPLE = 3
EXCERPT_MAX = 80


class ValidatorError(Exception):
    """Raised when the contract, the draft, or the metrics layer is unusable."""


ComputeFn = Callable[[str], dict]
LanguageDetector = Callable[[str], Any]


# ---------------------------------------------------------------------------
# Draft metrics (delegated, never reimplemented here)
# ---------------------------------------------------------------------------

def load_default_compute(
    language: str = "en",
    metrics_module: str = _DEFAULT_METRICS_MODULE,
    lexicon_module: str = _DEFAULT_LEXICON_MODULE,
) -> ComputeFn:
    """Load text_metrics + lexicon_loader and return compute(text) -> metrics.

    This is the only metrics entry point of the validator. Any failure to import
    or to load the frozen lexicons is reported as ValidatorError instead of
    being papered over with a local implementation.
    """
    try:
        lexicons = importlib.import_module(lexicon_module)
        metrics = importlib.import_module(metrics_module)
    except ImportError as exc:
        raise ValidatorError(
            f"cannot import {lexicon_module}/{metrics_module}: {exc}. "
            "Draft metrics must come from text_metrics.compute_text_metrics."
        ) from exc
    missing = [
        name for name, module in (("load_lexicons", lexicons), ("compute_text_metrics", metrics))
        if not callable(getattr(module, name, None))
    ]
    if missing:
        raise ValidatorError(f"metrics layer is incomplete, missing callable(s): {', '.join(missing)}")
    try:
        # The draft's language decides which word-list release is loaded; a
        # Chinese draft measured with English lists would produce numbers that
        # look fine and mean nothing.
        bundle = lexicons.load_lexicons(language=language)
    except TypeError:
        # an injected loader without language support (interface stays lenient)
        bundle = lexicons.load_lexicons()
    except Exception as exc:  # noqa: BLE001 - surface any lexicon failure verbatim
        raise ValidatorError(f"load_lexicons() failed: {exc}") from exc

    def compute(text: str) -> dict:
        result = metrics.compute_text_metrics(text, bundle)
        if not isinstance(result, dict):
            raise ValidatorError("compute_text_metrics() did not return a mapping")
        return result

    return compute


def load_compute_module(module_name: str) -> ComputeFn:
    """Load an injected metrics provider: module.compute(text) -> metrics."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValidatorError(f"cannot import compute module {module_name!r}: {exc}") from exc
    func = getattr(module, "compute", None)
    if not callable(func):
        raise ValidatorError(f"compute module {module_name!r} must expose compute(text)")

    def compute(text: str) -> dict:
        result = func(text)
        if not isinstance(result, dict):
            raise ValidatorError(f"{module_name}.compute() did not return a mapping")
        return result

    return compute


# ---------------------------------------------------------------------------
# Draft language scope (single source of truth: text_metrics.detect_language)
# ---------------------------------------------------------------------------

def load_language_detector(
    module_name: str = _DEFAULT_METRICS_MODULE,
    *,
    required: bool = False,
) -> "LanguageDetector | None":
    """Resolve detect_language() from the metrics module.

    Returns None when an optional (default) module has no detector, so the
    validator keeps working before the detector lands. An explicitly requested
    module that lacks it is an error instead of a silent downgrade.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        if required:
            raise ValidatorError(f"cannot import language module {module_name!r}: {exc}") from exc
        return None
    detector = getattr(module, "detect_language", None)
    if not callable(detector):
        if required:
            raise ValidatorError(
                f"language module {module_name!r} must expose detect_language(text)"
            )
        return None
    return detector


def _first_of(raw: Any, keys: tuple[str, ...], kind: type) -> Any:
    for key in keys:
        value = raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)
        if isinstance(value, bool):
            if kind is bool:
                return value
            continue
        if isinstance(value, kind):
            return value
    return None


def normalize_language_verdict(raw: Any, detector: str | None = None) -> dict:
    """Normalize whatever detect_language() returns into the validator's shape.

    Accepted: a mapping or object with supported/is_supported, language/lang/code
    and cjk_ratio/ratio; a bare bool; or a language code alone. Anything else is
    reported as an unusable verdict rather than approximated.

    "supported" answers "does this layer have metric rules for the language" via
    _rule_languages(). The detector's own flag is NOT that answer: it is the
    Round-A "text is English-majority" contract (cjk_ratio <= 0.10), so a Chinese
    draft reports supported=False even though Chinese rules exist. That raw flag
    is archived as detector_supported; only a language the detector cannot name
    (None/"unknown") keeps its verdict, because there is no table entry to look up.
    """
    if isinstance(raw, bool):
        detector_supported, language, cjk_ratio = raw, None, None
    elif isinstance(raw, str):
        detector_supported, language, cjk_ratio = None, raw, None
    else:
        detector_supported = _first_of(raw, ("supported", "is_supported", "ok", "in_scope"), bool)
        language = _first_of(raw, ("language", "lang", "code", "detected"), str)
        cjk_ratio = _first_of(raw, ("cjk_ratio", "ratio", "han_ratio", "cjk"), float)
    code = language.lower().split("-")[0] if isinstance(language, str) and language else None
    if code and code != "unknown":
        supported = code in _rule_languages()
    else:
        supported = detector_supported
    warnings: list[str] = []
    if supported is None:
        warnings.append(
            f"unusable detect_language() verdict ({type(raw).__name__}: {raw!r}); "
            "the language scope could not be checked"
        )
        return {
            "available": False, "supported": None, "language": language,
            "cjk_ratio": round(cjk_ratio, 6) if cjk_ratio is not None else None,
            "detector_supported": detector_supported,
            "supported_languages": list(_rule_languages()),
            "detector": detector, "note": LANGUAGE_UNDECIDABLE_NOTE, "warnings": warnings,
        }
    return {
        "available": True,
        "supported": bool(supported),
        "language": language,
        "cjk_ratio": round(cjk_ratio, 6) if cjk_ratio is not None else None,
        "detector_supported": detector_supported,
        "supported_languages": list(_rule_languages()),
        "detector": detector,
        "note": None if supported else UNSUPPORTED_LANGUAGE_NOTE,
        "warnings": warnings,
    }


def _rule_languages() -> tuple[str, ...]:
    """Languages the metrics layer has validated rules for ("en", "zh").

    Read from the metrics module so there is exactly one source of truth; falls
    back to English-only when an injected provider has no SUPPORTED_LANGUAGES.
    """
    try:
        module = importlib.import_module(_DEFAULT_METRICS_MODULE)
        declared = getattr(module, "SUPPORTED_LANGUAGES", None)
        if isinstance(declared, (list, tuple)) and declared:
            return tuple(str(item) for item in declared)
    except Exception:  # noqa: BLE001 - an unreadable provider must not block
        pass
    return tuple(SUPPORTED_METRIC_LANGUAGES)


def detect_draft_language(
    text: str,
    detector: "LanguageDetector | None" = None,
    *,
    module_name: str | None = None,
) -> dict:
    """Language verdict for the draft, delegated to text_metrics.detect_language."""
    module = module_name or _DEFAULT_METRICS_MODULE
    resolved = detector if detector is not None else load_language_detector(module)
    if resolved is None:
        return {
            "available": False, "supported": None, "language": None, "cjk_ratio": None,
            "supported_languages": list(_rule_languages()), "detector": None,
            "detector_supported": None,
            "note": LANGUAGE_DETECTION_UNAVAILABLE_NOTE,
            "warnings": [f"language detection unavailable: {module}.detect_language is missing; "
                         "the language scope was not checked"],
        }
    name = f"{getattr(resolved, '__module__', module)}.detect_language"
    try:
        raw = resolved(text)
    except Exception as exc:  # noqa: BLE001 - an exploding detector is not a pass
        return {
            "available": False, "supported": None, "language": None, "cjk_ratio": None,
            "supported_languages": list(_rule_languages()), "detector": name,
            "detector_supported": None,
            "note": LANGUAGE_DETECTION_UNAVAILABLE_NOTE,
            "warnings": [f"detect_language() failed ({type(exc).__name__}): {exc}"],
        }
    return normalize_language_verdict(raw, name)


def language_scope_message(language: dict) -> str:
    """One-line reason used in JSON reasons and in the human report."""
    parts = [f"language={language.get('language')} is not supported"]
    if language.get("cjk_ratio") is not None:
        parts.append(f"cjk_ratio={language['cjk_ratio']}")
    parts.append(UNSUPPORTED_LANGUAGE_NOTE)
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Structure metric owned by the validator: M-PCNT-25 (paragraph mean length)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParagraphBlock:
    """A blank-line-separated Markdown block that is prose (no heading/fence)."""
    index: int
    start: int
    end: int
    text: str
    words: int


def split_paragraphs(text: str) -> list[ParagraphBlock]:
    """Split a Markdown draft into prose paragraph blocks.

    Frozen rules: blocks are separated by blank lines; heading lines (first
    non-space character '#') and fenced code blocks (''' or ~~~) are never prose
    and also terminate the current block; paragraph words = number of
    whitespace-separated tokens in the joined block text. Block start/end are
    character offsets into `text`, so they map to draft line numbers.
    """
    blocks: list[ParagraphBlock] = []
    buffer: list[str] = []
    block_start = 0
    block_end = 0
    in_fence = False

    def flush() -> None:
        nonlocal buffer
        if buffer:
            body = " ".join(buffer)
            blocks.append(ParagraphBlock(
                index=len(blocks), start=block_start, end=block_end,
                text=body, words=len(body.split()),
            ))
        buffer = []

    offset = 0
    for raw in text.splitlines(keepends=True):
        line_start = offset
        offset += len(raw)
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        if not buffer:
            block_start = line_start
        block_end = offset
        buffer.append(stripped)
    flush()
    return blocks


def _excerpt(text: str) -> str:
    """First EXCERPT_MAX characters of a block (slice first, then truncate)."""
    return text.strip()[:EXCERPT_MAX]


def _paragraph_distribution(words: list[int]) -> dict:
    if not words:
        return {"median": 0, "p25": 0, "p75": 0, "std": 0.0}
    ordered = sorted(words)
    n = len(ordered)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return float(ordered[idx])

    std = statistics.pstdev(ordered) if n > 1 else 0.0
    return {"median": pct(0.5), "p25": pct(0.25), "p75": pct(0.75), "std": round(float(std), 6)}


def compute_paragraph_metric(text: str, blocks: list[ParagraphBlock] | None = None,
                             language: str = "en") -> dict:
    """M-PCNT-25 for a draft: mean length of prose paragraphs.

    English measures words per paragraph with a 15-word floor; Chinese measures
    cjk-units per paragraph with a 40-unit floor (Chinese has no spaces, so the
    word caliber would collapse every paragraph to ~1 and then filter it out).
    """
    if blocks is None:
        blocks = split_paragraphs(text)
    if language == "zh":
        lengths = [_cjk_units(block.text) for block in blocks]
        floor = MIN_PARAGRAPH_CJK_UNITS
        unit = "cjk-units/paragraph"
        warning_code = "no_paragraphs_above_min_cjk_units"
    else:
        lengths = [block.words for block in blocks]
        floor = MIN_PARAGRAPH_WORDS
        unit = "words/paragraph"
        warning_code = "no_paragraphs_above_min_words"
    keep = [index for index, length in enumerate(lengths) if length >= floor]
    count = len(keep)
    value = round(sum(lengths[index] for index in keep) / count, 6) if count else None
    longest = sorted(keep, key=lambda index: (-lengths[index], blocks[index].index))[:PARAGRAPH_EVIDENCE_SAMPLE]
    return {
        "value": value,
        "n": count,
        "denominator": count,
        "unit": unit,
        "state": "OBSERVED",
        "method": "rule",
        "metric_spec": PARAGRAPH_METRIC_ID,
        "distribution": _paragraph_distribution([lengths[index] for index in keep]),
        "n_blocks": len(blocks),
        "evidence": {
            "count": count,
            "sample": [{"block_index": blocks[index].index,
                        "excerpt": _excerpt(blocks[index].text)} for index in longest],
        },
        "warnings": [] if count else [warning_code],
    }


def _cjk_units(text: str) -> int:
    """CJK characters + ASCII alpha tokens (the Chinese length unit)."""
    cjk = sum(1 for ch in text
              if "\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
              or "\uf900" <= ch <= "\ufaff")
    return cjk + len([1 for ch in text if ch.isascii() and ch.isalpha()])
    


# ---------------------------------------------------------------------------
# Draft validity (degenerate-input guard)
# ---------------------------------------------------------------------------

def count_alpha_tokens(text: str) -> tuple[int, str]:
    """Alpha-token count of the draft, using the draft metrics tokenizer.

    INTERFACES.md section 3 freezes text_metrics.tokenize(), so the draft metrics
    layer stays the single source of truth for what a token is. The regex
    fallback only prevents the guard from silently vanishing when that module is
    missing; it never produces a metric value.
    """
    try:
        module = importlib.import_module(_DEFAULT_METRICS_MODULE)
        tokenize = getattr(module, "tokenize", None)
        if callable(tokenize):
            tokens = tokenize(text)
            if isinstance(tokens, list):
                return len(tokens), f"{_DEFAULT_METRICS_MODULE}.tokenize"
    except Exception:  # noqa: BLE001 - any failure degrades to the regex counter
        pass
    return len(_FALLBACK_TOKEN_RE.findall(text)), "regex_fallback"


# ---------------------------------------------------------------------------
# Contract loading and validation
# ---------------------------------------------------------------------------

def _require_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidatorError(f"{where}: expected a number, got {type(value).__name__}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValidatorError(f"{where}: expected a finite number")
    return number


def check_contract(data: Any) -> dict:
    """Validate the frozen contract shape; fail fast on any violation."""
    if not isinstance(data, dict):
        raise ValidatorError("contract must be a mapping")
    clauses = data.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        raise ValidatorError("contract must contain a non-empty 'clauses' list")
    seen: set[str] = set()
    for index, clause in enumerate(clauses):
        where = f"clauses[{index}]"
        if not isinstance(clause, dict):
            raise ValidatorError(f"{where}: must be a mapping")
        for key in ("id", "metric", "level"):
            if not isinstance(clause.get(key), str) or not clause.get(key):
                raise ValidatorError(f"{where}: missing required string field {key!r}")
        if clause["id"] in seen:
            raise ValidatorError(f"{where}: duplicate clause id {clause['id']!r}")
        seen.add(clause["id"])
        if clause["level"] not in VALID_LEVELS:
            raise ValidatorError(
                f"{where}: level must be one of {VALID_LEVELS}, got {clause['level']!r}"
            )
        target = clause.get("target")
        if target is not None:
            if not isinstance(target, (list, tuple)) or len(target) != 2:
                raise ValidatorError(f"{where}: target must be null or a [lo, hi] pair")
            low = _require_number(target[0], f"{where}.target[0]")
            high = _require_number(target[1], f"{where}.target[1]")
            if low > high:
                raise ValidatorError(f"{where}: target lo {low} > hi {high}")
    return data


def load_contract(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidatorError(f"cannot read contract {path.name}: {exc}") from exc
    try:
        data = build_contract.load_yaml(text)
    except build_contract.YamlError as exc:
        raise ValidatorError(f"cannot parse contract {path.name}: {exc}") from exc
    return check_contract(data)


# ---------------------------------------------------------------------------
# Line mapping and evidence
# ---------------------------------------------------------------------------

def line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def line_of(offset: int, starts: list[int]) -> int:
    """1-based line number for a character offset in the draft."""
    if offset < 0:
        offset = 0
    return bisect.bisect_right(starts, offset)


def _span_pair(span: Any) -> tuple[int, int] | None:
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return None
    start, end = span[0], span[1]
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return start, end


def _evidence_anchor(entry: dict) -> int:
    span = entry.get("span")
    if isinstance(span, list):
        return span[0]
    return entry.get("block_index", 0)


def extract_evidence(
    metric: dict,
    starts: list[int],
    n_lines: int,
    block_lines: dict[int, int] | None = None,
) -> list[dict]:
    """Map metric evidence onto draft line numbers (INTERFACES.md section 7).

    Both frozen shapes are supported: span-type entries (text metrics expose
    char offsets) and block_index-type entries (structure metrics such as
    M-PCNT-25), which are resolved through `block_lines`.
    """
    evidence = metric.get("evidence")
    sample = evidence.get("sample") if isinstance(evidence, dict) else None
    if not isinstance(sample, list):
        return []
    out: list[dict] = []
    for item in sample:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt", ""))
        pair = _span_pair(item.get("span"))
        if pair is not None:
            line = min(max(line_of(pair[0], starts), 1), max(n_lines, 1))
            out.append({"line": line, "span": [pair[0], pair[1]], "excerpt": excerpt})
            continue
        block_index = item.get("block_index")
        if (isinstance(block_index, int) and not isinstance(block_index, bool)
                and block_lines and block_index in block_lines):
            out.append({"line": block_lines[block_index], "block_index": block_index, "excerpt": excerpt})
    out.sort(key=lambda entry: (entry["line"], _evidence_anchor(entry)))
    return out[:MAX_EVIDENCE]


def _skip_reason(metric_id: str, entry: Any) -> str:
    """Why a clause could not be evaluated on this draft."""
    if not isinstance(entry, dict):
        return "metric_not_produced_by_the_metrics_layer"
    if metric_id == PARAGRAPH_METRIC_ID:
        return "no_paragraphs_above_min_words"
    if entry.get("value") is None:
        # Name the real cause: "this language/metric pair is not measurable yet"
        # is a different statement from "the metrics layer returned nothing"
        # (issue #13 per-metric capability).
        warnings = set(entry.get("warnings") or ())
        for code in ("CAPABILITY_NOT_SUPPORTED", "LANGUAGE_NOT_SUPPORTED"):
            if code in warnings:
                return code.lower()
        return "value_missing_or_null_in_the_metrics_layer"
    return "value_not_a_finite_number"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _metric_value(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 6)


def validate(
    contract: dict,
    draft_text: str,
    compute: ComputeFn | None = None,
    *,
    draft_name: str | None = None,
    contract_name: str | None = None,
    min_alpha_tokens: int = MIN_ALPHA_TOKENS,
    min_evaluable_ratio: float = MIN_EVALUABLE_RATIO,
    language_detector: LanguageDetector | None = None,
    require_language_detector: bool = False,
) -> dict:
    """Compare draft metrics against contract clauses (per clause, no aggregate)."""
    check_contract(contract)
    # One NFC pass before any metric or offset math: the corpus side normalizes in
    # Paper.canonical_text(), and the two sides must tokenize identical characters.
    draft_text = unicodedata.normalize("NFC", draft_text)

    # Language scope is decided before anything else. An out-of-scope draft gets no
    # pass/fail/warn verdict at all, so it can never be graded against an English
    # baseline (and no metric is computed, which also keeps the provider uncalled).
    draft_language = detect_draft_language(draft_text, language_detector)
    # Issue #13: a language is blocked only when *no* metric is validated for it.
    # Chinese is partially measurable, so it is evaluated clause by clause; the
    # clauses whose metric is not (yet) validated for it are skipped with the
    # per-metric reason rather than silently graded.
    rule_languages = _rule_languages()
    draft_language_code = draft_language.get("language")
    # Blocked when the language is known to have no rules, or when the detector
    # refuses without naming a language (in that case there is nothing to look up
    # and its supported=False verdict must be trusted).
    language_blocked = bool(
        draft_language["available"]
        and (
            (draft_language_code not in (None, "unknown")
             and draft_language_code not in rule_languages)
            or (draft_language_code in (None, "unknown")
                and draft_language.get("supported") is False)
        )
    )

    starts = line_starts(draft_text)
    n_lines = len(starts)
    paragraph_blocks: list[ParagraphBlock] = []
    block_lines: dict[int, int] = {}
    metrics: dict = {}

    if not language_blocked:
        if compute is None:
            compute = load_default_compute(draft_language_code or "en")
        try:
            metrics = compute(draft_text)
        except ValidatorError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken metrics layer is an error, never a gate failure
            raise ValidatorError(
                f"metrics computation failed ({type(exc).__name__}): {exc}"
            ) from exc
        if not isinstance(metrics, dict):
            raise ValidatorError("metrics provider did not return a mapping")

        # M-PCNT-25 is structural: compute it here from the draft's block layout unless
        # the injected provider already produced an OBSERVED value for it.
        if any(clause["metric"] == PARAGRAPH_METRIC_ID for clause in contract["clauses"]):
            paragraph_blocks = split_paragraphs(draft_text)
            block_lines = {block.index: line_of(block.start, starts) for block in paragraph_blocks}
            if _metric_value(metrics.get(PARAGRAPH_METRIC_ID)) is None:
                metrics = dict(metrics)
                metrics[PARAGRAPH_METRIC_ID] = compute_paragraph_metric(
                    draft_text, paragraph_blocks, draft_language_code or "en")

    results: list[dict] = []
    warnings: list[str] = []
    for clause in contract["clauses"]:
        metric_id = clause["metric"]
        level = clause["level"]
        target = clause.get("target")
        if language_blocked:
            blocked: dict = {
                "id": clause["id"],
                "metric": metric_id,
                "actual": None,
                "target": list(target) if isinstance(target, (list, tuple)) else None,
                "deviation": None,
                "level": level,
                "status": "skipped",
                "status_reason": "language_unsupported",
                "evidence_lines": [],
                "evidence": [],
            }
            if isinstance(clause.get("unit"), str):
                blocked["unit"] = clause["unit"]
            if isinstance(clause.get("provenance"), dict):
                blocked["provenance"] = clause["provenance"]
            results.append(blocked)
            continue
        entry = metrics.get(metric_id)
        metric_entry = entry if isinstance(entry, dict) else {}
        actual = _metric_value(entry)
        evidence = extract_evidence(metric_entry, starts, n_lines, block_lines)
        evidence_lines = sorted({item["line"] for item in evidence})

        deviation: float | None = None
        status_reason: str | None = None
        if target is None:
            status = "skipped"
            status_reason = "target_unavailable"
        elif actual is None:
            status = "skipped"
            status_reason = _skip_reason(metric_id, entry)
            detail = f"{metric_id}: skipped ({status_reason})"
            if metric_id == PARAGRAPH_METRIC_ID and isinstance(metric_entry.get("n_blocks"), int):
                detail += (f"; no paragraph with >= {MIN_PARAGRAPH_WORDS} words among "
                           f"{metric_entry['n_blocks']} paragraph block(s)")
            warnings.append(detail)
        else:
            low = _require_number(target[0], f"{clause['id']}.target[0]")
            high = _require_number(target[1], f"{clause['id']}.target[1]")
            if low <= actual <= high:
                deviation = 0.0
                status = "pass"
            else:
                distance = actual - high if actual > high else low - actual
                deviation = round(distance, 6)
                status = "fail" if level == "gate" else "warn"

        result: dict = {
            "id": clause["id"],
            "metric": metric_id,
            "actual": actual,
            "target": list(target) if isinstance(target, (list, tuple)) else None,
            "deviation": deviation,
            "level": level,
            "status": status,
            "status_reason": status_reason,
            "evidence_lines": evidence_lines,
            "evidence": evidence,
        }
        if isinstance(clause.get("unit"), str):
            result["unit"] = clause["unit"]
        if isinstance(clause.get("provenance"), dict):
            result["provenance"] = clause["provenance"]
        results.append(result)

    n_clauses = len(results)
    n_evaluable = sum(1 for item in results if item["status"] in ("pass", "fail", "warn"))
    evaluable_ratio = round(n_evaluable / n_clauses, 6) if n_clauses else 0.0
    gate_failures = [item["id"] for item in results if item["status"] == "fail"]
    warn_failures = [item["id"] for item in results if item["status"] == "warn"]
    n_gate_clauses = sum(1 for clause in contract["clauses"] if clause["level"] == "gate")

    alpha_tokens, tokenizer = count_alpha_tokens(draft_text)
    # The degenerate-input floor is language-specific: a Chinese draft has no
    # ASCII words at all, so it is measured in cjk-units (issue #13).
    cjk_units = _cjk_units(draft_text) if draft_language_code == "zh" else 0
    validity_reasons: list[str] = []
    if language_blocked:
        validity_reasons.append(language_scope_message(draft_language))
        validity_status = "language_unsupported"
    else:
        if draft_language_code == "zh":
            if cjk_units < MIN_CJK_UNITS:
                validity_reasons.append(f"cjk_units={cjk_units} < {MIN_CJK_UNITS}")
        elif alpha_tokens < min_alpha_tokens:
            validity_reasons.append(f"alpha_tokens={alpha_tokens} < {min_alpha_tokens}")
        if n_clauses and n_evaluable == 0:
            # absolute floor: an all-skipped contract is unusable evidence whatever
            # the configurable ratio threshold says
            validity_reasons.append(f"no evaluable clause: 0/{n_clauses} clause(s) skipped")
        elif evaluable_ratio < min_evaluable_ratio:
            validity_reasons.append(
                f"evaluable_clauses={n_evaluable}/{n_clauses} ({evaluable_ratio:.3g}) < {min_evaluable_ratio}"
            )
        if require_language_detector and not draft_language["available"]:
            # opt-in strictness: without a detector the language scope cannot be
            # checked at all, so a caller who needs that guarantee refuses instead
            detail = (draft_language.get("warnings") or ["no language verdict"])[0]
            validity_reasons.append(f"language detection unavailable: {detail}")
        validity_status = "insufficient_evidence" if validity_reasons else "ok"
    draft_validity = {
        "status": validity_status,
        "reasons": validity_reasons,
        "language": draft_language.get("language"),
        "language_supported": draft_language.get("supported"),
        "cjk_ratio": draft_language.get("cjk_ratio"),
        "alpha_tokens": alpha_tokens,
        "min_alpha_tokens": min_alpha_tokens,
        "cjk_units": cjk_units or None,
        "min_cjk_units": MIN_CJK_UNITS if draft_language_code == "zh" else None,
        "tokenizer": tokenizer,
        "n_evaluable": n_evaluable,
        "n_clauses": n_clauses,
        "evaluable_ratio": evaluable_ratio,
        "min_evaluable_ratio": min_evaluable_ratio,
        "skipped_reasons": {
            item["metric"]: item.get("status_reason")
            for item in results if item["status"] == "skipped"
        },
    }

    generated_from = contract.get("generated_from")
    if not isinstance(generated_from, dict):
        generated_from = {}
    contract_source = contract_name or generated_from.get("source_file")
    gate_mode = generated_from.get("gate_mode")
    if not isinstance(gate_mode, str) or not gate_mode:
        gate_mode = "quantile-gate" if n_gate_clauses else "warn-only"
    note = None
    if n_gate_clauses == 0 and not language_blocked:
        note = ("contract has no gate clause (warn-only): passing this check does NOT mean "
                "the draft meets the corpus baseline")
    for warning in draft_language.get("warnings") or []:
        warnings.append(f"language: {warning}")

    summary = {
        "n_clauses": n_clauses,
        "n_pass": sum(1 for item in results if item["status"] == "pass"),
        "n_fail": len(gate_failures),
        "n_warn": sum(1 for item in results if item["status"] == "warn"),
        "n_warn_failures": len(warn_failures),
        "n_skipped": sum(1 for item in results if item["status"] == "skipped"),
        "n_gate_clauses": n_gate_clauses,
        "n_evaluable": n_evaluable,
        "gate_mode": gate_mode,
        "gate_failures": gate_failures,
        "warn_failures": warn_failures,
        "warnings": warnings,
        "note": note,
    }

    # Exit code: unusable evidence (2) > gate failure (1) > pass (0). Warn-level
    # misses are reported but never move the exit code.
    if draft_validity["status"] != "ok":
        exit_code = EXIT_ERROR
    elif gate_failures:
        exit_code = EXIT_GATE_FAIL
    else:
        exit_code = EXIT_OK

    return {
        "schema_version": VALIDATOR_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "contract": {
            "source_file": contract_source,
            "schema_version": contract.get("schema_version"),
            "corpus_id": generated_from.get("corpus_id", generated_from.get("corpus_fingerprint")),
            "summary_sha256": generated_from.get("summary_sha256"),
            "gate_mode": gate_mode,
        },
        "draft": {
            "source_file": draft_name,
            "n_chars": len(draft_text),
            "n_lines": n_lines,
            "n_paragraph_blocks": len(paragraph_blocks),
            "nfc_normalized": True,
        },
        "draft_validity": draft_validity,
        "draft_language": draft_language,
        "clauses": results,
        "summary": summary,
        "assessment_policy": {"aggregate": None, "policy": "per_clause_only"},
        "exit_code": exit_code,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_report(result: dict) -> str:
    lines: list[str] = []
    contract_info = result["contract"]
    lines.append(f"[validate_draft] contract={contract_info.get('source_file')} "
                 f"schema={contract_info.get('schema_version')} clauses={result['summary']['n_clauses']}")
    lines.append(f"[validate_draft] draft={result['draft'].get('source_file')} "
                 f"lines={result['draft']['n_lines']} chars={result['draft']['n_chars']} "
                 f"nfc=NFC")
    validity = result.get("draft_validity") or {}
    gate_policy = (f"gate_mode={result['summary'].get('gate_mode')} "
                   f"gate_clauses={result['summary'].get('n_gate_clauses')}")
    if validity.get("status") == "language_unsupported":
        language = result.get("draft_language") or {}
        lines.append("[validate_draft] UNSUPPORTED LANGUAGE (exit 2): language="
                     f"{language.get('language')} cjk_ratio={language.get('cjk_ratio')} "
                     f"supported={language.get('supported')}")
        lines.append("[validate_draft] " + str(language.get("note") or UNSUPPORTED_LANGUAGE_NOTE))
        lines.append("[validate_draft] no verdict was produced: an unsupported language is outside "
                     "the metrics' scope, so no gate/warn evaluation was performed")
    elif validity.get("status") == "insufficient_evidence":
        lines.append(f"[validate_draft] INSUFFICIENT EVIDENCE (exit 2): "
                     + "; ".join(validity.get("reasons") or []))
        lines.append(f"[validate_draft] {gate_policy}; "
                     f"alpha_tokens={validity.get('alpha_tokens')} "
                     f"({validity.get('tokenizer')}); "
                     f"evaluable={validity.get('n_evaluable')}/{validity.get('n_clauses')}")
        skipped = validity.get("skipped_reasons") or {}
        if skipped:
            lines.append("[validate_draft] skipped reasons: "
                         + ", ".join(f"{mid}={reason}" for mid, reason in sorted(skipped.items())))
    else:
        lines.append(f"[validate_draft] draft validity=ok ({gate_policy}; "
                     f"alpha_tokens={validity.get('alpha_tokens')}; "
                     f"evaluable={validity.get('n_evaluable')}/{validity.get('n_clauses')})")
    for clause in result["clauses"]:
        actual = "null" if clause["actual"] is None else f"{clause['actual']:g}"
        target = "null" if clause["target"] is None else "[" + ", ".join(f"{v:g}" for v in clause["target"]) + "]"
        deviation = "null" if clause["deviation"] is None else f"{clause['deviation']:g}"
        evidence = ",".join(str(line) for line in clause["evidence_lines"]) or "-"
        reason = clause.get("status_reason")
        suffix = f" reason={reason}" if reason else ""
        lines.append(
            f"  {clause['id']:<16} {clause['metric']:<12} actual={actual:<12} target={target:<20} "
            f"deviation={deviation:<10} {clause['level']:<5} {clause['status']:<8} lines=[{evidence}]{suffix}"
        )
    summary = result["summary"]
    lines.append(f"[validate_draft] pass={summary['n_pass']} fail={summary['n_fail']} "
                 f"warn={summary['n_warn']} skipped={summary['n_skipped']}")
    if summary.get("note"):
        lines.append("[validate_draft] NO GATE CLAUSES (warn-only): " + summary["note"])
    if summary["warn_failures"]:
        lines.append(f"[validate_draft] WARN FAILURES ({summary['n_warn_failures']}, "
                     "warn-level misses never change the exit code): "
                     + ", ".join(summary["warn_failures"]))
    for warning in summary["warnings"]:
        lines.append(f"  ! {warning}")
    if summary["gate_failures"]:
        lines.append("[validate_draft] GATE FAILED: " + ", ".join(summary["gate_failures"]))
    return "\n".join(lines) + "\n"


def _json_text(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a draft against _writing_contract.yaml (I9, no LLM).",
    )
    parser.add_argument("--contract", required=True, help="path to _writing_contract.yaml")
    parser.add_argument("--draft", required=True, help="path to the draft (Markdown)")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="write the machine-readable result to this path ('-' for stdout)")
    parser.add_argument("--compute-module", default=None,
                        help="inject a metrics provider module exposing compute(text) -> metrics "
                             "(default: text_metrics.compute_text_metrics + lexicon_loader)")
    parser.add_argument("--language-module", default=None, metavar="MODULE",
                        help="inject a language detector module exposing detect_language(text) "
                             "(default: text_metrics.detect_language)")
    parser.add_argument("--min-alpha-tokens", type=int, default=MIN_ALPHA_TOKENS,
                        help="drafts below this alpha-token count are insufficient evidence "
                             f"(default {MIN_ALPHA_TOKENS}); 0 disables the guard")
    parser.add_argument("--min-evaluable-ratio", type=float, default=MIN_EVALUABLE_RATIO,
                        help="minimum share of clauses that must be evaluable "
                             f"(default {MIN_EVALUABLE_RATIO}); 0 disables the guard")
    parser.add_argument("--require-language-detector", action="store_true",
                        help="refuse (exit 2) when text_metrics.detect_language is unavailable, "
                             "instead of validating without checking the language scope")
    parser.add_argument("--quiet", action="store_true", help="suppress the human-readable report")
    args = parser.parse_args(argv)

    contract_path = Path(args.contract)
    if not contract_path.is_file():
        print(f"[validate_draft] error: contract not found: {contract_path}", file=sys.stderr)
        return EXIT_ERROR
    draft_path = Path(args.draft)
    if not draft_path.is_file():
        print(f"[validate_draft] error: draft not found: {draft_path}", file=sys.stderr)
        return EXIT_ERROR

    try:
        contract = load_contract(contract_path)
        draft_text = draft_path.read_text(encoding="utf-8")
        compute = load_compute_module(args.compute_module) if args.compute_module else None
        language_detector = (load_language_detector(args.language_module, required=True)
                             if args.language_module else None)
        result = validate(
            contract,
            draft_text,
            compute,
            draft_name=draft_path.name,
            contract_name=contract_path.name,
            min_alpha_tokens=args.min_alpha_tokens,
            min_evaluable_ratio=args.min_evaluable_ratio,
            language_detector=language_detector,
            require_language_detector=args.require_language_detector,
        )
    except (ValidatorError, build_contract.YamlError, OSError, UnicodeDecodeError) as exc:
        print(f"[validate_draft] error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    exit_code = result["exit_code"]
    report = render_report(result)
    if args.json_out == "-":
        sys.stdout.write(_json_text(result))
        if not args.quiet:
            sys.stderr.write(report)
    else:
        if args.json_out:
            json_path = Path(args.json_out)
            try:
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(_json_text(result), encoding="utf-8")
            except OSError as exc:
                print(f"[validate_draft] error: cannot write {json_path}: {exc}", file=sys.stderr)
                return EXIT_ERROR
        if not args.quiet:
            sys.stdout.write(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
