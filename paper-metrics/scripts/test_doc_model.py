#!/usr/bin/env python3
"""Tests for doc_model — the typed base-artifact document model.

Written before the implementation (TDD): the first run fails on the missing
module, which is the right reason.

Covers:
  1. Stream mapping: every MinerU block type lands in a named stream
  2. Normalisers: table HTML loses its markup AND its attribute digits;
     LaTeX loses its commands but keeps variables and digits
  3. Census: per-stream blocks/chars/digits/caption counts, dropped flag
  4. Boundary parsing: a malformed content_list raises DocumentParseError

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_doc_model.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import doc_model as dm

# ---------------------------------------------------------------------------
# 1. Stream mapping
# ---------------------------------------------------------------------------

def test_every_known_mineru_type_maps_to_a_named_stream() -> None:
    expected = {
        "text": dm.Stream.PROSE,
        "table": dm.Stream.TABLES,
        "equation": dm.Stream.EQUATIONS,
        "image": dm.Stream.FIGURES,
        "chart": dm.Stream.FIGURES,
        "list": dm.Stream.LISTS,
        "page_footnote": dm.Stream.FOOTNOTES,
        "header": dm.Stream.RUNNING_HEADS,
        "footer": dm.Stream.RUNNING_HEADS,
        "page_number": dm.Stream.PAGE_NUMBERS,
        "aside_text": dm.Stream.ASIDES,
        "code": dm.Stream.CODE,
    }
    for block_type, stream in expected.items():
        assert dm.stream_of_type(block_type) is stream, block_type
    # An unknown type is reported, never silently dropped.
    assert dm.stream_of_type("brand_new_type") is dm.Stream.OTHER


# ---------------------------------------------------------------------------
# 2. Normalisers
# ---------------------------------------------------------------------------

def test_html_to_text_strips_markup_and_attribute_digits() -> None:
    html = ('<table><tr><td colspan="3">12.73</td>'
            '<td style="width:5%">0.982</td></tr>'
            "<tr><td>A&amp;B</td><td>&#39;q&#39;</td></tr></table>")
    text = dm.html_to_text(html)
    assert "12.73" in text and "0.982" in text
    assert "A&B" in text and "'q'" in text
    assert dm.count_digits(text) == 8, f"attribute digits leaked: {text!r}"
    assert "<" not in text and ">" not in text


def test_latex_to_text_keeps_variables_and_digits() -> None:
    text = dm.latex_to_text("$$ \\frac{12}{34} \\alpha x $$")
    assert dm.count_digits(text) == 4
    assert "frac" not in text and "alpha" not in text
    assert "x" in text


# ---------------------------------------------------------------------------
# 3. Census
# ---------------------------------------------------------------------------

def _block(block_type: str, **fields: object) -> dict:
    return {"type": block_type, **fields}


def _write_content_list(path: Path, blocks: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    return path


def test_census_counts_each_stream_separately(tmp_path: Path) -> None:
    path = _write_content_list(tmp_path / "auto" / "p1_content_list.json", [
        _block("text", text="Body sentence with 7 words here."),
        _block("table",
               table_body='<table><tr><td colspan="3">12.73</td>'
                          '<td>0.982</td></tr></table>',
               table_caption=["Table 1", "Results 2026"]),
        _block("equation", text="$$ \\frac{12}{34} \\alpha x $$"),
        _block("list", list_items=["item 1", "item 2 with 42"]),
        _block("image", img_path="images/f1.jpg",
               image_caption=["Figure 2", "Flow"]),
        _block("page_footnote", text="Funded by grant 2020-1234."),
    ])
    model = dm.parse_document(path)
    census = model.census()

    assert census[dm.Stream.PROSE].blocks == 1
    assert census[dm.Stream.PROSE].dropped_by_metrics is False
    # S-TBL-* read the tables stream (cross-review H2) - this used to assert True,
    # which made the product claim it never looked at them.
    assert census[dm.Stream.TABLES].dropped_by_metrics is False
    assert census[dm.Stream.TABLES].digits == 8, "cell digits only, never attributes"
    assert census[dm.Stream.TABLES].raw_chars > census[dm.Stream.TABLES].chars
    assert census[dm.Stream.TABLES].caption_digits == 5   # "Table 1" + "Results 2026"
    assert census[dm.Stream.EQUATIONS].digits == 4
    assert census[dm.Stream.EQUATIONS].raw_chars > census[dm.Stream.EQUATIONS].chars
    assert census[dm.Stream.LISTS].digits == 4
    assert census[dm.Stream.FIGURES].caption_digits == 1
    assert census[dm.Stream.FOOTNOTES].digits == 8
    assert [s for s, c in census.items() if not c.dropped_by_metrics] == [
        dm.Stream.FIGURES, dm.Stream.PROSE, dm.Stream.TABLES]


def test_census_is_recomputable_and_keeps_block_order(tmp_path: Path) -> None:
    path = _write_content_list(tmp_path / "auto" / "p1_content_list.json", [
        _block("text", text="First."),
        _block("table", table_body="<table><tr><td>1</td></tr></table>"),
        _block("text", text="Second."),
    ])
    first = dm.parse_document(path)
    second = dm.parse_document(path)
    assert first == second
    assert [b.kind for b in first.blocks] == [dm.Stream.PROSE, dm.Stream.TABLES,
                                             dm.Stream.PROSE]
    assert first.text(dm.Stream.PROSE) == "First.\nSecond."


# ---------------------------------------------------------------------------
# 4. Boundary parsing
# ---------------------------------------------------------------------------

def test_malformed_content_list_raises_a_typed_error(tmp_path: Path) -> None:
    broken = tmp_path / "auto" / "p1_content_list.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not a list", encoding="utf-8")
    with pytest.raises(dm.DocumentParseError):
        dm.parse_document(broken)

    not_a_list = tmp_path / "auto" / "p2_content_list.json"
    not_a_list.write_text('{"blocks": []}', encoding="utf-8")
    with pytest.raises(dm.DocumentParseError):
        dm.parse_document(not_a_list)


def test_deeply_nested_content_list_is_a_typed_error(tmp_path: Path) -> None:
    """A pathological depth must be a parse error, not a RecursionError escape."""
    nested = tmp_path / "auto" / "p1_content_list.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("[{\"type\": \"text\", \"text\": " + "[" * 2000 + "]" * 2000 + "}]",
                      encoding="utf-8")
    with pytest.raises(dm.DocumentParseError):
        dm.parse_document(nested)
