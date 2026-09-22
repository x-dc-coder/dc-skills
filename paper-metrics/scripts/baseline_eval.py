#!/usr/bin/env python3
"""I12 baseline evaluation harness for the paper-metrics profiler.

What it does (all OBSERVED, zero LLM, pure stdlib):

  1. Runs profile_papers.run_profile() TWICE on the same real corpus into two
     separate temp dirs.
  2. Proves the bit-level reproducibility promise: for the fingerprinted
     artifacts (_domain_profile.json / _per_paper_metrics.jsonl /
     _corpus_summary.json) it records sha256 for both runs and asserts
     byte-identical output. _run_meta.json is explicitly excluded (volatile:
     timestamp / host / elapsed).
  3. Verifies every sampled OBSERVED value can be traced back to the source,
     supporting BOTH frozen evidence forms (INTERFACES.md sec.7) and reporting a
     single merged trace-back rate (target >= 0.95):
       form A (text metrics): text[start:end][:80] == excerpt  -- slice first,
                              truncate after; comparing the full slice would
                              wrongly flag legitimately truncated long spans;
       form B (M-PCNT-25):    content_list.json blocks[block_index] exists and
                              its text starts with excerpt, with the recorded
                              input sha256 re-derived to pin the artifact.
     A sample with neither form is not verifiable and counts as a miss.
     A third party can repeat all of this with any JSON reader -- no model.
  4. Locks a golden value table (per metric: unit / n_valid / mean / sd /
     median / p25 / p75 / iqr / min / max / CI95) so later changes become
     detectable regressions instead of silent drift.
  5. Runs the INTERFACES.md sec.6 acceptance checks that are mechanically
     checkable here and exits 1 if any of them fails.

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/baseline_eval.py \
        --corpus /mnt/e/AllProjects202601/M-PCA/VRP-GPU课题分析/paper-analysis \
        --out /path/to/output/

Outputs:
    <out>/_baseline_report.json     machine-readable report (byte-reproducible)
    <out>/_baseline_report.md       human-readable rendering of the same data
    <out>/_baseline_golden.json     regression baseline for third-party recompute
    <out>/_baseline_run_meta.json   volatile timing/host side-car (NOT compared)

Exit codes: 0 = every acceptance check passed; 1 = at least one failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import profile_papers as prof  # noqa: E402

BASELINE_VERSION = "1.0"
# Mirrors profile_papers.FINGERPRINTED_FILES: _domain_profile.md is included
# because the human-readable view is rendered from the same rounded data as the
# JSON (before that fix the md leaked 17-digit floats and was not byte-stable).
FINGERPRINTED_FILES = (
    "_domain_profile.json",
    "_domain_profile.md",
    "_per_paper_metrics.jsonl",
    "_corpus_summary.json",
)
EXCLUDED_FILES = ("_run_meta.json", "_baseline_run_meta.json")
REQUIRED_METRIC_FIELDS = (
    "value", "n", "denominator", "unit", "state", "method", "metric_spec",
    "evidence", "warnings",
)
MAX_EVIDENCE_PER_METRIC = 3
MIN_EVIDENCE_SAMPLES = 10
# INTERFACES sec.7: merged trace-back rate over evidence forms A (span) and B (block).
MIN_TRACEBACK_RATE = 0.95
_EVIDENCE_EXCERPT_MAX = 80
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# Hashing / IO helpers
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


MODULE_FILES = ("profile_papers.py", "text_metrics.py", "lexicon_loader.py",
                "baseline_eval.py")


def module_sha256() -> dict:
    """sha256 of every module that can change a number.

    The golden table is only recomputable if a third party knows which code
    revision produced it: metric_spec_version and the lexicon fingerprint pin the
    spec and the word lists, these hashes pin the implementation.
    """
    out: dict[str, str] = {}
    for name in MODULE_FILES:
        digest = sha256_file(_SCRIPTS_DIR / name)
        if digest:
            out[name] = digest
    return out


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Step 2: reproducibility of two independent runs
# ---------------------------------------------------------------------------

def compare_fingerprints(dir_a: Path, dir_b: Path) -> dict:
    """sha256 of each fingerprinted artifact in both runs + byte-identity flag."""
    out: dict[str, dict] = {}
    for name in FINGERPRINTED_FILES:
        ha = sha256_file(dir_a / name)
        hb = sha256_file(dir_b / name)
        out[name] = {
            "sha256_run1": ha,
            "sha256_run2": hb,
            "present_run1": ha is not None,
            "present_run2": hb is not None,
            "byte_identical": ha is not None and ha == hb,
        }
    return out


def scan_volatile_leaks(directory: Path, corpus_dir: Path) -> list[dict]:
    """Enforce the fingerprint-scope rule: no path / host / timestamp inside it."""
    leaks: list[dict] = []
    needles = {
        "corpus_absolute_path": str(corpus_dir.resolve()),
        "output_absolute_path": str(directory.resolve()),
        "hostname": platform.node(),
    }
    for name in FINGERPRINTED_FILES:
        p = directory / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for code, needle in needles.items():
            if needle and needle in text:
                leaks.append({"file": name, "code": code, "needle": needle})
        if _TIMESTAMP_RE.search(text):
            leaks.append({"file": name, "code": "iso_timestamp",
                          "needle": _TIMESTAMP_RE.search(text).group(0)})
    return leaks


# ---------------------------------------------------------------------------
# Step 3: evidence traceability
# ---------------------------------------------------------------------------

def build_paper_contexts(corpus_dir: Path) -> dict[str, dict]:
    """paper_key -> verification context (canonical text, blocks, provenance).

    Blocks are kept so block-index evidence (INTERFACES sec.7 form B,
    M-PCNT-25) can be checked against the exact content_list.json bytes, and the
    recorded input sha256 is re-derived so the report proves which artifact the
    evidence came from.
    """
    discovery = prof.discover_papers_detailed(corpus_dir)
    contexts: dict[str, dict] = {}
    for paper in discovery.papers:
        expected = None
        for item in (paper.inputs or []):
            if item.get("artifact") == "mineru_content_list":
                expected = item.get("sha256")
                break
        actual = sha256_file(paper.content_list_path)
        contexts[paper.paper_key] = {
            "text": paper.canonical_text(),
            "blocks": paper.load_blocks(),
            "content_list_path": str(paper.content_list_path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "sha256_match": (expected == actual) if expected is not None else None,
        }
    return contexts


def _verify_span(text: str | None, sample: dict) -> tuple[bool | None, str | None]:
    """Form A: excerpt must equal text[start:end][:80] (slice, THEN truncate).

    Comparing the full slice would flag every legitimately truncated long span as
    a mismatch (INTERFACES sec.7 explicitly forbids that).
    """
    if text is None:
        return None, None
    start, end = int(sample["span"][0]), int(sample["span"][1])
    sliced = text[start:end][:_EVIDENCE_EXCERPT_MAX]
    return sliced == sample.get("excerpt"), sliced


def _verify_block(blocks: list[dict] | None, sample: dict) -> tuple[bool | None, str | None]:
    """Form B: blocks[block_index] exists and the named field starts with excerpt.

    A sample may name the field it read ("text" when absent). Figure/table metrics
    keep their evidence in a caption or table_body, so verifying those against the
    block's prose text would either fail or pass vacuously on an empty string.
    """
    if blocks is None:
        return None, None
    idx = sample.get("block_index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(blocks):
        return False, None
    block = blocks[idx]
    field = sample.get("field")
    raw: object = (block.get(field) if isinstance(field, str) and field
                   else block.get("text"))
    if isinstance(raw, list):
        raw = " ".join(str(item) for item in raw)
    block_text = str(raw or "").strip()
    excerpt = sample.get("excerpt") or ""
    return block_text.startswith(excerpt), block_text[:_EVIDENCE_EXCERPT_MAX]


def collect_evidence(records: list[dict], contexts: dict[str, dict]) -> dict:
    """Sample evidence per metric and trace every sample back to its source.

    Both frozen evidence forms are supported and merged into ONE trace-back
    rate (INTERFACES sec.7): form A (span) via text[start:end][:80] == excerpt,
    form B (block_index) via blocks[block_index] starting with excerpt. A sample
    carrying neither form is not verifiable and counts as a miss.
    """
    samples: list[dict] = []
    per_metric: dict[str, int] = {}
    forms = {"span": {"checked": 0, "matched": 0},
             "block": {"checked": 0, "matched": 0},
             "opaque": {"checked": 0, "matched": 0}}
    provenance_checked = 0
    provenance_matched = 0
    for rec in records:
        ctx = contexts.get(rec["paper_key"]) or {}
        text = ctx.get("text")
        blocks = ctx.get("blocks")
        if ctx.get("sha256_match") is not None:
            provenance_checked += 1
            provenance_matched += 1 if ctx["sha256_match"] else 0
        for mid in sorted(rec.get("metrics") or {}):
            if per_metric.get(mid, 0) >= MAX_EVIDENCE_PER_METRIC:
                continue
            metric = rec["metrics"][mid]
            ev = metric.get("evidence") or {}
            for s in (ev.get("sample") or []):
                if per_metric.get(mid, 0) >= MAX_EVIDENCE_PER_METRIC:
                    break
                per_metric[mid] = per_metric.get(mid, 0) + 1
                item = {"metric": mid, "paper_key": rec["paper_key"],
                        "unit": metric.get("unit"),
                        "excerpt": s.get("excerpt")}
                if "span" in s:
                    ok, sliced = _verify_span(text, s)
                    forms["span"]["checked"] += 1
                    forms["span"]["matched"] += 1 if ok else 0
                    item.update({"form": "span",
                                 "span": [int(s["span"][0]), int(s["span"][1])],
                                 "source_slice": sliced, "verified": bool(ok)})
                elif "block_index" in s:
                    ok, block_text = _verify_block(blocks, s)
                    forms["block"]["checked"] += 1
                    forms["block"]["matched"] += 1 if ok else 0
                    item.update({"form": "block", "block_index": s.get("block_index"),
                                 "field": s.get("field"),
                                 "section": s.get("section"),
                                 "source_slice": block_text, "verified": bool(ok)})
                else:
                    forms["opaque"]["checked"] += 1
                    item.update({"form": "opaque", "verified": False,
                                 "reason": "no_span_or_block_index"})
                samples.append(item)
    total = len(samples)
    matched = sum(1 for s in samples if s.get("verified"))
    rate = round(matched / total, 6) if total else None
    return {
        "samples": samples,
        "samples_total": total,
        "samples_matched": matched,
        "samples_missed": total - matched,
        "traceback_rate": rate,
        "form_counts": forms,
        "forms_verifiable": forms["span"]["checked"] + forms["block"]["checked"],
        "provenance": {"papers_checked": provenance_checked,
                       "sha256_matched": provenance_matched},
        "min_samples_required": MIN_EVIDENCE_SAMPLES,
        "target_rate": MIN_TRACEBACK_RATE,
    }


# ---------------------------------------------------------------------------
# D4: mechanical quantile recomputation (frozen nearest-rank convention)
# ---------------------------------------------------------------------------

NEAREST_RANK_METHOD = "nearest_rank_no_interpolation"
QUANTILE_TOLERANCE = 1e-9


def nearest_rank(sorted_vals: list[float], p: float) -> float | None:
    """The FROZEN quantile convention: nearest rank, NO interpolation.

    idx = max(0, min(n-1, int(round(p*(n-1))))) -- for even n and p=0.5 this picks
    the LOWER middle observation (int(round(16.5)) == 16 in Python), deliberately
    different from statistics.median / numpy linear interpolation. Hard-coded here
    so a convention drift fails CI instead of silently moving every median.
    """
    n = len(sorted_vals)
    if n == 0:
        return None
    idx = max(0, min(n - 1, int(round(p * (n - 1)))))
    return float(sorted_vals[idx])


def recompute_quantiles(records: list[dict], summary: dict,
                        tolerance: float = QUANTILE_TOLERANCE) -> dict:
    """Recompute corpus quantiles from the jsonl and compare with the summary.

    Aggregation guard: _corpus_summary.json must be mechanically reproducible from
    _per_paper_metrics.jsonl under the DECLARED convention (nearest-rank).
    """
    declared_method = summary.get("quantile_method")
    metrics = summary.get("metrics") or {}
    mismatches: list[dict] = []
    checked = 0
    for mid in sorted(metrics):
        entry = metrics[mid]
        vals = sorted(float(r["metrics"][mid]["value"])
                      for r in records
                      if (r.get("metrics") or {}).get(mid) is not None
                      and r["metrics"][mid].get("value") is not None)
        if not vals:
            continue
        for q, key in ((0.25, "p25"), (0.5, "median"), (0.75, "p75")):
            checked += 1
            mine = nearest_rank(vals, q)
            theirs = entry.get(key)
            mine_r = None if mine is None else round(mine, 6)
            if theirs is None or abs(mine_r - float(theirs)) > tolerance:
                mismatches.append({"metric": mid, "quantile": key,
                                   "declared": theirs, "recomputed": mine_r})
    declaration_ok = declared_method == NEAREST_RANK_METHOD
    if not declaration_ok:
        mismatches.append({"metric": None, "quantile": "quantile_method",
                           "declared": declared_method,
                           "recomputed": NEAREST_RANK_METHOD})
    return {
        "method_declared": declared_method,
        "method_expected": NEAREST_RANK_METHOD,
        "declaration_ok": declaration_ok,
        "quantiles_checked": checked,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "all_match": not mismatches,
    }


# ---------------------------------------------------------------------------
# Contract compliance + acceptance checks
# ---------------------------------------------------------------------------

def check_metric_contract(records: list[dict]) -> dict:
    """Every metric dict must carry the frozen field set (INTERFACES.md sec.0.4)."""
    violations: list[dict] = []
    observed_states = set()
    n_metrics = 0
    for rec in records:
        for mid, metric in sorted((rec.get("metrics") or {}).items()):
            n_metrics += 1
            observed_states.add(metric.get("state"))
            missing = [f for f in REQUIRED_METRIC_FIELDS if f not in metric]
            if missing:
                violations.append({"paper_key": rec["paper_key"], "metric": mid,
                                   "missing": missing})
            elif metric.get("metric_spec") != mid:
                violations.append({"paper_key": rec["paper_key"], "metric": mid,
                                   "detail": "metric_spec != metric_id"})
    return {
        "metric_dicts_checked": n_metrics,
        "violations": violations[:20],
        "violation_count": len(violations),
        "states_seen": sorted(s for s in observed_states if s is not None),
    }


def _check(check_id: str, description: str, passed: bool, detail) -> dict:
    return {"id": check_id, "description": description,
            "pass": bool(passed), "detail": detail}


def build_acceptance(report_inputs: dict) -> list[dict]:
    checks: list[dict] = []
    det = report_inputs["determinism"]
    det_ok = all(v["byte_identical"] for v in det.values())
    checks.append(_check(
        "A-determinism",
        "INSPECT sec.6.3: two runs are byte-identical on all fingerprinted files",
        det_ok,
        {k: v["byte_identical"] for k, v in det.items()}))

    leaks = report_inputs["fingerprint_scope"]
    checks.append(_check(
        "A-fingerprint-scope",
        "INSPECT sec.0.3: no path / host / timestamp inside the fingerprinted JSON",
        not leaks, leaks or "clean"))

    ev = report_inputs["evidence_verification"]
    ev_ok = (ev["samples_total"] >= MIN_EVIDENCE_SAMPLES
             and (ev["traceback_rate"] or 0.0) >= MIN_TRACEBACK_RATE
             and ev["forms_verifiable"] > 0)
    checks.append(_check(
        "A-evidence-traceback",
        "INSPECT sec.6.5/sec.7: sampled OBSERVED values trace back to source "
        "(form A span + form B block_index, merged rate >= 0.95)",
        ev_ok,
        {"samples": ev["samples_total"], "matched": ev["samples_matched"],
         "traceback_rate": ev["traceback_rate"], "target": MIN_TRACEBACK_RATE,
         "forms": ev["form_counts"]}))

    rc = report_inputs["reference_count"]
    checks.append(_check(
        "A-reference-count",
        "INSPECT sec.6.4: reference_count.median > 0 on the real corpus",
        bool((rc or {}).get("median", 0) > 0), rc))

    cc = report_inputs["contract_compliance"]
    checks.append(_check(
        "A-metric-contract",
        "INSPECT sec.0.4: every metric dict carries value/n/denominator/unit/state/method/metric_spec/evidence",
        cc["violation_count"] == 0,
        {"checked": cc["metric_dicts_checked"], "violations": cc["violation_count"]}))

    states = cc["states_seen"]
    checks.append(_check(
        "A-observed-only",
        "INSPECT sec.0.1: OBSERVED layer only, no model-produced state",
        states in ([], ["OBSERVED"]), states))

    qr = report_inputs["quantile_recompute"]
    checks.append(_check(
        "A-quantile-recompute",
        "V5: _corpus_summary.json quantiles are mechanically recomputable from "
        "_per_paper_metrics.jsonl under the declared "
        "nearest_rank_no_interpolation convention (not statistics.median)",
        bool(qr["all_match"]) and qr["quantiles_checked"] > 0,
        {"checked": qr["quantiles_checked"], "method": qr["method_declared"],
         "mismatches": qr["mismatch_count"]}))

    corpus = report_inputs["corpus"]
    checks.append(_check(
        "A-corpus-profiled",
        "INSPECT sec.6.2: real corpus profiled, skipped papers explicitly listed",
        corpus["n_profiled"] > 0, {"n_profiled": corpus["n_profiled"],
                                   "n_skipped": corpus["n_skipped"]}))
    return checks


# ---------------------------------------------------------------------------
# Golden table
# ---------------------------------------------------------------------------

_GOLDEN_FIELDS = ("unit", "n_valid", "n_missing", "mean", "sd", "median", "p25",
                  "p75", "iqr", "min", "max", "ci95_low", "ci95_high", "warnings")


def build_golden(summary: dict, profile: dict,
                 modules: dict | None = None) -> dict:
    per_metric: dict[str, dict] = {}
    for mid, s in sorted((summary.get("metrics") or {}).items()):
        per_metric[mid] = {k: s.get(k) for k in _GOLDEN_FIELDS}
    toolchain = profile.get("toolchain") or {}
    return {
        "baseline_version": BASELINE_VERSION,
        "schema_version": summary.get("schema_version"),
        "metric_spec_version": profile.get("metric_spec_version"),
        "profiler_version": (profile.get("meta") or {}).get("profiler_version"),
        "lexicon_fingerprint": toolchain.get("lexicon_fingerprint"),
        # Tolerant of the pending toolchain/corpus-summary additions: absent keys
        # stay null now and populate automatically once the profiler emits them.
        "unicode_norm": toolchain.get("unicode_norm"),
        "upstream": summary.get("upstream"),
        # Quantile convention of the frozen artifacts (nearest-rank, NO
        # interpolation; even n takes the LOWER middle value). Surfaced so a
        # reader never has to guess whether these are statistics.median values.
        "quantile_method": summary.get("quantile_method"),
        "module_sha256": dict(modules or {}),
        "corpus_id": (profile.get("corpus") or {}).get("id"),
        "n_papers": summary.get("n_papers"),
        "analysis_unit": summary.get("analysis_unit"),
        "weight_mode": summary.get("weight_mode"),
        "metrics": per_metric,
        "recompute_hint": (
            "cd ~/projects/dc-skills && uv run python "
            "paper-metrics/scripts/profile_papers.py --corpus <corpus> --out <out>; "
            "then compare <out>/_corpus_summary.json metrics[*] against this table. "
            "Values match only when metric_spec_version and lexicon_fingerprint match."
        ),
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# 指标基线评测报告 (Baseline Evaluation)")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    meta = report["baseline"]
    lines.append(f"| baseline_version | {meta['baseline_version']} |")
    lines.append(f"| schema_version | {meta['schema_version']} |")
    lines.append(f"| metric_spec_version | {meta['metric_spec_version']} |")
    lines.append(f"| profiler_version | {meta['profiler_version']} |")
    lines.append(f"| lexicon_fingerprint | {meta['lexicon_fingerprint']} |")
    lines.append(f"| quantile_method | {report.get('quantile_method')} |")
    c = report["corpus"]
    lines.append(f"| 语料 corpus_id | {c['corpus_id']} |")
    lines.append(f"| 论文 profiled / skipped | {c['n_profiled']} / {c['n_skipped']} |")
    lines.append("")

    lines.append("## 1. 验收检查 (INTERFACES sec.6)")
    lines.append("")
    lines.append("| id | 检查 | 结果 | 详情 |")
    lines.append("|---|---|---|---|")
    for chk in report["acceptance"]:
        lines.append(f"| {chk['id']} | {chk['description']} | "
                     f"{'PASS' if chk['pass'] else 'FAIL'} | "
                     f"{json.dumps(chk['detail'], ensure_ascii=False)} |")
    lines.append("")

    lines.append("## 2. 两次运行逐字节一致性 (determinism)")
    lines.append("")
    lines.append("| 文件 | sha256 run1 | sha256 run2 | 逐字节相同 |")
    lines.append("|---|---|---|---|")
    for name, d in report["determinism"].items():
        lines.append(f"| {name} | {str(d['sha256_run1'])[:16]}... | "
                     f"{str(d['sha256_run2'])[:16]}... | "
                     f"{'YES' if d['byte_identical'] else 'NO'} |")
    lines.append(f"| {EXCLUDED_FILES[0]} | (excluded) | (excluded) | 不参与指纹 |")
    lines.append("")

    lines.append("## 3. 逐指标数值 (golden values)")
    lines.append("")
    lines.append(f"- 分位数口径：**{report.get('quantile_method')}** —— nearest-rank、**不插值**；"
                 "偶数 n 时中位数取**下中位数**，**刻意不同于** statistics.median / numpy 默认插值。"
                 "引用本表 median 时须注明此口径，勿与插值中位数混用。")
    qr = report.get("quantile_recompute") or {}
    if qr:
        lines.append(f"- 机械重算校验：从 _per_paper_metrics.jsonl 按同一 nearest-rank 口径"
                     f"重算 {qr.get('quantiles_checked')} 个分位点，"
                     f"与 _corpus_summary.json **{'全部一致' if qr.get('all_match') else '存在不一致'}**"
                     f"（mismatch={qr.get('mismatch_count')}，声明口径={qr.get('method_declared')}）")
    lines.append("| metric_id | 单位 | n_valid | mean | sd | median | p25 | p75 | IQR | CI95 | 告警 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for mid, g in sorted(report["golden"]["metrics"].items()):
        ci = "-"
        if g.get("ci95_low") is not None and g.get("ci95_high") is not None:
            ci = f"[{_fmt(g['ci95_low'])}, {_fmt(g['ci95_high'])}]"
        warns = ",".join(g.get("warnings") or []) or "-"
        lines.append(
            f"| {mid} | {g.get('unit')} | {g.get('n_valid')} | {_fmt(g.get('mean'))} | "
            f"{_fmt(g.get('sd'))} | {_fmt(g.get('median'))} | {_fmt(g.get('p25'))} | "
            f"{_fmt(g.get('p75'))} | {_fmt(g.get('iqr'))} | {ci} | {warns} |")
    lines.append("")

    lines.append("## 4. evidence 抽样回指 (traceability)")
    lines.append("")
    ev = report["evidence_verification"]
    fc = ev["form_counts"]
    lines.append(f"- 抽样总数: {ev['samples_total']}（要求 >= {ev['min_samples_required']}）")
    lines.append(f"- 合并回指率: **{_fmt(ev['traceback_rate'])}**"
                 f"（命中 {ev['samples_matched']} / {ev['samples_total']}，"
                 f"目标 >= {ev['target_rate']}）")
    lines.append(f"- 形态 A（span，text[start:end][:80] == excerpt）: "
                 f"{fc['span']['matched']} / {fc['span']['checked']}")
    lines.append(f"- 形态 B（block_index，块文本以 excerpt 开头）: "
                 f"{fc['block']['matched']} / {fc['block']['checked']}")
    lines.append(f"- 不可核验样本（既无 span 又无 block_index，计为未命中）: "
                 f"{fc['opaque']['checked']}")
    lines.append(f"- 输入 sha256 复核: {ev['provenance']['sha256_matched']} / "
                 f"{ev['provenance']['papers_checked']}")
    lines.append("")
    lines.append("| metric | paper_key | 形态 | 定位 | excerpt | 已核验 |")
    lines.append("|---|---|---|---|---|---|")
    for s in report["evidence_samples"][:40]:
        if s.get("span"):
            loc = f"span{s['span']}"
        elif "block_index" in s:
            loc = f"block#{s['block_index']}"
        else:
            loc = "-"
        ok_txt = "YES" if s.get("verified") else "NO"
        excerpt = str(s.get("excerpt", "")).replace("|", "\\|")[:60]
        lines.append(f"| {s['metric']} | {s['paper_key']} | {s.get('form', '-')} | "
                     f"{loc} | {excerpt} | {ok_txt} |")
    lines.append("")

    up = report.get("upstream")
    lines.append("## 5. 上游输入与归一化")
    lines.append("")
    lines.append(f"- toolchain.unicode_norm: {report.get('unicode_norm')}")
    if up:
        for k in sorted(up):
            lines.append(f"- upstream.{k}: {up[k]}")
        lines.append("- 注：paper-reader 的 _META.json 未记录引擎版本，故 PDF->Canonical "
                     "漂移可发现但不可归因（ENGINE_VERSION_NOT_RECORDED）。")
    else:
        lines.append("- upstream 段：未提供（当前 profiler 版本未输出）")
    lines.append("")

    drift = report.get("engine_drift")
    lines.append("## 6. 引擎漂移（V7 探针）")
    lines.append("")
    # Printed in BOTH branches: enabling the optional probe must never read as
    # "V7 is now covered by the profile main chain".
    lines.append("- **V7 状态：主链未实现。** profile 主链（_domain_profile.json / "
                 "_corpus_summary.json / _per_paper_metrics.jsonl）**不含任何 drift 数值**；"
                 "marker_markdown 的 sha256 虽被记录，但**没有任何指标读取它**；主链只有 "
                 "ENGINE_VERSION_NOT_RECORDED 告警，即「漂移可发现、不可归因」。")
    lines.append("- 结构类指标（章节骨架/图表公式落位）在 Marker 侧无对应物；引擎版本未记录 "
                 "→ 漂移不可归因到具体引擎版本。")
    if not drift:
        lines.append("- 下面这条为**可选加分项**，默认关闭：加 --drift-probe N 才在基线报告侧"
                     "采集文本层三项指标对照。")
    else:
        lines.append(f"- 范围：{drift['scope']}")
        lines.append(f"- 归一化：{drift['normalisation']}")
        lines.append(f"- 对照篇数：{drift['n_papers_compared']} / 请求 "
                     f"{drift['n_papers_requested']}（跳过 {len(drift['skipped'])}）")
        lines.append("")
        lines.append("| metric | n_pairs | 平均有符号差 | 平均绝对差 | 最大绝对差 | 平均相对差 |")
        lines.append("|---|---|---|---|---|---|")
        for mid, s in sorted(drift["summary"].items()):
            lines.append(f"| {mid} | {s['n_pairs']} | {_fmt(s['mean_signed_delta'])} | "
                         f"{_fmt(s['mean_abs_delta'])} | {_fmt(s['max_abs_delta'])} | "
                         f"{_fmt(s['mean_rel_delta'])} |")
        lines.append("")
        lines.append("| paper_key | MinerU tokens | Marker tokens | M-SLEN-01 M/M | "
                     "M-LSF-16 M/M | M-PAS-09 M/M |")
        lines.append("|---|---|---|---|---|---|")
        for row in drift["papers"]:

            def _pair(mid: str, row=row) -> str:
                m = row["metrics"][mid]
                return f"{_fmt(m['mineru'])}/{_fmt(m['marker'])}"

            lines.append(f"| {row['paper_key'][:44]} | {row['mineru_tokens']} | "
                         f"{row['marker_tokens']} | {_pair('M-SLEN-01')} | "
                         f"{_pair('M-LSF-16')} | {_pair('M-PAS-09')} |")
        lines.append("")
        lines.append("- **探针结果不代表主链已覆盖 V7**：上表只是报告侧的可选对照，"
                     "profile 产物本身仍不含 drift 数值。")
    lines.append("")

    if report.get("gaps"):
        lines.append("## 7. 效力缺口与语料告警")
        lines.append("")
        for g in report["gaps"]:
            lines.append(f"- {g['code']}: metric={g.get('metric')} detail={g.get('detail')}")
        lines.append("")
    lines.append("---")
    lines.append("由 baseline_eval.py 确定性渲染；同一语料 + 同一 "
                 "metric_spec_version + 同一词表指纹下，本报告逐字节可复现。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# V7 engine-drift probe: MinerU content_list vs Marker markdown (text layer)
# ---------------------------------------------------------------------------

DRIFT_METRICS = ("M-SLEN-01", "M-LSF-16", "M-PAS-09")

_TICK = chr(96)
_FENCE_RE = re.compile(_TICK * 3 + r".*?" + _TICK * 3, re.DOTALL)
_INLINE_CODE_RE = re.compile(_TICK + "[^" + _TICK + r"]*" + _TICK)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_RE = re.compile(r"^#{1,6}[ \t]*", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_)")
_HTML_RE = re.compile(r"<[^>]+>")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
# Mirrors profile_papers._NON_PROSE_SECTIONS: a bibliography is not body text, so
# cutting it keeps the two engines' denominators comparable.
_NON_PROSE_HEADING_RE = re.compile(
    r"^[#*\s]*(references|bibliography|appendix|appendices|acknowledg\w*)\W*$",
    re.IGNORECASE | re.MULTILINE)
# "Keywords" is deliberately NOT a cut trigger: in a Marker markdown it appears
# right after the abstract, so cutting there would delete the entire body. Any
# candidate must also fall in the second half of the document.
_NON_PROSE_CUT_MIN_FRACTION = 0.5


def markdown_to_text(md: str, trim_non_prose: bool = True) -> str:
    """Best-effort prose extraction from a Marker markdown file.

    Removes fences, inline code, image/link targets, heading markers, emphasis
    runs and HTML tags, collapses blank runs, and (by default) cuts everything
    from the first References / Bibliography / Appendix / Acknowledgments /
    Keywords heading -- mirroring profile_papers._NON_PROSE_SECTIONS so both
    engines are measured on body prose.

    This is a DOCUMENTED APPROXIMATION, not the normalisation paper-reader
    applies. Residual asymmetries remain: the Marker side keeps figure/table
    captions and the abstract, while Paper.canonical_text() drops front_matter.
    The deltas are therefore indicative, not metrological.
    """
    text = _FENCE_RE.sub(" ", md)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _HTML_RE.sub(" ", text)
    text = _EMPHASIS_RE.sub("", text)
    if trim_non_prose:
        floor = int(len(text) * _NON_PROSE_CUT_MIN_FRACTION)
        for cut in _NON_PROSE_HEADING_RE.finditer(text):
            if cut.start() >= floor:
                text = text[:cut.start()]
                break
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def _read_first_markdown(directory: Path) -> str:
    files = sorted(directory.rglob("*.md"))
    if not files:
        return ""
    try:
        return files[0].read_text(encoding="utf-8")
    except OSError:
        return ""


def probe_engine_drift(corpus_dir: Path, n_papers: int) -> dict:
    """Compare text-layer metrics between the two engine outputs per paper.

    Scope, stated explicitly so this is not mistaken for full V7 coverage:
      * covers the TEXT layer only (M-SLEN-01 / M-LSF-16 / M-PAS-09), because
        structural metrics (assets, section skeleton) have no Marker analogue;
      * quantifies the DISCOVERABLE drift between MinerU content_list text and
        Marker markdown text;
      * does NOT attribute drift to engine versions: paper-reader records no
        engine version (see ENGINE_VERSION_NOT_RECORDED), so drift can be
        observed but not attributed to a release.
    """
    corpus_dir = Path(corpus_dir)
    _, bundle = prof._load_lexicons()
    tm = prof._import_sibling("text_metrics")
    discovery = prof.discover_papers_detailed(corpus_dir)
    rows: list[dict] = []
    skipped: list[dict] = []
    for paper in discovery.papers:
        if len(rows) >= n_papers:
            break
        if not paper.marker_md_path:
            skipped.append({"paper_key": paper.paper_key, "reason": "no_marker_md"})
            continue
        marker_md = _read_first_markdown(paper.marker_md_path.parent)
        if not marker_md.strip():
            skipped.append({"paper_key": paper.paper_key, "reason": "empty_marker_md"})
            continue
        mineru_text = paper.canonical_text()
        if not mineru_text.strip():
            skipped.append({"paper_key": paper.paper_key, "reason": "empty_mineru_text"})
            continue
        marker_text = markdown_to_text(marker_md)
        mineru_m = tm.compute_text_metrics(mineru_text, bundle)
        marker_m = tm.compute_text_metrics(marker_text, bundle)
        row = {
            "paper_key": paper.paper_key,
            "mineru_tokens": mineru_m["M-SLEN-01"].get("denominator"),
            "marker_tokens": marker_m["M-SLEN-01"].get("denominator"),
            "mineru_sentences": mineru_m["M-SLEN-01"].get("n"),
            "marker_sentences": marker_m["M-SLEN-01"].get("n"),
            "metrics": {},
        }
        for mid in DRIFT_METRICS:
            a = mineru_m.get(mid, {}).get("value")
            b = marker_m.get(mid, {}).get("value")
            row["metrics"][mid] = {
                "mineru": a, "marker": b,
                "delta": (None if (a is None or b is None) else round(b - a, 6)),
                "abs_delta": (None if (a is None or b is None) else round(abs(b - a), 6)),
                "rel_delta": (None if (a is None or b is None or not a)
                              else round((b - a) / a, 6)),
            }
        rows.append(row)

    summary: dict[str, dict] = {}
    for mid in DRIFT_METRICS:
        deltas = [r["metrics"][mid]["delta"] for r in rows
                  if r["metrics"][mid]["delta"] is not None]
        rels = [r["metrics"][mid]["rel_delta"] for r in rows
                if r["metrics"][mid]["rel_delta"] is not None]
        if not deltas:
            summary[mid] = {"n_pairs": 0, "mean_signed_delta": None,
                            "mean_abs_delta": None, "max_abs_delta": None,
                            "mean_rel_delta": None}
            continue
        summary[mid] = {
            "n_pairs": len(deltas),
            "mean_signed_delta": round(sum(deltas) / len(deltas), 6),
            "mean_abs_delta": round(sum(abs(d) for d in deltas) / len(deltas), 6),
            "max_abs_delta": round(max(abs(d) for d in deltas), 6),
            "mean_rel_delta": (round(sum(rels) / len(rels), 6) if rels else None),
        }
    return {
        "probe_version": BASELINE_VERSION,
        "scope": ("text layer only (M-SLEN-01 / M-LSF-16 / M-PAS-09); "
                  "engine-version attribution NOT available "
                  "(ENGINE_VERSION_NOT_RECORDED)"),
        "normalisation": ("marker markdown reduced to prose by markdown_to_text "
                          "(non-prose tail cut at the first References/Bibliography/"
                          "Appendix/Acknowledg*/Keywords heading, mirroring "
                          "profile_papers._NON_PROSE_SECTIONS); mineru text = "
                          "Paper.canonical_text()"),
        "residual_asymmetry": ("marker side keeps figure/table captions and the "
                               "abstract; canonical_text() drops front_matter -- "
                               "deltas are indicative, not metrological"),
        "n_papers_requested": n_papers,
        "n_papers_compared": len(rows),
        "skipped": skipped,
        "papers": rows,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_baseline(corpus_dir: Path, out_dir: Path, runs: int = 2,
                 keep_temp: bool = False, drift_papers: int = 0) -> dict:
    if not corpus_dir.is_dir():
        raise SystemExit(f"corpus directory not found: {corpus_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    workdirs: list[Path] = []
    try:
        t0 = time.time()
        run_dirs: list[Path] = []
        first_profile: dict | None = None
        for i in range(max(2, runs)):
            d = Path(tempfile.mkdtemp(prefix=f"baseline_run{i + 1}_"))
            workdirs.append(d)
            profile = prof.run_profile(corpus_dir, d)
            if first_profile is None:
                first_profile = profile
            run_dirs.append(d)
        elapsed_ms = int((time.time() - t0) * 1000)

        assert first_profile is not None
        summary = load_json(run_dirs[0] / "_corpus_summary.json")
        records = load_jsonl(run_dirs[0] / "_per_paper_metrics.jsonl")
        contexts = build_paper_contexts(corpus_dir)

        determinism = compare_fingerprints(run_dirs[0], run_dirs[1])
        scope_leaks = scan_volatile_leaks(run_dirs[0], corpus_dir)
        evidence = collect_evidence(records, contexts)
        compliance = check_metric_contract(records)
        corpus = first_profile.get("corpus") or {}
        skipped = corpus.get("skipped") or []
        corpus_info = {
            "corpus_id": corpus.get("id"),
            "n_papers": len(records),
            "n_profiled": len(records),
            "n_skipped": len(skipped),
            "skipped": skipped,
        }

        gaps = sorted(
            ({"code": w.get("code"), "metric": w.get("metric"),
              "detail": w.get("detail")}
             for w in (summary.get("corpus_warnings") or [])),
            key=lambda w: (str(w["code"]), str(w["metric"])))

        inputs = {
            "quantile_recompute": recompute_quantiles(records, summary),
            "determinism": determinism,
            "fingerprint_scope": scope_leaks,
            "evidence_verification": evidence,
            "reference_count": first_profile.get("reference_count"),
            "contract_compliance": compliance,
            "corpus": corpus_info,
        }
        acceptance = build_acceptance(inputs)
        toolchain = first_profile.get("toolchain") or {}
        modules = module_sha256()
        golden = build_golden(summary, first_profile, modules)
        drift = (probe_engine_drift(corpus_dir, drift_papers)
                 if drift_papers > 0 else None)

        report = {
            "baseline": {
                "baseline_version": BASELINE_VERSION,
                "schema_version": first_profile.get("schema_version"),
                "metric_spec_version": first_profile.get("metric_spec_version"),
                "profiler_version": (first_profile.get("meta") or {}).get("profiler_version"),
                "lexicon_fingerprint": toolchain.get("lexicon_fingerprint"),
                "lexicon_version": toolchain.get("lexicon_version"),
                "toolchain": toolchain,
                "module_sha256": module_sha256(),
                "runs": len(run_dirs),
                "excluded_from_fingerprint": list(EXCLUDED_FILES),
            },
            "corpus": corpus_info,
            "quantile_recompute": inputs["quantile_recompute"],
            "unicode_norm": toolchain.get("unicode_norm"),
            "upstream": summary.get("upstream"),
            "quantile_method": summary.get("quantile_method"),
            "engine_drift": drift,
            "acceptance": acceptance,
            "acceptance_all_pass": all(c["pass"] for c in acceptance),
            "determinism": determinism,
            "fingerprint_scope_leaks": scope_leaks,
            "contract_compliance": compliance,
            "evidence_verification": {k: v for k, v in evidence.items()
                                      if k != "samples"},
            "evidence_samples": evidence["samples"],
            "metrics": {mid: {k: s.get(k) for k in _GOLDEN_FIELDS}
                        for mid, s in sorted((summary.get("metrics") or {}).items())},
            "reference_count": first_profile.get("reference_count"),
            "citation_style": first_profile.get("citation_style"),
            "section_skeleton_top": (first_profile.get("section_skeleton") or [])[:10],
            "gaps": gaps,
            "golden": golden,
        }

        (out_dir / "_baseline_report.json").write_text(
            prof.canonical_json_text(prof._round_floats(report)), encoding="utf-8")
        (out_dir / "_baseline_report.md").write_text(
            render_markdown(report), encoding="utf-8")
        (out_dir / "_baseline_golden.json").write_text(
            prof.canonical_json_text(prof._round_floats(golden)), encoding="utf-8")
        # Volatile side-car, mirroring profile_papers._run_meta.json: timing and
        # host must never leak into a fingerprinted artifact, otherwise the
        # report stops being byte-reproducible across runs and machines.
        if drift is not None:
            (out_dir / "_engine_drift.json").write_text(
                prof.canonical_json_text(prof._round_floats(drift)), encoding="utf-8")
        (out_dir / "_baseline_run_meta.json").write_text(
            prof.canonical_json_text({
                "elapsed_ms": elapsed_ms,
                "runs": len(run_dirs),
                "host": platform.node(),
                "python_version": platform.python_version(),
                "baseline_version": BASELINE_VERSION,
            }), encoding="utf-8")
        return report
    finally:
        if not keep_temp:
            for d in workdirs:
                shutil.rmtree(d, ignore_errors=True)
        else:
            print(f"[baseline_eval] temp run dirs kept: {[str(d) for d in workdirs]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the I12 baseline evaluation on a real paper corpus.")
    parser.add_argument("--corpus", required=True, help="paper-analysis/ directory")
    parser.add_argument("--out", "--output", dest="out", required=True,
                        help="output directory for _baseline_report.{json,md}")
    parser.add_argument("--runs", type=int, default=2,
                        help="number of profiler runs for the determinism check (default 2)")
    parser.add_argument("--keep-temp", action="store_true",
                        help="keep the temp run dirs for inspection")
    parser.add_argument("--drift-probe", type=int, default=0, metavar="N",
                        help="V7: compare MinerU vs Marker text-layer metrics on the "
                             "first N papers that have both outputs (0 = off)")
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    out = Path(args.out).resolve()
    report = run_baseline(corpus, out, runs=args.runs, keep_temp=args.keep_temp,
                          drift_papers=args.drift_probe)

    print(f"[baseline_eval] wrote {out / '_baseline_report.json'}")
    print(f"[baseline_eval] wrote {out / '_baseline_report.md'}")
    print(f"[baseline_eval] wrote {out / '_baseline_golden.json'}")
    print(f"[baseline_eval] wrote {out / '_baseline_run_meta.json'} (volatile, not compared)")
    print(f"[baseline_eval] papers profiled: {report['corpus']['n_profiled']} "
          f"(skipped {report['corpus']['n_skipped']})")
    print(f"[baseline_eval] metrics locked: {len(report['golden']['metrics'])}")
    det = report["determinism"]
    print(f"[baseline_eval] determinism: "
          f"{'byte-identical' if all(v['byte_identical'] for v in det.values()) else 'MISMATCH'}")
    ev = report["evidence_verification"]
    print(f"[baseline_eval] evidence samples: {ev['samples_total']} "
          f"(trace-back rate {ev['traceback_rate']}, target {ev['target_rate']})")
    print(f"[baseline_eval] evidence forms: span {ev['form_counts']['span']}, "
          f"block {ev['form_counts']['block']}, opaque {ev['form_counts']['opaque']}")
    drift = report.get("engine_drift")
    if drift:
        print(f"[baseline_eval] V7 engine drift: {drift['n_papers_compared']} paper(s) compared; "
              f"summary={json.dumps(drift['summary'], ensure_ascii=False)}")
    else:
        print("[baseline_eval] V7 engine drift: not probed "
              "(use --drift-probe N); full Marker/MinerU drift comparison not implemented")
    for chk in report["acceptance"]:
        print(f"[baseline_eval] {'PASS' if chk['pass'] else 'FAIL'} {chk['id']}")
    if not report["acceptance_all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
