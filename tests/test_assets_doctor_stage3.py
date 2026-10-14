"""tests/test_assets_doctor_stage3.py — assets-doctor 阶段三新增重构特性的单元测试。

覆盖需求：
  1. Standalone 三标记断言（在 check_farm 中检查 manual 三标记与 auto 无残留）。
  2. 族群依赖闭包断言（requires.cli ⊆ registry.cli, requires.mcp ⊆ agent-map.mcps）。
  3. Bundled 技能三桶对账（结合 ~/.grok/config.toml [skills] disabled 检查状态）。
  4. MCP 工具与标记块断言（~/.grok/config.toml 标记块单侧缺失判 crit；tool_count_baseline 检查）。
  5. 统一退出码语义（0 通过 / 1 警告与降级 / 2 严重契约断裂 / 3 运行期异常）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pytest
import yaml


class TestAssetsDoctorExitCodes:
    def test_exit_codes_semantic(self, assets_doctor):
        """测试退出码语义：
          - 0: 全部通过
          - 1: 存在 ⚠️ 警告或降级（在 --check 或 --strict 下）
          - 2: 存在 🚨 严重契约断裂
        """
        rep = assets_doctor.Report()
        rep.add("cli", "ok", "test-item", "All good")

        args_check = argparse.Namespace(check=True, strict=False, json=False, only=None, offline=True, deep=False)
        assert assets_doctor.render(rep, args_check, 0.0) == 0

        # 加入 warn
        rep.add("cli", "warn", "warn-item", "Warning message")
        assert assets_doctor.render(rep, args_check, 0.0) == 1

        # 非 --check 非 --strict 下，警告退出码仍为 0
        args_no_check = argparse.Namespace(check=False, strict=False, json=False, only=None, offline=True, deep=False)
        assert assets_doctor.render(rep, args_no_check, 0.0) == 0

        # 加入 crit
        rep.add("farm", "crit", "crit-item", "Critical breach")
        assert assets_doctor.render(rep, args_check, 0.0) == 2
        assert assets_doctor.render(rep, args_no_check, 0.0) == 2


class TestAssetsDoctorFarmAndRequiresAssertions:
    def test_requires_closure_cli_and_mcp_violations(self, assets_doctor, monkeypatch, tmp_path):
        """测试族群依赖闭包断言：
        families.<name>.requires.cli ⊆ registry.cli
        families.<name>.requires.mcp ⊆ agent-map.mcps
        """
        fake_agent_map = tmp_path / "agent-map.yaml"
        fake_registry = tmp_path / "registry.yaml"

        agent_map_data = {
            "families": {
                "demo_family": {
                    "entry": "demo-entry",
                    "members": [],
                    "requires": {
                        "cli": ["unregistered-cli-tool"],
                        "mcp": ["unmanaged-mcp-server"],
                    },
                }
            },
            "standalone": {},
            "mcps": {
                "managed-mcp": {"transport": "stdio"}
            },
        }
        registry_data = {
            "cli": [
                {"name": "git", "bin": "git"},
                {"name": "gh", "bin": "gh"},
            ]
        }

        fake_agent_map.write_text(yaml.dump(agent_map_data), encoding="utf-8")
        fake_registry.write_text(yaml.dump(registry_data), encoding="utf-8")

        monkeypatch.setattr(assets_doctor, "AGENT_MAP", fake_agent_map)
        monkeypatch.setattr(assets_doctor, "REGISTRY", fake_registry)
        monkeypatch.setattr(assets_doctor, "run_cmd", lambda args, timeout=60: (0, "[families] 0 族 / 0 成员标记一致\n"))

        rep = assets_doctor.Report()
        assets_doctor.check_farm(rep)

        crit_items = [i for i in rep.by("farm") if i["level"] == "crit"]
        crit_msgs = [i["msg"] for i in crit_items]
        assert any("requires.cli 存在未登记 CLI" in m and "unregistered-cli-tool" in m for m in crit_msgs)
        assert any("requires.mcp 存在未纳管 MCP" in m and "unmanaged-mcp-server" in m for m in crit_msgs)

    def test_standalone_manual_missing_flags_assertion(self, assets_doctor, monkeypatch, tmp_path):
        """测试 check_farm 中捕获 standalone manual 缺少三标记与非法残留 family。"""
        fake_agent_map = tmp_path / "agent-map.yaml"
        fake_registry = tmp_path / "registry.yaml"

        agent_map_data = {
            "families": {},
            "standalone": {
                "test-standalone": {"load": "manual"}
            },
        }
        fake_agent_map.write_text(yaml.dump(agent_map_data), encoding="utf-8")
        fake_registry.write_text(yaml.dump({"cli": []}), encoding="utf-8")

        skill_dir = tmp_path / "test-standalone"
        skill_dir.mkdir(parents=True)
        # 错误配置：带 family，缺 dmi，无 openai.yaml
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-standalone\nmetadata:\n  family: wrong\n  load-mode: auto\n---\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(assets_doctor, "AGENT_MAP", fake_agent_map)
        monkeypatch.setattr(assets_doctor, "REGISTRY", fake_registry)
        monkeypatch.setattr(assets_doctor, "ROOT", tmp_path)
        monkeypatch.setattr(assets_doctor, "run_cmd", lambda args, timeout=60: (0, "[families] 一致\n"))

        rep = assets_doctor.Report()
        assets_doctor.check_farm(rep)

        crit_items = [i for i in rep.by("farm") if i["level"] == "crit"]
        crit_msgs = [i["msg"] for i in crit_items]
        assert any("不应包含 metadata.family" in m for m in crit_msgs)
        assert any("缺 metadata.load-mode: manual" in m for m in crit_msgs)
        assert any("缺 disable-model-invocation: true" in m for m in crit_msgs)
        assert any("缺 agents/openai.yaml" in m for m in crit_msgs)


class TestAssetsDoctorMcpAndBundled:
    def test_grok_mcp_marker_broken_alarm(self, assets_doctor, monkeypatch, tmp_path):
        """测试 check_mcp 中检查 Grok config.toml 标记块单侧缺失时报 crit。"""
        fake_home = tmp_path / "home"
        grok_dir = fake_home / ".grok"
        grok_dir.mkdir(parents=True)
        # 仅有 BEGIN 没有 END
        (grok_dir / "config.toml").write_text("# BEGIN dc-farm mcp\n[mcp_servers]\n", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: fake_home)
        rep = assets_doctor.Report()
        assets_doctor.check_mcp(rep, reg={"mcp": []}, offline=True, deep=False)

        crit_items = [i for i in rep.by("mcp") if i["level"] == "crit"]
        assert any("标记块单侧缺失（损坏）" in i["msg"] for i in crit_items)

    def test_grok_bundled_skills_status_audit(self, assets_doctor, monkeypatch, tmp_path):
        """测试 check_app_roots 中针对 grok-bundled 结合 [skills] disabled 进行三桶状态对账。"""
        fake_home = tmp_path / "home"
        grok_dir = fake_home / ".grok"
        bundled_skills_dir = grok_dir / "bundled" / "skills"
        bundled_skills_dir.mkdir(parents=True)

        for sname in ["docx", "create-skill", "pptx", "design", "code-review"]:
            (bundled_skills_dir / sname).mkdir()

        (grok_dir / "config.toml").write_text(
            '[skills]\ndisabled = ["docx", "create-skill", "pptx"]\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: fake_home)

        reg = {
            "app_roots": [
                {
                    "name": "grok-bundled",
                    "location": str(bundled_skills_dir),
                    "count_baseline": {"dirs": 5},
                    "asserts": ["count_unchanged"],
                }
            ]
        }

        rep = assets_doctor.Report()
        assets_doctor.check_app_roots(rep, reg)

        ok_items = [i for i in rep.by("app_roots") if i["level"] == "ok" and i["name"] == "grok-bundled-status"]
        assert len(ok_items) == 1
        assert "2 启用, 3 禁用 (create-skill/docx/pptx)" in ok_items[0]["msg"]
