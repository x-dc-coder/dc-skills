#!/usr/bin/env python3
from __future__ import annotations

import argparse

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import plan_output  # noqa: E402
import json
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 模块级正则与常量（保持原样）
# ---------------------------------------------------------------------------

IMG_HTML_RE = re.compile(r"<\s*img\b", re.IGNORECASE)
IMAGE_MD_RE = re.compile(r"!\[(.*?)\]\(([^)]+)\)")
SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")
ATX_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
TABLE_CAPTION_RE = re.compile(r"^\s*\*{0,2}\s*(?:表|Table)\s*\d+(?:\s*[-－.]\s*\d+)?\s+[^。！？!?\n]{1,60}\*{0,2}\s*$")
FIGURE_TITLE_RE = re.compile(r"^\s*(?:图|Figure|Fig\.|Fi\.)\s*\d+(?:\s*[-－.]\s*\d+)?\s+.+\s*$")
FIGURE_TITLE_RE_LOOSE = re.compile(r"^\s*\*{0,2}\s*(?:图|Figure|Fig\.|Fi\.)\s*\d+(?:\s*[-－.]\s*\d+)?\s+.+\*{0,2}\s*$")
MERMAID_FENCE_RE = re.compile(r"^\s*```\s*mermaid\s*$", re.IGNORECASE)
CITATION_RE = re.compile(r"\[(\d+)\]")
META_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,30}\s*:\s*\S+")
_SPECIAL_HEADINGS_UNDERGRAD = {
    "摘要", "abstract", "参考文献", "references", "致谢",
    "acknowledgements", "结论", "结  论", "致  谢",
}
_SPECIAL_HEADINGS_JOURNAL_EXTRA = {
    "keywords", "keyword", "acknowledgments",
    "introduction", "related work", "related works",
    "background", "preliminaries", "preliminary",
    "discussion", "conclusions",
    "appendix", "appendices",
    "author contributions", "author contribution",
    "conflict of interest", "competing interests",
    "data availability", "data availability statement",
    "funding", "funding sources",
}


def _special_headings_for(mode: str) -> set[str]:
    if mode == "journal":
        return _SPECIAL_HEADINGS_UNDERGRAD | _SPECIAL_HEADINGS_JOURNAL_EXTRA
    return set(_SPECIAL_HEADINGS_UNDERGRAD)


SPECIAL_HEADINGS = _SPECIAL_HEADINGS_UNDERGRAD
_CN_SEQ_RE = re.compile(
    r"^[一二三四五六七八九十百]+[、.]"
    r"|^[（(][一二三四五六七八九十百]+[）)]"
    r"|^第[一二三四五六七八九十百]+[章节部分篇]"
)

# 中文数字映射与转换
_CN_DIGIT_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT_MAP = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(cn: str) -> int | None:
    """中文数字 → 整数：一→1, 十一→11, 二十→20, 一百二十→120。不可转换返回 None。"""
    if not cn or not all(c in "一二三四五六七八九十百千零" for c in cn):
        return None
    if cn == "十":
        return 10
    if cn.startswith("一十") and len(cn) > 2:
        cn = cn[1:]  # 一十二 → 十二
    total = 0
    current = 0
    for ch in cn:
        if ch in _CN_DIGIT_MAP:
            current = _CN_DIGIT_MAP[ch]
        elif ch in _CN_UNIT_MAP:
            if current == 0:
                current = 1
            total += current * _CN_UNIT_MAP[ch]
            current = 0
    total += current
    return total if total > 0 else None


# 多体系标题编号正则
_H1_ARABIC_RE = re.compile(r"^(\d+)\s+")
_H1_CN_RE = re.compile(r"^([一二三四五六七八九十百千]+)[、，\s]")
_H1_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十百千]+|\d+)[章节]\s*")

_H2_ARABIC_RE = re.compile(r"^(\d+)\.(\d+)\s+")
_H2_CN_RE = re.compile(r"^[（(]([一二三四五六七八九十百千]+)[）)]")
_H2_CN_BARE_RE = re.compile(r"^([一二三四五六七八九十百千]+)[、，\s]")
_H2_ARABIC_PAREN_RE = re.compile(r"^[（(](\d+)[）)]")
_H2_SIMPLE_RE = re.compile(r"^(\d+)[、.,\s]")

_H3_ARABIC_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\s+")
_H3_TWO_DOT_RE = re.compile(r"^(\d+)\.(\d+)\s+")
_H3_CN_RE = re.compile(r"^[（(]([一二三四五六七八九十百千]+)[）)]")
_H3_ARABIC_PAREN_RE = re.compile(r"^[（(](\d+)[）)]")
_H3_SIMPLE_RE = re.compile(r"^(\d+)[、.,\s]")
REFERENCE_ITEM_RE = re.compile(r"^\[(\d+)\]")
FORMULA_NUMBER_RE = re.compile(r"\\tag\{\s*(\d+)-(\d+)\s*\}|(?:\\?\()\s*(\d+)-(\d+)\s*(?:\\?\))")
FORMULA_IMG_KEYWORDS_RE = re.compile(r"公式|equation|formula", re.IGNORECASE)


# ---- 引用 ↔ 文献表交叉核验（issue #21） ----
# 正文数字引用表达式：单编号 / 区间 / 列表；支持全角括号、全角逗号与常见连接符。
CITATION_EXPR_RE = re.compile(r"[\[［]\s*(\d+(?:\s*[-–—,，]\s*\d+)*)\s*[\]］]")
# 触发"文献表区域"的一级标题（中英文）。
REFERENCE_HEADINGS = frozenset({"参考文献", "references", "bibliography"})
# 单段区间展开上限：超过该数量则整段丢弃并说明（对齐 paper-metrics 的 _REF_RANGE_CAP）。
CITATION_RANGE_CAP = 50


def _expand_citation_expr(expr: str) -> tuple[list[int], list[str]]:
    """展开一个正文引用表达式的内文（如 "3-5" / "1,2" / "42"）为编号列表。

    返回 (numbers, dropped)：dropped 为单段区间超过 CITATION_RANGE_CAP 而整段丢弃的
    段（"宁可少计且可见"，对齐 paper-metrics 的 _expand_ref_expr 思路）。负数与 0
    编号由调用方依据文献表条目数判定是否为数学区间，本函数只做展开。
    """
    parts = [p.strip() for p in re.split(r"[，,]", expr) if p.strip()]
    numbers: list[int] = []
    dropped: list[str] = []
    for part in parts:
        rng = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if rng:
            start = int(rng.group(1))
            end = int(rng.group(2))
            if start > end:
                start, end = end, start
            if end - start + 1 > CITATION_RANGE_CAP:
                dropped.append(part)
                continue
            numbers.extend(range(start, end + 1))
        elif part.isdigit():
            numbers.append(int(part))
        else:
            dropped.append(part)
    return numbers, dropped


# ---------------------------------------------------------------------------
# 辅助函数（保持原样）
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    level: str
    line: int
    code: str
    message: str


def add_findings(findings: list[Finding], level: str, line: int, code: str, message: str) -> None:
    findings.append(Finding(level=level, line=line, code=code, message=message))


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and "|" in s[1:]


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    inner = s[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return all(c in "-:| \t" for c in inner) and "-" in inner


def _count_table_cols(line: str) -> int:
    """统计 markdown 表格行的列数（按未转义竖线分割，去首尾空段）。

    ``| a | b |`` → ['', ' a ', ' b ', ''] → 去首尾 → 2
    ``a | b``    → [' a ', ' b ']          → 2
    转义竖线（反斜杠 + 竖线）不被视为分隔符。
    """
    s = line.strip()
    parts = re.split(r"(?<!\\)\|", s)
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return len(parts)


def _merge_parse_cell(raw: str) -> tuple[str, int, int]:
    """解析单元格文本，提取 <<N / ^^N 合并标记。返回 (文本, colspan, rowspan)。

    标记必须锚定在 cell 末尾，避免 ``数据<<2后续`` 这类内容被误判合并
    并丢失"后续"（issue #66）。裸 ``^^`` 为续行占位。
    """
    t = raw.strip()
    col = 1
    m = re.search(r"<<(\d+)\s*$", t)
    if m:
        col = int(m.group(1)); t = t[:m.start()].strip()
    row = 1
    if t == "^^":
        return ("", 1, 0)  # 裸 ^^ = 续行占位
    m = re.search(r"\^\^(\d+)\s*$", t)
    if m:
        row = int(m.group(1)); t = t[:m.start()].strip()
    return t, col, row


def is_setext_candidate(prev_line: str) -> bool:
    prev = prev_line.strip()
    if not prev:
        return False
    if prev.startswith("#"):
        return False
    if prev.startswith(">"):
        return False
    if _is_table_row(prev):
        return False
    return True


def _normalize_caption(text: str) -> str:
    """移除表题/图题周围的加粗/斜体标记和首尾空格，用于对比重复标题。"""
    return re.sub(r"^\s*\*+\s*", "", text).replace("*", "").strip()


def _strip_inline_code(text: str) -> str:
    return re.sub(r"`[^`]*`", "", text)


# ---------------------------------------------------------------------------
# MarkdownChecker — 将 322 行状态机提取为类
# ---------------------------------------------------------------------------

class MarkdownChecker:
    """Markdown 格式规范检查器。

    使用方式：
        checker = MarkdownChecker(path)
        checker.run()
        for f in checker.findings:
            print(f)
    """

    def __init__(self, path: Path, mode: str = "undergraduate") -> None:
        self.path = path
        self.mode = mode
        self._special_headings = _special_headings_for(mode)
        self.findings: list[Finding] = []
        self.notes: list[str] = []

        # ---- 扫描状态 ----
        self._lines: list[str] = []
        self._in_fence = False
        self._in_math_block = False
        self._in_references = False
        self._pending_formula: int | None = None
        self._fence_start: int | None = None  # 代码块起始行（未闭合时用于报错定位）
        self._math_start: int | None = None   # 公式块起始行（未闭合时用于报错定位）

        # ---- 标题跟踪 ----
        self._prev_level = 0
        self._chapter: str | None = None
        self._chapter_int: int | None = None  # 章节号整数值（支持中文转换）
        self._section: str | None = None
        self._first_heading: int | None = None
        self._h1_count = 0

        # ---- 标题编号追踪（连续性与格式一致性） ----
        # H1: (line_no, num_int, format_str)
        self._h1_nums: list[tuple[int, int, str]] = []
        # H2: (line_no, chapter_int, section_int, format_str)
        self._h2_nums: list[tuple[int, int, int, str]] = []
        # H3: (line_no, chapter_int, section_int, subsection_int, format_str)
        self._h3_nums: list[tuple[int, int, int, int, str]] = []
        # 首个非特殊标题确定的格式（用于一致性校验）
        self._h1_fmt: str | None = None
        self._h2_fmt: str | None = None
        self._h3_fmt: str | None = None
        self._h1_has_special: bool = False  # 是否有摘要/参考文献等特殊标题

        # ---- 收集器 ----
        self._table_starts: list[tuple[int, str | None]] = []
        self._images: list[tuple[int, str, str | None]] = []
        self._refs: list[tuple[int, int]] = []
        self._formula_seq: dict[int, int] = {}

        # ---- 引用 ↔ 文献表交叉核验状态（issue #21） ----
        self._citation_exprs: list[tuple[int, str]] = []  # (line_no, 引用表达式内文)
        self._numeric_citation_count = 0  # 正文数字引用表达式计数（风格判定，含区间/列表）
        self._author_year_count = 0        # 正文作者-年份计数（风格判定）
        self._has_reference_section = False
        self._reference_section_line: int | None = None

    # ---- 公开 API ----

    def run(self) -> None:
        """执行完整检查。"""
        self._read()
        self._check_meta()
        self._scan()
        self._post_scan()

    # ---- 阶段 1：读取与编码 ----

    def _read(self) -> None:
        raw = self.path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._add("ERROR", exc.start + 1, "ENCODING", "文件不是有效 UTF-8 编码")
            return
        if "\r\n" in text:
            self.notes.append("检测到 CRLF 行尾，建议统一为 LF（非强制）")
        self._lines = text.splitlines()

    # ---- 阶段 2：元信息检查 ----

    def _check_meta(self) -> None:
        if not self._lines:
            return
        first_non_empty_idx = None
        for i, ln in enumerate(self._lines, start=1):
            if ln.strip():
                first_non_empty_idx = i
                break
        if first_non_empty_idx is None:
            return
        first_line = self._lines[first_non_empty_idx - 1].strip()
        if first_line == "---":
            self._add("ERROR", first_non_empty_idx, "META_FRONT_MATTER", "禁止 YAML front matter 元信息区块")
        if first_line.startswith("%"):
            self._add("ERROR", first_non_empty_idx, "META_PANDOC_BLOCK", "禁止 Pandoc 标题元信息块（% 开头）")

    # ---- 阶段 3：逐行扫描（核心状态机） ----

    def _scan(self) -> None:
        for idx, line in enumerate(self._lines, start=1):
            stripped = line.strip()

            # 公式编号待检查
            if self._pending_formula is not None and stripped:
                self._validate_formula(idx, stripped)
                self._pending_formula = None

            # fenced code
            if stripped.startswith("```"):
                if MERMAID_FENCE_RE.match(stripped):
                    self._add("WARN", idx, "MERMAID_DISABLED", "检测到 Mermaid 代码块：当前导出流程会移除该代码块")
                if self._in_fence:
                    self._in_fence = False
                    self._fence_start = None
                else:
                    self._in_fence = True
                    self._fence_start = idx
                continue

            if self._in_fence:
                continue

            # math block
            if stripped.startswith("$$"):
                self._handle_math_block(idx, stripped)
                continue

            if self._in_math_block:
                continue

            # 行内检查
            self._check_line(findings_ref=None, idx=idx, line=line)

            # 标题
            heading = ATX_HEADING_RE.match(line)
            if heading:
                self._handle_heading(idx, heading)
                continue

            # 顶部元信息字段
            if self._first_heading is None and idx <= 40 and META_FIELD_RE.match(stripped):
                self._add("ERROR", idx, "META_FIELD", "检测到顶部元信息字段（Key: Value），请移除")

            # HTML img
            if IMG_HTML_RE.search(line):
                self._add("ERROR", idx, "HTML_IMG", "不建议使用 <img>，请改用 Markdown 图片语法")

            # setext heading
            if idx > 1 and SETEXT_RE.match(stripped):
                prev = self._lines[idx - 2]
                if is_setext_candidate(prev):
                    self._add("WARN", idx, "SETEXT_HEADING", "检测到 Setext 标题风格，建议改用 #/##/###")

            # 图片
            self._check_images_in_line(idx, line)

            # 引用
            self._check_citations(idx, line)

            # 表格起始
            if _is_table_row(line) and idx < len(self._lines):
                next_line = self._lines[idx].strip()
                if _is_table_separator(next_line):
                    self._table_starts.append((idx, self._chapter))

            # 参考文献条目空行检查
            if self._in_references:
                ref_m = REFERENCE_ITEM_RE.match(stripped)
                if ref_m:
                    self._refs.append((idx, int(ref_m.group(1))))
                    if idx < len(self._lines):
                        next_line = self._lines[idx].strip()
                        if next_line and next_line.startswith("["):
                            self._add("ERROR", idx, "REF_MISSING_BLANK_LINE", "参考文献条目之间缺少空行，请在每条文献后添加一个空行")

        # 一级标题数量
        if self._h1_count == 0:
            self._add("ERROR", 1, "NO_H1", "文档必须使用一级标题（#）组织章节")

        # 代码块 / 公式块未闭合（fence 或 $$ 配对）
        if self._in_fence:
            self._add("ERROR", self._fence_start or 1, "FENCE_UNCLOSED",
                      "代码块未闭合：缺少 ``` 结束标记")
        if self._in_math_block:
            self._add("ERROR", self._math_start or 1, "MATH_BLOCK_UNCLOSED",
                      "公式块未闭合：缺少 $$ 结束标记")

    # ---- 行内检查子方法 ----

    def _check_line(self, findings_ref, idx: int, line: str) -> None:
        """行内验证：引号配对、Markdown 标记配对、段内换行。"""
        self._validate_paired_quotes(idx, line)
        self._validate_markdown_pairs(idx, line)
        self._validate_line_breaks(idx, line)

    # ---- 数学块处理 ----

    def _handle_math_block(self, idx: int, stripped: str) -> None:
        if self._in_math_block:
            self._validate_formula(idx, stripped)
            if not FORMULA_NUMBER_RE.search(stripped):
                self._pending_formula = idx
            self._in_math_block = False
            self._math_start = None
        else:
            if stripped.endswith("$$") and len(stripped) > 4:
                self._validate_formula(idx, stripped)
                if not FORMULA_NUMBER_RE.search(stripped):
                    self._pending_formula = idx
            else:
                self._in_math_block = True
                self._math_start = idx

    # ---- 标题处理（多体系支持） ----

    def _handle_heading(self, idx: int, heading: re.Match) -> None:
        level = len(heading.group(1))
        raw_title = heading.group(2).strip()
        title = raw_title.lower()

        if self._first_heading is None:
            self._first_heading = idx

        # 跳级检查
        if self._prev_level > 0 and level > self._prev_level + 1:
            self._add("ERROR", idx, "HEADING_SKIP_LEVEL",
                      f"标题层级跳级：从 level {self._prev_level} 直接到 level {level}，请保持层级连续")

        is_special = title in self._special_headings

        if level == 1:
            self._h1_count += 1
            if is_special:
                self._h1_has_special = True
            else:
                self._parse_h1_number(idx, raw_title)
            self._section = None
            self._chapter_int = None  # 重置章节上下文
        elif level == 2:
            self._validate_h2_format(idx, raw_title, is_special)
        elif level == 3:
            self._validate_h3_format(idx, raw_title, is_special)

        if is_special and level != 1:
            self._add("ERROR", idx, "SPECIAL_HEADING_LEVEL", "摘要/Abstract/参考文献/致谢必须使用一级标题（#）")

        self._prev_level = level

        # 参考文献区域边界
        if level == 1 and title in REFERENCE_HEADINGS:
            self._in_references = True
            self._has_reference_section = True
            self._reference_section_line = idx
        elif level == 1 and self._in_references:
            self._in_references = False

    # ---- 一级标题编号解析 ----

    def _parse_h1_number(self, idx: int, raw_title: str) -> None:
        """解析一级标题编号，支持阿拉伯数字、中文序号、第X章三种体系。"""
        # 尝试「数字 标题」
        m = _H1_ARABIC_RE.match(raw_title)
        if m:
            num = int(m.group(1))
            self._chapter = str(num)
            self._chapter_int = num
            self._h1_nums.append((idx, num, "arabic"))
            self._set_h1_fmt("arabic")
            return

        # 尝试「一、标题」
        m = _H1_CN_RE.match(raw_title)
        if m:
            num = _cn_to_int(m.group(1))
            if num is not None:
                self._chapter = str(num)
                self._chapter_int = num
                self._h1_nums.append((idx, num, "chinese"))
                self._set_h1_fmt("chinese")
                return

        # 尝试「第一章 标题」
        m = _H1_CHAPTER_RE.match(raw_title)
        if m:
            cn_or_num = m.group(1)
            if cn_or_num.isdigit():
                num = int(cn_or_num)
            else:
                num = _cn_to_int(cn_or_num)
            if num is not None:
                self._chapter = str(num)
                self._chapter_int = num
                self._h1_nums.append((idx, num, "chapter"))
                self._set_h1_fmt("chapter")
                return

        # 不匹配任何编号格式
        self._add("ERROR", idx, "HEADING1_FORMAT",
                  "一级标题必须包含编号（如 '1 引言'、'一、引言' 或 '第一章 引言'）")

    def _set_h1_fmt(self, fmt: str) -> None:
        if self._h1_fmt is None:
            self._h1_fmt = fmt
        elif self._h1_fmt != fmt:
            self._add("WARN", 0, "HEADING1_CONSISTENCY",
                      f"一级标题编号格式不一致：之前使用 {self._fmt_label(self._h1_fmt)}，"
                      f"当前行使用了 {self._fmt_label(fmt)} 格式")

    # ---- 二级标题格式验证 ----

    def _validate_h2_format(self, idx: int, raw_title: str, is_special: bool) -> None:
        """验证二级标题格式并跟踪编号（多体系）。"""
        if is_special:
            return

        # 尝试「X.Y 标题」
        m = _H2_ARABIC_RE.match(raw_title)
        if m:
            chap = int(m.group(1))
            sec = int(m.group(2))
            self._h2_nums.append((idx, chap, sec, "arabic"))
            self._section = f"{chap}.{sec}"
            self._set_h2_fmt("arabic")
            # 章节号一致性（仅当 H1 也用阿拉伯数字时）
            if self._h1_fmt == "arabic" and self._chapter_int is not None and chap != self._chapter_int:
                self._add("ERROR", idx, "HEADING2_CHAPTER_MISMATCH",
                          f"二级标题章节号不一致：当前章节为 {self._chapter}，但二级标题以 {chap} 开头")
            if self._chapter_int is None:
                self._chapter_int = chap  # 推断当前章节
            return

        # 尝试「（一）标题」
        m = _H2_CN_RE.match(raw_title)
        if m:
            num = _cn_to_int(m.group(1))
            if num is not None:
                chap = self._chapter_int or 1  # 中文体系通常在章节上下文中
                self._h2_nums.append((idx, chap, num, "chinese_paren"))
                self._section = f"{chap}.{num}"
                self._set_h2_fmt("chinese_paren")
                return

        # 尝试「一、标题」（中文裸序号，无括号）
        m = _H2_CN_BARE_RE.match(raw_title)
        if m:
            num = _cn_to_int(m.group(1))
            if num is not None:
                chap = self._chapter_int or 1
                self._h2_nums.append((idx, chap, num, "chinese_bare"))
                self._section = f"{chap}.{num}"
                self._set_h2_fmt("chinese_bare")
                return

        # 尝试「（1）标题」
        m = _H2_ARABIC_PAREN_RE.match(raw_title)
        if m:
            num = int(m.group(1))
            chap = self._chapter_int or 1
            self._h2_nums.append((idx, chap, num, "arabic_paren"))
            self._section = f"{chap}.{num}"
            self._set_h2_fmt("arabic_paren")
            return

        # 尝试「1. 标题」（带点/顿号，用于中文章节体系下的二级标题）
        m = _H2_SIMPLE_RE.match(raw_title)
        if m:
            num = int(m.group(1))
            chap = self._chapter_int or 1
            self._h2_nums.append((idx, chap, num, "arabic_simple"))
            self._section = f"{chap}.{num}"
            self._set_h2_fmt("arabic_simple")
            return

        # 完全不匹配
        self._add("ERROR", idx, "HEADING2_FORMAT",
                  "二级标题必须包含编号（如 '1.1 标题'、'（一）标题'、'一、标题'、'（1）标题' 或 '1. 标题'）")

    def _set_h2_fmt(self, fmt: str) -> None:
        if self._h2_fmt is None:
            self._h2_fmt = fmt
        elif self._h2_fmt != fmt:
            self._add("WARN", 0, "HEADING2_CONSISTENCY",
                      f"二级标题编号格式不一致：之前使用 {self._fmt_label(self._h2_fmt)}，"
                      f"当前行使用了 {self._fmt_label(fmt)} 格式")

    # ---- 三级标题格式验证 ----

    def _validate_h3_format(self, idx: int, raw_title: str, is_special: bool) -> None:
        """验证三级标题格式并跟踪编号（多体系）。"""
        if is_special:
            return

        chap = self._chapter_int or 1
        # 从 _section 提取当前二级编号
        sec = 1
        if self._section:
            parts = self._section.split(".")
            if len(parts) >= 2:
                try:
                    sec = int(parts[1])
                except ValueError:
                    pass

        # 尝试「X.Y.Z 标题」
        m = _H3_ARABIC_RE.match(raw_title)
        if m:
            ch = int(m.group(1))
            s = int(m.group(2))
            sub = int(m.group(3))
            self._h3_nums.append((idx, ch, s, sub, "arabic"))
            self._set_h3_fmt("arabic")
            if self._section and f"{ch}.{s}" != self._section:
                self._add("ERROR", idx, "HEADING3_SECTION_MISMATCH",
                          f"三级标题编号不一致：当前二级为 {self._section}，但三级标题以 {ch}.{s} 开头")
            return

        # 尝试「X.Y 标题」（两段式，用于中文章节体系：三 → 3.1）
        m = _H3_TWO_DOT_RE.match(raw_title)
        if m:
            ch = int(m.group(1))
            sub = int(m.group(2))
            s = sec  # 从当前 H2 上下文推断
            self._h3_nums.append((idx, ch, s, sub, "arabic_twodot"))
            self._set_h3_fmt("arabic_twodot")
            return

        # 尝试「（一）标题」（中文括号序号）
        m = _H3_CN_RE.match(raw_title)
        if m:
            num = _cn_to_int(m.group(1))
            if num is not None:
                self._h3_nums.append((idx, chap, sec, num, "chinese_paren"))
                self._set_h3_fmt("chinese_paren")
                return

        # 尝试「（1）标题」（阿拉伯括号序号）
        m = _H3_ARABIC_PAREN_RE.match(raw_title)
        if m:
            num = int(m.group(1))
            self._h3_nums.append((idx, chap, sec, num, "arabic_paren"))
            self._set_h3_fmt("arabic_paren")
            return

        # 尝试「1. 标题」（带点/顿号，用于中文体系下的三级标题）
        m = _H3_SIMPLE_RE.match(raw_title)
        if m:
            num = int(m.group(1))
            self._h3_nums.append((idx, chap, sec, num, "arabic_simple"))
            self._set_h3_fmt("arabic_simple")
            return

        # 完全不匹配
        self._add("ERROR", idx, "HEADING3_FORMAT",
                  "三级标题必须包含编号（如 '1.1.1 标题'、'3.1 标题'、'（一）标题'、'（1）标题' 或 '1. 标题'）")

    def _set_h3_fmt(self, fmt: str) -> None:
        if self._h3_fmt is None:
            self._h3_fmt = fmt
        elif self._h3_fmt != fmt:
            self._add("WARN", 0, "HEADING3_CONSISTENCY",
                      f"三级标题编号格式不一致：之前使用 {self._fmt_label(self._h3_fmt)}，"
                      f"当前行使用了 {self._fmt_label(fmt)} 格式")

    @staticmethod
    def _fmt_label(fmt: str) -> str:
        """格式标识 → 人类可读标签。"""
        return {
            "arabic": "'1 引言'（阿拉伯数字）",
            "chinese": "'一、引言'（中文序号）",
            "chapter": "'第一章 引言'（章节式）",
            "arabic_dotted": "'1.1'（阿拉伯数字）",
            "chinese_paren": "'（一）'（中文括号）",
            "chinese_bare": "'一、'（中文序号）",
            "arabic_paren": "'（1）'（阿拉伯括号）",
            "arabic_simple": "'1.'（数字加点）",
            "arabic_twodot": "'3.1'（两段数字）",
        }.get(fmt, fmt)

    # ---- 标题编号连续性与一致性检查（_post_scan 中调用） ----

    def _check_heading_continuity(self) -> None:
        """检查各级标题编号是否连续（无重复、无跳号、从 1 开始）。"""
        self._check_h1_continuity()
        self._check_h2_continuity()
        self._check_h3_continuity()

    def _check_h1_continuity(self) -> None:
        if not self._h1_nums:
            return
        seen: set[int] = set()
        expected = 1
        for line_no, num, fmt in self._h1_nums:
            if num in seen:
                self._add("ERROR", line_no, "HEADING1_DUPLICATE", f"一级标题编号重复：{self._num_label(num, fmt)}")
            seen.add(num)
            if num != expected:
                self._add("ERROR", line_no, "HEADING1_DISCONTINUITY",
                          f"一级标题编号不连续：期望 {self._num_label(expected, fmt)}，"
                          f"实际为 {self._num_label(num, fmt)}")
                expected = num
            expected += 1

    def _check_h2_continuity(self) -> None:
        if not self._h2_nums:
            return
        # 按章节分组检查
        chapters: dict[int, list[tuple[int, int, int]]] = {}  # chapter → [(line, section, idx)]
        for i, (line_no, chap, sec, fmt) in enumerate(self._h2_nums):
            chapters.setdefault(chap, []).append((line_no, sec, i))

        for chap, items in chapters.items():
            seen: set[int] = set()
            expected = 1
            items.sort(key=lambda x: x[2])  # 按原始出现顺序
            for line_no, sec, _idx in items:
                if sec in seen:
                    self._add("ERROR", line_no, "HEADING2_DUPLICATE",
                              f"二级标题编号重复：第{chap}章下序号 {sec} 重复")
                seen.add(sec)
                if sec != expected:
                    self._add("ERROR", line_no, "HEADING2_DISCONTINUITY",
                              f"二级标题编号不连续：第{chap}章期望序号 {expected}，实际为 {sec}")
                    expected = sec
                expected += 1

    def _check_h3_continuity(self) -> None:
        if not self._h3_nums:
            return
        # 按章节-小节分组检查
        sections: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        for i, (line_no, chap, sec, sub, fmt) in enumerate(self._h3_nums):
            sections.setdefault((chap, sec), []).append((line_no, sub, i))

        for (chap, sec), items in sections.items():
            seen: set[int] = set()
            expected = 1
            items.sort(key=lambda x: x[2])
            for line_no, sub, _idx in items:
                if sub in seen:
                    self._add("ERROR", line_no, "HEADING3_DUPLICATE",
                              f"三级标题编号重复：第{chap}.{sec}节下序号 {sub} 重复")
                seen.add(sub)
                if sub != expected:
                    self._add("ERROR", line_no, "HEADING3_DISCONTINUITY",
                              f"三级标题编号不连续：第{chap}.{sec}节期望序号 {expected}，实际为 {sub}")
                    expected = sub
                expected += 1

    @staticmethod
    def _num_label(num: int, fmt: str) -> str:
        """将编号整数值转为人类可读标签。"""
        cn_map = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
        if fmt == "chinese":
            return cn_map.get(num, str(num))
        if fmt == "chapter":
            return f"第{cn_map.get(num, str(num))}章"
        return str(num)

    # ---- 图片检查 ----

    def _check_images_in_line(self, idx: int, line: str) -> None:
        for m in IMAGE_MD_RE.finditer(line):
            alt = m.group(1).strip()
            rel = m.group(2).strip()
            if not alt:
                self._add("WARN", idx, "IMAGE_ALT_EMPTY", "图片 alt 为空，建议填写图题（如 图1 xxx 或 图3-1 xxx）")
            elif not FIGURE_TITLE_RE.match(alt):
                self._add("WARN", idx, "IMAGE_TITLE_STYLE", '图片标题建议使用"图N 标题"或"图N-M 标题"格式')
            if FORMULA_IMG_KEYWORDS_RE.search(alt):
                self._add("ERROR", idx, "FORMULA_AS_IMAGE",
                          "禁止将公式以图片形式插入，请使用 LaTeX 数学语法（如 $E=mc^2$ 或 $$...$$）")
            self._images.append((idx, alt, self._chapter))
            if not re.match(r"^(https?://|data:)", rel, re.IGNORECASE):
                local = (self.path.parent / rel).resolve()
                if not local.exists():
                    self._add("WARN", idx, "IMAGE_PATH_MISSING", f"图片路径不存在: {rel}")

    # ---- 引用检查 ----

    def _check_citations(self, idx: int, line: str) -> None:
        if ("[" not in line or "]" not in line) and ("［" not in line or "］" not in line):
            return
        for m in CITATION_RE.finditer(line):
            if int(m.group(1)) <= 0:
                self._add("WARN", idx, "CITATION_INVALID", "引用编号应为正整数")
        if self._in_references:
            return  # 文献表自身 [N] 不计为正文引用
        for m in CITATION_EXPR_RE.finditer(line):
            self._citation_exprs.append((idx, m.group(1)))
        self._numeric_citation_count += len(CITATION_EXPR_RE.findall(line))
        self._author_year_count += (
            len(self._CITATION_RE_AUTHOR_YEAR.findall(line))
            + len(self._CITATION_RE_NARRATIVE.findall(line))
        )

    # ---- 行内验证 ----

    def _validate_paired_quotes(self, idx: int, text: str) -> None:
        sanitized = _strip_inline_code(text)
        ascii_count = sanitized.count('"')
        if ascii_count % 2 != 0:
            self._add("ERROR", idx, "UNPAIRED_DOUBLE_QUOTES", "ASCII 双引号未成对出现，请检查本行是否完整")
        left_count = sanitized.count("“")
        right_count = sanitized.count("”")
        if left_count != right_count:
            self._add("ERROR", idx, "UNPAIRED_DOUBLE_QUOTES",
                      f"中文双引号未成对出现（左引号 {left_count} 个，右引号 {right_count} 个），请检查本行的中英文双引号是否完整")

    def _validate_markdown_pairs(self, idx: int, text: str) -> None:
        code_free = re.sub(r"`[^`]*`", "", text)
        if code_free.count("**") % 2 != 0:
            self._add("ERROR", idx, "UNPAIRED_BOLD", "加粗标记 ** 未成对，请检查是否缺少闭合标记")
        no_bold = code_free.replace("**", "")
        if no_bold.count("*") % 2 != 0:
            self._add("ERROR", idx, "UNPAIRED_ITALIC", "斜体标记 * 未成对，请检查是否缺少闭合标记")
        if code_free.count("~~") % 2 != 0:
            self._add("ERROR", idx, "UNPAIRED_STRIKE", "删除线标记 ~~ 未成对，请检查是否缺少闭合标记")
        if code_free.count("`") % 2 != 0:
            self._add("ERROR", idx, "UNPAIRED_INLINE_CODE", "行内代码标记 ` 未成对，请检查是否缺少闭合标记")
        if code_free.count("[") != code_free.count("]"):
            self._add("ERROR", idx, "UNPAIRED_BRACKETS",
                      f"方括号 [] 不匹配：左 {code_free.count('[')} 个，右 {code_free.count(']')} 个")
        if code_free.count("(") != code_free.count(")"):
            self._add("ERROR", idx, "UNPAIRED_PARENTHESES",
                      f"圆括号 () 不匹配：左 {code_free.count('(')} 个，右 {code_free.count(')')} 个")

    def _validate_line_breaks(self, idx: int, text: str) -> None:
        stripped = text.rstrip("\n\r")
        if stripped.endswith("  ") and not stripped.endswith("    "):
            self._add("ERROR", idx, "INTRAPARAGRAPH_LINE_BREAK",
                      "检测到段内换行（行末两个空格），请使用空行分隔段落，不要使用段内换行符")
        if re.search(r"<br\s*/?>", text, re.IGNORECASE):
            self._add("ERROR", idx, "HTML_LINE_BREAK", "检测到 <br> 标签，请使用空行分隔段落")

    def _validate_formula(self, idx: int, text: str) -> None:
        m = FORMULA_NUMBER_RE.search(text)
        if not m:
            return
        if m.group(1) is not None:
            chapter = int(m.group(1))
            seq = int(m.group(2))
        else:
            chapter = int(m.group(3))
            seq = int(m.group(4))
        if self._chapter is not None and chapter != int(self._chapter):
            self._add("WARN", idx, "FORMULA_NUMBER_MISMATCH",
                      f"公式编号章节号不一致：当前章节为 {self._chapter}，但公式编号为 ({chapter}-{seq})")
        expected = self._formula_seq.get(chapter, 0) + 1
        if seq != expected:
            self._add("ERROR", idx, "FORMULA_NUMBER_DISCONTINUITY",
                      f"公式编号不连续：章节 {chapter} 当前期望 ({chapter}-{expected})，实际为 ({chapter}-{seq})")
        self._formula_seq[chapter] = seq

    # ---- 阶段 4：扫描后全局检查 ----

    def _post_scan(self) -> None:
        self._check_heading_continuity()
        self._check_figure_duplicates()
        self._check_table_captions()
        self._check_figure_table_sequence()
        self._check_text_around_blocks()
        self._check_merge_table_syntax()
        self._check_table_column_consistency()
        self._check_reference_continuity()
        self._check_citation_crosslink()
        if self.mode == "journal":
            self._check_citation_density_journal()

    def _check_merge_table_syntax(self) -> None:
        """校验友好合并语法（<<N/^^N）合法性（issue #66）。

        检查：跨列越界（单元格 colspan 之和超过表格列数）、跨行越界
        （^^N 超过表格剩余行数）、续行 ^^ 出现在首行（无上方合并单元格）。
        """
        for start, _chapter in self._table_starts:
            block_start = start
            if start > 0 and _is_table_row(self._lines[start - 1].strip()):
                block_start = start - 1  # 包含表头行
            table_end = start
            while table_end < len(self._lines) and _is_table_row(self._lines[table_end].strip()):
                table_end += 1
            rows: list[tuple[int, list[tuple[str, int, int]]]] = []
            for ln_no in range(block_start, table_end):
                s = self._lines[ln_no].strip()
                if _is_table_separator(s):
                    continue
                inner = s[1:-1] if s.endswith("|") else s[1:]
                cells = [_merge_parse_cell(c) for c in re.split(r"(?<!\\)\|", inner)]
                rows.append((ln_no, cells))
            if not rows:
                continue
            ncols = sum(c[1] for c in rows[0][1])  # 表头行 colspan 和 = 表格列数
            for r_idx, (ln_no, cells) in enumerate(rows):
                # 去掉末尾补列空 cell（用户 `| 内容 | 合并<<2 | |` 写法，翻译器会跳过）
                effective = list(cells)
                while effective and effective[-1][0] == "" and effective[-1][1] == 1 and effective[-1][2] == 1:
                    effective.pop()
                row_cols = sum(c[1] for c in effective)
                if row_cols > ncols:
                    self._add(
                        "ERROR", ln_no, "MERGE_COLSPAN_EXCEED",
                        f"合并单元格跨列 {row_cols} 列超过表格列数 {ncols}（标记 <<N/^^N 越界）",
                    )
                for (text, col, rowspan) in cells:
                    if rowspan == 0 and col == 1:
                        # 裸 ^^ 续行占位：首行无上方合并单元格则无效
                        if r_idx == 0:
                            self._add(
                                "ERROR", ln_no, "MERGE_ROWSPAN_ORPHAN",
                                "续行标记 ^^ 出现在表格首行（无上方合并单元格），无效",
                            )
                    elif rowspan > 1 and rowspan > len(rows) - r_idx:
                        self._add(
                            "ERROR", ln_no, "MERGE_ROWSPAN_EXCEED",
                            f"合并跨行 {rowspan} 行超过表格剩余行数 {len(rows) - r_idx}（^^N 越界）",
                        )

    def _check_table_column_consistency(self) -> None:
        """校验表格每行列数一致（header/数据行列数应相同）。

        独立扫描连续表格块（不依赖 _table_starts 的起始语义），
        跳过分隔行，比较各数据行与首行列数。列数不一致通常意味着
        单元格漏填或多填（markdown 表格要求各行列数一致）。
        含 ``<<N``/``^^N`` 合并语法的表格豁免（合并行列数天然不一致）。
        """
        lines = self._lines
        i = 0
        while i < len(lines):
            if not _is_table_row(lines[i].strip()):
                i += 1
                continue
            block: list[tuple[int, int]] = []
            has_merge = False
            while i < len(lines) and _is_table_row(lines[i].strip()):
                s = lines[i].strip()
                if "<<" in s or "^^" in s:
                    has_merge = True
                if not _is_table_separator(s):
                    block.append((i + 1, _count_table_cols(s)))
                i += 1
            if has_merge:
                continue  # 合并表格各行列数天然不一致（跨列<<N/续行^^），豁免
            if len(block) >= 2:
                ref = block[0][1]
                for ln_no, cols in block[1:]:
                    if cols != ref:
                        self._add(
                            "ERROR", ln_no, "TABLE_COLUMN_MISMATCH",
                            f"表格列数不一致：首行 {ref} 列，本行 {cols} 列",
                        )
                        break

    def _check_figure_duplicates(self) -> None:
        for img_line, alt, _chapter in self._images:
            if not alt:
                continue
            normalized_alt = _normalize_caption(alt)
            nxt = img_line
            while nxt < len(self._lines) and not self._lines[nxt].strip():
                nxt += 1
            if nxt < len(self._lines):
                nxt_stripped = self._lines[nxt].strip()
                if FIGURE_TITLE_RE_LOOSE.match(nxt_stripped):
                    nxt_norm = _normalize_caption(nxt_stripped)
                    if nxt_norm == normalized_alt:
                        self._add("ERROR", nxt + 1, "DUPLICATE_FIGURE_CAPTION",
                                  f'图片下方重复出现图题"{nxt_norm}"，标题应仅在图片 alt 中体现')

    def _check_table_captions(self) -> None:
        for start, _tbl_chapter in self._table_starts:
            look = start - 1
            while look >= 1 and not self._lines[look - 1].strip():
                look -= 1
            if look < 1:
                self._add("WARN", start, "TABLE_CAPTION_MISSING",
                          '表格前缺少表题（建议"表N 标题"或"表N-M 标题"）')
                continue
            if not TABLE_CAPTION_RE.match(self._lines[look - 1]):
                self._add("WARN", start, "TABLE_CAPTION_STYLE",
                          '表格前一行不是规范表题（建议"表N 标题"或"表N-M 标题"）')

    def _check_figure_table_sequence(self) -> None:
        """检查图片和表格的序号是否连续、章节号是否匹配。"""
        # 图片
        figure_seq: dict[int, int] = {}
        global_fig = 0
        for img_line, alt, chapter_no in self._images:
            m = re.search(r"(?:图|Figure|Fig\.|Fi\.)\s*(\d+)(?:\s*[-－.]\s*(\d+))?", alt)
            if m:
                if m.group(2) is not None:
                    chapter = int(m.group(1))
                    seq = int(m.group(2))
                    if chapter_no is not None and chapter != int(chapter_no):
                        self._add("WARN", img_line, "FIGURE_NUMBER_MISMATCH",
                                  f"图片编号章节号不一致：当前章节为 {chapter_no}，但图片编号为 {chapter}-{seq}")
                    expected = figure_seq.get(chapter, 0) + 1
                    if seq != expected:
                        self._add("ERROR", img_line, "FIGURE_NUMBER_DISCONTINUITY",
                                  f"图片编号不连续：章节 {chapter} 当前期望 {chapter}-{expected}，实际为 {chapter}-{seq}")
                    figure_seq[chapter] = seq
                else:
                    num = int(m.group(1))
                    global_fig += 1
                    if num != global_fig:
                        self._add("ERROR", img_line, "FIGURE_NUMBER_DISCONTINUITY",
                                  f"图片编号不连续：当前期望 {global_fig}，实际为 {num}")
                        global_fig = num
            else:
                self._add("WARN", img_line, "FIGURE_NUMBER_MISSING",
                          "图片缺少规范的序号（建议格式：图N 标题 或 图N-M 标题）")

        # 表格
        table_seq: dict[int, int] = {}
        global_tbl = 0
        for start, chapter_no in self._table_starts:
            look = start - 1
            while look >= 1 and not self._lines[look - 1].strip():
                look -= 1
            if look >= 1:
                caption = self._lines[look - 1].strip()
                m = re.search(r"(?:表|Table)\s*(\d+)(?:\s*[-－.]\s*(\d+))?", caption)
                if m:
                    if m.group(2) is not None:
                        chapter = int(m.group(1))
                        seq = int(m.group(2))
                        if chapter_no is not None and chapter != int(chapter_no):
                            self._add("WARN", start, "TABLE_NUMBER_MISMATCH",
                                      f"表格编号章节号不一致：当前章节为 {chapter_no}，但表格编号为 {chapter}-{seq}")
                        expected = table_seq.get(chapter, 0) + 1
                        if seq != expected:
                            self._add("ERROR", start, "TABLE_NUMBER_DISCONTINUITY",
                                      f"表格编号不连续：章节 {chapter} 当前期望 {chapter}-{expected}，实际为 {chapter}-{seq}")
                        table_seq[chapter] = seq
                    else:
                        num = int(m.group(1))
                        global_tbl += 1
                        if num != global_tbl:
                            self._add("ERROR", start, "TABLE_NUMBER_DISCONTINUITY",
                                      f"表格编号不连续：当前期望 {global_tbl}，实际为 {num}")
                            global_tbl = num
                else:
                    self._add("WARN", start, "TABLE_NUMBER_MISSING",
                              "表格缺少规范的序号（建议格式：表N 标题 或 表N-M 标题）")

    def _check_text_around_blocks(self) -> None:
        """检查图片和表格前后是否有段落文字描述。"""

        def _is_valid_text(line: str) -> bool:
            s = line.strip()
            if not s:
                return False
            if ATX_HEADING_RE.match(s):
                return False
            if IMAGE_MD_RE.search(s):
                return False
            if _is_table_row(s) or _is_table_separator(s):
                return False
            if TABLE_CAPTION_RE.match(s):
                return False
            if FIGURE_TITLE_RE_LOOSE.match(s):
                return False
            if s.startswith("```") or s.startswith("$$") or s.startswith(">"):
                return False
            if s == "---":
                return False
            if SETEXT_RE.match(s):
                return False
            if REFERENCE_ITEM_RE.match(s):
                return False
            return True

        for img_line, _alt, _chapter in self._images:
            has_before = any(_is_valid_text(self._lines[i]) for i in range(img_line - 2, -1, -1)
                             if self._lines[i].strip() or i == 0)
            if not has_before:
                has_before = any(_is_valid_text(self._lines[i]) for i in range(img_line - 2, -1, -1)
                                 if not self._lines[i].strip())
            # 简化为直接扫描
            has_before = False
            for i in range(img_line - 2, -1, -1):
                if _is_valid_text(self._lines[i]):
                    has_before = True
                    break
                if self._lines[i].strip():
                    break
            has_after = False
            for i in range(img_line, len(self._lines)):
                if _is_valid_text(self._lines[i]):
                    has_after = True
                    break
                if self._lines[i].strip():
                    break
            if not has_before and not has_after:
                self._add("ERROR", img_line, "MISSING_TEXT_AROUND_IMAGE",
                          "图片前后均缺少段落文字描述，请在图片前或图片后添加对图片的说明或分析文字")

        for start, _chapter in self._table_starts:
            table_end = start
            for j in range(start, len(self._lines)):
                if not _is_table_row(self._lines[j].strip()):
                    break
                table_end = j
            has_before = False
            look = start - 2
            while look >= 0 and not self._lines[look].strip():
                look -= 1
            if look >= 0 and TABLE_CAPTION_RE.match(self._lines[look].strip()):
                look -= 1
            while look >= 0 and not self._lines[look].strip():
                look -= 1
            if look >= 0 and _is_valid_text(self._lines[look]):
                has_before = True
            has_after = False
            look = table_end + 1
            while look < len(self._lines) and not self._lines[look].strip():
                look += 1
            if look < len(self._lines) and _is_valid_text(self._lines[look]):
                has_after = True
            if not has_before and not has_after:
                self._add("ERROR", start, "MISSING_TEXT_AROUND_TABLE",
                          "表格前后均缺少段落文字描述，请在表格前或表格后添加对表格的说明或分析文字")

    def _check_reference_continuity(self) -> None:
        if not self._refs:
            return
        numbers = [n for _ln, n in self._refs]
        seen: set[int] = set()
        if numbers[0] != 1:
            self._add("ERROR", self._refs[0][0], "REF_NUMBER_NOT_START_AT_ONE",
                      f"参考文献编号应从 [1] 开始，当前首条为 [{numbers[0]}]")
        for i, (line_no, n) in enumerate(self._refs):
            if n in seen:
                self._add("ERROR", line_no, "REF_NUMBER_DUPLICATE", f"参考文献编号重复：[{n}] 出现多次")
            seen.add(n)
            if i > 0:
                prev_n = numbers[i - 1]
                if n != prev_n + 1:
                    self._add("ERROR", line_no, "REF_NUMBER_DISCONTINUITY",
                              f"参考文献编号不连续：前一条为 [{prev_n}]，当前为 [{n}]，期望 [{prev_n + 1}]")

    def _check_citation_crosslink(self) -> None:
        """正文数字引用 ↔ 文献表条目号 双向核验（issue #21，对齐 paper-metrics M-REFLINK-54）。

        能力感知（issue #13 规矩）：无 References 段、条目格式不可识别、或作者-年份制
        时，跳过本 gate 并在 findings 里说明原因，不判失败（INFO 级别，不参与 ERROR/WARN 计数）。
        """
        if not self._has_reference_section:
            self._add("INFO", 1, "CITATION_CROSSLINK_SKIPPED",
                      "跳过正文引用↔文献表交叉核验：未检测到 References/参考文献 章节")
            return
        if not self._refs:
            self._add("INFO", self._reference_section_line or 1, "CITATION_CROSSLINK_SKIPPED",
                      "跳过正文引用↔文献表交叉核验：文献表存在但未识别出 [N] 条目格式")
            return
        if self._numeric_citation_count == 0 or self._author_year_count > self._numeric_citation_count:
            self._add("INFO", self._reference_section_line or 1, "CITATION_STYLE_NOT_NUMERIC",
                      "检测到作者-年份制引文（或正文无数字引用），跳过数字引用↔文献表交叉核验，"
                      "避免误报 100% 未引用")
            return
        ref_numbers: set[int] = {n for _ln, n in self._refs}
        ref_lines: dict[int, int] = {n: ln for ln, n in self._refs}
        cited: dict[int, list[int]] = {}
        for line_no, expr in self._citation_exprs:
            numbers, dropped = _expand_citation_expr(expr)
            if dropped:
                self.notes.append(
                    f"L{line_no} 引用表达式 [{expr}] 含超上限/不可展开段，整段跳过交叉核验")
                continue
            if len(numbers) == 1:
                if numbers[0] >= 1:
                    cited.setdefault(numbers[0], []).append(line_no)
            elif numbers and all(n >= 1 for n in numbers):
                for n in numbers:
                    cited.setdefault(n, []).append(line_no)
            # 多编号且含 0（如 [0,1]）→ 数学区间，不算引用
        for n in sorted(cited):
            if n not in ref_numbers:
                self._add("ERROR", cited[n][0], "DANGLING_CITATION",
                          f"正文引用了文献编号 [{n}]，但参考文献表中不存在该条目")
        for n in sorted(ref_numbers):
            if n not in cited:
                self._add("WARN", ref_lines[n], "UNCITED_REFERENCE",
                          f"参考文献条目 [{n}] 在正文中从未被引用")

    _CITATION_DENSITY_MIN_WORDS = 500
    _CITATION_RE_NUMERIC = re.compile(r"\[\d+\]")
    _CITATION_RE_AUTHOR_YEAR = re.compile(r"\([A-Z][A-Za-z'’-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z'’-]+)*,?\s*\d{4}[a-z]?\)")
    _CITATION_RE_NARRATIVE = re.compile(r"[A-Z][A-Za-z'’-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z'’-]+)*\s*\(\d{4}[a-z]?\)")

    def _check_citation_density_journal(self) -> None:
        """WARN if a top-level section exceeds _CITATION_DENSITY_MIN_WORDS with zero citations.

        Only active in journal mode. Splits the document by level-1 ATX headings
        (excluding the References section itself), counts words and citation marks.
        """
        h1_boundaries: list[tuple[int, str]] = []  # (line_idx, normalized_title)
        for i, line in enumerate(self._lines):
            m = ATX_HEADING_RE.match(line)
            if m and len(m.group(1)) == 1:
                h1_boundaries.append((i, m.group(2).strip().lower()))
        if not h1_boundaries:
            return
        for idx, (start, title) in enumerate(h1_boundaries):
            if title in {"references", "参考文献", "bibliography",
                         "acknowledgments", "acknowledgements", "致谢",
                         "appendix", "appendices"}:
                continue
            end = h1_boundaries[idx + 1][0] if idx + 1 < len(h1_boundaries) else len(self._lines)
            body = self._lines[start + 1:end]
            text = "\n".join(body)
            word_count = len(text.split())
            if word_count < self._CITATION_DENSITY_MIN_WORDS:
                continue
            has_cite = bool(
                self._CITATION_RE_NUMERIC.search(text)
                or self._CITATION_RE_AUTHOR_YEAR.search(text)
                or self._CITATION_RE_NARRATIVE.search(text)
            )
            if not has_cite:
                self._add("WARN", start + 1, "CITATION_DENSITY_LOW",
                          f"该章节约 {word_count} 词但无任何文献引用：期刊论文应密集引用前人工作（建议每段至少 1 处引用）")

    # ---- 工具方法 ----

    def _add(self, level: str, line: int, code: str, message: str) -> None:
        self.findings.append(Finding(level=level, line=line, code=code, message=message))


# ---------------------------------------------------------------------------
# 公开 API（保持兼容）
# ---------------------------------------------------------------------------

def check_markdown(path: Path, mode: str = "undergraduate") -> tuple[list[Finding], list[str]]:
    """对 Markdown 文件执行格式规范检查。

    mode: "undergraduate"（本科毕设，默认，行为与历史版本完全一致）或
          "journal"（期刊/会议论文，启用英文特殊标题 + 引用密度检查）。
    """
    checker = MarkdownChecker(path, mode=mode)
    checker.run()
    return checker.findings, checker.notes


# ---------------------------------------------------------------------------
# 保留的模块级函数（check_markdown 内部不再使用，但可能被外部引用）
# ---------------------------------------------------------------------------

def _validate_paired_double_quotes(findings: list[Finding], line_no: int, text: str) -> None:
    sanitized = _strip_inline_code(text)
    ascii_count = sanitized.count('"')
    if ascii_count % 2 != 0:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_DOUBLE_QUOTES", "ASCII 双引号未成对出现，请检查本行是否完整")
    left_count = sanitized.count("“")
    right_count = sanitized.count("”")
    if left_count != right_count:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_DOUBLE_QUOTES",
                     f"中文双引号未成对出现（左引号 {left_count} 个，右引号 {right_count} 个），请检查本行的中英文双引号是否完整")


def _validate_markdown_pairs(findings: list[Finding], line_no: int, text: str) -> None:
    code_free = re.sub(r"`[^`]*`", "", text)
    if code_free.count("**") % 2 != 0:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_BOLD", "加粗标记 ** 未成对，请检查是否缺少闭合标记")
    no_bold = code_free.replace("**", "")
    if no_bold.count("*") % 2 != 0:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_ITALIC", "斜体标记 * 未成对，请检查是否缺少闭合标记")
    if code_free.count("~~") % 2 != 0:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_STRIKE", "删除线标记 ~~ 未成对，请检查是否缺少闭合标记")
    if code_free.count("`") % 2 != 0:
        add_findings(findings, "ERROR", line_no, "UNPAIRED_INLINE_CODE", "行内代码标记 ` 未成对，请检查是否缺少闭合标记")
    if code_free.count("[") != code_free.count("]"):
        add_findings(findings, "ERROR", line_no, "UNPAIRED_BRACKETS",
                     f"方括号 [] 不匹配：左 {code_free.count('[')} 个，右 {code_free.count(']')} 个")
    if code_free.count("(") != code_free.count(")"):
        add_findings(findings, "ERROR", line_no, "UNPAIRED_PARENTHESES",
                     f"圆括号 () 不匹配：左 {code_free.count('(')} 个，右 {code_free.count(')')} 个")


def _validate_no_intra_paragraph_line_breaks(findings: list[Finding], line_no: int, text: str) -> None:
    stripped = text.rstrip("\n\r")
    if stripped.endswith("  ") and not stripped.endswith("    "):
        add_findings(findings, "ERROR", line_no, "INTRAPARAGRAPH_LINE_BREAK",
                     "检测到段内换行（行末两个空格），请使用空行分隔段落，不要使用段内换行符")
    if re.search(r"<br\s*/?>", text, re.IGNORECASE):
        add_findings(findings, "ERROR", line_no, "HTML_LINE_BREAK", "检测到 <br> 标签，请使用空行分隔段落")


def _validate_formula_number(
    findings: list[Finding], line_no: int, text: str,
    current_chapter_no: str | None, formula_seq_in_chapter: dict[int, int],
) -> None:
    m = FORMULA_NUMBER_RE.search(text)
    if not m:
        return
    if m.group(1) is not None:
        chapter = int(m.group(1))
        seq = int(m.group(2))
    else:
        chapter = int(m.group(3))
        seq = int(m.group(4))
    if current_chapter_no is not None and chapter != int(current_chapter_no):
        add_findings(findings, "WARN", line_no, "FORMULA_NUMBER_MISMATCH",
                     f"公式编号章节号不一致：当前章节为 {current_chapter_no}，但公式编号为 ({chapter}-{seq})")
    expected = formula_seq_in_chapter.get(chapter, 0) + 1
    if seq != expected:
        add_findings(findings, "ERROR", line_no, "FORMULA_NUMBER_DISCONTINUITY",
                     f"公式编号不连续：章节 {chapter} 当前期望 ({chapter}-{expected})，实际为 ({chapter}-{seq})")
    formula_seq_in_chapter[chapter] = seq


def _validate_figure_table_sequence(
    findings: list[Finding],
    image_lines: list[tuple[int, str, str | None]],
    table_starts: list[tuple[int, str | None]],
    lines: list[str],
) -> None:
    """检查图片和表格的序号是否连续、章节号是否匹配。"""
    # 图片
    figure_seq: dict[int, int] = {}
    global_figure_seq: int = 0
    for img_line, alt, chapter_no in image_lines:
        m = re.search(r"(?:图|Figure|Fig\.|Fi\.)\s*(\d+)(?:\s*[-－.]\s*(\d+))?", alt)
        if m:
            if m.group(2) is not None:
                chapter = int(m.group(1))
                seq = int(m.group(2))
                if chapter_no is not None and chapter != int(chapter_no):
                    add_findings(findings, "WARN", img_line, "FIGURE_NUMBER_MISMATCH",
                                 f"图片编号章节号不一致：当前章节为 {chapter_no}，但图片编号为 {chapter}-{seq}")
                expected = figure_seq.get(chapter, 0) + 1
                if seq != expected:
                    add_findings(findings, "ERROR", img_line, "FIGURE_NUMBER_DISCONTINUITY",
                                 f"图片编号不连续：章节 {chapter} 当前期望 {chapter}-{expected}，实际为 {chapter}-{seq}")
                figure_seq[chapter] = seq
            else:
                num = int(m.group(1))
                global_figure_seq += 1
                if num != global_figure_seq:
                    add_findings(findings, "ERROR", img_line, "FIGURE_NUMBER_DISCONTINUITY",
                                 f"图片编号不连续：当前期望 {global_figure_seq}，实际为 {num}")
                    global_figure_seq = num
        else:
            add_findings(findings, "WARN", img_line, "FIGURE_NUMBER_MISSING",
                         "图片缺少规范的序号（建议格式：图N 标题 或 图N-M 标题）")

    # 表格
    table_seq: dict[int, int] = {}
    global_table_seq: int = 0
    for start, chapter_no in table_starts:
        look = start - 1
        while look >= 1 and not lines[look - 1].strip():
            look -= 1
        if look >= 1:
            caption = lines[look - 1].strip()
            m = re.search(r"(?:表|Table)\s*(\d+)(?:\s*[-－.]\s*(\d+))?", caption)
            if m:
                if m.group(2) is not None:
                    chapter = int(m.group(1))
                    seq = int(m.group(2))
                    if chapter_no is not None and chapter != int(chapter_no):
                        add_findings(findings, "WARN", start, "TABLE_NUMBER_MISMATCH",
                                     f"表格编号章节号不一致：当前章节为 {chapter_no}，但表格编号为 {chapter}-{seq}")
                    expected = table_seq.get(chapter, 0) + 1
                    if seq != expected:
                        add_findings(findings, "ERROR", start, "TABLE_NUMBER_DISCONTINUITY",
                                     f"表格编号不连续：章节 {chapter} 当前期望 {chapter}-{expected}，实际为 {chapter}-{seq}")
                    table_seq[chapter] = seq
                else:
                    num = int(m.group(1))
                    global_table_seq += 1
                    if num != global_table_seq:
                        add_findings(findings, "ERROR", start, "TABLE_NUMBER_DISCONTINUITY",
                                     f"表格编号不连续：当前期望 {global_table_seq}，实际为 {num}")
                        global_table_seq = num
            else:
                add_findings(findings, "WARN", start, "TABLE_NUMBER_MISSING",
                             "表格缺少规范的序号（建议格式：表N 标题 或 表N-M 标题）")


def _validate_reference_number_continuity(
    findings: list[Finding],
    ref_items: list[tuple[int, int]],
) -> None:
    if not ref_items:
        return
    numbers = [n for _ln, n in ref_items]
    seen: set[int] = set()
    if numbers[0] != 1:
        add_findings(findings, "ERROR", ref_items[0][0], "REF_NUMBER_NOT_START_AT_ONE",
                     f"参考文献编号应从 [1] 开始，当前首条为 [{numbers[0]}]")
    for i, (line_no, n) in enumerate(ref_items):
        if n in seen:
            add_findings(findings, "ERROR", line_no, "REF_NUMBER_DUPLICATE", f"参考文献编号重复：[{n}] 出现多次")
        seen.add(n)
        if i > 0:
            prev_n = numbers[i - 1]
            if n != prev_n + 1:
                add_findings(findings, "ERROR", line_no, "REF_NUMBER_DISCONTINUITY",
                             f"参考文献编号不连续：前一条为 [{prev_n}]，当前为 [{n}]，期望 [{prev_n + 1}]")


def _validate_text_around_blocks(
    findings: list[Finding],
    image_lines: list[tuple[int, str, str | None]],
    table_starts: list[tuple[int, str | None]],
    lines: list[str],
) -> None:
    """检查图片和表格前后是否有段落文字描述。"""

    def _is_valid_text(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if ATX_HEADING_RE.match(s):
            return False
        if IMAGE_MD_RE.search(s):
            return False
        if _is_table_row(s) or _is_table_separator(s):
            return False
        if TABLE_CAPTION_RE.match(s):
            return False
        if FIGURE_TITLE_RE_LOOSE.match(s):
            return False
        if s.startswith("```") or s.startswith("$$") or s.startswith(">"):
            return False
        if s == "---":
            return False
        if SETEXT_RE.match(s):
            return False
        if REFERENCE_ITEM_RE.match(s):
            return False
        return True

    for img_line, _alt, _chapter in image_lines:
        has_text_before = False
        for i in range(img_line - 2, -1, -1):
            if _is_valid_text(lines[i]):
                has_text_before = True
                break
            if lines[i].strip():
                break
        has_text_after = False
        for i in range(img_line, len(lines)):
            if _is_valid_text(lines[i]):
                has_text_after = True
                break
            if lines[i].strip():
                break
        if not has_text_before and not has_text_after:
            add_findings(findings, "ERROR", img_line, "MISSING_TEXT_AROUND_IMAGE",
                         "图片前后均缺少段落文字描述，请在图片前或图片后添加对图片的说明或分析文字")

    for start, _chapter in table_starts:
        table_end = start
        for j in range(start, len(lines)):
            if not _is_table_row(lines[j].strip()):
                break
            table_end = j
        has_text_before = False
        look = start - 2
        while look >= 0 and not lines[look].strip():
            look -= 1
        if look >= 0 and TABLE_CAPTION_RE.match(lines[look].strip()):
            look -= 1
        while look >= 0 and not lines[look].strip():
            look -= 1
        if look >= 0 and _is_valid_text(lines[look]):
            has_text_before = True
        has_text_after = False
        look = table_end
        while look < len(lines) and not lines[look].strip():
            look += 1
        if look < len(lines) and _is_valid_text(lines[look]):
            has_text_after = True
        if not has_text_before and not has_text_after:
            add_findings(findings, "ERROR", start, "MISSING_TEXT_AROUND_TABLE",
                         "表格前后均缺少段落文字描述，请在表格前或表格后添加对表格的说明或分析文字")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="检查 Markdown 是否符合本项目导出规范")
    parser.add_argument("--md", required=True, help="Markdown 文件路径")
    parser.add_argument(
        "--mode",
        choices=["undergraduate", "journal"],
        default="undergraduate",
        help="写作模式：undergraduate=本科毕设（默认），journal=期刊/会议论文",
    )
    parser.add_argument("--strict", action="store_true", help="将 WARN 也视为失败")
    parser.add_argument("--always-write", action="store_true",
                        help="无 --output 时也落盘到统一产物树（默认仅 stdout）")
    parser.add_argument("--output", "-o", default=None,
                        help="输出目录（默认：统一产物树 <cwd>/skills-output/thesis/thesis-writing/<时间戳>/，docs/specs/OUTPUT.md C-1）")
    args = parser.parse_args()

    path = Path(args.md).resolve()
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    findings, notes = check_markdown(path, mode=args.mode)

    if args.output or args.always_write:
        plan = plan_output("thesis", "thesis-writing", f"{path.stem}_findings.json",
                           explicit=args.output)
        out_path = plan.primary
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([{"level": f.level, "line": f.line, "code": f.code, "message": f.message} for f in findings],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        plan.commit()
        print(f"[md-check] wrote findings → {out_path}")
        return

    print(f"[md-check] file={path}")
    for n in notes:
        print(f"[md-check] NOTE: {n}")

    if not findings:
        print("[md-check] PASS: 未发现规范问题")
        return

    level_rank = {"ERROR": 0, "WARN": 1}
    findings.sort(key=lambda f: (level_rank.get(f.level, 9), f.line, f.code))

    err = 0
    warn = 0
    for f in findings:
        print(f"[md-check] {f.level} L{f.line} {f.code}: {f.message}")
        if f.level == "ERROR":
            err += 1
        elif f.level == "WARN":
            warn += 1

    print(f"[md-check] SUMMARY: ERROR={err}, WARN={warn}")

    if err > 0:
        raise SystemExit(2)
    if args.strict and warn > 0:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
