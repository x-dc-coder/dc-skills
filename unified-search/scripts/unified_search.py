#!/usr/bin/env python3
"""
unified_search.py — Unified web + academic search aggregator.

Modes:
  general  : 2-source parallel (keenable + tavily), arbitration by firecrawl if divergence
  academic : 4-source parallel (arxiv + dblp + semantic_scholar + openalex), paper links recorded
  fetch    : single-URL content extraction (firecrawl markdown / keenable / tavily extract)
  history  : query past search cache

Usage:
  uv run python unified-search/scripts/unified_search.py "query" [--mode general|academic|auto]
  uv run python unified-search/scripts/unified_search.py "query" --mode academic --top 10
  uv run python unified-search/scripts/unified_search.py --fetch <url> [--strategy markdown_body|fast_summary|ai_extract]
  uv run python unified-search/scripts/unified_search.py --history "query"
  uv run python unified-search/scripts/unified_search.py --quota
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import yaml
from feedparser import parse as feedparse


def _load_env_file(path: Path) -> None:
    """轻量加载 .env（无依赖，不覆盖已存在环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


# 自读统一密钥文件（~/.config/vision-ai/.env）
_load_env_file(Path.home() / ".config" / "vision-ai" / ".env")


def _sanitize_no_proxy_env() -> None:
    """剔除 no_proxy 里的方括号 IPv6 条目。

    DSH（@deepseek-ai/dsh-http-proxy 的 LOOPBACK_NO_PROXY）会把
    [localhost,127.0.0.1,::1,[::1]] 连同 NODE_USE_ENV_PROXY=1 注入所有子进程。
    Node/undici 需要方括号写法，但 httpx 解析 no_proxy 时把每个条目丢给 urlparse()，
    方括号里的 ::1 会被当成 host:port 并抛 InvalidURL: Invalid port: ':1]' —— 于是本脚本
    所有 HTTP 源在发请求前就全部失败。这里只删带方括号的条目（裸 ::1 保留，语义不变）。
    """
    for var in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(var)
        if not raw:
            continue
        keep = [t.strip() for t in raw.split(",")
                if t.strip() and not re.fullmatch(r"\[.*\]", t.strip())]
        cleaned = ",".join(keep)
        if cleaned != raw:
            os.environ[var] = cleaned


_sanitize_no_proxy_env()

# ─── Paths ──────────────────────────────────────────────────────────────────
SKILL_DIR = Path(__file__).resolve().parent.parent          # ~/projects/dc-skills/unified-search
SCRIPT_DIR = Path(__file__).resolve().parent                # .../scripts
CONFIG_PATH = SKILL_DIR / "config.json"
# 全局运行时状态（docs/specs/OUTPUT.md C-8 登记）：跨项目共享的配额/历史/缓存，
# 不得放技能目录内；按 cwd 拆分会破坏配额感知，故固定全局位置。
STATE_ROOT = Path(os.environ.get("DC_SKILLS_STATE_DIR",
                                 Path.home() / ".local" / "state" / "dc-skills"))
DATA_DIR = STATE_ROOT / "unified-search" / "data"
CACHE_DIR = STATE_ROOT / "unified-search" / "cache"
DB_PATH = DATA_DIR / "history.db"
QUOTA_PATH = DATA_DIR / "quota.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Config ─────────────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_api_key(cfg: dict, name: str) -> str | None:
    ak = cfg["api_keys"].get(name, {})
    env_var = ak.get("env_var")
    val = os.environ.get(env_var) if env_var else None
    if not val:
        val = ak.get("value")
    return val


# ─── Quota management ───────────────────────────────────────────────────────
def load_quota() -> dict:
    if QUOTA_PATH.exists():
        with open(QUOTA_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_quota(quota: dict) -> None:
    QUOTA_PATH.write_text(json.dumps(quota, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Source circuit breaker ─────────────────────────────────────────────────
# 401/402/403（密钥失效 / 额度耗尽 / 无权限）属于重试也没用的错误：命中一次就在本进程内
# 熔断该源，避免每次搜索都真的再打一次 API（firecrawl 402 就是这种情况）。
_DEAD_SOURCES: dict[str, str] = {}


def disable_source(name: str, reason: str) -> None:
    _DEAD_SOURCES.setdefault(name, reason)


def source_disabled(name: str) -> bool:
    return name in _DEAD_SOURCES


def _disabled_result(name: str) -> list[dict]:
    return [make_result("", "", "[%s disabled this run: %s]" % (name, _DEAD_SOURCES.get(name, "")),
                        name, 0.0, error="source_disabled")]


def current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def quota_available(cfg: dict, source: str) -> bool:
    """Return True if source still has monthly quota (for metered sources)."""
    qcfg = cfg.get("quota", {}).get(source)
    if not qcfg:
        return True
    quota = load_quota()
    month = current_month_key()
    used = quota.get(source, {}).get(month, 0)
    return used < qcfg["monthly_limit"]


def quota_consume(source: str, n: int = 1) -> None:
    """Record n calls against source's monthly quota."""
    quota = load_quota()
    month = current_month_key()
    entry = quota.setdefault(source, {})
    entry[month] = entry.get(month, 0) + n
    save_quota(quota)


def quota_remaining(cfg: dict, source: str) -> int | None:
    qcfg = cfg.get("quota", {}).get(source)
    if not qcfg:
        return None
    quota = load_quota()
    used = quota.get(source, {}).get(current_month_key(), 0)
    return max(0, qcfg["monthly_limit"] - used)


# ─── Result schema ──────────────────────────────────────────────────────────
def make_result(title: str, url: str, snippet: str = "", source: str = "",
                score: float = 0.0, **extra) -> dict:
    r = {
        "title": title,
        "url": url,
        "snippet": snippet,
        "source": source,
        "score": float(score),
    }
    r.update(extra)
    return r


# ─── URL normalization for dedup ────────────────────────────────────────────
def normalize_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", p.path or "")
    # drop fragment, lowercase scheme
    return urlunparse((p.scheme.lower() or "https", netloc, path, "", "", ""))


# ─── Source adapters ────────────────────────────────────────────────────────

def search_zhipu(query: str, cfg: dict, max_results: int | None = None) -> list[dict]:
    """智谱 web_search（search_std 引擎）。GLM Coding Plan 套餐 Key 可调用，按次计积分。

    响应 search_result[] 字段：title / link / content / media / publish_date / refer / icon。
    注意：search_std / search_pro 均不回传 URL（link 为空字符串），只给标题+正文摘要；
    本源定位为「中文摘要引擎」，无链接结果不参与按 URL 合并，但保留标题供排序展示。
    """
    key = get_api_key(cfg, "zhipu")
    if not key:
        return [make_result("", "", "[zhipu: no API key]", "zhipu", 0.0, error="no_key")]
    src = cfg["sources"]["zhipu"]
    body = dict(src.get("default_params", {}))
    body["search_query"] = query
    if max_results:
        body["count"] = max_results
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.post(src["endpoint"], json=body,
                               headers={"Authorization": f"Bearer {key}"})
            if resp.status_code in (401, 402, 403):
                disable_source("zhipu", f"HTTP {resp.status_code}: {resp.text[:120]}")
            resp.raise_for_status()
        data = resp.json()
        pages = data.get("search_result") or []
        results = []
        for r in pages:
            results.append(make_result(
                title=r.get("title") or "",
                url=r.get("link") or r.get("url") or "",
                snippet=(r.get("content") or "")[:400],
                source="zhipu",
                score=0.75,
                published_date=r.get("publish_date"),
                no_link=True,
            ))
        return results
    except Exception as e:
        return [make_result("", "", f"[zhipu error: {type(e).__name__}: {e}]", "zhipu", 0.0, error=str(e))]


def search_keenable(query: str, cfg: dict, timeout: int | None = None) -> list[dict]:
    src = cfg["sources"]["keenable"]
    to = timeout or src["timeout_sec"]
    try:
        proc = subprocess.run(
            [src["command"], "search", query, "--site", ""],
            capture_output=True, text=True, timeout=to
        )
        # keenable search prints a YAML *wrapper* to stdout:
        #   query: ...
        #   mode: pro
        #   results:
        #   - title: ...
        #     url: ...
        # Older releases could append a trailing "Update: ..." notice that
        # breaks YAML parsing, so strip anything before the first mapping key
        # and tolerate a bare list for forward compatibility.
        out = proc.stdout
        if not out.strip():
            return []
        docs = _load_keenable_yaml(out)
        if isinstance(docs, dict):
            docs = docs.get("results") or []
        if not isinstance(docs, list):
            docs = [docs] if docs else []
        results = []
        for d in docs:
            if not isinstance(d, dict):
                continue
            title = d.get("title", "") or ""
            url = d.get("url", "") or ""
            if not title and not url:
                continue
            results.append(make_result(
                title=title,
                url=url,
                snippet=d.get("snippet") or d.get("description", ""),
                source="keenable",
                score=0.6,
                published_date=_iso_date(d.get("published_at")),
            ))
        return results
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        return [make_result("", "", f"[keenable error: {type(e).__name__}: {e}]", "keenable", 0.0, error=str(e))]


def _iso_date(value) -> str | None:
    """把 YAML 解析出的 datetime / date 归一为 ISO 字符串（JSON 可序列化）。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_keenable_yaml(out: str):
    """解析 keenable 的 YAML 输出，容忍尾部 update 提示等噪声。"""
    try:
        return yaml.safe_load(out)
    except yaml.YAMLError:
        # 截断到最后一个 YAML 顶格键（如 "results:"）之后仍失败时，逐行裁剪尾部噪声
        lines = out.splitlines()
        for cut in range(len(lines), 0, -1):
            chunk = "\n".join(lines[:cut])
            try:
                return yaml.safe_load(chunk)
            except yaml.YAMLError:
                continue
        raise


def search_tavily(query: str, cfg: dict, advanced: bool = False, topic: str | None = None,
                  time_range: str | None = None) -> list[dict]:
    if source_disabled("tavily"):
        return _disabled_result("tavily")
    if not quota_available(cfg, "tavily"):
        return [make_result("", "", "[tavily quota exhausted this month]", "tavily", 0.0, error="quota_exhausted")]
    key = get_api_key(cfg, "tavily")
    if not key:
        return [make_result("", "", "[tavily: no API key]", "tavily", 0.0, error="no_key")]
    src = cfg["sources"]["tavily"]
    params = dict(src["advanced_params" if advanced else "default_params"])
    params["query"] = query
    if topic:
        params["topic"] = topic
    if time_range:
        params["time_range"] = time_range
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.post(src["endpoint"], json=params,
                               headers={"Authorization": f"Bearer {key}"})
            if resp.status_code in (401, 402, 403):
                disable_source("tavily", "http_%d" % resp.status_code)
                return [make_result("", "", "[tavily %d: %s]" % (resp.status_code, resp.text[:200]),
                                    "tavily", 0.0, error="http_%d" % resp.status_code)]
            resp.raise_for_status()
            quota_consume("tavily")
        data = resp.json()
        answer = data.get("answer")
        results = []
        for r in data.get("results", []):
            results.append(make_result(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source="tavily",
                score=float(r.get("score", 0.7)),
                published_date=r.get("published_date"),
                domain=r.get("domain"),
            ))
        if answer:
            results.insert(0, make_result(
                "[AI Answer]", "", answer, "tavily", 1.0, kind="ai_answer"
            ))
        return results
    except Exception as e:
        return [make_result("", "", f"[tavily error: {type(e).__name__}: {e}]", "tavily", 0.0, error=str(e))]


def search_firecrawl(query: str, cfg: dict, limit: int | None = None) -> list[dict]:
    if source_disabled("firecrawl"):
        return _disabled_result("firecrawl")
    if not quota_available(cfg, "firecrawl"):
        return [make_result("", "", "[firecrawl quota exhausted this month]", "firecrawl", 0.0, error="quota_exhausted")]
    key = get_api_key(cfg, "firecrawl")
    if not key:
        return [make_result("", "", "[firecrawl: no API key]", "firecrawl", 0.0, error="no_key")]
    src = cfg["sources"]["firecrawl"]
    body = dict(src["default_params"])
    body["query"] = query
    if limit:
        body["limit"] = limit
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.post(src["endpoint"], json=body,
                               headers={"Authorization": f"Bearer {key}"})
            if resp.status_code in (401, 402, 403):
                disable_source("firecrawl", "http_%d" % resp.status_code)
                return [make_result("", "", "[firecrawl %d: %s]" % (resp.status_code, resp.text[:200]),
                                    "firecrawl", 0.0, error="http_%d" % resp.status_code)]
            resp.raise_for_status()
            quota_consume("firecrawl")
        data = resp.json()
        if not data.get("success", True):
            return [make_result("", "", f"[firecrawl: {data.get('error','')}]",
                                "firecrawl", 0.0, error=str(data.get("error")))]
        results = []
        web = (data.get("data") or {}).get("web", []) or data.get("web", [])
        for r in web:
            results.append(make_result(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
                source="firecrawl",
                score=0.65,
                markdown=r.get("markdown", "")[:2000],
            ))
        return results
    except Exception as e:
        return [make_result("", "", f"[firecrawl error: {type(e).__name__}: {e}]", "firecrawl", 0.0, error=str(e))]


def search_bocha(query: str, cfg: dict) -> list[dict]:
    key = get_api_key(cfg, "bocha")
    if not key:
        return [make_result("", "", "[bocha: no API key]", "bocha", 0.0, error="no_key")]
    src = cfg["sources"]["bocha"]
    body = dict(src.get("default_params", {}))
    body["query"] = query
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.post(src["endpoint"], json=body,
                               headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
        data = resp.json()
        d = data.get("data") or {}
        wp = d.get("webPages")
        pages = wp if isinstance(wp, list) else (wp.get("value") or [] if isinstance(wp, dict) else [])
        results = []
        for r in pages:
            results.append(make_result(
                title=r.get("name") or r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("summary") or r.get("snippet", ""),
                source="bocha",
                score=0.7,
                published_date=r.get("datePublished") or r.get("dateLastCrawled"),
            ))
        return results
    except Exception as e:
        return [make_result("", "", f"[bocha error: {type(e).__name__}: {e}]", "bocha", 0.0, error=str(e))]


def search_arxiv(query: str, cfg: dict, max_results: int = 15) -> list[dict]:
    src = cfg["sources"]["arxiv"]
    params = dict(src["default_params"])
    params["search_query"] = f"all:{query}"
    params["max_results"] = max_results
    # arxiv is strict on rate limits: use httpx directly so we can see 429
    api_err = None
    try:
        with httpx.Client(timeout=src["timeout_sec"], follow_redirects=True) as client:
            resp = client.get(src["endpoint"], params=params)
        if resp.status_code == 429:
            api_err = "429_rate_limited"
            raise RuntimeError("arxiv api rate limited")
        resp.raise_for_status()
        feed = feedparse(resp.text)
        results = []
        for e in feed.entries:
            arxiv_url = e.get("id", "")
            # 从 http://arxiv.org/abs/2506.06962v3 里取纯 arXiv id（版本号去掉）
            aid = arxiv_url.rstrip("/").split("/abs/")[-1] if "/abs/" in arxiv_url else ""
            aid = re.sub(r"v[0-9]+$", "", aid)
            pdf_link = next((l.href for l in e.get("links", []) if l.rel == "related" and "pdf" in l.href), "")
            results.append(make_result(
                title=e.get("title", "").strip().replace("\n", " "),
                url=arxiv_url,
                snippet=e.get("summary", "").strip().replace("\n", " ")[:500],
                source="arxiv",
                score=0.8,
                published=e.get("published"),
                authors=[a.name for a in e.get("authors", [])],
                journal_ref=e.get("journal_ref"),
                arxiv_id=aid,
                pdf_url=pdf_link or ("https://arxiv.org/pdf/%s" % aid if aid else None),
                pdf_link=pdf_link,
                primary_category=getattr(e, "arxiv_primary_category", {}).get("term", "") if hasattr(e, "arxiv_primary_category") else "",
            ))
        if results:
            return results
        api_err = "api_empty"
    except Exception as e:
        api_err = "%s: %s" % (type(e).__name__, e)

    # 降级：官方 export API 被限流（429）时改抓 arxiv.org 的 HTML 搜索结果页。
    # 两条通道限流互相独立，本机实测 API 持续 429 时 HTML 页稳定返回 200。
    html_results = search_arxiv_html(query, cfg, max_results=max_results)
    if any(not r.get("error") for r in html_results):
        return html_results
    detail = html_results[0].get("error") if html_results else "n/a"
    return [make_result("", "", "[arxiv error: api=%s; html_fallback=%s]" % (api_err, detail),
                        "arxiv", 0.0, error=str(api_err))]


ARXIV_HTML_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def search_arxiv_html(query: str, cfg: dict, max_results: int = 15) -> list[dict]:
    """arXiv HTML 搜索页降级通道（API 429 时使用；字段少于官方 export API）。"""
    src = cfg["sources"]["arxiv"]
    size = min(max(max_results, 25), 200)
    params = {"searchtype": "all", "query": query, "start": 0, "size": size}
    html = None
    last_err = None
    for _attempt in range(2):
        try:
            with httpx.Client(timeout=max(src["timeout_sec"], 60), follow_redirects=True,
                              headers={"User-Agent": ARXIV_HTML_UA}) as client:
                resp = client.get("https://arxiv.org/search/", params=params)
                resp.raise_for_status()
            html = resp.text
            break
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            time.sleep(2)
    if html is None:
        return [make_result("", "", "[arxiv html fallback error: %s]" % last_err,
                            "arxiv", 0.0, error=str(last_err))]

    def clean(raw):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw or "")).strip()

    results = []
    for block in re.split(r'<li class="arxiv-result">', html)[1:]:
        m_title = re.search(r'<p class="title is-5 mathjax">(.*?)</p>', block, re.S)
        m_id = re.search(r"arxiv\.org/abs/([0-9]{4}\.[0-9]{4,5})", block)
        m_abs = (re.search(r'<span class="abstract-full[^"]*"[^>]*>(.*?)</span>', block, re.S)
                 or re.search(r'<p class="abstract mathjax">(.*?)</p>', block, re.S))
        m_auth = re.search(r'<p class="authors">(.*?)</p>', block, re.S)
        m_date = re.search(r"Submitted\s*<span[^>]*>([^<]+)</span>", block)
        title = clean(m_title.group(1) if m_title else "")
        aid = m_id.group(1) if m_id else ""
        if not title and not aid:
            continue
        authors = [clean(a) for a in re.findall(r"<a[^>]*>([^<]+)</a>", m_auth.group(1))] if m_auth else []
        results.append(make_result(
            title=title,
            url="https://arxiv.org/abs/%s" % aid if aid else "",
            snippet=clean(m_abs.group(1))[:500] if m_abs else "",
            source="arxiv",
            score=0.75,
            arxiv_id=aid,
            pdf_url="https://arxiv.org/pdf/%s" % aid if aid else "",
            published=clean(m_date.group(1)) if m_date else None,
            authors=authors,
            via="html_fallback",
        ))
        if len(results) >= max_results:
            break
    if not results:
        return [make_result("", "", "[arxiv html fallback: no result blocks parsed]",
                            "arxiv", 0.0, error="html_no_results")]
    return results


def _openalex_abstract(inv, limit: int = 1500) -> str:
    """OpenAlex 的摘要存成倒排索引，这里还原成顺序文本。"""
    if not inv:
        return ""
    positions = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions[:limit])


def search_openalex(query: str, cfg: dict, max_results: int = 15) -> list[dict]:
    """OpenAlex：免费、无需 key、无人机墙，作为 dblp/arxiv 挂掉时的学术检索主力。"""
    src = cfg["sources"].get("openalex", {})
    endpoint = src.get("endpoint", "https://api.openalex.org/works")
    params = {"search": query, "per-page": max_results,
              "mailto": src.get("mailto", "unified-search@example.com")}
    try:
        with httpx.Client(timeout=src.get("timeout_sec", 25)) as client:
            resp = client.get(endpoint, params=params)
            resp.raise_for_status()
        data = resp.json()
        results = []
        for w in data.get("results", []):
            loc = w.get("primary_location") or {}
            srcinfo = loc.get("source") or {}
            oa = w.get("best_oa_location") or {}
            ids = w.get("ids") or {}
            authors = [(a.get("author") or {}).get("display_name", "")
                       for a in (w.get("authorships") or [])][:12]
            doi = (ids.get("doi") or "").replace("https://doi.org/", "")
            results.append(make_result(
                title=w.get("display_name") or w.get("title") or "",
                url=loc.get("landing_page_url") or ids.get("doi") or w.get("id", ""),
                snippet=_openalex_abstract(w.get("abstract_inverted_index"))[:500],
                source="openalex",
                score=0.8,
                year=w.get("publication_year"),
                venue=srcinfo.get("display_name"),
                authors=[a for a in authors if a],
                citation_count=w.get("cited_by_count"),
                doi=doi or None,
                pdf_url=oa.get("pdf_url"),
                openalex_id=(w.get("id") or "").rsplit("/", 1)[-1],
            ))
        return results
    except Exception as e:
        return [make_result("", "", "[openalex error: %s: %s]" % (type(e).__name__, e),
                            "openalex", 0.0, error=str(e))]


def _parse_ai4scholar_payload(data: dict, credits_left=None) -> list[dict]:
    """把 Ai4Scholar /graph/v1（Semantic Scholar 形态）的响应转成统一结果结构。"""
    results = []
    for p in data.get("data", []):
        ids = p.get("externalIds") or {}
        oa = p.get("openAccessPdf") or {}
        authors = [a.get("name", "") for a in (p.get("authors") or [])]
        pid = p.get("paperId")
        url = p.get("url") or ("https://www.semanticscholar.org/paper/%s" % pid if pid else "")
        results.append(make_result(
            title=p.get("title", ""),
            url=url,
            snippet=(p.get("abstract") or "")[:500],
            source="ai4scholar",
            score=0.84,
            paper_id=pid,
            year=p.get("year"),
            venue=p.get("venue"),
            authors=authors,
            citation_count=p.get("citationCount"),
            doi=ids.get("DOI"),
            arxiv_id=ids.get("ArXiv"),
            pdf_url=(oa.get("url") or None),
            external_ids=ids,
            credits_remaining=credits_left,
        ))
    return results


def search_ai4scholar(query: str, cfg: dict, max_results: int = 15) -> list[dict]:
    """Ai4Scholar 开放 API：REST /graph/v1（Semantic Scholar 2 亿+ 语料）。

    认证只认 Authorization: Bearer <AI4SCHOLAR_API_KEY>（x-api-key 无效）。
    每次成功调用扣积分（多数 1 分/次），可用 x-credits-charged / x-credits-remaining 对账。
    密钥放在 ~/.config/vision-ai/.env，不进 config.json。
    """
    src = cfg["sources"].get("ai4scholar", {})
    if source_disabled("ai4scholar"):
        return _disabled_result("ai4scholar")
    key = get_api_key(cfg, "ai4scholar")
    if not key:
        return [make_result("", "", "[ai4scholar: no API key (AI4SCHOLAR_API_KEY)]",
                            "ai4scholar", 0.0, error="no_key")]
    endpoint = src.get("endpoint", "https://ai4scholar.net/graph/v1/paper/search")
    fields = (src.get("default_params") or {}).get(
        "fields",
        "paperId,title,abstract,authors,year,venue,citationCount,externalIds,openAccessPdf,url")
    params = {"query": query, "limit": int(max_results), "fields": fields}
    try:
        with httpx.Client(timeout=src.get("timeout_sec", 25)) as client:
            resp = client.get(endpoint, params=params, headers={"Authorization": "Bearer " + key})
            if resp.status_code in (401, 402, 403):
                disable_source("ai4scholar", "http_%d" % resp.status_code)
                return [make_result("", "", "[ai4scholar %d: %s]" % (resp.status_code, resp.text[:160]),
                                    "ai4scholar", 0.0, error="http_%d" % resp.status_code)]
            resp.raise_for_status()
        return _parse_ai4scholar_payload(resp.json(), resp.headers.get("x-credits-remaining"))
    except Exception as e:
        return [make_result("", "", "[ai4scholar error: %s: %s]" % (type(e).__name__, e),
                            "ai4scholar", 0.0, error=str(e))]


def _parse_dblp_payload(data: dict) -> list[dict]:
    """把 dblp API 的 JSON 转成统一结果结构（普通通道与过墙通道共用）。"""
    hits = ((data.get("result") or {}).get("hits") or {}).get("hit", [])
    results = []
    for h in hits:
        info = h.get("info", {})
        # dblp may nest single author as string or list
        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", a) if isinstance(a, dict) else a for a in authors_raw] if authors_raw else []
        url = info.get("url") or info.get("ee") or ""
        if url and not url.startswith("http"):
            url = "https://dblp.org/" + url
        results.append(make_result(
            title=info.get("title", ""),
            url=url,
            snippet=("%s %s" % (info.get("venue", ""), info.get("year", ""))).strip(),
            source="dblp",
            score=0.75,
            year=info.get("year"),
            venue=info.get("venue"),
            type=info.get("type"),
            authors=authors,
            doi=info.get("doi"),
        ))
    return results


def _dblp_via_anubis(query: str, max_results: int) -> list[dict]:
    """dblp 被 Anubis 人机墙拦截时的过墙通道（纯 Python SHA-256 PoW）。

    实现在 scripts/anubis_dblp.py：解析挑战页 -> 解 PoW -> 换 auth cookie（1h 有效）
    -> 用 cookie 重新请求。cookie 缓存在 data/dblp_cookies.json。
    """
    os.environ.setdefault("DBLP_COOKIE_CACHE", str(DATA_DIR / "dblp_cookies.json"))
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import anubis_dblp
    except Exception as e:
        return [make_result("", "", "[dblp: Anubis wall; solver unavailable: %s]" % e,
                            "dblp", 0.0, error="anubis_no_solver")]
    try:
        data = anubis_dblp.dblp_search(query, h=int(max_results))
    except Exception as e:
        return [make_result("", "", "[dblp: Anubis wall; PoW solver failed: %s: %s]"
                            % (type(e).__name__, e), "dblp", 0.0, error="anubis_failed")]
    results = _parse_dblp_payload(data)
    for r in results:
        r["via"] = "anubis_pow"
    return results or [make_result("", "", "[dblp: Anubis solved but 0 hits]",
                                   "dblp", 0.0, error="anubis_empty")]


def search_dblp(query: str, cfg: dict, max_results: int = 15) -> list[dict]:
    src = cfg["sources"]["dblp"]
    params = dict(src["default_params"])
    params["q"] = query
    params["h"] = max_results
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.get(src["endpoint"], params=params)
            resp.raise_for_status()
            probe = resp.text[:3000].lower()
            if "html" in (resp.headers.get("content-type") or "").lower() or "anubis" in probe:
                return _dblp_via_anubis(query, max_results)
        return _parse_dblp_payload(resp.json())
    except Exception as e:
        # 网络层失败（超时/连接）也试一次过墙通道：dblp 的代理/直连链路实测时好时坏
        fallback = _dblp_via_anubis(query, max_results)
        if any(not r.get("error") for r in fallback):
            return fallback
        return [make_result("", "", "[dblp error: %s: %s]" % (type(e).__name__, e),
                            "dblp", 0.0, error=str(e))]


def search_semantic_scholar(query: str, cfg: dict, limit: int = 15) -> list[dict]:
    src = cfg["sources"]["semantic_scholar"]
    key = get_api_key(cfg, "semantic_scholar")
    params = dict(src["default_params"])
    params["query"] = query
    params["limit"] = limit
    headers = {}
    if key:
        headers["x-api-key"] = key
    try:
        with httpx.Client(timeout=src["timeout_sec"]) as client:
            resp = client.get(src["endpoint"], params=params, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        results = []
        for p in data.get("data", []):
            oap = p.get("openAccessPdf") or {}
            authors = [a.get("name", "") for a in p.get("authors", [])]
            results.append(make_result(
                title=p.get("title", ""),
                url=p.get("url", ""),
                snippet=(p.get("abstract") or "")[:500],
                source="semantic_scholar",
                score=0.85,
                year=p.get("year"),
                venue=p.get("venue"),
                authors=authors,
                citation_count=p.get("citationCount"),
                paper_id=p.get("paperId"),
                pdf_url=oap.get("url"),
                doi=(p.get("externalIds") or {}).get("DOI"),
                arxiv_id=(p.get("externalIds") or {}).get("ArXiv"),
            ))
        return results
    except Exception as e:
        return [make_result("", "", f"[semantic_scholar error: {type(e).__name__}: {e}]",
                            "semantic_scholar", 0.0, error=str(e))]


# ─── Parallel execution wrapper ─────────────────────────────────────────────
def run_sources_parallel(search_fn_map: dict, query: str, cfg: dict) -> dict[str, list[dict]]:
    """Run multiple source searchers in parallel threads.
    search_fn_map: {source_name: callable(query, cfg) -> list[dict]}
    """
    out: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(search_fn_map)) as pool:
        futures = {name: pool.submit(fn, query, cfg) for name, fn in search_fn_map.items()}
        for name, fut in futures.items():
            try:
                out[name] = fut.result()
            except Exception as e:
                out[name] = [make_result("", "", f"[{name} exception: {e}]", name, 0.0, error=str(e))]
    return out


# ─── Retry with backoff ─────────────────────────────────────────────────────
def search_with_retry(fn, query: str, cfg: dict, retries: int = 2, base_delay: float = 1.0,
                      **fn_kwargs) -> list[dict]:
    last_err = None
    for attempt in range(retries + 1):
        try:
            res = fn(query, cfg, **fn_kwargs) if fn_kwargs else fn(query, cfg)
            # if every result has an error field, treat as failure
            if res and all(r.get("error") for r in res):
                last_err = res[0].get("error", "")
                if attempt < retries:
                    # 429 / rate limit → longer exponential backoff (3s, 6s)
                    delay = base_delay * (3 ** attempt) if "429" in str(last_err) else base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
            return res
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(base_delay * (2 ** attempt))
    return [make_result("", "", f"[retry exhausted: {last_err}]", "retry", 0.0, error=last_err or "unknown")]


# ─── Dedup + rank ───────────────────────────────────────────────────────────
def _dedup_key(r: dict) -> str:
    """跨源去重键：优先 DOI / arXiv id，其次规范化 URL，最后回退标题。

    同一篇论文在不同源里的落地页不同（arxiv.org/abs/... vs semanticscholar.org/paper/...），
    只按 URL 去重会漏掉交叉验证；带上 DOI/arXiv id 才能让多源命中合并并加交叉验证分。
    """
    doi = (r.get("doi") or "").strip().lower().replace("https://doi.org/", "")
    if doi:
        return "doi:" + doi
    aid = (r.get("arxiv_id") or "").strip().lower()
    if aid:
        return "arxiv:" + aid
    nurl = normalize_url(r.get("url", ""))
    if nurl:
        return nurl
    return "title:" + r.get("title", "") + "|" + r.get("source", "")


def dedup_and_rank(per_source: dict[str, list[dict]], cfg: dict, top_k: int = 20) -> list[dict]:
    seen: dict[str, dict] = {}
    for source, items in per_source.items():
        weight = cfg["sources"].get(source, {}).get("weight", 1.0)
        for r in items:
            if r.get("error"):
                continue
            key = _dedup_key(r)
            if key in seen:
                # bump score if multiple sources agree (cross-validation bonus)
                seen[key]["score"] += 0.15 * weight
                seen[key].setdefault("also_from", []).append(source)
                continue
            r2 = dict(r)
            r2["score"] = r2.get("score", 0.5) * weight
            r2.setdefault("also_from", [source])
            seen[key] = r2
    ranked = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)
    return ranked[:top_k]


def sources_failed(per_source: dict) -> list:
    """列出完全失败的源，让上层能区分 没搜到 和 源挂了。"""
    out = []
    for name, items in per_source.items():
        if not items:
            out.append({"source": name, "error": "empty"})
        elif all(r.get("error") for r in items):
            out.append({"source": name, "error": str(items[0].get("error"))})
    return out


# ─── Agreement metric (Jaccard on URL hosts) ────────────────────────────────
def url_agreement(results_a: list[dict], results_b: list[dict]) -> float:
    hosts_a = {normalize_url(r.get("url", "")).split("/")[2] if normalize_url(r.get("url", "")) else "" for r in results_a if r.get("url")}
    hosts_b = {normalize_url(r.get("url", "")).split("/")[2] if normalize_url(r.get("url", "")) else "" for r in results_b if r.get("url")}
    if not hosts_a and not hosts_b:
        return 0.0
    inter = hosts_a & hosts_b
    union = hosts_a | hosts_b
    return len(inter) / len(union) if union else 0.0


# ─── Mode: general (2-source + arbitration) ──────────────────────────────────
def mode_general(query: str, cfg: dict, top_k: int = 15, tier: str = "value") -> dict:
    mcfg = cfg["modes"]["general"]
    primary = mcfg.get("tier_sources", {}).get(tier) or mcfg["primary_pair"]
    arbitrator = mcfg["arbitrator"]
    threshold = mcfg["agreement_threshold"]

    fn_map = {}
    if "keenable" in primary:
        fn_map["keenable"] = lambda q, c: search_with_retry(search_keenable, q, c)
    if "tavily" in primary:
        fn_map["tavily"] = lambda q, c: search_with_retry(search_tavily, q, c, advanced=(tier == "flagship"))
    if "bocha" in primary:
        fn_map["bocha"] = lambda q, c: search_with_retry(search_bocha, q, c)
    if "zhipu" in primary:
        fn_map["zhipu"] = lambda q, c: search_with_retry(search_zhipu, q, c)
    per_source = run_sources_parallel(fn_map, query, cfg)

    # check agreement
    sources_with_results = {s: [r for r in v if not r.get("error")] for s, v in per_source.items()}
    src_names = list(sources_with_results.keys())
    agreement = 1.0
    if len(src_names) >= 2:
        agreement = url_agreement(sources_with_results[src_names[0]], sources_with_results[src_names[1]])

    arbitration_used = False
    if agreement < threshold and arbitrator not in per_source:
        arbitration_used = True
        arb_fn = {
            "firecrawl": lambda q, c: search_with_retry(search_firecrawl, q, c),
            "tavily": lambda q, c: search_with_retry(search_tavily, q, c, advanced=True),
            "keenable": lambda q, c: search_with_retry(search_keenable, q, c),
        }.get(arbitrator)
        if arb_fn and quota_available(cfg, arbitrator):
            per_source[arbitrator] = arb_fn(query, cfg)

    merged = dedup_and_rank(per_source, cfg, top_k=top_k)
    return {
        "mode": "general",
        "query": query,
        "sources_used": list(per_source.keys()),
        "agreement_score": round(agreement, 3),
        "arbitration_triggered": arbitration_used,
        "total_results": len(merged),
        "sources_failed": sources_failed(per_source),
        "degraded": bool(sources_failed(per_source)),
        "results": merged,
    }


# ─── Mode: academic ─────────────────────────────────────────────────────────
def mode_academic(query: str, cfg: dict, top_k: int = 30) -> dict:
    mcfg = cfg["modes"]["academic"]
    sources_list = mcfg["sources"]
    fn_map = {}
    if "arxiv" in sources_list:
        fn_map["arxiv"] = lambda q, c: search_with_retry(search_arxiv, q, c, retries=0, max_results=mcfg["max_results_per_source"])
    if "dblp" in sources_list:
        fn_map["dblp"] = lambda q, c: search_with_retry(search_dblp, q, c, retries=0, max_results=mcfg["max_results_per_source"])
    if "semantic_scholar" in sources_list:
        fn_map["semantic_scholar"] = lambda q, c: search_with_retry(search_semantic_scholar, q, c, retries=2, base_delay=3.0, limit=mcfg["max_results_per_source"])
    if "openalex" in sources_list:
        fn_map["openalex"] = lambda q, c: search_with_retry(search_openalex, q, c, max_results=mcfg["max_results_per_source"])
    if "ai4scholar" in sources_list:
        fn_map["ai4scholar"] = lambda q, c: search_with_retry(search_ai4scholar, q, c, retries=1, max_results=mcfg["max_results_per_source"])
    if "zhipu" in sources_list:
        fn_map["zhipu"] = lambda q, c: search_with_retry(search_zhipu, q, c, max_results=mcfg["max_results_per_source"])

    per_source = run_sources_parallel(fn_map, query, cfg)
    merged = dedup_and_rank(per_source, cfg, top_k=top_k)

    # extract paper links for record
    paper_links = []
    for r in merged:
        if r.get("url") and any(r.get("source") == s for s in ["arxiv", "dblp", "semantic_scholar", "openalex", "ai4scholar"]):
            paper_links.append({
                "title": r.get("title"),
                "url": r.get("url"),
                "pdf_url": r.get("pdf_url") or r.get("pdf_link"),
                "doi": r.get("doi"),
                "arxiv_id": r.get("arxiv_id"),
                "year": r.get("year"),
                "venue": r.get("venue"),
                "authors": r.get("authors"),
                "source": r.get("source"),
                "also_from": r.get("also_from"),
            })

    # record to history
    if mcfg.get("record_papers"):
        save_history(cfg, query, merged[:5], mode="academic", paper_links=paper_links)

    return {
        "mode": "academic",
        "query": query,
        "sources_used": list(per_source.keys()),
        "total_results": len(merged),
        "paper_links_recorded": len(paper_links),
        "sources_failed": sources_failed(per_source),
        "degraded": bool(sources_failed(per_source)),
        "results": merged,
        "paper_links": paper_links,
    }


# ─── Mode: auto ──────────────────────────────────────────────────────────────
ACADEMIC_HINTS = (
    "paper", "论文", "research", "study", "survey", "experiment", "algorithm",
    "model", "neural", "deep learning", "machine learning", "transformer",
    "LLM", "benchmark", "dataset", "arxiv", "doi", "citation", "author",
    "theory", "method", "approach", "novel", "proposed", "evaluation",
)


def classify_intent(query: str) -> str:
    q = query.lower()
    if any(h.lower() in q for h in ACADEMIC_HINTS):
        return "academic"
    return "general"


def mode_auto(query: str, cfg: dict, top_k: int = 20, tier: str = "value") -> dict:
    intent = classify_intent(query)
    if intent == "academic":
        return mode_academic(query, cfg, top_k=top_k)
    return mode_general(query, cfg, top_k=top_k, tier=tier)


# ─── Fetch (content extraction) ─────────────────────────────────────────────
def fetch_with_firecrawl(url: str, cfg: dict) -> dict:
    if source_disabled("firecrawl"):
        return {"error": "firecrawl disabled this run (%s)" % _DEAD_SOURCES.get("firecrawl"),
                "source": "firecrawl"}
    if not quota_available(cfg, "firecrawl"):
        return {"error": "firecrawl quota exhausted", "source": "firecrawl"}
    key = get_api_key(cfg, "firecrawl")
    if not key:
        return {"error": "no firecrawl key", "source": "firecrawl"}
    endpoint = "https://api.firecrawl.dev/v2/scrape"
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.post(endpoint, json={"url": url, "formats": ["markdown"]},
                               headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
            quota_consume("firecrawl")
        data = resp.json().get("data", {})
        return {
            "url": url, "title": data.get("title", ""), "markdown": data.get("markdown", ""),
            "source": "firecrawl", "status": "ok"
        }
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}", "source": "firecrawl"}


def fetch_with_keenable(url: str, cfg: dict) -> dict:
    try:
        proc = subprocess.run(
            ["keenable", "fetch", url], capture_output=True, text=True, timeout=30
        )
        out = proc.stdout
        if not out.strip():
            return {"url": url, "error": "empty output", "source": "keenable"}
        doc = yaml.safe_load(out)
        return {
            "url": url, "title": doc.get("title", ""), "markdown": doc.get("content", ""),
            "source": "keenable", "status": "ok"
        }
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}", "source": "keenable"}


def fetch_with_tavily(url: str, cfg: dict) -> dict:
    if not quota_available(cfg, "tavily"):
        return {"error": "tavily quota exhausted", "source": "tavily"}
    key = get_api_key(cfg, "tavily")
    if not key:
        return {"error": "no tavily key", "source": "tavily"}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.tavily.com/extract",
                json={"urls": [url]},
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            quota_consume("tavily")
        data = resp.json()
        results = data.get("results", [])
        if results:
            r = results[0]
            return {
                "url": url, "title": r.get("title", ""),
                "markdown": r.get("raw_content") or r.get("content", ""),
                "source": "tavily", "status": "ok"
            }
        return {"url": url, "error": "no results", "source": "tavily"}
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}", "source": "tavily"}


def mode_fetch(url: str, cfg: dict, strategy: str = "markdown_body") -> dict:
    strats = cfg.get("fetch", {}).get("strategies", {})
    strat = strats.get(strategy, strats.get("markdown_body", {}))
    primary = strat.get("primary")
    fallback = strat.get("fallback")
    fetchers = {
        "firecrawl": fetch_with_firecrawl,
        "keenable": fetch_with_keenable,
        "tavily": fetch_with_tavily,
    }
    # try primary
    if primary and primary in fetchers:
        res = fetchers[primary](url, cfg)
        if res.get("status") == "ok":
            return {"mode": "fetch", "url": url, "strategy": strategy, **res}
    # try fallback
    if fallback and fallback in fetchers:
        res2 = fetchers[fallback](url, cfg)
        if res2.get("status") == "ok":
            return {"mode": "fetch", "url": url, "strategy": strategy, **res2}
    # last resort: keenable always
    if primary != "keenable" and fallback != "keenable":
        res3 = fetch_with_keenable(url, cfg)
        if res3.get("status") == "ok":
            return {"mode": "fetch", "url": url, "strategy": "fallback_keenable", **res3}
    return {"mode": "fetch", "url": url, "strategy": strategy,
            "error": "all fetchers failed", "primary_attempt": primary, "fallback_attempt": fallback}


# ─── History (SQLite) ───────────────────────────────────────────────────────
def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            query TEXT NOT NULL,
            mode TEXT NOT NULL,
            intent TEXT,
            sources_used TEXT,
            total_results INTEGER,
            top_results TEXT,
            paper_links TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_query ON searches(query);
        CREATE INDEX IF NOT EXISTS idx_ts ON searches(ts);
        """)


def save_history(cfg: dict, query: str, top_results: list[dict], mode: str = "general",
                 paper_links: list[dict] | None = None) -> None:
    hcfg = cfg.get("history", {})
    if not hcfg.get("enabled", True):
        return
    db_path = SKILL_DIR / hcfg.get("db_path", "data/history.db")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO searches (ts, query, mode, sources_used, total_results, top_results, paper_links) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                query, mode,
                "", len(top_results),
                json.dumps(top_results, ensure_ascii=False),
                json.dumps(paper_links or [], ensure_ascii=False),
            )
        )


def query_history(query: str, cfg: dict, limit: int = 5) -> dict:
    hcfg = cfg.get("history", {})
    db_path = SKILL_DIR / hcfg.get("db_path", "data/history.db")
    if not db_path.exists():
        return {"mode": "history", "query": query, "results": [], "note": "no history db yet"}
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM searches WHERE query LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    results = []
    for r in rows:
        results.append({
            "ts": r["ts"], "query": r["query"], "mode": r["mode"],
            "total_results": r["total_results"],
            "top_results": json.loads(r["top_results"] or "[]"),
            "paper_links": json.loads(r["paper_links"] or "[]"),
        })
    return {"mode": "history", "query": query, "matches": len(results), "results": results}


# ─── Quota report ────────────────────────────────────────────────────────────
def _real_tavily_usage(cfg: dict) -> dict | None:
    key = get_api_key(cfg, "tavily")
    if not key:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
        a = resp.json().get("account", {})
        return {"plan": a.get("current_plan"), "used": a.get("plan_usage"), "limit": a.get("plan_limit")}
    except Exception:
        return None


def _real_firecrawl_usage(cfg: dict) -> dict | None:
    key = get_api_key(cfg, "firecrawl")
    if not key:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get("https://api.firecrawl.dev/v2/team/credit-usage", headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
        d = resp.json().get("data", {})
        return {"remaining": d.get("remainingCredits"), "plan": d.get("planCredits"),
                "period_end": (d.get("billingPeriodEnd") or "")[:10]}
    except Exception:
        return None


def _real_ai4scholar_credits(cfg: dict) -> dict | None:
    """Ai4Scholar 积分余额（GET /api/credits 免费，不扣分）。"""
    key = get_api_key(cfg, "ai4scholar")
    if not key:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get("https://ai4scholar.net/api/credits",
                              headers={"Authorization": "Bearer " + key})
            resp.raise_for_status()
        d = resp.json()
        c = d.get("credits") or {}
        return {"total_available": c.get("total_available"),
                "permanent": c.get("permanent"),
                "member_monthly_remaining": c.get("member_monthly_remaining"),
                "plan": ((d.get("membership") or {}) or {}).get("plan")}
    except Exception:
        return None


def report_quota(cfg: dict) -> dict:
    out = {}
    for src_name in ["tavily", "firecrawl"]:
        qcfg = cfg.get("quota", {}).get(src_name)
        if qcfg:
            remaining = quota_remaining(cfg, src_name)
            used = qcfg["monthly_limit"] - (remaining or 0)
            entry = {
                "monthly_limit": qcfg["monthly_limit"],
                "used_this_month_local": used,
                "remaining_local": remaining,
                "month": current_month_key(),
            }
            live = _real_tavily_usage(cfg) if src_name == "tavily" else _real_firecrawl_usage(cfg)
            if live:
                entry["live"] = live
            exhausted = entry.get("remaining_local") == 0
            if isinstance(live, dict) and live.get("remaining") == 0:
                exhausted = True
            entry["exhausted"] = bool(exhausted)
            out[src_name] = entry
    credits = _real_ai4scholar_credits(cfg)
    if credits is not None:
        out["ai4scholar"] = {"unit": "credits", "cost_per_call": 1, "live": credits,
                             "exhausted": (credits.get("total_available") or 0) <= 0}
    return {"mode": "quota", "quota": out}


# ─── Manifest export (paper-reader bridge) ────────────────────────────────
def export_manifest(paper_links: list[dict], query: str, output_dir: Path) -> Path:
    """  paper_links  paper-reader   _download_manifest.json.

      → arxiv_id   filename;       .
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    def _infer_filename(paper: dict, index: int) -> str:
        """  arxiv_id → pdf_url → url →   ."""
        # 1. arxiv_id (  )
        aid = paper.get("arxiv_id") or ""
        if aid:
            clean = aid.split("v")[0].strip()
            return f"{clean}.pdf"
        # 2. pdf_url
        pdf = paper.get("pdf_url") or paper.get("pdf_link") or ""
        if pdf and "/" in pdf:
            parsed = urllib.parse.urlparse(pdf)
            fname = Path(parsed.path).name
            if fname and fname.lower().endswith(".pdf"):
                return fname
            for part in reversed(parsed.path.split("/")):
                if part and any(c.isdigit() for c in part):
                    return f"{part}.pdf" if not part.endswith(".pdf") else part
        # 3. url
        url = paper.get("url") or ""
        if url and "/" in url:
            parsed = urllib.parse.urlparse(url)
            stem = Path(parsed.path).name or parsed.path.strip("/").split("/")[-1]
            if stem:
                return f"{stem}.pdf" if not stem.endswith(".pdf") else stem
        # 4.
        return f"paper_{index + 1:03d}.pdf"

    papers = []
    for i, p in enumerate(paper_links):
        manifest_entry = {
            "filename": _infer_filename(p, i),
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "pdf_url": p.get("pdf_url") or p.get("pdf_link") or "",
            "source_db": p.get("source", "unknown"),
        }
        #   (  None)
        for key in ("arxiv_id", "doi", "year", "venue", "authors"):
            val = p.get(key)
            if val is not None:
                manifest_entry[key] = val

        papers.append(manifest_entry)

    manifest = {
        "generated_by": "unified-search",
        "query": query,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "papers": papers,
    }

    out_path = output_dir / "_download_manifest.json"
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  {len(papers)}   → {out_path}", file=sys.stderr)

    #    filenames
    fnames = [p["filename"] for p in papers]
    dups = [f for f in set(fnames) if fnames.count(f) > 1]
    if dups:
        print(f"  ⚠️    : {dups}——  ", file=sys.stderr)

    return out_path


# ─── CLI ─────────────────────────────────────────────────────────────────────
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Unified web + academic search aggregator (8 sources)."
    )
    p.add_argument("query", nargs="?", help="search query (or history search term)")
    p.add_argument("--mode", choices=["general", "academic", "auto"], default="auto",
                   help="search mode (default: auto-classify)")
    p.add_argument("--top", type=int, default=None, help="top-K results to return")
    p.add_argument("--fetch", metavar="URL", help="fetch single URL content (markdown)")
    p.add_argument("--strategy", choices=["markdown_body", "fast_summary", "ai_extract"],
                   default="markdown_body", help="fetch strategy")
    p.add_argument("--history", action="store_true", help="search history cache instead of web")
    p.add_argument("--quota", action="store_true", help="show monthly quota usage")
    p.add_argument("--topic", default=None, help="tavily topic hint (general/news/finance)")
    p.add_argument("--tier", choices=["value", "flagship"], default="value",
                   help="general 模式分层：value=性价比源(keenable+bocha)，flagship=旗舰源(tavily advanced+bocha)")
    p.add_argument("--time-range", default=None, help="tavily time_range (day/week/month/year)")
    p.add_argument("--export-manifest", type=Path, default=None, metavar="DIR",
                   help="export paper links as _download_manifest.json to DIR (academic mode)")
    p.add_argument("--output", "-o", default=None, metavar="DIR",
                   help="output directory for JSON result file (default: stdout)")
    return p


def main() -> int:
    args = build_argparser().parse_args()
    cfg = load_config()

    # ── quota ──
    if args.quota:
        print(json.dumps(report_quota(cfg), indent=2, ensure_ascii=False))
        return 0

    # ── history ──
    if args.history:
        if not args.query:
            print(json.dumps({"error": "history search requires a query"}, ensure_ascii=False))
            return 1
        print(json.dumps(query_history(args.query, cfg), indent=2, ensure_ascii=False))
        return 0

    # ── fetch ──
    if args.fetch:
        result = mode_fetch(args.fetch, cfg, strategy=args.strategy)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    # ── search ──
    if not args.query:
        print(json.dumps({"error": "no query provided"}, ensure_ascii=False))
        return 1

    top_k = args.top or (30 if args.mode == "academic" else 15)
    if args.mode == "general":
        result = mode_general(args.query, cfg, top_k=top_k, tier=args.tier)
    elif args.mode == "academic":
        result = mode_academic(args.query, cfg, top_k=top_k)
    else:  # auto
        result = mode_auto(args.query, cfg, top_k=top_k, tier=args.tier)

    # always record general search history too (top-5)
    if result.get("mode") == "general":
        save_history(cfg, args.query, result["results"][:5], mode="general")

    # ── export manifest (paper-reader ) ──
    if args.export_manifest is not None:
        paper_links = result.get("paper_links", [])
        if not paper_links and result.get("mode") == "academic":
            print("⚠️    paper_links     (    )",
                  file=sys.stderr)
        elif paper_links:
            export_manifest(paper_links, args.query, args.export_manifest)
        else:
            print("⚠️    (  general   academic   )",
                  file=sys.stderr)

    # ── output ──
    output_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        out_dir = Path(args.output).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-]", "_", args.query[:60])
        out_path = out_dir / f"search_{safe_name}.json"
        out_path.write_text(output_json, encoding="utf-8")
        print(json.dumps({"mode": result.get("mode"), "output_file": str(out_path),
                          "total_results": result.get("total_results", 0)}, indent=2, ensure_ascii=False))
    else:
        print(output_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
