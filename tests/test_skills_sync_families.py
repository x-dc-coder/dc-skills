"""tests/test_skills_sync_families.py — skills-sync 技能标记一致性与格式校验单测。

覆盖需求：
  测试 check_families 对：
    - standalone manual（通过/缺标记报警：缺 dmi / 缺 openai.yaml / 未禁隐式调用 / 存在 family 等）
    - standalone auto（禁止残留报警：残留 dmi / 残留 openai.yaml）
    - member 列表与 dict 覆写（auto 允许无 dmi 与 openai.yaml，manual 必须有）
    - 格式损坏的判定逻辑（SKILL.md 缺失、frontmatter 缺失或 YAML 格式损坏、openai.yaml 格式损坏等）
"""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml


def _create_skill(
    base_dir: Path,
    name: str,
    *,
    fm_content: str | None = None,
    openai_yaml_content: str | None = None,
    create_skill_md: bool = True,
) -> Path:
    skill_dir = base_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if create_skill_md:
        md = skill_dir / "SKILL.md"
        if fm_content is not None:
            md.write_text(f"---\n{fm_content}\n---\n# Content of {name}\n", encoding="utf-8")
        else:
            md.write_text(f"# No frontmatter content {name}\n", encoding="utf-8")

    if openai_yaml_content is not None:
        oa = skill_dir / "agents" / "openai.yaml"
        oa.parent.mkdir(parents=True, exist_ok=True)
        oa.write_text(openai_yaml_content, encoding="utf-8")
    return skill_dir


class TestCheckFamiliesStandalone:
    def test_standalone_manual_pass(self, skills_sync, tmp_path):
        """standalone manual 配置三标记完整，check_families 无问题。"""
        manifest = {
            "families": {},
            "standalone": {
                "proxy-ops": {"load": "manual"}
            }
        }
        fm = """name: proxy-ops
description: Proxy ops
metadata:
  role: standalone
  load-mode: manual
disable-model-invocation: true
"""
        oa = """interface:
  display_name: "Proxy Ops"
policy:
  allow_implicit_invocation: false
"""
        _create_skill(tmp_path, "proxy-ops", fm_content=fm, openai_yaml_content=oa)

        problems = skills_sync.check_families(manifest, tmp_path)
        assert problems == []

    def test_standalone_manual_missing_flags_alarm(self, skills_sync, tmp_path):
        """standalone manual 缺 dmi、缺 openai.yaml、或者错误残留 family 产生报警。"""
        manifest = {
            "families": {},
            "standalone": {
                "proxy-ops": {"load": "manual"}
            }
        }
        # 错误：有 metadata.family，缺 disable-model-invocation，且无 openai.yaml
        fm = """name: proxy-ops
description: Proxy ops
metadata:
  family: somefam
  role: standalone
  load-mode: manual
"""
        _create_skill(tmp_path, "proxy-ops", fm_content=fm, openai_yaml_content=None)

        problems = skills_sync.check_families(manifest, tmp_path)
        assert len(problems) >= 3
        text = "\n".join(problems)
        assert "不应有 metadata.family" in text
        assert "缺 disable-model-invocation: true" in text
        assert "缺 agents/openai.yaml" in text

    def test_standalone_manual_openai_yaml_not_disabled(self, skills_sync, tmp_path):
        """standalone manual 中 openai.yaml 未禁隐式调用时报警。"""
        manifest = {
            "families": {},
            "standalone": {
                "proxy-ops": {"load": "manual"}
            }
        }
        fm = """name: proxy-ops
description: Proxy ops
metadata:
  role: standalone
  load-mode: manual
disable-model-invocation: true
"""
        oa = """interface:
  display_name: "Proxy Ops"
policy:
  allow_implicit_invocation: true
"""
        _create_skill(tmp_path, "proxy-ops", fm_content=fm, openai_yaml_content=oa)

        problems = skills_sync.check_families(manifest, tmp_path)
        assert any("openai.yaml 未禁隐式调用" in p for p in problems)

    def test_standalone_auto_prohibits_residuals(self, skills_sync, tmp_path):
        """standalone auto 严禁残留 dmi 与 openai.yaml。"""
        manifest = {
            "families": {},
            "standalone": {
                "fast-tool": {"load": "auto"}
            }
        }
        # 错误残留 dmi 和 openai.yaml
        fm = """name: fast-tool
description: Fast tool
metadata:
  role: standalone
  load-mode: auto
disable-model-invocation: true
"""
        oa = """policy:
  allow_implicit_invocation: false
"""
        _create_skill(tmp_path, "fast-tool", fm_content=fm, openai_yaml_content=oa)

        problems = skills_sync.check_families(manifest, tmp_path)
        assert len(problems) == 2
        text = "\n".join(problems)
        assert "不应有 disable-model-invocation" in text
        assert "auto 模式不应有 agents/openai.yaml" in text


class TestCheckFamiliesMembers:
    def test_member_list_and_dict_override(self, skills_sync, tmp_path):
        """测试 member 列表（默认 manual）与 dict 覆写（auto 豁免，manual 必检）。"""
        manifest = {
            "families": {
                "coding": {
                    "entry": "code-entry",
                    "members": {
                        "code-sub": "auto",       # 覆写为 auto
                        "code-manual": "manual",  # manual 成员
                    }
                }
            },
            "standalone": {}
        }

        # 1. entry 正常
        _create_skill(
            tmp_path,
            "code-entry",
            fm_content="name: code-entry\ndescription: Entry\nmetadata:\n  family: coding\n  role: entry\n  load-mode: auto\n"
        )

        # 2. code-sub (auto member): 无 dmi, 无 openai.yaml
        _create_skill(
            tmp_path,
            "code-sub",
            fm_content="name: code-sub\ndescription: Sub auto\nmetadata:\n  family: coding\n  role: member\n  load-mode: auto\n"
        )

        # 3. code-manual (manual member): 有 dmi, 有 openai.yaml
        _create_skill(
            tmp_path,
            "code-manual",
            fm_content="name: code-manual\ndescription: Sub man\nmetadata:\n  family: coding\n  role: member\n  load-mode: manual\ndisable-model-invocation: true\n",
            openai_yaml_content="policy:\n  allow_implicit_invocation: false\n"
        )

        problems = skills_sync.check_families(manifest, tmp_path)
        assert problems == []


class TestCheckFamiliesCorruptedFormats:
    def test_missing_directory(self, skills_sync, tmp_path):
        """目录缺失判定。"""
        manifest = {
            "families": {
                "fam": {"entry": "missing-skill", "members": []}
            },
            "standalone": {}
        }
        problems = skills_sync.check_families(manifest, tmp_path)
        assert any("目录缺失" in p for p in problems)

    def test_missing_frontmatter_or_bad_yaml(self, skills_sync, tmp_path):
        """frontmatter 缺失或 YAML 损坏判定。"""
        manifest = {
            "families": {
                "fam": {"entry": "bad-fm-skill", "members": []}
            },
            "standalone": {}
        }
        # 无 frontmatter
        _create_skill(tmp_path, "bad-fm-skill", fm_content=None)
        problems = skills_sync.check_families(manifest, tmp_path)
        assert any("frontmatter 缺失或 YAML 错误" in p for p in problems)

        # YAML 语法错误
        skill_dir = tmp_path / "bad-fm-skill"
        (skill_dir / "SKILL.md").write_text("---\n: invalid: [yaml : error\n---\n", encoding="utf-8")
        problems2 = skills_sync.check_families(manifest, tmp_path)
        assert any("frontmatter 缺失或 YAML 错误" in p for p in problems2)

    def test_corrupted_openai_yaml(self, skills_sync, tmp_path):
        """openai.yaml 语法损坏判定。"""
        manifest = {
            "families": {},
            "standalone": {
                "bad-oa": {"load": "manual"}
            }
        }
        fm = """name: bad-oa
description: Bad oa
metadata:
  role: standalone
  load-mode: manual
disable-model-invocation: true
"""
        _create_skill(tmp_path, "bad-oa", fm_content=fm, openai_yaml_content="[invalid: {yaml: unclosed")
        problems = skills_sync.check_families(manifest, tmp_path)
        assert any("openai.yaml YAML 错误" in p for p in problems)
