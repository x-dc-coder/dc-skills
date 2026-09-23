"""Tests for assets-doctor（V1-V12 验收的可自动化子集）。

跑法：cd ~/projects/dc-skills && uv run python -m pytest scripts/test_assets_doctor.py -q
（注：需 import 无扩展名脚本，用 importlib 按路径加载。）
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "scripts" / "assets-doctor"


def _load():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("assets_doctor", str(DOCTOR))
    spec = importlib.util.spec_from_loader("assets_doctor", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DOCTOR), *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=120)


class TestModes:
    def test_json_schema(self) -> None:
        r = run("--json")
        data = json.loads(r.stdout)
        assert set(data) >= {"counts", "items", "exit"}
        assert set(data["counts"]) >= {"ok", "warn", "crit"}
        for item in data["items"]:
            assert set(item) >= {"group", "level", "name", "msg"}

    def test_check_exit_semantics(self) -> None:
        """当前实机有 2 个真实 ⚠️（paper-reader 行数/dsh provider）→ --check 应退 1。"""
        r = run("--check")
        assert r.returncode == 1, r.stdout[-500:]

    def test_strict_is_not_worse_than_check(self) -> None:
        a, b = run("--check"), run("--check", "--strict")
        assert b.returncode >= a.returncode

    def test_only_limits_groups(self) -> None:
        r = run("--json", "--only", "landing")
        data = json.loads(r.stdout)
        assert {i["group"] for i in data["items"]} <= {"landing", "app_roots"}

    def test_offline_runs(self) -> None:
        r = run("--offline")
        assert r.returncode in (0, 1)
        assert "assets-doctor" in r.stdout


class TestNoKeyLeakage:
    def test_no_secrets_in_output(self) -> None:
        """V7：输出不得含任何 sk- 前缀 token 或 >20 字符的连贯密钥样字符串。"""
        import re
        for args in (["--json"], []):
            out = run(*args).stdout
            assert "sk-" not in out
            assert not re.search(r"[A-Za-z0-9]{32,}", out), "疑似密钥泄漏"


class TestParsers:
    def test_dsh_mcp_names_found(self) -> None:
        mod = _load()
        names = mod._dsh_mcp_names()
        assert {"codegraph", "vision", "browser"} <= names

    def test_claude_mcp_names_found(self) -> None:
        mod = _load()
        names = mod._claude_mcp_names()
        assert {"codegraph", "vision", "keenable"} <= names

    def test_http_reachable_whitelist(self) -> None:
        mod = _load()
        ok, code = mod._http_reachable("https://api.keenable.ai/mcp", 4, {200, 405, 401})
        assert isinstance(ok, bool) and code in (200, 401, 405, 0)


class TestReadOnly:
    def test_no_repo_mutation(self) -> None:
        before = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        run()
        after = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        assert before == after  # V10：巡检不得改动仓库
