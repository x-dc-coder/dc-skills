#!/usr/bin/env python3
"""Writing-contract generator (INTERFACES.md v1 section 5, Issue I8).

Derives a per-metric writing contract for the paper-metrics skill from a corpus
summary produced by profile_papers.py (_corpus_summary.json, schema 2.0,
INTERFACES.md section 4.4).

Design rules (frozen):
  * Pure stdlib. No LLM / network anywhere on the OBSERVED path (red line 1).
  * Targets come exclusively from the corpus quantiles: target = [p25, p75],
    scale = IQR. When IQR == 0, or the statistics are unavailable, the clause is
    downgraded to level=warn and target is emitted as null.
  * Deterministic: identical input bytes -> identical output bytes. No
    timestamps, no absolute paths, no host names; keys are emitted in a fixed
    order and floats are rounded to 6 decimals.
  * Every clause carries provenance {source, n_valid, iqr, generated_by}.
  * generated_from keeps two distinct identities: corpus_id is the profiler's
    corpus content fingerprint (summary["corpus_id"]; null when absent, with a
    MISSING_CORPUS_ID contract warning) and summary_sha256 is the hash of the
    source _corpus_summary.json file bytes. Never conflate them: only the pair
    (corpus_id, summary_sha256) makes clause -> corpus -> input-sha256 traceable.
  * Additivity: quantiles are not additive, so derived metrics (see DERIVED_METRICS)
    take their target from the SUM of their component targets, with a post-build
    self-check recorded under derivation.rules. A derived clause degrades whenever
    one of its components degrades, so a draft can never face two incompatible bands.
  * Gate strictness is explicit: --gate-mode warn-only (default) emits every
    quantile-derived clause as level=warn, because a corpus p25/p75 band only
    describes the middle 50% of the sample -- gating on it fails almost every
    published paper (joint probability) and has no discriminating power.
    --gate-mode quantile-gate restores level=gate and is an explicit opt-in.
    Either way the recorded gate_mode tells the reader how strict the contract is.
  * Baseline-leakage guard: --holdout <paper_key> (repeatable) re-aggregates the
    corpus from _per_paper_metrics.jsonl with those papers excluded, using
    profile_papers.aggregate_corpus (the profiler's own math, no second
    implementation), and derives the quantiles from the filtered records. If the
    jsonl is missing the builder fails instead of silently keeping the held-out
    papers inside the target band.

The module also hosts the minimal deterministic YAML emitter/parser used by
validate_draft.py (same write scope, no extra file, no third-party dependency).

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/build_contract.py \
        --summary <dir>/_corpus_summary.json --out <dir>/_writing_contract.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

CONTRACT_SCHEMA_VERSION = "1.0"
BUILDER_VERSION = "1.0"
DEFAULT_GENERATED_BY = "build_contract.py"
DEFAULT_MIN_N_VALID = 5
PROVENANCE_SOURCE = "corpus_quantile"
CLAUSE_DIRECTION = "two_sided"

GATE_MODE_WARN_ONLY = "warn-only"
GATE_MODE_QUANTILE_GATE = "quantile-gate"
GATE_MODES = (GATE_MODE_WARN_ONLY, GATE_MODE_QUANTILE_GATE)
DEFAULT_GATE_MODE = GATE_MODE_WARN_ONLY

# Derived metrics (frozen identities from INTERFACES.md section 3). Corpus
# quantiles are NOT additive: p25(a + b) != p25(a) + p25(b), so sampling each
# column independently can produce a contract whose total-density band is
# incompatible with its own components (a draft can pass all three component
# gates and still fail the total). Targets of derived metrics are therefore the
# SUM of their component targets. Never guess this from metric names: the table
# below is the single source of truth.
DERIVED_METRICS: dict[str, tuple[str, ...]] = {
    "M-CONN-30": ("M-CONN-30c", "M-CONN-30k", "M-CONN-30r"),
}
DERIVED_SUM_SOURCE = "derived_sum_of_components"
DERIVATION_NOTE = ("corpus quantiles are not additive: a derived metric target is the sum of its "
                   "component targets (see derivation.rules), never an independent quantile")
# Reasons that mark a clause as degraded by the data itself (as opposed to a
# policy choice such as gate_mode_warn_only).
DATA_DEGRADED_REASONS = frozenset({
    "stats_null", "iqr_zero", "inverted_quantiles", "n_valid_below_min",
})

PER_PAPER_JSONL_NAME = "_per_paper_metrics.jsonl"
SUMMARY_SOURCE_SUMMARY = "corpus_summary"
SUMMARY_SOURCE_JSONL = "per_paper_jsonl"
N_LT_5_NVALID = 5

EXIT_OK = 0
EXIT_ERROR = 2

_INT_RE = re.compile(r"^[+-]?\d+$")
_PLAIN_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


class BuildContractError(Exception):
    """Raised when a corpus summary cannot be turned into a writing contract."""


class YamlError(Exception):
    """Raised when the emitted YAML subset cannot be parsed back."""


# ---------------------------------------------------------------------------
# Deterministic minimal YAML emitter (block mappings/sequences + flow lists)
# ---------------------------------------------------------------------------

def _round6(value: float) -> float:
    return round(float(value), 6)


def _fmt_float(value: float) -> str:
    rounded = _round6(value)
    if rounded != rounded or rounded in (float("inf"), float("-inf")):
        raise BuildContractError("non-finite float cannot be serialized")
    text = repr(rounded)
    if "e" in text or "E" in text:
        text = format(rounded, ".10f").rstrip("0")
        if text.endswith("."):
            text += "0"
    if "." not in text:
        text += ".0"
    return text


def _fmt_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _fmt_float(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise BuildContractError(f"unsupported scalar type: {type(value).__name__}")


def _fmt_key(key: str) -> str:
    """Emit a bare YAML key when it is unambiguous, otherwise a quoted one."""
    if _PLAIN_KEY_RE.match(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _is_flow_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _is_flow(value: Any) -> bool:
    if _is_flow_scalar(value):
        return True
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return True
        return len(value) <= 10 and all(_is_flow_scalar(item) for item in value)
    return False


def _inline(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt_scalar(item) for item in value) + "]"
    return _fmt_scalar(value)


def _emit(value: Any, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(pad + "{}")
            return
        for key, item in value.items():
            if not isinstance(key, str):
                raise BuildContractError(f"mapping keys must be strings, got {type(key).__name__}")
            label = _fmt_key(key)
            if _is_flow(item):
                lines.append(f"{pad}{label}: {_inline(item)}")
            elif isinstance(item, (dict, list, tuple)) and len(item):
                lines.append(f"{pad}{label}:")
                _emit(item, indent + 2, lines)
            else:
                raise BuildContractError(f"unsupported value for key {key!r}: {type(item).__name__}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            if _is_flow(item):
                lines.append(f"{pad}- {_inline(item)}")
            elif isinstance(item, dict) and item:
                sub: list[str] = []
                _emit(item, indent + 2, sub)
                head = sub[0][indent + 2:]
                lines.append(f"{pad}- {head}")
                lines.extend(sub[1:])
            elif isinstance(item, (list, tuple)) and item:
                lines.append(pad + "-")
                _emit(item, indent + 2, lines)
            else:
                raise BuildContractError(f"unsupported sequence item: {type(item).__name__}")
    else:
        raise BuildContractError(f"cannot emit bare {type(value).__name__}")


def dump_yaml(obj: Any) -> str:
    """Serialize a JSON-shaped object to deterministic YAML (subset)."""
    lines: list[str] = []
    _emit(obj, 0, lines)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Minimal YAML parser for the subset emitted above
# ---------------------------------------------------------------------------

def _split_kv(text: str) -> tuple[str, str, str]:
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            return text[:i], ":", text[i + 1:].strip()
        i += 1
    return text, "", ""


def _parse_flow(text: str) -> list[Any]:
    body = text.strip()
    if not (body.startswith("[") and body.endswith("]")):
        raise YamlError(f"malformed flow sequence: {text!r}")
    inner = body[1:-1].strip()
    if not inner:
        return []
    items: list[Any] = []
    quote: str | None = None
    depth = 0
    current = ""
    for ch in inner:
        if quote is not None:
            current += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            current += ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(_parse_scalar(current))
            current = ""
            continue
        current += ch
    items.append(_parse_scalar(current))
    return items


def _parse_scalar(text: str) -> Any:
    body = text.strip()
    if body == "":
        return None
    if body.startswith("["):
        return _parse_flow(body)
    if body.startswith('"'):
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise YamlError(f"malformed double-quoted scalar: {body!r}") from exc
    if body.startswith("'"):
        if not body.endswith("'") or len(body) < 2:
            raise YamlError(f"malformed single-quoted scalar: {body!r}")
        return body[1:-1].replace("''", "'")
    lowered = body.lower()
    if lowered in ("null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INT_RE.match(body):
        return int(body)
    if _FLOAT_RE.match(body):
        return float(body)
    return body


def _parse_map(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while pos < len(lines):
        ind, content, lineno = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"unexpected indentation at line {lineno}")
        if content == "-" or content.startswith("- "):
            break
        key, sep, rest = _split_kv(content)
        if not sep:
            raise YamlError(f"expected 'key: value' at line {lineno}: {content!r}")
        key = str(_parse_scalar(key))
        pos += 1
        if rest == "":
            if pos < len(lines) and lines[pos][0] > indent:
                value, pos = _parse_node(lines, pos, lines[pos][0])
            elif pos < len(lines) and lines[pos][0] == indent and (
                lines[pos][1] == "-" or lines[pos][1].startswith("- ")
            ):
                value, pos = _parse_seq(lines, pos, indent)
            else:
                value = None
        else:
            value = _parse_scalar(rest)
        if key in out:
            raise YamlError(f"duplicate key {key!r} at line {lineno}")
        out[key] = value
    return out, pos


def _parse_seq(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while pos < len(lines):
        ind, content, lineno = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"unexpected indentation at line {lineno}")
        if not (content == "-" or content.startswith("- ")):
            break
        body = content[1:].strip()
        if body == "":
            pos += 1
            if pos < len(lines) and lines[pos][0] > indent:
                item, pos = _parse_node(lines, pos, lines[pos][0])
            else:
                item = None
        else:
            key, sep, _rest = _split_kv(body)
            if sep:
                lines[pos] = (indent + 2, body, lineno)
                item, pos = _parse_map(lines, pos, indent + 2)
            else:
                item = _parse_scalar(body)
                pos += 1
        items.append(item)
    return items, pos


def _parse_node(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[Any, int]:
    content = lines[pos][1]
    if content == "-" or content.startswith("- "):
        return _parse_seq(lines, pos, indent)
    return _parse_map(lines, pos, indent)


def load_yaml(text: str) -> Any:
    """Parse the deterministic YAML subset emitted by dump_yaml()."""
    lines: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise YamlError(f"tabs are not allowed in indentation (line {lineno})")
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        lines.append((indent, stripped.rstrip(), lineno))
    if not lines:
        return None
    value, pos = _parse_node(lines, 0, lines[0][0])
    if pos != len(lines):
        raise YamlError(f"trailing content at line {lines[pos][2]}")
    return value


# ---------------------------------------------------------------------------
# Contract construction
# ---------------------------------------------------------------------------

def _round6_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _round6(float(value))


def _component_data_degraded(clause: dict) -> bool:
    """True when this component cannot support a gate (null target or bad data)."""
    return clause.get("target") is None or clause.get("level_reason") in DATA_DEGRADED_REASONS


def _summary_corpus_id(summary: dict) -> str | None:
    """Corpus content fingerprint published by the profiler (never the file hash)."""
    candidate = summary.get("corpus_id")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


def _parse_per_paper_records(raw: bytes, name: str) -> list[dict]:
    """Parse _per_paper_metrics.jsonl (INTERFACES.md section 4.3) into records."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildContractError(f"{name}: not valid UTF-8: {exc}") from exc
    records: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BuildContractError(f"{name}:{lineno}: invalid JSON: {exc}") from exc
        if (not isinstance(row, dict) or not isinstance(row.get("paper_key"), str)
                or not isinstance(row.get("metrics"), dict)):
            raise BuildContractError(f"{name}:{lineno}: expected {{paper_key, metrics}}")
        records.append(row)
    if not records:
        raise BuildContractError(f"{name}: no per-paper records")
    return records


def _import_corpus_aggregator():
    """The profiler owns corpus aggregation; this builder never reimplements it."""
    try:
        profiler = importlib.import_module("profile_papers")
    except ImportError as exc:
        raise BuildContractError(
            f"--holdout re-aggregates with profile_papers.aggregate_corpus: {exc}"
        ) from exc
    aggregate = getattr(profiler, "aggregate_corpus", None)
    if not callable(aggregate):
        raise BuildContractError(
            "profile_papers.aggregate_corpus is unavailable; refusing to re-aggregate locally"
        )
    return aggregate


def apply_holdout(summary: dict, summary_path: Path, holdout: list[str]) -> tuple[dict, str]:
    """Re-aggregate the corpus without the held-out papers (anti-leakage path).

    Returns the filtered summary (same shape as _corpus_summary.json) and the
    sha256 of the per-paper jsonl that was actually aggregated. A missing jsonl
    or an unknown paper_key is a hard error: falling back to the full-corpus
    summary would leave the held-out papers inside their own target band.
    """
    held = sorted({key for key in holdout if key})
    if not held:
        raise BuildContractError("--holdout given without a paper_key")
    jsonl_path = summary_path.parent / PER_PAPER_JSONL_NAME
    if not jsonl_path.is_file():
        raise BuildContractError(
            f"--holdout needs {PER_PAPER_JSONL_NAME} next to {summary_path.name} "
            f"(looked for {jsonl_path}); refusing to fall back to the full-corpus summary, "
            "which would keep the held-out papers inside the target band"
        )
    raw = jsonl_path.read_bytes()
    jsonl_sha = hashlib.sha256(raw).hexdigest()
    records = _parse_per_paper_records(raw, jsonl_path.name)
    known = {row["paper_key"] for row in records}
    unknown = [key for key in held if key not in known]
    if unknown:
        raise BuildContractError(
            f"holdout paper_key(s) not present in {jsonl_path.name}: " + ", ".join(unknown)
        )
    held_set = set(held)
    kept = [row for row in records if row["paper_key"] not in held_set]
    if not kept:
        raise BuildContractError("--holdout removes every profiled paper; nothing left to aggregate")
    aggregate = _import_corpus_aggregator()
    filtered = aggregate(kept, corpus_id=summary.get("corpus_id"))
    if not isinstance(filtered, dict) or not isinstance(filtered.get("metrics"), dict):
        raise BuildContractError("profile_papers.aggregate_corpus returned an unexpected value")
    return filtered, jsonl_sha


#: Metrics a Markdown draft can recompute, declared by id instead of inferred from
#: scope.  scope == ["prose"] is the right proxy for most metrics, but it is WRONG
#: for the reference metrics (scope ["prose","references"]): a draft has a reference
#: list, so M-REFAGE-53 / M-REFLINK-54 are recomputable despite a non-prose scope.
#: This allow-list is the single source of truth for that exception.
DRAFT_CHECKABLE_METRICS: frozenset[str] = frozenset({
    "M-REFAGE-53", "M-REFLINK-54",
})


def _is_toolchain_defect(stats: object) -> bool:
    """Does this metric measure the extraction chain rather than the writing?

    class == "toolchain_defect" (LaTeX residue, a missing table body, an unusable
    extracted raster) is never a writing target: promoting one into a contract
    would tell the author to imitate our parser's defect (issue #18-9).
    """
    return isinstance(stats, dict) and stats.get("class") == "toolchain_defect"


def _is_draft_checkable(metric_id: str, stats: object) -> bool:
    """May this metric appear in a DRAFT contract?

    Draft-checkability is declared per metric id, not inferred from scope alone:
    prose metrics (scope == ["prose"]) are checkable, the reference metrics are
    checkable via DRAFT_CHECKABLE_METRICS, and anything with a table/figure scope is
    not.  A summary written before scope existed carries no scope field; those are
    treated as prose so old contracts stay buildable.
    """
    if metric_id in DRAFT_CHECKABLE_METRICS:
        return True
    if not isinstance(stats, dict) or _is_toolchain_defect(stats):
        return False
    scope = stats.get("scope")
    if not isinstance(scope, list) or not scope:
        # Absent or empty means "unspecified" (legacy summaries): produce the clause
        # rather than silently dropping a metric, because the profile already reports
        # METRIC_SCOPE_MISSING for it.
        return True
    # Exactly prose.  A mixed-scope metric (prose + tables, e.g. table density) is NOT
    # draft-checkable either: a Markdown draft carries no tables, so its clause could
    # only ever come out "skipped".
    return set(scope) <= {"prose"}


def build_contract(
    summary: dict,
    *,
    summary_sha256: str,
    source_file: str,
    generated_by: str = DEFAULT_GENERATED_BY,
    min_n_valid: int = DEFAULT_MIN_N_VALID,
    metrics: list[str] | None = None,
    gate_mode: str = DEFAULT_GATE_MODE,
    holdout: list[str] | None = None,
    summary_source: str = SUMMARY_SOURCE_SUMMARY,
    per_paper_jsonl_sha256: str | None = None,
    n_papers_total: int | None = None,
    adaptive_widening: bool = False,
) -> dict:
    """Build the writing-contract mapping from a _corpus_summary.json mapping."""
    if min_n_valid < 1:
        raise BuildContractError(f"min_n_valid must be >= 1, got {min_n_valid}")
    if gate_mode not in GATE_MODES:
        raise BuildContractError(f"gate_mode must be one of {GATE_MODES}, got {gate_mode!r}")
    metrics_block = summary.get("metrics")
    if not isinstance(metrics_block, dict) or not metrics_block:
        raise BuildContractError("summary has no non-empty 'metrics' object")
    excluded: list[str] = []
    defects: list[str] = []
    if metrics is not None:
        missing = [mid for mid in metrics if mid not in metrics_block]
        if missing:
            raise BuildContractError(
                "requested metrics not present in summary: " + ", ".join(sorted(missing))
            )
        defects = sorted(mid for mid in metrics if _is_toolchain_defect(metrics_block[mid]))
        if defects:
            raise BuildContractError(
                "class=toolchain_defect metrics are never accepted by a writing "
                "contract (they measure the extraction chain): " + ", ".join(defects)
            )
        ids = list(metrics)
    else:
        # Stream metrics (figures/tables/...) cannot be graded against a text draft:
        # a Markdown draft has no block inventory, so such a clause could only ever
        # come out "skipped".  They stay in the profile and out of the contract.
        ids = sorted(mid for mid in metrics_block
                     if _is_draft_checkable(mid, metrics_block[mid]))
        excluded = sorted(set(metrics_block) - set(ids))
        defects = sorted(mid for mid in excluded
                         if _is_toolchain_defect(metrics_block.get(mid)))

    clauses: list[dict] = []
    contract_warnings: list[dict] = []
    n_valids: list[int] = []
    if excluded:
        # Transparency: a metric quietly dropped from a contract is as bad as one
        # silently included; the profile keeps all of them.
        contract_warnings.append({
            "code": "NON_PROSE_METRICS_EXCLUDED", "metric": None,
            "detail": "kept in the profile, not draft-checkable: " + ", ".join(excluded),
        })
    if defects:
        # A separate code, not folded into the one above: "no block inventory in a
        # Markdown draft" and "this measures our converter, not the writing" are
        # different statements, and only the second is a red line (issue #18-9).
        contract_warnings.append({
            "code": "TOOLCHAIN_DEFECT_METRICS_EXCLUDED", "metric": None,
            "detail": ("never draft-checkable (class=toolchain_defect, they measure the "
                       "extraction chain): " + ", ".join(defects)),
        })

    corpus_id = _summary_corpus_id(summary)
    if corpus_id is None:
        contract_warnings.append({
            "code": "MISSING_CORPUS_ID",
            "metric": None,
            "detail": "summary has no corpus_id; generated_from.corpus_id is null and the "
                      "clause -> corpus -> input-sha256 chain cannot be verified",
        })

    for mid in ids:
        stats = metrics_block.get(mid)
        if not isinstance(stats, dict):
            raise BuildContractError(f"metrics[{mid!r}] must be an object")
        unit = stats.get("unit")
        n_valid = stats.get("n_valid")
        if isinstance(n_valid, bool) or not isinstance(n_valid, int):
            n_valid = None
        else:
            n_valids.append(n_valid)
        p25 = _round6_or_none(stats.get("p25"))
        p75 = _round6_or_none(stats.get("p75"))
        iqr = _round6_or_none(stats.get("iqr"))

        target: list[float] | None = None
        level_reason: str | None = None
        if p25 is None or p75 is None:
            level_reason = "stats_null"
            contract_warnings.append({
                "code": "STATS_NULL",
                "metric": mid,
                "detail": "p25/p75 unavailable; target=null and clause downgraded to warn",
            })
        elif iqr is None or iqr == 0.0:
            level_reason = "iqr_zero"
            contract_warnings.append({
                "code": "IQR_ZERO",
                "metric": mid,
                "detail": "iqr==0; target=null and clause downgraded to warn",
            })
        elif p25 > p75:
            level_reason = "inverted_quantiles"
            contract_warnings.append({
                "code": "INVERTED_QUANTILES",
                "metric": mid,
                "detail": f"p25({p25}) > p75({p75}); target=null and clause downgraded to warn",
            })
        else:
            target = [p25, p75]

        if n_valid is not None and n_valid < min_n_valid:
            # INTERFACES.md section 4.4 spells the thin-sample code N_LT_5;
            # N_LT_MIN is kept for a non-default --min-n-valid threshold.
            code = "N_LT_5" if n_valid < N_LT_5_NVALID else "N_LT_MIN"
            contract_warnings.append({
                "code": code,
                "metric": mid,
                "detail": f"n_valid={n_valid} < {min_n_valid}; clause downgraded to warn",
            })
            if adaptive_widening:
                contract_warnings.append({
                    "code": "CONTRACT_LOW_SAMPLE_WARNING",
                    "metric": mid,
                    "detail": f"n_valid={n_valid} < {min_n_valid}; 样本量过小，目标区间自动启用自适应宽放 [p10, p75+15%IQR]",
                })
                # Adaptive widening for low sample size
                if target is not None and iqr is not None and iqr > 0:
                    p10_cand = stats.get("p10")
                    p90_cand = stats.get("p90")
                    low = _round6(float(p10_cand)) if p10_cand is not None else _round6(max(0.0, target[0] - 0.15 * iqr))
                    high = _round6(float(p90_cand)) if p90_cand is not None else _round6(target[1] + 0.15 * iqr)
                    if low <= high:
                        target = [low, high]
        if unit is None:
            contract_warnings.append({
                "code": "UNIT_MISSING",
                "metric": mid,
                "detail": "unit missing in summary; clause unit is null",
            })

        level = "gate" if (target is not None and n_valid is not None and n_valid >= min_n_valid) else "warn"
        if level_reason is None and level == "warn":
            level_reason = "n_valid_below_min"
        if gate_mode == GATE_MODE_WARN_ONLY:
            # Numbers and provenance are unchanged; only the gate/warn label moves.
            level = "warn"
            if level_reason is None:
                level_reason = "gate_mode_warn_only"

        clauses.append({
            "id": f"C-{mid}",
            "metric": mid,
            "level": level,
            "target": target,
            "unit": unit,
            "direction": CLAUSE_DIRECTION,
            "provenance": {
                "source": PROVENANCE_SOURCE,
                "n_valid": n_valid,
                "iqr": iqr,
                "generated_by": generated_by,
            },
            "level_reason": level_reason,
        })

    # --- derived metrics: component-sum targets, never independent quantiles ----
    by_metric = {clause["metric"]: clause for clause in clauses}
    derivation_rules: list[dict] = []
    for metric_id, components in sorted(DERIVED_METRICS.items()):
        clause = by_metric.get(metric_id)
        if clause is None:
            continue
        component_clauses = [by_metric.get(component) for component in components]
        missing_components = [component for component, found in zip(components, component_clauses)
                              if found is None]
        present_components = [found for found in component_clauses if found is not None]

        degraded = bool(missing_components)
        expected_target: list[float] | None = None
        if missing_components:
            clause["target"] = None
            clause["level"] = "warn"
            clause["level_reason"] = "derived_component_missing"
            contract_warnings.append({
                "code": "DERIVED_COMPONENT_MISSING",
                "metric": metric_id,
                "detail": "component clause(s) absent from this contract: "
                          + ", ".join(missing_components)
                          + "; target set to null instead of an independent quantile",
            })
        elif any(component["target"] is None for component in present_components):
            # a component has no band (e.g. iqr == 0): nothing can be summed
            clause["target"] = None
            degraded = True
        elif clause.get("target") is None and clause.get("level_reason") in DATA_DEGRADED_REASONS:
            # the derived column itself has no usable spread: keep the frozen
            # "iqr == 0 -> target null" rule and record why nothing was derived
            degraded = True
        else:
            expected_target = [
                round(sum(component["target"][0] for component in present_components), 6),
                round(sum(component["target"][1] for component in present_components), 6),
            ]
            clause["target"] = expected_target
            if any(_component_data_degraded(component) for component in present_components):
                degraded = True

        if degraded:
            clause["level"] = "warn"
            if clause.get("level_reason") not in DATA_DEGRADED_REASONS:
                clause["level_reason"] = (
                    "derived_component_missing" if missing_components else "derived_component_degraded"
                )

        check = "not_applicable" if expected_target is None else (
            "ok" if clause["target"] == expected_target else "mismatch"
        )
        if check == "mismatch":
            contract_warnings.append({
                "code": "DERIVED_TARGET_MISMATCH",
                "metric": metric_id,
                "detail": f"derived target {clause['target']} != sum of component targets "
                          f"{expected_target}; clause downgraded to warn",
            })
            clause["level"] = "warn"
            clause["level_reason"] = "derived_target_mismatch"

        clause["provenance"]["source"] = DERIVED_SUM_SOURCE
        clause["provenance"]["derived_from"] = list(components)
        clause["derivation"] = {
            "rule": "sum_of_components",
            "derived_from": list(components),
            "component_targets": {component["metric"]: component["target"]
                                  for component in present_components},
            "expected_target": expected_target,
            "check": check,
        }
        derivation_rules.append({
            "metric": metric_id,
            "components": list(components),
            "source": DERIVED_SUM_SOURCE,
            "expected_target": expected_target,
            "check": check,
        })

    raw_warnings = summary.get("corpus_warnings")
    corpus_warnings: list[dict] = []
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if isinstance(warning, dict):
                corpus_warnings.append({
                    "code": str(warning.get("code", "UNKNOWN")),
                    "metric": warning.get("metric"),
                    "detail": str(warning.get("detail", "")),
                })
    corpus_warnings.sort(key=lambda w: (w["code"], str(w["metric"]), w["detail"]))

    holdout_list = sorted({key for key in (holdout or [])})
    n_papers_after_holdout = summary.get("n_papers")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "generated_from": {
            "summary_schema_version": summary.get("schema_version"),
            "summary_sha256": summary_sha256,
            "corpus_id": corpus_id,
            "per_paper_jsonl_sha256": per_paper_jsonl_sha256,
            "source_file": source_file,
            "summary_source": summary_source,
            "gate_mode": gate_mode,
            "analysis_unit": summary.get("analysis_unit"),
            "weight_mode": summary.get("weight_mode"),
            "n_papers": n_papers_total if isinstance(n_papers_total, int) else n_papers_after_holdout,
            "n_papers_after_holdout": n_papers_after_holdout,
            "holdout": holdout_list,
            "n_valid_min": min(n_valids) if n_valids else None,
            "n_valid_max": max(n_valids) if n_valids else None,
            "min_n_valid": min_n_valid,
            "provenance_source": PROVENANCE_SOURCE,
        },
        "clauses": clauses,
        "derivation": {
            "note": DERIVATION_NOTE,
            "rules": derivation_rules,
        },
        "corpus_warnings": corpus_warnings,
        "contract_warnings": contract_warnings,
    }


def render_contract(contract: dict) -> str:
    """Contract YAML with a human-readable header (comments are ignored by parsers)."""
    header = [
        "# _writing_contract.yaml -- generated by build_contract.py; do not edit by hand.",
        "# 声明：本契约目标区间仅表示与目标语料写作特征的常模对齐（描述性指标），不构成学术创新性与论文质量的充分裁判。",
        "# Corpus quantiles are NOT additive: the target of a derived metric is the SUM of its",
        "# component targets (see derivation.rules), never an independently sampled quantile.",
        "# gate_mode=warn-only means the quantile clauses are advisory: a passing draft has not",
        "# passed a gate. See gate_mode and derivation.note below.",
    ]
    return "\n".join(header) + "\n" + dump_yaml(contract)


def _load_summary(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildContractError(f"cannot parse summary JSON {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildContractError(f"{path.name}: expected a JSON object")
    return data, sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate _writing_contract.yaml from _corpus_summary.json (I8).",
    )
    parser.add_argument("--summary", required=True, help="path to _corpus_summary.json")
    parser.add_argument("--out", required=True, help="path to write _writing_contract.yaml")
    parser.add_argument("--generated-by", default=DEFAULT_GENERATED_BY,
                        help="value recorded in each clause's provenance.generated_by")
    parser.add_argument("--min-n-valid", type=int, default=DEFAULT_MIN_N_VALID,
                        help="clauses below this n_valid are downgraded to warn (default 5)")
    parser.add_argument("--metrics", default=None,
                        help="comma-separated metric ids to include (default: all in summary)")
    parser.add_argument("--gate-mode", choices=GATE_MODES, default=DEFAULT_GATE_MODE,
                        help="warn-only (default): quantile-derived clauses are warn, never "
                             "gate; quantile-gate: opt in to level=gate clauses")
    parser.add_argument("--holdout", action="append", default=None, metavar="PAPER_KEY",
                        help="exclude this paper_key from the corpus quantiles (repeatable); "
                             f"requires {PER_PAPER_JSONL_NAME} next to --summary, and writes a "
                             "leave-one-out contract (summary_source=per_paper_jsonl)")
    parser.add_argument("--adaptive-widening", action="store_true",
                        help="widen target band for thin samples (n_valid < min_n_valid)")
    parser.add_argument("--quiet", action="store_true", help="suppress the stdout report")
    args = parser.parse_args(argv)

    if args.min_n_valid < 1:
        print(f"[build_contract] error: --min-n-valid must be >= 1, got {args.min_n_valid}", file=sys.stderr)
        return EXIT_ERROR

    summary_path = Path(args.summary)
    if not summary_path.is_file():
        print(f"[build_contract] error: summary not found: {summary_path}", file=sys.stderr)
        return EXIT_ERROR

    metrics = None
    if args.metrics:
        metrics = [mid.strip() for mid in args.metrics.split(",") if mid.strip()]
        if not metrics:
            print("[build_contract] error: --metrics given but empty", file=sys.stderr)
            return EXIT_ERROR

    holdout = sorted({key.strip() for key in (args.holdout or []) if key.strip()})

    try:
        summary, sha = _load_summary(summary_path)
        n_papers_total = summary.get("n_papers")
        summary_source = SUMMARY_SOURCE_SUMMARY
        jsonl_sha = None
        if holdout:
            summary, jsonl_sha = apply_holdout(summary, summary_path, holdout)
            summary_source = SUMMARY_SOURCE_JSONL
        contract = build_contract(
            summary,
            summary_sha256=sha,
            source_file=summary_path.name,
            generated_by=args.generated_by,
            min_n_valid=args.min_n_valid,
            metrics=metrics,
            gate_mode=args.gate_mode,
            holdout=holdout,
            summary_source=summary_source,
            per_paper_jsonl_sha256=jsonl_sha,
            n_papers_total=n_papers_total,
            adaptive_widening=args.adaptive_widening,
        )
        text = render_contract(contract)
    except BuildContractError as exc:
        print(f"[build_contract] error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"[build_contract] error: cannot write {out_path}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not args.quiet:
        gates = sum(1 for clause in contract["clauses"] if clause["level"] == "gate")
        warns = len(contract["clauses"]) - gates
        print(f"[build_contract] {out_path.name}: gate_mode={contract['generated_from']['gate_mode']}, "
              f"{len(contract['clauses'])} clauses ({gates} gate / {warns} warn), "
              f"{len(contract['contract_warnings'])} builder warning(s)")
        if holdout:
            print(f"[build_contract] holdout: {len(holdout)} paper(s) excluded -> "
                  f"n_papers_after_holdout={contract['generated_from']['n_papers_after_holdout']} "
                  f"(summary_source={SUMMARY_SOURCE_JSONL})")
        for warning in contract["contract_warnings"]:
            print(f"  - {warning['code']} {warning['metric']}: {warning['detail']}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
