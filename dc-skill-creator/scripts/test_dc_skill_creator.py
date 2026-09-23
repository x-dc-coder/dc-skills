"""Tests for dc-skill-creator（validate / new_skill / check_triggers）。

跑法：cd ~/projects/dc-skills && uv run python -m pytest dc-skill-creator/scripts -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent.parent


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=ROOT)


class TestValidate:
    def test_self_passes(self) -> None:
        """dogfooding：dc-skill-creator 自身必须全过（验收清单第 0 条）。"""
        r = run(SCRIPTS / "validate.py", "dc-skill-creator")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_registered_skill_passes(self) -> None:
        r = run(SCRIPTS / "validate.py", "drawio-xml")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_bad_name_dir_mismatch_is_error(self, tmp_path: Path) -> None:
        """name != 目录名 → R2 E 级 → 退出码 2。"""
        d = tmp_path / "wrong-dir"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: other-name\ndescription: 做 X。当用户需要 Y 时使用。\n---\n\n# X\n",
            encoding="utf-8")
        r = run(SCRIPTS / "validate.py", d)
        assert r.returncode == 2
        assert "R2" in r.stdout

    def test_missing_registration_is_error(self, tmp_path: Path) -> None:
        """未登记 agent-map → R8 E 级。"""
        d = tmp_path / "orphan-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: orphan-skill\ndescription: 做 X。当用户需要 Y 时使用。\n---\n\n# X\n",
            encoding="utf-8")
        r = run(SCRIPTS / "validate.py", d)
        assert r.returncode == 2
        assert "R8" in r.stdout

    def test_json_output_schema(self) -> None:
        import json
        r = run(SCRIPTS / "validate.py", "dc-skill-creator", "--json")
        data = json.loads(r.stdout)
        assert data["skill"] == "dc-skill-creator"
        assert data["errors"] == [] and data["exit"] == 0


class TestNewSkill:
    def test_dry_run_writes_nothing(self) -> None:
        before = {p.name for p in ROOT.iterdir()}
        r = run(SCRIPTS / "new_skill.py", "probe-skill-xyz",
                "--desc", "探测用。当用户说 probe 时使用。", "--env", "C")
        assert r.returncode == 0
        assert not (ROOT / "probe-skill-xyz").exists()
        assert {p.name for p in ROOT.iterdir()} == before
        assert "dry-run" in r.stdout

    def test_invalid_name_rejected(self) -> None:
        r = run(SCRIPTS / "new_skill.py", "Bad_Name", "--desc", "x。当用户需要 Y 时使用。")
        assert r.returncode != 0

    def test_desc_too_long_rejected(self) -> None:
        r = run(SCRIPTS / "new_skill.py", "ok-name", "--desc", "x" * 1025)
        assert r.returncode != 0


class TestCheckTriggers:
    def test_high_overlap_exits_1(self) -> None:
        """候选 ≈ codegraph-explore 现有描述 → J≥0.30 → 退出码 1。"""
        r = run(SCRIPTS / "check_triggers.py",
                "用 CodeGraph 做代码库符号检索、调用链追踪和变更影响分析。理解大仓代码、"
                "定位定义或调用者、评估重构影响、选择测试时使用")
        assert r.returncode == 1, r.stdout
        assert "codegraph-explore" in r.stdout

    def test_unique_desc_exits_0(self) -> None:
        r = run(SCRIPTS / "check_triggers.py", "量子隧穿效应可视化 弦理论 科普 动画")
        assert r.returncode == 0
