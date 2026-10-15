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

    doc_json = json.dumps(doc, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>dc-skills · 全机技能资产驾驶舱</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #131c2e;
      --card-hover: #18243c;
      --border: #24344d;
      --border-subtle: #1e2c42;
      --text: #f8fafc;
      --text-muted: #8e9fb5;
      --primary: #38bdf8;
      --primary-glow: rgba(56, 189, 248, 0.15);
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.15);
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.15);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.15);
      --info: #818cf8;
      --info-bg: rgba(129, 140, 248, 0.15);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px 20px;
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
    .title-group h1 {{
      font-size: 24px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .badge-sub {{
      font-size: 12px;
      background: var(--primary-glow);
      color: var(--primary);
      padding: 2px 8px;
      border-radius: 4px;
      font-weight: 600;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }}
    .meta-bar {{
      font-size: 13px;
      color: var(--text-muted);
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px 20px;
    }}
    .card .label {{ font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }}
    .card .val {{ font-size: 26px; font-weight: 700; display: flex; align-items: baseline; gap: 8px; }}
    .card .sub {{ font-size: 12px; color: var(--text-muted); font-weight: 400; }}

    .toolbar {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 18px;
      margin-bottom: 22px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
    }}
    .tool-group-left {{
      display: flex;
      gap: 10px;
      flex: 1;
      min-width: 300px;
      flex-wrap: wrap;
    }}
    .tool-group-right {{
      display: flex;
      gap: 8px;
      align-items: center;
    }}
    .search-box {{
      flex: 1;
      min-width: 220px;
      background: #060911;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
    }}
    .search-box:focus {{ outline: none; border-color: var(--primary); }}
    .filter-select {{
      background: #060911;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 13px;
      cursor: pointer;
    }}

    .btn-toggle {{
      background: #060911;
      color: var(--text-muted);
      border: 1px solid var(--border);
      padding: 8px 14px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }}
    .btn-toggle.active {{
      background: rgba(56, 189, 248, 0.15);
      color: var(--primary);
      border-color: rgba(56, 189, 248, 0.4);
    }}
    .btn-sub {{
      background: transparent;
      color: var(--text-muted);
      border: 1px solid var(--border);
      padding: 7px 12px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
    }}
    .btn-sub:hover {{ background: #1e2c42; color: #fff; }}

    /* ── 族群聚合视图 (Family View) ── */
    .family-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 20px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }}
    .family-header {{
      padding: 14px 20px;
      background: linear-gradient(90deg, rgba(56, 189, 248, 0.08) 0%, rgba(19, 28, 46, 0.8) 100%);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
    }}
    .family-title-wrap {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .family-title {{
      font-size: 17px;
      font-weight: 700;
      color: #fff;
    }}
    .family-cn {{
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 400;
    }}
    .family-badges {{
      display: flex;
      gap: 8px;
      align-items: center;
    }}
    .family-body {{
      padding: 20px;
    }}

    /* 👑 入口技能卡片 */
    .entry-container {{
      background: rgba(56, 189, 248, 0.04);
      border: 1px solid rgba(56, 189, 248, 0.35);
      border-left: 5px solid var(--primary);
      border-radius: 8px;
      padding: 16px 18px;
      margin-bottom: 16px;
    }}
    .entry-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(56, 189, 248, 0.2);
      color: var(--primary);
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      margin-bottom: 8px;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }}
    .skill-row-main {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .skill-info {{ flex: 1; min-width: 280px; }}
    .skill-title-line {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 16px;
      font-weight: 700;
      color: #fff;
    }}
    .skill-desc-text {{
      font-size: 13px;
      color: var(--text-muted);
      margin-top: 6px;
      line-height: 1.5;
    }}
    .skill-side {{
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 8px;
    }}

    /* ↳ 族内从属技能树 */
    .member-tree {{
      margin-left: 20px;
      padding-left: 18px;
      border-left: 2px dashed #334155;
    }}
    .member-tree-header {{
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .member-list {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .member-item {{
      background: #090f1d;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 12px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      transition: border-color 0.15s;
    }}
    .member-item:hover {{
      border-color: #475569;
      background: #0d1527;
    }}

    /* 独立技能专区 */
    .standalone-card {{
      background: var(--card-bg);
      border: 1px solid rgba(129, 140, 248, 0.3);
      border-radius: 10px;
      margin-top: 24px;
      overflow: hidden;
    }}
    .standalone-header {{
      padding: 14px 20px;
      background: linear-gradient(90deg, rgba(129, 140, 248, 0.1) 0%, rgba(19, 28, 46, 0.8) 100%);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    /* 徽标与点阵 */
    .tag {{
      display: inline-flex;
      align-items: center;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .tag-entry {{ background: var(--primary); color: #021224; }}
    .tag-member {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }}
    .tag-standalone {{ background: var(--info-bg); color: var(--info); border: 1px solid rgba(129, 140, 248, 0.3); }}
    .tag-auto {{ background: var(--success-bg); color: var(--success); }}
    .tag-manual {{ background: var(--warning-bg); color: var(--warning); }}

    .agent-matrix {{
      display: flex;
      gap: 5px;
      align-items: center;
    }}
    .dot-box {{
      width: 22px;
      height: 22px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      font-weight: 700;
      cursor: help;
    }}
    .dot-mounted {{ background: var(--success-bg); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.4); }}
    .dot-missing {{ background: var(--danger-bg); color: var(--danger); border: 1px solid rgba(239, 68, 68, 0.4); }}
    .dot-extra {{ background: var(--warning-bg); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.4); }}
    .dot-unmounted {{ background: rgba(148, 163, 184, 0.08); color: #475569; }}

    .badge-status {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 4px;
    }}
    .status-ok {{ background: var(--success-bg); color: var(--success); }}
    .status-warn {{ background: var(--warning-bg); color: var(--warning); }}
    .status-crit {{ background: var(--danger-bg); color: var(--danger); }}

    .deps-pill {{
      background: #060911;
      padding: 2px 7px;
      border-radius: 4px;
      border: 1px solid var(--border);
      font-size: 11px;
      color: var(--text-muted);
    }}

    /* ── 平铺表格视图 (Flat Table View) ── */
    .table-container {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }}
    th, td {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    th {{
      background: #090f1d;
      font-weight: 600;
      color: var(--text-muted);
      font-size: 13px;
      white-space: nowrap;
    }}
    tr:hover {{ background: rgba(56, 189, 248, 0.03); }}

    footer {{
      margin-top: 32px;
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>dc-skills · 技能农场驾驶舱 <span class="badge-sub">v1.1</span></h1>
      </div>
      <div class="meta-bar">
        <span>生成时间: <strong>{html.escape(meta.get('generated_at', '—'))}</strong></span>
        <span>Commit: <strong>{html.escape(meta.get('master_commit') or '—')}</strong></span>
        <span>耗时: <strong>{meta.get('duration_ms', 0)} ms</strong></span>
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
        <div class="val" style="color: var(--primary);">
          {load_modes.get('auto', 0)} <span class="sub">Auto (入口)</span>
          <span style="color: var(--text-muted); font-size: 16px; margin: 0 4px;">/</span>
          {load_modes.get('manual', 0)} <span class="sub">Manual (按需)</span>
        </div>
      </div>
      <div class="card">
        <div class="label">全资产巡检状态</div>
        <div class="val">
          <span style="color: {'var(--danger)' if doctor.get('crit',0)>0 else 'var(--success)'};">
            🚨 {doctor.get('crit', 0)}
          </span>
          <span style="color: var(--warning); margin-left: 10px;">
            ⚠️ {doctor.get('warn', 0)}
          </span>
          <span style="color: var(--success); margin-left: 10px;">
            ✅ {doctor.get('ok', 0)}
          </span>
        </div>
      </div>
    </div>

    <div class="toolbar">
      <div class="tool-group-left">
        <input type="text" id="searchInput" class="search-box" placeholder="搜索技能名称、描述、族群或依赖...">
        <select id="familyFilter" class="filter-select">
          <option value="all">全家族群 (All Families)</option>
        </select>
        <select id="roleFilter" class="filter-select">
          <option value="all">全角色 (Role: All)</option>
          <option value="entry">Entry (👑 族群入口)</option>
          <option value="member">Member (↳ 族内成员)</option>
          <option value="standalone">Standalone (🛠️ 独立技能)</option>
        </select>
        <select id="loadFilter" class="filter-select">
          <option value="all">全模式 (Load: All)</option>
          <option value="auto">Auto (随上下文注入)</option>
          <option value="manual">Manual (按需显式调用)</option>
        </select>
        <select id="statusFilter" class="filter-select">
          <option value="all">全状态 (Status: All)</option>
          <option value="ok">✅ 正常</option>
          <option value="warn">⚠️ 警告</option>
          <option value="crit">🚨 异常</option>
        </select>
      </div>

      <div class="tool-group-right">
        <button id="btnViewFamily" class="btn-toggle active" onclick="switchView('family')">🏢 族群聚合视图</button>
        <button id="btnViewTable" class="btn-toggle" onclick="switchView('table')">📋 平铺表格</button>
        <button class="btn-sub" onclick="toggleAllFamilies(true)">全部展开</button>
        <button class="btn-sub" onclick="toggleAllFamilies(false)">全部折叠</button>
      </div>
    </div>

    <!-- 1. 族群聚合视图 (默认) -->
    <div id="familyView">
      <!-- JS 渲染各个族群卡片与从属树 -->
    </div>

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
              <th>5 大 Agent 挂载点 (C / G / D / Z / X)</th>
              <th>依赖 (CLI / MCP)</th>
              <th>上次更新</th>
              <th>巡检状态</th>
            </tr>
          </thead>
          <tbody id="skillsTbody">
            <!-- JS 渲染 -->
          </tbody>
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
      agentops: "Agent 运维与编排 (Agent Operations)",
      drawing: "工程图表 (Diagrams & Charts)",
      frontend: "前端设计 (Frontend UI/UX)",
      info: "信息检索与视觉 (Search & Web)",
      docs: "办公文档 (Office Documents)",
      db: "数据库交互 (Database)",
      bridge: "跨边界桥接 (WSL-Windows Bridge)",
      lark: "飞书协作 (Lark / Feishu)",
      dshplugin: "DSH 插件治理 (DSH Plugin)",
      meta: "仓库元工作 (Meta Governance)"
    }};

    let currentView = 'family';
    let familyCollapsedState = {{}};

    // 建立名称索引
    const skillMap = {{}};
    skills.forEach(s => {{ skillMap[s.name] = s; }});

    // 填充族群下拉列表
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
    optNone.textContent = "🛠️ 独立技能 (Standalone)";
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
      if (!deps) return '—';
      const clis = (deps.cli || []).map(c => `<span class="deps-pill">CLI:${{c}}</span>`).join('');
      const mcps = (deps.mcp || []).map(m => `<span class="deps-pill">MCP:${{m}}</span>`).join('');
      return (clis + mcps) || '<span style="color:#64748b;">无</span>';
    }}

    function renderStatusBadge(s) {{
      const stClass = s.status === 'ok' ? 'status-ok' : (s.status === 'warn' ? 'status-warn' : 'status-crit');
      const icon = s.status === 'ok' ? '✅' : (s.status === 'warn' ? '⚠️' : '🚨');
      return `<span class="badge-status ${{stClass}}" title="${{s.status_hint || ''}}">${{icon}} ${{s.status}}</span>`;
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

    // ── 渲染族群聚合视图 ──
    function renderFamilyView() {{
      const container = document.getElementById('familyView');
      container.innerHTML = '';

      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const fam = document.getElementById('familyFilter').value;
      const role = document.getElementById('roleFilter').value;
      const load = document.getElementById('loadFilter').value;
      const status = document.getElementById('statusFilter').value;

      let renderedAny = false;

      // 遍历 12 族群
      familiesList.forEach(f => {{
        const entrySkill = skillMap[f.entry];
        const memberSkills = (f.members || []).map(mName => skillMap[mName]).filter(Boolean);

        // 过滤
        const matchEntry = entrySkill && filterMatches(entrySkill, q, fam, role, load, status);
        const matchedMembers = memberSkills.filter(m => filterMatches(m, q, fam, role, load, status));

        // 如果用户按族群筛选，或者搜索命中族群名，全显
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

        // 族群头部
        const headerDiv = document.createElement('div');
        headerDiv.className = 'family-header';
        headerDiv.onclick = () => {{
          familyCollapsedState[f.name] = !familyCollapsedState[f.name];
          renderFamilyView();
        }};

        headerDiv.innerHTML = `
          <div class="family-title-wrap">
            <span class="family-title">📁 ${{f.name}}</span>
            <span class="family-cn">${{famTitle}}</span>
          </div>
          <div class="family-badges">
            <span class="deps-pill">依赖: ${{renderDepsPills(f.deps)}}</span>
            <span class="tag tag-entry">1 入口 (Auto)</span>
            <span class="tag tag-member">${{membersCount}} 成员 (Manual)</span>
            <span class="btn-sub" style="padding:2px 8px; font-size:11px;">${{isCollapsed ? '展开 +' : '折叠 -'}}</span>
          </div>
        `;
        card.appendChild(headerDiv);

        // 族群主体
        if (!isCollapsed) {{
          const bodyDiv = document.createElement('div');
          bodyDiv.className = 'family-body';

          // 1. 渲染 Entry 技能
          if (entrySkill) {{
            const entryDiv = document.createElement('div');
            entryDiv.className = 'entry-container';
            entryDiv.innerHTML = `
              <div class="entry-badge">👑 族群入口网关 · Auto 随上下文注入启动清单</div>
              <div class="skill-row-main">
                <div class="skill-info">
                  <div class="skill-title-line">
                    <span>${{entrySkill.name}}</span>
                    <span class="tag tag-entry">${{entrySkill.role}}</span>
                    <span class="tag tag-auto">${{entrySkill.load_mode}}</span>
                  </div>
                  <div class="skill-desc-text">${{entrySkill.description || '—'}}</div>
                </div>
                <div class="skill-side">
                  <div class="agent-matrix">${{renderMatrixDots(entrySkill.mounts)}}</div>
                  <div style="font-size:12px; color:var(--text-muted);">${{entrySkill.last_commit || '—'}}</div>
                  <div>${{renderStatusBadge(entrySkill)}}</div>
                </div>
              </div>
            `;
            bodyDiv.appendChild(entryDiv);
          }}

          // 2. 渲染 Member 技能树
          if (memberSkills.length > 0) {{
            const treeDiv = document.createElement('div');
            treeDiv.className = 'member-tree';
            treeDiv.innerHTML = `
              <div class="member-tree-header">
                <span>↳ 族内从属技能树 (Member · Manual 模式 · 不占启动上下文 · 仅按需调用或入口路由)</span>
              </div>
            `;

            const listDiv = document.createElement('div');
            listDiv.className = 'member-list';

            memberSkills.forEach(m => {{
              const itemDiv = document.createElement('div');
              itemDiv.className = 'member-item';
              itemDiv.innerHTML = `
                <div class="skill-info">
                  <div class="skill-title-line" style="font-size:15px;">
                    <span style="color:#94a3b8; font-weight:400;">├─</span>
                    <span>${{m.name}}</span>
                    <span class="tag tag-member">${{m.role}}</span>
                    <span class="tag tag-manual">${{m.load_mode}}</span>
                  </div>
                  <div class="skill-desc-text" style="padding-left:22px;">${{m.description || '—'}}</div>
                </div>
                <div class="skill-side">
                  <div class="agent-matrix">${{renderMatrixDots(m.mounts)}}</div>
                  <div style="font-size:12px; color:var(--text-muted);">${{m.last_commit || '—'}}</div>
                  <div>${{renderStatusBadge(m)}}</div>
                </div>
              `;
              listDiv.appendChild(itemDiv);
            }});

            treeDiv.appendChild(listDiv);
            bodyDiv.appendChild(treeDiv);
          }} else {{
            const emptyNote = document.createElement('div');
            emptyNote.style.cssText = 'padding: 4px 12px; color:#64748b; font-size:12px; font-style:italic;';
            emptyNote.textContent = '单体族群 · 暂无下属成员技能';
            bodyDiv.appendChild(emptyNote);
          }}

          card.appendChild(bodyDiv);
        }}

        container.appendChild(card);
      }});

      // 3. 渲染独立技能专区 (Standalone)
      const standaloneSkills = skills.filter(s => s.role === 'standalone');
      const matchedStandalone = standaloneSkills.filter(s => filterMatches(s, q, fam, role, load, status));

      if (matchedStandalone.length > 0) {{
        renderedAny = true;
        const standCard = document.createElement('div');
        standCard.className = 'standalone-card';
        standCard.innerHTML = `
          <div class="standalone-header">
            <div class="family-title-wrap">
              <span class="family-title" style="color:var(--info);">🛠️ 独立手动技能 (Standalone)</span>
              <span class="family-cn">全 Agent 挂载 · 不入族群 · Manual 按需触发 · 零启动上下文占用</span>
            </div>
            <span class="tag tag-standalone">${{matchedStandalone.length}} 技能</span>
          </div>
          <div class="family-body">
            <div class="member-list">
              ${{matchedStandalone.map(s => `
                <div class="member-item" style="border-color:rgba(129, 140, 248, 0.2);">
                  <div class="skill-info">
                    <div class="skill-title-line" style="font-size:15px;">
                      <span>${{s.name}}</span>
                      <span class="tag tag-standalone">${{s.role}}</span>
                      <span class="tag tag-manual">${{s.load_mode}}</span>
                    </div>
                    <div class="skill-desc-text">${{s.description || '—'}}</div>
                  </div>
                  <div class="skill-side">
                    <div class="agent-matrix">${{renderMatrixDots(s.mounts)}}</div>
                    <div style="font-size:12px; color:var(--text-muted);">${{s.last_commit || '—'}}</div>
                    <div>${{renderStatusBadge(s)}}</div>
                  </div>
                </div>
              `).join('')}}
            </div>
          </div>
        `;
        container.appendChild(standCard);
      }}

      if (!renderedAny) {{
        container.innerHTML = '<div class="card" style="text-align:center; padding:48px; color:var(--text-muted);">未匹配到任何技能或族群</div>';
      }}
    }}

    // ── 渲染平铺表格视图 ──
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
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#94a3b8; padding:32px;">未匹配到任何技能条目</td></tr>';
        return;
      }}

      filtered.forEach(s => {{
        const tr = document.createElement('tr');
        const roleClass = s.role === 'entry' ? 'tag-entry' : (s.role === 'member' ? 'tag-member' : 'tag-standalone');
        const loadClass = s.load_mode === 'auto' ? 'tag-auto' : 'tag-manual';

        tr.innerHTML = `
          <td>
            <div style="font-weight:600; color:#fff;">${{s.name}}</div>
            <div style="font-size:12px; color:var(--text-muted); max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${{s.description || '—'}}</div>
          </td>
          <td>${{s.family || '—'}}</td>
          <td><span class="tag ${{roleClass}}">${{s.role}}</span></td>
          <td><span class="tag ${{loadClass}}">${{s.load_mode}}</span></td>
          <td><div class="agent-matrix">${{renderMatrixDots(s.mounts)}}</div></td>
          <td style="font-size:12px;">${{renderDepsPills(s.deps)}}</td>
          <td style="font-size:12px; color:var(--text-muted);">${{s.last_commit || '—'}}</td>
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
      if (currentView === 'family') {{
        renderFamilyView();
      }}
    }}

    document.getElementById('searchInput').addEventListener('input', renderAll);
    document.getElementById('familyFilter').addEventListener('change', renderAll);
    document.getElementById('roleFilter').addEventListener('change', renderAll);
    document.getElementById('loadFilter').addEventListener('change', renderAll);
    document.getElementById('statusFilter').addEventListener('change', renderAll);

    // 默认展示族群聚合视图
    renderAll();
  </script>
</body>
</html>
"""
    out.write_text(html_content, encoding="utf-8")
    return out
