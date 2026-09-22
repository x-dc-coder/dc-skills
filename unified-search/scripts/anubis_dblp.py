#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dblp.org Anubis PoW 绕过 —— 纯 Python，无浏览器、无 WASM。

背景
----
dblp.org 在 v1.27.0 的 Anubis 后面。检索 API 被挑战页拦截时返回 HTTP 200 +
text/html（"Making sure you're not a bot!"）。绕过只需复现浏览器 main.mjs 的动作：

  1. GET 目标 URL，解析内联 <script id="anubis_challenge" type="application/json">。
  2. 解 PoW：fast 算法 = 找最小 nonce，使
         sha256(randomData + str(nonce)) 的十六进制前 difficulty 位为 '0'
     （与 Anubis worker sha256-webcrypto.mjs 完全一致；difficulty=4 时约 6.5 万次
     哈希、毫秒级。纯 JS 的 sha256-purejs.mjs 逻辑相同，无 WASM。）
  3. GET  {basePrefix}/.within.website/x/cmd/anubis/api/pass-challenge
       ?id=<challenge.id>&response=<hash>&nonce=<nonce>&redir=<原 URL>&elapsedTime=<ms>
     响应会 Set-Cookie（Anubis v1.2x 的 cookie 名是 "<slug>-auth-<suffix>"，
     本站为 dblp_org-auth-8702d140，是 EdDSA 签名的 JWT，exp-iat = 3600s）。
  4. 带上该 cookie 重新请求检索 API，即得到真正的 JSON。

用法
----
    cd /home/dc/projects/dc-skills && export NO_PROXY='localhost,127.0.0.1,::1' no_proxy='localhost,127.0.0.1,::1'
    uv run python ~/.claude/skills-output/unified-search/anubis_dblp.py "transformer" -n 5

仅依赖 httpx（skills venv 已装，无第三方新依赖）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

API_BASE = "https://dblp.org/search/publ/api"
PASS_CHALLENGE_PATH = "/.within.website/x/cmd/anubis/api/pass-challenge"

CHALLENGE_RE = re.compile(
    r'<script\s+id="anubis_challenge"\s+type="application/json">(.*?)</script>', re.S
)
BASE_PREFIX_RE = re.compile(
    r'<script\s+id="anubis_base_prefix"\s+type="application/json">(.*?)</script>', re.S
)
AUTH_COOKIE_RE = re.compile(r"^(.+)-auth(?:-[0-9a-f]+)?$")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CACHE_PATH = Path(
    os.environ.get("DBLP_COOKIE_CACHE")
    or Path(__file__).resolve().with_name("dblp_cookies.json")
)

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=8.0)

# 最近一次调用的诊断信息（供 CLI / 调用方查看）
LAST_RUN: dict = {}
REFRESH_MARGIN = 60  # cookie 过期前多少秒就重新过墙


# --------------------------------------------------------------------------- #
# 网络客户端：显式代理，绕开 httpx 对 no_proxy 中 "[::1]" 的解析 bug
# --------------------------------------------------------------------------- #

def _detect_proxy() -> str | None:
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    return None


def _new_client(proxy: str | None = None) -> httpx.Client:
    """trust_env=False：完全忽略 no_proxy/NO_PROXY，避免 httpx 0.28 的
    InvalidURL: Invalid port: ':1]'（"[::1]" 被当成 host:port）。"""
    kwargs: dict[str, Any] = {
        "timeout": DEFAULT_TIMEOUT,
        "follow_redirects": True,
        "trust_env": False,
        "headers": {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    p = proxy if proxy is not None else _detect_proxy()
    if p:
        kwargs["proxy"] = p
    return httpx.Client(**kwargs)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def _content_type(resp: httpx.Response) -> str:
    return (resp.headers.get("content-type") or "").lower()


def is_challenge(resp: httpx.Response) -> bool:
    """是否是 Anubis 挑战页。"""
    if "text/html" not in _content_type(resp):
        return False
    body = resp.text[:4096]
    return "anubis_challenge" in body or "Making sure you" in body


def _jwt_exp(token: str) -> int | None:
    """从 JWT 里取 exp（Anubis 的 auth cookie 是 JWT）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# PoW
# --------------------------------------------------------------------------- #

def solve_pow(random_data: str, difficulty: int) -> tuple[str, int]:
    """返回 (hex_digest, nonce)，满足 sha256(random_data + str(nonce)) 前
    difficulty 个十六进制字符为 '0'。"""
    prefix = "0" * int(difficulty)
    nonce = 0
    while True:
        digest = hashlib.sha256((random_data + str(nonce)).encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return digest, nonce
        nonce += 1


def _parse_challenge(html: str) -> tuple[dict, str]:
    m = CHALLENGE_RE.search(html)
    if not m:
        raise RuntimeError("页面里找不到 anubis_challenge JSON —— 不是 Anubis 挑战页？")
    challenge = json.loads(m.group(1))
    base_prefix = ""
    mb = BASE_PREFIX_RE.search(html)
    if mb:
        try:
            base_prefix = json.loads(mb.group(1)) or ""
        except Exception:
            base_prefix = ""
    if not base_prefix:
        base_prefix = challenge.get("basePrefix") or ""
    return challenge, base_prefix


def _pass_challenge(
    client: httpx.Client, redir: str, first: httpx.Response | None = None
) -> httpx.Response:
    """对一个被挑战的 URL 完成 PoW 并提交；follow_redirects=True 时返回的通常
    已是 redir 的最终内容。first 为已取到的挑战响应时复用，省一次往返。"""
    if first is None:
        first = client.get(redir)
    if not is_challenge(first):
        return first

    challenge, base_prefix = _parse_challenge(first.text)
    rules = challenge.get("rules") or {}
    payload = challenge.get("challenge") or {}
    algorithm = str(rules.get("algorithm") or "fast")
    difficulty = int(rules.get("difficulty") or 0)
    random_data = str(payload.get("randomData") or "")
    challenge_id = str(payload.get("id") or "")

    if not random_data or not challenge_id:
        raise RuntimeError(f"挑战参数缺失：id={challenge_id!r} randomData={len(random_data)} 字节")
    if algorithm not in ("fast", "slow", "legacy"):
        raise RuntimeError(f"未知 Anubis 算法：{algorithm!r}")

    started = time.time()
    digest, nonce = solve_pow(random_data, difficulty)
    elapsed_ms = int((time.time() - started) * 1000)
    LAST_RUN.update(
        {
            "algorithm": algorithm,
            "difficulty": difficulty,
            "nonce": nonce,
            "pow_ms": elapsed_ms,
            "challenge_id": challenge_id,
        }
    )

    params = {
        "id": challenge_id,
        "response": digest,
        "nonce": nonce,
        "redir": redir,
        "elapsedTime": elapsed_ms,
    }
    # base_prefix / PASS_CHALLENGE_PATH 都是站内绝对路径，需要拼到同一 origin 上
    target = httpx.URL(redir).copy_with(path=f"{base_prefix}{PASS_CHALLENGE_PATH}", query=None)
    resp = client.get(target, params=params)

    if is_challenge(resp):
        raise RuntimeError(
            "pass-challenge 之后仍返回挑战页：cookie 未被接受 "
            f"(algorithm={algorithm} difficulty={difficulty} nonce={nonce})"
        )
    return resp


# --------------------------------------------------------------------------- #
# cookie 缓存
# --------------------------------------------------------------------------- #

def _read_cache() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(data: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # 缓存写不了不影响功能


def _save_cache(client: httpx.Client, data: dict, channel: str) -> None:
    """把成功的 cookie 与本次可用的网络通道一起写进缓存。"""
    auth_name = None
    exp = None
    cookies: dict[str, str] = {}
    for c in client.cookies.jar:
        cookies[c.name] = c.value
        if AUTH_COOKIE_RE.match(c.name):
            auth_name = c.name
            exp = _jwt_exp(c.value) or exp
    if not cookies:
        return
    data = dict(data)
    data.update(
        {
            "cookies": cookies,
            "auth_cookie": auth_name,
            "exp": exp,
            "channel": channel,
            "saved_at": time.time(),
        }
    )
    _write_cache(data)


def load_cache(client: httpx.Client, data: dict | None = None) -> bool:
    """把缓存的 cookie 装载进 client；过期/无效返回 False。"""
    data = _read_cache() if data is None else data
    exp = data.get("exp")
    if exp is not None and time.time() >= float(exp) - REFRESH_MARGIN:
        return False
    cookies = data.get("cookies") or {}
    if not cookies:
        return False
    for name, value in cookies.items():
        client.cookies.set(name, value, domain="dblp.org")
    return True


def clear_cache() -> None:
    try:
        CACHE_PATH.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #

def _proxy_candidates() -> list[str | None]:
    """候选网络通道：先代理，再直连（实测 dblp.org 有时只有其中一条通）。"""
    out: list[str | None] = []
    env_proxy = _detect_proxy()
    if env_proxy:
        out.append(env_proxy)
    out.append(None)
    return out


def _channel_proxy(channel: Any) -> str | None:
    return None if channel in (None, "direct") else _detect_proxy()


def _channel_name(proxy: str | None) -> str:
    return "direct" if proxy is None else "proxy"


def _search_once(q: str, h: int, proxy: str | None) -> dict:
    params = {"q": q, "format": "json", "h": int(h)}
    with _new_client(proxy) as client:
        cached = _read_cache()
        load_cache(client, cached)
        resp = client.get(API_BASE, params=params)

        if is_challenge(resp):
            # 缓存无效（或首次）：复用刚拿到的挑战页完成 PoW
            redir = API_BASE + "?" + str(httpx.QueryParams(params))
            resp = _pass_challenge(client, redir, first=resp)
            _save_cache(client, cached, _channel_name(proxy))
            if "json" not in _content_type(resp):
                resp = client.get(API_BASE, params=params)

        if resp.status_code != 200:
            raise RuntimeError(f"dblp 返回 HTTP {resp.status_code}: {resp.text[:200]!r}")

        ctype = _content_type(resp)
        if "json" not in ctype and "text/plain" not in ctype:
            raise RuntimeError(
                f"未拿到 JSON（content-type={ctype!r}）—— 可能仍未过墙。"
                f"响应片段：{resp.text[:200]!r}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"响应不是合法 JSON：{resp.text[:200]!r}") from exc

        if not isinstance(data, dict) or "result" not in data:
            raise RuntimeError(f"JSON 结构异常，缺少 result 键：{list(data)[:10]}")
        return data


def dblp_search(q: str, h: int = 5) -> dict:
    """检索 dblp 出版物，返回解析后的 JSON dict（含 result.hits.hit）。

    自动处理 Anubis 挑战墙：优先用本地 cookie 缓存，缓存失效则重新做 PoW。
    网络通道（HTTPS_PROXY / 直连）自动回退，并把成功的通道记在缓存里。
    失败抛 RuntimeError / httpx.HTTPError。
    """
    LAST_RUN.clear()
    candidates: list[str | None] = []
    channel = _read_cache().get("channel")
    if channel:
        candidates.append(_channel_proxy(channel))
    for cand in _proxy_candidates():
        if cand not in candidates:
            candidates.append(cand)

    last_exc: Exception | None = None
    for proxy in candidates:
        try:
            data = _search_once(q, h, proxy)
        except httpx.TransportError as exc:  # 网络层失败 -> 换通道重试
            last_exc = exc
            continue
        _write_cache({**_read_cache(), "channel": _channel_name(proxy)})
        LAST_RUN["channel"] = _channel_name(proxy)
        LAST_RUN["used_cache"] = "pow_ms" not in LAST_RUN
        return data
    raise RuntimeError(
        f"dblp 所有网络通道均不可用（proxy/direct 都失败）：{last_exc}"
    ) from last_exc


def dblp_hits(q: str, h: int = 5) -> list[dict]:
    """便利函数：直接返回 hit 列表（可能为空）。"""
    data = dblp_search(q, h)
    hits = (((data.get("result") or {}).get("hits") or {}).get("hit")) or []
    return hits if isinstance(hits, list) else [hits]


# --------------------------------------------------------------------------- #
# 自测入口
# --------------------------------------------------------------------------- #

def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dblp 检索（自动绕过 Anubis PoW 挑战）")
    ap.add_argument("query", nargs="?", default="transformer", help="检索词")
    ap.add_argument("-n", "--h", type=int, default=5, help="返回条数（默认 5）")
    ap.add_argument("--clear-cache", action="store_true", help="先清空 cookie 缓存（冷启动自测）")
    ap.add_argument("--titles", action="store_true", help="额外打印命中 URL")
    ap.add_argument("--debug", action="store_true", help="打印通道 / PoW 诊断信息")
    args = ap.parse_args(argv)

    if args.clear_cache:
        clear_cache()
        print(f"[cache] 已清空 {CACHE_PATH}")

    print(f"[env]   proxy={_detect_proxy()}  cache={CACHE_PATH}")
    t0 = time.time()
    try:
        data = dblp_search(args.query, args.h)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"[run]   {LAST_RUN}")
    hits_obj = (data.get("result") or {}).get("hits") or {}
    hits = hits_obj.get("hit") or []
    if isinstance(hits, dict):
        hits = [hits]
    print(f"[ok]    {time.time() - t0:.2f}s  top-level keys = {list(data)}")
    print(f"[ok]    result.hits @total={hits_obj.get('@total')} @sent={hits_obj.get('@sent')} "
          f"hit 条数 = {len(hits)}")
    for i, hit in enumerate(hits[: args.h], 1):
        info = hit.get("info") or {}
        print(f"  {i}. {info.get('title', '?')}  ({info.get('year', '?')})")
        if args.titles:
            print(f"     {info.get('venue', '')} :: {info.get('url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
