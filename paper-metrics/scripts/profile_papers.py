#!/usr/bin/env python3
"""Domain profiler for the paper-metrics skill (input to thesis-writing Mode B).

Reads a paper-analysis/ corpus (MinerU content_list.json files, produced by the
paper-reader skill) and emits a machine-readable domain profile that drives
section-skeleton selection, figure/table/equation placement, and citation-style
decisions in the writing-plan phase.

Usage:
    cd ~/projects/dc-skills && uv run python paper-metrics/scripts/profile_papers.py \
        --corpus /path/to/paper-analysis/ --out /path/to/output/

Outputs:
    <out>/_domain_profile.json      — machine-readable (consumed by the skill)
    <out>/_domain_profile.md        — human-readable, rendered deterministically from the JSON
    <out>/_per_paper_metrics.jsonl  — one line per profiled paper (audit trail)
    <out>/_corpus_summary.json      — corpus aggregation, recomputable from the jsonl
    <out>/_run_meta.json            — volatile run metadata (NOT part of the fingerprint)

Design:
    - Pure stdlib. No LLM calls, no third-party deps (OBSERVED-layer red line).
    - Deterministic: dicts emitted with sort_keys=True, floats rounded to 6
      decimals, every directory walk sorted, no timestamps inside the
      fingerprinted JSON. Two runs over the same corpus + path produce
      byte-identical _domain_profile.json / _per_paper_metrics.jsonl /
      _corpus_summary.json.
    - Reproducibility boundary: the bit-level guarantee covers
      "Canonical Document -> OBSERVED metrics". The PDF -> Canonical step is done
      by paper-reader and its drift is NOT measured here: paper-reader does not
      record engine versions, so this profiler can only raise
      ENGINE_VERSION_NOT_RECORDED and record input hashes. Do not read this
      module as providing an engine-drift report.
    - Verifiability: every metric carries value/n/denominator/unit/evidence, and
      every paper carries sha256 input hashes, so a third party can recompute a
      number from the exact artifact it came from.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib
import json
import math
import platform
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Algorithm version — bump when metric *semantics* change.
PROFILER_VERSION = "2.5"  # 2.5: appendix context inheritance (issue #17) — canonical scope changed
# Output contract version — bump when the JSON *schema* changes.
SCHEMA_VERSION = "2.4"  # 2.4: + toolchain.text_metrics_version
# Version of the written metric definitions (references/metric-definitions.md).
METRIC_SPEC_VERSION = "1.0"

class ProfileError(Exception):
    """Raised when the corpus cannot be profiled (empty, malformed, unreadable)."""


# A text block becomes a "paragraph" for M-PCNT-25 only if it has at least this
# many words; shorter blocks are usually captions, fragments or reference lines.
_PARAGRAPH_MIN_WORDS = 15
# Sections that get a stratified (by_section) aggregation in _corpus_summary.json.
# Kept to the three sections the metric contract requires (introduction / method /
# experiments): every extra stratum costs a full metric pass over that section,
# and cross-section pooling is meaningless anyway.
_STRATIFIED_SECTIONS = ("introduction", "method", "experiments")
# ---------------------------------------------------------------------------
# Paper discovery
# ---------------------------------------------------------------------------

# Sections whose text is NOT prose and therefore must stay out of the style
# metrics (a bibliography is not body text; a keyword list is not a sentence).
_NON_PROSE_SECTIONS = frozenset(
    {"references", "appendix", "acknowledgments", "keywords", "front_matter"}
)

#: A keywords marker line is metadata even when its block was attributed to a
#: prose section, which is what happens when the journal prints the keyword list
#: under an unrecognised heading ("A R T I C L E I N F O").  Only the leading
#: marker is matched, so a sentence that merely mentions the word "keywords"
#: ("The keywords were extracted from the abstract.") stays body text.
_KEYWORDS_MARKER_RE = re.compile(
    r"^\s*(?:keywords?|key\s+words|关键词|关键字|主題詞|主题词)\s*[:：]",
    re.IGNORECASE,
)

#: An appendix heading may carry a letter prefix and a long title that mentions a
#: word a body-section keyword also matches.  Without this check, "A Appendix: The
#: Global Feature Importance ... Summary ..." was classified as 'conclusion' (the
#: summary keyword matched first) and "Appendix A. CVRP mathematical formulation"
#: as 'method', so two real appendices were measured as body text.  Anchored at the
#: start: an appendix is recognised by what it is CALLED, not by what it mentions.
_APPENDIX_PREFIX_RE = re.compile(
    r"^\s*(?:[a-z]\.?\s+)?(?:appendix|appendices|supplementary\s+material|附录|补充材料)\b"
)


def sha256_file(path: Path) -> str:
    """sha256 of the raw bytes of a file (hex). Used for input provenance."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _figure_image_inputs(content_list_path: Path) -> list[dict]:
    """sha256 of every figure image this paper's metrics will open.

    S-SIZ-04 measures image headers, so replacing an image moves a metric value.
    While the images were absent from `inputs` the corpus id stayed the same, so
    audit_corpus reported that movement as UNEXPLAINED DRIFT - the one verdict the
    audit exists to reserve for a determinism break (issue #18-6).  The file set is
    taken from doc_model's own stream classification, so it cannot drift from the
    set image_resolution() actually opens.
    """
    try:
        doc_model = _import_sibling("doc_model")
        model = doc_model.parse_document(content_list_path)
    except Exception:  # noqa: BLE001 - an unparseable paper is reported by the metrics layer
        return []
    base = content_list_path.parent
    by_artifact: dict[str, dict] = {}
    for block in model.blocks:
        if block.kind is not doc_model.Stream.FIGURES or not block.image_path:
            continue
        path = base / block.image_path
        if not path.is_file():
            continue
        by_artifact.setdefault(
            f"figure_image:{block.image_path}",
            {"artifact": f"figure_image:{block.image_path}", "sha256": sha256_file(path)})
    return [by_artifact[key] for key in sorted(by_artifact)]


@dataclass
class Paper:
    name: str
    content_list_path: Path
    marker_md_path: Path | None = None
    blocks: list[dict] = field(default_factory=list)
    # Stable identifier used as the JSON key / jsonl ordering key.
    paper_key: str = ""
    # [{"artifact": "mineru_content_list", "sha256": "..."}] — provenance so a
    # third party can prove which exact bytes produced a number.
    inputs: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Derived-text caches. The profiler touches each paper's text several times
    # (metrics, token count, per-section strata); recomputing the join and the
    # section walk every time dominated the runtime for a 34-paper corpus.
    _labelled_cache: list | None = field(default=None, repr=False, compare=False)
    _text_cache: str | None = field(default=None, repr=False, compare=False)
    _sections_cache: dict | None = field(default=None, repr=False, compare=False)
    # Upstream (paper-reader) provenance: which engines produced this canonical
    # document, and whether their versions were recorded at all.
    upstream: dict = field(default_factory=dict)
    # reason -> {"blocks": n, "words": m}: every text block kept out of the
    # canonical body, so a reader can see how much material was dropped and
    # why (issue: 前置页/关键词混入正文). Populated by canonical_text().
    non_prose_dropped: dict = field(default_factory=dict)

    def load_blocks(self) -> list[dict]:
        if self.blocks:
            return self.blocks
        try:
            data = json.loads(self.content_list_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, RecursionError) as exc:
            # RecursionError: a pathological nesting depth is a parse failure, not a
            # crash (doc_model would refuse the same file).
            raise ProfileError(f"failed to read {self.content_list_path}: {exc}") from exc
        if not isinstance(data, list):
            raise ProfileError(f"{self.content_list_path}: expected a JSON list")
        self.blocks = [b for b in data if isinstance(b, dict)]
        return self.blocks

    # -- section attribution -------------------------------------------------

    def labelled_blocks(self) -> list[tuple[str, dict]]:
        """(canonical_section_label, block) for every block, in document order.

        A block's label is the nearest preceding level-2 heading; blocks before
        the first heading get "front_matter".
        """
        if self._labelled_cache is not None:
            return self._labelled_cache
        out: list[tuple[str, dict]] = []
        current = "front_matter"
        # The appendix is a CONTEXT, not a letter pattern: IEEE body subsections are
        # lettered exactly like appendix children ("A. Accuracy study" next to
        # "A. Numerical results"), so a lettered heading is inherited into the
        # appendix only while an explicit appendix heading is in force - and a real
        # body heading (a non-prose section or a canonical title) ends that context.
        in_appendix = False
        for b in self.load_blocks():
            t = _is_title_block(b)
            if t and t[1] == 2:
                norm = normalize_section_title(t[0])
                label = canonical_section_label(norm)
                if label == "appendix":
                    in_appendix = True
                    current = "appendix"
                elif (in_appendix and label not in _NON_PROSE_SECTIONS
                        and norm not in _CANONICAL_MAP):
                    current = "appendix"
                else:
                    in_appendix = False
                    current = label
            elif b.get("type") == "header":
                # MinerU sometimes emits the section title as a running head
                # (type="header") instead of a heading with text_level. Only the
                # bibliography labels are honoured here, so page headers cannot
                # silently re-label body sections.
                header_label = canonical_section_label(
                    normalize_section_title(str(b.get("text") or "")))
                if header_label in ("references", "bibliography"):
                    current = "references"
                    in_appendix = False
            out.append((current, b))
        self._labelled_cache = out
        return out

    def canonical_text(self, include_non_prose: bool = False) -> str:
        """Body text of the canonical document.

        Concatenates non-heading text blocks in document order, dropping
        non-prose sections (references / appendix / keywords / ...) unless asked.
        Deterministic: depends only on the content_list.json bytes.
        """
        if not include_non_prose and self._text_cache is not None:
            return self._text_cache
        parts: list[str] = []
        dropped: dict[str, dict[str, int]] = {}
        for section, b in self.labelled_blocks():
            if _is_title_block(b):
                continue
            if b.get("type") != "text":
                continue
            text = (b.get("text") or "").strip()
            if not text:
                continue
            reason: str | None = None
            if not include_non_prose:
                if section in _NON_PROSE_SECTIONS:
                    reason = section
                elif _KEYWORDS_MARKER_RE.match(text):
                    # journal keyword list printed under a heading the section
                    # classifier does not recognise as non-prose
                    reason = "keywords_line"
            if reason is not None:
                stats = dropped.setdefault(reason, {"blocks": 0, "words": 0})
                stats["blocks"] += 1
                stats["words"] += len(text.split())
                continue
            parts.append(text)
        if not include_non_prose:
            self.non_prose_dropped = {k: dropped[k] for k in sorted(dropped)}
        # NFC normalisation happens here, once, at the ingestion point: the same
        # logical text in NFC vs NFD would otherwise tokenise differently and
        # silently shift every length/ratio metric. Spans recorded in evidence
        # slice this normalised string, so they stay consistent.
        joined = unicodedata.normalize("NFC", "\n\n".join(parts))
        if not include_non_prose:
            self._text_cache = joined
        return joined

    def section_texts(self) -> dict[str, str]:
        """canonical_section_label -> concatenated body text of that section."""
        if self._sections_cache is not None:
            return self._sections_cache
        buckets: dict[str, list[str]] = defaultdict(list)
        for section, b in self.labelled_blocks():
            if _is_title_block(b) or b.get("type") != "text":
                continue
            text = (b.get("text") or "").strip()
            if not text:
                continue
            if section in _NON_PROSE_SECTIONS or _KEYWORDS_MARKER_RE.match(text):
                continue
            buckets[section].append(text)
        self._sections_cache = {
            k: unicodedata.normalize("NFC", "\n\n".join(v)) for k, v in buckets.items()
        }
        return self._sections_cache

    def sections_present(self) -> list[str]:
        seen = [s for s, _ in self.labelled_blocks() if s != "front_matter"]
        return sorted(set(seen))


@dataclass
class Discovery:
    """Result of walking a corpus: papers + an explicit skip ledger."""

    papers: list[Paper]
    skipped: list[dict]
    empty_dirs: list[str]


_CONTENT_LIST_GLOB = "**/*_content_list.json"


def _pick_first_sorted(paths: list[Path]) -> Path | None:
    """Deterministically choose one path.

    rglob() order depends on the filesystem, which would make the selected
    content_list.json machine-dependent. Sort by (relative POSIX path) instead.
    """
    if not paths:
        return None
    return sorted(paths, key=lambda p: p.as_posix())[0]


def discover_papers_detailed(corpus_dir: Path) -> Discovery:
    """Walk corpus_dir and return papers plus an explicit skip ledger.

    Unlike v1, the choice of content_list.json / marker markdown is order
    independent (sorted), and every skipped paper is recorded with a reason so
    the omission is visible inside the artifact instead of only on stderr.
    """
    papers: list[Paper] = []
    skipped: list[dict] = []
    empty_dirs: list[str] = []
    if not corpus_dir.is_dir():
        raise ProfileError(f"corpus directory not found: {corpus_dir}")
    for paper_dir in sorted((p for p in corpus_dir.iterdir() if p.is_dir()),
                            key=lambda p: p.name):
        mineru_dir = paper_dir / "mineru"
        if not mineru_dir.is_dir():
            skipped.append({"paper_key": paper_dir.name, "reason": "no_mineru_dir"})
            continue
        v2_files = [f for f in mineru_dir.rglob("*_content_list.json")
                    if "_v2" not in f.name]
        cl_path = _pick_first_sorted(v2_files)
        if cl_path is None:
            empty_dirs.append(paper_dir.name)
            skipped.append({"paper_key": paper_dir.name, "reason": "no_content_list_json"})
            continue
        marker_md = None
        marker_dir = paper_dir / "marker"
        if marker_dir.is_dir():
            marker_md = _pick_first_sorted(list(marker_dir.rglob("*.md")))
        inputs = [{"artifact": "mineru_content_list", "sha256": sha256_file(cl_path)}]
        if marker_md is not None:
            inputs.append({"artifact": "marker_markdown", "sha256": sha256_file(marker_md)})
        inputs.extend(_figure_image_inputs(cl_path))
        upstream: dict = {}
        meta_path = paper_dir / "_META.json"
        if meta_path.is_file():
            inputs.append({"artifact": "paper_reader_meta", "sha256": sha256_file(meta_path)})
            try:
                meta_obj = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta_obj = None
            if isinstance(meta_obj, dict):
                raw_versions = meta_obj.get("engine_versions")
                versions: dict = {}
                if isinstance(raw_versions, dict):
                    # Contract (paper-reader): keys marker/mineru/torch/cuda/python;
                    # missing or undetectable engines are null, never omitted.
                    versions = {str(k): (str(v) if v not in (None, "") else None)
                                for k, v in raw_versions.items()}
                upstream = {
                    "engines": meta_obj.get("engines"),
                    "marker_ok": bool((meta_obj.get("marker") or {}).get("ok")),
                    "mineru_ok": bool((meta_obj.get("mineru") or {}).get("ok")),
                    "pdf_sha256_recorded": bool(meta_obj.get("pdf_sha256")),
                    "engine_versions": versions,
                    "engine_versions_source": meta_obj.get("engine_versions_source"),
                    # True only when at least one real version was recorded; a
                    # dict of all-nulls must NOT count as "recorded".
                    "versions_recorded": any(v for v in versions.values()),
                }
                # Only carried over when the conversion layer actually recorded it:
                # adding null keys would move every corpus audit trail for a
                # cross-check that has nothing to compare.
                for key, meta_key in (("language_recorded", "language"),
                                      ("cjk_ratio_recorded", "cjk_ratio"),
                                      ("language_source_recorded", "lang_source")):
                    if meta_obj.get(meta_key) is not None:
                        upstream[key] = meta_obj[meta_key]
        papers.append(Paper(
            name=paper_dir.name,
            content_list_path=cl_path,
            marker_md_path=marker_md,
            paper_key=paper_dir.name,
            inputs=inputs,
            upstream=upstream,
        ))
    if empty_dirs:
        for name in empty_dirs:
            print(f"[profile_papers] WARN: skipping '{name}' (no content_list.json)",
                  file=sys.stderr)
    if not papers:
        raise ProfileError(
            f"corpus '{corpus_dir}' contains no papers with parseable content_list.json; "
            f"run paper-reader on the source PDFs first"
        )
    papers.sort(key=lambda p: p.paper_key)
    return Discovery(papers=papers, skipped=skipped, empty_dirs=empty_dirs)


def discover_papers(corpus_dir: Path) -> list[Paper]:
    """Backward-compatible wrapper returning just the paper list."""
    return discover_papers_detailed(corpus_dir).papers


# ---------------------------------------------------------------------------
# Section title normalization + canonical mapping
# ---------------------------------------------------------------------------

_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"(?:section\s+)?\d+(?:\.\d+)*\.?\s*[:：]?\s+"   # 1 / 1. / 1.2 / Section 4:
    r"|[IVXLC]+\.\s+"
    r"|第[一二三四五六七八九十百千\d]+[章节]\s*"
    r"|[（(]\d+[）)]\s*"
    r")",
    re.IGNORECASE,
)
_TRAILING_PUNCT_RE = re.compile(r"[\s.。:：]+$")


def normalize_section_title(raw: str, keep_case: bool = False) -> str:
    """Strip numbering prefix and trailing punctuation.

    By default lowercases (used as a dict key for canonical lookup).
    Pass keep_case=True to preserve the original case (used for human-readable
    variant reporting, e.g. 'Introduction' rather than 'introduction').
    """
    s = raw.strip()
    s = _NUM_PREFIX_RE.sub("", s, count=1)
    s = _TRAILING_PUNCT_RE.sub("", s)
    s = s.strip()
    return s if keep_case else s.lower()


# Canonical labels: variants → single canonical key.
# Keys use snake_case so they can be used as JSON object keys / identifiers.
_CANONICAL_MAP: dict[str, str] = {
    "introduction": "introduction",
    "intro": "introduction",
    "background": "background",
    "motivation": "background",
    "related work": "related_work",
    "related works": "related_work",
    "related research": "related_work",
    "literature review": "related_work",
    "prior work": "related_work",
    "preliminaries": "preliminaries",
    "preliminary": "preliminaries",
    "problem definition": "preliminaries",
    "problem formulation": "preliminaries",
    "problem statement": "preliminaries",
    "definitions": "preliminaries",
    "notations": "preliminaries",
    "notation": "preliminaries",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "approach": "method",
    "our approach": "method",
    "proposed approach": "method",
    "proposed method": "method",
    "the proposed framework": "method",
    "framework": "method",
    "model": "method",
    "algorithm": "method",
    "algorithm design": "method",
    "solution approach": "method",
    "move evaluation and attribute matrix": "method",
    "tensor-based gpu acceleration framework": "method",
    "tensor-based gpu acceleration for local search operators": "method",
    "the proposed algorithm": "method",
    "our method": "method",
    "the proposed method": "method",
    "design": "method",
    "system design": "method",
    "architecture": "method",
    "system architecture": "method",
    "experiments": "experiments",
    "experiment": "experiments",
    "experimental results": "experiments",
    "results": "experiments",
    "evaluation": "experiments",
    "experiments and results": "experiments",
    "computational results": "experiments",
    "empirical study": "experiments",
    "empirical evaluation": "experiments",
    "case study": "experiments",
    "discussion": "discussion",
    "discussions": "discussion",
    "analysis": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "concluding remarks": "conclusion",
    "summary": "conclusion",
    "conclusion and future work": "conclusion",
    "future work": "conclusion",
    "abstract": "abstract",
    "keywords": "keywords",
    "acknowledgments": "acknowledgments",
    "acknowledgements": "acknowledgments",
    "references": "references",
    "bibliography": "references",
    "appendix": "appendix",
    "appendices": "appendix",
    "supplementary material": "appendix",
    "taxonomy": "taxonomy",
    "open challenges": "open_challenges",
    "future directions": "open_challenges",
}


def canonical_section_label(normalized_title: str) -> str:
    """Map a normalized section title to its canonical label.

    Four-stage resolution:
      0. Anchored appendix prefix ("appendix", "A Appendix: ..."), so a long
         appendix title cannot be re-routed by a keyword it happens to contain.
      1. Exact match in _CANONICAL_MAP (handles canonical forms + common synonyms).
      2. Keyword-based substring match (handles numbered subsection variants
         like '3.1 reformulating kd', '4.4 comparison with state-of-the-arts'
         that MinerU tags as level-2 headings).
      3. Fall back to the normalized title verbatim (rare/domain-specific
         sections are preserved as-is for the human-readable report).

    The keyword stage is what makes the profiler domain-agnostic: rather than
    enumerating every possible subsection name, we recognize the parent section
    by its characteristic keywords (experiment/ablation/baseline → experiments,
     distillation/architecture/method → method, etc.).
    """
    if _APPENDIX_PREFIX_RE.match(normalized_title):
        return "appendix"
    exact = _CANONICAL_MAP.get(normalized_title)
    if exact:
        return exact
    for pattern, canonical in _SECTION_KEYWORD_PATTERNS:
        if pattern.search(normalized_title):
            return canonical
    return normalized_title


import re as _re_for_patterns

# Keyword patterns applied AFTER exact-match lookup fails. Substring-based
# canonicalization lets us recognize numbered subsection variants like
# '3.1 reformulating kd' or '4.4 comparison with state-of-the-arts' that MinerU
# tags as level-2 headings, without enumerating every possible subsection name.
#
# ORDER MATTERS: more specific patterns must precede broader ones. E.g.
# 'ablation' must come before 'experiment' so 'ablation study' → experiments
# rather than being caught by a later general pattern.
_SECTION_KEYWORD_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (_re_for_patterns.compile(r"\b(ablation|ablating)\b"), "experiments"),
    (_re_for_patterns.compile(r"\bcomparison with (the )?state[- ]?of[- ]?the[- ]?art"), "experiments"),
    (_re_for_patterns.compile(r"\b(baseline|benchmark|evaluation results|main results|experimental results)\b"), "experiments"),
    (_re_for_patterns.compile(r"\b(experiment|experiments|experimental setup|implementation details|datasets? and metrics?|training details|results and analysis|qualitative results|quantitative results)\b"), "experiments"),
    (_re_for_patterns.compile(r"(消融|对比实验|实验结果|实验设置|数据集|训练细节|实现细节|可视化|效果|数值实验|结果对比|对比分析|敏感性分析|性能分析|算例|案例)"), "experiments"),
    (_re_for_patterns.compile(r"\b(sensitivity analysis|case stud(y|ies)|computational results|numerical experiments|performance study|comparison results)\b"), "experiments"),
    (_re_for_patterns.compile(r"\b(visualiz|visualization|reconstruction results?|image quality)\b"), "experiments"),
    (_re_for_patterns.compile(r"\b(distillation|distilling|kd|knowledge distillation)\b"), "method"),
    (_re_for_patterns.compile(r"\b(proposed (method|approach|framework)|our (method|approach|framework))\b"), "method"),
    (_re_for_patterns.compile(r"\b(network architecture|model architecture|architecture|backbone|encoder|decoder)\b"), "method"),
    (_re_for_patterns.compile(r"\b(loss function|loss design|objective function|training objective|attention transfer|feature distillation|contrastive)\b"), "method"),
    (_re_for_patterns.compile(r"\b(method|methods|methodology|approach|algorithm design|formulation|pipeline)\b"), "method"),
    (_re_for_patterns.compile(r"(算法|方法|模型|网络结构|损失函数|注意力机制|特征蒸馏|数学建模|建模|算子|分段函数|对偶|匹配机制|选择策略|构造|程序流程)"), "method"),
    (_re_for_patterns.compile(r"\b(related work|related works?|prior work|literature review)\b"), "related_work"),
    (_re_for_patterns.compile(r"\b(preliminaries|preliminary|background|problem (definition|formulation|statement|description|setting|settings))\b"), "preliminaries"),
    (_re_for_patterns.compile(r"\b(representation analysis|representation learning|notation|notations|definitions?)\b"), "preliminaries"),
    (_re_for_patterns.compile(r"(预备知识|相关工作|文献综述|问题定义|问题描述|符号说明|记号|模型假设|假设|参数说明|变量定义|参数定义|背景)"), "preliminaries"),
    (_re_for_patterns.compile(r"\b(discussion|discussions|analysis|analysis of|discussion and conclusion)\b"), "discussion"),
    (_re_for_patterns.compile(r"\b(model analysis|complexity analysis|theoretical analysis|running time|efficiency analysis)\b"), "discussion"),
    (_re_for_patterns.compile(r"(讨论|分析)"), "discussion"),
    (_re_for_patterns.compile(r"\b(conclusion|conclusions? and future work|concluding remarks|summary|future work|future directions?)\b"), "conclusion"),
    (_re_for_patterns.compile(r"(结论|总结|展望)"), "conclusion"),
    (_re_for_patterns.compile(r"(appendix|appendices|supplementary( material)?|附录|补充材料)"), "appendix"),
    (_re_for_patterns.compile(r"(acknowledg(e)?ments?|致谢|鸣谢)"), "acknowledgments"),
    (_re_for_patterns.compile(r"(references|bibliography|参考文献)"), "references"),
    (_re_for_patterns.compile(r"\b(introduction|motivation|contributions?)\b"), "introduction"),
    (_re_for_patterns.compile(r"(引言|绪论|介绍)"), "introduction"),
]


# ---------------------------------------------------------------------------
# Asset sub-type classification
# ---------------------------------------------------------------------------

# Image/chart sub-types. Heuristic: figures in the Method section are
# framework/architecture overviews; figures in Experiments are data plots.
_METHOD_SECTION_LABELS = {"method", "preliminaries", "background"}
_EXPERIMENT_SECTION_LABELS = {"experiments", "results", "discussion"}


def _classify_figure(section_label: str, block_type: str) -> str:
    """Classify an image/chart block by where it appears."""
    if block_type == "chart":
        if section_label in _EXPERIMENT_SECTION_LABELS:
            return "data-plot"
        return "chart-other"
    # block_type == "image"
    if section_label in _METHOD_SECTION_LABELS:
        return "framework-overview"
    if section_label in _EXPERIMENT_SECTION_LABELS:
        return "result-figure"
    if section_label == "introduction":
        return "concept-diagram"
    return "image-other"


def _classify_table(section_label: str) -> str:
    if section_label in _EXPERIMENT_SECTION_LABELS:
        return "benchmark-comparison"
    if section_label in _METHOD_SECTION_LABELS:
        return "notation-table"
    if section_label == "preliminaries":
        return "notation-table"
    return "table-other"


def _classify_equation(section_label: str) -> str:
    if section_label == "preliminaries":
        return "problem-definition"
    if section_label == "method":
        return "method-formulation"
    if section_label in _EXPERIMENT_SECTION_LABELS:
        return "evaluation-metric"
    return "equation-other"


# ---------------------------------------------------------------------------
# Section skeleton extraction
# ---------------------------------------------------------------------------

@dataclass
class SectionStat:
    canonical: str
    frequency: int = 0
    variants_seen: set[str] = field(default_factory=set)
    positions: list[float] = field(default_factory=list)
    word_shares: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "frequency": self.frequency,
            "variants_seen": sorted(self.variants_seen),
            "median_position": round(statistics.median(self.positions), 3) if self.positions else 0.0,
            "median_word_share": round(statistics.median(self.word_shares), 3) if self.word_shares else 0.0,
        }


def _is_title_block(b: dict) -> tuple[str, int] | None:
    """Return (title_text, level) if block b is a heading, else None.

    MinerU content_list.json represents headings as type="text" with a
    "text_level" field (1 = paper title, 2 = section, 3 = subsection).
    """
    if b.get("type") != "text":
        return None
    level = b.get("text_level")
    if level is None or not isinstance(level, int) or level < 1:
        return None
    text = b.get("text", "").strip()
    if not text:
        return None
    return text, level


def _block_word_count(b: dict) -> int:
    """Word count of a block.

    v2 also counts list / code / table blocks: ignoring them made
    median_word_share systematically low (tables and reference lists carry real
    content). Equation blocks count as 1 word so a formula-heavy section is not
    reported as empty.
    """
    btype = b.get("type")
    if btype == "text":
        return len(b.get("text", "").split())
    if btype == "equation":
        return 1
    if btype == "list":
        items = b.get("list_items") or b.get("items")
        if isinstance(items, list) and items:
            return len(" ".join(str(i) for i in items).split())
        return len(str(b.get("text", "")).split())
    if btype == "code":
        return len(str(b.get("code_body") or b.get("text") or "").split())
    if btype == "table":
        return len(re.sub(r"<[^>]+>", " ", str(b.get("table_body") or "")).split())
    return 0


def extract_section_skeleton(papers: list[Paper]) -> list[dict]:
    """Aggregate section-level headings across all papers into frequency stats.

    variants_seen stores each section's raw heading text verbatim (including
    original numbering like '1 Introduction', 'I. INTRODUCTION') rather than the
    normalized form, so the human-readable report shows what titles actually
    look like in the field. Canonicalization happens separately via
    canonical_section_label() for aggregation keys.
    """
    stats: dict[str, SectionStat] = defaultdict(lambda: SectionStat(canonical=""))
    for paper in papers:
        blocks = paper.load_blocks()
        section_starts: list[tuple[int, str, str]] = []
        total_blocks = len(blocks)
        for idx, b in enumerate(blocks):
            t = _is_title_block(b)
            if t and t[1] == 2:
                raw_title = t[0].strip()
                canonical = canonical_section_label(normalize_section_title(raw_title))
                section_starts.append((idx, raw_title, canonical))
        if not section_starts:
            continue
        total_words = sum(_block_word_count(b) for b in blocks) or 1
        for i, (idx, raw_title, canonical) in enumerate(section_starts):
            end_idx = section_starts[i + 1][0] if i + 1 < len(section_starts) else total_blocks
            section_words = sum(_block_word_count(b) for b in blocks[idx:end_idx])
            stat = stats[canonical]
            stat.canonical = canonical
            stat.frequency += 1
            stat.variants_seen.add(raw_title)
            stat.positions.append(idx / total_blocks if total_blocks else 0.0)
            stat.word_shares.append(section_words / total_words)
    # Sort by frequency desc, then alphabetical
    ordered = sorted(stats.values(), key=lambda s: (-s.frequency, s.canonical))
    return [s.to_dict() for s in ordered]


# ---------------------------------------------------------------------------
# Asset pattern extraction
# ---------------------------------------------------------------------------

@dataclass
class AssetPattern:
    section: str
    sub_type: str
    frequency: int = 0

    def to_dict(self) -> dict:
        return {"section": self.section, "sub_type": self.sub_type, "frequency": self.frequency}


def extract_asset_patterns(papers: list[Paper]) -> tuple[list[dict], list[dict], list[dict]]:
    """Attribute figures/tables/equations to their nearest preceding section."""
    fig_counter: Counter = Counter()
    tbl_counter: Counter = Counter()
    eq_counter: Counter = Counter()

    for paper in papers:
        blocks = paper.load_blocks()
        current_section = "front_matter"
        for b in blocks:
            t = _is_title_block(b)
            if t and t[1] == 2:
                current_section = canonical_section_label(normalize_section_title(t[0]))
                continue
            btype = b.get("type")
            if btype in ("image", "chart"):
                sub = _classify_figure(current_section, btype)
                fig_counter[(current_section, sub)] += 1
            elif btype == "table":
                sub = _classify_table(current_section)
                tbl_counter[(current_section, sub)] += 1
            elif btype == "equation":
                sub = _classify_equation(current_section)
                eq_counter[(current_section, sub)] += 1

    def _to_list(counter: Counter) -> list[dict]:
        items = [AssetPattern(section=s, sub_type=st, frequency=f)
                 for (s, st), f in counter.items()]
        items.sort(key=lambda a: (-a.frequency, a.section, a.sub_type))
        return [a.to_dict() for a in items]

    return _to_list(fig_counter), _to_list(tbl_counter), _to_list(eq_counter)


# ---------------------------------------------------------------------------
# Citation style detection
# ---------------------------------------------------------------------------

#: Full-width brackets dominate Chinese journals: measured on 10/10
#: 《运筹与管理》 papers every in-text citation is ［12］, so a half-width-only
#: pattern reported 0 numeric citations and citation_style = unknown.
_BRACKET_NUMERIC_RE = re.compile(r"[\[\uff3b]\s*\d+\s*[\]\uff3d]")
#: Numeric citation with ranges/lists ("[3-5]", "[1,2]"); the style detector above only
#: needs the single-bracket form, but the two-way check must expand ranges.
_CITATION_REF_RE = re.compile(r"[\[\uff3b]\s*(\d+(?:\s*[-\u2013,\uff0c]\s*\d+)*)\s*[\]\uff3d]")

#: A reference counts as "recent" when it is at most this many years older than the
#: newest reference of the same paper (self-referential on purpose: the paper's own
#: publication year is not reliably available).
_RECENT_REFERENCE_YEARS = 5

#: These metrics read the reference SECTION (excluded from canonical_text) plus the
#: body citations.  Not exactly "prose", so they stay out of draft contracts until a
#: draft-side implementation exists.
_SCOPE_REFERENCES = ("prose", "references")
_AUTHOR_YEAR_PAREN_RE = re.compile(
    r"\([A-Z][A-Za-z''-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z''-]+)*,?\s*\d{4}[a-z]?\)"
)
_NARRATIVE_RE = re.compile(
    r"[A-Z][A-Za-z''-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z''-]+)*\s*\(\d{4}[a-z]?\)"
)


def _paper_citation_text(paper: Paper) -> str:
    if paper.marker_md_path and paper.marker_md_path.exists():
        try:
            return paper.marker_md_path.read_text(encoding="utf-8")
        except OSError:
            pass
    # Fall back to concatenating text blocks
    blocks = paper.load_blocks()
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


@dataclass
class CitationStyleResult:
    detected: str
    # NOTE: the legacy field name "confidence" is kept for backward
    # compatibility but it is NOT a statistical confidence — it is the
    # dominance ratio of the winning style, max(ratio, 1-ratio). The honest
    # name is "separation", emitted alongside by _citation_meta().
    confidence: float
    evidence: dict


def _citation_meta(total: int) -> dict:
    return {
        "separation": None,
        "metric_spec": "M-CITSTYLE-50",
        "method": "rule",
        "unit": "ratio",
        "n": total,
        "state": "OBSERVED",
        "thresholds": {"ieee_numeric_min": 0.85, "author_year_max": 0.15},
        "note": ("legacy 'confidence' is a dominance ratio, not a calibrated "
                 "probability; do not compare it with model confidence scores."),
    }


def detect_citation_style(papers: list[Paper]) -> dict:
    numeric_total = 0
    author_year_total = 0
    for paper in papers:
        text = _paper_citation_text(paper)
        numeric_total += len(_BRACKET_NUMERIC_RE.findall(text))
        author_year_total += len(_AUTHOR_YEAR_PAREN_RE.findall(text))
        author_year_total += len(_NARRATIVE_RE.findall(text))
    total = numeric_total + author_year_total
    if total == 0:
        payload = CitationStyleResult(
            detected="unknown", confidence=0.0,
            evidence={"bracket_numeric_matches": 0, "author_year_matches": 0},
        ).__dict__
        payload.update(_citation_meta(0))
        payload["separation"] = 0.0
        return payload
    ratio = numeric_total / total
    separation = round(max(ratio, 1 - ratio), 3)
    if ratio >= 0.85:
        detected = "ieee-numeric"
    elif ratio <= 0.15:
        detected = "author-year"
    else:
        detected = "mixed"
    payload = CitationStyleResult(
        detected=detected,
        confidence=round(max(ratio, 1 - ratio), 3),
        evidence={"bracket_numeric_matches": numeric_total,
                  "author_year_matches": author_year_total},
    ).__dict__
    payload.update(_citation_meta(total))
    payload["separation"] = separation
    return payload


# ---------------------------------------------------------------------------
# Contribution phrases + reference count
# ---------------------------------------------------------------------------

_CONTRIBUTION_PHRASES = [
    r"our (?:main |key |primary )?contributions? (?:are|is)",
    r"in this paper,? we (?:propose|present|introduce)",
    r"we (?:propose|present|introduce|develop|design) (?:a |an |the )?",
    r"the (?:main |key )?contributions? of this (?:paper|work|article)",
    r"this paper (?:makes|presents|proposes) (?:the following |several )?(?:main |key )?contributions?",
]
_CONTRIBUTION_RE = re.compile(
    "|".join(f"(?:{p})" for p in _CONTRIBUTION_PHRASES), re.IGNORECASE
)


def _detect_contribution_phrases(papers: list[Paper]) -> list[str]:
    found: set[str] = set()
    for paper in papers:
        text = _paper_citation_text(paper)
        for m in _CONTRIBUTION_RE.finditer(text):
            found.add(m.group(0).strip())
    return sorted(found)


# A reference list entry, as MinerU renders it. v1 only accepted
# type == "text" blocks starting with "[N]" / "N. ", while real MinerU emits
# reference lists as type == "list" -> reference_count was silently 0.
_REF_BRACKET_RE = re.compile(r"^\s*[\[\uff3b]\s*\d+\s*[\]\uff3d]")
#: "12." / "12)" / "12、" ---- the separator must be followed by whitespace
#: or a CJK character, so a decimal ("1.5") is never read as an entry number.
_REF_NUMBERED_RE = re.compile(r"^\s*\d{1,3}[.)、．](?:\s|[\u4e00-\u9fff\uf900-\ufaff])")
_REF_AUTHOR_YEAR_RE = re.compile(r"^\s*[A-Z][A-Za-z'\-]+,\s*[A-Z]")
_REF_AUTHOR_YEAR_ALT_RE = re.compile(
    r"^\s*[A-Z][A-Za-z'\-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z'\-]+)*"
    r",?\s*\(\d{4}[a-z]?\)"
)
_REF_BRACKETED_AUTHOR_YEAR_RE = re.compile(
    r"^\s*\[[A-Z][^\]]{0,80}?(?:19|20)\d{2}[a-z]?\]"
)
_REF_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")
#: CJK ranges, used to tell "a Chinese entry written without spaces" from "a
#: stray line of body text".
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
#: GB/T 7714 document-type markers ("[J]." journal, "[D]." thesis, "[M]." book).
_REF_GB_T_MARKER_RE = re.compile(
    r"\[(?:J|M|D|C|N|S|P|R|A|G|Z|EB/OL|DB/OL|J/OL|M/OL|C/OL)\]")
#: A Chinese bibliography entry usually starts with "作者, 题名" / "作者. 题名".
_REF_CJK_AUTHOR_RE = re.compile(r"^\s*[\u4e00-\u9fff]{2,4}\s*[,，、.]")


def _looks_like_reference(text: str) -> bool:
    words = len(text.split())
    cjk = len(_CJK_CHAR_RE.findall(text))
    # An explicit entry marker is decisive on its own and must be checked before
    # the length guard: MinerU renders many Chinese-journal entries with no
    # ASCII spaces at all ("［13］LIBX，KRUSHINSKYD，REIJERSHA，etal．…"), which the
    # word guard would veto even though the bracket proves it is an entry.
    if _REF_BRACKET_RE.match(text) or _REF_NUMBERED_RE.match(text):
        return True
    # Chinese entries are written without spaces between words, so the word
    # guard alone dropped every one of them (measured: 10/10 《运筹与管理》
    # papers reported reference_count = 0).  Count CJK characters as well.
    if words < 5 and cjk < 8:
        return False
    if _REF_BRACKETED_AUTHOR_YEAR_RE.match(text):
        return True
    if _REF_AUTHOR_YEAR_RE.match(text) or _REF_AUTHOR_YEAR_ALT_RE.match(text):
        return True
    if cjk >= 8:
        # Chinese / GB-T 7714 shapes: a "[J]" type marker, or "作者, 题名 … 年".
        if _REF_GB_T_MARKER_RE.search(text) and _REF_YEAR_RE.search(text):
            return True
        if _REF_CJK_AUTHOR_RE.match(text) and _REF_YEAR_RE.search(text):
            return True
        return False
    # Fallback heuristic: capitalised entry containing a publication year.
    return text[:1].isupper() and bool(_REF_YEAR_RE.search(text))


def _split_reference_entries(chunks: list[str]) -> list[str]:
    """Split reference-section blocks/lists into individual entries.

    Handles the three shapes seen in the wild: one entry per block, one entry
    per list item, and several "[N] ... [N] ..." entries packed into one block.
    """
    entries: list[str] = []
    for chunk in chunks:
        for part in re.split(r"\n+", chunk):
            part = part.strip()
            if not part:
                continue
            starts = [m.start() for m in
                      re.finditer(r"[\[\uff3b]\s*\d+\s*[\]\uff3d]", part)]
            if len(starts) > 1 and starts[0] <= 2:
                for i, s in enumerate(starts):
                    end = starts[i + 1] if i + 1 < len(starts) else len(part)
                    seg = part[s:end].strip()
                    if seg:
                        entries.append(seg)
            else:
                entries.append(part)
    return [e for e in entries if _looks_like_reference(e)]


def _reference_chunks_with_source(paper: Paper) -> list[tuple[int, str, str]]:
    """(block_index, field, chunk) for the reference section (headings excluded).

    The block index and field name are what the evidence contract needs to re-open the
    source (form B); the chunk text alone cannot be verified.
    """
    out: list[tuple[int, str, str]] = []
    for block_index, (section, b) in enumerate(paper.labelled_blocks()):
        if _is_title_block(b):
            continue
        # Two independent signals, either is sufficient:
        #   (a) the block sits in a bibliography section, or
        #   (b) MinerU itself classified the block as reference text.
        if section != "references" and b.get("sub_type") != "ref_text":
            continue
        btype = b.get("type")
        if btype == "text":
            t = (b.get("text") or "").strip()
            if t:
                out.append((block_index, "text", t))
        elif btype == "list":
            items = b.get("list_items") or b.get("items")
            if isinstance(items, list) and items:
                out.append((block_index, "list_items",
                            " ".join(str(i).strip() for i in items
                                     if str(i).strip())))
            else:
                t = (b.get("text") or "").strip()
                if t:
                    out.append((block_index, "text", t))
    return out


def _reference_chunks(paper: Paper) -> list[str]:
    """Raw reference-section text/list chunks of one paper (headings excluded)."""
    return [chunk for _index, _field, chunk in _reference_chunks_with_source(paper)]


def count_references_for_paper(paper: Paper) -> tuple[int, list[str]]:
    entries = _split_reference_entries(_reference_chunks(paper))
    return len(entries), entries


def _cited_numbers(text: str, entry_count: int) -> set[int]:
    """Numeric citations in the body, with math intervals excluded.

    A range or list counts as citations only when EVERY number lies inside the
    reference list (1..entry_count): "[0,1]", "[-1,1]" and "[0,2]" are intervals in
    this domain, and counting them would invent citations of entries 0/1/2.  A single
    bracket is kept whatever its value, because an out-of-range single IS the dangling
    citation this metric exists to find.
    """
    cited: set[int] = set()
    for match in _CITATION_REF_RE.finditer(text):
        parts = [p.strip() for p in re.split(r"[,\uff0c]", match.group(1)) if p.strip()]
        numbers: list[int] = []
        single = len(parts) == 1
        for part in parts:
            rng = re.match(r"(\d+)\s*[-\u2013]\s*(\d+)", part)
            if rng:
                numbers.extend(range(int(rng.group(1)), int(rng.group(2)) + 1))
                single = False
            elif part.isdigit():
                numbers.append(int(part))
            else:
                single = False
        if not numbers:
            continue
        if single and len(numbers) == 1:
            cited.add(numbers[0])
        elif all(1 <= number <= entry_count for number in numbers):
            cited.update(numbers)
    return cited


def _reference_entry_sources(paper: Paper) -> list[dict]:
    """One record per reference entry, carrying a VERIFIABLE evidence coordinate.

    The excerpt is the BLOCK's field prefix, not the entry text: the evidence contract
    verifies form B with startswith() against the block field, and an entry that is the
    third in a block would never match.  The parsed entry travels beside it as "entry".
    """
    out: list[dict] = []
    for block_index, field_name, chunk in _reference_chunks_with_source(paper):
        prefix = chunk[:80]
        for entry in _split_reference_entries([chunk]):
            out.append({"block_index": block_index, "field": field_name,
                        "excerpt": prefix, "entry": entry})
    return out


def _citation_spans(text: str, limit: int) -> list[dict]:
    """Form-A evidence: the first few numeric citations with exact character spans."""
    spans: list[dict] = []
    for match in _CITATION_REF_RE.finditer(text):
        if len(spans) >= limit:
            break
        start = match.start()
        end = min(len(text), start + 80)
        spans.append({"span": [start, end], "excerpt": text[start:end],
                      "citation": match.group(0)})
    return spans


def _parse_reference_years(entries: list[str]) -> list[int]:
    """One publication year per entry, in entry order.

    The FIRST year-like token wins (in both GB/T and author-year shapes the publication
    year precedes volume/page numbers), and the optional LNCS-style suffix is stripped
    ("2020a" is a 2020 reference, and int("2020a") is simply a crash).  One year per
    entry keeps year_coverage a genuine share instead of letting a multi-year entry
    count twice - the first version of this function reported 100.8% coverage, which is
    impossible and was caught by the real-corpus run.
    """
    years: list[int] = []
    for entry in entries:
        matches = _REF_YEAR_RE.findall(entry)
        if not matches:
            continue
        digits = re.sub(r"\D", "", matches[0])
        if len(digits) == 4:
            years.append(int(digits))
    return years


def _reference_record(metric_spec: str, *, value, n: int, denominator: int,
                      evidence: dict, warnings: list[str]) -> dict:
    """Product-shaped record for the reference metrics (same contract as the streams)."""
    return {
        "value": None if value is None else round(value, 6),
        "n": int(n), "denominator": int(denominator), "unit": "ratio",
        "state": "OBSERVED", "method": "rule", "metric_spec": metric_spec,
        "scope": list(_SCOPE_REFERENCES), "evidence": evidence,
        "warnings": sorted(set(warnings)),
    }


def _reference_metrics(paper: Paper, text: str) -> dict:
    """M-REFAGE-53 (freshness) and M-REFLINK-54 (citation <-> list two-way check).

    The citation style is decided on the BODY text only: the numbered reference list
    itself would otherwise make every paper look numeric-style, and an author-year
    paper would then be reported as "100% uncited" instead of "not measurable here".
    """
    entries = _split_reference_entries(_reference_chunks(paper))
    count = len(entries)
    # Evidence must be re-openable: reference entries point at their block/field (form B),
    # and the citation metric points at exact character spans in the body (form A).
    entry_sources = _reference_entry_sources(paper)
    entry_samples = entry_sources[:3]
    if not count:
        return {
            metric_id: _reference_record(metric_id, value=None, n=0, denominator=0,
                                         evidence={"count": 0, "sample": []},
                                         warnings=["NO_REFERENCE_ENTRIES"])
            for metric_id in ("M-REFAGE-53", "M-REFLINK-54")}
    years = sorted(_parse_reference_years(entries))
    if not years:
        age = _reference_record(
            "M-REFAGE-53", value=None, n=0, denominator=0,
            evidence={"count": count, "sample": entry_samples,
                      "year_coverage": 0.0,
                      "recent_window_years": _RECENT_REFERENCE_YEARS},
            warnings=["NO_REFERENCE_YEARS"])
    else:
        newest = years[-1]
        window = newest - (_RECENT_REFERENCE_YEARS - 1)
        recent = sum(1 for year in years if year >= window)
        age_warnings = []
        if len(years) < count:
            age_warnings.append("REFERENCE_YEARS_INCOMPLETE")
        age = _reference_record(
            "M-REFAGE-53", value=recent / len(years), n=len(years),
            denominator=len(years),
            evidence={"count": len(years), "sample": entry_samples,
                      "year_coverage": round(len(years) / count, 6),
                      "newest_year": newest, "oldest_year": years[0],
                      "median_year": years[len(years) // 2],
                      "recent_entries": recent,
                      "recent_window_years": _RECENT_REFERENCE_YEARS,
                      # The headline is a RELATIVE reading (concentration near this
                      # paper's newest reference); the years travel so a corpus-level
                      # absolute anchor can be computed without re-parsing
                      # (cross-review M3).
                      "anchor": "self_newest",
                      "years": years},
            warnings=age_warnings)

    numeric = len(_BRACKET_NUMERIC_RE.findall(text))
    author_year = (len(_AUTHOR_YEAR_PAREN_RE.findall(text))
                   + len(_NARRATIVE_RE.findall(text)))
    style = {"numeric_matches": numeric, "author_year_matches": author_year}
    if numeric == 0 or author_year > numeric:
        link = _reference_record(
            "M-REFLINK-54", value=None, n=0, denominator=count,
            evidence={"count": count, "sample": entry_samples, "cited_count": 0,
                      "dangling": [], "uncited": [], "uncited_count": 0,
                      "citation_style": style},
            warnings=["CITATION_STYLE_NOT_NUMERIC"])
        return {"M-REFAGE-53": age, "M-REFLINK-54": link}

    cited = _cited_numbers(text, count)
    citation_samples = _citation_spans(text, 3)
    shared = len({number for number in cited if 1 <= number <= count})
    # Jaccard: |cited ∪ 1..N|.  The old denominator (|cited| + N) capped the value at
    # 0.5, so a corpus that cites everything read as "half of them do not match"
    # (cross-review blocker B2).
    union = len(set(cited) | set(range(1, count + 1)))
    dangling = sorted(number for number in cited if number > count)
    uncited = [index for index in range(1, count + 1) if index not in cited]
    link_warnings = []
    if dangling:
        link_warnings.append("DANGLING_CITATIONS")
    if uncited:
        link_warnings.append("UNCITED_REFERENCES")
    if not cited:
        link_warnings.append("NO_CITATIONS_FOUND")
    link = _reference_record(
        "M-REFLINK-54", value=shared / union, n=shared, denominator=union,
        evidence={"count": count, "sample": citation_samples or entry_samples,
                  "cited_count": len(cited),
                  "cited": sorted(cited)[:50], "dangling": dangling,
                  "uncited": uncited[:50], "uncited_count": len(uncited),
                  "citation_style": style},
        warnings=link_warnings)
    return {"M-REFAGE-53": age, "M-REFLINK-54": link}


def _count_references(papers: list[Paper]) -> dict:
    per_paper: dict[str, int] = {}
    sample: list[dict] = []
    for paper in papers:
        n, entries = count_references_for_paper(paper)
        per_paper[paper.paper_key] = n
        for e in entries[:1]:
            if len(sample) < 3:
                sample.append({"paper_key": paper.paper_key, "excerpt": e[:80]})
    counts = sorted(c for c in per_paper.values() if c > 0)
    base = {
        "metric_spec": "M-REFCNT-51",
        "method": "rule",
        "unit": "reference entries",
        "n_papers_with_references": len(counts),
        "per_paper": dict(sorted(per_paper.items())),
        "evidence": {"sample": sample},
    }
    if not counts:
        base.update({"median": 0, "p25": 0, "p75": 0})
        return base
    n = len(counts)

    def _pct(p: float) -> int:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return counts[idx]

    # Use the SAME nearest-rank convention as the corpus summary (the local
    # _pct() above is already nearest-rank); int(statistics.median(...)) would
    # be a different convention that only coincides on odd/even edge cases.
    base.update({"median": int(_percentile([float(c) for c in counts], 0.5)),
                 "p25": _pct(0.25), "p75": _pct(0.75), "quantile_method":
                 "nearest_rank_no_interpolation"})
    return base


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deterministic serialisation helpers
# ---------------------------------------------------------------------------

def _round_floats(obj, ndigits: int = 6):
    """Recursively round floats and replace NaN/Inf with None.

    NaN is not valid JSON, and float noise must not leak into the fingerprint.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, ndigits) for v in obj]
    return obj


def canonical_json_text(obj) -> str:
    """Byte-stable JSON: sorted keys, fixed indent, rounded floats, LF ending."""
    return json.dumps(_round_floats(obj), ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Optional metric-layer imports (lexicon_loader / text_metrics)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _import_sibling(module_name: str):
    """Import a sibling module from this directory (uv run / pytest safe)."""
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ProfileError(
            f"required sibling module '{module_name}' is missing: {exc}. "
            f"Expected at {_SCRIPTS_DIR / (module_name + '.py')}"
        ) from exc


def _load_lexicons():
    """Load one lexicon release per supported language (issue #13).

    English keeps the frozen v1 release; Chinese ships its own curated release.
    Loading both up front costs a few milliseconds and keeps the per-paper path
    free of I/O, so a mixed-language corpus is profiled in one pass.
    """
    loader = _import_sibling("lexicon_loader")
    bundles = {language: loader.load_lexicons(language=language)
               for language in loader.SUPPORTED_LEXICON_LANGUAGES}
    return loader, bundles


def _bundle_for(bundles: dict, language: str | None):
    """Lexicon release for a detected language, falling back to English.

    Chinese needs its own word lists (there is no public Chinese Hyland
    equivalent), so the language decides which release is loaded rather than
    which metrics are attempted - the capability matrix in text_metrics owns
    that decision.
    """
    if language and language in bundles:
        return bundles[language]
    return bundles["en"]


def _toolchain(bundle, bundles_used: tuple[str, ...] = ("en",),
               releases: dict | None = None,
               text_metrics_version: str | None = None) -> dict:
    """Versions that a third party needs in order to reproduce a number."""
    toolchain = {
        "python": platform.python_version(),
        "implementation": "stdlib-only",
        "third_party_dependencies": [],
        "profiler_version": PROFILER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "metric_spec_version": METRIC_SPEC_VERSION,
        # Which metric layer produced these numbers. Without it a result cannot
        # be attributed to a version, which is exactly what an audit needs
        # (see data/test-corpora.json).
        "text_metrics_version": text_metrics_version,
        # Text is normalised to NFC before any counting (see Paper.canonical_text).
        "unicode_norm": "NFC",
        "lexicon_version": bundle.version,
        "lexicon_fingerprint": bundle.fingerprint(),
    }
    # Only present when a non-English release actually took part: an English-only
    # corpus must keep its historical toolchain bytes identical.
    if any(language != "en" for language in bundles_used):
        releases = releases or {}
        toolchain["lexicon_releases"] = {
            language: {"version": releases[language].version,
                       "fingerprint": releases[language].fingerprint()}
            for language in sorted(bundles_used) if language in releases
        }
    return toolchain


# ---------------------------------------------------------------------------
# Paragraph metrics (M-PCNT-25) — structure-based, not prose-based
# ---------------------------------------------------------------------------

def _distribution(values: list[int]) -> dict:
    if not values:
        return {"median": 0, "p25": 0, "p75": 0, "std": 0.0}
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return float(s[idx])

    return {
        "median": pct(0.5),
        "p25": pct(0.25),
        "p75": pct(0.75),
        "std": statistics.pstdev(s) if n > 1 else 0.0,
    }


#: Chinese paragraph calibers (issue #13). A Chinese "paragraph" is measured in
#: cjk-units and the minimum is set low enough that a short real paragraph is not
#: discarded as a caption (15 characters would be far too strict).
_PARAGRAPH_MIN_CJK_UNITS = 40
_PARAGRAPH_UNIT_EN = "words/paragraph"
_PARAGRAPH_UNIT_CJK = "cjk-units/paragraph"


def _cjk_units_in_text(text: str) -> int:
    """CJK characters + ASCII alpha tokens, the Chinese length unit."""
    cjk = sum(1 for ch in text
              if "\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
              or "\uf900" <= ch <= "\ufaff")
    ascii_tokens = len([m for m in _ASCII_TOKEN_RE.finditer(text)])
    return cjk + ascii_tokens


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def compute_paragraph_metric(paper: Paper, language: str = "en") -> dict:
    """M-PCNT-25: paragraph count + word-length distribution.

    Definition: a paragraph is a text block with >= _PARAGRAPH_MIN_WORDS words,
    outside non-prose sections (references/appendix/keywords) and not a heading.
    Denominator: number of such paragraphs.
    """
    rows: list[tuple[int, str, int, str]] = []
    for idx, (section, b) in enumerate(paper.labelled_blocks()):
        if section in _NON_PROSE_SECTIONS or _is_title_block(b):
            continue
        if b.get("type") != "text":
            continue
        text = (b.get("text") or "").strip()
        # Chinese paragraphs carry no whitespace, so len(text.split()) collapses
        # to ~1 and the ">= 15 words" filter dropped every real paragraph
        # (measured earlier: a Chinese corpus reported ~62 "words"/paragraph for
        # the handful that survived). The CJK caliber counts CJK characters plus
        # ASCII tokens, and uses a lower minimum because a 15-*character* Chinese
        # paragraph is a caption, not a paragraph.
        if language == "zh":
            length = _cjk_units_in_text(text)
            threshold = _PARAGRAPH_MIN_CJK_UNITS
        else:
            length = len(text.split())
            threshold = _PARAGRAPH_MIN_WORDS
        if length >= threshold:
            rows.append((idx, section, length, text[:80]))
    words_list = [r[2] for r in rows]
    n = len(rows)
    longest = sorted(rows, key=lambda r: (-r[2], r[0]))[:3]
    return {
        "value": (sum(words_list) / n) if n else None,
        "n": n,
        "denominator": n,
        "unit": _PARAGRAPH_UNIT_CJK if language == "zh" else _PARAGRAPH_UNIT_EN,
        "state": "OBSERVED",
        "method": "rule",
        "metric_spec": "M-PCNT-25",
        "distribution": _distribution(words_list),
        "evidence": {
            "count": n,
            "sample": [{"block_index": r[0], "section": r[1],
                        "words" if language != "zh" else "cjk_units": r[2],
                        "excerpt": r[3]} for r in longest],
        },
        "warnings": [] if n else (
            ["no_paragraphs_above_min_cjk_units"] if language == "zh"
            else ["no_paragraphs_above_min_words"]),
    }


# ---------------------------------------------------------------------------
# Per-paper metric computation
# ---------------------------------------------------------------------------

def compute_paper_metrics(paper: Paper, bundles, text_metrics_mod) -> dict:
    text = paper.canonical_text()
    language = text_metrics_mod.detect_language(text)
    metrics = dict(text_metrics_mod.compute_text_metrics(
        text, _bundle_for(bundles, language["language"])))
    metrics["M-PCNT-25"] = compute_paragraph_metric(paper, language["language"])
    metrics.update(_stream_metrics(paper))
    metrics.update(_reference_metrics(paper, text))
    # Every metric declares the stream it reads; the prose metrics never carried
    # that field, which is how the scope stayed implicit until 2026-09-14.
    for record in metrics.values():
        record.setdefault("scope", list(_BASE_ARTIFACT["scope"]))
        # class travels with EVERY metric record, not only the stream ones: it is a
        # pure function of the metric, and a missing class would read as "unknown" in
        # the report and in the contract layer.  The text metrics measure the author's
        # own prose by definition (issue #18-9).
        record.setdefault("class", "author_style")
    return metrics


def _stream_metrics(paper: Paper) -> dict:
    """Figure/table metrics: language-independent, so the prose guard skips them.

    A parse failure becomes three explicit "not measured" records instead of an
    absent metric, so the corpus summary reports n_missing rather than pretending
    the paper had no figures.
    """
    doc_model = _import_sibling("doc_model")
    stream_metrics_mod = _import_sibling("stream_metrics")
    try:
        model = doc_model.parse_document(paper.content_list_path)
    except doc_model.DocumentParseError as exc:
        print(f"[profile_papers] WARN: stream metrics unavailable for "
              f"'{paper.paper_key}': {exc}", file=sys.stderr)
        return stream_metrics_mod.unavailable_records("CANONICAL_UNPARSEABLE")
    # Section labels for the placement metric.  labelled_blocks() walks the same
    # content_list, so its position IS the block index doc_model reports (doc_model
    # rejects non-dict entries, so the two lists cannot drift apart).
    sections = {index: section
                for index, (section, _block) in enumerate(paper.labelled_blocks())}
    # The canonical BODY is handed over explicitly: citations must not be searched in
    # a text that contains the bibliography itself, and the density denominator must
    # not include headings/front matter (cross-review H3).
    return stream_metrics_mod.stream_metrics(
        model, sections, paper.canonical_text())


def compute_section_metrics(paper: Paper, bundles, text_metrics_mod,
                            metrics_of_interest: tuple[str, ...]) -> dict:
    """Stratified values for the sections in _STRATIFIED_SECTIONS.

    Cross-section mixing is meaningless (Introduction and Method differ in
    sentence length and passive voice), so the corpus summary keeps them apart.
    """
    out: dict[str, dict] = {}
    for section, text in sorted(paper.section_texts().items()):
        if section not in _STRATIFIED_SECTIONS:
            continue
        computed = text_metrics_mod.compute_text_metrics(
            text, _bundle_for(bundles, text_metrics_mod.detect_language(text)["language"]))
        out[section] = {mid: (computed[mid].get("value") if mid in computed else None)
                        for mid in metrics_of_interest}
    return out


def compute_section_divergence(paper: Paper, bundles, text_metrics_mod) -> dict:
    """Compute abstract vs body stance/assertion divergence.

    Catches the classic overclaiming pattern: abstract has high booster and low hedge
    compared to the body text.
    """
    sec_texts = paper.section_texts()
    abstract_text = sec_texts.get("abstract", "").strip()
    if not abstract_text:
        return {
            "abstract_found": False,
            "abstract_booster": None,
            "body_booster": None,
            "abstract_hedge": None,
            "body_hedge": None,
            "booster_divergence_ratio": None,
            "hedge_divergence_ratio": None,
            "status": "abstract_not_found",
        }
    body_parts = [text for sec, text in sec_texts.items() if sec not in ("abstract", "references", "appendix")]
    body_text = "\n\n".join(body_parts).strip()
    if not body_text:
        return {
            "abstract_found": True,
            "abstract_booster": None,
            "body_booster": None,
            "abstract_hedge": None,
            "body_hedge": None,
            "booster_divergence_ratio": None,
            "hedge_divergence_ratio": None,
            "status": "insufficient_body_text",
        }

    lang = text_metrics_mod.detect_language(abstract_text)["language"]
    bundle = _bundle_for(bundles, lang)
    comp_abs = text_metrics_mod.compute_text_metrics(abstract_text, bundle)
    comp_body = text_metrics_mod.compute_text_metrics(body_text, bundle)

    abs_boo = comp_abs.get("M-BOO-15", {}).get("value")
    body_boo = comp_body.get("M-BOO-15", {}).get("value")
    abs_hed = comp_abs.get("M-HED-14", {}).get("value")
    body_hed = comp_body.get("M-HED-14", {}).get("value")

    boo_ratio = None
    if abs_boo is not None and body_boo is not None:
        boo_ratio = round(abs_boo / body_boo, 4) if body_boo > 0 else (1.0 if abs_boo == 0 else 999.0)

    hed_ratio = None
    if abs_hed is not None and body_hed is not None:
        hed_ratio = round(abs_hed / body_hed, 4) if body_hed > 0 else (1.0 if abs_hed == 0 else 0.0)

    return {
        "abstract_found": True,
        "abstract_booster": abs_boo,
        "body_booster": body_boo,
        "abstract_hedge": abs_hed,
        "body_hedge": body_hed,
        "booster_divergence_ratio": boo_ratio,
        "hedge_divergence_ratio": hed_ratio,
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# Corpus aggregation
# ---------------------------------------------------------------------------

_T_CRITICAL_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank quantile, NO interpolation.

    Defined as sorted_vals[round(p * (n-1))]. Consequence worth knowing: for an
    even-sized sample the value reported as "median" is the LOWER of the two
    middle observations, not their average -- i.e. it deliberately differs from
    statistics.median()/numpy's default linear interpolation. The summary carries
    quantile_method="nearest_rank_no_interpolation" so a consumer never has to
    guess which convention produced the number.
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    idx = max(0, min(n - 1, int(round(p * (n - 1)))))
    return float(sorted_vals[idx])


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _aggregate_metric(per_paper_values: dict[str, float], unit: str,
                      section_values: dict[str, list[float]],
                      record_codes: frozenset[str] = frozenset()) -> dict:
    """Corpus-level summary of one metric over per-paper values."""
    valid = {k: v for k, v in per_paper_values.items() if v is not None}
    missing = sorted(k for k, v in per_paper_values.items() if v is None)
    n_valid = len(valid)
    warnings: list[str] = []
    if n_valid == 0:
        # An INFERRED metric pending its calibration set (#23 stance layer) is
        # null on every paper for one named reason; reporting the small-n trio
        # here would bury that reason in noise, exactly the #13 failure mode.
        zero_warnings = (["NOT_IMPLEMENTED"] if "NOT_IMPLEMENTED" in record_codes
                         else ["NO_VALID_VALUES", "N_LT_5", "N_VALID_LT_3"])
        return {
            "unit": unit, "n_valid": 0, "n_missing": len(missing),
            "missing_papers": missing, "mean": None, "sd": None, "median": None,
            "p25": None, "p75": None, "iqr": None, "min": None, "max": None,
            "ci95_low": None, "ci95_high": None, "by_section": {},
            "warnings": zero_warnings,
        }
    vals = sorted(float(v) for v in valid.values())
    n = n_valid
    mean = sum(vals) / n
    sd = statistics.stdev(vals) if n > 1 else 0.0
    p25, p75 = _percentile(vals, 0.25), _percentile(vals, 0.75)
    iqr = p75 - p25
    if n < 5:
        warnings.append("N_LT_5")
    if n < 3:
        warnings.append("N_VALID_LT_3")
    if iqr == 0:
        warnings.append("IQR_ZERO")
    if len(missing) / max(1, len(per_paper_values)) > 0.3:
        warnings.append("HIGH_MISSING")
    tcrit = _T_CRITICAL_975.get(n - 1, 1.96)
    half = tcrit * sd / math.sqrt(n) if n > 1 else 0.0
    by_section: dict[str, dict] = {}
    for section, svals in sorted(section_values.items()):
        sv = sorted(float(v) for v in svals if v is not None)
        by_section[section] = {
            "n_valid": len(sv),
            "mean": (sum(sv) / len(sv)) if sv else None,
            "median": _percentile(sv, 0.5) if sv else None,
            # Explicit, never absent: a stratified value carries no evidence
            # pointer of its own. Trace a stratum back through
            # _per_paper_metrics.jsonl (per-paper records hold the samples).
            # See issue #6: an implicit missing key was indistinguishable from
            # "no evidence exists".
            "evidence": None,
        }
    return {
        "unit": unit, "n_valid": n, "n_missing": len(missing),
        "missing_papers": missing, "mean": mean, "sd": sd,
        "median": _percentile(vals, 0.5), "p25": p25, "p75": p75, "iqr": iqr,
        "min": vals[0], "max": vals[-1],
        "ci95_low": mean - half, "ci95_high": mean + half,
        "by_section": by_section, "warnings": warnings,
    }


# Languages this layer has validated rules for.  MUST mirror
# text_metrics.SUPPORTED_LANGUAGES (the single source of truth); a test asserts the
# two agree so this cannot drift again.  Until 2026-09-14 it said ("en",) while the
# metrics module already declared ("en", "zh") and measured Chinese - which is how a
# Chinese corpus ended up shipping "unsupported, all metrics null" next to real
# numbers (cross-review B1).
SUPPORTED_METRIC_LANGUAGES = ("en", "zh")


#: Sample-size noise, not a reason.  A metric can be null because the corpus is
#: small (these codes) or because the artifact genuinely has nothing to measure
#: (the cause codes below) - and only the second kind should be shown to a reader.
_MISSINGNESS_SMALL_N = frozenset({"NO_VALID_VALUES", "N_LT_5", "N_VALID_LT_3"})


def _is_missingness_cause(code: str) -> bool:
    """Does this warning code EXPLAIN why a metric has no value?

    A rule, not a hand-maintained list: every previous list missed a member the day
    a new metric introduced one (NO_FIGURES_OR_TABLES was the last miss), which is
    how small-n noise kept drowning the real cause.  NO_* / *_NOT_SUPPORTED are the
    naming conventions for "nothing to measure here"; the small-n trio is excluded
    because it is noise, and METRIC_SCOPE_MISSING names a declaration gap.
    """
    if code in _MISSINGNESS_SMALL_N:
        return False
    return (code.startswith("NO_") or code.endswith("_NOT_SUPPORTED")
            or code in {"CANONICAL_UNPARSEABLE", "SECTIONS_UNAVAILABLE",
                        "METRIC_SCOPE_MISSING",
                        "CITATION_STYLE_NOT_NUMERIC", "IMAGE_UNREADABLE",
                        "TABLE_BODY_EMPTY", "NO_FIGURES_OR_TABLES"})


def _suppress_language_dependent_paragraph_stats(metrics: dict) -> None:
    """Language guard for M-PCNT-25 when its rules do NOT cover the language.

    Still needed for languages outside SUPPORTED_METRIC_LANGUAGES, and it keeps the
    "suppress everything, including the count" behaviour: the paragraph filter is
    "len(text.split()) >= 15", which silently drops CJK paragraphs, so a
    whitespace-based count is not meaningful there either.

    NOTE (2026-09-14, cross-review M8): this docstring used to say Chinese was
    suppressed outright.  Since issue #13 the Chinese paragraph caliber is measured
    (cjk-units/paragraph) and this function is not reached for zh; the text above
    describes the remaining, genuinely unsupported languages.
    """
    pm = metrics.get("M-PCNT-25")
    if not isinstance(pm, dict):
        return
    pm["value"] = None
    pm["distribution"] = None
    pm["n"] = 0
    pm["denominator"] = 0
    pm["evidence"] = {"count": 0, "sample": []}
    pm["warnings"] = sorted(set(pm.get("warnings") or []) | {"LANGUAGE_NOT_SUPPORTED"})
    pm["note"] = ("suppressed: paragraph segmentation depends on whitespace "
                  "tokenisation, which CJK text does not provide (issue #10)")


def _language_facts(text_metrics_mod, text: str, metrics: dict) -> dict:
    """Per-paper language facts.

    Single source of truth is text_metrics.detect_language(). When the language
    cannot be judged we return language_supported = None, i.e. "not assessed" —
    NEVER True. A default of True would be actively dangerous: on a Chinese
    corpus it would let an artifact declare "language verified" while the
    numbers in it are all zeros.
    """
    declared = tuple(str(item) for item in
                     (getattr(text_metrics_mod, "SUPPORTED_LANGUAGES", None) or ()))
    detect = getattr(text_metrics_mod, "detect_language", None)
    if callable(detect):
        info = detect(text) or {}
        language = str(info.get("language", "unknown"))
        detector_flag = info.get("supported")
        if language == "unknown":
            # Not assessed is never True: an artifact must not claim "language
            # verified" when the detector could not decide.
            supported = None
        elif declared:
            # "Does this layer have rules for that language?" - the same question
            # validate_draft asks through _rule_languages().
            supported = language in declared
        else:
            supported = None if detector_flag is None else bool(detector_flag)
        return {
            "language": language,
            "cjk_ratio": info.get("cjk_ratio", 0.0),
            "language_supported": supported,
            # The detector's own flag is the Round-A "is the text predominantly
            # English" contract, NOT a capability verdict.  Kept for audit under a
            # name that says what it is, because conflating the two is exactly how
            # the false "Chinese unsupported" statement was produced.
            "detector_english_contract": (None if detector_flag is None
                                          else bool(detector_flag)),
        }
    for m in metrics.values():
        if isinstance(m, dict) and "language" in m:
            warnings = m.get("warnings") or []
            return {
                "language": m.get("language"),
                "cjk_ratio": m.get("cjk_ratio"),
                "language_supported": "LANGUAGE_NOT_SUPPORTED" not in warnings,
            }
    # Language layer not present yet: undetermined, not "OK".
    return {"language": "unknown", "cjk_ratio": None, "language_supported": None}


def _language_record_mismatch(record: dict, upstream: dict | None) -> dict | None:
    """Compare the conversion layer's recorded language with this layer's answer.

    Two layers answer the same question from different artifacts, so they *can*
    legitimately disagree: this layer measures its prose-filtered canonical text
    while paper-reader measures the raw content_list, and a paper whose body is
    English but whose references are Chinese may land on opposite sides of the 0.10
    threshold.  Silence would be the wrong answer - a reader would see one label and
    never learn that the other layer said something else - so a disagreement is
    reported instead of being averaged away.
    """
    recorded = _normalise_language((upstream or {}).get("language_recorded"))
    detected = _normalise_language(record.get("language"))
    if recorded is None:
        # Nothing definite was recorded upstream: no claim, nothing to compare.
        return None
    if detected is None:
        # Upstream made a claim and this layer could not measure anything.  That is
        # NOT "no difference" - treating it as silence would let a failed
        # measurement read exactly like agreement (see the R2 red line), so it gets
        # its own kind instead of being folded into the mismatch.
        return {
            "kind": "detect_missing",
            "recorded": recorded,
            "recorded_cjk_ratio": (upstream or {}).get("cjk_ratio_recorded"),
            "recorded_source": (upstream or {}).get("language_source_recorded"),
            "detected": record.get("language"),
            "detected_cjk_ratio": record.get("cjk_ratio"),
        }
    if recorded == detected:
        return None
    return {
        "kind": "mismatch",
        "recorded": recorded,
        "recorded_cjk_ratio": (upstream or {}).get("cjk_ratio_recorded"),
        "recorded_source": (upstream or {}).get("language_source_recorded"),
        "detected": detected,
        "detected_cjk_ratio": record.get("cjk_ratio"),
    }


def _normalise_language(value) -> str | None:
    """A definite language tag, or None when the value makes no claim.

    Case and region subtags are folded ("EN", "zh-CN" -> "en", "zh") so a label
    difference that is not a language difference cannot raise a false mismatch.
    """
    if not isinstance(value, str):
        return None
    tag = value.strip().lower().replace("_", "-")
    if not tag or tag == "unknown":
        return None
    return tag.split("-")[0]


# ---------------------------------------------------------------------------
# Base artifact census: what the metrics read, and what they never see
# ---------------------------------------------------------------------------
# The canonical text is prose-only on purpose (a table is not a sentence), but the
# scope was implicit until now: nothing recorded how much of the document the
# metrics never look at.  Measured on 21 real papers, type=="text" blocks carry
# 75.7% of the characters and only 20.7% of the digits - tables alone hold 72% of
# them.  The census makes that a stated number, so "why did this metric move" can
# be answered with "the table stream changed" instead of guessing.

_BASE_ARTIFACT = {
    "name": "mineru_content_list",
    "scope": ["prose"],
    "note": ("metrics are defined on the prose stream of the MinerU "
             "content_list.json; tables / figures / equations / footnotes / lists "
             "are censused but are never merged into the prose text"),
}

# The per-stream rules (which field holds the text, how table markup and LaTeX
# are normalised) live in doc_model.py: one parser for the base artifact, typed
# records for every consumer.  A second copy here is how the census once counted
# lists as "0 characters" - the field name differs per block type, and a local copy
# of the mapping is exactly what drifts.



def block_census(paper: "Paper") -> dict:
    """Per-stream census of one paper's canonical input.

    doc_model owns the parsing and the per-stream rules; this function only turns
    the typed census into the JSON shape the products carry.  Streams the metrics
    do not read are declared as such instead of being silently absent.
    """
    doc_model = _import_sibling("doc_model")
    model = doc_model.parse_document(paper.content_list_path)
    return {
        stream.value: {
            "blocks": census.blocks,
            "chars": census.chars,
            "digits": census.digits,
            "raw_chars": census.raw_chars,
            "caption_chars": census.caption_chars,
            "caption_digits": census.caption_digits,
            "dropped_by_metrics": census.dropped_by_metrics,
        }
        for stream, census in model.census().items()
    }


def _census_totals(records: list[dict]) -> dict:
    totals: dict[str, dict] = {}
    for record in records:
        for stream, stats in (record.get("block_census") or {}).items():
            acc = totals.setdefault(stream, {
                "papers": 0, "blocks": 0, "chars": 0, "digits": 0,
                "raw_chars": 0, "caption_chars": 0, "caption_digits": 0,
                "dropped_by_metrics": stats.get("dropped_by_metrics", True)})
            acc["papers"] += 1
            for key in ("blocks", "chars", "digits", "raw_chars", "caption_chars",
                        "caption_digits"):
                acc[key] += int(stats.get(key, 0))
    return {key: totals[key] for key in sorted(totals)}


def aggregate_corpus(records: list[dict], corpus_id: str | None = None) -> dict:
    """Aggregate per-paper records into _corpus_summary.json.

    Analysis unit = paper (sentences are observation units, not independent
    samples: pooling sentences across papers is pseudoreplication).
    Weighting = equal_paper. Missing values stay missing (never filled with 0).
    """
    n_papers = len(records)
    metric_ids: set[str] = set()
    for r in records:
        metric_ids.update(r["metrics"].keys())
    metrics: dict[str, dict] = {}
    corpus_warnings: list[dict] = []
    undeclared_scope: list[str] = []
    length_by_paper = {r["paper_key"]: r.get("n_tokens", 0) for r in records}
    # Per-metric explanation codes seen in the per-paper records. The summary has
    # to inherit them: otherwise a corpus-level row can only say "no valid value"
    # and loses whether the cause was the language or a metric-specific
    # capability gap (issue #13).
    metric_record_codes: dict[str, set[str]] = defaultdict(set)
    # How many papers raised each warning code, per metric.  The corpus summary used
    # to keep only NO_VALID_VALUES / N_LT_5 / N_VALID_LT_3, which answered "how many
    # were measured" but not "why not" (cross-review: an unmeasured M-REFAGE-53 said
    # nothing about NO_REFERENCE_ENTRIES).  Cause codes travel in the same dict the
    # consumer already reads, so the question is answered where it is asked.
    record_warning_counts: dict[str, Counter] = defaultdict(Counter)
    for mid in sorted(metric_ids):
        per_paper: dict[str, float] = {}
        unit = ""
        # Units of the MEASURED values only: a not-measured record carries a
        # placeholder unit and must not look like a second scale (issue #18-7).
        units_measured: set[str] = set()
        # author_style vs toolchain_defect travels with the summary, otherwise the
        # human report cannot split the tables and the contract layer cannot refuse
        # a defect metric (issue #18-9).
        metric_class = ""
        scope_seen: set[str] = set()
        section_values: dict[str, list[float]] = defaultdict(list)
        for r in records:
            m = r["metrics"].get(mid)
            if m is None:
                per_paper[r["paper_key"]] = None
                continue
            per_paper[r["paper_key"]] = m.get("value")
            if m.get("value") is not None and m.get("unit"):
                units_measured.add(str(m["unit"]))
            if not metric_class and m.get("class"):
                metric_class = str(m["class"])
            metric_record_codes[mid].update(m.get("warnings") or ())
            for code in (m.get("warnings") or ()):
                record_warning_counts[mid][str(code)] += 1
            unit = unit or m.get("unit", "")
            scope_seen.update(m.get("scope") or ())
            for section, smap in (r.get("section_metrics") or {}).items():
                if smap.get(mid) is not None:
                    section_values[section].append(float(smap[mid]))
        summary = _aggregate_metric(
            per_paper, unit, section_values,
            record_codes=frozenset(metric_record_codes[mid]))
        if metric_class:
            summary["class"] = metric_class
        if len(units_measured) > 1:
            # S-TBL-09 is per-1000-words for English and per-1000-cjk-units for
            # Chinese; a corpus mixing both would average two different scales.
            # Null (never 0) plus a named warning, and the colliding units are
            # listed so a reader sees WHICH two scales met (issue #18-7).
            summary["mean"] = None
            summary["units_measured"] = sorted(units_measured)
            summary["warnings"] = sorted(set(summary.get("warnings") or ())
                                         | {"MIXED_UNIT_AGGREGATION"})
        causes = record_warning_counts.get(mid)
        if causes:
            summary["record_warning_counts"] = dict(sorted(causes.items()))
            if not summary.get("n_valid"):
                # Nothing was measured: name every cause that any paper reported,
                # otherwise the summary only says "no valid values" and the real
                # reason stays buried in the per-paper records.
                summary["warnings"] = sorted(set(summary.get("warnings") or ())
                                             | set(causes))
        # Scope travels with the metric: a table metric that reached the corpus
        # summary labelled "prose" would be the very confusion this field prevents.
        # Records written before the field existed fall back to the corpus default
        # AND are named in a corpus warning, so the gap is visible rather than silent.
        if scope_seen:
            summary["scope"] = sorted(scope_seen)
        else:
            summary["scope"] = list(_BASE_ARTIFACT["scope"])
            undeclared_scope.append(mid)
        # Length confound: a metric that tracks paper length is not a style fact.
        pairs = [(length_by_paper[k], v) for k, v in per_paper.items() if v is not None]
        if len(pairs) >= 5:
            rho = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if rho is not None and abs(rho) > 0.3:
                summary["spearman_rho_vs_length"] = rho
                summary["warnings"].append("LENGTH_CORR")
        metrics[mid] = summary
        for code in summary["warnings"]:
            if code in {"N_LT_5", "N_VALID_LT_3", "NO_VALID_VALUES"}:
                corpus_warnings.append({"code": code, "metric": mid,
                                        "detail": f"n_valid={summary['n_valid']}"})
    # Upstream provenance: can a reader attribute a PDF -> Canonical drift?
    upstream_known = [r.get("upstream") or {} for r in records]
    upstream_known = [u for u in upstream_known if u]
    version_lists: dict[str, set] = defaultdict(set)
    for u in upstream_known:
        for engine, ver in (u.get("engine_versions") or {}).items():
            if ver:
                version_lists[engine].add(str(ver))
    upstream_summary = {
        "papers_with_paper_reader_meta": len(upstream_known),
        "engines": sorted({str(u.get("engines")) for u in upstream_known if u.get("engines")}),
        "marker_ok": sum(1 for u in upstream_known if u.get("marker_ok")),
        "mineru_ok": sum(1 for u in upstream_known if u.get("mineru_ok")),
        "pdf_sha256_recorded": sum(1 for u in upstream_known if u.get("pdf_sha256_recorded")),
        # Observed engine versions, per engine. Empty => nothing recorded.
        "engine_versions": {k: sorted(v) for k, v in sorted(version_lists.items())},
        "engine_versions_sources": sorted({str(u.get("engine_versions_source"))
                                           for u in upstream_known
                                           if u.get("engine_versions_source")}),
        "engine_versions_recorded": bool(version_lists),
    }
    for kind, code, why in (
            ("mismatch", "LANGUAGE_METADATA_MISMATCH",
             ("differs from the language detected here; the two layers read different "
              "artifacts, so the labels are allowed to differ - but a consumer must "
              "not treat them as interchangeable")),
            ("detect_missing", "LANGUAGE_DETECT_MISSING",
             ("could not be detected here at all; a missing measurement is not "
              "agreement"))):
        # No default kind.  A record without the key made no claim at all, and
        # defaulting it to "mismatch" (the earlier bug) made every AGREEING corpus
        # report every one of its papers as a mismatch - a deterministic false
        # positive that the per-paper tests and the corpus audits could not see.
        keys = sorted(r["paper_key"] for r in records
                      if (r.get("language_mismatch") or {}).get("kind") == kind)
        if not keys:
            continue
        sources = sorted({str((r.get("language_mismatch") or {}).get("recorded_source"))
                          for r in records
                          if (r.get("language_mismatch") or {}).get("kind") == kind
                          and (r.get("language_mismatch") or {}).get("recorded_source")})
        detail = (f"{len(keys)} paper(s): the language recorded by paper-reader "
                  f"{why} ({', '.join(keys[:5])}).")
        if sources:
            detail += " Recorded sources: " + ", ".join(sources) + "."
        corpus_warnings.append({"code": code, "metric": "", "detail": detail})
    if upstream_known and not upstream_summary["engine_versions_recorded"]:
        corpus_warnings.append({
            "code": "ENGINE_VERSION_NOT_RECORDED", "metric": "",
            "detail": ("paper-reader's _META.json carries no engine version, so a "
                       "PDF->Canonical drift can be detected but not attributed; "
                       "reproducibility here only covers Canonical Document -> metrics"),
        })

    # Section skew: one section dominating the corpus makes pooled means unsafe.
    section_counts: Counter = Counter()
    for r in records:
        for s in r.get("sections", []):
            section_counts[s] += 1
    for section, cnt in sorted(section_counts.items()):
        if n_papers and cnt / n_papers > 0.7 and section not in {"introduction", "method"}:
            corpus_warnings.append(
                {"code": "SECTION_SKEW", "metric": "",
                 "detail": f"section '{section}' present in {cnt}/{n_papers} papers"})
    # Language coverage: an unsupported language must be loud, not silent. Papers
    # in such a language contribute null values (counted as missing) and raise
    # CORPUS_LANGUAGE_UNSUPPORTED, so no downstream consumer can mistake
    # "not measured" for "zero".
    lang_counts: Counter = Counter()
    unsupported = 0
    undetermined = 0
    for r in records:
        lang_counts[str(r.get("language") or "unknown")] += 1
        if r.get("language_supported") is False:
            unsupported += 1
        elif r.get("language_supported") is None:
            undetermined += 1
    # True only when every paper was positively judged as a supported language.
    # None = "not assessed" (the language layer is absent or could not decide):
    # consumers must not read this as a pass.
    if unsupported:
        language_supported: bool | None = False
    elif undetermined:
        language_supported = None
    else:
        language_supported = True
    if undetermined and not unsupported:
        corpus_warnings.append({
            "code": "LANGUAGE_NOT_ASSESSED", "metric": "",
            "detail": (f"{undetermined}/{n_papers} paper(s) have no language verdict "
                       f"(language layer unavailable or text undecidable); "
                       f"language_supported is null meaning \"not assessed\", not \"supported\"."),
        })
    _small_n_codes = _MISSINGNESS_SMALL_N
    if language_supported is False:
        # Every text metric is null for the same reason, so 13x NO_VALID_VALUES +
        # 13x N_LT_5 + 13x N_VALID_LT_3 is pure noise that hides the real cause.
        # Collapse them into the single, actionable LANGUAGE_NOT_SUPPORTED code.
        # CAPABILITY_NOT_SUPPORTED (issue #13) is the per-metric sibling of
        # LANGUAGE_NOT_SUPPORTED: a Chinese corpus now measures the subset whose
        # rules are language-independent and reports the rest as unmeasurable, so
        # both codes have to count as "explained" before the small-n noise is
        # collapsed away.
        corpus_warnings = [w for w in corpus_warnings
                           if w["code"] not in _small_n_codes]
    # A per-metric cause survives wherever it exists, not only when the whole corpus
    # language is unsupported: a metric can be null for its own reason (no
    # bibliography, no tables, no figures), and NO_VALID_VALUES + N_LT_5 +
    # N_VALID_LT_3 would then be the only thing a consumer sees.  Cross-review found
    # exactly this: an unmeasured M-REFAGE-53 never mentioned NO_REFERENCE_ENTRIES.
    for mid, msum in metrics.items():
        if msum.get("n_valid"):
            continue
        codes = set(msum.get("warnings") or ())
        causes = {code for code in metric_record_codes.get(mid, set())
                  if _is_missingness_cause(code)}
        if not causes:
            continue
        msum["warnings"] = sorted((codes - _small_n_codes) | causes)
    if unsupported:
        corpus_warnings.append({
            "code": "CORPUS_LANGUAGE_UNSUPPORTED", "metric": "",
            "detail": (f"{unsupported}/{n_papers} paper(s) are in a language this "
                       f"layer has no validated rules for "
                       f"{list(SUPPORTED_METRIC_LANGUAGES)}; the metrics that need "
                       f"those rules are reported as null and counted as missing, "
                       f"never as 0. Language-independent metrics may still carry "
                       f"values - check their warnings per metric."),
        })
    # Absolute counterpart to M-REFAGE-53.  That metric anchors on each paper's own
    # NEWEST reference, which makes it comparable across eras but unable to notice a
    # reference list that is uniformly old (2026 paper citing only 2005 works scores
    # high).  The absolute reading only makes sense with a corpus-wide anchor, so it
    # lives here, computed from the years each paper already reports.
    all_years = [int(year) for r in records
                 for year in (((r.get("metrics") or {}).get("M-REFAGE-53") or {})
                              .get("evidence") or {}).get("years", [])]
    if all_years:
        corpus_anchor = max(all_years)
        window = _RECENT_REFERENCE_YEARS - 1
        recent = sum(1 for year in all_years if year >= corpus_anchor - window)
        reference_freshness = {
            "anchor_year": corpus_anchor,
            "anchor_kind": "corpus_max_reference_year",
            "window_years": _RECENT_REFERENCE_YEARS,
            "entries_with_year": len(all_years),
            "absolute_recent_share": round(recent / len(all_years), 6),
            "note": ("M-REFAGE-53 uses a per-paper anchor (cross-era comparable); this "
                     "is the absolute reading against one corpus-wide anchor."),
        }
    else:
        reference_freshness = None
    # How much text was kept out of the canonical body, and why (issue: 前置页
    # 混入正文). Aggregated over papers so the filtering rule is auditable.
    dropped_totals: dict[str, dict[str, int]] = {}
    for r in records:
        for reason, stats in (r.get("non_prose_dropped") or {}).items():
            acc = dropped_totals.setdefault(reason, {"papers": 0, "blocks": 0, "words": 0})
            acc["papers"] += 1
            acc["blocks"] += int(stats.get("blocks", 0))
            acc["words"] += int(stats.get("words", 0))
    # Every metric states which stream it reads.  The scope used to be implicit,
    # and that is exactly how "digits that live in tables" got mistaken for
    # "digits lost in conversion": a number without its stream is not evidence.
    if undeclared_scope:
        corpus_warnings.append({
            "code": "METRIC_SCOPE_MISSING", "metric": "",
            "detail": ("these metrics declared no stream and fall back to the corpus "
                       "default scope: " + ", ".join(sorted(undeclared_scope))
                       + "; an implicit scope is how table digits were once read as "
                         "lost digits")})
    out = {
        "schema_version": SCHEMA_VERSION,
        "analysis_unit": "paper",
        "base_artifact": dict(_BASE_ARTIFACT),
        "block_census": _census_totals(records),
        "non_prose_dropped": {k: dropped_totals[k] for k in sorted(dropped_totals)},
        "weight_mode": "equal_paper",
        # metrics[id].mean is the mean of the per-paper means, NOT a pooled
        # statistic.  The documented headline numbers of S-REF-14 were pooled
        # counts and disagreed with the product by 11% (issue #18-5).
        "mean_basis": "mean_of_per_paper_means",
        "languages": {k: lang_counts[k] for k in sorted(lang_counts)},
        "language_supported": language_supported,
        # Median/p25/p75 are nearest-rank at index round(p * (n-1)) with round-half-even,
        # no interpolation.  For an even n that is the LOWER middle observation only when
        # round((n-1)/2) rounds down (n = 2 mod 4); for n = 0 mod 4 it rounds up, which is
        # why the blanket "even n -> lower middle" claim used to be wrong
        # (2026-09-14 cross-review, M1).
        "quantile_method": "nearest_rank_no_interpolation",
        "n_papers": n_papers,
        "upstream": upstream_summary,
        "metrics": metrics,
        "corpus_warnings": sorted(corpus_warnings, key=lambda w: (w["code"], w["metric"])),
    }
    if reference_freshness is not None:
        out["reference_freshness"] = reference_freshness
    if corpus_id is not None:
        # Lets a downstream contract document name the exact corpus it was
        # derived from (traceability from clause -> corpus -> input hashes).
        out["corpus_id"] = corpus_id
    return out


def _corpus_fingerprint(records: list[dict]) -> str:
    """Content hash of every input artifact in the corpus (order independent)."""
    payload = sorted(
        (r["paper_key"], a["artifact"], a["sha256"])
        for r in records for a in r.get("inputs", [])
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


_LOCAL_CONFOUND_METRICS = tuple(
    m for m in ("M-SLEN-01", "M-PCNT-25", "M-HED-14", "M-BOO-15",
                "M-CONN-30", "M-NOM-10", "M-PAS-09")
)


def run_profile(corpus_dir: Path, out_dir: Path,
                include_section_metrics: bool = True) -> dict:
    """Profile the corpus and write the five artifacts to out_dir."""
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    discovery = discover_papers_detailed(corpus_dir)
    papers = discovery.papers
    loader, bundles = _load_lexicons()
    text_metrics_mod = _import_sibling("text_metrics")
    # Which lexicon releases actually took part, so the toolchain record can name
    # them (an English-only corpus keeps its historical toolchain bytes).
    releases_used: set[str] = set()

    skeleton = extract_section_skeleton(papers)
    figures, tables, equations = extract_asset_patterns(papers)
    citation_style = detect_citation_style(papers)
    contribution_phrases = _detect_contribution_phrases(papers)
    reference_count = _count_references(papers)

    records: list[dict] = []
    for paper in papers:
        metrics = compute_paper_metrics(paper, bundles, text_metrics_mod)
        section_metrics = (
            compute_section_metrics(paper, bundles, text_metrics_mod,
                                    _LOCAL_CONFOUND_METRICS)
            if include_section_metrics else {}
        )
        text = paper.canonical_text()
        section_divergence = compute_section_divergence(paper, bundles, text_metrics_mod)
        record: dict = {
            "paper_key": paper.paper_key,
            "non_prose_dropped": paper.non_prose_dropped,
            "inputs": paper.inputs,
            "sections": paper.sections_present(),
            "n_tokens": len(text_metrics_mod.tokenize(text)),
            "metrics": metrics,
            "section_metrics": section_metrics,
            "section_divergence": section_divergence,
            "upstream": paper.upstream,
            "warnings": list(paper.warnings),
        }
        record.update(_language_facts(text_metrics_mod, text, metrics))
        mismatch = _language_record_mismatch(record, paper.upstream)
        if mismatch:
            record["language_mismatch"] = mismatch
            code = ("LANGUAGE_DETECT_MISSING"
                    if mismatch.get("kind") == "detect_missing"
                    else "LANGUAGE_METADATA_MISMATCH")
            record["warnings"] = sorted(set(record["warnings"]) | {code})
        record["block_census"] = block_census(paper)
        releases_used.add(_bundle_for(bundles, record.get("language")).language)
        if record.get("language_supported") is False and record.get("language") not in (
                "en", "zh"):
            # M-PCNT-25 is only suppressed for languages we cannot measure at
            # all; Chinese now has a CJK caliber, so it is computed there.
            _suppress_language_dependent_paragraph_stats(record["metrics"])
        records.append(record)

    corpus_id = _corpus_fingerprint(records)
    summary = aggregate_corpus(records, corpus_id)

    profile = {
        "schema_version": SCHEMA_VERSION,
        "metric_spec_version": METRIC_SPEC_VERSION,
        "toolchain": _toolchain(bundles["en"], tuple(sorted(releases_used)) or ("en",),
                                bundles,
                                getattr(text_metrics_mod, "TEXT_METRICS_VERSION", None)),
        "meta": {
            "profiler_version": PROFILER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "paper_count": len(papers),
            # NOTE: no corpus_path here. A machine-specific absolute path inside
            # a fingerprinted artifact would break cross-path / cross-machine
            # reproducibility. The path lives in _run_meta.json (not part of the
            # fingerprint); the corpus identity is corpus.id (content hash).
        },
        "corpus": {
            "id": corpus_id,
            "n_papers": len(papers),
            "profiled": [{"paper_key": r["paper_key"], "inputs": r["inputs"]}
                         for r in records],
            "skipped": discovery.skipped,
        },
        "lexicons": bundles["en"].to_manifest(),
        # Chinese word lists are a separate release; only listed when used.
        "lexicons_by_language": {
            language: bundles[language].to_manifest()
            for language in sorted(releases_used) if language != "en"
        },
        "section_skeleton": skeleton,
        "figure_placement_patterns": figures,
        "table_placement_patterns": tables,
        "equation_placement_patterns": equations,
        "citation_style": citation_style,
        "reference_count": reference_count,
        "contribution_phrases": contribution_phrases,
        "corpus_summary": summary,
    }

    # Round once, then derive BOTH views from the same rounded payload.
    # (Rendering the markdown from the un-rounded in-memory dict used to emit
    # 17-digit floats that the JSON did not contain -> human and machine views
    # disagreed, and the markdown escaped the determinism check.)
    rounded = _round_floats(profile)
    (out_dir / "_domain_profile.json").write_text(
        canonical_json_text(rounded), encoding="utf-8")
    (out_dir / "_domain_profile.md").write_text(_render_md(rounded), encoding="utf-8")
    (out_dir / "_corpus_summary.json").write_text(
        canonical_json_text(summary), encoding="utf-8")
    with (out_dir / "_per_paper_metrics.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(_round_floats(r), ensure_ascii=False,
                                sort_keys=True) + "\n")
    (out_dir / "_run_meta.json").write_text(canonical_json_text({
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "corpus_path": str(corpus_dir),
        "out_dir": str(out_dir),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "profiler_version": PROFILER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "corpus_id": corpus_id,
    }), encoding="utf-8")
    return rounded


_TICK = chr(96)


def _render_md(profile: dict) -> str:
    """Render the human-readable report deterministically from the JSON.

    Both views come from one source of truth, so the human report and the
    machine profile can never disagree (explainability requirement E6).
    """
    lines: list[str] = []
    meta = profile["meta"]
    lines.append("# 领域写作规范摘要 (Domain Profile)")
    lines.append("")
    lines.append("> **声明**：本画像指标仅表示目标语料可测特征的实证常模分布，不构成对论文科学创新性、实验充分性与学术质量的裁判。")
    lines.append("")
    lines.append(f"- 论文数量: {meta['paper_count']}")
    lines.append(f"- Profiler 版本: {meta['profiler_version']}")
    lines.append(f"- schema 版本: {meta['schema_version']}")
    lines.append(f"- 指标规范版本: {profile.get('metric_spec_version', '')}")
    lines.append(f"- 语料指纹 corpus id: {_TICK}{profile['corpus']['id']}{_TICK}")
    lines.append(f"- 词表指纹: {_TICK}{profile['toolchain']['lexicon_fingerprint']}{_TICK}")
    lines.append(f"- 依赖: {profile['toolchain']['implementation']}（无第三方依赖，无 LLM 调用）")
    base = (profile.get("corpus_summary") or {}).get("base_artifact") or {}
    if base:
        lines.append(f"- 指标基座: {base.get('name')}（范围: {', '.join(base.get('scope') or [])}）")
    census = (profile.get("corpus_summary") or {}).get("block_census") or {}
    ignored = [f"{k} {v['blocks']}块/{v['digits']}数字"
               for k, v in sorted(census.items()) if v.get("dropped_by_metrics")]
    if ignored:
        lines.append(f"- 未被指标读取的流: {'；'.join(ignored)}")
    skipped = profile["corpus"].get("skipped") or []
    if skipped:
        lines.append(f"- 跳过论文: {len(skipped)} 篇（原因见 _domain_profile.json 的 corpus.skipped）")
    lines.append("")
    # Upstream provenance (issue #7): the machine profile already carried
    # upstream.* but the human report dropped it, so a reader could not tell a
    # measured engine version from an environment estimate.
    upstream = (profile.get("corpus_summary") or {}).get("upstream") or {}
    if upstream:
        lines.append("## 上游溯源 (Upstream Provenance)")
        lines.append("")
        lines.append(f"- 记录到 paper-reader meta 的论文: {upstream.get('papers_with_paper_reader_meta', 0)}")
        lines.append(f"- 引擎版本来源: {', '.join(upstream.get('engine_versions_sources') or []) or '未记录'}")
        lines.append(f"- pdf_sha256 已记录: {upstream.get('pdf_sha256_recorded', 0)} 篇")
        for engine, versions in sorted((upstream.get("engine_versions") or {}).items()):
            lines.append(f"- {engine}: {', '.join(versions) or '未记录'}")
        lines.append("")
        lines.append("> **来源等级**：" + _TICK + "conversion_time" + _TICK
                     + " = 转换当时写入的真实版本（可证明转换环境）；"
                     + _TICK + "current_env_estimate" + _TICK
                     + " = 当时未记录、用当前环境版本回填的**估计值**，不得当作转换时实测；"
                     "缺失则为未知，不填 0。")
        lines.append("")
    lines.append("## 章节骨架 (Section Skeleton)")
    lines.append("")
    lines.append("| 规范标签 | 出现频次 | 变体示例 | 中位位置 | 中位字数占比 |")
    lines.append("|---------|---------|---------|---------|------------|")
    for s in profile["section_skeleton"][:15]:
        variants = ", ".join(s["variants_seen"][:3])
        lines.append(
            f"| {s['canonical']} | {s['frequency']} | {variants} | "
            f"{s['median_position']} | {s['median_word_share']} |"
        )
    lines.append("")
    lines.append("## 图片使用模式 (Figure Placement)")
    lines.append("")
    lines.append("| 章节 | 子类型 | 频次 |")
    lines.append("|------|--------|------|")
    for f in profile["figure_placement_patterns"][:15]:
        lines.append(f"| {f['section']} | {f['sub_type']} | {f['frequency']} |")
    lines.append("")
    lines.append("## 表格使用模式 (Table Placement)")
    lines.append("")
    lines.append("| 章节 | 子类型 | 频次 |")
    lines.append("|------|--------|------|")
    for t in profile["table_placement_patterns"][:15]:
        lines.append(f"| {t['section']} | {t['sub_type']} | {t['frequency']} |")
    lines.append("")
    lines.append("## 公式使用模式 (Equation Placement)")
    lines.append("")
    lines.append("| 章节 | 子类型 | 频次 |")
    lines.append("|------|--------|------|")
    for e in profile["equation_placement_patterns"][:15]:
        lines.append(f"| {e['section']} | {e['sub_type']} | {e['frequency']} |")
    lines.append("")
    cs = profile["citation_style"]
    lines.append("## 引文风格 (Citation Style)")
    lines.append("")
    lines.append(f"- 检测结果: **{cs['detected']}** (置信度 {cs['confidence']})")
    lines.append(f"- 数字方括号引用 [N] 次数: {cs['evidence']['bracket_numeric_matches']}")
    lines.append(f"- 作者-年份引用次数: {cs['evidence']['author_year_matches']}")
    lines.append("")
    rc = profile["reference_count"]
    lines.append("## 参考文献数量分布")
    lines.append("")
    lines.append(f"- 中位数: {rc['median']} 篇")
    lines.append(f"- 25 分位: {rc['p25']} 篇")
    lines.append(f"- 75 分位: {rc['p75']} 篇")
    lines.append("")
    cp = profile["contribution_phrases"]
    if cp:
        lines.append("## 贡献声明句式 (Contribution Phrases)")
        lines.append("")
        for p in cp[:10]:
            lines.append(f"- {_TICK}{p}{_TICK}")
        lines.append("")
    summary = profile.get("corpus_summary") or {}
    metrics = summary.get("metrics") or {}
    if metrics:
        # Split by class when the records carry one: a toolchain defect (LaTeX
        # residue, an empty table body, an unusable extracted raster) and an author
        # habit are the same three numbers in one table, so Mode B could not tell
        # "this journal writes long sentences" from "the converter ate the table"
        # (issue #18-9).  Records written before the field keep the old rendering.
        classes = {m.get("class") for m in metrics.values()}
        grouped: list[tuple[str, list[tuple[str, dict]]]] = []
        if classes == {None}:
            grouped.append(("写作特征指标 (Style Metrics, OBSERVED)", list(metrics.items())))
        else:
            for cls, title in (("author_style", "写作特征指标 · 作者风格类 (author_style)"),
                               (None, "写作特征指标 · 未分类 (class 缺失)"),
                               ("toolchain_defect",
                                "工具链缺陷类 (toolchain_defect) — 不是写作风格，不得作为规范")):
                rows = [(mid, m) for mid, m in metrics.items() if m.get("class") == cls]
                if rows:
                    grouped.append((title, rows))
        for title, rows in grouped:
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| 指标 | 单位 | n | 中位数 | IQR | 95% CI | 告警 |")
            lines.append("|------|------|---|--------|-----|--------|------|")
            for mid, m in rows:
                ci = ""
                if m.get("ci95_low") is not None and m.get("ci95_high") is not None:
                    ci = f"[{m['ci95_low']}, {m['ci95_high']}]"
                warn = ", ".join(m.get("warnings") or [])
                med = m.get("median")
                iqr = m.get("iqr")
                lines.append(
                    f"| {mid} | {m.get('unit') or ''} | {m.get('n_valid')} | "
                    f"{'' if med is None else med} | {'' if iqr is None else iqr} | {ci} | {warn} |")
            lines.append("")
        lines.append("注：分析单位为**论文**（n 为有效论文数），不是句子；缺失值保持空缺、从不填 0。"
                     "n_valid < 5 的指标只作参考，不得据此得出期刊级结论。")
        lines.append("")
        if "toolchain_defect" in classes:
            lines.append("> **红线**：" + _TICK + "toolchain_defect" + _TICK
                         + " 类指标测的是**转换链缺陷**（LaTeX 残留 / 表格正文缺失 / 抽取图分辨率），"
                           "不是该作者或该刊的写作风格，**不得**作为写作规范或生成目标。")
            lines.append("")
    freshness = summary.get("reference_freshness")
    if freshness:
        # The md report used to drop this field, so the only ABSOLUTE reading of
        # reference recency existed in the JSON alone while M-REFAGE-53 (per-paper
        # anchor) was the one a human saw.  The anchor year is printed WITH the
        # reading: one mis-parsed year poisons the corpus anchor (vrp-en reads
        # 0.000731 against a 2041 anchor), and a silent number would hide that.
        lines.append("## 参考文献绝对新鲜度 (Reference Freshness)")
        lines.append("")
        lines.append(f"- 锚年（{freshness.get('anchor_kind')}）: {freshness.get('anchor_year')}")
        lines.append(f"- 近 {freshness.get('window_years')} 年文献占比（绝对口径）: "
                     f"{freshness.get('absolute_recent_share')}"
                     f"（有年份的文献 {freshness.get('entries_with_year')} 条）")
        lines.append("")
    warns = summary.get("corpus_warnings") or []
    if warns:
        lines.append("## 语料告警 (Corpus Warnings)")
        lines.append("")
        for w in warns:
            lines.append(f"- {_TICK}{w['code']}{_TICK} {w.get('metric') or ''} — {w.get('detail') or ''}")
        lines.append("")
    lines.append("---")
    lines.append("由 profile_papers.py 确定性生成（同一输入两次运行逐字节相同）。")
    lines.append("指标定义、公式与不可推断边界见 references/metric-definitions.md。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Determinism self-check
# ---------------------------------------------------------------------------

# Files covered by the bit-level reproducibility guarantee. _run_meta.json is
# deliberately excluded: it carries wall-clock time and host identity.
FINGERPRINTED_FILES = ("_domain_profile.json", "_domain_profile.md",
                       "_per_paper_metrics.jsonl", "_corpus_summary.json")


def verify_determinism(corpus_dir: Path, out_dir: Path,
                       include_section_metrics: bool = True) -> dict:
    """Re-run the profiler into a temp dir and byte-compare the fingerprints."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pp_verify_") as tmp:
        tmp_out = Path(tmp)
        run_profile(corpus_dir, tmp_out, include_section_metrics=include_section_metrics)
        result: dict = {"ok": True, "checked": list(FINGERPRINTED_FILES), "diffs": []}
        for name in FINGERPRINTED_FILES:
            a, b = out_dir / name, tmp_out / name
            if not a.exists() or not b.exists():
                result["ok"] = False
                which = "out_dir" if not a.exists() else "temp"
                result["diffs"].append(f"{name}: missing in {which}")
                continue
            if a.read_bytes() != b.read_bytes():
                result["ok"] = False
                result["diffs"].append(f"{name}: bytes differ")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile a paper-analysis/ corpus for the paper-metrics journal mode.",
    )
    parser.add_argument("--corpus", required=True, help="paper-analysis/ directory")
    parser.add_argument("--out", "--output", dest="out", default=None,
                        help="output directory for _domain_profile.{json,md} + metric artifacts "
                             "(default: unified tree <cwd>/skills-output/thesis/paper-metrics/<timestamp>/, "
                             "docs/specs/OUTPUT.md C-1)")
    parser.add_argument("--min-papers", type=int, default=3,
                        help="warn if fewer papers are found (default: 3)")
    parser.add_argument("--no-section-metrics", action="store_true",
                        help="skip stratified per-section metrics (faster)")
    parser.add_argument("--verify", action="store_true",
                        help="re-run into a temp dir and assert fingerprints are byte-identical")
    args = parser.parse_args()
    if args.out is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
        from common import run_dir  # noqa: E402
        args.out = run_dir("thesis", "paper-metrics")


    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        raise SystemExit(f"corpus directory not found: {corpus}")
    out = Path(args.out).resolve()
    section_metrics = not args.no_section_metrics

    profile = run_profile(corpus, out, include_section_metrics=section_metrics)
    if profile["meta"]["paper_count"] < args.min_papers:
        print(f"[profile_papers] WARN: only {profile['meta']['paper_count']} papers found; "
              f"results may not be statistically representative", file=sys.stderr)
    for name in ("_domain_profile.json", "_domain_profile.md", "_corpus_summary.json",
                 "_per_paper_metrics.jsonl", "_run_meta.json"):
        print(f"[profile_papers] wrote {out / name}")
    print(f"[profile_papers] papers profiled: {profile['meta']['paper_count']}")
    skipped = profile["corpus"].get("skipped") or []
    if skipped:
        print(f"[profile_papers] papers skipped: {len(skipped)}")
    print(f"[profile_papers] sections detected: {len(profile['section_skeleton'])}")
    print(f"[profile_papers] citation style: {profile['citation_style']['detected']}")
    rcnt = profile["reference_count"]
    print(f"[profile_papers] reference_count median: {rcnt.get('median')} "
          f"(papers with refs: {rcnt.get('n_papers_with_references')})")
    print(f"[profile_papers] corpus id: {profile['corpus']['id']}")
    warns = (profile.get("corpus_summary") or {}).get("corpus_warnings") or []
    if warns:
        print(f"[profile_papers] corpus warnings: "
              f"{', '.join(sorted({w['code'] for w in warns}))}")

    if args.verify:
        res = verify_determinism(corpus, out, include_section_metrics=section_metrics)
        if res["ok"]:
            print(f"[profile_papers] VERIFY OK: {len(res['checked'])} fingerprinted files "
                  f"byte-identical across runs")
        else:
            for d in res["diffs"]:
                print(f"[profile_papers] VERIFY FAIL: {d}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
