#!/usr/bin/env python3
"""
skill-update-check.py
SKILL 外部依赖「更新通道」统一扫描器（只读、零副作用、绝不执行升级）。

设计原则（与 skill-doctor / skillctl 分工明确）：
  - skill-doctor  → 有效性（能不能跑），零网络 <200ms
  - 本工具        → 版本（有没有新版），只读网络，可 TTL 缓存
  - skillctl      → 生命周期（增删改），只动本地文件

通道（全部只读探测）：
  npm      registry.npmjs.org 全量 versions 取最高 semver（防 latest tag 滞后误报）
  pypi     pypi.org/pypi/<pkg>/json（paper-reader 引擎）
  github   api.github.com/repos/<o>/<r>/releases/latest
  self     无公开 registry 的自更新二进制：只报当前版本 + 官方升级方式
  baseline 系统包（apt/nvm 管理）：不联网，仅与 ENVIRONMENT.md 登记值比对漂移

用法：
  skill-update-check                # 全量扫描
  skill-update-check --json         # 机器可读
  skill-update-check --focus engines  # 只看 paper-reader 引擎
  skill-update-check --only lark-cli  # 只看单项
  skill-update-check --no-network   # 离线：只报本地版本与登记漂移
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parent.parent
# 2026-09-24 文档归类：ENVIRONMENT.md 移至 docs/arch/；保留根级回退兼容旧检出
ENVIRONMENT_MD = SKILLS_ROOT / "docs" / "arch" / "ENVIRONMENT.md"
if not ENVIRONMENT_MD.exists():
    ENVIRONMENT_MD = SKILLS_ROOT / "ENVIRONMENT.md"

# ── 依赖登记表（通道驱动；新增依赖只需在此加一行）────────────────────────────
TRACKED_DEPS: list[dict] = [
    # ── npm 全局包通道 ────────────────────────────────────────────────────
    {
        "id": "lark-cli",
        "skill": "lark-cli",
        "bin": "lark-cli",
        "channel": "npm",
        "pkg": "@larksuite/cli",
        "upgrade": "lark-cli update   或   npm i -g @larksuite/cli",
        "notes": "飞书全家桶 23 域底座，模型目录强依赖",
    },
    {
        "id": "mmdc",
        "skill": "diagram",
        "bin": "mmdc",
        "channel": "npm",
        "pkg": "@mermaid-js/mermaid-cli",
        "upgrade": "npm i -g @mermaid-js/mermaid-cli",
        "notes": "diagram sequence 类渲染",
    },
    # ── GitHub Releases 通道 ─────────────────────────────────────────────
    {
        "id": "keenable",
        "skill": "unified-search",
        "bin": "keenable",
        "channel": "github",
        "repo": "keenableai/keenable-cli",
        "upgrade": "重跑官方 installer（见 unified-search/references/keenable-setup.md）",
        "notes": "unified-search 的 keenable 源硬依赖",
    },
    # ── PyPI 通道（paper-reader 双引擎）───────────────────────────────────
    {
        "id": "marker-pdf",
        "skill": "paper-reader",
        "bin": None,
        "channel": "pypi",
        "pkg": "marker-pdf",
        "venv": "paper-reader/venvs/marker",
        "dist": "marker_pdf",
        "upgrade": "uv pip install --python paper-reader/venvs/marker/bin/python marker-pdf==<version>",
        "notes": "学术 PDF 解析引擎（双引擎之一）",
    },
    {
        "id": "mineru",
        "skill": "paper-reader",
        "bin": None,
        "channel": "pypi",
        "pkg": "mineru",
        "venv": "paper-reader/venvs/mineru",
        "dist": "mineru",
        "upgrade": "uv pip install --python paper-reader/venvs/mineru/bin/python mineru==<version>",
        "notes": "版面分析与公式提取引擎（双引擎之一）",
    },
    # ── 自更新二进制通道（无公开 registry）───────────────────────────────
    {
        "id": "officecli",
        "skill": "officecli",
        "bin": "officecli",
        "channel": "self",
        "upgrade": "curl -fsSL https://d.officecli.ai/install.sh | bash",
        "notes": "docx/xlsx/pptx 处理；无公开版本 API，只能查官方安装脚本",
    },
    {
        "id": "codegraph",
        "skill": "（工具）代码知识图谱 / MCP",
        "bin": "codegraph",
        "channel": "github",
        "repo": "colbymchenry/codegraph",
        "upgrade": "codegraph upgrade",
        "notes": "符号级代码索引与调用图；Claude Code 侧已挂 MCP，DSH 侧见 cordis.patch.yml",
    },
    # ── 基线比对通道（系统包，不联网）────────────────────────────────────
    {"id": "uv", "skill": "全部 Python 技能", "bin": "uv", "channel": "baseline",
     "upgrade": "uv self update"},
    {"id": "gh", "skill": "github-workflow", "bin": "gh", "channel": "baseline",
     "upgrade": "见官方安装文档"},
    {"id": "git", "skill": "全部（github-workflow 为基座）", "bin": "git", "channel": "baseline",
     "upgrade": "sudo apt update && sudo apt install git"},
    {"id": "node", "skill": "drawio-xml / lark-cli / diagram", "bin": "node", "channel": "baseline",
     "baseline_key": "node / npx", "upgrade": "nvm install --lts"},
    {"id": "graph-easy", "skill": "diagram（draft 类）", "bin": "graph-easy", "channel": "baseline",
     "upgrade": "sudo apt install libgraph-easy-perl graphviz"},
    {"id": "mysql", "skill": "db-skill", "bin": "mysql", "channel": "baseline",
     "upgrade": "系统包 / 容器"},
    {"id": "python3", "skill": "脚本运行（uv 环境内）", "bin": "python3", "channel": "baseline",
     "upgrade": "系统包"},
]

SEMVER_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


# ══ 网络层（只读，直连失败自动回退本机代理）══════════════════════════════════

def _openers() -> list[urllib.request.OpenerDirector]:
    """返回可用的 opener 列表：优先环境代理/直连，回退本机 7890 代理。"""
    candidates: list[dict | None] = []
    env_proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")
    if env_proxy:
        candidates.append({"http": env_proxy, "https": env_proxy})
    candidates.append(None)  # 直连
    candidates.append({"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
    return [urllib.request.build_opener(urllib.request.ProxyHandler(p)) for p in candidates]


def fetch_json(url: str, timeout: int = 8) -> dict:
    """只读 GET JSON；直连/代理依次尝试，全失败抛最后一次异常。"""
    last_err: Exception | None = None
    for opener in _openers():
        req = urllib.request.Request(url, headers={"User-Agent": "skill-update-check/1.0"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 逐通道降级
            last_err = e
    raise last_err if last_err else RuntimeError("no opener succeeded")


# ══ 版本解析与比较 ══════════════════════════════════════════════════════════

def parse_semver(text: str) -> tuple[int, int, int] | None:
    """从任意版本字符串提取 (major, minor, patch)。"""
    m = SEMVER_RE.search(text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_semver(candidate), parse_semver(current)
    if a is None or b is None:
        return False
    return a > b


def highest_semver(versions: list[str]) -> str | None:
    """从版本列表中取最高版本（忽略 prerelease 复杂度，取数值最大）。"""
    best, best_key = None, None
    for v in versions:
        key = parse_semver(v)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = v, key
    return best


# ══ 本地版本探测 ════════════════════════════════════════════════════════════

def probe_bin_version(bin_name: str, timeout: int = 5) -> str | None:
    """运行 <bin> --version 并返回含版本号的那一行（失败返回 None）。

    注意：部分工具（如 graph-easy）打印版本后以非 0 退出码结束，
    因此只要能提取到版本号即视为有效，不强制 returncode == 0。
    """
    for args in (["--version"], ["-V"], ["version"]):
        try:
            r = subprocess.run([bin_name, *args], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        out = (r.stdout or r.stderr or "").strip()
        if not out:
            continue
        for line in out.splitlines():
            if SEMVER_RE.search(line):
                return line.strip()
        if r.returncode == 0:
            return out.splitlines()[0].strip()
    return None


def probe_venv_version(rel_venv: str, dist: str) -> str | None:
    """零子进程：扫描 venv site-packages 的 <dist>-<ver>.dist-info 目录。"""
    venv = SKILLS_ROOT / rel_venv
    if not venv.exists():
        return None
    sp_dirs = list(venv.glob("lib/python*/site-packages")) + list(venv.glob("Lib/site-packages"))
    if not sp_dirs:
        return None
    pattern = re.compile(rf"^{re.escape(dist)}-([0-9A-Za-z._\-]+)\.dist-info$")
    for item in sp_dirs[0].iterdir():
        if item.is_dir():
            m = pattern.match(item.name)
            if m:
                return m.group(1)
    return None


# ══ ENVIRONMENT.md 登记基线 ═════════════════════════════════════════════════

def read_env_baseline() -> dict[str, str]:
    """解析 ENVIRONMENT.md 依赖登记表 → {工具名: 登记版本}。"""
    baseline: dict[str, str] = {}
    if not ENVIRONMENT_MD.exists():
        return baseline
    for line in ENVIRONMENT_MD.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "工具" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        tool, ver = cells[0], cells[1]
        if tool and ver and re.search(r"\d+\.\d+", ver):
            baseline[tool] = ver
    return baseline


# ══ 各通道最新版本探测 ══════════════════════════════════════════════════════

def latest_npm(pkg: str) -> tuple[str | None, str | None]:
    try:
        doc = fetch_json(f"https://registry.npmjs.org/{pkg}")
        versions = list(doc.get("versions", {}).keys())
        newest = highest_semver(versions)
        return newest or doc.get("dist-tags", {}).get("latest"), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def latest_pypi(pkg: str) -> tuple[str | None, str | None]:
    try:
        doc = fetch_json(f"https://pypi.org/pypi/{pkg}/json")
        return doc.get("info", {}).get("version"), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def latest_github(repo: str) -> tuple[str | None, str | None]:
    try:
        doc = fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
        return doc.get("tag_name"), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


# ══ 主扫描逻辑 ══════════════════════════════════════════════════════════════

def scan(no_network: bool = False) -> list[dict]:
    baseline = read_env_baseline()
    results: list[dict] = []

    for dep in TRACKED_DEPS:
        local_raw: str | None = None
        if dep.get("venv"):
            local_raw = probe_venv_version(dep["venv"], dep["dist"])
        elif dep.get("bin"):
            local_raw = probe_bin_version(dep["bin"])

        local_ver = None
        if local_raw:
            m = SEMVER_RE.search(local_raw)
            local_ver = m.group(0) if m else local_raw

        entry = {
            "id": dep["id"],
            "skill": dep["skill"],
            "channel": dep["channel"],
            "notes": dep.get("notes", ""),
            "upgrade": dep.get("upgrade", ""),
            "local_version": local_ver,
            "local_raw": local_raw,
            "latest_version": None,
            "status": "UNKNOWN",
            "detail": "",
            "drift": None,
        }

        # 通用登记漂移检测：任何通道都对照 ENVIRONMENT.md 登记值
        key = dep.get("baseline_key", dep["id"])
        registered = baseline.get(key) or baseline.get(dep.get("bin", ""))
        entry["registered_version"] = registered
        if local_ver and registered and parse_semver(registered) != parse_semver(local_ver):
            entry["drift"] = f"ENVIRONMENT.md 登记 {registered} ≠ 实际 {local_ver}"

        if local_ver is None:
            entry["status"] = "MISSING"
            entry["detail"] = "未安装或无法探测版本"
            results.append(entry)
            continue

        if no_network:
            entry["status"] = "OFFLINE"
            entry["detail"] = "离线模式：未查询远程"
        elif dep["channel"] == "npm":
            latest, err = latest_npm(dep["pkg"])
            entry["latest_version"] = latest
            entry["status"] = _classify(local_ver, latest, err)
        elif dep["channel"] == "pypi":
            latest, err = latest_pypi(dep["pkg"])
            entry["latest_version"] = latest
            entry["status"] = _classify(local_ver, latest, err)
        elif dep["channel"] == "github":
            latest, err = latest_github(dep["repo"])
            entry["latest_version"] = latest
            entry["status"] = _classify(local_ver, latest, err)
        elif dep["channel"] == "self":
            entry["status"] = "SELF_UPDATE"
            entry["detail"] = "无公开版本 API；请用官方升级方式或手动核对"
        elif dep["channel"] == "baseline":
            if registered is None:
                entry["status"] = "NO_BASELINE"
                entry["detail"] = "ENVIRONMENT.md 无登记"
            elif entry["drift"]:
                entry["status"] = "DRIFT"
                entry["detail"] = entry["drift"]
            else:
                entry["status"] = "BASELINE_OK"
                entry["detail"] = f"与登记值一致（{registered}）"

        results.append(entry)

    return results


def _classify(local: str, latest: str | None, err: str | None) -> str:
    if latest is None:
        return "UNKNOWN"
    return "UPDATE_AVAILABLE" if is_newer(latest, local) else "UP_TO_DATE"


# ══ 渲染 ════════════════════════════════════════════════════════════════════

GROUP_ORDER = [
    ("UPDATE_AVAILABLE", "🆙 可更新"),
    ("UP_TO_DATE", "✅ 已是最新"),
    ("SELF_UPDATE", "❓ 无公开版本通道（需手动核对）"),
    ("DRIFT", "📋 登记漂移（ENVIRONMENT.md 与实机不一致）"),
    ("BASELINE_OK", "📌 系统包（与登记值一致）"),
    ("NO_BASELINE", "📌 系统包（无登记基线）"),
    ("MISSING", "⛔ 缺失"),
    ("OFFLINE", "💤 离线未查"),
    ("UNKNOWN", "❔ 查询失败"),
]


def render(reports: list[dict], focus: str | None = None) -> None:
    print(f"\n🔄 SKILL 依赖更新扫描 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 88)

    grouped: dict[str, list[dict]] = {}
    for r in reports:
        grouped.setdefault(r["status"], []).append(r)

    for status, title in GROUP_ORDER:
        items = grouped.get(status)
        if not items:
            continue
        print(f"\n{title} ({len(items)})")
        for r in items:
            loc = (r["local_version"] or "-").ljust(9)
            if status == "UPDATE_AVAILABLE":
                lat = (r["latest_version"] or "-").ljust(9)
                print(f"   {r['id']:<12} {loc} → {lat} [{r['channel']}]")
                print(f"      依赖技能: {r['skill']}")
                if r["notes"]:
                    print(f"      说明: {r['notes']}")
                print(f"      升级: {r['upgrade']}")
            elif status == "UP_TO_DATE":
                print(f"   {r['id']:<12} {loc} [{r['channel']}]")
            elif status == "SELF_UPDATE":
                print(f"   {r['id']:<12} {loc} [{r['channel']}] {r['detail']}")
                print(f"      升级: {r['upgrade']}")
            elif status in ("DRIFT", "BASELINE_OK", "NO_BASELINE"):
                print(f"   {r['id']:<12} {loc} {r['detail']}")
            else:
                print(f"   {r['id']:<12} {loc} [{r['channel']}] {r['detail']}")

    # engines 模式附加蓝绿升级规程
    if focus == "engines":
        print("\n💡 paper-reader 蓝绿安全升级规程：")
        print("   1. 绝不原地 upgrade 生产环境；先备份 venvs/marker 与 venvs/mineru")
        print("   2. 新建候选沙箱: uv venv venvs/<engine>-candidate --python 3.10")
        print("   3. 编写适配层抹平 CLI/API 差异，跑 benchmark 对比后再切换")

    # 跨通道登记漂移汇总（含 npm/pypi/github/self 等非 baseline 通道）
    drift_items = [r for r in reports if r.get("drift") and r["status"] != "DRIFT"]
    if drift_items:
        print(f"\n📋 ENVIRONMENT.md 登记漂移汇总（{len(drift_items)}）")
        for r in drift_items:
            print(f"   {r['id']:<12} {r['drift']}   → 建议同步更新登记表")

    print("\n" + "=" * 88)
    print("⚠️  本工具只读：检测 ≠ 执行。升级请由用户显式执行上述命令。\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="SKILL 外部依赖更新通道扫描器（只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--focus", choices=["engines"], help="只看 paper-reader 引擎")
    ap.add_argument("--only", help="只看指定依赖 id（如 lark-cli）")
    ap.add_argument("--no-network", action="store_true", help="离线：不查询远程")
    args = ap.parse_args()

    reports = scan(no_network=args.no_network)

    if args.only:
        reports = [r for r in reports if r["id"] == args.only]
    elif args.focus == "engines":
        reports = [r for r in reports if r["id"] in ("marker-pdf", "mineru")]

    if args.json:
        print(json.dumps({
            "scanned_at": datetime.now().isoformat(),
            "count": len(reports),
            "deps": reports,
        }, indent=2, ensure_ascii=False))
        return

    render(reports, focus=args.focus)


if __name__ == "__main__":
    main()
