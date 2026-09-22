#!/usr/bin/env python3
"""Corpus audit: re-profile a registered corpus and compare it with its record.

Why this exists
---------------
A metric value is only auditable if you can say *which inputs* and *which code*
produced it.  The registry (data/test-corpora.json) records, per corpus:

  * corpus_id  - a sha256 over every input artifact, i.e. the identity of the
                 corpus content itself;
  * expected_metrics - the corpus means that were verified when the record was
                 made;
  * recorded_with    - the toolchain versions, including the lexicon release
                 fingerprint and the metric-layer version.

This script re-runs the profiler and compares the three.  The comparison is the
audit, and the order of the checks is what makes the verdict diagnosable:

  1. corpus_id differs            -> INPUTS CHANGED.  Nothing else is comparable;
                                     the record must be re-made.
  2. corpus_id same, versions or
     lexicon fingerprint differ   -> CODE OR WORD LIST CHANGED.  The numbers may
                                     legitimately move; the difference is
                                     explained, not mysterious.
  3. same corpus_id AND same
     versions, metric differs     -> UNEXPLAINED DRIFT.  This is the case worth
                                     investigating: determinism is broken.

The same three-way comparison covers the rest of the artifact, not just the
registered metric means: corpus_warnings (counted by code), language_supported,
by_section and section_skeleton.  Those were outside the audit's scope until
issue #18 - which is how a corpus-wide language false alarm survived both 400+
tests and a "23/23 checks match" run (the audit compared values and fingerprints
but never the warnings that explained the values).

Usage
-----
    cd ~/projects/dc-skills
    # audit a registered corpus (exit 0 = match, 1 = drift)
    uv run python paper-metrics/scripts/audit_corpus.py --name vrp-en --out /tmp/audit-en

    # inspect an unregistered corpus (prints its identity so it can be registered)
    uv run python paper-metrics/scripts/audit_corpus.py --corpus <path> --out /tmp/audit-x
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_REGISTRY = SCRIPTS_DIR.parent / "data" / "test-corpora.json"
#: Corpus means are compared exactly by default: the profiler is deterministic,
#: so a tolerance would only hide the drift this tool exists to find.
DEFAULT_TOLERANCE = 0.0


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("corpora"), list):
        raise SystemExit(f"{path}: missing 'corpora' list")
    return data


def find_entry(registry: dict, name: str) -> dict:
    for entry in registry["corpora"]:
        if entry.get("name") == name:
            return entry
    known = ", ".join(sorted(entry.get("name", "?") for entry in registry["corpora"]))
    raise SystemExit(f"unknown corpus {name!r}; registry has: {known}")


def _digest(payload) -> str:
    """Stable sha256 over a JSON payload (sorted keys, no whitespace noise)."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _warning_counts(summary: dict) -> dict:
    """Corpus warning code -> how many entries raised it."""
    counts = Counter(str(w.get("code")) for w in summary.get("corpus_warnings") or [])
    return {code: counts[code] for code in sorted(counts)}


def _by_section_means(summary: dict) -> dict:
    """{metric_id: {section: mean}} for the stratifications that produced a value.

    Only non-empty stratifications are listed, so a metric that never had a
    by_section block does not create 30 empty expectations.
    """
    out: dict = {}
    for mid, row in sorted((summary.get("metrics") or {}).items()):
        sections = {section: round(float(stat["mean"]), 6)
                    for section, stat in sorted((row.get("by_section") or {}).items())
                    if isinstance(stat, dict) and stat.get("mean") is not None}
        if sections:
            out[mid] = sections
    return out


def audit(entry: dict, out_dir: Path, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    import profile_papers as pp

    out_dir.mkdir(parents=True, exist_ok=True)
    pp.run_profile(Path(entry["path"]), out_dir)
    profile = json.loads((out_dir / "_domain_profile.json").read_text(encoding="utf-8"))
    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))

    findings: list = []

    def record(check: str, expected, actual, ok: bool, note: str = "") -> None:
        findings.append({"check": check, "expected": expected, "actual": actual,
                         "status": "match" if ok else "drift", "note": note})

    actual_id = profile["corpus"]["id"]
    record("corpus_id", entry.get("corpus_id"), actual_id, actual_id == entry.get("corpus_id"),
           "" if actual_id == entry.get("corpus_id")
           else "INPUTS CHANGED: the corpus content differs, nothing else is comparable")
    record("n_papers", entry.get("n_papers"), profile["corpus"]["n_papers"],
           profile["corpus"]["n_papers"] == entry.get("n_papers"))
    record("skipped", entry.get("skipped"), len(profile["corpus"]["skipped"]),
           len(profile["corpus"]["skipped"]) == entry.get("skipped"))

    recorded_with = entry.get("recorded_with") or {}
    for key in ("profiler_version", "schema_version", "metric_spec_version",
                "text_metrics_version", "lexicon_version"):
        if key not in recorded_with:
            continue
        actual = profile["toolchain"].get(key)
        record(f"toolchain.{key}", recorded_with[key], actual, actual == recorded_with[key],
               "" if actual == recorded_with[key]
               else "CODE OR WORD LIST CHANGED (explains metric movement)")
    if recorded_with.get("lexicon_language") != "en":
        # NB: toolchain.lexicon_version above is the ENGLISH release (the
        # historical field).  The release a non-English corpus actually used
        # lives in toolchain.lexicon_releases[language], so both the version and
        # the fingerprint of THAT release are checked here.
        releases = profile["toolchain"].get("lexicon_releases") or {}
        release = releases.get(recorded_with.get("lexicon_language")) or {}
        if "lexicon_release_version" in recorded_with:
            expected_version = recorded_with["lexicon_release_version"]
            actual_version = release.get("version")
            record("lexicon_release_version", expected_version, actual_version,
                   actual_version == expected_version,
                   "" if actual_version == expected_version
                   else "WORD LIST CHANGED (explains metric movement)")
        expected_fp = recorded_with.get("lexicon_fingerprint")
        actual_fp = release.get("fingerprint")
        record("lexicon_fingerprint", expected_fp, actual_fp, actual_fp == expected_fp,
               "" if actual_fp == expected_fp
               else "WORD LIST CHANGED (explains metric movement)")

    metric_rows: list = []
    for metric_id, expected in sorted((entry.get("expected_metrics") or {}).items()):
        row = summary["metrics"].get(metric_id) or {}
        actual = row.get("mean")
        if actual is None:
            ok = False
            note = "metric is now null (not measured)"
        else:
            ok = abs(actual - expected) <= tolerance
            note = "" if ok else "metric moved"
        metric_rows.append({"metric": metric_id, "expected": expected,
                            "actual": actual, "status": "match" if ok else "drift",
                            "note": note})
        record(f"metric.{metric_id}", expected, actual, ok, note)

    # Beyond the metric means: the warnings that explain them, the language
    # verdict, the section stratification and the section skeleton.  A registry
    # entry that does not declare one of these is not compared on it (older
    # records keep working), but a declared one must match exactly.
    if "expected_corpus_warnings" in entry:
        expected_warnings = entry.get("expected_corpus_warnings") or {}
        actual_warnings = _warning_counts(summary)
        for code in sorted(set(expected_warnings) | set(actual_warnings)):
            expected_n = int(expected_warnings.get(code, 0))
            actual_n = int(actual_warnings.get(code, 0))
            record(f"corpus_warning.{code}", expected_n, actual_n, expected_n == actual_n,
                   "" if expected_n == actual_n
                   else ("warning no longer raised" if actual_n < expected_n
                         else "unregistered warning raised"))
    if "expected_language_supported" in entry:
        expected_language = entry.get("expected_language_supported")
        actual_language = summary.get("language_supported")
        record("language_supported", expected_language, actual_language,
               actual_language == expected_language,
               "" if actual_language == expected_language
               else "the corpus language verdict moved (languages: %s)"
                    % json.dumps(summary.get("languages"), ensure_ascii=False, sort_keys=True))
    if "expected_null_metrics" in entry:
        expected_nulls = sorted(entry.get("expected_null_metrics") or [])
        actual_nulls = sorted(metric for metric, row in summary["metrics"].items()
                              if not row.get("n_valid"))
        record("null_metrics", expected_nulls, actual_nulls, expected_nulls == actual_nulls,
               "" if expected_nulls == actual_nulls
               else "the set of metrics with no valid value changed (a metric that "
                    "silently stopped being measurable is otherwise invisible)")
    if "expected_by_section" in entry:
        expected_sections = entry.get("expected_by_section") or {}
        actual_sections = _by_section_means(summary)
        for mid in sorted(set(expected_sections) | set(actual_sections)):
            expected_mid = expected_sections.get(mid) or {}
            actual_mid = actual_sections.get(mid) or {}
            record(f"by_section.{mid}", expected_mid, actual_mid, expected_mid == actual_mid,
                   "" if expected_mid == actual_mid
                   else "section stratification moved for this metric")
    if "expected_section_skeleton" in entry:
        skeleton = profile.get("section_skeleton") or []
        expected_skeleton = entry.get("expected_section_skeleton") or {}
        actual_digest = _digest(skeleton)
        expected_digest = expected_skeleton.get("digest")
        # A digest, not the 500+ label list: the registry stays readable while any
        # change is still caught, and the fresh _domain_profile.json is named in the
        # finding so the difference can be located immediately.
        record("section_skeleton.digest", expected_digest, actual_digest,
               actual_digest == expected_digest,
               "" if actual_digest == expected_digest
               else "section skeleton changed; diff the fresh %s"
                    % (out_dir / "_domain_profile.json"))
        record("section_skeleton.n_entries", expected_skeleton.get("n_entries"), len(skeleton),
               len(skeleton) == expected_skeleton.get("n_entries"))

    drifted = [item for item in findings if item["status"] == "drift"]
    # The verdict explains itself: which class of change was found.
    if not drifted:
        cause = None
    elif any(item["check"] == "corpus_id" for item in drifted):
        cause = "inputs_changed"
    elif any(item["check"].startswith(("toolchain.", "lexicon_")) for item in drifted):
        cause = "code_or_word_list_changed"
    else:
        cause = "unexplained_drift"
    return {
        "name": entry.get("name"),
        "corpus": entry.get("path"),
        "corpus_id": actual_id,
        "verdict": "match" if not drifted else "drift",
        "cause": cause,
        "n_drifted": len(drifted),
        "findings": findings,
        "metrics": metric_rows,
        "artifacts": {
            "profile": str(out_dir / "_domain_profile.json"),
            "summary": str(out_dir / "_corpus_summary.json"),
            "jsonl": str(out_dir / "_per_paper_metrics.jsonl"),
        },
    }


def inspect_unregistered(corpus: Path, out_dir: Path) -> dict:
    """Report the identity of an unregistered corpus so it can be recorded."""
    import profile_papers as pp

    out_dir.mkdir(parents=True, exist_ok=True)
    pp.run_profile(corpus, out_dir)
    profile = json.loads((out_dir / "_domain_profile.json").read_text(encoding="utf-8"))
    summary = json.loads((out_dir / "_corpus_summary.json").read_text(encoding="utf-8"))
    return {
        "corpus": str(corpus),
        "corpus_id": profile["corpus"]["id"],
        "n_papers": profile["corpus"]["n_papers"],
        "skipped": len(profile["corpus"]["skipped"]),
        "languages": summary.get("languages"),
        "toolchain": {key: profile["toolchain"].get(key) for key in
                      ("profiler_version", "schema_version", "metric_spec_version",
                       "text_metrics_version", "lexicon_version")},
        "lexicon_releases": profile["toolchain"].get("lexicon_releases"),
        "expected_metrics": {metric: round(row["mean"], 6)
                             for metric, row in sorted(summary["metrics"].items())
                             if row.get("n_valid") and row.get("mean") is not None},
        "null_metrics": [metric for metric, row in sorted(summary["metrics"].items())
                         if not row.get("n_valid")],
        "expected_corpus_warnings": _warning_counts(summary),
        "expected_language_supported": summary.get("language_supported"),
        "expected_by_section": _by_section_means(summary),
        "expected_section_skeleton": {
            "n_entries": len(profile.get("section_skeleton") or []),
            "digest": _digest(profile.get("section_skeleton") or []),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit a corpus against its registry record")
    parser.add_argument("--name", help="registered corpus name")
    parser.add_argument("--corpus", type=Path, help="unregistered corpus path (report only)")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the fresh profile (default: a temp dir)")
    parser.add_argument("--json", type=Path, default=None, help="write the audit report here")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    if bool(args.name) == bool(args.corpus):
        parser.error("pass exactly one of --name or --corpus")

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="corpus-audit-"))

    if args.corpus:
        report = inspect_unregistered(args.corpus, out_dir)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        print(payload)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(payload, encoding="utf-8")
        return 0

    registry = load_registry(args.registry)
    entry = find_entry(registry, args.name)
    report = audit(entry, out_dir, args.tolerance)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(payload, encoding="utf-8")

    print("[audit] %s (%s)" % (report["name"], report["corpus"]))
    print("[audit] corpus_id %s" % report["corpus_id"])
    print("[audit] verdict: %s%s" % (report["verdict"],
                                     "" if not report["cause"] else " (%s)" % report["cause"]))
    for item in report["findings"]:
        if item["status"] == "drift":
            print("  DRIFT %-28s expected=%s actual=%s %s"
                  % (item["check"], item["expected"], item["actual"], item["note"]))
    if report["verdict"] == "match":
        print("  all %d checks match (metrics + corpus identity + toolchain)"
              % len(report["findings"]))
    print("[audit] fresh artifacts: %s" % report["artifacts"]["profile"])
    return 0 if report["verdict"] == "match" else 1


if __name__ == "__main__":
    raise SystemExit(main())
