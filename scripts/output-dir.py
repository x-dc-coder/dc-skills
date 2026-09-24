#!/usr/bin/env python3
"""output-dir — 打印统一产物树的运行目录（供 bash 技能脚本调用）。

用法：
  uv run python scripts/output-dir.py <family> <skill-name> [--sub <子目录>]
  # 例：uv run python scripts/output-dir.py drawing diagram-draft
  # 输出：/home/dc/projects/MyThesis/skills-output/drawing/diagram-draft/20260924-143022

docs/specs/OUTPUT.md C-1：bash 技能脚本无法 import common.py，用本 CLI 取运行目录。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import run_dir  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="打印统一产物树运行目录")
    ap.add_argument("family", help="族群键（agent-map.yaml families）")
    ap.add_argument("skill", help="技能名/子类型目录名")
    ap.add_argument("--sub", default=None, help="运行目录下的子目录（如 downloads）")
    args = ap.parse_args()
    d = run_dir(args.family, args.skill)
    if args.sub:
        d = d / args.sub
        d.mkdir(parents=True, exist_ok=True)
    print(d)


if __name__ == "__main__":
    main()
