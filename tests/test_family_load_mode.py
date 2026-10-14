"""tests/test_family_load_mode.py — 技能加载模式仲裁与元数据标记单测。

覆盖需求：
  1. resolve_load 仲裁优先级：
     - standalone 显式 > entry 强制 auto > member 默认 manual > member dict 覆写 auto
  2. 非法覆写报错（FarmModelError）
  3. iter_members 排序契约与重名检测（跨段与段内冲突）
  4. standalone manual 的三标记生成：
     - load-mode: manual, disable-model-invocation: true
     - agents/openai.yaml 且 policy.allow_implicit_invocation == false
     - 删除 metadata.family
  5. entry 与 member auto 覆写清理 manual 标记
"""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml


class TestResolveLoad:
    def test_standalone_explicit_priority(self, farm_model):
        """standalone 显式配置具有最高优先级，无论角色是什么。"""
        # standalone manual 覆写
        standalone_cfg = {"my-skill": {"load": "manual"}}
        mode = farm_model.resolve_load(
            "my-skill",
            role="entry",
            family_config={"entry": "my-skill"},
            standalone_config=standalone_cfg,
        )
        assert mode == "manual"

        # standalone 简写字符串形式
        standalone_str = {"my-skill-2": "manual"}
        mode = farm_model.resolve_load(
            "my-skill-2",
            role="member",
            standalone_config=standalone_str,
        )
        assert mode == "manual"

        # standalone 未显式提供 load 字段时缺省为 auto
        standalone_empty = {"my-skill-3": {}}
        mode = farm_model.resolve_load(
            "my-skill-3",
            role="member",
            standalone_config=standalone_empty,
        )
        assert mode == "auto"

    def test_entry_enforces_auto(self, farm_model):
        """entry 角色强制为 auto（未在 standalone 覆写时）。"""
        mode = farm_model.resolve_load("entry-skill", role="entry")
        assert mode == "auto"

    def test_member_default_manual(self, farm_model):
        """member 角色默认收敛为 manual。"""
        fcfg = {"entry": "e-skill", "members": ["mem-skill"]}
        mode = farm_model.resolve_load("mem-skill", role="member", family_config=fcfg)
        assert mode == "manual"

    def test_member_dict_override_auto(self, farm_model):
        """member 在字典覆写中声明 auto 时为 auto。"""
        fcfg = {"entry": "e-skill", "members": {"mem-skill": "auto", "mem-manual": "manual"}}
        mode_auto = farm_model.resolve_load("mem-skill", role="member", family_config=fcfg)
        assert mode_auto == "auto"

        mode_man = farm_model.resolve_load("mem-manual", role="member", family_config=fcfg)
        assert mode_man == "manual"

    def test_invalid_override_raises_farm_model_error(self, farm_model):
        """非法覆写值或配置类型抛出 FarmModelError。"""
        # member 覆写值非法
        fcfg_invalid = {"members": {"bad-member": "dynamic"}}
        with pytest.raises(farm_model.FarmModelError, match="覆写值非法"):
            farm_model.resolve_load("bad-member", role="member", family_config=fcfg_invalid)

        # standalone 覆写值非法
        std_invalid = {"bad-std": {"load": "unknown"}}
        with pytest.raises(farm_model.FarmModelError, match="必须为 'auto' 或 'manual'"):
            farm_model.resolve_load("bad-std", role="standalone", standalone_config=std_invalid)

        # standalone 类型非法
        std_type_err = {"bad-std-2": 12345}
        with pytest.raises(farm_model.FarmModelError, match="配置类型非法"):
            farm_model.resolve_load("bad-std-2", role="standalone", standalone_config=std_type_err)

        # 未知角色类型
        with pytest.raises(farm_model.FarmModelError, match="未知角色类型"):
            farm_model.resolve_load("bad-role", role="unknown")  # type: ignore


class TestIterMembers:
    def test_iter_members_order_contract(self, farm_model, tmp_path):
        """排序契约：
        1. 族群按族名（family name）字母升序排序；
        2. 族内先 entry 后 members（保持声明顺序）；
        3. standalone 技能按技能名字母升序排序紧随其后。
        """
        manifest = {
            "families": {
                "zeta": {
                    "entry": "zeta-entry",
                    "members": ["zeta-m2", "zeta-m1"],
                },
                "alpha": {
                    "entry": "alpha-entry",
                    "members": {"alpha-m1": "manual", "alpha-m2": "auto"},
                },
            },
            "standalone": {
                "zeta-standalone": {"load": "manual"},
                "alpha-standalone": {"load": "auto"},
            },
        }

        specs = list(farm_model.iter_members(manifest, root=tmp_path))
        names = [s.name for s in specs]
        expected_names = [
            # alpha family
            "alpha-entry",
            "alpha-m1",
            "alpha-m2",
            # zeta family
            "zeta-entry",
            "zeta-m2",
            "zeta-m1",
            # standalone
            "alpha-standalone",
            "zeta-standalone",
        ]
        assert names == expected_names

        # 检查各 spec 属性
        spec_map = {s.name: s for s in specs}
        assert spec_map["alpha-entry"].role == "entry"
        assert spec_map["alpha-entry"].load_mode == "auto"
        assert spec_map["alpha-entry"].family == "alpha"

        assert spec_map["alpha-m1"].role == "member"
        assert spec_map["alpha-m1"].load_mode == "manual"

        assert spec_map["alpha-m2"].role == "member"
        assert spec_map["alpha-m2"].load_mode == "auto"

        assert spec_map["alpha-standalone"].role == "standalone"
        assert spec_map["alpha-standalone"].load_mode == "auto"
        assert spec_map["alpha-standalone"].family is None

        assert spec_map["zeta-standalone"].role == "standalone"
        assert spec_map["zeta-standalone"].load_mode == "manual"
        assert spec_map["zeta-standalone"].family is None

    def test_collision_detection_same_section(self, farm_model):
        """同段重名检测。"""
        # members 内部重名
        manifest = {
            "families": {
                "fam1": {
                    "entry": "e1",
                    "members": ["dup-mem", "dup-mem"],
                }
            },
            "standalone": {},
        }
        with pytest.raises(farm_model.FarmModelError, match="检测到重复技能 'dup-mem'"):
            list(farm_model.iter_members(manifest))

    def test_collision_detection_cross_sections(self, farm_model):
        """跨段重名检测：entry vs member, family vs standalone 等。"""
        # entry vs member 跨段冲突
        manifest_entry_member = {
            "families": {
                "fam1": {
                    "entry": "same-skill",
                    "members": ["same-skill"],
                }
            },
            "standalone": {},
        }
        with pytest.raises(farm_model.FarmModelError, match="检测到重复技能 'same-skill'"):
            list(farm_model.iter_members(manifest_entry_member))

        # member vs standalone 跨段冲突
        manifest_mem_std = {
            "families": {
                "fam1": {
                    "entry": "e1",
                    "members": ["shared-skill"],
                }
            },
            "standalone": {
                "shared-skill": {"load": "manual"},
            },
        }
        with pytest.raises(farm_model.FarmModelError, match="检测到重复技能 'shared-skill'"):
            list(farm_model.iter_members(manifest_mem_std))


class TestMetadataMarkingAndCleanup:
    def test_standalone_manual_three_flags_generation(self, family_apply, farm_model, tmp_path):
        """测试 standalone manual 的三标记生成：
        1. load-mode: manual
        2. disable-model-invocation: true
        3. openai.yaml 且 policy.allow_implicit_invocation == false
        4. 且必须移除 metadata.family
        """
        skill_dir = tmp_path / "standalone-ops"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"

        # 初始 frontmatter 含历史遗留的 family: oldfam
        initial_content = """---
name: standalone-ops
description: Dedicated standalone proxy and operations tool
metadata:
  family: oldfam
  role: member
  load-mode: auto
---
# Body of Standalone Ops
"""
        skill_md.write_text(initial_content, encoding="utf-8")

        spec = farm_model.SkillLoadSpec(
            name="standalone-ops",
            role="standalone",
            load_mode="manual",
            family=None,
            skill_path=skill_dir,
        )

        n_changed, logs = family_apply.apply_skill(spec, apply=True)
        assert n_changed == 1

        # 检查 SKILL.md
        new_text = skill_md.read_text(encoding="utf-8")
        fm_txt, body, _ = family_apply.split_frontmatter(new_text)
        fm = yaml.safe_load(fm_txt)
        meta = fm.get("metadata", {})

        assert meta.get("role") == "standalone"
        assert meta.get("load-mode") == "manual"
        assert "family" not in meta  # 必须删除 metadata.family
        assert fm.get("disable-model-invocation") is True  # 标记二

        # 检查 agents/openai.yaml（标记三）
        openai_yaml = skill_dir / "agents" / "openai.yaml"
        assert openai_yaml.exists()
        oa_data = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
        assert oa_data["policy"]["allow_implicit_invocation"] is False

    def test_entry_and_member_auto_cleans_manual_flags(self, family_apply, farm_model, tmp_path):
        """测试 entry 与 member auto 覆写时，能够自动清理 manual 标记与 openai.yaml。"""
        skill_dir = tmp_path / "promoted-entry"
        skill_dir.mkdir(parents=True)
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir(parents=True)
        openai_yaml = agents_dir / "openai.yaml"
        openai_yaml.write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")

        # 初始 frontmatter 包含 manual 和 dmi
        initial_content = """---
name: promoted-entry
description: Promoted entry skill description
metadata:
  family: software
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Main Content
"""
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(initial_content, encoding="utf-8")

        # 升位为 entry (load_mode: auto)
        spec = farm_model.SkillLoadSpec(
            name="promoted-entry",
            role="entry",
            load_mode="auto",
            family="software",
            skill_path=skill_dir,
        )

        n_changed, logs = family_apply.apply_skill(spec, apply=True)
        assert n_changed == 1

        new_text = skill_md.read_text(encoding="utf-8")
        fm_txt, _, _ = family_apply.split_frontmatter(new_text)
        fm = yaml.safe_load(fm_txt)
        meta = fm.get("metadata", {})

        assert meta.get("role") == "entry"
        assert meta.get("load-mode") == "auto"
        assert meta.get("family") == "software"
        assert "disable-model-invocation" not in fm  # 必须清除 dmi
        assert not openai_yaml.exists()  # 必须清理 openai.yaml
