#!/usr/bin/env python3
"""plugin-catalog — 从 agent-map.yaml 生成 .agent-plugin/marketplace.json（声明层）。

定位（2026-09-24 W3，设计见 docs/specs/SKILL-MANAGEMENT.md）：本机五农场继续走
skills-sync 软链（唯一通道）；marketplace.json 只是**可选的对外声明层**——让
Claude Code / Codex / Grok 能以原生 `/plugins` 浏览器发现本仓库技能，供外发或
插件化安装使用。生成物进 git（标注 DO NOT EDIT），不产生任何副本。

- 插件名一律 `dc-<族群>` 前缀（P8 命名去品牌化 + 避免与 App 自带技能重名）
- 每个插件 = 一个族群的全部技能（入口 + 成员），source 指向仓库根，skills 为
  平铺技能目录（Claude skill-bundle 模式：strict:false + skills 数组）
- `.claude-plugin/` 是指向 `.agent-plugin/` 的兼容软链（Codex/Grok 当前只认前者）

用法：
  cd ~/projects/dc-skills && uv run python scripts/plugin-catalog.py          # 生成（dry-run）
  uv run python scripts/plugin-catalog.py --apply                             # 写入
  uv run python scripts/plugin-catalog.py --check                             # 校验已提交文件与生成一致
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "agent-map.yaml"
OUT = ROOT / ".agent-plugin" / "marketplace.json"

HEADER_NOTE = ("由 scripts/plugin-catalog.py 从 agent-map.yaml 生成，DO NOT EDIT；"
               "改族群请改 agent-map 后重跑 --apply")


def build(m: dict) -> dict:
    base = list(m.get("base") or [])
    on_demand = list(m.get("on_demand") or [])
    plugins = []
    for fname, f in sorted((m.get("families") or {}).items()):
        entry = f.get("entry")
        members = list(f.get("members") or [])
        skills = [entry] + [x for x in members if x != entry]
        # on_demand 入口（如 dsh-plugin-troubleshooting）也随族声明，安装时按需
        plugins.append({
            "name": f"dc-{fname}",
            "description": f"{fname} 族群：入口 {entry}"
                           + (f" + {len(members)} 个 manual 成员" if members else "（单体族）"),
            "source": ".",
            "strict": False,
            "skills": [f"./{s}" for s in skills],
        })
    covered = {s for p in plugins for s in (x.lstrip("./") for x in p["skills"])}
    orphans = [s for s in base + on_demand if s not in covered]
    if orphans:
        print(f"! 未归族的技能（不会进 marketplace）: {orphans}", file=sys.stderr)
    return {"name": "dc-skills",
            "description": "dc-skills 技能农场：11 族群 / 34 技能的插件市场声明层"
                           "（本机走 skills-sync 软链，本文件供原生 /plugins 发现与外发）",
            "owner": {"name": "x-dc-coder"},
            "x-generated-by": HEADER_NOTE,
            "plugins": plugins}


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 .agent-plugin/marketplace.json")
    ap.add_argument("--apply", action="store_true", help="写入（默认 dry-run）")
    ap.add_argument("--check", action="store_true", help="校验已提交文件与生成一致（CI）")
    args = ap.parse_args()

    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    catalog = build(m)
    text = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"🚨 缺少 {OUT}（跑 --apply 生成）")
            sys.exit(1)
        have = OUT.read_text(encoding="utf-8")
        if have != text:
            print("🚨 marketplace.json 与 agent-map.yaml 不一致（重跑 --apply）")
            sys.exit(1)
        print(f"✅ marketplace.json 与 agent-map.yaml 一致（{len(catalog['plugins'])} 个插件）")
        sys.exit(0)

    print(f"📋 marketplace 计划（dry-run）：{len(catalog['plugins'])} 个插件")
    for p in catalog["plugins"]:
        print(f"  - {p['name']:<18} skills={p['skills']}")
    if not args.apply:
        print("\n加 --apply 写入 .agent-plugin/marketplace.json")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    compat = ROOT / ".claude-plugin"
    if not compat.is_symlink() and not compat.exists():
        compat.symlink_to(".agent-plugin", target_is_directory=True)
        print("🔗 .claude-plugin -> .agent-plugin（兼容软链已建）")
    print(f"✅ 已写入 {OUT}")


if __name__ == "__main__":
    main()
