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


def test_db_skill_does_not_own_infra() -> None:
    desc = description("db-skill")
    assert "MySQL" in desc
    assert "PostgreSQL" in desc
    assert "Codex" not in desc
    assert "infra" not in desc
    body = skill_text("db-skill").split("---", 2)[2]
    assert "基础设施运维族群入口" not in body
    assert "newapi-management" in body
    assert "wsl-windows-bridge" in body


def test_programming_exposes_member_triggers() -> None:
    desc = description("programming")
    for token in ("commit", "提交", "崩溃", "调用链", "假绿", "deslop", "github-workflow"):
        assert token in desc, token
    body = skill_text("programming").split("---", 2)[2]
    assert "一次提交只打开 git-master 或 github-workflow 其中一个" in body
    assert "不限 .py/.rs/.ts/.go" in body


def test_codegraph_is_a_member() -> None:
    text = skill_text("codegraph-explore")
    assert "repo 族" not in text
    assert "仓库工程族群入口" not in text
    body = text.split("---", 2)[2]
    assert "大约 1 秒" in body
    assert "必传" in body


def test_creator_lists_live_families() -> None:
    body = skill_text("dc-skill-creator").split("---", 2)[2]
    for name in ("frontend", "software", "bridge", "dshplugin"):
        assert name in body
    assert "不要传 `--family standalone`" in body
    assert "已删除的 coding、infra、repo" in body


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


def test_officecli_claims_office_work() -> None:
    desc = description("officecli")
    assert "word-extractor" in desc
    assert "docx" in desc
    assert "pptx" in desc


def test_kimi_does_not_require_missing_playwright() -> None:
    body = skill_text("kimi-webbridge").split("---", 2)[2]
    assert "没有 Playwright MCP" in body
    assert "mcp__browser__*" in body


def test_dshplugin_entry_is_on_every_farm() -> None:
    data = yaml.safe_load((ROOT / "agent-map.yaml").read_text(encoding="utf-8"))
    assert "dsh-plugin-troubleshooting" in data["base"]
    assert "dsh-ui-optimization" in data["base"]
    assert not data.get("on_demand")


def test_entry_descriptions_keep_member_phrases() -> None:
    prog = description("programming")
    assert "代码 deslop" in prog
    assert "结果错误" in prog
    assert "squash" in prog
    assert "preview" in prog
    assert "论文的 AI 腔不在这里" in prog
    thesis = description("thesis-writing")
    assert "paper-metrics" in thesis
    assert "md-to-thesis-latex" in thesis
    assert "封面拼接" in thesis
    assert "用我的浏览器" in description("unified-search")
    assert "机制图" in description("design-ui")
    assert "插件前端丑" in description("dsh-plugin-troubleshooting")
    assert "待办" in description("orca-cli")
    assert "分析论文 docx" in description("officecli")


def test_output_docs_do_not_teach_retired_paths() -> None:
    rules = (ROOT / "docs/specs/SKILL-AUTHORING-RULES.md").read_text(encoding="utf-8")
    assert "必须实现两级回退" not in rules
    assert "plan_output" in rules
    guide = (ROOT / "dc-skill-creator/references/description-guide.md").read_text(encoding="utf-8")
    assert "thesis-output/" not in guide
    draft = (ROOT / "diagram/references/draft.md").read_text(encoding="utf-8")
    assert "diagram-flow" not in draft
    env = (ROOT / "docs/arch/ENVIRONMENT.md").read_text(encoding="utf-8")
    assert "仅在用户显式指定" not in env


def test_git_commit_styles_stay_separate() -> None:
    assert "git-master" in skill_text("github-workflow")
    assert "github-workflow" in skill_text("git-master")
