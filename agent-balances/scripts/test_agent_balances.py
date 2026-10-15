"""Tests for agent-balances CLI（由 dc-skill-creator 生成）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parent / "cli.py"


def test_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
