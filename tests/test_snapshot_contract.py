"""tests/test_snapshot_contract.py — 全机技能资产快照契约与 HTML 驾驶舱测试。

确保：
  1. 快照 Schema 格式（schema_version: 1）严格满足 monitor-panel 的对接要求；
  2. 原子落盘写入无残留且内容完备；
  3. 离线 HTML 仪表盘自包含、无外部网络依赖，包含核心 31 技能和 5 大 Agent 矩阵；
  4. assets-doctor 的 --export-snapshot 与 --html 参数行为稳定无回归。
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from snapshot import (
    build_snapshot,
    export_snapshot,
    render_html_dashboard,
    DEFAULT_SNAPSHOT_PATH,
    DEFAULT_HTML_PATH,
)


class DummyReport:
    """模拟 assets-doctor 的 Report 实例。"""
    def __init__(self, items=None, counts_dict=None):
        self.items = items or [
            {"group": "contract", "level": "warn", "name": "md-to-thesis-latex", "msg": "SKILL.md 401 行 > 400 门禁", "hint": ""},
            {"group": "farm", "level": "ok", "name": "farm", "msg": "农场零漂移", "hint": ""},
        ]
        self._counts = counts_dict or {"crit": 0, "warn": 1, "ok": 20}

    def counts(self):
        return self._counts

    def by(self, group):
        return [i for i in self.items if i.get("group") == group]


class TestSnapshotContract:
    def test_build_snapshot_schema_and_fields(self):
        rep = DummyReport()
        doc = build_snapshot(rep, started=0.0, offline=True)

        assert doc["schema_version"] == 1
        meta = doc["meta"]
        assert meta["producer"] == "assets-doctor"
        assert meta["offline"] is True
        assert "generated_at" in meta
        assert "duration_ms" in meta
        assert "master_commit" in meta

        summary = doc["summary"]
        assert summary["skills_total"] == 32
        assert summary["families_total"] == 7
        assert set(summary["agents"]) == {"claude-code", "grok", "dsh", "zcode", "codex"}
        assert summary["load_modes"]["auto"] == 10
        assert summary["load_modes"]["manual"] == 22
        assert summary["roles"]["entry"] == 7
        assert summary["roles"]["member"] == 18
        assert summary["roles"]["standalone"] == 7
        assert summary["doctor"]["warn"] == 1

        skills = doc["skills"]
        assert len(skills) == 32

        skill_map = {s["name"]: s for s in skills}
        assert "proxy-ops" in skill_map
        p = skill_map["proxy-ops"]
        assert p["role"] == "standalone"
        assert p["family"] is None
        assert p["load_mode"] == "manual"
        assert set(p["mounts"].keys()) == {"claude-code", "grok", "dsh", "zcode", "codex"}
        assert p["status"] == "ok"

        # 检查带警告的技能状态映射
        assert "md-to-thesis-latex" in skill_map
        o = skill_map["md-to-thesis-latex"]
        assert o["status"] == "warn"
        assert "400" in o["status_hint"]

    def test_export_snapshot_atomic(self, tmp_path):
        rep = DummyReport()
        doc = build_snapshot(rep, started=0.0, offline=True)

        target = tmp_path / "sub" / "skills-snapshot.json"
        exported = export_snapshot(doc, target)

        assert exported == target
        assert target.is_file()
        assert not list(tmp_path.glob("*.tmp.*"))

        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == 1
        assert loaded["summary"]["skills_total"] == 32

    def test_render_html_dashboard_offline(self, tmp_path):
        rep = DummyReport()
        doc = build_snapshot(rep, started=0.0, offline=True)

        target = tmp_path / "skills-dashboard.html"
        rendered = render_html_dashboard(doc, target)

        assert rendered == target
        assert target.is_file()

        content = target.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "dc-skills · 技能农场驾驶舱" in content
        assert "proxy-ops" in content
        assert "programming" in content
        # 验证完全离线，无外部 CDN 脚本链接
        assert 'src="http' not in content
        assert 'href="http' not in content

    def test_assets_doctor_render_with_snapshot_and_html(self, assets_doctor, tmp_path):
        rep = DummyReport()
        import argparse
        snap_file = tmp_path / "custom-snap.json"
        html_file = tmp_path / "custom-dash.html"

        args = argparse.Namespace(
            check=True,
            strict=False,
            json=False,
            only=None,
            offline=True,
            deep=False,
            export_snapshot=True,
            snapshot_path=str(snap_file),
            html=True,
            html_path=str(html_file),
        )

        code = assets_doctor.render(rep, args, started=0.0)
        assert code == 1  # DummyReport 有 1 个 warn，--check 下返回 1
        assert snap_file.is_file()
        assert html_file.is_file()

        data = json.loads(snap_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert "dc-skills · 技能农场驾驶舱" in html_file.read_text(encoding="utf-8")
