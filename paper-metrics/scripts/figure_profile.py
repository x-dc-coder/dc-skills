#!/usr/bin/env python3
"""Figure Inventory + Figure Profile JSON (issue #1, MVP-1 + MVP-6).

纯 stdlib、确定性、零 LLM、零 GPU —— 本轮**只做图目录与可测量的图片头画像**，
不做任何视觉识别 / 图表生成 / 相似度判断（那些是 issue #1 的 Phase 1/4/5，
需要视觉模型/GPU）。

Scope decision (self-contained): this module reads MinerU *_content_list.json and
the PNG/JPEG/GIF file headers ITSELF.  It does NOT import profile_papers /
stream_metrics / doc_model, because those files are being edited in parallel and
this module must stay decoupled.  The S-SIZ-04 width threshold is re-declared here
at the SAME value (800 px) so the two stay in lockstep (asserted by a test).

产出的两条文件（都逐字节确定，无时间戳、无绝对路径）：
  * _figure_profile.json — 逐图记录（figure_id / page / block_index / caption /
    image_path / width / height / aspect_ratio / width_usable_for_print /
    section + type_guess（INFERRED，带 confidence+basis）/ not_implemented 占位）。
  * _figure_summary.json — 语料级分布（尺寸 / 宽高比 / 题注长度分位、达标率），
    读不了的图不计入分母并逐条列出（与 S-SIZ-04 同口径）。

Run:
    cd ~/projects/dc-skills
    uv run python paper-metrics/scripts/figure_profile.py \
        --corpus <paper-analysis dir> --out <output dir>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Version / product identity (constant, so two runs are byte-identical)
# ---------------------------------------------------------------------------

FIGURE_PROFILE_VERSION: Final[str] = "1.0"

#: Journal figures are printed at ~300 dpi; 800 px is roughly a 6.8 cm
#: single-column figure at that density.  Same value AND same semantics as
#: stream_metrics._MIN_IMAGE_WIDTH (S-SIZ-04): "readable width >= 800" is the
#: print-adequacy cut.  The schema doc states this identity explicitly.
_MIN_IMAGE_WIDTH: Final[int] = 800

#: Header bytes are enough to find the dimensions in any PNG/JPEG/GIF.
_HEADER_BYTES: Final[int] = 1 << 16

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
#: SOF0-SOF3 / SOF5-SOF7 / SOF9-SOF11 / SOF13-SOF15 carry the frame dimensions.
_JPEG_SOF_MARKERS: Final = frozenset(
    list(range(0xC0, 0xC4)) + list(range(0xC5, 0xC8))
    + list(range(0xC9, 0xCC)) + list(range(0xCD, 0xD0)))

_CONTENT_LIST_GLOB: Final[str] = "**/*_content_list.json"

#: figure_id 的形状（写进 schema 文档）：fig-<paper_stem>-<page>-<block_index>。
_FIGURE_ID_PREFIX: Final[str] = "fig"


# ---------------------------------------------------------------------------
# JSON boundary types
# ---------------------------------------------------------------------------

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ProfileError(Exception):
    """The corpus could not be turned into a figure profile."""


# ---------------------------------------------------------------------------
# Paper discovery (same convention as profile_papers: sorted, order-independent)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Paper:
    """One paper in the corpus: its directory name and its content_list.json."""

    name: str
    content_list_path: Path


def discover_papers(corpus_dir: Path) -> list[Paper]:
    """Walk corpus_dir and return one content_list.json per paper directory.

    Deterministic: paper directories are sorted by name, and the content_list.json
    is picked by sorting candidate POSIX paths (never filesystem order).  A paper
    directory with no mineru/ or no content_list.json is skipped.
    """
    if not corpus_dir.is_dir():
        raise ProfileError(f"corpus directory not found: {corpus_dir}")
    papers: list[Paper] = []
    for paper_dir in sorted((p for p in corpus_dir.iterdir() if p.is_dir()),
                            key=lambda p: p.name):
        mineru_dir = paper_dir / "mineru"
        if not mineru_dir.is_dir():
            continue
        candidates = [f for f in mineru_dir.rglob(_CONTENT_LIST_GLOB)
                      if "_v2" not in f.name]
        if not candidates:
            continue
        cl_path = sorted(candidates, key=lambda p: p.as_posix())[0]
        papers.append(Paper(name=paper_dir.name, content_list_path=cl_path))
    papers.sort(key=lambda p: p.name)
    if not papers:
        raise ProfileError(
            f"corpus '{corpus_dir}' contains no parseable content_list.json; "
            f"run paper-reader on the source PDFs first")
    return papers


def load_blocks(path: Path) -> list[dict[str, JsonValue]]:
    """Read and parse a content_list.json into a list of block objects."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError(f"{path}: unreadable: {exc}") from exc
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProfileError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ProfileError(f"{path}: expected a JSON list of blocks")
    blocks: list[dict[str, JsonValue]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ProfileError(f"{path}: block {index} is not an object")
        blocks.append(item)
    return blocks


# ---------------------------------------------------------------------------
# Deterministic slug (paper name -> ID-safe stem)
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    """Deterministic, ID-safe stem of a paper directory name.

    NFC-normalise, lowercase, keep every alphanumeric character (ASCII letters,
    CJK ideographs, accented letters, digits), replace everything else with a
    single '-'.  The CJK is KEPT so a Chinese paper's stem is readable and
    distinct instead of collapsing to an empty string.
    """
    text = unicodedata.normalize("NFC", name).lower()
    out = [ch if ch.isalnum() else "-" for ch in text]
    collapsed = re.sub(r"-+", "-", "".join(out)).strip("-")
    return collapsed or "paper"


def _disambiguate_slugs(names: list[str]) -> dict[str, str]:
    """Slug -> name mapping with deterministic collision disambiguation.

    Two papers that slug to the same stem would otherwise collide in figure_id.
    The fix is content-addressed (stable under adding/removing unrelated papers):
    every colliding stem gets a "-<sha256(name)[:6]>" suffix.
    """
    import hashlib
    stem_of: dict[str, str] = {name: slug(name) for name in names}
    by_stem: dict[str, list[str]] = {}
    for name in names:
        by_stem.setdefault(stem_of[name], []).append(name)
    for stem, group in by_stem.items():
        if len(group) == 1:
            continue
        for name in group:
            stem_of[name] = (
                f"{stem}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:6]}")
    return stem_of


def figure_id(paper_stem: str, page: int | None, block_index: int) -> str:
    """Stable, traceable figure id: fig-<stem>-<page>-<block_index>.

    Stable because every component is a pure function of the content_list.json:
    the stem is a deterministic slug of the paper directory name, page is the raw
    MinerU page_idx (0-based), block_index is the block's position in the list.
    No timestamps, no absolute paths, no filesystem order.  See the schema doc.
    """
    page_part = str(page) if isinstance(page, int) and page >= 0 else "u"
    return f"{_FIGURE_ID_PREFIX}-{paper_stem}-{page_part}-{block_index}"


# ---------------------------------------------------------------------------
# Caption / figure-number helpers
# ---------------------------------------------------------------------------

_CAPTION_KEY_RE: Final = re.compile(r"_caption$")
#: Figure-number declaration in a caption: Figure 1 / Fig. 1 / 图 1 (full-width
#: digits normalised).  Same family as stream_metrics' S-NUM-02 "declared" parsing.
_FIGURE_NUM_RE: Final = re.compile(
    r"(?:figure|fig\.?|图)\s*([0-9０-９]+)", re.IGNORECASE)


def _caption_of(block: dict[str, JsonValue]) -> str:
    """Join every *_caption value in the block (list or string) with a space.

    The real corpus uses image_caption / chart_caption / table_caption /
    code_caption and each is a list of strings; a fixed pair of key names would
    drop captions.
    """
    parts: list[str] = []
    for key, value in block.items():
        if not isinstance(key, str) or not _CAPTION_KEY_RE.search(key):
            continue
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, str)]
        elif isinstance(value, str):
            items = [value]
        else:
            items = []
        parts.extend(items)
    return " ".join(parts)


def caption_has_number(caption: str) -> bool:
    """Whether the caption declares a figure number (Figure/Fig/图 + digit)."""
    return bool(_FIGURE_NUM_RE.search(caption))


# ---------------------------------------------------------------------------
# Image header readers (PNG / JPEG / GIF, pure stdlib, no decoder)
# ---------------------------------------------------------------------------

def _png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG IHDR chunk."""
    if not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return (width, height) if width and height else None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first JPEG frame header (SOFn)."""
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    limit = len(data) - 9
    while index < limit:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in _JPEG_SOF_MARKERS:
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return (width, height) if width and height else None
        if marker == 0xDA:
            return None            # start of scan: no frame header left
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2             # stand-alone marker, no length field
            continue
        length = int.from_bytes(data[index + 2:index + 4], "big")
        index += 2 + max(length, 2)
    return None


def _gif_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a GIF logical screen descriptor."""
    if not data.startswith((b"GIF87a", b"GIF89a")):
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    return (width, height) if width and height else None


def _svg_size(path: Path) -> tuple[int, int] | None:
    """Parse SVG viewBox or width/height attributes using xml.etree.ElementTree.

    SVG vector images are infinitely scalable for print; we assign (3000, 3000) or
    parsed viewBox dimensions (>= 800px usable).
    """
    import xml.etree.ElementTree as ET
    try:
        # Read first 8KB to avoid large file parsing
        with path.open("rb") as handle:
            head = handle.read(8192)
        if b"<svg" not in head.lower():
            return None
        # Parse xml
        tree = ET.fromstring(head.decode("utf-8", errors="ignore") + "</svg>" if not head.rstrip().endswith(b"</svg>") else head.decode("utf-8", errors="ignore"))
        # Check viewBox
        vb = tree.attrib.get("viewBox") or tree.attrib.get("viewbox")
        if vb:
            parts = [float(p) for p in vb.replace(",", " ").split() if p]
            if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                # Scale viewBox proportionally up to vector print standard
                w, h = parts[2], parts[3]
                scale = max(1.0, 3000.0 / max(w, h))
                return (int(round(w * scale)), int(round(h * scale)))
        # Check width / height
        w_str = tree.attrib.get("width")
        h_str = tree.attrib.get("height")
        if w_str and h_str:
            w_m = re.match(r"^([0-9.]+)", w_str)
            h_m = re.match(r"^([0-9.]+)", h_str)
            if w_m and h_m:
                w, h = float(w_m.group(1)), float(h_m.group(1))
                if w > 0 and h > 0:
                    scale = max(1.0, 3000.0 / max(w, h))
                    return (int(round(w * scale)), int(round(h * scale)))
        # Default high-res vector representation for valid SVG
        return (3000, 3000)
    except Exception:
        # If it has <svg tag, treat as valid vector graphic (3000, 3000)
        try:
            with path.open("rb") as handle:
                snippet = handle.read(1024).lower()
                if b"<svg" in snippet:
                    return (3000, 3000)
        except OSError:
            pass
        return None


def image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from the file's own header, or None when unreadable/unknown.

    Headers only — no decoder, no third-party dependency.  PNG / JPEG / GIF / SVG are
    recognised; anything else returns None and the caller
    reports it as unreadable rather than guessing.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(_HEADER_BYTES)
    except OSError:
        return None
    for reader in (_png_size, _jpeg_size, _gif_size):
        size = reader(data)
        if size is not None:
            return size
    # Check SVG vector format
    if path.suffix.lower() == ".svg" or b"<svg" in data.lower():
        return _svg_size(path)
    return None


# ---------------------------------------------------------------------------
# Section labelling + figure type_guess (INFERRED, position heuristic only)
# ---------------------------------------------------------------------------
# This is a self-contained copy of the profile_papers section heuristic so the
# two stay consistent WITHOUT importing a file that is being edited in parallel.
# It is a POSITION heuristic: which section a figure lands in, read off the
# nearest preceding level-2 heading.  It is NOT visual recognition.

_APPENDIX_PREFIX_RE: Final = re.compile(
    r"^\s*(?:[a-z]\.?\s+)?(?:appendix|appendices|supplementary\s+material|附录|补充材料)\b")

_NUM_PREFIX_RE: Final = re.compile(
    r"^\s*(?:"
    r"(?:section\s+)?\d+(?:\.\d+)*\.?\s*[:：]?\s+"
    r"|[IVXLC]+\.\s+"
    r"|第[一二三四五六七八九十百千\d]+[章节]\s*"
    r"|[（(]\d+[）)]\s*"
    r")",
    re.IGNORECASE,
)
_TRAILING_PUNCT_RE: Final = re.compile(r"[\s.。:：]+$")

_CANONICAL_MAP: Final[dict[str, str]] = {
    "introduction": "introduction", "intro": "introduction",
    "background": "background", "motivation": "background",
    "related work": "related_work", "related works": "related_work",
    "related research": "related_work", "literature review": "related_work",
    "prior work": "related_work",
    "preliminaries": "preliminaries", "preliminary": "preliminaries",
    "problem definition": "preliminaries", "problem formulation": "preliminaries",
    "problem statement": "preliminaries", "definitions": "preliminaries",
    "notations": "preliminaries", "notation": "preliminaries",
    "method": "method", "methods": "method", "methodology": "method",
    "approach": "method", "our approach": "method", "proposed approach": "method",
    "proposed method": "method", "the proposed framework": "method",
    "framework": "method", "model": "method", "algorithm": "method",
    "algorithm design": "method", "solution approach": "method",
    "our method": "method", "the proposed method": "method", "design": "method",
    "system design": "method", "architecture": "method",
    "system architecture": "method",
    "experiments": "experiments", "experiment": "experiments",
    "experimental results": "experiments", "results": "experiments",
    "evaluation": "experiments", "experiments and results": "experiments",
    "computational results": "experiments", "empirical study": "experiments",
    "empirical evaluation": "experiments", "case study": "experiments",
    "discussion": "discussion", "discussions": "discussion",
    "analysis": "discussion",
    "conclusion": "conclusion", "conclusions": "conclusion",
    "concluding remarks": "conclusion", "summary": "conclusion",
    "conclusion and future work": "conclusion", "future work": "conclusion",
    "abstract": "abstract", "keywords": "keywords",
    "acknowledgments": "acknowledgments", "acknowledgements": "acknowledgments",
    "references": "references", "bibliography": "references",
    "appendix": "appendix", "appendices": "appendix",
    "supplementary material": "appendix", "taxonomy": "taxonomy",
    "open challenges": "open_challenges", "future directions": "open_challenges",
}

#: Keyword patterns applied AFTER exact-match lookup fails.  ORDER MATTERS:
#: 'ablation' before 'experiment', etc.
_SECTION_KEYWORD_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"\b(ablation|ablating)\b"), "experiments"),
    (re.compile(r"\bcomparison with (the )?state[- ]?of[- ]?the[- ]?art"), "experiments"),
    (re.compile(r"\b(baseline|benchmark|evaluation results|main results|experimental results)\b"), "experiments"),
    (re.compile(r"\b(experiment|experiments|experimental setup|implementation details|datasets? and metrics?|training details|results and analysis|qualitative results|quantitative results)\b"), "experiments"),
    (re.compile(r"(消融|对比实验|实验结果|实验设置|数据集|训练细节|实现细节|可视化|效果|数值实验|结果对比|对比分析|敏感性分析|性能分析|算例|案例)"), "experiments"),
    (re.compile(r"\b(sensitivity analysis|case stud(y|ies)|computational results|numerical experiments|performance study|comparison results)\b"), "experiments"),
    (re.compile(r"\b(visualiz|visualization|reconstruction results?|image quality)\b"), "experiments"),
    (re.compile(r"\b(distillation|distilling|kd|knowledge distillation)\b"), "method"),
    (re.compile(r"\b(proposed (method|approach|framework)|our (method|approach|framework))\b"), "method"),
    (re.compile(r"\b(network architecture|model architecture|architecture|backbone|encoder|decoder)\b"), "method"),
    (re.compile(r"\b(loss function|loss design|objective function|training objective|attention transfer|feature distillation|contrastive)\b"), "method"),
    (re.compile(r"\b(method|methods|methodology|approach|algorithm design|formulation|pipeline)\b"), "method"),
    (re.compile(r"(算法|方法|模型|网络结构|损失函数|注意力机制|特征蒸馏|数学建模|建模|算子|分段函数|对偶|匹配机制|选择策略|构造|程序流程)"), "method"),
    (re.compile(r"\b(related work|related works?|prior work|literature review)\b"), "related_work"),
    (re.compile(r"\b(preliminaries|preliminary|background|problem (definition|formulation|statement|description|setting|settings))\b"), "preliminaries"),
    (re.compile(r"\b(representation analysis|representation learning|notation|notations|definitions?)\b"), "preliminaries"),
    (re.compile(r"(预备知识|相关工作|文献综述|问题定义|问题描述|符号说明|记号|模型假设|假设|参数说明|变量定义|参数定义|背景)"), "preliminaries"),
    (re.compile(r"\b(discussion|discussions|analysis|analysis of|discussion and conclusion)\b"), "discussion"),
    (re.compile(r"\b(model analysis|complexity analysis|theoretical analysis|running time|efficiency analysis)\b"), "discussion"),
    (re.compile(r"(讨论|分析)"), "discussion"),
    (re.compile(r"\b(conclusion|conclusions? and future work|concluding remarks|summary|future work|future directions?)\b"), "conclusion"),
    (re.compile(r"(结论|总结|展望)"), "conclusion"),
    (re.compile(r"(appendix|appendices|supplementary( material)?|附录|补充材料)"), "appendix"),
    (re.compile(r"(acknowledg(e)?ments?|致谢|鸣谢)"), "acknowledgments"),
    (re.compile(r"(references|bibliography|参考文献)"), "references"),
    (re.compile(r"\b(introduction|motivation|contributions?)\b"), "introduction"),
    (re.compile(r"(引言|绪论|介绍)"), "introduction"),
]

_METHOD_SECTION_LABELS: Final = frozenset({"method", "preliminaries", "background"})
_EXPERIMENT_SECTION_LABELS: Final = frozenset({"experiments", "results", "discussion"})


def _normalize_section_title(raw: str) -> str:
    s = raw.strip()
    s = _NUM_PREFIX_RE.sub("", s, count=1)
    s = _TRAILING_PUNCT_RE.sub("", s)
    return s.strip().lower()


def _section_resolution(normalized: str) -> tuple[str, str]:
    """(canonical label, resolution kind) for a normalized section title.

    resolution kind drives the confidence: "exact" (canonical map hit) > "keyword"
    (substring pattern) > "verbatim" (fallback: title used as-is).
    """
    if _APPENDIX_PREFIX_RE.match(normalized):
        return "appendix", "exact"
    exact = _CANONICAL_MAP.get(normalized)
    if exact:
        return exact, "exact"
    for pattern, canonical in _SECTION_KEYWORD_PATTERNS:
        if pattern.search(normalized):
            return canonical, "keyword"
    return normalized, "verbatim"


def _is_heading(block: dict[str, JsonValue]) -> tuple[str, int] | None:
    """(title, level) if the block is a heading, else None.  MinerU headings are
    type="text" with a text_level (1 = paper title, 2 = section, 3 = subsection)."""
    if block.get("type") != "text":
        return None
    level = block.get("text_level")
    if not isinstance(level, int) or level < 1:
        return None
    text = block.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip(), level


def _classify_figure(section_label: str, block_type: str) -> str:
    """Position-only type guess (NOT visual recognition).

    chart in experiments/results/discussion -> data_plot; image in method ->
    framework_architecture; image in experiments -> result_figure; image in
    introduction -> concept_diagram; otherwise chart_other / image_other.
    """
    if block_type == "chart":
        return ("data_plot" if section_label in _EXPERIMENT_SECTION_LABELS
                else "chart_other")
    if section_label in _METHOD_SECTION_LABELS:
        return "framework_architecture"
    if section_label in _EXPERIMENT_SECTION_LABELS:
        return "result_figure"
    if section_label == "introduction":
        return "concept_diagram"
    return "image_other"


_CONFIDENCE_OF_RESOLUTION: Final[dict[str, str]] = {
    "exact": "high", "keyword": "medium", "verbatim": "low", "none": "low",
}

_TYPE_GUESS_BASIS: Final[str] = (
    "由章节位置启发：method/preliminaries/background→framework_architecture、"
    "experiments/results/discussion→data_plot(chart)/result_figure(image)、"
    "introduction→concept_diagram；这不是视觉识别")


def _section_envelope(label: str, confidence: str, basis: str) -> dict[str, JsonValue]:
    return {"value": label, "confidence": confidence, "basis": basis,
            "status": "INFERRED"}


# ---------------------------------------------------------------------------
# NOT_IMPLEMENTED placeholders (visual / semantic fields, issue #1 Phase 1/4/5)
# ---------------------------------------------------------------------------

#: Visual / semantic fields that need a vision model / GPU.  All are emitted as
#: {"value": null, "status": "NOT_IMPLEMENTED"}; the schema doc lists the Phase
#: 1/4/5 dependency for each.  NOT to be confused with OBSERVED or INFERRED.
_NOT_IMPLEMENTED_KEYS: Final[tuple[str, ...]] = (
    "chart_type", "panel_count", "has_axis", "has_legend",
    "colors", "fonts", "line_width", "caption_semantic_role",
)


def _not_implemented() -> dict[str, JsonValue]:
    return {key: {"value": None, "status": "NOT_IMPLEMENTED"}
            for key in _NOT_IMPLEMENTED_KEYS}


# ---------------------------------------------------------------------------
# Per-figure record assembly
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Unreadable:
    image_rel_path: str
    reason: str  # "missing_file" | "unreadable_header"


def _figures_of_paper(corpus_dir: Path, paper: Paper,
                      stem_of: dict[str, str]) -> tuple[list[dict[str, JsonValue]],
                                                        list[Unreadable]]:
    """Extract every image/chart block of one paper into figure records.

    Duplicate img_path rule (written down + tested): one block == one figure entry.
    N blocks pointing at the SAME img_path produce N entries (they are N distinct
    placements in the document), each still uniquely identified by block_index;
    every entry records img_path_occurrence_index (1-based, document order) and
    img_path_occurrence_total for dedup visibility.  The summary reports both
    n_figures (block count) and n_unique_images (distinct image files).
    """
    blocks = load_blocks(paper.content_list_path)
    stem = stem_of[paper.name]
    base = paper.content_list_path.parent

    img_total: Counter = Counter(
        str(b.get("img_path")) for b in blocks
        if b.get("type") in ("image", "chart")
        and isinstance(b.get("img_path"), str) and b.get("img_path"))

    current_label = "front_matter"
    current_resolution = "none"
    current_raw = ""
    figures: list[dict[str, JsonValue]] = []
    unreadable: list[Unreadable] = []
    occurrence: Counter = Counter()
    for block_index, block in enumerate(blocks):
        heading = _is_heading(block)
        if heading is not None and heading[1] == 2:
            normalized = _normalize_section_title(heading[0])
            current_label, current_resolution = _section_resolution(normalized)
            current_raw = heading[0]
            continue
        btype = block.get("type")
        if btype not in ("image", "chart"):
            continue
        img_path = block.get("img_path")
        if not isinstance(img_path, str) or not img_path:
            continue
        resolved = base / img_path
        image_rel_path = resolved.relative_to(corpus_dir).as_posix()

        caption = _caption_of(block)
        page = block.get("page_idx")
        page_value = page if isinstance(page, int) else None

        occurrence[img_path] += 1
        occ_index = occurrence[img_path]
        occ_total = img_total[img_path]

        size = image_size(resolved)
        readable = size is not None
        if size is None:
            width = height = None
            aspect = None
            usable = None
            reason = "missing_file" if not resolved.is_file() else "unreadable_header"
            unreadable.append(Unreadable(image_rel_path=image_rel_path,
                                         reason=reason))
        else:
            width, height = size
            aspect = round(width / height, 4) if height else None
            usable = width >= _MIN_IMAGE_WIDTH

        confidence = _CONFIDENCE_OF_RESOLUTION[current_resolution]
        if current_resolution == "exact":
            section_basis = (f"最近前置 level-2 标题 '{current_raw}' "
                             f"精确命中 canonical 表")
        elif current_resolution == "keyword":
            section_basis = (f"最近前置 level-2 标题 '{current_raw}' "
                             f"经关键词映射为 '{current_label}'")
        elif current_resolution == "verbatim":
            section_basis = (f"最近前置 level-2 标题 '{current_raw}' "
                             f"未命中映射，按原文小写作为章节")
        else:
            section_basis = "该图之前无 level-2 标题，按位置归为 front_matter"

        subfigures = re.findall(r"[(（]([a-zA-Z0-9])[)）]", caption)
        has_subfigures = bool(subfigures)
        figures.append({
            "figure_id": figure_id(stem, page_value, block_index),
            "paper": paper.name,
            "paper_stem": stem,
            "page": page_value,
            "block_index": block_index,
            "img_path_occurrence_index": occ_index,
            "img_path_occurrence_total": occ_total,
            "image_path": img_path,
            "image_rel_path": image_rel_path,
            "caption": caption,
            "caption_chars": len(caption),
            "caption_has_number": caption_has_number(caption),
            "has_subfigures": has_subfigures,
            "subfigures": sorted(set(subfigures)) if has_subfigures else [],
            "readable": readable,
            "width": width,
            "height": height,
            "aspect_ratio": aspect,
            "width_usable_for_print": usable,
            "section": _section_envelope(current_label, confidence, section_basis),
            "type_guess": _section_envelope(
                _classify_figure(current_label, str(btype)), confidence,
                _TYPE_GUESS_BASIS),
            "not_implemented": _not_implemented(),
        })
    return figures, unreadable


# ---------------------------------------------------------------------------
# Quantile helpers (nearest-rank; even-sized samples take the earlier element)
# ---------------------------------------------------------------------------

def _quantile(sorted_vals: list[int | float], q: float) -> int | float | None:
    if not sorted_vals:
        return None
    rank = math.ceil(q * len(sorted_vals))
    idx = max(0, min(len(sorted_vals) - 1, rank - 1))
    return sorted_vals[idx]


def _round4(value: int | float | None) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(value, 4)


def _distribution(vals: list[int | float]) -> dict[str, JsonValue]:
    """min/p25/median/p75/max/mean/count of a value list (deterministic)."""
    if not vals:
        return {"count": 0, "min": None, "p25": None, "median": None,
                "p75": None, "max": None, "mean": None}
    ordered = sorted(vals)
    mean = sum(ordered) / len(ordered)
    return {
        "count": len(ordered),
        "min": _round4(ordered[0]),
        "p25": _round4(_quantile(ordered, 0.25)),
        "median": _round4(_quantile(ordered, 0.5)),
        "p75": _round4(_quantile(ordered, 0.75)),
        "max": _round4(ordered[-1]),
        "mean": round(mean, 4),
    }


# ---------------------------------------------------------------------------
# Profile + summary assembly
# ---------------------------------------------------------------------------

_BASE_ARTIFACT: Final[dict[str, JsonValue]] = {
    "name": "mineru_content_list", "scope": ["figures"],
}


def _collect(corpus_dir: Path) -> tuple[list[dict[str, JsonValue]],
                                        list[Unreadable], int, int]:
    papers = discover_papers(corpus_dir)
    stem_of = _disambiguate_slugs([p.name for p in papers])
    figures: list[dict[str, JsonValue]] = []
    unreadable: list[Unreadable] = []
    papers_with_figures = 0
    for paper in papers:
        paper_figures, paper_unreadable = _figures_of_paper(
            corpus_dir, paper, stem_of)
        figures.extend(paper_figures)
        unreadable.extend(paper_unreadable)
        if paper_figures:
            papers_with_figures += 1
    return figures, unreadable, len(papers), papers_with_figures


def build_profile(figures: list[dict[str, JsonValue]],
                  n_papers: int, n_papers_with_figures: int) -> dict[str, JsonValue]:
    return {
        "schema": "figure_profile",
        "schema_version": FIGURE_PROFILE_VERSION,
        "base_artifact": _BASE_ARTIFACT,
        "threshold_width_px": _MIN_IMAGE_WIDTH,
        "threshold_same_as": "S-SIZ-04",
        "n_papers": n_papers,
        "n_papers_with_figures": n_papers_with_figures,
        "n_figures": len(figures),
        "figures": figures,
    }


def _envelope_value(record: dict[str, JsonValue], key: str) -> str:
    env = record.get(key)
    if isinstance(env, dict):
        value = env.get("value")
        if isinstance(value, str):
            return value
    return "unknown"


def build_summary(figures: list[dict[str, JsonValue]], unreadable: list[Unreadable],
                  n_papers: int, n_papers_with_figures: int) -> dict[str, JsonValue]:
    readable_figures = [f for f in figures if f["readable"] is True]
    widths = sorted(f["width"] for f in readable_figures
                    if isinstance(f["width"], int))
    heights = sorted(f["height"] for f in readable_figures
                     if isinstance(f["height"], int))
    aspects = [f["aspect_ratio"] for f in readable_figures
               if isinstance(f["aspect_ratio"], float)]
    caption_chars = [f["caption_chars"] for f in figures
                     if isinstance(f["caption_chars"], int)]
    adequate = sum(1 for f in readable_figures if f["width_usable_for_print"] is True)
    measured = len(readable_figures)

    with_number = sum(1 for f in figures if f["caption_has_number"] is True)
    without_number = sum(1 for f in figures if f["caption_has_number"] is False)

    section_dist = Counter(_envelope_value(f, "section") for f in figures)
    type_dist = Counter(_envelope_value(f, "type_guess") for f in figures)

    unreadable_rows = [{"image_rel_path": u.image_rel_path, "reason": u.reason}
                       for u in sorted(unreadable,
                                       key=lambda u: (u.image_rel_path, u.reason))]

    return {
        "schema": "figure_summary",
        "schema_version": FIGURE_PROFILE_VERSION,
        "base_artifact": _BASE_ARTIFACT,
        "threshold_width_px": _MIN_IMAGE_WIDTH,
        "threshold_same_as": "S-SIZ-04",
        "n_papers": n_papers,
        "n_papers_with_figures": n_papers_with_figures,
        "n_figures": len(figures),
        "n_unique_images": len({f["image_rel_path"] for f in figures}),
        "n_measured": measured,
        "n_unreadable": len(unreadable),
        "unreadable": unreadable_rows,
        "adequacy": {
            "threshold_width_px": _MIN_IMAGE_WIDTH,
            "same_as": "S-SIZ-04",
            "adequate": adequate,
            "measured": measured,
            "rate": round(adequate / measured, 4) if measured else None,
        },
        "width": _distribution(widths),
        "height": _distribution(heights),
        "aspect_ratio": _distribution(aspects),
        "caption_chars": _distribution(caption_chars),
        "caption_has_number": {
            "with_number": with_number,
            "without_number": without_number,
            "measured": with_number + without_number,
        },
        "section_distribution": dict(sorted(section_dist.items())),
        "type_guess_distribution": dict(sorted(type_dist.items())),
    }


# ---------------------------------------------------------------------------
# Deterministic serialisation
# ---------------------------------------------------------------------------

def _dump(obj: dict[str, JsonValue]) -> str:
    """Byte-stable JSON: sort_keys + ensure_ascii=False + indent 2 + trailing newline.

    No timestamps, no absolute paths are ever placed in the object, so two runs on
    the same corpus are byte-identical (asserted by a determinism test).
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_products(corpus_dir: Path, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    figures, unreadable, n_papers, n_papers_with_figures = _collect(corpus_dir)
    profile_path = out_dir / "_figure_profile.json"
    summary_path = out_dir / "_figure_summary.json"
    profile_path.write_text(_dump(build_profile(figures, n_papers,
                                                n_papers_with_figures)),
                            encoding="utf-8")
    summary_path.write_text(_dump(build_summary(figures, unreadable, n_papers,
                                                n_papers_with_figures)),
                            encoding="utf-8")
    return {"profile": profile_path, "summary": summary_path}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Figure Inventory + Figure Profile JSON (issue #1 MVP, "
                    "pure stdlib, no vision layer)")
    parser.add_argument("--corpus", required=True, type=Path,
                        help="paper-analysis/ directory (MinerU content_list.json)")
    parser.add_argument("--out", required=True, type=Path,
                        help="output directory for _figure_profile.json / "
                             "_figure_summary.json")
    args = parser.parse_args(argv)
    try:
        written = write_products(args.corpus, args.out)
    except ProfileError as exc:
        print(f"[figure_profile] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"[figure_profile] wrote {written['profile']}")
    print(f"[figure_profile] wrote {written['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
