#!/usr/bin/env python3
"""new_skill — dc-skill-creator 脚手架生成器（形式正确的最小骨架）。

生成内容（--apply 才落盘；默认 dry-run 打印计划）：
  <name>/SKILL.md          frontmatter 占位（name/description/metadata.version/
                           family/role/load-mode）+ heading 骨架（对齐 B8）
  <name>/scripts/          __init__.py（A 类必需）+ cli.py 桩（含 --output 与
                           resolve_output_path 接线，对齐 OUTPUT.md C-1/C-3）
  <name>/scripts/test_*.py pytest 桩（R13）
  <name>/.venv -> ../.venv（A 类；规则 A1）
登记（文本级最小侵入，保留原注释缩进）：
  agent-map.yaml 的 on_demand/base/某 agent extra/families.<族>.members

用法：
  uv run python dc-skill-creator/scripts/new_skill.py my-skill \
      --family drawing --env A --desc "做 X。当用户需要 Y 时使用。"
  uv run python dc-skill-creator/scripts/new_skill.py my-skill ... --apply
生成后必须：family-apply --apply（成员标记）→ validate.py（自过检）→ skills-sync。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import MASTER_ROOT  # noqa: E402

SKILL_TEMPLATE = """---
name: {name}
description: >
  {desc}
metadata:
  version: "0.1"
{family_block}---

# {title}

## 能力概述

{desc}

## 触发场景

<!-- 补触发词（B2：祈使句 Use when…/当用户…时；关键触发词前置）-->

## 依赖说明

<!-- 外部二进制登记 docs/arch/ENVIRONMENT.md 后在此 metadata.requires-bins 声明（R6）-->

## 快速用法

```bash
cd ~/projects/dc-skills && uv run python {name}/scripts/cli.py --help
```

## 详细规程

<!-- 超过 400 行前拆到 references/（B7a）；产物路径遵循 docs/specs/OUTPUT.md -->
"""

CLI_TEMPLATE = '''#!/usr/bin/env python3
"""{name} CLI 桩（由 dc-skill-creator 生成；接线 OUTPUT.md 两级回退）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import fallback_output_dir, resolve_output_path  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="{desc}")
    ap.add_argument("input", nargs="?", help="输入文件（绝对路径时按 OUTPUT.md C-2 推断项目根）")
    ap.add_argument("--output", help="显式输出路径/目录（最高优先，OUTPUT.md）")
    args = ap.parse_args()
    # TODO(实现): 调用 resolve_output_path(args.input, "{name}", "<default>.png")
    raise SystemExit("未实现：替换为真实逻辑")


if __name__ == "__main__":
    main()
'''

TEST_TEMPLATE = '''"""Tests for {name} CLI（由 dc-skill-creator 生成）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parent / "cli.py"


def test_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
'''


def title_of(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def insert_into_list(yaml_text: str, key: str, entry: str, comment: str) -> str:
    """把 `  - entry   # comment` 追加到顶层 key 的块列表末尾（保留注释）。"""
    lines = yaml_text.split("\n")
    start = next((n for n, ln in enumerate(lines) if ln == f"{key}:"), None)
    if start is None:
        raise SystemExit(f"agent-map.yaml 找不到顶层键: {key}")
    end = start + 1
    while end < len(lines) and (lines[end].startswith("  ") or lines[end].strip() == ""):
        if lines[end].strip() == "" and end + 1 < len(lines) and not lines[end + 1].startswith("  "):
            break
        end += 1
    # 回退到列表最后一个元素
    while end > start + 1 and not lines[end - 1].lstrip().startswith("- "):
        end -= 1
    new_line = f"  - {entry}   # {comment}"
    lines.insert(end, new_line)
    return "\n".join(lines)


def insert_extra(yaml_text: str, agent: str, entry: str) -> str:
    lines = yaml_text.split("\n")
    a = next((n for n, ln in enumerate(lines) if ln == f"  {agent}:"), None)
    if a is None:
        raise SystemExit(f"agent-map.yaml 找不到 agent: {agent}")
    e = next((n for n in range(a, min(a + 12, len(lines))) if lines[n].strip() == "extra: []"), None)
    if e is not None:
        indent = " " * (len(lines[e]) - len(lines[e].lstrip()))
        lines[e:e + 1] = [f"{indent}extra:", f"{indent}  - {entry}"]
        return "\n".join(lines)
    e = next((n for n in range(a, min(a + 12, len(lines))) if lines[n].strip() == "extra:"), None)
    if e is None:
        raise SystemExit(f"agent {agent} 无 extra 键")
    end = e + 1
    while end < len(lines) and lines[end].startswith("      "):
        end += 1
    lines.insert(end, f"      - {entry}")
    return "\n".join(lines)


def insert_family_member(yaml_text: str, family: str, entry: str) -> str:
    lines = yaml_text.split("\n")
    f = next((n for n, ln in enumerate(lines) if ln == f"  {family}:"), None)
    if f is None:
        raise SystemExit(f"families 中找不到族群: {family}")
    m = next((n for n in range(f, min(f + 8, len(lines))) if lines[n].strip().startswith("members:")), None)
    if m is None:
        raise SystemExit(f"族群 {family} 无 members 行")
    line = lines[m]
    if line.strip() == "members: []":
        lines[m] = re.sub(r"members: \[\]", f"members: [{entry}]", line)
        return "\n".join(lines)
    if line.strip().endswith("]"):
        lines[m] = line[: line.rfind("]")] + f", {entry}]"
        return "\n".join(lines)
    raise SystemExit(f"族群 {family} 的 members 是非常规形式，请手工登记: {line!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="技能骨架生成器")
    ap.add_argument("name", help="技能名（kebab-case，将同时作为目录名）")
    ap.add_argument("--desc", required=True, help="一段话：能力 + 触发场景（≤1024 字符）")
    ap.add_argument("--family", help="归属族群（agent-map families 的键；缺省=standalone）")
    ap.add_argument("--env", choices=["A", "B", "C"], default="C", help="环境分类（默认 C）")
    ap.add_argument("--tier", choices=["on_demand", "base"], default="on_demand", help="登记层级")
    ap.add_argument("--agent", help="改为登记到某 agent 的 extra（如 dsh）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()

    name = args.name
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        raise SystemExit(f"name 不合规（R2）: {name}")
    d = MASTER_ROOT / name
    if d.exists():
        raise SystemExit(f"目录已存在: {d}")
    if len(args.desc) > 1024:
        raise SystemExit(f"description 超 1024 字符: {len(args.desc)}（R3）")

    family_block = ""
    if args.family:
        family_block = f'  family: {args.family}\n  role: member\n  load-mode: manual\n'
    else:
        family_block = '  role: standalone\n  load-mode: auto\n'

    plan: list[str] = []
    files: dict[Path, str] = {
        d / "SKILL.md": SKILL_TEMPLATE.format(
            name=name, desc=args.desc, family_block=family_block, title=title_of(name)),
    }
    if args.env in ("A", "B"):
        files[d / "scripts" / "__init__.py"] = ""
        files[d / "scripts" / "cli.py"] = CLI_TEMPLATE.format(name=name, desc=args.desc)
        files[d / "scripts" / f"test_{name.replace('-', '_')}.py"] = TEST_TEMPLATE.format(name=name)
    plan.append(f"SKILL.md（env {args.env}，family={args.family or 'standalone'}）")
    if args.env in ("A", "B"):
        plan.append("scripts/__init__.py + cli.py（--output 已接线）+ test 桩")
        if args.env == "A":
            plan.append(".venv -> ../.venv 软链")

    manifest_path = MASTER_ROOT / "agent-map.yaml"
    manifest = manifest_path.read_text(encoding="utf-8")
    if args.agent:
        manifest = insert_extra(manifest, args.agent, name)
        plan.append(f"agent-map.yaml: agents.{args.agent}.extra += {name}")
    else:
        manifest = insert_into_list(manifest, args.tier, name, args.desc[:40])
        plan.append(f"agent-map.yaml: {args.tier} += {name}")
    if args.family:
        manifest = insert_family_member(manifest, args.family, name)
        plan.append(f"agent-map.yaml: families.{args.family}.members += {name}")

    print(f"📋 新建技能计划: {name}（dry-run，加 --apply 写入）")
    for p in plan:
        print(f"  - {p}")
    print("\n后续必须执行：")
    print("  1) uv run python scripts/family-apply.py --apply   # 成员标记+openai.yaml")
    print("  2) uv run python dc-skill-creator/scripts/validate.py " + name)
    print("  3) uv run python scripts/skills-sync              # 物化农场")
    if not args.apply:
        return

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if args.env == "A":
        (d / ".venv").symlink_to("../.venv")
    manifest_path.write_text(manifest, encoding="utf-8")
    print(f"\n✅ 已生成 {d}（{len(files)} 个文件）+ agent-map.yaml 登记")


if __name__ == "__main__":
    main()
