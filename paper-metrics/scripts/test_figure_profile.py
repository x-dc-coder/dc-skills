#!/usr/bin/env python3
"""Tests for figure_profile — Figure Inventory + Figure Profile JSON (issue #1 MVP).

Written against the contract in the task + references/figure-profile-schema.md:
  * stable figure_id (same input -> same id, byte-identical output across runs);
  * width/height/aspect_ratio/width_usable_for_print read from PNG/JPEG/GIF headers;
  * unreadable files are listed and kept OUT of the adequacy denominator;
  * visual/semantic fields are null + status NOT_IMPLEMENTED placeholders;
  * duplicate img_path counting/naming rule (one block = one entry, unique id);
  * section + type_guess are INFERRED with confidence + basis (not vision).

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_figure_profile.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import figure_profile as fp


# ---------------------------------------------------------------------------
# Minimal image file headers (PNG / JPEG / GIF), pure stdlib
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


def _block(block_type: str, **fields: object) -> dict:
    return {"type": block_type, **fields}


def _write_corpus(tmp_path: Path, blocks: list[dict],
                  images: dict[str, bytes] | None = None,
                  paper_name: str = "Paper One") -> Path:
    """Build a corpus dir with one paper and one content_list.json under mineru/."""
    auto = tmp_path / paper_name / "mineru" / "p1" / "auto"
    images_dir = auto / "images"
    images_dir.mkdir(parents=True)
    for img_name, data in (images or {}).items():
        (images_dir / img_name).write_bytes(data)
    cl = auto / "p1_content_list.json"
    cl.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _figures(profile: dict) -> list[dict]:
    return [f for f in profile["figures"] if isinstance(f, dict)]


def _collect(tmp_path: Path) -> tuple[dict, dict]:
    out = tmp_path / "out"
    written = fp.write_products(tmp_path, out)
    return (json.loads(written["profile"].read_text(encoding="utf-8")),
            json.loads(written["summary"].read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. Stable figure_id + byte-identical determinism
# ---------------------------------------------------------------------------

def test_stable_figure_id_and_byte_identical_output(tmp_path: Path) -> None:
    """Same input -> same figure_id, and two runs are byte-identical."""
    _write_corpus(tmp_path, [
        _block("text", text="Introduction", text_level=2),
        _block("image", img_path="images/f1.png",
               image_caption=["Figure 1", "Overview"], page_idx=0),
        _block("chart", img_path="images/f2.png",
               chart_caption=["Figure 2: Results"], page_idx=2),
    ], images={"f1.png": _png_bytes(900, 600), "f2.png": _png_bytes(500, 400)})

    profile, _ = _collect(tmp_path)
    figures = _figures(profile)
    assert [f["figure_id"] for f in figures] == [
        "fig-paper-one-0-1", "fig-paper-one-2-2"]

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    fp.write_products(tmp_path, out1)
    fp.write_products(tmp_path, out2)
    for name in ("_figure_profile.json", "_figure_summary.json"):
        b1 = (out1 / name).read_bytes()
        b2 = (out2 / name).read_bytes()
        assert b1 == b2, f"{name} differs across runs"
        # No timestamp / no absolute path: the JSON must not embed the tmp path.
        assert str(tmp_path).encode() not in b1


def test_no_absolute_path_in_output(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/f.png", image_caption=["Figure 1"],
               page_idx=0),
    ], images={"f.png": _png_bytes(900, 600)})
    profile, _ = _collect(tmp_path)
    figure = _figures(profile)[0]
    # image_rel_path is corpus-relative, not absolute.
    assert not Path(figure["image_rel_path"]).is_absolute()
    assert figure["image_rel_path"].endswith("mineru/p1/auto/images/f.png")


# ---------------------------------------------------------------------------
# 2. Header-derived width / height / aspect_ratio / print usability
# ---------------------------------------------------------------------------

def test_header_dimensions_and_print_usability(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/big.png",
               image_caption=["Figure 1"], page_idx=0),
        _block("image", img_path="images/small.jpg",
               image_caption=["Figure 2"], page_idx=0),
        _block("image", img_path="images/tiny.gif",
               image_caption=["Figure 3"], page_idx=0),
    ], images={"big.png": _png_bytes(1200, 800),
               "small.jpg": _jpeg_bytes(400, 300),
               "tiny.gif": _gif_bytes(100, 100)})
    profile, _ = _collect(tmp_path)
    by_path = {f["image_path"]: f for f in _figures(profile)}
    assert by_path["images/big.png"]["width"] == 1200
    assert by_path["images/big.png"]["height"] == 800
    assert by_path["images/big.png"]["aspect_ratio"] == pytest.approx(1.5)
    assert by_path["images/big.png"]["width_usable_for_print"] is True
    assert by_path["images/small.jpg"]["width"] == 400
    assert by_path["images/small.jpg"]["aspect_ratio"] == pytest.approx(
        400 / 300, abs=1e-4)
    assert by_path["images/small.jpg"]["width_usable_for_print"] is False
    assert by_path["images/tiny.gif"]["width_usable_for_print"] is False


# ---------------------------------------------------------------------------
# 3. Unreadable images are listed and kept OUT of the adequacy denominator
# ---------------------------------------------------------------------------

def test_unreadable_images_not_in_denominator(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/big.png",
               image_caption=["Figure 1"], page_idx=0),
        _block("image", img_path="images/small.jpg",
               image_caption=["Figure 2"], page_idx=0),
        _block("image", img_path="images/missing.png",
               image_caption=["Figure 3"], page_idx=0),
        _block("image", img_path="images/bad.webp",
               image_caption=["Figure 4"], page_idx=0),
    ], images={"big.png": _png_bytes(1200, 800),
               "small.jpg": _jpeg_bytes(400, 300),
               "bad.webp": b"RIFF\x00\x00\x00\x00WEBP-not-raster"})
    _, summary = _collect(tmp_path)

    assert summary["n_figures"] == 4
    assert summary["n_measured"] == 2          # big + small only
    assert summary["n_unreadable"] == 2        # missing + webp
    assert summary["adequacy"]["adequate"] == 1
    assert summary["adequacy"]["measured"] == 2
    assert summary["adequacy"]["rate"] == pytest.approx(0.5)
    reasons = {row["image_rel_path"].split("/")[-1]: row["reason"]
               for row in summary["unreadable"]}
    assert reasons == {"missing.png": "missing_file",
                       "bad.webp": "unreadable_header"}


# ---------------------------------------------------------------------------
# 4. Visual / semantic fields are NOT_IMPLEMENTED placeholders
# ---------------------------------------------------------------------------

def test_visual_fields_are_not_implemented_placeholders(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/f.png", image_caption=["Figure 1"],
               page_idx=0),
    ], images={"f.png": _png_bytes(900, 600)})
    profile, _ = _collect(tmp_path)
    figure = _figures(profile)[0]
    expected = {"chart_type", "panel_count", "has_axis", "has_legend",
                "colors", "fonts", "line_width", "caption_semantic_role"}
    ni = figure["not_implemented"]
    assert set(ni.keys()) == expected
    for key in expected:
        assert ni[key] == {"value": None, "status": "NOT_IMPLEMENTED"}


# ---------------------------------------------------------------------------
# 5. Duplicate img_path: one block = one entry, unique id, occurrence indices
# ---------------------------------------------------------------------------

def test_duplicate_img_path_counting_and_naming(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/dup.png",
               image_caption=["Figure 1"], page_idx=0),
        _block("chart", img_path="images/dup.png",
               chart_caption=["Figure 1 (sub-panel)"], page_idx=0),
    ], images={"dup.png": _png_bytes(900, 600)})
    profile, summary = _collect(tmp_path)
    figures = _figures(profile)
    assert summary["n_figures"] == 2
    assert summary["n_unique_images"] == 1
    # Two entries, distinct ids (block_index is unique), occurrence [1, 2] of 2.
    assert [f["figure_id"] for f in figures] == [
        "fig-paper-one-0-0", "fig-paper-one-0-1"]
    assert [f["img_path_occurrence_index"] for f in figures] == [1, 2]
    assert [f["img_path_occurrence_total"] for f in figures] == [2, 2]


# ---------------------------------------------------------------------------
# 6. section + type_guess are INFERRED (confidence + basis), not vision
# ---------------------------------------------------------------------------

def test_section_and_type_guess_are_inferred(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("text", text="Methodology", text_level=2),
        _block("image", img_path="images/arch.png",
               image_caption=["Figure 1: The proposed framework"], page_idx=0),
        _block("text", text="Experiments", text_level=2),
        _block("chart", img_path="images/plot.png",
               chart_caption=["Figure 2: Convergence curves"], page_idx=1),
    ], images={"arch.png": _png_bytes(900, 600),
               "plot.png": _png_bytes(500, 400)})
    profile, _ = _collect(tmp_path)
    arch, plot = _figures(profile)
    assert arch["section"]["value"] == "method"
    assert arch["section"]["confidence"] == "high"
    assert arch["section"]["status"] == "INFERRED"
    assert arch["type_guess"]["value"] == "framework_architecture"
    assert arch["type_guess"]["status"] == "INFERRED"
    assert "视觉" in arch["type_guess"]["basis"]  # explicitly "not vision"
    assert plot["section"]["value"] == "experiments"
    assert plot["type_guess"]["value"] == "data_plot"


def test_section_confidence_tiers(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("text", text="Methodology", text_level=2),
        _block("image", img_path="images/a.png", image_caption=["A"], page_idx=0),
        _block("text", text="4.2 Comparison with state-of-the-art", text_level=2),
        _block("image", img_path="images/b.png", image_caption=["B"], page_idx=1),
        _block("text", text="Custom Unmapped Section Title", text_level=2),
        _block("image", img_path="images/c.png", image_caption=["C"], page_idx=2),
    ], images={"a.png": _png_bytes(900, 600), "b.png": _png_bytes(900, 600),
               "c.png": _png_bytes(900, 600)})
    profile, _ = _collect(tmp_path)
    a, b, c = _figures(profile)
    assert a["section"]["confidence"] == "high"      # exact canonical hit
    assert b["section"]["confidence"] == "medium"    # keyword hit
    assert b["section"]["value"] == "experiments"
    assert c["section"]["confidence"] == "low"       # verbatim fallback


def test_figure_before_any_heading_is_front_matter(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/f.png", image_caption=["Figure 1"],
               page_idx=0),
    ], images={"f.png": _png_bytes(900, 600)})
    profile, _ = _collect(tmp_path)
    figure = _figures(profile)[0]
    assert figure["section"]["value"] == "front_matter"
    assert figure["section"]["confidence"] == "low"


# ---------------------------------------------------------------------------
# 7. Caption fields (chars + has-number)
# ---------------------------------------------------------------------------

def test_caption_fields(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/a.png",
               image_caption=["Fig. 1: The model."], page_idx=0),
        _block("image", img_path="images/b.png",
               image_caption=["Overview of the system"], page_idx=1),
        _block("image", img_path="images/c.png", image_caption=[], page_idx=2),
    ], images={"a.png": _png_bytes(900, 600), "b.png": _png_bytes(900, 600),
               "c.png": _png_bytes(900, 600)})
    profile, _ = _collect(tmp_path)
    a, b, c = _figures(profile)
    assert a["caption"] == "Fig. 1: The model."
    assert a["caption_chars"] == len("Fig. 1: The model.")
    assert a["caption_has_number"] is True
    assert b["caption_has_number"] is False
    assert c["caption"] == "" and c["caption_chars"] == 0
    assert c["caption_has_number"] is False


# ---------------------------------------------------------------------------
# 8. Print threshold locks to the S-SIZ-04 value (800 px)
# ---------------------------------------------------------------------------

def test_width_threshold_matches_s_siz_04() -> None:
    """The figure-profile print cut and S-SIZ-04 share the same 800 px value."""
    assert fp._MIN_IMAGE_WIDTH == 800


# ---------------------------------------------------------------------------
# 9. CLI exit code and output files
# ---------------------------------------------------------------------------

def test_cli_writes_both_products_and_returns_zero(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [
        _block("image", img_path="images/f.png", image_caption=["Figure 1"],
               page_idx=0),
    ], images={"f.png": _png_bytes(900, 600)})
    out = tmp_path / "out"
    assert fp.main(["--corpus", str(tmp_path), "--out", str(out)]) == 0
    assert (out / "_figure_profile.json").is_file()
    assert (out / "_figure_summary.json").is_file()


def test_cli_errors_when_corpus_missing(tmp_path: Path) -> None:
    assert fp.main(["--corpus", str(tmp_path / "nope"), "--out",
                    str(tmp_path / "out")]) == 1


def test_svg_image_size_and_subfigures(tmp_path: Path) -> None:
    svg_content = b'<svg viewBox="0 0 100 50" xmlns="http://www.w3.org/2000/svg"></svg>'
    _write_corpus(tmp_path, [
        _block("image", img_path="images/diag.svg", image_caption=["Figure 1: (a) overview, (b) details"],
               page_idx=0),
    ], images={"diag.svg": svg_content})
    profile, _ = _collect(tmp_path)
    figs = _figures(profile)
    assert len(figs) == 1
    f0 = figs[0]
    assert f0["readable"] is True
    assert f0["width_usable_for_print"] is True
    assert f0["has_subfigures"] is True
    assert f0["subfigures"] == ["a", "b"]
