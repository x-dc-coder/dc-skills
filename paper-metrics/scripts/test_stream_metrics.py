#!/usr/bin/env python3
"""Tests for stream_metrics — metrics computed on the NON-prose streams.

Written before the implementation (TDD).  These are the metrics the 2026-09-14
scope decision requires: tables and figures are analysed separately and are NEVER
merged into the prose plain text.

Covers:
  1. S-CAP-01 caption coverage (a figure/table without a caption is not paired)
  2. S-NUM-02 caption numbering (gaps and duplicates, per kind)
  3. S-REF-03 in-text reference consistency (dangling references, uncited items)
  4. Every record follows the product contract and declares its stream scope

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_stream_metrics.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import doc_model as dm
import stream_metrics as sm


def _block(block_type: str, **fields: object) -> dict:
    return {"type": block_type, **fields}


def _write(tmp_path: Path, name: str, blocks: list[dict]) -> dm.DocumentModel:
    path = tmp_path / "auto" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    return dm.parse_document(path)


@pytest.fixture
def model(tmp_path: Path) -> dm.DocumentModel:
    """Two figures (one uncaptioned), one table, prose with references.

    Deliberately inconsistent: Figure 1 and Table 2 are declared, while the prose
    cites Figure 1 / 图 2 / Figure 9 / Table 1 - so the figure stream is missing 2
    and 9, the table stream is missing 1, and Table 2 is never cited.
    """
    return _write(tmp_path, "p1_content_list.json", [
        _block("text", text="As shown in Figure 1 and 图 2, the method works."),
        _block("text", text="Table 1 lists the results; see Figure 9 for details."),
        _block("image", img_path="images/f1.jpg",
               image_caption=["Figure 1", "Framework overview"]),
        _block("image", img_path="images/f2.jpg"),
        _block("table", table_body="<table><tr><td>1</td></tr></table>",
               table_caption=["Table 2", "Results"]),
    ])


# ---------------------------------------------------------------------------
# 1. Caption coverage
# ---------------------------------------------------------------------------

def test_caption_coverage_counts_uncaptioned_figures(model: dm.DocumentModel) -> None:
    record = sm.caption_coverage(model)
    assert record["metric_spec"] == "S-CAP-01"
    assert record["n"] == 2 and record["denominator"] == 3   # n = captioned (numerator)
    assert record["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert record["unit"] == "ratio"
    assert record["state"] == "OBSERVED" and record["method"] == "rule"
    assert record["scope"] == ["figures", "tables"]
    assert record["evidence"]["uncaptioned"] == [
        {"kind": "figures", "index": 1, "block_index": 3}]
    assert "CAPTION_MISSING" in record["warnings"]


def test_caption_coverage_is_null_when_there_is_nothing_to_pair(tmp_path: Path) -> None:
    record = sm.caption_coverage(_write(tmp_path, "p2_content_list.json", [
        _block("text", text="Only prose here.")]))
    assert record["value"] is None and record["n"] == 0
    assert "NO_FIGURES_OR_TABLES" in record["warnings"]


# ---------------------------------------------------------------------------
# 2. Caption numbering
# ---------------------------------------------------------------------------

def test_numbering_consistency_reports_gaps_and_duplicates(model: dm.DocumentModel) -> None:
    record = sm.numbering_consistency(model)
    assert record["metric_spec"] == "S-NUM-02"
    evidence = record["evidence"]
    assert evidence["declared"] == {"figures": ["1"], "tables": ["2"]}
    assert evidence["gaps"] == {"figures": [], "tables": ["1"]}
    assert evidence["duplicates"] == {"figures": [], "tables": []}
    assert record["n"] == 2                       # n = non-duplicate keys (numerator)
    assert record["denominator"] == 2
    assert record["value"] == pytest.approx(1.0)  # none duplicated
    assert "NUMBER_GAPS" in record["warnings"]


def test_numbering_duplicates_lower_the_value(tmp_path: Path) -> None:
    record = sm.numbering_consistency(_write(tmp_path, "p3_content_list.json", [
        _block("image", img_path="a.jpg", image_caption=["Figure 1", "One"]),
        _block("image", img_path="b.jpg", image_caption=["图 1", "重复编号"]),
        _block("table", table_body="<table></table>",
               table_caption=["Table 1", "One"]),
    ]))
    assert record["evidence"]["duplicates"] == {"figures": ["1"], "tables": []}
    assert record["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert "DUPLICATE_NUMBERS" in record["warnings"]


# ---------------------------------------------------------------------------
# 3. In-text reference consistency
# ---------------------------------------------------------------------------

def test_reference_consistency_finds_dangling_and_uncited(model: dm.DocumentModel) -> None:
    record = sm.reference_consistency(model)
    assert record["metric_spec"] == "S-REF-03"
    evidence = record["evidence"]
    assert evidence["declared"] == {"figures": ["1"], "tables": ["2"]}
    assert evidence["referenced"] == {"figures": ["1", "2", "9"], "tables": ["1"]}
    assert evidence["dangling"] == {"figures": ["2", "9"], "tables": ["1"]}
    assert evidence["uncited"] == {"figures": [], "tables": ["2"]}
    # (1 shared figure number + 0 shared table numbers) / (2 declared + 4 referenced)
    # Jaccard: |A ∪ B| = 2 + 4 - 1 = 5, so 1/5.  The old denominator (|A| + |B|)
    # capped this at 1/2 and made a near-perfect corpus read as "half unmatched"
    # (cross-review blocker B2).
    assert record["value"] == pytest.approx(1 / 5, abs=1e-6)
    assert (record["n"], record["denominator"]) == (1, 5)   # value == n / denominator
    assert "DANGLING_REFERENCES" in record["warnings"]
    assert "UNCITED_FIGURES_OR_TABLES" in record["warnings"]


def test_reference_consistency_ignores_numbers_in_tables(tmp_path: Path) -> None:
    """Only the prose stream is searched: a dataset value of 2 inside a table cell
    must never look like a figure reference."""
    record = sm.reference_consistency(_write(tmp_path, "p4_content_list.json", [
        _block("text", text="See Figure 1."),
        _block("image", img_path="a.jpg", image_caption=["Figure 1", "One"]),
        _block("table",
               table_body="<table><tr><td>Figure</td><td>2</td></tr></table>",
               table_caption=["Table 1", "Values"]),
    ]))
    assert record["evidence"]["referenced"]["figures"] == ["1"]
    # Table 1 is declared but never cited -> uncited, not dangling.
    assert record["evidence"]["dangling"] == {"figures": [], "tables": []}
    assert record["evidence"]["uncited"] == {"figures": [], "tables": ["1"]}
    # Jaccard: |A ∪ B| = 2 + 1 - 1 = 2 -> 0.5 (the old denominator gave 1/3)
    assert record["value"] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Product contract
# ---------------------------------------------------------------------------

def test_every_stream_metric_declares_scope_and_contract(model: dm.DocumentModel) -> None:
    metrics = sm.stream_metrics(model)
    assert set(metrics) == {"S-CAP-01", "S-NUM-02", "S-REF-03", "S-SIZ-04",
                            "S-CAPL-05", "S-TBL-06", "S-TBL-07", "S-TBL-08",
                            "S-TBL-09", "S-TBL-10", "S-TBL-11", "S-TBL-12",
                            "S-TBL-13", "S-REF-14"}
    required = {"value", "n", "denominator", "unit", "state", "method",
                "metric_spec", "evidence", "warnings", "scope"}
    for metric_id, record in metrics.items():
        assert required <= set(record), metric_id
        assert record["metric_spec"] == metric_id
        assert record["scope"], metric_id
        for name in record["scope"]:
            assert name in {s.value for s in dm.Stream}, (metric_id, name)
        # A stream metric is *about* a non-prose stream, so it must name one.  Prose
        # may appear next to it only as a denominator: S-TBL-09 (table density) needs
        # prose units, and a metric like that must never be prose-only.
        assert ("tables" in record["scope"] or "figures" in record["scope"]), \
            f"{metric_id}: stream metrics must read a non-prose stream"
        if "prose" in record["scope"]:
            assert len(record["scope"]) > 1, \
                f"{metric_id}: prose may only appear alongside a non-prose stream"


def test_stream_metrics_are_deterministic(model: dm.DocumentModel) -> None:
    first = sm.stream_metrics(model)
    second = sm.stream_metrics(model)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_unavailable_records_are_explicit_not_silent() -> None:
    """When the base artifact cannot be parsed, every stream metric must still be
    reported - as explicit "not measured" records, never as absent ones."""
    records = sm.unavailable_records("CANONICAL_UNPARSEABLE")
    assert set(records) == {"S-CAP-01", "S-NUM-02", "S-REF-03", "S-SIZ-04",
                            "S-CAPL-05", "S-TBL-06", "S-TBL-07", "S-TBL-08",
                            "S-TBL-09", "S-TBL-10", "S-TBL-11", "S-TBL-12",
                            "S-TBL-13", "S-REF-14"}
    for metric_id, record in records.items():
        assert record["metric_spec"] == metric_id
        assert record["value"] is None and record["n"] == 0
        assert "CANONICAL_UNPARSEABLE" in record["warnings"]
        assert record["scope"] == list(sm._SCOPE_OF_METRIC[metric_id])
        assert record["state"] == "OBSERVED" and record["method"] == "rule"
        assert record["unit"] == sm._METRIC_UNITS[metric_id]   # M9: never a hardcoded ratio


# ---------------------------------------------------------------------------
# 5. Inventory structure: image resolution + caption length (D-1)
# ---------------------------------------------------------------------------

def _png_bytes(width: int, height: int) -> bytes:
    import struct
    ihdr = struct.pack(">II", width, height) + bytes([8, 6, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
            + b"\x00\x00\x00\x00")


def _jpeg_bytes(width: int, height: int) -> bytes:
    import struct
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + bytes([8])
            + struct.pack(">HH", height, width) + bytes([3]) + b"\x00" * 6)
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _gif_bytes(width: int, height: int) -> bytes:
    import struct
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 4


def test_image_resolution_metric_reads_headers(tmp_path: Path) -> None:
    """S-SIZ-04: figure images must be measured from their own headers, with the
    unreadable ones reported instead of silently dropped."""
    auto = tmp_path / "auto"
    (auto / "images").mkdir(parents=True)
    (auto / "images" / "big.png").write_bytes(_png_bytes(1200, 800))
    (auto / "images" / "small.jpg").write_bytes(_jpeg_bytes(400, 300))
    (auto / "images" / "tiny.gif").write_bytes(_gif_bytes(100, 100))
    model = _write(tmp_path, "p5_content_list.json", [
        _block("image", img_path="images/big.png",
               image_caption=["Figure 1", "Big"]),
        _block("image", img_path="images/small.jpg",
               image_caption=["Figure 2", "Small"]),
        _block("image", img_path="images/tiny.gif",
               image_caption=["Figure 3", "Tiny"]),
        _block("image", img_path="images/missing.png",
               image_caption=["Figure 4", "Gone"]),
    ])
    record = sm.image_resolution(model)
    assert record["metric_spec"] == "S-SIZ-04"
    assert record["scope"] == ["figures"]
    assert record["n"] == 1 and record["denominator"] == 3   # n = adequate (numerator); missing one not measured
    assert record["value"] == pytest.approx(1 / 3, abs=1e-6)  # only the 1200px one is adequate
    assert record["unit"] == "ratio"
    sizes = {item["img_path"]: (item["width"], item["height"])
             for item in record["evidence"]["images"]}
    assert sizes["images/big.png"] == (1200, 800)
    assert sizes["images/small.jpg"] == (400, 300)
    assert sizes["images/tiny.gif"] == (100, 100)
    assert record["evidence"]["unreadable"] == ["images/missing.png"]
    assert "IMAGE_UNREADABLE" in record["warnings"]
    assert record["evidence"]["median_width"] == 400
    assert record["evidence"]["min_width_required"] == sm._MIN_IMAGE_WIDTH
    for sample in record["evidence"]["sample"]:
        assert sample["block_index"] in {0, 1, 2}
        assert sample["field"] == "img_path"
        assert sample["excerpt"]


def test_image_resolution_is_null_without_figures(tmp_path: Path) -> None:
    record = sm.image_resolution(_write(tmp_path, "p6_content_list.json", [
        _block("text", text="Only prose.")]))
    assert record["value"] is None and record["n"] == 0
    assert "NO_FIGURES" in record["warnings"]
    # the evidence shape must not depend on whether anything was measurable
    assert record["evidence"]["median_width"] is None
    assert record["evidence"]["min_width_required"] == sm._MIN_IMAGE_WIDTH


def test_caption_length_reports_the_median(tmp_path: Path) -> None:
    """S-CAPL-05: caption length is a pure inventory fact (characters, so it is
    language-agnostic - and therefore NOT comparable across languages)."""
    model = _write(tmp_path, "p7_content_list.json", [
        _block("image", img_path="a.png", image_caption=["Figure 1", "x" * 10]),
        _block("image", img_path="b.png", image_caption=["Figure 2", "y" * 50]),
        _block("table", table_body="<table></table>",
               table_caption=["Table 1", "z" * 90]),
        _block("text", text="No caption here."),
    ])
    record = sm.caption_length(model)
    assert record["metric_spec"] == "S-CAPL-05"
    assert record["scope"] == ["figures", "tables"]
    assert record["n"] == 3
    assert record["unit"] == "characters"
    # lengths: 12 ("Figure 1" + 10 x's) is wrong on purpose -> the caption is joined
    lengths = sorted(item["chars"] for item in record["evidence"]["lengths"])
    assert lengths == sorted([len("Figure 1 " + "x" * 10),
                              len("Figure 2 " + "y" * 50),
                              len("Table 1 " + "z" * 90)])
    assert record["value"] == pytest.approx(lengths[1], abs=1e-6)   # median
    assert record["evidence"]["min"] == lengths[0]
    assert record["evidence"]["max"] == lengths[-1]
    for sample in record["evidence"]["sample"]:
        assert sample["field"] in {"image_caption", "table_caption"}
        assert sample["excerpt"]


def test_caption_length_is_null_without_captions(tmp_path: Path) -> None:
    record = sm.caption_length(_write(tmp_path, "p8_content_list.json", [
        _block("image", img_path="a.png")]))
    assert record["value"] is None and record["n"] == 0
    assert "NO_CAPTIONS" in record["warnings"]


# ---------------------------------------------------------------------------
# 6. Table structure (D-1, tables only)
# ---------------------------------------------------------------------------

_TABLE_HTML_A = ('<table><tr><td colspan="3">Totals 2026</td></tr>'
                 '<tr><td>1</td><td></td><td>3</td></tr></table>')
_TABLE_HTML_B = "<table><tr><td>x</td><td>y</td></tr></table>"
_TABLE_HTML_C = "<table><tr><td>a</td><td>b</td><td>c</td><td>d</td></tr></table>"


def _table_model(tmp_path: Path) -> dm.DocumentModel:
    return _write(tmp_path, "p9_content_list.json", [
        _block("text", text="Table 1 summarises the results."),
        _block("table", table_body=_TABLE_HTML_A, table_caption=["表 1", "汇总"]),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["表 2", "对比"]),
        _block("table", table_body=_TABLE_HTML_C, table_caption=["表 3", "明细"]),
        _block("table", table_body="", table_caption=["表 4", "缺失正文"]),
    ])


def test_table_columns_metric_counts_colspan(tmp_path: Path) -> None:
    """S-TBL-06: declared columns must sum colspan per row.

    On the real corpus colspan appears ~19 times per table, so counting <td> would
    understate every wide table.
    """
    record = sm.table_columns(_table_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-06"
    assert record["scope"] == ["tables"]
    assert record["unit"] == "columns"
    assert record["n"] == 3                       # the empty-body table is not measured
    columns = sorted(item["declared_columns"] for item in record["evidence"]["tables"])
    assert columns == [2, 3, 4]
    assert record["value"] == pytest.approx(3.0)  # nearest-rank median of [2, 3, 4]
    assert record["evidence"]["empty_bodies"] == [4]
    first = record["evidence"]["tables"][0]
    assert first["block_index"] == 1
    assert first["rows"] == 2 and first["cells"] == 4
    assert first["colspan_merges"] == 1
    assert first["excerpt"].startswith("<table>")
    assert "TABLE_BODY_EMPTY" in record["warnings"]


def test_table_empty_cell_ratio_pools_cells(tmp_path: Path) -> None:
    """S-TBL-07: 1 empty cell out of 4 + 2 + 4 = 10 measurable cells."""
    record = sm.table_empty_cells(_table_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-07"
    assert record["scope"] == ["tables"]
    assert record["unit"] == "ratio"
    assert record["evidence"]["cells"] == 10
    assert record["evidence"]["empty_cells"] == 1
    assert record["value"] == pytest.approx(0.1)
    assert record["n"] == 1 and record["denominator"] == 10   # n = empty cells (numerator)


def test_table_missing_body_ratio_is_reported(tmp_path: Path) -> None:
    """S-TBL-08: a table with no body is missing DATA, not a table with 0 cells."""
    record = sm.table_missing_body(_table_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-08"
    assert record["scope"] == ["tables"]
    assert record["unit"] == "ratio"
    assert record["n"] == 1 and record["denominator"] == 4   # n = missing-body tables (numerator)
    assert record["value"] == pytest.approx(0.25)
    assert record["evidence"]["missing"] == [4]
    assert "TABLE_BODY_EMPTY" in record["warnings"]


def test_table_metrics_are_null_without_tables(tmp_path: Path) -> None:
    model = _write(tmp_path, "p10_content_list.json", [
        _block("text", text="Only prose."),
        _block("image", img_path="a.png", image_caption=["Figure 1", "x"]),
    ])
    for record in (sm.table_columns(model), sm.table_empty_cells(model),
                   sm.table_missing_body(model)):
        assert record["value"] is None and record["n"] == 0
        assert "NO_TABLES" in record["warnings"]
        assert record["scope"] == ["tables"]


# ---------------------------------------------------------------------------
# 7. Table density (cross-stream) + section placement
# ---------------------------------------------------------------------------

def _density_model(tmp_path: Path) -> dm.DocumentModel:
    return _write(tmp_path, "p11_content_list.json", [
        _block("text", text="one two three four five"),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["Table 1", "x"]),
        _block("table", table_body=_TABLE_HTML_C, table_caption=["Table 2", "y"]),
    ])


def test_table_density_uses_the_frozen_tokenizer(tmp_path: Path) -> None:
    """S-TBL-09: 2 tables over 5 prose words -> 400 per 1000 words.

    The denominator comes from text_metrics (the frozen tokenizer), not from a second
    word-splitting rule: two tokenizers would drift apart and the density would follow
    whichever one ran.
    """
    record = sm.table_density(_density_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-09"
    assert record["scope"] == ["prose", "tables"]
    assert record["unit"] == "per-1000-words"
    assert record["n"] == 2
    assert record["denominator"] == 5
    assert record["value"] == pytest.approx(400.0)
    assert record["evidence"]["unit_basis"] == "words"
    assert record["evidence"]["language"] == "en"


def test_table_density_counts_cjk_units_in_chinese(tmp_path: Path) -> None:
    model = _write(tmp_path, "p12_content_list.json", [
        _block("text", text="本文提出方法"),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["表 1", "x"]),
    ])
    record = sm.table_density(model)
    assert record["unit"] == "per-1000-cjk-units"
    assert record["evidence"]["language"] == "zh"
    assert record["denominator"] == 6            # 6 CJK characters, no ASCII tokens
    assert record["value"] == pytest.approx(1000 / 6)


def test_table_density_is_null_without_prose_or_tables(tmp_path: Path) -> None:
    no_prose = _write(tmp_path, "p13_content_list.json", [
        _block("text", text=""),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["Table 1", "x"]),
    ])
    record = sm.table_density(no_prose)
    assert record["value"] is None
    assert "NO_PROSE_UNITS" in record["warnings"]

    no_tables = _write(tmp_path, "p14_content_list.json", [
        _block("text", text="one two three")])
    record = sm.table_density(no_tables)
    assert record["value"] is None
    assert "NO_TABLES" in record["warnings"]


def test_table_placement_is_concentrated_where_tables_actually_sit(tmp_path: Path) -> None:
    """S-TBL-10: top-1 section share, with the full distribution as evidence.

    The value is deliberately the most-used section rather than "share in results":
    on the real Chinese corpus many tables sit under sub-headings whose label cannot be
    resolved ("3．1 案例构造"), so a results-only value would under-report by an unknown
    amount.  The results share IS reported, next to the caveat that it is a lower bound.
    """
    model = _write(tmp_path, "p15_content_list.json", [
        _block("text", text="body"),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["Table 1", "x"]),
        _block("table", table_body=_TABLE_HTML_C, table_caption=["Table 2", "y"]),
        _block("table", table_body="<table><tr><td>a</td></tr></table>",
               table_caption=["Table 3", "z"]),
    ])
    sections = {0: "introduction", 1: "experiments", 2: "experiments", 3: "method"}
    record = sm.table_placement(model, sections=sections)
    assert record["metric_spec"] == "S-TBL-10"
    assert record["scope"] == ["tables"]
    assert record["n"] == 2 and record["denominator"] == 3   # n = top-section tables (numerator)
    assert record["evidence"]["sections"] == {"experiments": 2, "method": 1}
    assert record["evidence"]["top_section"] == "experiments"
    assert record["value"] == pytest.approx(2 / 3)
    assert record["evidence"]["results_share"] == pytest.approx(2 / 3)
    assert "保守下界" in record["evidence"]["results_share_note"]
    for sample in record["evidence"]["sample"]:
        assert sample["section"] in {"experiments", "method"}
        assert sample["field"] == "table_body"


def test_table_placement_is_null_without_a_section_mapping(tmp_path: Path) -> None:
    record = sm.table_placement(_density_model(tmp_path))
    assert record["value"] is None and record["n"] == 0
    assert "SECTIONS_UNAVAILABLE" in record["warnings"]

    no_tables = _write(tmp_path, "p16_content_list.json", [_block("text", text="x")])
    record = sm.table_placement(no_tables, sections={0: "introduction"})
    assert record["value"] is None
    assert "NO_TABLES" in record["warnings"]


# ---------------------------------------------------------------------------
# 9. Table content: numeric density + LaTeX residue
# ---------------------------------------------------------------------------

_TABLE_NUM_A = ("<table><tr><td>a</td><td>1</td><td>2.5</td></tr>"
                "<tr><td>b</td><td>3</td><td>4</td></tr></table>")
_TABLE_NUM_B = ("<table><tr><td colspan='2'>Header</td></tr>"
                "<tr><td>G13</td><td>522(90.0%)</td></tr></table>")
_TABLE_NUM_C = "<table><tr><td>$r=4.5$</td><td>1</td></tr></table>"


def _content_model(tmp_path: Path) -> dm.DocumentModel:
    return _write(tmp_path, "p17_content_list.json", [
        _block("table", table_body=_TABLE_NUM_A, table_caption=["Table 1", "a"]),
        _block("table", table_body=_TABLE_NUM_B, table_caption=["Table 2", "b"]),
        _block("table", table_body=_TABLE_NUM_C, table_caption=["Table 3", "c"]),
    ])


def test_numeric_cell_share_separates_strict_from_number_bearing(tmp_path: Path) -> None:
    """S-TBL-11: 5 of 11 cells are STRICTLY numeric; 8 merely contain a number.

    Both readings are reported because the difference is real: '522(90.0%)' and 'G13'
    carry numbers but are labels/results-with-parentheses, so a single number would be
    either misleading or useless depending on the reader.
    """
    record = sm.numeric_cell_share(_content_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-11"
    assert record["scope"] == ["tables"]
    assert record["unit"] == "ratio"
    assert record["n"] == 5 and record["denominator"] == 11   # n = numeric cells (numerator)
    # the product contract rounds values to 6 decimals, so compare at that resolution
    assert record["value"] == pytest.approx(5 / 11, abs=1e-6)
    assert record["evidence"]["numeric_bearing_cells"] == 8
    assert record["evidence"]["numeric_bearing_share"] == pytest.approx(8 / 11)
    first = record["evidence"]["tables"][0]
    assert first["cells"] == 6 and first["numeric_cells"] == 4
    assert "LATEX_IN_CELLS" in record["warnings"]


def test_numeric_row_share_is_robust_to_merged_header_rows(tmp_path: Path) -> None:
    """S-TBL-12: rows are counted, not reconstructed columns - colspan/rowspan make
    column alignment unreliable, while a row's own cells are always known."""
    record = sm.numeric_row_share(_content_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-12"
    assert record["scope"] == ["tables"]
    assert record["n"] == 3 and record["denominator"] == 5          # n = numeric rows (numerator); 2 + 2 + 1 rows
    assert record["value"] == pytest.approx(3 / 5)
    assert record["evidence"]["numeric_rows"] == 3


def test_table_latex_residue_is_reported(tmp_path: Path) -> None:
    """S-TBL-13: MinerU puts LaTeX inside table cells; that is an extraction artifact
    worth its own number, because it silently breaks any numeric reuse of the table."""
    record = sm.table_latex_residue(_content_model(tmp_path))
    assert record["metric_spec"] == "S-TBL-13"
    assert record["scope"] == ["tables"]
    assert record["n"] == 1 and record["denominator"] == 11   # n = latex cells (numerator)
    assert record["value"] == pytest.approx(1 / 11, abs=1e-6)
    tables = {item["block_index"]: item for item in record["evidence"]["tables"]}
    assert tables[2]["latex_cells"] == 1
    for sample in record["evidence"]["sample"]:
        assert sample["field"] == "table_body"
        assert sample["excerpt"].startswith("<table>")


def test_table_content_metrics_are_null_without_tables(tmp_path: Path) -> None:
    model = _write(tmp_path, "p18_content_list.json", [_block("text", text="prose")])
    for record in (sm.numeric_cell_share(model), sm.numeric_row_share(model),
                   sm.table_latex_residue(model)):
        assert record["value"] is None and record["n"] == 0
        assert "NO_TABLES" in record["warnings"]


# ---------------------------------------------------------------------------
# 10. Table citation depth
# ---------------------------------------------------------------------------

def test_reference_depth_counts_mentions_per_table(tmp_path: Path) -> None:
    """S-REF-14: 表 1 mentioned twice, 表 2 once, 表 3 never -> mean depth 1.0.

    Depth is the difference between "the table is listed" and "the table is discussed";
    the uncited one stays visible instead of being averaged away.
    """
    model = _write(tmp_path, "p19_content_list.json", [
        _block("text", text="表 1 给出对比。表 1 也讨论了下界。表 2 是明细。"),
        _block("table", table_body=_TABLE_NUM_B, table_caption=["表 1", "对比"]),
        _block("table", table_body=_TABLE_HTML_B, table_caption=["表 2", "明细"]),
        _block("table", table_body=_TABLE_HTML_C, table_caption=["表 3", "未引用"]),
    ])
    record = sm.reference_depth(model)
    assert record["metric_spec"] == "S-REF-14"
    assert record["scope"] == ["prose", "tables"]
    assert record["unit"] == "citations-per-declared-table"
    assert record["n"] == 3 and record["denominator"] == 3
    assert record["value"] == pytest.approx(1.0)
    evidence = record["evidence"]
    assert evidence["depths"] == {"1": 2, "2": 1, "3": 0}
    assert evidence["uncited"] == ["3"]
    assert evidence["single_mention_share"] == pytest.approx(1 / 3, abs=1e-6)
    assert evidence["multi_mention_share"] == pytest.approx(1 / 3, abs=1e-6)
    assert evidence["max_depth"] == 2
    for sample in evidence["sample"]:
        # the declared number is read from the CAPTION, so evidence points at that field
        assert sample["field"] == "table_caption"


def test_reference_depth_is_null_without_tables_or_prose(tmp_path: Path) -> None:
    no_tables = _write(tmp_path, "p20_content_list.json", [
        _block("text", text="表 1 被引用了，但没有表。")])
    record = sm.reference_depth(no_tables)
    assert record["value"] is None and record["n"] == 0
    assert "NO_TABLES" in record["warnings"]

    no_prose = _write(tmp_path, "p21_content_list.json", [
        _block("table", table_body=_TABLE_HTML_B, table_caption=["表 1", "x"])])
    record = sm.reference_depth(no_prose)
    assert record["value"] is None
    assert "NO_PROSE_TEXT" in record["warnings"]


def test_reference_counts_stay_prose_only(tmp_path: Path) -> None:
    """The counting version must inherit the prose-only rule: a "表 2" inside a cell is
    not a citation, or tables would cite themselves."""
    model = _write(tmp_path, "p22_content_list.json", [
        _block("text", text="表 1 见正文。"),
        _block("table", table_body="<table><tr><td>表 2</td><td>表 2</td></tr></table>",
               table_caption=["表 1", "x"]),
    ])
    counts, _dropped = sm._reference_counts(model)
    assert counts["tables"] == {"1": 1}

# ---------------------------------------------------------------------------
# 11. #18.8 reference parsing: plurals, ranges, letter numbers, boundaries
# ---------------------------------------------------------------------------

def test_reference_regex_recognises_plurals_ranges_and_letter_numbers() -> None:
    assert sm._ref_keys("Figures 4 and Figure A1", sm._FIGURE_REF_RE) == ["4", "A1"]
    assert sm._ref_keys("Tables 13-15 and Table 5", sm._TABLE_REF_RE) == ["13", "14", "15", "5"]


def test_reference_regex_rejects_embeddings_and_math_intervals() -> None:
    # "tablet" / "figure of merit" are not references (word boundary); "Table 3 shows"
    # captures only "3"; a bare math interval is never a reference.
    assert sm._ref_keys("a tablet", sm._TABLE_REF_RE) == []
    assert sm._ref_keys("a figure of merit", sm._FIGURE_REF_RE) == []
    assert sm._ref_keys("Table 3 shows the results", sm._TABLE_REF_RE) == ["3"]
    assert sm._ref_keys("[0,1] and [3-5]", sm._TABLE_REF_RE) == []
    assert sm._ref_keys("[0,1] and [3-5]", sm._FIGURE_REF_RE) == []


def test_reference_range_expansion_is_capped() -> None:
    keys, dropped = sm._expand_ref_expr("1-50")
    assert len(keys) == 50 and keys[0] == "1" and keys[-1] == "50"
    assert dropped == []
    keys, dropped = sm._expand_ref_expr("1-51")
    assert keys == [] and dropped == ["1-51"]


def test_oversized_reference_range_is_dropped_with_a_warning(tmp_path: Path) -> None:
    model = _write(tmp_path, "p_big_range_content_list.json", [
        _block("text", text="See Tables 1-10000 for all rows."),
        _block("table", table_body="<table><tr><td>1</td></tr></table>",
               table_caption=["Table 1", "Rows"]),
    ])
    record = sm.reference_consistency(model)
    assert "REFERENCE_RANGE_TOO_LARGE" in record["warnings"]


def test_numbering_consistency_keeps_letter_numbers_out_of_the_gap_scan(
        tmp_path: Path) -> None:
    model = _write(tmp_path, "p_letter_content_list.json", [
        _block("image", img_path="a.jpg", image_caption=["Figures 13-15", "Overview"]),
        _block("table", table_body="<table></table>", table_caption=["Table A1", "Appendix"]),
    ])
    record = sm.numbering_consistency(model)
    evidence = record["evidence"]
    assert evidence["declared"] == {"figures": ["13", "14", "15"], "tables": ["A1"]}
    # The letter key A1 is not numeric, so it is neither a gap nor a duplicate.
    assert evidence["gaps"]["tables"] == []
    assert evidence["duplicates"] == {"figures": [], "tables": []}


# ---------------------------------------------------------------------------
# 12. #18.3 value == n / denominator identity (M2)
# ---------------------------------------------------------------------------

#: Ratio metrics whose value MUST equal n / denominator (the product contract: n is
#: the numerator).  The four S-* metrics not in this tuple are exempt below.
_RATIO_METRICS: tuple[str, ...] = (
    "S-CAP-01", "S-NUM-02", "S-REF-03", "S-SIZ-04", "S-TBL-07", "S-TBL-08",
    "S-TBL-10", "S-TBL-11", "S-TBL-12", "S-TBL-13",
)

#: Metrics where value is NOT n / denominator, and why: three report a median
#: (n == denominator == the count of observations, value is the quantile), and one is
#: a per-1000 scaled density (value == n / denominator * 1000).
_NON_RATIO_METRICS: dict[str, str] = {
    "S-CAPL-05": "median caption length: value is a quantile, n == denominator == count",
    "S-TBL-06": "median declared columns: value is a quantile, n == denominator == count",
    "S-REF-14": "median citations per declared table: value is a quantile",
    "S-TBL-09": "tables per 1000 prose units: value == n / denominator * 1000",
}


@pytest.fixture
def identity_model(tmp_path: Path) -> tuple[dm.DocumentModel, dict[int, str]]:
    """One figure, one table, prose references: every ratio metric is measurable."""
    (tmp_path / "auto" / "images").mkdir(parents=True, exist_ok=True)
    (tmp_path / "auto" / "images" / "f.png").write_bytes(_png_bytes(1200, 800))
    model = _write(tmp_path, "p_identity_content_list.json", [
        _block("text", text="See Figure 1 and Table 1 for details."),
        _block("image", img_path="images/f.png", image_caption=["Figure 1", "Framework"]),
        _block("table", table_body="<table><tr><td>1</td><td></td></tr></table>",
               table_caption=["Table 1", "Results"]),
    ])
    return model, {0: "introduction", 1: "experiments", 2: "experiments"}


@pytest.mark.parametrize("metric_id", _RATIO_METRICS)
def test_ratio_metric_satisfies_value_equals_n_over_denominator(
        identity_model: tuple[dm.DocumentModel, dict[int, str]], metric_id: str) -> None:
    model, sections = identity_model
    record = sm.stream_metrics(model, sections=sections)[metric_id]
    assert record["value"] is not None, f"{metric_id} should be measurable here"
    expected = record["n"] / record["denominator"]
    assert record["value"] == pytest.approx(expected, abs=1e-6), (
        f"{metric_id}: value {record['value']} != n/denominator "
        f"{record['n']}/{record['denominator']}")


def test_non_ratio_metrics_are_explicitly_exempt_and_the_partition_is_complete() -> None:
    assert set(_NON_RATIO_METRICS) == {"S-CAPL-05", "S-TBL-06", "S-TBL-09", "S-REF-14"}
    assert set(_RATIO_METRICS) | set(_NON_RATIO_METRICS) == set(sm._METRIC_IDS)



def test_reference_metrics_state_which_text_the_search_space_was(tmp_path: Path) -> None:
    """S-REF-03 / S-REF-14 fall back to the whole prose stream when the caller passes no
    canonical text, and the two bases differ by ~50% of characters on the Chinese
    corpus (measured); the record must say which one produced the number
    (issue #20 顺带项)."""
    model = _write(tmp_path, "p_basis_content_list.json", [
        _block("text", text="See Figure 1 and Table 1."),
        _block("image", img_path="a.jpg", image_caption=["Figure 1", "One"]),
        _block("table", table_body="<table><tr><td>1</td></tr></table>",
               table_caption=["Table 1", "Values"]),
    ])
    with_canonical = sm.reference_consistency(model, "See Figure 1 and Table 1.")
    assert with_canonical["evidence"]["unit_basis_text"] == "canonical_body"
    assert "UNIT_BASIS_NOT_CANONICAL" not in with_canonical["warnings"]
    fallback = sm.reference_consistency(model)
    assert fallback["evidence"]["unit_basis_text"] == \
        "prose_stream_incl_headings_and_references"
    assert "UNIT_BASIS_NOT_CANONICAL" in fallback["warnings"]
    depth = sm.reference_depth(model)
    assert depth["evidence"]["unit_basis_text"] == \
        "prose_stream_incl_headings_and_references"
