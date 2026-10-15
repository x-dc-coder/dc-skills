#!/usr/bin/env python3
"""snapshot.py — 全机技能与资产状态快照生成器、原子持久化与 HTML 仪表盘渲染。

规格与契约：docs/specs/全资产治理.md §4.2、§4.3。
对接目标：
  1. 磁盘单向契约文件：~/.local/state/dc-skills/skills-snapshot.json（供 monitor-panel 只读消费）
  2. 离线可视化报表：skills-output/dashboard/skills-dashboard.html
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from farm_model import iter_members

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = Path(
    os.environ.get("XDG_STATE_HOME") or "~/.local/state"
).expanduser() / "dc-skills" / "skills-snapshot.json"
DEFAULT_HTML_PATH = ROOT / "skills-output" / "dashboard" / "skills-dashboard.html"


def get_skill_description(skill_path: Path) -> str:
    """提取 SKILL.md frontmatter 中的 description。"""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        return ""
    try:
        txt = skill_md.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", txt)
        if not m:
            return ""
        for line in m.group(1).splitlines():
            if line.strip().startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"\'')
                if desc in (">", "|", ">-", "|-"):
                    continue
                return desc
        import yaml
        data = yaml.safe_load(m.group(1)) or {}
        return str(data.get("description") or "").strip().replace("\n", " ")
    except Exception:
        return ""


def build_snapshot(
    rep: Any,
    started: float,
    *,
    offline: bool = False,
    root: Path | None = None,
    agent_map_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成全机技能状态快照字典（schema_version: 1）。"""
    r = ROOT if root is None else Path(root)

    master_commit = None
    try:
        master_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(r),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        pass

    duration_ms = round((time.time() - started) * 1000, 1)
    generated_at = datetime.now().astimezone().isoformat()

    if agent_map_data is None:
        import yaml
        agent_map_file = r / "agent-map.yaml"
        if agent_map_file.is_file():
            try:
                agent_map_data = yaml.safe_load(agent_map_file.read_text(encoding="utf-8")) or {}
            except Exception:
                agent_map_data = {}
        else:
            agent_map_data = {}

    specs = list(iter_members(agent_map_data, root=r))

    on_windows = os.name == "nt"
    agents: list[str] = []
    for a_name, a_cfg in (agent_map_data.get("agents") or {}).items():
        if a_cfg.get("platform") == "windows" and not on_windows:
            continue
        agents.append(a_name)

    load_modes = {
        "auto": sum(1 for s in specs if s.load_mode == "auto"),
        "manual": sum(1 for s in specs if s.load_mode == "manual"),
    }
    roles = {
        "entry": sum(1 for s in specs if s.role == "entry"),
        "member": sum(1 for s in specs if s.role == "member"),
        "standalone": sum(1 for s in specs if s.role == "standalone"),
    }
    counts = rep.counts() if hasattr(rep, "counts") else {"crit": 0, "warn": 0, "ok": 0}

    summary = {
        "skills_total": len(specs),
        "families_total": len(agent_map_data.get("families") or {}),
        "agents": agents,
        "load_modes": load_modes,
        "roles": roles,
        "doctor": {
            "crit": counts.get("crit", 0),
            "warn": counts.get("warn", 0),
            "ok": counts.get("ok", 0),
        },
    }

    # 提取族群结构定义
    families_data: list[dict[str, Any]] = []
    for f_name, f_cfg in sorted((agent_map_data.get("families") or {}).items()):
        req = f_cfg.get("requires") or {}
        cli_reqs = []
        for x in (req.get("cli") or []):
            cli_reqs.append(x.get("name") if isinstance(x, dict) else str(x))
        mcp_reqs = [str(x) for x in (req.get("mcp") or [])]
        raw_m = f_cfg.get("members")
        member_names = list(raw_m.keys()) if isinstance(raw_m, dict) else list(raw_m or [])
        families_data.append({
            "name": f_name,
            "entry": f_cfg.get("entry"),
            "members": member_names,
            "deps": {"cli": cli_reqs, "mcp": mcp_reqs},
        })

    base_skills = set(agent_map_data.get("base") or [])
    skills_list: list[dict[str, Any]] = []

    for s in specs:
        mounts: dict[str, str] = {}
        for a_name in agents:
            a_cfg = (agent_map_data.get("agents") or {}).get(a_name) or {}
            a_skills = a_cfg.get("skills")
            if a_skills == "base":
                expected_set = set(base_skills)
            elif isinstance(a_skills, list):
                expected_set = set(a_skills)
            else:
                expected_set = set()
            expected_set.update(a_cfg.get("extra") or [])

            is_expected = s.name in expected_set
            a_dir_raw = a_cfg.get("dir")
            is_present = False
            if a_dir_raw:
                try:
                    a_dir = Path(os.path.expanduser(a_dir_raw))
                    target = a_dir / s.name
                    is_present = target.is_symlink() or target.exists()
                except Exception:
                    is_present = False

            if is_expected and is_present:
                mounts[a_name] = "mounted"
            elif is_expected and not is_present:
                mounts[a_name] = "missing"
            elif not is_expected and is_present:
                mounts[a_name] = "extra"
            else:
                mounts[a_name] = "unmounted"

        fam_cfg = (agent_map_data.get("families") or {}).get(s.family) or {}
        req = fam_cfg.get("requires") or {}
        cli_reqs = req.get("cli") or []
        cli_list: list[str] = []
        for item in cli_reqs:
            if isinstance(item, dict) and "name" in item:
                cli_list.append(str(item["name"]))
            elif isinstance(item, str):
                cli_list.append(item)
        mcp_list = [str(x) for x in (req.get("mcp") or [])]
        deps = {"cli": cli_list, "mcp": mcp_list}

        last_commit = None
        try:
            out = subprocess.check_output(
                ["git", "log", "-n", "1", "--format=%h %cs", "--", s.name],
                cwd=str(r),
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            if out:
                last_commit = out
        except Exception:
            pass

        items = getattr(rep, "items", [])
        matched = [i for i in items if i.get("name") == s.name]
        crit_item = next((i for i in matched if i.get("level") == "crit"), None)
        warn_item = next((i for i in matched if i.get("level") == "warn"), None)
        if crit_item:
            status = "crit"
            status_hint = crit_item.get("msg", "契约异常")
        elif warn_item:
            status = "warn"
            status_hint = warn_item.get("msg", "存在警告")
        else:
            status = "ok"
            status_hint = "正常"

        desc = get_skill_description(s.skill_path)

        skills_list.append({
            "name": s.name,
            "family": s.family,
            "role": s.role,
            "load_mode": s.load_mode,
            "mounts": mounts,
            "deps": deps,
            "last_commit": last_commit,
            "status": status,
            "status_hint": status_hint,
            "description": desc,
        })

    return {
        "schema_version": 1,
        "meta": {
            "generated_at": generated_at,
            "duration_ms": duration_ms,
            "producer": "assets-doctor",
            "master_commit": master_commit,
            "offline": offline,
        },
        "summary": summary,
        "families": families_data,
        "skills": skills_list,
    }


def export_snapshot(doc: dict[str, Any], target_path: Path | str | None = None) -> Path:
    """原子写入 JSON 快照文件（.tmp.<pid> + os.replace）。"""
    dest = Path(target_path or DEFAULT_SNAPSHOT_PATH).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return dest


def render_html_dashboard(doc: dict[str, Any], output_path: Path | str | None = None) -> Path:
    """生成完全自包含、美观现代的单文件 HTML 技能驾驶舱（零 CDN、零外部网络依赖）。"""
    out = Path(output_path or DEFAULT_HTML_PATH).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    meta = doc.get("meta") or {}
    summary = doc.get("summary") or {}
    doctor = summary.get("doctor") or {}
    load_modes = summary.get("load_modes") or {}

    raw_ts = meta.get("generated_at") or "—"
    try:
        cleaned_ts = raw_ts.replace("T", " ").split(".")[0]
    except Exception:
        cleaned_ts = raw_ts

    master_commit = meta.get("master_commit") or "—"
    duration_str = f"{meta.get('duration_ms', 0):.0f} ms"

    doc_json = json.dumps(doc, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>dc-skills · 全机技能资产驾驶舱</title>
  <style>
    :root {{
      --bg: #09090b;
      --card-bg: #121215;
      --card-hover: #18181b;
      --border: rgba(255, 255, 255, 0.07);
      --border-subtle: rgba(255, 255, 255, 0.04);
      --text: #f4f4f5;
      --text-muted: #71717a;
      --primary: #f4f4f5;
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.12);
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.12);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.12);
      --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px 20px;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{ max-width: 1340px; margin: 0 auto; }}

    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
      gap: 12px;
    }}
    .title-group {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .title-group h1 {{
      font-size: 20px;
      font-weight: 600;
      letter-spacing: -0.02em;
      color: #fff;
      margin: 0;
    }}
    .badge-sub {{
      font-size: 11px;
      font-family: var(--font-mono);
      background: rgba(255, 255, 255, 0.06);
      color: #a1a1aa;
      padding: 2px 7px;
      border-radius: 4px;
      font-weight: 500;
      border: 1px solid var(--border);
    }}
    .meta-bar {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .meta-pill {{
      background: #121215;
      border: 1px solid rgba(255, 255, 255, 0.07);
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-family: var(--font-mono);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
    }}
    .meta-pill strong {{
      color: #e4e4e7;
      font-weight: 500;
    }}

    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 18px;
    }}
    .card .label {{ font-size: 12px; color: var(--text-muted); margin-bottom: 6px; font-weight: 500; }}
    .card .val {{
      font-size: 28px;
      font-weight: 600;
      letter-spacing: -0.02em;
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      color: #f4f4f5;
      display: flex;
      align-items: baseline;
      gap: 6px;
    }}
    .card .sub {{ font-size: 12px; color: #71717a; font-weight: 400; font-family: -apple-system, sans-serif; }}

    .kpi-doctor {{
      font-size: 13px;
      font-weight: 500;
      display: flex;
      gap: 14px;
      align-items: baseline;
      margin-top: 2px;
      font-family: var(--font-mono);
    }}
    .kpi-dot-item {{
      display: inline-flex;
      align-items: baseline;
      gap: 5px;
    }}
    .kpi-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      display: inline-block;
      align-self: center;
    }}
    .kpi-dot-item strong {{
      font-size: 26px;
      font-weight: 600;
      line-height: 1;
      font-variant-numeric: tabular-nums;
      margin-right: 2px;
    }}
    .kpi-dot-item .kpi-unit {{
      font-size: 12px;
      color: #71717a;
      font-weight: 400;
      font-family: -apple-system, sans-serif;
    }}

    .toolbar {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 12px;
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: nowrap;
      overflow-x: auto;
    }}
    .tool-group-left {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }}
    .search-box {{
      width: 220px;
      max-width: 240px;
      height: 32px;
      background: #09090b;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0 10px;
      border-radius: 6px;
      font-size: 13px;
    }}
    .search-box:focus {{ outline: none; border-color: rgba(255, 255, 255, 0.2); }}
    .filter-select {{
      height: 32px;
      background: #09090b;
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 0 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
    }}
    .filter-select:focus {{ outline: none; border-color: rgba(255, 255, 255, 0.2); color: var(--text); }}

    .segmented-control {{
      background: #09090b;
      padding: 3px;
      border-radius: 7px;
      border: 1px solid rgba(255, 255, 255, 0.07);
      display: inline-flex;
      gap: 2px;
      flex-shrink: 0;
    }}
    .segmented-btn {{
      background: transparent;
      color: #71717a;
      border: none;
      border-radius: 5px;
      height: 26px;
      padding: 0 12px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .segmented-btn.active {{
      background: #27272a;
      color: #f4f4f5;
      font-weight: 600;
    }}

    .btn-sub {{
      background: transparent;
      color: var(--text-muted);
      border: 1px solid var(--border);
      height: 32px;
      padding: 0 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
      flex-shrink: 0;
    }}
    .btn-sub:hover {{ background: #18181b; color: #f4f4f5; border-color: rgba(255, 255, 255, 0.15); }}

    .legend-bar {{
      font-size: 12px;
      color: #71717a;
      display: flex;
      gap: 20px;
      margin-bottom: 20px;
      padding: 0 4px;
      flex-wrap: wrap;
    }}
    .legend-bar span strong {{ color: #a1a1aa; }}

    /* ── 族群聚合卡片 ── */
    .family-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 14px;
      overflow: hidden;
    }}
    .family-header {{
      padding: 11px 16px;
      background: #141418;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease;
    }}
    .family-header:hover {{ background: #18181e; }}
    .family-title-wrap {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .fold-arrow {{
      color: var(--text-muted);
      font-size: 11px;
      width: 12px;
      display: inline-block;
      font-family: var(--font-mono);
    }}
    .family-title {{
      font-size: 14px;
      font-weight: 600;
      color: #f4f4f5;
      font-family: var(--font-mono);
    }}
    .family-cn {{
      font-size: 12px;
      color: var(--text-muted);
      font-weight: 400;
    }}
    .family-badges {{
      display: flex;
      gap: 6px;
      align-items: center;
    }}
    .family-body {{
      padding: 14px 16px;
    }}

    /* 入口技能卡片 */
    .entry-container {{
      background: #18181c;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-left: 3px solid #10b981;
      border-radius: 6px;
      padding: 12px 14px;
      margin-bottom: 12px;
    }}
    .entry-container.no-members {{
      margin-bottom: 0;
    }}
    .skill-row-main {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .skill-info {{ flex: 1; min-width: 260px; }}
    .skill-title-line {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      font-weight: 600;
      color: #fff;
    }}
    .skill-title-line .skill-name {{
      font-family: var(--font-mono);
    }}
    .skill-desc-text {{
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 4px;
      line-height: 1.45;
      max-width: 82ch;
    }}

    .skill-side {{
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 14px;
      flex-shrink: 0;
    }}

    /* 从属技能树 */
    .member-tree {{
      margin-left: 12px;
      padding-left: 14px;
      border-left: 1px dashed rgba(255, 255, 255, 0.1);
    }}
    .member-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .member-item {{
      background: #121215;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 10px 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      transition: border-color 0.15s, background 0.15s;
    }}
    .member-item:hover {{
      border-color: rgba(255, 255, 255, 0.12);
      background: #16161a;
    }}

    /* 独立技能专区 */
    .standalone-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-top: 14px;
      overflow: hidden;
    }}

    /* 标签与胶囊 */
    .tag {{
      display: inline-flex;
      align-items: center;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 500;
      font-family: var(--font-mono);
      white-space: nowrap;
    }}
    .tag-entry {{
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }}
    .tag-member, .tag-manual {{
      background: rgba(255, 255, 255, 0.05);
      color: #a1a1aa;
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .tag-standalone {{
      background: rgba(161, 161, 170, 0.1);
      color: #d4d4d8;
      border: 1px solid rgba(161, 161, 170, 0.2);
    }}
    .tag-auto {{
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }}
    .tag-zero {{
      color: #52525b;
      background: transparent;
      border: 1px solid rgba(255, 255, 255, 0.04);
    }}

    /* Agent 矩阵点阵 */
    .agent-matrix {{
      display: flex;
      gap: 4px;
      align-items: center;
    }}
    .dot-box {{
      width: 20px;
      height: 20px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      font-family: var(--font-mono);
      font-weight: 600;
      cursor: help;
    }}
    .dot-mounted {{
      background: rgba(16, 185, 129, 0.08);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.2);
    }}
    .dot-missing {{
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.4);
    }}
    .dot-extra {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }}
    .dot-unmounted {{
      background: rgba(255, 255, 255, 0.03);
      color: #52525b;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }}

    .commit-cell {{
      font-size: 11px;
      font-family: var(--font-mono);
      color: var(--text-muted);
      white-space: nowrap;
    }}

    .deps-pill {{
      background: #09090b;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid var(--border);
      font-size: 11px;
      font-family: var(--font-mono);
      color: var(--text-muted);
      white-space: nowrap;
    }}

    /* 平铺表格视图 */
    .table-container {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 13px;
    }}
    th, td {{
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    th {{
      background: #141418;
      font-weight: 500;
      color: var(--text-muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    tr:hover {{ background: rgba(255, 255, 255, 0.02); }}

    footer {{
      margin-top: 32px;
      text-align: center;
      font-size: 12px;
      color: #52525b;
      font-family: var(--font-mono);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>dc-skills · 技能农场驾驶舱</h1>
        <span class="badge-sub">v1.1</span>
      </div>
      <div class="meta-bar">
        <span class="meta-pill">生成时间 <strong>{cleaned_ts}</strong></span>
        <span class="meta-pill">Commit <strong>{master_commit}</strong></span>
        <span class="meta-pill">耗时 <strong>{duration_str}</strong></span>
      </div>
    </header>

    <div class="kpis">
      <div class="card">
        <div class="label">主库托管技能</div>
        <div class="val">{summary.get('skills_total', 0)} <span class="sub">个技能</span></div>
      </div>
      <div class="card">
        <div class="label">能力内聚族群</div>
        <div class="val">{summary.get('families_total', 0)} <span class="sub">个族群</span></div>
      </div>
      <div class="card">
        <div class="label">启动清单注入状态</div>
        <div class="val" style="color: var(--text);">
          <span>{load_modes.get('auto', 0)}</span> <span class="tag tag-auto" style="margin-left: 4px;">Auto 入口</span>
          <span style="color: #3f3f46; font-size: 16px; margin: 0 8px; font-weight: 300;">/</span>
          <span style="color: #a1a1aa;">{load_modes.get('manual', 0)}</span> <span class="tag tag-manual" style="margin-left: 4px;">Manual 按需</span>
        </div>
      </div>
      <div class="card">
        <div class="label">全资产巡检状态</div>
        <div class="val kpi-doctor">
          <span class="kpi-dot-item">
            <span class="kpi-dot" style="background: {'#ef4444' if doctor.get('crit', 0) > 0 else '#52525b'};"></span>
            <strong style="color: {'#ef4444' if doctor.get('crit', 0) > 0 else '#52525b'};">{doctor.get('crit', 0)}</strong>
            <span class="kpi-unit">异常</span>
          </span>
          <span class="kpi-dot-item">
            <span class="kpi-dot" style="background: {'#f59e0b' if doctor.get('warn', 0) > 0 else '#52525b'};"></span>
            <strong style="color: {'#f59e0b' if doctor.get('warn', 0) > 0 else '#52525b'};">{doctor.get('warn', 0)}</strong>
            <span class="kpi-unit">警告</span>
          </span>
          <span class="kpi-dot-item">
            <span class="kpi-dot" style="background: #10b981;"></span>
            <strong style="color: #f4f4f5;">{doctor.get('ok', 0)}</strong>
            <span class="kpi-unit">正常</span>
          </span>
        </div>
      </div>
    </div>

    <div class="toolbar">
      <div class="tool-group-left">
        <input type="text" id="searchInput" class="search-box" placeholder="搜索技能、描述或依赖...">
        <select id="familyFilter" class="filter-select">
          <option value="all">全部族群</option>
        </select>
        <select id="roleFilter" class="filter-select">
          <option value="all">全部角色</option>
          <option value="entry">Entry (入口)</option>
          <option value="member">Member (成员)</option>
          <option value="standalone">Standalone (独立)</option>
        </select>
        <select id="loadFilter" class="filter-select">
          <option value="all">全部模式</option>
          <option value="auto">Auto (自动注入)</option>
          <option value="manual">Manual (按需调用)</option>
        </select>
        <select id="statusFilter" class="filter-select">
          <option value="all">全部状态</option>
          <option value="ok">正常</option>
          <option value="warn">警告</option>
          <option value="crit">异常</option>
        </select>
      </div>

      <div class="tool-group-right" style="display:flex; gap:8px; align-items:center; flex-shrink:0;">
        <div class="segmented-control">
          <button id="btnViewFamily" class="segmented-btn active" onclick="switchView('family')">族群视图</button>
          <button id="btnViewTable" class="segmented-btn" onclick="switchView('table')">平铺表格</button>
        </div>
        <button class="btn-sub" onclick="toggleAllFamilies(true)">全部展开</button>
        <button class="btn-sub" onclick="toggleAllFamilies(false)">全部折叠</button>
      </div>
    </div>

    <div class="legend-bar">
      <span><strong style="color:#10b981;">3px 绿线 / Auto</strong> 入口网关技能（随上下文注入启动清单）</span>
      <span><strong style="color:#a1a1aa;">Member / Manual</strong> 从属技能（不占启动上下文，/name 显式调用或由入口路由）</span>
      <span><strong style="color:#d4d4d8;">Standalone</strong> 独立运维技能（不入族群，全 Agent 挂载，Manual 按需调用）</span>
    </div>

    <!-- 1. 族群聚合视图 (默认) -->
    <div id="familyView"></div>

    <!-- 2. 平铺表格视图 (可选) -->
    <div id="tableView" style="display: none;">
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>技能名称</th>
              <th>族群</th>
              <th>角色</th>
              <th>加载模式</th>
              <th>5 大 Agent 挂载 (C / G / D / Z / X)</th>
              <th>依赖 (CLI / MCP)</th>
              <th>上次更新</th>
              <th>巡检状态</th>
            </tr>
          </thead>
          <tbody id="skillsTbody"></tbody>
        </table>
      </div>
    </div>

    <footer>
      dc-skills 全能力农场监控架构 · 数据源: assets-doctor --export-snapshot · 契约遵循 docs/specs/全资产治理.md
    </footer>
  </div>

  <script>
    const DATA = {doc_json};
    const skills = DATA.skills || [];
    const familiesList = DATA.families || [];
    const agents = (DATA.summary && DATA.summary.agents) || [];

    const FAMILY_TITLES = {{
      software: "软件工程 (Software Engineering)",
      thesis: "学术论文 (Thesis & Paper)",
      drawing: "工程图表 (Diagrams & Charts)",
      frontend: "前端设计 (Frontend UI/UX)",
      info: "信息检索与视觉 (Search & Web)",
      docs: "办公文档 (Office Documents)",
      dshplugin: "DSH 插件治理 (DSH Plugin)"
    }};

    let currentView = 'family';
    let familyCollapsedState = {{}};

    const skillMap = {{}};
    skills.forEach(s => {{ skillMap[s.name] = s; }});

    const famNames = Array.from(new Set(skills.map(s => s.family).filter(Boolean))).sort();
    const familySelect = document.getElementById('familyFilter');
    famNames.forEach(f => {{
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = FAMILY_TITLES[f] || f;
      familySelect.appendChild(opt);
    }});
    const optNone = document.createElement('option');
    optNone.value = "__none__";
    optNone.textContent = "独立技能 (Standalone)";
    familySelect.appendChild(optNone);

    function agentChar(agent) {{
      if (agent === "claude-code") return "C";
      if (agent === "grok") return "G";
      if (agent === "dsh") return "D";
      if (agent === "zcode") return "Z";
      if (agent === "codex") return "X";
      return agent.slice(0, 1).toUpperCase();
    }}

    function renderMatrixDots(mounts) {{
      return agents.map(a => {{
        const st = (mounts || {{}})[a] || 'unmounted';
        return `<div class="dot-box dot-${{st}}" title="${{a}}: ${{st}}">${{agentChar(a)}}</div>`;
      }}).join('');
    }}

    function renderDepsPills(deps) {{
      if (!deps) return '';
      const clis = (deps.cli || []).map(c => `<span class="deps-pill">CLI:${{c}}</span>`).join('');
      const mcps = (deps.mcp || []).map(m => `<span class="deps-pill">MCP:${{m}}</span>`).join('');
      return (clis + mcps);
    }}

    function renderStatusBadge(s) {{
      if (s.status === 'ok') {{
        return `<span style="color:#71717a; font-size:12px; font-family:var(--font-mono); display:inline-flex; align-items:center; gap:5px;"><span style="color:#10b981; font-size:9px;">●</span> OK</span>`;
      }}
      if (s.status === 'warn') {{
        return `<span style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid rgba(245,158,11,0.25); padding:2px 7px; border-radius:4px; font-size:11px; font-weight:600; font-family:var(--font-mono); display:inline-flex; align-items:center; gap:5px;"><span style="font-size:9px;">●</span> WARN</span>`;
      }}
      return `<span style="background:rgba(239,68,68,0.12); color:#ef4444; border:1px solid rgba(239,68,68,0.25); padding:2px 7px; border-radius:4px; font-size:11px; font-weight:600; font-family:var(--font-mono); display:inline-flex; align-items:center; gap:5px;"><span style="font-size:9px;">●</span> CRIT</span>`;
    }}

    function filterMatches(s, q, fam, role, load, status) {{
      if (q) {{
        const matchName = s.name.toLowerCase().includes(q);
        const matchDesc = (s.description || '').toLowerCase().includes(q);
        const matchDeps = (s.deps.cli || []).concat(s.deps.mcp || []).join(' ').toLowerCase().includes(q);
        const matchFam = (s.family || '').toLowerCase().includes(q);
        if (!matchName && !matchDesc && !matchDeps && !matchFam) return false;
      }}
      if (fam !== 'all') {{
        if (fam === '__none__' && s.family !== null) return false;
        if (fam !== '__none__' && s.family !== fam) return false;
      }}
      if (role !== 'all' && s.role !== role) return false;
      if (load !== 'all' && s.load_mode !== load) return false;
      if (status !== 'all' && s.status !== status) return false;
      return true;
    }}

    function renderFamilyView() {{
      const container = document.getElementById('familyView');
      container.innerHTML = '';

      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const fam = document.getElementById('familyFilter').value;
      const role = document.getElementById('roleFilter').value;
      const load = document.getElementById('loadFilter').value;
      const status = document.getElementById('statusFilter').value;

      let renderedAny = false;

      familiesList.forEach(f => {{
        const entrySkill = skillMap[f.entry];
        const memberSkills = (f.members || []).map(mName => skillMap[mName]).filter(Boolean);

        const matchEntry = entrySkill && filterMatches(entrySkill, q, fam, role, load, status);
        const matchedMembers = memberSkills.filter(m => filterMatches(m, q, fam, role, load, status));
        const matchFamilyDirect = fam === f.name || (q && f.name.toLowerCase().includes(q));

        if (!matchEntry && matchedMembers.length === 0 && !matchFamilyDirect) {{
          return;
        }}

        renderedAny = true;
        const card = document.createElement('div');
        card.className = 'family-card';

        const isCollapsed = Boolean(familyCollapsedState[f.name]);
        const membersCount = memberSkills.length;
        const famTitle = FAMILY_TITLES[f.name] || f.name;

        // 计算族内异常与警告
        let famCrits = 0;
        let famWarns = 0;
        const allFamSkills = (entrySkill ? [entrySkill] : []).concat(memberSkills);
        allFamSkills.forEach(s => {{
          if (s.status === 'crit') famCrits++;
          if (s.status === 'warn') famWarns++;
        }});

        let alertBadgeHtml = '';
        if (famCrits > 0) {{
          alertBadgeHtml += `<span class="tag" style="background:rgba(239,68,68,0.12); color:#ef4444; border:1px solid rgba(239,68,68,0.25);">● ${{famCrits}} 异常</span>`;
        }}
        if (famWarns > 0) {{
          alertBadgeHtml += `<span class="tag" style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid rgba(245,158,11,0.25);">● ${{famWarns}} 警告</span>`;
        }}

        const headerDiv = document.createElement('div');
        headerDiv.className = 'family-header';
        headerDiv.onclick = () => {{
          familyCollapsedState[f.name] = !familyCollapsedState[f.name];
          renderFamilyView();
        }};

        const depsHtml = renderDepsPills(f.deps);
        const memberBadgeHtml = membersCount === 0 
          ? `<span class="tag tag-zero">0 成员</span>`
          : `<span class="tag tag-member">${{membersCount}} 成员</span>`;

        headerDiv.innerHTML = `
          <div class="family-title-wrap">
            <span class="fold-arrow">${{isCollapsed ? '▸' : '▾'}}</span>
            <span class="family-title">${{f.name}}</span>
            <span class="family-cn">${{famTitle}}</span>
          </div>
          <div class="family-badges">
            ${{alertBadgeHtml}}
            ${{depsHtml ? `<span style="display:inline-flex; gap:4px;">${{depsHtml}}</span>` : ''}}
            <span class="tag tag-entry">1 入口</span>
            ${{memberBadgeHtml}}
          </div>
        `;
        card.appendChild(headerDiv);

        if (!isCollapsed) {{
          const bodyDiv = document.createElement('div');
          bodyDiv.className = 'family-body';

          if (entrySkill) {{
            const entryDiv = document.createElement('div');
            entryDiv.className = 'entry-container' + (membersCount === 0 ? ' no-members' : '');
            
            const warnHintHtml = entrySkill.status !== 'ok'
              ? `<div style="font-size:12px; color:#f59e0b; margin-top:4px; font-family:var(--font-mono);">${{entrySkill.status_hint}}</div>`
              : '';

            entryDiv.innerHTML = `
              <div class="skill-row-main">
                <div class="skill-info">
                  <div class="skill-title-line">
                    <span class="skill-name">${{entrySkill.name}}</span>
                    <span class="tag tag-entry">entry · auto</span>
                  </div>
                  <div class="skill-desc-text">${{entrySkill.description || '—'}}</div>
                  ${{warnHintHtml}}
                </div>
                <div class="skill-side">
                  <div class="agent-matrix">${{renderMatrixDots(entrySkill.mounts)}}</div>
                  <div class="commit-cell">${{entrySkill.last_commit || '—'}}</div>
                  <div>${{renderStatusBadge(entrySkill)}}</div>
                </div>
              </div>
            `;
            bodyDiv.appendChild(entryDiv);
          }}

          if (memberSkills.length > 0) {{
            const treeDiv = document.createElement('div');
            treeDiv.className = 'member-tree';

            const listDiv = document.createElement('div');
            listDiv.className = 'member-list';

            memberSkills.forEach(m => {{
              const itemDiv = document.createElement('div');
              itemDiv.className = 'member-item';
              const mWarnHintHtml = m.status !== 'ok'
                ? `<div style="font-size:12px; color:#f59e0b; margin-top:4px; font-family:var(--font-mono); padding-left:18px;">${{m.status_hint}}</div>`
                : '';

              itemDiv.innerHTML = `
                <div class="skill-info">
                  <div class="skill-title-line">
                    <span style="color:#52525b; font-family:var(--font-mono); font-weight:400; font-size:13px;">├─</span>
                    <span class="skill-name">${{m.name}}</span>
                    <span class="tag tag-member">member · manual</span>
                  </div>
                  <div class="skill-desc-text" style="padding-left:18px;">${{m.description || '—'}}</div>
                  ${{mWarnHintHtml}}
                </div>
                <div class="skill-side">
                  <div class="agent-matrix">${{renderMatrixDots(m.mounts)}}</div>
                  <div class="commit-cell">${{m.last_commit || '—'}}</div>
                  <div>${{renderStatusBadge(m)}}</div>
                </div>
              `;
              listDiv.appendChild(itemDiv);
            }});

            treeDiv.appendChild(listDiv);
            bodyDiv.appendChild(treeDiv);
          }}

          card.appendChild(bodyDiv);
        }}

        container.appendChild(card);
      }});

      // 独立技能专区
      const standaloneSkills = skills.filter(s => s.role === 'standalone');
      const matchedStandalone = standaloneSkills.filter(s => filterMatches(s, q, fam, role, load, status));

      if (matchedStandalone.length > 0) {{
        renderedAny = true;
        const standCard = document.createElement('div');
        standCard.className = 'standalone-card';
        const isCollapsed = Boolean(familyCollapsedState['__standalone__']);

        const sHeader = document.createElement('div');
        sHeader.className = 'family-header';
        sHeader.onclick = () => {{
          familyCollapsedState['__standalone__'] = !familyCollapsedState['__standalone__'];
          renderFamilyView();
        }};
        sHeader.innerHTML = `
          <div class="family-title-wrap">
            <span class="fold-arrow">${{isCollapsed ? '▸' : '▾'}}</span>
            <span class="family-title" style="color:#e4e4e7;">standalone</span>
            <span class="family-cn">独立手动技能 (全 Agent 挂载 · Manual 显式调用 · 零启动上下文)</span>
          </div>
          <div class="family-badges">
            <span class="tag tag-standalone">${{matchedStandalone.length}} 技能</span>
          </div>
        `;
        standCard.appendChild(sHeader);

        if (!isCollapsed) {{
          const sBody = document.createElement('div');
          sBody.className = 'family-body';
          const listDiv = document.createElement('div');
          listDiv.className = 'member-list';

          matchedStandalone.forEach(s => {{
            const itemDiv = document.createElement('div');
            itemDiv.className = 'member-item';
            const sWarnHintHtml = s.status !== 'ok'
              ? `<div style="font-size:12px; color:#f59e0b; margin-top:4px; font-family:var(--font-mono);">${{s.status_hint}}</div>`
              : '';

            itemDiv.innerHTML = `
              <div class="skill-info">
                <div class="skill-title-line">
                  <span class="skill-name">${{s.name}}</span>
                  <span class="tag tag-standalone">standalone · manual</span>
                </div>
                <div class="skill-desc-text">${{s.description || '—'}}</div>
                ${{sWarnHintHtml}}
              </div>
              <div class="skill-side">
                <div class="agent-matrix">${{renderMatrixDots(s.mounts)}}</div>
                <div class="commit-cell">${{s.last_commit || '—'}}</div>
                <div>${{renderStatusBadge(s)}}</div>
              </div>
            `;
            listDiv.appendChild(itemDiv);
          }});

          sBody.appendChild(listDiv);
          standCard.appendChild(sBody);
        }}

        container.appendChild(standCard);
      }}

      if (!renderedAny) {{
        container.innerHTML = '<div class="card" style="text-align:center; padding:48px; color:var(--text-muted); font-size:13px;">未匹配到任何技能或族群</div>';
      }}
    }}

    function renderTableView() {{
      const tbody = document.getElementById('skillsTbody');
      tbody.innerHTML = '';

      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const fam = document.getElementById('familyFilter').value;
      const role = document.getElementById('roleFilter').value;
      const load = document.getElementById('loadFilter').value;
      const status = document.getElementById('statusFilter').value;

      const filtered = skills.filter(s => filterMatches(s, q, fam, role, load, status));

      if (filtered.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#71717a; padding:32px; font-size:13px;">未匹配到任何技能条目</td></tr>';
        return;
      }}

      filtered.forEach(s => {{
        const tr = document.createElement('tr');
        const roleClass = s.role === 'entry' ? 'tag-entry' : (s.role === 'member' ? 'tag-member' : 'tag-standalone');
        const loadClass = s.load_mode === 'auto' ? 'tag-auto' : 'tag-manual';
        const warnHintHtml = s.status !== 'ok'
          ? `<div style="font-size:11px; color:#f59e0b; margin-top:2px; font-family:var(--font-mono);">${{s.status_hint}}</div>`
          : '';

        tr.innerHTML = `
          <td>
            <div style="font-weight:600; color:#fff; font-family:var(--font-mono);">${{s.name}}</div>
            <div style="font-size:12px; color:var(--text-muted); max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${{s.description || '—'}}</div>
            ${{warnHintHtml}}
          </td>
          <td style="font-family:var(--font-mono); color:#d4d4d8;">${{s.family || '—'}}</td>
          <td><span class="tag ${{roleClass}}">${{s.role}}</span></td>
          <td><span class="tag ${{loadClass}}">${{s.load_mode}}</span></td>
          <td><div class="agent-matrix">${{renderMatrixDots(s.mounts)}}</div></td>
          <td style="font-size:12px;">${{renderDepsPills(s.deps) || '—'}}</td>
          <td class="commit-cell">${{s.last_commit || '—'}}</td>
          <td>${{renderStatusBadge(s)}}</td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    function renderAll() {{
      if (currentView === 'family') {{
        renderFamilyView();
      }} else {{
        renderTableView();
      }}
    }}

    function switchView(view) {{
      currentView = view;
      document.getElementById('btnViewFamily').classList.toggle('active', view === 'family');
      document.getElementById('btnViewTable').classList.toggle('active', view === 'table');
      document.getElementById('familyView').style.display = view === 'family' ? 'block' : 'none';
      document.getElementById('tableView').style.display = view === 'table' ? 'block' : 'none';
      renderAll();
    }}

    function toggleAllFamilies(expand) {{
      familiesList.forEach(f => {{
        familyCollapsedState[f.name] = !expand;
      }});
      familyCollapsedState['__standalone__'] = !expand;
      if (currentView === 'family') {{
        renderFamilyView();
      }}
    }}

    document.getElementById('searchInput').addEventListener('input', renderAll);
    document.getElementById('familyFilter').addEventListener('change', renderAll);
    document.getElementById('roleFilter').addEventListener('change', renderAll);
    document.getElementById('loadFilter').addEventListener('change', renderAll);
    document.getElementById('statusFilter').addEventListener('change', renderAll);

    renderAll();
  </script>
</body>
</html>
"""
    out.write_text(html_content, encoding="utf-8")
    return out
