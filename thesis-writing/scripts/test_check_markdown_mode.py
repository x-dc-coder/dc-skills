#!/usr/bin/env python3
"""Red-phase tests for check_markdown_spec.py --mode journal.

These tests assert behavior that does NOT exist yet (the --mode flag and
journal-specific rules). They are expected to FAIL until C3 implements
the feature (TDD red phase). After C3, all tests should pass (green phase).

Run:
    cd ~/projects/dc-skills && uv run pytest thesis-writing/scripts/test_check_markdown_mode.py -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKER = Path(__file__).parent / "check_markdown_spec.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_checker(md_content: str, mode: str = "journal", strict: bool = False,
                tmp_path: Path | None = None) -> tuple[int, str]:
    """Write md_content to a temp file, run the checker, return (exit_code, stdout)."""
    if tmp_path is None:
        raise RuntimeError("pytest tmp_path fixture required")
    md_file = tmp_path / "sample.md"
    md_file.write_text(md_content, encoding="utf-8")
    cmd = [sys.executable, str(CHECKER), "--md", str(md_file), "--mode", mode]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _codes(stdout: str) -> set[str]:
    """Extract finding codes from checker output like '[md-check] ERROR L5 CODE: msg'."""
    codes: set[str] = set()
    for line in stdout.splitlines():
        # Match: [md-check] ERROR L5 CODE: ...
        #        [md-check] WARN  L5 CODE: ...
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "[md-check]" and parts[1] in {"ERROR", "WARN"}:
            # parts[2] is like "L5", parts[3] is the code
            codes.add(parts[3].rstrip(":"))
    return codes


# ---------------------------------------------------------------------------
# Minimal valid journal-style fixtures
# ---------------------------------------------------------------------------

JOURNAL_ABSTRACT = """\
# Abstract

Local search is central to high-performing metaheuristic algorithms for vehicle routing. This paper proposes a tensor-based GPU acceleration framework. Extensive experiments demonstrate significant speedups over CPU baselines. The framework is extensible to a wide range of problem variants.

Keywords: tensor computation; GPU; vehicle routing; local search.
"""

JOURNAL_INTRO_CITED = """\
# 1 Introduction

The vehicle routing problem (VRP) [1] is a well-known combinatorial optimization problem. Several metaheuristic approaches have been proposed [2][3]. Recent GPU-based methods [4] show promising speedups. In this paper, we propose a tensor-based framework that addresses the limitations of prior work [5].
"""

JOURNAL_REFS = """\
# References

[1] Author A. Title one[J]. Journal One, 2020, 10(2): 1-20.

[2] Author B. Title two[J]. Journal Two, 2021, 11(3): 21-40.

[3] Author C. Title three[J]. Journal Three, 2022, 12(4): 41-60.

[4] Author D. Title four[J]. Journal Four, 2023, 13(5): 61-80.

[5] Author E. Title five[J]. Journal Five, 2024, 14(6): 81-100.
"""

VALID_JOURNAL_PAPER = f"""\
{JOURNAL_ABSTRACT}
{JOURNAL_INTRO_CITED}
# 2 Method

We introduce the attribute-based solution tensor representation. The objective function is shown below.

$$f(S) = \\mu_1 \\cdot M + \\mu_2 \\cdot D(S) \\tag{{2-1}}$$

Subject to the capacity constraint $q_{{n_{{i,j}}}} \\leq Q$ for all visited nodes.

# 3 Conclusion

This paper presented a tensor-based GPU acceleration framework. Experiments confirm substantial speedups.
{JOURNAL_REFS}
"""


# ---------------------------------------------------------------------------
# Test 1: --mode flag is accepted (currently fails: unrecognized arg)
# ---------------------------------------------------------------------------

def test_mode_flag_accepted(tmp_path: Path) -> None:
    """The --mode flag must be accepted by argparse without error."""
    code, out = run_checker(VALID_JOURNAL_PAPER, mode="journal", tmp_path=tmp_path)
    # Should exit 0 (PASS) — not crash with "unrecognized arguments"
    assert "unrecognized arguments" not in out, f"--mode flag not accepted: {out}"
    assert code == 0, f"Valid journal paper should PASS, got code {code}: {out}"


# ---------------------------------------------------------------------------
# Test 2: Default mode is undergraduate (backward compat)
# ---------------------------------------------------------------------------

def test_default_mode_is_undergraduate(tmp_path: Path) -> None:
    """Without --mode, behavior must equal --mode undergraduate (backward compatible)."""
    md = VALID_JOURNAL_PAPER
    md_file = tmp_path / "x.md"
    md_file.write_text(md, encoding="utf-8")
    # Run without --mode
    cmd = [sys.executable, str(CHECKER), "--md", str(md_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert "unrecognized arguments" not in proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Test 3: --mode journal accepts English-only special headings
# ---------------------------------------------------------------------------

def test_journal_accepts_english_special_headings(tmp_path: Path) -> None:
    """Journal mode must accept 'Abstract', 'References' as level-1 special headings
    (current SPECIAL_HEADINGS already includes them, but journal mode should also
    accept 'Keywords', 'Acknowledgments', 'Introduction' etc. without numbering errors)."""
    code, out = run_checker(VALID_JOURNAL_PAPER, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    # Abstract and References must NOT trigger SPECIAL_HEADING_LEVEL or numbering errors
    assert "SPECIAL_HEADING_LEVEL" not in codes, \
        f"Abstract/References wrongly flagged in journal mode: {out}"


# ---------------------------------------------------------------------------
# Test 4: Citation density warning — long uncited paragraph
# ---------------------------------------------------------------------------

def test_citation_density_warning(tmp_path: Path) -> None:
    """Journal mode should WARN when a section > 500 words has zero citations."""
    long_uncited_intro = """\
# 1 Introduction

""" + "This is a sentence about vehicle routing. " * 100  # ~700 words, no citation

    md = f"""\
# Abstract

Short abstract about routing.

{long_uncited_intro}
# 2 Method

Method content with a citation [1].

# References

[1] Author. Title[J]. Journal, 2020.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_DENSITY_LOW" in codes, \
        f"Expected CITATION_DENSITY_LOW warning for uncited long section, got codes: {codes}\n{out}"


def test_citation_density_no_warning_when_cited(tmp_path: Path) -> None:
    """Conversely, a cited long section should NOT trigger the density warning."""
    cited_intro = """\
# 1 Introduction

""" + "The vehicle routing problem is important [1]. " * 40  # ~280 words with citations

    md = f"""\
# Abstract

Short abstract.

{cited_intro}
# 2 Method

Content [2].

# References

[1] A. Title[J]. J, 2020.

[2] B. Title[J]. J, 2021.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_DENSITY_LOW" not in codes, \
        f"Cited section should not trigger density warning, got: {codes}\n{out}"


# ---------------------------------------------------------------------------
# Test 5: Undergrad mode does NOT enforce citation density (backward compat)
# ---------------------------------------------------------------------------

def test_undergrad_mode_no_citation_density_check(tmp_path: Path) -> None:
    """Undergraduate mode must NOT run citation-density checks."""
    long_uncited = """\
# 1 绪论

""" + "车辆路径问题是一个经典的组合优化问题。" * 100  # long uncited Chinese

    md = f"""\
# 摘要

摘要内容。

{long_uncited}
# 参考文献

[1] 作者. 题名[J]. 刊名, 2020.
"""
    code, out = run_checker(md, mode="undergraduate", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_DENSITY_LOW" not in codes, \
        f"Undergrad mode should not check citation density, got: {codes}\n{out}"


# ---------------------------------------------------------------------------
# Test 6: Invalid --mode value is rejected by argparse
# ---------------------------------------------------------------------------

def test_invalid_mode_rejected(tmp_path: Path) -> None:
    """An invalid --mode value must be rejected by argparse."""
    md_file = tmp_path / "x.md"
    md_file.write_text("# Abstract\n\nText.\n", encoding="utf-8")
    cmd = [sys.executable, str(CHECKER), "--md", str(md_file), "--mode", "bogus"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr or "invalid choice" in proc.stdout

# ---------------------------------------------------------------------------
# issue #21: 正文数字引用 ↔ 文献表条目号 双向核验
# ---------------------------------------------------------------------------

def test_dangling_citation_reported(tmp_path: Path) -> None:
    """正文引用 [42] 而文献表只有 1..2 → DANGLING_CITATION（ERROR）。"""
    md = """# Abstract

We propose a method [1] and cite a missing entry [42].

# References

[1] Author A. One[J]. J, 2020.

[2] Author B. Two[J]. J, 2021.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "DANGLING_CITATION" in codes, f"Expected DANGLING_CITATION, got: {codes}\n{out}"
    assert "42" in out


def test_uncited_reference_warned(tmp_path: Path) -> None:
    """文献表有 [1][2]，但正文只引用 [1] → UNCITED_REFERENCE（WARN）。"""
    md = """# Abstract

We cite [1].

# References

[1] Author A. One[J]. J, 2020.

[2] Author B. Two[J]. J, 2021.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "UNCITED_REFERENCE" in codes, f"Expected UNCITED_REFERENCE, got: {codes}\n{out}"
    assert "DANGLING_CITATION" not in codes, f"Unexpected DANGLING_CITATION: {out}"


def test_math_interval_not_a_citation(tmp_path: Path) -> None:
    """[0,1] 是数学区间，不算引用（不报 dangling）。"""
    md = """# Abstract

The closed interval [0,1] is common in this domain.

# References

[1] Author A. One[J]. J, 2020.

[2] Author B. Two[J]. J, 2021.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "DANGLING_CITATION" not in codes, f"[0,1] 不应触发 DANGLING_CITATION: {codes}\n{out}"


def test_range_within_list_is_valid(tmp_path: Path) -> None:
    """[3-5] 在 N=5 时是合法引用（无 dangling / uncited）。"""
    md = """# Abstract

See [1][2] and [3-5].

# References

[1] A[J]. J, 2020.

[2] B[J]. J, 2021.

[3] C[J]. J, 2022.

[4] D[J]. J, 2023.

[5] E[J]. J, 2024.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "DANGLING_CITATION" not in codes, f"[3-5]@N=5 不应 dangling: {codes}\n{out}"
    assert "UNCITED_REFERENCE" not in codes, f"[3-5]@N=5 不应 uncited: {codes}\n{out}"


def test_range_out_of_bounds_is_dangling(tmp_path: Path) -> None:
    """[3-5] 在 N=3 时越界 → 4、5 报 DANGLING_CITATION。"""
    md = """# Abstract

See [3-5].

# References

[1] A[J]. J, 2020.

[2] B[J]. J, 2021.

[3] C[J]. J, 2022.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "DANGLING_CITATION" in codes, f"[3-5]@N=3 应报 dangling: {codes}\n{out}"


def test_author_year_style_skipped(tmp_path: Path) -> None:
    """作者-年份制草稿跳过数字交叉核验（不报假的 100% 未引用）。"""
    md = """# Abstract

Smith (2020) proposed a method. See also Jones and Brown (2019).

# References

[1] Author A. One[J]. J, 2020.

[2] Author B. Two[J]. J, 2021.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_STYLE_NOT_NUMERIC" in out, f"应给出 CITATION_STYLE_NOT_NUMERIC 说明: {out}"
    assert "DANGLING_CITATION" not in codes, f"作者-年份制不应报 dangling: {codes}\n{out}"
    assert "UNCITED_REFERENCE" not in codes, f"作者-年份制不应报 uncited: {codes}\n{out}"


def test_no_reference_section_skipped(tmp_path: Path) -> None:
    """无 References 段时跳过交叉核验并说明原因，不判失败。"""
    md = """# Abstract

We propose a method [1].
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_CROSSLINK_SKIPPED" in out, f"应说明跳过原因: {out}"
    assert "DANGLING_CITATION" not in codes, f"无文献表不应报 dangling: {codes}\n{out}"
    assert "UNCITED_REFERENCE" not in codes, f"无文献表不应报 uncited: {codes}\n{out}"


def test_unparseable_reference_entries_skipped(tmp_path: Path) -> None:
    """References 段存在但条目不是 [N] 格式 → 跳过并说明，不判失败。"""
    md = """# Abstract

We propose a method [1].

# References

Smith, J. (2020). Title one. Journal.

Jones, K. (2021). Title two. Journal.
"""
    code, out = run_checker(md, mode="journal", tmp_path=tmp_path)
    codes = _codes(out)
    assert "CITATION_CROSSLINK_SKIPPED" in out, f"应说明跳过原因: {out}"
    assert "DANGLING_CITATION" not in codes, f"条目不可识别不应报 dangling: {codes}\n{out}"
    assert "UNCITED_REFERENCE" not in codes, f"条目不可识别不应报 uncited: {codes}\n{out}"

