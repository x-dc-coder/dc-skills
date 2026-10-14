#!/usr/bin/env python3
"""family-apply — 按 agent-map.yaml families 与 standalone 段幂等标记族群元数据。

语义（2026-09-24 族群化，2026-10 阶段二泛化）：
- entry：SKILL.md metadata 补 family / role: entry / load-mode: auto（进模型启动清单）
- member：metadata 补 family / role: member / load-mode: <auto|manual>
  - 若 load-mode 为 manual：写 disable-model-invocation: true 并生成 agents/openai.yaml
    （policy.allow_implicit_invocation: false）
  - 若 load-mode 为 auto：移除 disable-model-invocation 与 agents/openai.yaml（如有）
- standalone：SKILL.md metadata 补 role: standalone / load-mode: <auto|manual>，并移除 metadata.family
  - 若 load-mode 为 manual：写 disable-model-invocation: true 并生成 agents/openai.yaml
  - 若 load-mode 为 auto：移除 disable-model-invocation 与 agents/openai.yaml（如有）
- 顺带清理存量违规：顶层 version: → metadata.version（字符串，B3a）；顶层 whenToUse: 整行删除

用法：
  cd ~/projects/dc-skills && uv run python scripts/family-apply.py           # dry-run
  uv run python scripts/family-apply.py --apply                             # 写入
标记一致性校验由 skills-sync --check 负责，不在本脚本重复。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# 支持同目录导入 farm_model
sys.path.insert(0, str(Path(__file__).resolve().parent))
from farm_model import FarmModelError, SkillLoadSpec, iter_members

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "agent-map.yaml"

ACRONYMS = {
    "cli": "CLI", "xml": "XML", "ui": "UI", "mcp": "MCP", "dsh": "DSH", "ai": "AI",
    "er": "ER", "pdf": "PDF", "docx": "DOCX", "xlsx": "XLSX", "pptx": "PPTX",
    "sql": "SQL", "api": "API", "gh": "GH", "js": "JS", "ts": "TS", "latex": "LaTeX",
}
SMALL_WORDS = {"to", "of", "for", "and", "the", "a", "in"}


def format_display_name(skill_name: str) -> str:
    """drawio-xml → Drawio XML（对齐 Codex skill-creator 的惯例）。"""
    out = []
    for i, word in enumerate(w for w in skill_name.split("-") if w):
        low = word.lower()
        if low in ACRONYMS:
            out.append(ACRONYMS[low])
        elif i > 0 and low in SMALL_WORDS:
            out.append(low)
        else:
            out.append(word.capitalize())
    return " ".join(out)


def split_frontmatter(txt: str) -> tuple[str, str, str]:
    """返回 (frontmatter 文本, 正文, 原名)。无 frontmatter 时 fm 为 ""。"""
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)", txt)
    if not m:
        return "", txt, ""
    return m.group(1), txt[m.end():], "ok"


def extract_description(fm: str) -> str:
    """从 frontmatter 提取 description 全文（行级解析，不受后续键影响）。"""
    lines = fm.split("\n")
    idx = next((n for n, ln in enumerate(lines) if ln.startswith("description:")), None)
    if idx is None:
        return ""
    rest = lines[idx][len("description:"):].strip()
    if rest and rest[0] not in "|>":
        return rest.strip('"\'')
    body = [ln.strip() for ln in lines[idx + 1:] if ln.strip() and ln[0].isspace()]
    return " ".join(body) or rest.lstrip(">|").strip()


def clamp_desc(text: str, lo: int = 25, hi: int = 64) -> str:
    """把短描述夹到 [lo, hi] 字符（Codex openai.yaml 约束）。"""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > hi:
        cut = text[:hi]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        text = cut.rstrip(" ,，。；;、")
    while len(text) < lo:
        text = f"{text} workflows" if len(text) + 10 <= hi else f"{text} guide"
        if len(text) > hi:
            text = text[:hi].rstrip()
            break
    return text


def first_clause(desc: str) -> str:
    m = re.split(r"[。．!?！？;；]", desc, maxsplit=1)
    return m[0].strip() if m and m[0].strip() else desc


def mark_frontmatter(
    fm: str,
    role: str,
    load_mode: str,
    family: str | None = None,
) -> tuple[str, list[str]]:
    """文本级改造 frontmatter，返回 (新 fm, 变更说明)。幂等。

    参数：
      role: "entry" | "member" | "standalone"
      load_mode: "auto" | "manual"
      family: 族名，standalone 时为 None
    """
    changes: list[str] = []
    lines = fm.split("\n")
    out: list[str] = []
    version_val = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^version:\s*\S+\s*$", ln):  # 顶层 version（B3a 违规）→ 迁 metadata
            version_val = ln.split(":", 1)[1].strip().strip('"\'')
            changes.append(f"顶层 version: {version_val} → metadata.version")
            i += 1
            continue
        if re.match(r"^whenToUse:", ln):  # 驼峰违禁字段 → 删除
            changes.append("删除顶层 whenToUse（触发信息以 description 为准）")
            i += 1
            continue
        out.append(ln)
        i += 1
    lines = out

    # metadata 期望键值组装
    want: dict[str, str] = {}
    if family is not None:
        want["family"] = family
    want["role"] = role
    want["load-mode"] = load_mode
    if version_val:
        want["version"] = version_val

    meta_idx = next((n for n, ln in enumerate(lines) if re.match(r"^metadata:", ln)), None)
    if meta_idx is None:
        block = ["metadata:"] + [f"  {k}: {v}" for k, v in want.items()]
        lines = lines + block
        changes.append("新增 metadata 块（" + "/".join(want.keys()) + "）")
    else:
        head = lines[meta_idx]
        m_inline = re.match(r"^metadata:\s*\{([^}]*)\}\s*$", head)
        if m_inline:
            inner = [p.strip() for p in m_inline.group(1).split(",") if p.strip()]
            if not inner:
                lines[meta_idx:meta_idx + 1] = ["metadata:"] + [f"  {k}: {v}" for k, v in want.items()]
                changes.append("metadata: {} → 块形式（" + "/".join(want.keys()) + "）")
            else:
                merged = dict(p.split(":", 1) for p in inner)
                if family is None and "family" in merged:
                    del merged["family"]
                    changes.append("移除 metadata.family（standalone 技能）")
                merged.update({k: str(v) for k, v in want.items()})
                lines[meta_idx:meta_idx + 1] = ["metadata:"] + [
                    f"  {k}: {v}" for k, v in merged.items()
                ]
                changes.append("metadata 行内 map → 块形式并合并键")
        else:
            # 块形式 metadata：
            # 1. standalone 场景下若已有 family 行，予以删除
            if family is None:
                del_indices = [
                    n for n in range(meta_idx + 1, len(lines))
                    if re.match(r"^  family:", lines[n])
                ]
                for idx in reversed(del_indices):
                    lines.pop(idx)
                    changes.append("移除 metadata.family（standalone 技能）")

            # 2. 既有键：值不同则更新；缺失键：插入
            updated, added = [], []
            for k, v in want.items():
                hit = next((n for n in range(meta_idx + 1, len(lines))
                            if re.match(rf"^  {re.escape(k)}:", lines[n])), None)
                if hit is None:
                    added.append((k, v))
                elif lines[hit].strip() != f"{k}: {v}":
                    old_val = lines[hit].strip()
                    lines[hit] = f"  {k}: {v}"
                    updated.append(f"{k}: {old_val} → {k}: {v}")
            if added:
                lines[meta_idx + 1:meta_idx + 1] = [f"  {k}: {v}" for k, v in added]
            if updated:
                changes.append("metadata 更新: " + "; ".join(updated))
            if added:
                changes.append(f"metadata 补键: {', '.join(k for k, _ in added)}")

    # disable-model-invocation（顶层，kebab）：仅当 load_mode == "manual" 时需要
    if load_mode == "manual":
        if not any(ln.strip() == "disable-model-invocation: true" for ln in lines):
            lines.append("disable-model-invocation: true")
            changes.append("新增 disable-model-invocation: true（manual-only 显式触发）")
    else:
        stripped = [ln for ln in lines if ln.strip() != "disable-model-invocation: true"]
        if len(stripped) != len(lines):
            lines = stripped
            changes.append("移除 disable-model-invocation（auto 加载，恢复自动触发）")

    return "\n".join(lines), changes


def gen_openai_yaml(skill_name: str, desc: str) -> str:
    display = format_display_name(skill_name)
    short = clamp_desc(first_clause(desc) or f"Help with {display} tasks")
    return (
        "# Codex 技能级元数据（族群化 member：不允许隐式调用，仅 $<name> 显式触发）\n"
        "# 由 scripts/family-apply.py 生成；SKILL.md description 变更后需重跑刷新。\n"
        "interface:\n"
        f'  display_name: "{display}"\n'
        f'  short_description: "{short}"\n'
        "policy:\n"
        "  allow_implicit_invocation: false\n"
    )


def apply_skill(spec: SkillLoadSpec, *, apply: bool) -> tuple[int, list[str]]:
    """对单个技能执行 frontmatter 标记与 openai.yaml 管理，返回 (变更计数, 日志行列表)。"""
    d = spec.skill_path
    skill_md = d / "SKILL.md"
    logs: list[str] = []
    if not skill_md.is_file():
        logs.append(f"🚨 [{spec.family or 'standalone'}] {spec.name}: SKILL.md 缺失")
        return 0, logs

    txt = skill_md.read_text(encoding="utf-8")
    fm, body, _ = split_frontmatter(txt)
    if not fm:
        logs.append(f"🚨 [{spec.family or 'standalone'}] {spec.name}: 无 frontmatter（B1 违规）")
        return 0, logs

    new_fm, changes = mark_frontmatter(
        fm=fm,
        role=spec.role,
        load_mode=spec.load_mode,
        family=spec.family,
    )

    yaml_path = d / "agents" / "openai.yaml"
    oa_change = None

    if spec.load_mode == "auto":
        if yaml_path.exists():
            if apply:
                yaml_path.unlink()
                oa_change = "删除残留 agents/openai.yaml（auto 加载）"
            else:
                oa_change = "将删除残留 agents/openai.yaml（auto 加载）"
    elif spec.load_mode == "manual":
        want = gen_openai_yaml(spec.name, extract_description(fm))
        have = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else None
        if have != want:
            oa_change = "生成/刷新 agents/openai.yaml"
            if apply:
                yaml_path.parent.mkdir(parents=True, exist_ok=True)
                yaml_path.write_text(want, encoding="utf-8")

    tag_scope = f"{spec.family or 'standalone'}/{spec.role}"
    if changes or oa_change:
        tag = "写入" if apply else "计划"
        logs.append(f"[{tag}] {spec.name}（{tag_scope}，{spec.load_mode}）")
        for c in changes:
            logs.append(f"    - {c}")
        if oa_change:
            logs.append(f"    - {oa_change}")
        if apply and changes:
            skill_md.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")
        return 1, logs
    else:
        logs.append(f"[一致] {spec.name}（{tag_scope}）")
        return 0, logs


def main() -> None:
    ap = argparse.ArgumentParser(description="按 agent-map.yaml families 与 standalone 段幂等标记族群元数据")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()

    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    fams = m.get("families") or {}
    standalone = m.get("standalone") or {}
    if not fams and not standalone:
        sys.exit("agent-map.yaml 无 families 或 standalone 段")

    try:
        specs = list(iter_members(m, root=ROOT))
    except FarmModelError as err:
        sys.exit(f"🚨 农场模型校验失败: {err}")

    n_changed = 0
    for spec in specs:
        changed, logs = apply_skill(spec, apply=args.apply)
        n_changed += changed
        for line in logs:
            print(line)

    suffix = "" if args.apply else "（加 --apply 执行）"
    print(f"\n{'已写入' if args.apply else 'dry-run 计划'}: {n_changed} 个技能需要变更{suffix}")


if __name__ == "__main__":
    main()
