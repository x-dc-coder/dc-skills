"""路由回归：入口描述和正文不再指向已删除的族或技能名。

跑法：cd ~/projects/dc-skills && uv run python -m pytest dc-skill-creator/scripts/test_route_invariants.py -q
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def skill_text(name: str) -> str:
    return (ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def description(name: str) -> str:
    meta = yaml.safe_load(skill_text(name).split("---", 2)[1])
    desc = meta["description"]
    assert isinstance(desc, str)
    assert len(desc) <= 1024
    assert "<" not in desc
    assert ">" not in desc
    return desc


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_db_skill_does_not_own_infra() -> None:
    desc = description("db-skill")
    assert "MySQL" in desc
    assert "PostgreSQL" in desc
    assert "Codex" not in desc
    assert "infra" not in desc
    body = skill_text("db-skill").split("---", 2)[2]
    assert "基础设施运维族群入口" not in body
    assert "wsl-windows-bridge" in body


def test_programming_body_routing_contract() -> None:
    body = skill_text("programming").split("---", 2)[2]
    assert "git 与 GitHub 的事都进 git-master" in body
    assert "不限 .py/.rs/.ts/.go" in body
    assert "references/philosophy.md" in body  # C+②：七公理/TDD 下沉后正文必须保留指针


def test_codegraph_is_a_member() -> None:
    text = skill_text("codegraph-explore")
    assert "repo 族" not in text
    assert "仓库工程族群入口" not in text
    body = text.split("---", 2)[2]
    assert "大约 1 秒" in body
    assert "必传" in body


def test_creator_lists_live_families() -> None:
    body = skill_text("dc-skill-creator").split("---", 2)[2]
    for name in ("frontend", "software", "info", "dshplugin"):
        assert name in body
    assert "不要传 `--family standalone`" in body
    assert "已删除的 coding、infra、repo" in body
    for dissolved in ("db", "bridge", "lark", "meta"):
        assert f"`--family {dissolved}`" not in body


def test_design_ui_points_at_real_files() -> None:
    text = skill_text("design-ui")
    assert "artifact-diagramming" not in text
    assert "`dataviz`" not in text
    assert "dsh-plugin-troubleshooting" in text
    assert "design-dataviz/SKILL.md" in text
    assert "design-diagram/SKILL.md" in text


def test_thesis_routes_through_diagram_types() -> None:
    text = skill_text("thesis-writing")
    for retired in ("diagram-usecase", "diagram-flow", "diagram-module", "diagram-sequence", "diagram-ers"):
        assert retired not in text
    assert "thesis-output/" not in text
    assert "<cwd>/skills-output/thesis/thesis-writing/<时间戳>/" in text
    assert "](img/" in text
    desc = description("thesis-writing")
    assert "paper-reader" in desc
    assert "pdf" in desc
    assert "通用" not in desc
    spec = (ROOT / "thesis-writing/references/journal-image-spec.md").read_text(encoding="utf-8")
    assert "diagram-flow" not in spec


def test_kimi_does_not_require_missing_playwright() -> None:
    body = skill_text("kimi-webbridge").split("---", 2)[2]
    assert "没有 Playwright MCP" in body
    assert "mcp__browser__*" in body


def test_dshplugin_entry_is_on_every_farm() -> None:
    data = yaml.safe_load((ROOT / "agent-map.yaml").read_text(encoding="utf-8"))
    assert "dsh-plugin-troubleshooting" in data["base"]
    assert "dsh-ui-optimization" in data["base"]
    assert not data.get("on_demand")


def test_entry_covers_declared_triggers() -> None:
    """完备性锁（2026-10-16 C+）：agent-map triggers 声明是触发短语唯一事实源。

    每个 auto 入口的 description 必须覆盖本族全部声明短语；声明键必须等于成员全集
    （新增成员必须声明触发短语并补进入口 description，缺一即红）。"""
    data = yaml.safe_load((ROOT / "agent-map.yaml").read_text(encoding="utf-8"))
    problems: list[str] = []
    for fam, fcfg in data["families"].items():
        members_cfg = fcfg.get("members", [])
        member_set = set(members_cfg) if isinstance(members_cfg, dict) else set(members_cfg)
        triggers = fcfg.get("triggers")
        assert triggers is not None, (
            f"族群 {fam} 缺 triggers 声明（触发短语唯一事实源，写法见 agent-map 头注释）"
        )
        assert set(triggers) == member_set, (
            f"族群 {fam} 的 triggers 声明键与成员集不一致: {set(triggers) ^ member_set}"
        )
        desc = _norm(description(fcfg["entry"]))
        for member, phrases in triggers.items():
            for phrase in phrases:
                if _norm(phrase) not in desc:
                    problems.append(
                        f"{fam}/{member}: 触发短语 {phrase!r} 未出现在入口 {fcfg['entry']} description"
                    )
    for name, cfg in data.get("standalone", {}).items():
        if cfg.get("load") != "auto":
            continue
        phrases = cfg.get("triggers")
        assert phrases, f"standalone auto 技能 {name} 缺 triggers 声明"
        desc = _norm(description(name))
        for phrase in phrases:
            if _norm(phrase) not in desc:
                problems.append(f"standalone/{name}: 触发短语 {phrase!r} 未出现在自身 description")
    assert not problems, "\n".join(problems)


def test_cross_family_redirect_phrases_stay() -> None:
    """跨族反路由句是实测误触发抑制器（C+ 决议保留），简化 description 时不得删除。"""
    assert "论文的 AI 腔不在这里，走 paper-metrics" in _norm(description("programming"))
    assert "论文 AI 腔走 paper-metrics" in _norm(description("thesis-writing"))
    assert "DSH client 插件界面走 dsh-plugin-troubleshooting" in _norm(description("design-ui"))


def test_output_docs_do_not_teach_retired_paths() -> None:
    rules = (ROOT / "docs/specs/技能编写规则.md").read_text(encoding="utf-8")
    assert "必须实现两级回退" not in rules
    assert "plan_output" in rules
    guide = (ROOT / "dc-skill-creator/references/description-guide.md").read_text(encoding="utf-8")
    assert "thesis-output/" not in guide
    draft = (ROOT / "diagram/references/draft.md").read_text(encoding="utf-8")
    assert "diagram-flow" not in draft
    env = (ROOT / "docs/arch/环境依赖.md").read_text(encoding="utf-8")
    assert "仅在用户显式指定" not in env


def test_commit_style_rule_unified() -> None:
    """2026-10-19 合并后单轨规范：新仓库 Conventional Commits 中文起步，已有仓库沿用既有风格。"""
    text = skill_text("git-master")
    assert "Conventional Commits" in text
    assert "沿用" in text
    assert "github-workflow" not in text  # 已归档，正文不得再路由到旧技能
