#!/usr/bin/env python3
"""check_triggers — 创建前触发词冲突预检（访谈阶段用，技能目录还不存在时）。

与 validate.py 的 R9 互补：R9 是事后全量检查；本工具在写 SKILL.md 之前评估
候选 description 与现有 33 技能的重叠，返回 Top-5 相似技能与重叠词，供人工裁决。

用法：
  uv run python dc-skill-creator/scripts/check_triggers.py "候选 description 文本"
退出码：0 无显著重叠；1 有 ≥0.30 的相似（建议改写）；2 用法/环境错误。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import MASTER_ROOT  # noqa: E402

try:
    import yaml
except ImportError:
    sys.exit("需要 pyyaml")

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[一-龥]{2,4}")
#: 无区分度的通用词，不计入重叠（避免"技能/使用/用户"刷相似度）
STOPWORDS = {"skill", "SKILL", "use", "the", "and", "for", "when", "user", "使用", "技能", "用户", "当用户"}


def words_of(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text) if w not in STOPWORDS}


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cand = sys.argv[1]
    cw = words_of(cand)
    if not cw:
        print("候选 description 无可提取关键词")
        sys.exit(2)

    rows = []
    for other in sorted(MASTER_ROOT.glob("*/SKILL.md")):
        txt = other.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", txt)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        ow = words_of(str(fm.get("description") or ""))
        if not ow:
            continue
        inter = cw & ow
        j = len(inter) / len(cw | ow)
        if inter:
            rows.append((j, fm.get("name", other.parent.name), sorted(inter)[:8]))

    rows.sort(reverse=True)
    print(f"候选关键词 {len(cw)} 个；与现有技能重叠 Top-5：")
    worst = 0.0
    for j, name, inter in rows[:5]:
        worst = max(worst, j)
        flag = "🚨" if j >= 0.30 else ("⚠️" if j >= 0.18 else "·")
        print(f"  {flag} {name:<28} J={j:.2f}  重叠: {' '.join(inter)}")
    if not rows:
        print("  （无重叠）")
    if worst >= 0.30:
        print("\n🚨 存在高重叠技能：请差异化 description，或确认分工后在正文写明优先级（B5/B6）")
        sys.exit(1)
    print("\n✅ 无高重叠（≥0.18 的请人工确认分工）")
    sys.exit(0)


if __name__ == "__main__":
    main()
