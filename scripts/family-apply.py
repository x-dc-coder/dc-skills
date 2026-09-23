#!/usr/bin/env python3
"""family-apply — 按 agent-map.yaml families 段幂等标记族群元数据。

语义（2026-09-24 族群化，见 docs/specs/SKILL-MANAGEMENT.md）：
- entry：SKILL.md metadata 补 family / role: entry / load-mode: auto（进模型启动清单）
- member：上述 + role: member + load-mode: manual + disable-model-invocation: true
  （CC/Grok/dsh 同键 kebab-case），并生成 agents/openai.yaml 的
  policy.allow_implicit_invocation: false（Codex 侧 manual-only，仍可 $name 显式调用）
- 顺带清理存量违规：顶层 version: → metadata.version（字符串，B3a）；顶层 whenToUse:
  整行删除（触发信息以 description 为准，B3）

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


def mark_frontmatter(fm: str, family: str, role: str) -> tuple[str, list[str]]:
    """文本级改造 frontmatter，返回 (新 fm, 变更说明)。幂等。"""
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

    # metadata 合并
    want = {"family": family, "role": role, "load-mode": "auto" if role == "entry" else "manual"}
    if version_val:
        want["version"] = version_val
    meta_idx = next((n for n, ln in enumerate(lines) if re.match(r"^metadata:", ln)), None)
    if meta_idx is None:
        if not any(re.match(r"^\s{2}family:", ln) for ln in lines):
            block = ["metadata:"] + [f"  {k}: {v}" for k, v in want.items()]
            lines = lines + block
            changes.append("新增 metadata 块（family/role/load-mode）")
    else:
        head = lines[meta_idx]
        m_inline = re.match(r"^metadata:\s*\{([^}]*)\}\s*$", head)
        existing = {re.match(r"^\s+(\S+?):", ln).group(1) for ln in lines[meta_idx + 1:]
                    if re.match(r"^\s+\S+?:", ln)} if not m_inline else set()
        if m_inline:
            inner = [p.strip() for p in m_inline.group(1).split(",") if p.strip()]
            existing = {p.split(":", 1)[0].strip() for p in inner}
            if not inner:
                lines[meta_idx:meta_idx + 1] = ["metadata:"] + [f"  {k}: {v}" for k, v in want.items()]
                changes.append("metadata: {} → 块形式（family/role/load-mode）")
            else:
                merged = dict(p.split(":", 1) for p in inner)
                merged.update({k: str(v) for k, v in want.items()})
                lines[meta_idx:meta_idx + 1] = ["metadata:"] + [
                    f"  {k}: {v}" for k, v in merged.items()]
                changes.append("metadata 行内 map → 块形式并合并族群键")
        else:
            add = [(k, v) for k, v in want.items() if k not in existing]
            if add:
                lines[meta_idx + 1:meta_idx + 1] = [f"  {k}: {v}" for k, v in add]
                changes.append(f"metadata 补键: {', '.join(k for k, _ in add)}")

    # member：disable-model-invocation（顶层，kebab）
    if role == "member" and not any(ln.strip() == "disable-model-invocation: true" for ln in lines):
        lines.append("disable-model-invocation: true")
        changes.append("新增 disable-model-invocation: true（CC/Grok/dsh manual-only）")

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


def main() -> None:
    ap = argparse.ArgumentParser(description="按 families 段幂等标记族群元数据")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()

    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    fams = m.get("families") or {}
    if not fams:
        sys.exit("agent-map.yaml 无 families 段")

    n_changed = 0
    for fname, f in sorted(fams.items()):
        entry, members = f.get("entry"), list(f.get("members") or [])
        for name, role in [(entry, "entry")] + [(x, "member") for x in members]:
            d = ROOT / name
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                print(f"🚨 [{fname}] {name}: SKILL.md 缺失")
                continue
            txt = skill_md.read_text(encoding="utf-8")
            fm, body, _ = split_frontmatter(txt)
            if not fm:
                print(f"🚨 [{fname}] {name}: 无 frontmatter（B1 违规）")
                continue
            new_fm, changes = mark_frontmatter(fm, fname, role)
            new_txt = f"---\n{new_fm}\n---{body}" if changes else txt

            # Codex openai.yaml（仅 member）
            yaml_path = d / "agents" / "openai.yaml"
            oa_change = None
            if role == "member":
                want = gen_openai_yaml(name, extract_description(fm))
                have = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else None
                if have != want:
                    oa_change = "生成/刷新 agents/openai.yaml"
                    if args.apply:
                        yaml_path.parent.mkdir(parents=True, exist_ok=True)
                        yaml_path.write_text(want, encoding="utf-8")

            if changes or oa_change:
                n_changed += 1
                tag = "写入" if args.apply else "计划"
                print(f"[{tag}] {name}（{fname}/{role}）")
                for c in changes:
                    print(f"    - {c}")
                if oa_change:
                    print(f"    - {oa_change}")
                if args.apply and changes:
                    skill_md.write_text(new_txt, encoding="utf-8")
            else:
                print(f"[一致] {name}（{fname}/{role}）")

    suffix = "" if args.apply else "（加 --apply 执行）"
    print(f"\n{'已写入' if args.apply else 'dry-run 计划'}: {n_changed} 个技能需要变更{suffix}")


if __name__ == "__main__":
    main()
