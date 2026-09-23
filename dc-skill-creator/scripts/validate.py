#!/usr/bin/env python3
"""validate — dc-skill-creator 硬校验器（单技能契约 + 增量关系）。

规则分级：E = 错误（阻断），W = 警告（不阻断，--Werror 升级为失败）。
退出码：0 全过；2 存在 E 级违规；3 仅 W 级；4 环境/配置错误。

规则表（编号与 docs/specs/SKILL-AUTHORING-RULES.md 对应）：
  R1(E)  frontmatter 仅规范 6 字段 + metadata；禁 CC 私有字段；
         disable-model-invocation 仅在 metadata.load-mode: manual 时允许（B3 例外）
  R2(E)  name 规则（^[a-z0-9-]+$/无首尾-/无--/≤64/==目录名）且全仓+App 根唯一
  R3(E)  description ≤1024、前 80 字符含能力关键词、无尖括号、无 [TODO:]；
         (W) 含触发场景句式（当用户…时 / Use when）
  R4(E)  SKILL.md ≤400 行（B7a）
  R5(E)  A 类：scripts/__init__.py + .venv→主库 .venv；(W) 含 .py 但缺 __init__.py
  R6(E)  metadata.requires-bins 的外部二进制必须已登记 docs/arch/ENVIRONMENT.md
  R7(W)  import 的第三方包应在 pyproject.toml（误报交人工）
  R8(E)  必须登记进 agent-map.yaml（base/on_demand/extra/preset 之一）
  R9(W)  与现有技能触发词冲突（Jaccard Top-5）；兜底技能须含 B6 优先级声明
  R10(E) 禁硬编码 /tmp/skills-output；(W) 有产物文件但无 --output
  R11(E) 无 -workspace/ 孤儿目录、无 Zone.Identifier 污染文件
  R12(E) 正文无 [TODO: ...] 占位（代码围栏感知）
  R13(W) 含 scripts/*.py 应有 scripts/test_*.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import MASTER_ROOT  # noqa: E402

try:
    import yaml
except ImportError:
    sys.exit("需要 pyyaml（uv run python ...）")

#: 客户端私有字段（B3 禁止；disable-model-invocation 是唯一例外，见 R1）
PRIVATE_FIELDS = {
    "when_to_use", "whenToUse", "context", "model", "paths", "hooks",
    "argument-hint", "argumentHint", "disableModelInvocation", "userInvocable",
    "user-invocable-exception",
}
#: agentskills.io 规范字段全集
SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
#: App 自管根（R2 重名检查用；与 skillctl inventory 同源，见 SKILL-MANAGEMENT.md §1）
APP_OWNED_ROOTS = [
    "~/.grok/bundled/skills",
    "~/.codex/skills/.system",
    "~/.codex/plugins/cache",
    "~/.claude/plugins/cache",
]

TRIGGER_HINT = re.compile(r"(当用户|使用时?触发|Use when|Trigger)", re.I)


def split_frontmatter(txt: str) -> tuple[str, str]:
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)", txt)
    if not m:
        return "", txt
    return m.group(1), txt[m.end():]


def registered_names() -> set[str]:
    m = yaml.safe_load((MASTER_ROOT / "agent-map.yaml").read_text(encoding="utf-8"))
    names = set(m.get("base") or []) | set(m.get("on_demand") or [])
    for cfg in (m.get("agents") or {}).values():
        names |= set(cfg.get("extra") or [])
        for preset in (cfg.get("presets") or {}).values():
            names |= set(preset.get("skills") or [])
    return names


def app_root_names() -> dict[str, list[str]]:
    out = {}
    for raw in APP_OWNED_ROOTS:
        root = Path(os.path.expanduser(raw))
        if root.is_dir():
            hits = [p.parent.name for p in root.rglob("SKILL.md")]
            if hits:
                out[raw] = sorted(set(hits))
    return out


def registered_bins() -> set[str]:
    env_md = MASTER_ROOT / "docs" / "arch" / "ENVIRONMENT.md"
    if not env_md.exists():
        env_md = MASTER_ROOT / "ENVIRONMENT.md"
    bins: set[str] = set()
    if env_md.exists():
        for line in env_md.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\|\s*`?([A-Za-z0-9_.-]+)`?\s*\|", line)
            if m and not line.startswith("|---"):
                bins.add(m.group(1))
    return bins


def check(skill_dir: Path) -> list[dict]:
    """返回违规列表：[{rule, level, msg}]。"""
    issues: list[dict] = []

    def bad(rule: str, level: str, msg: str) -> None:
        issues.append({"rule": rule, "level": level, "msg": msg})

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [{"rule": "B1", "level": "E", "msg": "SKILL.md 不存在"}]
    txt = skill_md.read_text(encoding="utf-8", errors="replace")
    fm_text, body = split_frontmatter(txt)
    if not fm_text:
        bad("B1", "E", "SKILL.md 必须以 YAML frontmatter 开头")
        return issues
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        bad("B1", "E", f"frontmatter YAML 解析失败: {e}")
        return issues
    if not isinstance(fm, dict):
        bad("B1", "E", "frontmatter 必须是映射")
        return issues

    # R1 字段白名单
    extra_fields = set(fm) - SPEC_FIELDS
    private_hits = sorted(extra_fields & PRIVATE_FIELDS)
    if private_hits:
        bad("R1", "E", f"CC 私有字段禁止出现在顶层: {private_hits}（B3）")
    other = sorted(extra_fields - PRIVATE_FIELDS - {"version", "disable-model-invocation"})
    if other:
        bad("R1", "W", f"非规范顶层字段: {other}（各端忽略但不可移植）")
    if "version" in fm:
        bad("R1", "W", "顶层 version 非规范字段，应移入 metadata.version")
    meta = fm.get("metadata") or {}
    if meta and not isinstance(meta, dict):
        bad("R1", "E", "metadata 必须是 string→string map")
        meta = {}
    load_mode = meta.get("load-mode")
    dmi = fm.get("disable-model-invocation")
    if dmi is not None:
        if dmi is True and load_mode == "manual":
            pass  # B3 例外：manual 技能允许双写
        elif dmi is True:
            bad("R1", "E", "disable-model-invocation: true 仅允许与 metadata.load-mode: manual 同用")
        else:
            bad("R1", "W", f"disable-model-invocation 应为 true 或省略（当前 {dmi!r}）")
    if load_mode == "manual" and dmi is not True:
        bad("R1", "E", "load-mode: manual 必须双写 disable-model-invocation: true（B3 例外）")

    # R2 name 规则 + 唯一性
    name = fm.get("name")
    if not name:
        bad("R2", "E", "缺少 name")
    else:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(name)):
            bad("R2", "E", f"name 违规: {name!r}（须 ^[a-z0-9]+(-[a-z0-9]+)*$）")
        if len(str(name)) > 64:
            bad("R2", "E", f"name 超 64 字符: {len(name)}")
        if str(name) != skill_dir.name:
            bad("R2", "E", f"name({name}) != 目录名({skill_dir.name})（规范硬约束）")
        peers = {p.name for p in MASTER_ROOT.iterdir()
                 if p.is_dir() and (p / "SKILL.md").exists()} - {skill_dir.name}
        peers |= {p.name for p in (MASTER_ROOT / "archive").iterdir()
                  if p.is_dir()} if (MASTER_ROOT / "archive").is_dir() else set()
        if str(name) in peers:
            bad("R2", "E", f"name 与主库/archive 现有技能重名: {name}")
        for root, hits in app_root_names().items():
            if str(name) in hits:
                bad("R2", "W", f"name 与 App 自管根技能重名（{root}）: {name}")

    # R3 description
    desc = fm.get("description")
    if not desc:
        bad("R3", "E", "缺少 description")
    else:
        desc = str(desc)
        if len(desc) > 1024:
            bad("R3", "E", f"description 超 1024 字符: {len(desc)}")
        if "<" in desc or ">" in desc:
            bad("R3", "E", "description 含尖括号（各端可能解析失败）")
        if "[TODO" in desc:
            bad("R3", "E", "description 含 [TODO:] 占位")
        head = desc[:80]
        if not re.search(r"[一-龥a-zA-Z]", head):
            bad("R3", "E", "description 前 80 字符无可读内容")
        if not TRIGGER_HINT.search(desc):
            bad("R3", "W", "description 未见触发场景句式（当用户…时 / Use when）")

    # R4 行数门禁
    lines = txt.count("\n") + (0 if txt.endswith("\n") else 1)
    if lines > 400:
        heads = [ln for ln in body.splitlines() if re.match(r"^#{1,3} ", ln)][:3]
        bad("R4", "E", f"SKILL.md {lines} 行 > 400 门禁（应拆 references/；当前顶层标题: {heads}）")

    # R5 A 类环境
    scripts_dir = skill_dir / "scripts"
    has_py = scripts_dir.is_dir() and any(scripts_dir.glob("*.py"))
    if (skill_dir / ".venv").exists():
        v = skill_dir / ".venv"
        if not v.is_symlink() or Path(os.path.realpath(v)) != (MASTER_ROOT / ".venv").resolve():
            bad("R5", "E", ".venv 必须是指向主库 .venv 的软链")
        if has_py and not (scripts_dir / "__init__.py").exists():
            bad("R5", "E", "A 类技能缺 scripts/__init__.py（规则 A4）")
    elif has_py:
        if (skill_dir / "venvs").is_dir():
            pass  # B 类：skill 内私有重型 venvs（规则 A1/A2），不需要共享 .venv 软链
        else:
            bad("R5", "W", "含 .py 但无 .venv 软链也无 venvs/：确认是 C 类（系统 Python）并在 README 注明")

    # R6 外部二进制登记
    bins = meta.get("requires-bins")
    if bins:
        bin_list = [b.strip() for b in str(bins).split(",") if b.strip()]
        registered = registered_bins()
        for b in bin_list:
            if b not in registered:
                bad("R6", "E", f"外部二进制未登记 docs/arch/ENVIRONMENT.md: {b}")

    # R7 import 对照 pyproject（启发；B 类私有 venv 不参与）
    if has_py and not (skill_dir / "venvs").is_dir():
        pyproject = (MASTER_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        imports: set[str] = set()
        for py in scripts_dir.glob("*.py"):
            for m in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", py.read_text(encoding="utf-8", errors="replace"), re.M):
                imports.add(m.group(1))
        stdlib = set(sys.stdlib_module_names)
        # import 名 ≠ 发行包名的常见映射（pip 名写进 pyproject）
        alias = {"PIL": "Pillow", "docx": "python-docx", "bs4": "beautifulsoup4",
                 "yaml": "pyyaml", "cv2": "opencv-python", "sklearn": "scikit-learn"}
        # 本地模块：同目录脚本互引 + 顶层共享 common/scripts
        siblings = {p.stem for p in scripts_dir.glob("*.py")} | {"scripts", "common"}
        for mod in sorted(imports - stdlib - siblings):
            pkg = alias.get(mod, mod)
            if f'"{pkg}' not in pyproject and f"'{pkg}" not in pyproject and pkg not in pyproject:
                bad("R7", "W", f"import 的 {mod!r}（包 {pkg}）不在 pyproject.toml（误报可忽略）")

    # R8 agent-map 登记
    if str(name) not in registered_names():
        bad("R8", "E", "未登记 agent-map.yaml（base/on_demand/extra/preset 之一）——技能对任何 Agent 不可见")

    # R9 触发词冲突
    if desc:
        words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[一-龥]{2,4}", str(desc)))
        sims = []
        for other in sorted(MASTER_ROOT.glob("*/SKILL.md")):
            if other.parent.name == skill_dir.name:
                continue
            o_txt = other.read_text(encoding="utf-8", errors="replace")
            o_fm, _ = split_frontmatter(o_txt)
            try:
                o = yaml.safe_load(o_fm) or {}
            except yaml.YAMLError:
                continue
            o_desc = str(o.get("description") or "")
            o_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[一-龥]{2,4}", o_desc))
            if not o_words or not words:
                continue
            j = len(words & o_words) / len(words | o_words)
            if j >= 0.18:
                sims.append((round(j, 2), o.get("name", other.parent.name)))
        for j, other in sorted(sims, reverse=True)[:5]:
            bad("R9", "W", f"触发词与 {other} 相似度 {j}（确认分工或加 B6 优先级声明）")
        if any(k in str(desc) for k in ("通用", "兜底", "fallback")) and "仅当" not in str(desc):
            bad("R9", "W", "兜底/通用技能须含 B6 优先级声明（“仅当其他专用 skill 无法满足时使用”）")

    # R10 输出路径
    hard = re.findall(r"/tmp/skills-output", txt)
    if hard:
        bad("R10", "E", f"硬编码 /tmp/skills-output ×{len(hard)}（docs/specs/OUTPUT.md C-10）")
    if has_py and not re.search(r'"--output', "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in scripts_dir.glob("*.py"))):
        if re.search(r"\.(png|jpg|svg|md|json|docx|pdf|tex)\b", body):
            bad("R10", "W", "疑似有产物但 CLI 无 --output 参数（OUTPUT.md 实现条款）")

    # R11 孤儿目录与污染文件
    for p in skill_dir.rglob("*"):
        if p.is_dir() and p.name.endswith("-workspace"):
            bad("R11", "E", f"孤儿目录: {p.relative_to(skill_dir)}")
        if p.is_file() and "Zone.Identifier" in p.name:
            bad("R11", "E", f"Windows 污染文件: {p.relative_to(skill_dir)}")

    # R12 TODO 占位（代码围栏感知）
    in_fence = False
    for ln in body.splitlines():
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.search(r"\[TODO:", ln):
            bad("R12", "E", f"正文含 [TODO:] 占位: {ln.strip()[:60]}")
            break

    # R13 测试存在性
    if has_py and not list(scripts_dir.glob("test_*.py")):
        bad("R13", "W", "含 scripts/*.py 但无 scripts/test_*.py")

    return issues


def main() -> None:
    ap = argparse.ArgumentParser(description="dc-skill-creator 硬校验器（单技能）")
    ap.add_argument("target", help="技能名（主库下）或技能目录路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--Werror", action="store_true", help="W 级也判失败（退出码 3）")
    args = ap.parse_args()

    d = Path(args.target)
    skill_dir = d if d.is_dir() else MASTER_ROOT / args.target
    if not skill_dir.is_dir():
        print(f"技能目录不存在: {skill_dir}", file=sys.stderr)
        sys.exit(4)

    issues = check(skill_dir.resolve())
    e = [i for i in issues if i["level"] == "E"]
    w = [i for i in issues if i["level"] == "W"]

    if args.json:
        print(json.dumps({"skill": skill_dir.name, "errors": e, "warnings": w,
                          "exit": 2 if e else (3 if w and args.Werror else 0)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"🔍 {skill_dir.name}: E {len(e)} / W {len(w)}")
        for i in e + w:
            mark = "🚨" if i["level"] == "E" else "⚠️"
            print(f"  {mark} [{i['rule']}] {i['msg']}")
        if not issues:
            print("  ✅ 全部通过")

    if e:
        sys.exit(2)
    if w and args.Werror:
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
