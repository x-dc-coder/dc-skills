#!/usr/bin/env python3
"""Ai4Scholar PDF 下载器（走本地 stdio MCP）。

背景：Ai4Scholar 托管版 MCP（https://mcp.ai4scholar.net/sse）的 download_* 工具
会返回"该工具仅在本地模式（stdio）下可用"；官方 REST API 也只有元数据（没有下载端点）。
所以真正能落盘下载 PDF 的路径是：本地跑 ai4scholar-mcp（stdio）+ AI4SCHOLAR_API_KEY。

一次性环境准备（约 1 分钟）：
    uv venv --python 3.12 ~/.local/share/ai4scholar-mcp/venv
    uv pip install --python ~/.local/share/ai4scholar-mcp/venv/bin/python ai4scholar-mcp "mcp<2"
（必须 mcp<2：ai4scholar-mcp 0.4.0 用的是旧版 mcp.server.fastmcp API）

用法：
    cd ~/projects/dc-skills
    uv run python unified-search/scripts/ai4scholar_download.py --doi 10.48550/arXiv.1706.03762
    uv run python unified-search/scripts/ai4scholar_download.py --arxiv 1706.03762 --out ./papers
    uv run python unified-search/scripts/ai4scholar_download.py --semantic 659bf9ce7175e1ec266ff54359e2bd76e0b7ff31
    uv run python unified-search/scripts/ai4scholar_download.py --list-tools
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path.home() / ".config" / "vision-ai" / ".env"
DEFAULT_VENV = Path.home() / ".local" / "share" / "ai4scholar-mcp" / "venv" / "bin" / "python"

TOOLS = {
    "doi": ("download_pdf_by_doi", "doi"),
    "arxiv": ("download_arxiv", "paper_id"),
    "semantic": ("download_semantic", "paper_id"),
}


def load_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if os.environ.get("AI4SCHOLAR_API_KEY"):
        return os.environ["AI4SCHOLAR_API_KEY"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("AI4SCHOLAR_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def find_python(explicit: str | None = None) -> Path | None:
    for cand in (explicit, os.environ.get("AI4SCHOLAR_MCP_PYTHON"), str(DEFAULT_VENV)):
        if cand and Path(cand).exists():
            return Path(cand)
    try:
        import ai4scholar_mcp  # noqa: F401
        return Path(sys.executable)
    except Exception:
        return None


def default_out_dir() -> Path:
    cwd = Path.cwd()
    if cwd == SKILL_DIR.parent:  # 在 ~/projects/dc-skills 下运行 → 统一兜底目录
        return Path.home() / ".claude" / "skills-output" / "unified-search" / "downloads"
    return cwd / "unified-search-output" / "downloads"


class StdioMCP:
    """最小 MCP stdio 客户端（换行分隔 JSON-RPC，够用且不引入依赖）。"""

    def __init__(self, python: Path, api_key: str, timeout: int = 180):
        env = dict(os.environ)
        env["AI4SCHOLAR_API_KEY"] = api_key
        # 去掉 [::1]：DSH 注入的 no_proxy 会让 httpx/requests 解析崩溃
        for var in ("NO_PROXY", "no_proxy"):
            raw = env.get(var)
            if raw:
                env[var] = ",".join(t.strip() for t in raw.split(",")
                                    if t.strip() and not (t.strip().startswith("[") and t.strip().endswith("]")))
        self.timeout = timeout
        self.proc = subprocess.Popen(
            [str(python), "-m", "ai4scholar_mcp.server", "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, bufsize=1)
        self._responses: dict = {}
        self._stderr: list = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and "id" in obj:
                self._responses[obj["id"]] = obj

    def _read_stderr(self):
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())

    def rpc(self, method: str, params=None, notify: bool = False):
        mid = str(uuid.uuid4())
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = mid
        if params is not None:
            msg["params"] = params
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e)}
        if notify:
            return None
        end = time.time() + self.timeout
        while time.time() < end:
            if mid in self._responses:
                return self._responses.pop(mid)
            if self.proc.poll() is not None:
                return {"error": "server exited: " + " | ".join(self._stderr[-2:])[:300]}
            time.sleep(0.15)
        return {"error": "timeout after %ds" % self.timeout}

    def handshake(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "unified-search", "version": "1"}})
        if "error" in r:
            raise RuntimeError("MCP initialize failed: %s" % r["error"])
        self.rpc("notifications/initialized", {}, notify=True)
        return (r.get("result") or {}).get("serverInfo", {})

    def tools(self):
        r = self.rpc("tools/list")
        return (r.get("result") or {}).get("tools") or []

    def call(self, name: str, arguments: dict):
        r = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if "error" in r:
            return False, str(r["error"])
        res = r.get("result") or {}
        text = " ".join(p.get("text", "") for p in res.get("content", []) if isinstance(p, dict))
        return (not res.get("isError")), text

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Ai4Scholar PDF 下载器（本地 stdio MCP）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--doi", help="按 DOI 下载（Unpaywall/出版商页面/机构权限）")
    g.add_argument("--arxiv", help="按 arXiv id 下载")
    g.add_argument("--semantic", help="按 Semantic Scholar paperId 下载")
    ap.add_argument("--out", type=Path, default=None, help="保存目录（默认见 OUTPUT 约定）")
    ap.add_argument("--list-tools", action="store_true", help="列出本地 MCP 工具后退出")
    ap.add_argument("--venv-python", default=None, help="本地 ai4scholar-mcp 所在 python 路径")
    ap.add_argument("--api-key", default=None, help="覆盖 AI4SCHOLAR_API_KEY")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    key = load_api_key(args.api_key)
    python = find_python(args.venv_python)
    result: dict = {"ok": False}
    if not key:
        result["error"] = "未找到 AI4SCHOLAR_API_KEY（可写入 ~/.config/vision-ai/.env 或用 --api-key）"
    elif python is None:
        result["error"] = ("未找到本地 ai4scholar-mcp。请先执行：\n"
                           "  uv venv --python 3.12 ~/.local/share/ai4scholar-mcp/venv\n"
                           "  uv pip install --python ~/.local/share/ai4scholar-mcp/venv/bin/python "
                           "ai4scholar-mcp \"mcp<2\"")
    else:
        mcp = StdioMCP(python, key)
        try:
            info = mcp.handshake()
            result["server"] = info
            if args.list_tools:
                result.update(ok=True, tools=[t["name"] for t in mcp.tools()])
            else:
                which = "doi" if args.doi else ("arxiv" if args.arxiv else ("semantic" if args.semantic else None))
                if not which:
                    result["error"] = "需要 --doi / --arxiv / --semantic 之一，或用 --list-tools"
                else:
                    tool, argname = TOOLS[which]
                    out_dir = (args.out or default_out_dir()).resolve()
                    out_dir.mkdir(parents=True, exist_ok=True)
                    value = {"doi": args.doi, "arxiv": args.arxiv, "semantic": args.semantic}[which]
                    ok, text = mcp.call(tool, {argname: value, "save_path": str(out_dir)})
                    files = sorted(str(p) for p in out_dir.glob("*") if p.suffix.lower() == ".pdf")
                    result.update(ok=ok, tool=tool, target=value, out_dir=str(out_dir),
                                  message=text.strip()[:600], pdfs=files)
        finally:
            mcp.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("ok"):
            if args.list_tools:
                print("本地 ai4scholar-mcp 工具（%d 个）：" % len(result["tools"]))
                print("  " + ", ".join(result["tools"]))
            else:
                print(result.get("message") or "下载完成")
                for f in result.get("pdfs", []):
                    p = Path(f)
                    print("  -> %s (%d bytes)" % (f, p.stat().st_size))
        else:
            print("失败：%s" % result.get("error", "unknown"), file=sys.stderr)
            return 1
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
