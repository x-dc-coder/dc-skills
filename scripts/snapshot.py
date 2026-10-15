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
                    # 多行取后续内容
                    continue
                return desc
        # 兜底多行提取
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

    # 1. 元数据
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

    # 2. 读取 agent-map
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

    # 3. 候选 Agent 列表（Linux 过滤掉 windows 专用 agent）
    on_windows = os.name == "nt"
    agents: list[str] = []
    for a_name, a_cfg in (agent_map_data.get("agents") or {}).items():
        if a_cfg.get("platform") == "windows" and not on_windows:
            continue
        agents.append(a_name)

    # 4. 统计汇总
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

    # 5. 遍历各技能状态
    base_skills = set(agent_map_data.get("base") or [])
    skills_list: list[dict[str, Any]] = []

    for s in specs:
        # 探测各 Agent 挂载状态
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

        # 族群依赖
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

        # 最近一次 git commit
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

        # 巡检状态与提示（从 rep.items 匹配）
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
    skills = doc.get("skills") or []
    agents = summary.get("agents") or []

    # 预处理数据供嵌入
    doc_json = json.dumps(doc, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>dc-skills · 全机技能资产驾驶舱</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --primary-hover: #0ea5e9;
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.15);
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.15);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.15);
      --info: #6366f1;
      --info-bg: rgba(99, 102, 241, 0.15);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px 20px;
    }}
    .container {{ max-width: 1320px; margin: 0 auto; }}
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
      gap: 10px;
    }}
    .badge-sub {{
      font-size: 12px;
      background: rgba(56, 189, 248, 0.2);
      color: var(--primary);
      padding: 2px 8px;
      border-radius: 4px;
      font-weight: 600;
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
      border-radius: 8px;
      padding: 16px 20px;
    }}
    .card .label {{ font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }}
    .card .val {{ font-size: 26px; font-weight: 700; display: flex; align-items: baseline; gap: 8px; }}
    .card .sub {{ font-size: 12px; color: var(--text-muted); font-weight: 400; }}

    .toolbar {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 20px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .search-box {{
      flex: 1;
      min-width: 220px;
      background: #090d16;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
    }}
    .search-box:focus {{ outline: none; border-color: var(--primary); }}
    .filter-select {{
      background: #090d16;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
      cursor: pointer;
    }}

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
      font-size: 14px;
    }}
    th, td {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    th {{
      background: rgba(15, 23, 42, 0.75);
      font-weight: 600;
      color: var(--text-muted);
      font-size: 13px;
      white-space: nowrap;
    }}
    tr:hover {{ background: rgba(56, 189, 248, 0.04); }}
    .skill-name {{ font-weight: 600; color: #fff; display: flex; flex-direction: column; }}
    .skill-desc {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}

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
    .tag-standalone {{ background: var(--info-bg); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); }}
    .tag-auto {{ background: var(--success-bg); color: var(--success); }}
    .tag-manual {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); }}

    .agent-matrix {{
      display: flex;
      gap: 6px;
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

    .deps-cell {{ font-size: 12px; color: var(--text-muted); }}
    .deps-pill {{ background: #0f172a; padding: 1px 5px; border-radius: 3px; border: 1px solid #334155; margin-right: 4px; }}

    footer {{
      margin-top: 24px;
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
        <h1>dc-skills · 技能农场驾驶舱 <span class="badge-sub">v1.0</span></h1>
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
          {load_modes.get('auto', 0)} <span class="sub">Auto</span>
          <span style="color: var(--text-muted); font-size: 16px; margin: 0 4px;">/</span>
          {load_modes.get('manual', 0)} <span class="sub">Manual</span>
        </div>
      </div>
      <div class="card">
        <div class="label">全资产健康状态</div>
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
      <input type="text" id="searchInput" class="search-box" placeholder="搜索技能名称、描述或依赖...">
      <select id="familyFilter" class="filter-select">
        <option value="all">全家族群 (All)</option>
      </select>
      <select id="roleFilter" class="filter-select">
        <option value="all">全角色 (Role: All)</option>
        <option value="entry">Entry (入口)</option>
        <option value="member">Member (成员)</option>
        <option value="standalone">Standalone (独立)</option>
      </select>
      <select id="loadFilter" class="filter-select">
        <option value="all">全加载模式 (All)</option>
        <option value="auto">Auto (注入清单)</option>
        <option value="manual">Manual (按需调用)</option>
      </select>
      <select id="statusFilter" class="filter-select">
        <option value="all">全巡检状态 (All)</option>
        <option value="ok">✅ 正常</option>
        <option value="warn">⚠️ 警告</option>
        <option value="crit">🚨 异常</option>
      </select>
    </div>

    <div class="table-container">
      <table id="skillsTable">
        <thead>
          <tr>
            <th>技能名称</th>
            <th>族群</th>
            <th>角色</th>
            <th>加载模式</th>
            <th>5 大 Agent 挂载点 (C / G / D / Z / X)</th>
            <th>依赖 (CLI / MCP)</th>
            <th>上次更新</th>
            <th>巡检断言</th>
          </tr>
        </thead>
        <tbody id="skillsTbody">
          <!-- JS 动态渲染 -->
        </tbody>
      </table>
    </div>

    <footer>
      dc-skills 全能力农场监控架构 · 数据源: assets-doctor --export-snapshot · 契约遵循 docs/specs/全资产治理.md
    </footer>
  </div>

  <script>
    const DATA = {doc_json};
    const skills = DATA.skills || [];
    const agents = (DATA.summary && DATA.summary.agents) || [];

    // 初始化族群下拉项
    const families = Array.from(new Set(skills.map(s => s.family).filter(Boolean))).sort();
    const familySelect = document.getElementById('familyFilter');
    families.forEach(f => {{
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = f;
      familySelect.appendChild(opt);
    }});
    const optNone = document.createElement('option');
    optNone.value = "__none__";
    optNone.textContent = "无族群 (Standalone)";
    familySelect.appendChild(optNone);

    function agentChar(agent) {{
      if (agent === "claude-code") return "C";
      if (agent === "grok") return "G";
      if (agent === "dsh") return "D";
      if (agent === "zcode") return "Z";
      if (agent === "codex") return "X";
      return agent.slice(0, 1).toUpperCase();
    }}

    function renderTable() {{
      const q = document.getElementById('searchInput').value.toLowerCase().trim();
      const fam = document.getElementById('familyFilter').value;
      const role = document.getElementById('roleFilter').value;
      const load = document.getElementById('loadFilter').value;
      const status = document.getElementById('statusFilter').value;

      const tbody = document.getElementById('skillsTbody');
      tbody.innerHTML = '';

      const filtered = skills.filter(s => {{
        if (q) {{
          const matchName = s.name.toLowerCase().includes(q);
          const matchDesc = (s.description || '').toLowerCase().includes(q);
          const matchDeps = (s.deps.cli || []).concat(s.deps.mcp || []).join(' ').toLowerCase().includes(q);
          if (!matchName && !matchDesc && !matchDeps) return false;
        }}
        if (fam !== 'all') {{
          if (fam === '__none__' && s.family !== null) return false;
          if (fam !== '__none__' && s.family !== fam) return false;
        }}
        if (role !== 'all' && s.role !== role) return false;
        if (load !== 'all' && s.load_mode !== load) return false;
        if (status !== 'all' && s.status !== status) return false;
        return true;
      }});

      if (filtered.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#94a3b8; padding:32px;">未匹配到任何技能条目</td></tr>';
        return;
      }}

      filtered.forEach(s => {{
        const tr = document.createElement('tr');

        // 技能名与描述
        const tdName = document.createElement('td');
        tdName.innerHTML = `<div class="skill-name">${{s.name}}<span class="skill-desc">${{s.description || '—'}}</span></div>`;

        // 族群
        const tdFam = document.createElement('td');
        tdFam.textContent = s.family || '—';

        // 角色
        const tdRole = document.createElement('td');
        const roleClass = s.role === 'entry' ? 'tag-entry' : (s.role === 'member' ? 'tag-member' : 'tag-standalone');
        tdRole.innerHTML = `<span class="tag ${{roleClass}}">${{s.role}}</span>`;

        // 加载模式
        const tdLoad = document.createElement('td');
        const loadClass = s.load_mode === 'auto' ? 'tag-auto' : 'tag-manual';
        tdLoad.innerHTML = `<span class="tag ${{loadClass}}">${{s.load_mode}}</span>`;

        // Agent 挂载矩阵
        const tdMounts = document.createElement('td');
        const matrixDiv = document.createElement('div');
        matrixDiv.className = 'agent-matrix';
        agents.forEach(a => {{
          const st = (s.mounts || {{}})[a] || 'unmounted';
          const dot = document.createElement('div');
          dot.className = `dot-box dot-${{st}}`;
          dot.textContent = agentChar(a);
          dot.title = `${{a}}: ${{st}}`;
          matrixDiv.appendChild(dot);
        }});
        tdMounts.appendChild(matrixDiv);

        // 依赖
        const tdDeps = document.createElement('td');
        tdDeps.className = 'deps-cell';
        const cliPills = (s.deps.cli || []).map(c => `<span class="deps-pill">CLI:${{c}}</span>`).join('');
        const mcpPills = (s.deps.mcp || []).map(m => `<span class="deps-pill">MCP:${{m}}</span>`).join('');
        tdDeps.innerHTML = (cliPills + mcpPills) || '—';

        // 更新
        const tdCommit = document.createElement('td');
        tdCommit.style.fontSize = '12px';
        tdCommit.style.color = '#94a3b8';
        tdCommit.textContent = s.last_commit || '—';

        // 状态
        const tdStatus = document.createElement('td');
        const stClass = s.status === 'ok' ? 'status-ok' : (s.status === 'warn' ? 'status-warn' : 'status-crit');
        const icon = s.status === 'ok' ? '✅' : (s.status === 'warn' ? '⚠️' : '🚨');
        tdStatus.innerHTML = `<span class="badge-status ${{stClass}}" title="${{s.status_hint}}">${{icon}} ${{s.status}}</span>`;

        tr.appendChild(tdName);
        tr.appendChild(tdFam);
        tr.appendChild(tdRole);
        tr.appendChild(tdLoad);
        tr.appendChild(tdMounts);
        tr.appendChild(tdDeps);
        tr.appendChild(tdCommit);
        tr.appendChild(tdStatus);

        tbody.appendChild(tr);
      }});
    }}

    document.getElementById('searchInput').addEventListener('input', renderTable);
    document.getElementById('familyFilter').addEventListener('change', renderTable);
    document.getElementById('roleFilter').addEventListener('change', renderTable);
    document.getElementById('loadFilter').addEventListener('change', renderTable);
    document.getElementById('statusFilter').addEventListener('change', renderTable);

    renderTable();
  </script>
</body>
</html>
"""
    out.write_text(html_content, encoding="utf-8")
    return out
