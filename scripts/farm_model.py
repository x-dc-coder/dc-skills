#!/usr/bin/env python3
"""farm_model — 技能农场统一数据模型与加载模式解析契约。

定义技能角色（RoleType）、加载模式（LoadMode）以及统一元数据对象（SkillLoadSpec）。
集中处理 families、standalone 以及加载模式覆写逻辑，并对外提供严格契约与重名校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

RoleType = Literal["entry", "member", "standalone"]
LoadMode = Literal["auto", "manual"]


class FarmModelError(ValueError):
    """农场模型契约异常（配置格式错误、重名冲突、非法覆写等）。"""


@dataclass(frozen=True)
class SkillLoadSpec:
    """技能加载规格元数据描述符。"""

    name: str
    role: RoleType
    load_mode: LoadMode
    family: str | None
    skill_path: Path


def resolve_load(
    skill_name: str,
    role: RoleType,
    family_config: Mapping[str, Any] | None = None,
    standalone_config: Mapping[str, Any] | None = None,
) -> LoadMode:
    """计算确定指定技能的最终加载模式（load-mode）。

    优先级仲裁契约：
      1. 若在 standalone 中显式配置，以 standalone.<name>.load 为最高准则（若未配置 load 字段则缺省为 auto）；
      2. 若为 entry 角色，强制为 'auto'；
      3. 若为 member 角色：
         - 缺省为 'manual'；
         - 若 family.members 为 dict 且对应 value 为 'auto'，则为 'auto'；
         - 若 value 为 'manual'，则为 'manual'；
         - 若 value 为其他值，抛出 FarmModelError；
      4. 若角色非法或覆写值非法，抛出 FarmModelError。
    """
    if standalone_config and skill_name in standalone_config:
        cfg = standalone_config[skill_name]
        if isinstance(cfg, dict):
            load_val = cfg.get("load", "auto")
        elif isinstance(cfg, str):
            load_val = cfg
        elif cfg is None:
            load_val = "auto"
        else:
            raise FarmModelError(f"standalone 技能 {skill_name!r} 配置类型非法: {type(cfg).__name__}")

        if load_val not in ("auto", "manual"):
            raise FarmModelError(
                f"standalone 技能 {skill_name!r} load 必须为 'auto' 或 'manual'，得到: {load_val!r}"
            )
        return load_val

    if role == "entry":
        return "auto"

    if role == "member":
        if family_config:
            members_cfg = family_config.get("members")
            if isinstance(members_cfg, dict) and skill_name in members_cfg:
                val = members_cfg[skill_name]
                if val == "auto":
                    return "auto"
                if val == "manual":
                    return "manual"
                raise FarmModelError(
                    f"member 技能 {skill_name!r} 覆写值非法（仅允许 'auto' 或 'manual'）: {val!r}"
                )
        return "manual"

    if role == "standalone":
        return "auto"

    raise FarmModelError(f"未知角色类型: {role!r}")


def iter_members(
    manifest_data: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> Iterator[SkillLoadSpec]:
    """遍历 manifest 中所有需要进行元数据标记与分发的技能。

    排序与声明顺序契约：
      1. 族群按族名（family name）字母升序排序；
      2. 族内先 entry 后 members（保持原声明顺序）；
      3. standalone 技能按技能名（skill name）字母升序排序紧随其后；
      4. 支持 members 为 list[str] 或 dict[str, str]；
      5. 重名校验：entry / member / standalone 跨段或同段重名时抛出 FarmModelError。
    """
    base_dir = root if root is not None else Path(__file__).resolve().parent.parent
    seen_skills: dict[str, str] = {}  # skill_name -> source description

    families = manifest_data.get("families") or {}
    if not isinstance(families, dict):
        raise FarmModelError("manifest 中 'families' 必须为映射字典")

    standalone = manifest_data.get("standalone") or {}
    if not isinstance(standalone, dict):
        raise FarmModelError("manifest 中 'standalone' 必须为映射字典")

    for fname in sorted(families.keys()):
        fcfg = families[fname] or {}
        if not isinstance(fcfg, dict):
            raise FarmModelError(f"族群 {fname!r} 的配置必须为字典")

        entry_name = fcfg.get("entry")
        if entry_name:
            if not isinstance(entry_name, str):
                raise FarmModelError(f"族群 {fname!r} 的 entry 必须为字符串: {entry_name!r}")
            if entry_name in seen_skills:
                raise FarmModelError(
                    f"检测到重复技能 {entry_name!r}：在 {fname}.entry 重复出现（初次于 {seen_skills[entry_name]}）"
                )
            seen_skills[entry_name] = f"family {fname} (entry)"
            load_mode = resolve_load(entry_name, "entry", family_config=fcfg, standalone_config=standalone)
            yield SkillLoadSpec(
                name=entry_name,
                role="entry",
                load_mode=load_mode,
                family=fname,
                skill_path=base_dir / entry_name,
            )

        members_raw = fcfg.get("members") or []
        if isinstance(members_raw, list):
            member_names = members_raw
        elif isinstance(members_raw, dict):
            member_names = list(members_raw.keys())
        else:
            raise FarmModelError(f"族群 {fname!r} 的 members 必须为列表或字典: {type(members_raw).__name__}")

        for mname in member_names:
            if not isinstance(mname, str):
                raise FarmModelError(f"族群 {fname!r} 的 member 名称必须为字符串: {mname!r}")
            if mname in seen_skills:
                raise FarmModelError(
                    f"检测到重复技能 {mname!r}：在 {fname}.member 重复出现（初次于 {seen_skills[mname]}）"
                )
            seen_skills[mname] = f"family {fname} (member)"
            load_mode = resolve_load(mname, "member", family_config=fcfg, standalone_config=standalone)
            yield SkillLoadSpec(
                name=mname,
                role="member",
                load_mode=load_mode,
                family=fname,
                skill_path=base_dir / mname,
            )

    for sname in sorted(standalone.keys()):
        if not isinstance(sname, str):
            raise FarmModelError(f"standalone 技能名必须为字符串: {sname!r}")
        if sname in seen_skills:
            raise FarmModelError(
                f"检测到重复技能 {sname!r}：在 standalone 重复出现（初次于 {seen_skills[sname]}）"
            )
        seen_skills[sname] = "standalone"
        load_mode = resolve_load(sname, "standalone", family_config=None, standalone_config=standalone)
        yield SkillLoadSpec(
            name=sname,
            role="standalone",
            load_mode=load_mode,
            family=None,
            skill_path=base_dir / sname,
        )
