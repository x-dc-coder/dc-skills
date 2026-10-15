#!/usr/bin/env python3
"""agent-balances CLI — 查询本机各编程 Agent 的额度/用量余额。

复刻 Orca（app.asar）各 provider 的抓取逻辑（端点/请求头/字段逐一逆向提取），
输出数字与 Orca 状态栏/用量面板同源。纯 stdlib；zcode 的 Orca 加密 key 解密
条件调用 powershell.exe（DPAPI）+ node（AES-256-GCM），两者缺失时该 provider
优雅降级为 unavailable，不影响其他 provider。

产物接线（docs/specs/输出规范.md C-1/C-4）：--output 给定时 JSON 落
<cwd>/skills-output/agentops/agent-balances/<时间戳>/agent-balances.json
（固定名；显式路径优先，另存主库审计副本）；不给定时只打印到 stdout。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import plan_output  # noqa: E402

HOME = os.path.expanduser("~")
# Windows 用户配置目录（WSL 挂载视图）：Cursor/Orca 的凭据都在这里。
# 换机器/换用户时用环境变量覆盖。
WIN_HOME = os.environ.get("AGENT_BALANCES_WIN_HOME", "/mnt/c/Users/32841")
TIMEOUT = 12

# ---------- shared helpers ----------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts_ms) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_ns(raw):
    """grok/codex ISO 时间戳可能带 9 位纳秒；py3.10 fromisoformat 不接受，需截断。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    import re
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch(url, headers=None, method="GET", body=None, timeout=TIMEOUT):
    """HTTP 请求（遵循本机代理环境变量）；返回 (status, json_or_text)，网络错误 status=None。"""
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = body.encode() if isinstance(body, str) else body
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")
    except Exception as e:  # 网络/代理/DNS
        return None, f"{type(e).__name__}: {e}"
    try:
        return 200, json.loads(raw)
    except ValueError:
        return 200, raw.decode("utf-8", "replace")


def clamp_pct(x):
    if not isinstance(x, (int, float)):
        return None
    return round(min(100.0, max(0.0, float(x))), 1)


def window(used, minutes, resets_at_ms=None):
    return {
        "usedPercent": clamp_pct(used),
        "windowMinutes": minutes,
        "resetsAt": iso(resets_at_ms),
    }


def result(provider, status, **kw):
    out = {"provider": provider, "status": status}
    out.update(kw)
    return out


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------- grok（Orca Tko/Cko/_ko/vko） ----------

def grok_balance():
    auth = read_json(os.path.join(HOME, ".grok", "auth.json"))
    if not auth:
        return result("grok", "unavailable", error="~/.grok/auth.json not found — run grok login")
    entry = None
    for key, val in auth.items():
        if isinstance(key, str) and key.startswith("https://auth.x.ai") and isinstance(val, dict):
            entry = val
            break
    if not entry or not entry.get("key"):
        return result("grok", "unavailable", error="no auth.x.ai entry in auth.json")

    expires_ms = parse_iso_ns(entry.get("expires_at"))
    fresh = expires_ms is None or expires_ms - utcnow().timestamp() * 1000 > 300_000

    status, body = fetch(
        "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
        headers={
            "Authorization": f"Bearer {entry['key']}",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "Accept": "application/json",
            "x-userid": entry.get("user_id") or "",
        },
    )
    if status is None:
        return result("grok", "error", error=f"network: {body}", tokenFresh=fresh)
    if status in (401, 403):
        return result("grok", "error", error=f"HTTP {status} — token expired, run grok", tokenFresh=fresh)
    if status != 200 or not isinstance(body, dict):
        return result("grok", "error", error=f"HTTP {status}")

    cfg = body.get("config", body)
    out = {"tokenFresh": fresh, "email": entry.get("email")}

    pct = cfg.get("creditUsagePercent")
    if isinstance(pct, (int, float)):
        end = cfg.get("currentPeriod", {}).get("end") or cfg.get("billingPeriodEnd")
        out["weekly"] = window(pct, 10080, parse_iso_ns(end))

    def val(x):
        if isinstance(x, dict):
            v = x.get("val")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return None

    mlimit, muse = val(cfg.get("monthlyLimit")), val(cfg.get("used"))
    if mlimit and muse is not None and mlimit > 0:
        end = cfg.get("currentPeriod", {}).get("end") or cfg.get("billingPeriodEnd")
        out["monthly"] = window(muse / mlimit * 100, 43200, parse_iso_ns(end))
    od_used, od_cap = val(cfg.get("onDemandUsed")), val(cfg.get("onDemandCap"))
    if od_used is not None or od_cap is not None:
        out["onDemand"] = {"used": od_used, "cap": od_cap}
    prepaid = val(cfg.get("prepaidBalance"))
    if prepaid is not None:
        out["prepaidBalance"] = prepaid
    if cfg.get("subscriptionTier"):
        out["subscriptionTier"] = cfg["subscriptionTier"]
    return result("grok", "ok", **out)


# ---------- codex（Orca bTo/oTo/vTo/sTo） ----------

def codex_balance():
    auth = read_json(os.path.join(HOME, ".codex", "auth.json"))
    tokens = (auth or {}).get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return result("codex", "unavailable", error="~/.codex/auth.json has no access_token")
    headers = {
        "Authorization": f"Bearer {access}",
        "User-Agent": "codex-cli",
        "OpenAI-Beta": "codex-1",
        "originator": "Codex Desktop",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]
    status, body = fetch("https://chatgpt.com/backend-api/wham/usage", headers=headers)
    if status is None:
        return result("codex", "error", error=f"network: {body}")
    if status in (401, 403):
        return result("codex", "error", error=f"HTTP {status} — run codex to re-login")
    if status != 200 or not isinstance(body, dict):
        return result("codex", "error", error=f"HTTP {status}")

    def win(w, default_min):
        if not w or not isinstance(w.get("used_percent"), (int, float)):
            return None
        resets = w.get("reset_at")
        resets_ms = int(resets * 1000) if isinstance(resets, (int, float)) else None
        mins = w.get("limit_window_seconds")
        mins = int(mins / 60) if isinstance(mins, (int, float)) and mins > 0 else default_min
        return window(w["used_percent"], mins, resets_ms)

    rl = body.get("rate_limit") or {}
    out = {"planType": body.get("plan_type")}
    out["session"] = win(rl.get("primary_window"), 300)
    out["weekly"] = win(rl.get("secondary_window"), 10080)
    return result("codex", "ok", **out)


# ---------- cursor（Orca keo/lTe/Zko/Rko） ----------

def _b64url_json(part):
    try:
        pad = part + "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return None


def _cursor_token_from_vscdb():
    """读 Cursor state.vscdb 的 cursorAuth/*（先复制 db+wal 再查，避免占用锁）。"""
    db = os.path.join(WIN_HOME, "AppData", "Roaming", "Cursor", "User", "globalStorage", "state.vscdb")
    if not os.path.exists(db):
        return None, "state.vscdb not found (Cursor IDE not installed or different profile)"
    tmpdir = tempfile.mkdtemp(prefix="cursor-vscdb-")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = db + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmpdir, "state.vscdb" + suffix))
        conn = sqlite3.connect(os.path.join(tmpdir, "state.vscdb"))
        try:
            rows = conn.execute(
                "SELECT key, value FROM ItemTable WHERE key IN (?,?,?,?)",
                ("cursorAuth/accessToken", "cursorAuth/cachedEmail",
                 "cursorAuth/stripeMembershipType", "cursorAuth/stripeSubscriptionStatus"),
            ).fetchall()
        finally:
            conn.close()
        found = {}
        for key, value in rows:
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace").strip()
            if isinstance(value, str) and value:
                found[key] = value
        if not found.get("cursorAuth/accessToken"):
            return None, "cursorAuth/accessToken missing in state.vscdb (not signed in to Cursor IDE)"
        return found, None
    except Exception as e:
        return None, f"state.vscdb read failed: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cursor_balance():
    found, err = _cursor_token_from_vscdb()
    if err:
        return result("cursor", "unavailable", error=err)
    raw = found["cursorAuth/accessToken"]
    parts = raw.split(".") if "." in raw else []
    claims = _b64url_json(parts[1]) if len(parts) > 1 else None
    sub = (claims or {}).get("sub")
    if not sub:
        return result("cursor", "error", error="cursor access token is not a JWT with sub")
    exp = (claims or {}).get("exp")
    expired = isinstance(exp, (int, float)) and exp * 1000 <= utcnow().timestamp() * 1000
    cookie = f"WorkosCursorSessionToken={sub}%3A%3A{raw}"
    status, body = fetch(
        "https://cursor.com/api/usage-summary",
        headers={"Cookie": cookie, "Accept": "application/json",
                 "Origin": "https://cursor.com", "Referer": "https://cursor.com/dashboard"},
    )
    if status is None:
        return result("cursor", "error", error=f"network: {body}", tokenExpired=expired)
    if status == 401:
        return result("cursor", "error",
                      error="HTTP 401 — Cursor session expired, sign in again", tokenExpired=expired)
    if status != 200 or not isinstance(body, dict):
        return result("cursor", "error", error=f"HTTP {status}")
    out = {"tokenExpired": expired, "planType": found.get("cursorAuth/stripeMembershipType"),
           "email": found.get("cursorAuth/cachedEmail")}
    end = body.get("billingCycleEnd")
    reset_ms = parse_iso_ns(end) if isinstance(end, str) else None
    plan = (body.get("individualUsage") or {}).get("plan") or {}
    if plan.get("enabled") is not False:
        pct = plan.get("totalPercentUsed")
        if pct is None and isinstance(plan.get("limit"), (int, float)) and plan["limit"] > 0 \
                and isinstance(plan.get("used"), (int, float)):
            pct = plan["used"] / plan["limit"] * 100
        if pct is not None:
            out["monthly"] = window(pct, 43200, reset_ms)
        buckets = []
        for label, key in (("Cursor Models", "autoPercentUsed"), ("Other Models", "apiPercentUsed")):
            v = plan.get(key)
            if isinstance(v, (int, float)):
                buckets.append({"name": label, **window(v, 43200, reset_ms)})
        if buckets:
            out["buckets"] = buckets
    od = (body.get("individualUsage") or {}).get("onDemand") or {}
    if od.get("enabled") is True and isinstance(od.get("totalPercentUsed"), (int, float)):
        out["onDemand"] = window(od["totalPercentUsed"], 43200, reset_ms)
    if body.get("billingCycleStart"):
        out["billingCycleStart"] = body["billingCycleStart"]
    return result("cursor", "ok", **out)


# ---------- claude（Orca FDo/q8） ----------

def claude_balance():
    for path in (os.path.join(HOME, ".claude", ".credentials.json"),
                 os.path.join(WIN_HOME, ".claude", ".credentials.json")):
        creds = read_json(path)
        if creds:
            break
    if not creds:
        return result("claude", "unavailable",
                      error=".claude/.credentials.json not found (WSL and Windows)")
    token = ((creds.get("claudeAiOauth") or {}).get("accessToken"))
    if not token:
        return result("claude", "unavailable",
                      error="no claudeAiOauth.accessToken — not signed in to Claude Code")
    status, body = fetch(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": f"Bearer {token}",
                 "anthropic-beta": "oauth-2025-04-20",
                 "User-Agent": "claude-code/2.1.0"},
    )
    if status is None:
        return result("claude", "error", error=f"network: {body}")
    if status in (401, 403):
        return result("claude", "error", error=f"HTTP {status} — token expired, run claude to refresh")
    if status != 200 or not isinstance(body, dict):
        return result("claude", "error", error=f"HTTP {status}")

    def win(obj, minutes):
        if not isinstance(obj, dict):
            return None
        pct = obj.get("utilization") if isinstance(obj.get("utilization"), (int, float)) \
            else obj.get("used_percentage")
        if not isinstance(pct, (int, float)):
            return None
        resets = obj.get("resets_at")
        resets_ms = None
        if isinstance(resets, str):
            resets_ms = parse_iso_ns(resets)
        elif isinstance(resets, (int, float)):
            # Orca 规则：>1e10 视为 epoch ms，否则 epoch 秒
            resets_ms = int(resets) if resets > 1e10 else int(resets * 1000)
        return window(pct, minutes, resets_ms)

    out = {}
    out["session"] = win(body.get("five_hour"), 300)
    out["weekly"] = win(body.get("seven_day"), 10080)
    return result("claude", "ok", **out)


# ---------- kimi（Orca XMo） ----------

def kimi_balance():
    for base_dir in (os.path.join(HOME, ".kimi-code", "credentials"),
                     os.path.join(WIN_HOME, ".kimi-code", "credentials")):
        creds = read_json(os.path.join(base_dir, "kimi-code.json"))
        if creds:
            break
    if not creds:
        return result("kimi", "unavailable",
                      error="~/.kimi-code/credentials/kimi-code.json not found")
    token = creds.get("access_token")
    if not token:
        return result("kimi", "unavailable", error="kimi-code.json has no access_token")
    base = os.environ.get("KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1").rstrip("/")
    status, body = fetch(f"{base}/usages",
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if status is None:
        return result("kimi", "error", error=f"network: {body}")
    if status in (401, 403):
        return result("kimi", "error", error=f"HTTP {status} — token expired, run kimi to refresh")
    if status != 200 or not isinstance(body, dict):
        return result("kimi", "error", error=f"HTTP {status}")
    return result("kimi", "ok", raw=body)


# ---------- zcode / GLM coding plan（Orca q9a/K9a/V9a） ----------

ZCODE_SITES = {"zai": "https://api.z.ai", "bigmodel": "https://open.bigmodel.cn"}
ZCODE_UNIT_MINUTES = {1: 1440, 3: 60, 5: 1, 6: 10080}  # 日/小时/分钟/周


def _orca_settings():
    """Orca 设置文档：profile-state.db（新）优先，orca-data.json（旧）兜底。"""
    db = os.path.join(WIN_HOME, "AppData", "Roaming", "orca", "profiles", "local-default", "profile-state.db")
    if os.path.exists(db):
        tmpdir = tempfile.mkdtemp(prefix="orca-state-")
        try:
            for suffix in ("", "-wal", "-shm"):
                src = db + suffix
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(tmpdir, "ps.db" + suffix))
            conn = sqlite3.connect(os.path.join(tmpdir, "ps.db"))
            try:
                row = conn.execute(
                    "SELECT payload FROM profile_state_documents WHERE domain='settings'").fetchone()
            finally:
                conn.close()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except ValueError:
                    pass
        except Exception:
            pass
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    data = read_json(os.path.join(WIN_HOME, "AppData", "Roaming", "orca",
                                  "profiles", "local-default", "orca-data.json"))
    return (data or {}).get("settings") or {}


def _zcode_orca_key():
    """解密 Orca 存储的 GLM Coding Plan key。

    位置：<WIN_HOME>/.orca/zcode-plan-api-key.enc（Electron homedir = Windows
    profile，不是 WSL 的 ~/.orca）。信封 <prefix>:v1:encrypted:<base64>；
    payload 是 Chromium safeStorage 格式：v10 + 12B IV + 密文 + 16B GCM tag。
    AES 密钥在 Orca 的 Local State（os_crypt.encrypted_key）："DPAPI" 5 字节
    前缀 + CryptProtectData 输出，从偏移 5 起 DPAPI Unprotect（CurrentUser）。
    DPAPI 走 powershell.exe，AES-GCM 走 node；任一缺失返回 error。
    """
    enc = os.path.join(WIN_HOME, ".orca", "zcode-plan-api-key.enc")
    if not os.path.exists(enc):
        return None, None, "no Orca-stored key (.orca/zcode-plan-api-key.enc)"
    try:
        raw = open(enc).read().strip()
        b64 = raw.split(":", 2)[2].split(":", 1)[1]
        if not b64:
            return None, None, "zcode key envelope has no payload"
    except (OSError, IndexError):
        return None, None, "zcode key envelope unreadable"

    tmpdir = tempfile.mkdtemp(prefix="zcode-key-")
    try:
        blob_file = os.path.join(tmpdir, "blob.b64")
        keyhex_file = os.path.join(tmpdir, "key.hex")
        out_file = os.path.join(tmpdir, "key.txt")
        with open(blob_file, "w") as f:
            f.write(b64)

        # 1) DPAPI：Local State -> 32 字节 AES 密钥（hex）
        ps1 = os.path.join(tmpdir, "dpapi.ps1")
        with open(ps1, "w") as f:
            f.write(
                "Add-Type -AssemblyName System.Security\n"
                "$ls = Get-Content 'C:\\Users\\32841\\AppData\\Roaming\\orca\\Local State' -Raw | ConvertFrom-Json\n"
                "$ek = [Convert]::FromBase64String($ls.os_crypt.encrypted_key)\n"
                "$key = [System.Security.Cryptography.ProtectedData]::Unprotect($ek[5..($ek.Length-1)], $null, 'CurrentUser')\n"
                "[IO.File]::WriteAllText($args[0], ($key | ForEach-Object { $_.ToString('x2') }) -join '')\n")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", ps1.replace("/", "\\"), keyhex_file.replace("/", "\\")],
                           capture_output=True, timeout=30)
        keyhex = open(keyhex_file).read().strip() if os.path.exists(keyhex_file) else ""
        if len(keyhex) != 64:
            return None, None, f"DPAPI decrypt failed (rc={r.returncode}; needs Windows + same user)"

        # 2) AES-256-GCM：v10 blob -> API key
        js = os.path.join(tmpdir, "gcm.js")
        with open(js, "w") as f:
            f.write(
                "const fs=require('fs'),c=require('crypto');\n"
                "const key=Buffer.from(fs.readFileSync(process.argv[2],'utf8').trim(),'hex');\n"
                "const blob=Buffer.from(fs.readFileSync(process.argv[3],'utf8').trim(),'base64');\n"
                "const iv=blob.slice(3,15),ct=blob.slice(15,blob.length-16),tag=blob.slice(blob.length-16);\n"
                "const d=c.createDecipheriv('aes-256-gcm',key,iv);d.setAuthTag(tag);\n"
                "fs.writeFileSync(process.argv[4],Buffer.concat([d.update(ct),d.final()]).toString('utf8'));\n")
        r = subprocess.run(["node", js, keyhex_file, blob_file, out_file],
                           capture_output=True, timeout=30)
        if not os.path.exists(out_file) or r.returncode != 0:
            return None, None, f"AES-GCM decrypt failed (rc={r.returncode}; needs node)"
        api_key = open(out_file).read().strip()
        if not api_key:
            return None, None, "decrypted zcode key is empty"

        site = (_orca_settings() or {}).get("zcodePlanSite") or "zai"
        base = ZCODE_SITES.get(site, ZCODE_SITES["zai"])
        return api_key, base, None
    except Exception as e:
        return None, None, f"zcode key decrypt error: {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _zcode_query(api_key, base_url):
    status, body = fetch(
        f"{base_url.rstrip('/')}/api/monitor/usage/quota/limit",
        headers={"Authorization": api_key, "Accept-Language": "en-US,en",
                 "Content-Type": "application/json"})
    if status is None:
        return result("zcode", "error", error=f"network: {body}")
    if status in (401, 403):
        return result("zcode", "error", error=f"HTTP {status} — API key rejected")
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        return result("zcode", "error", error=f"HTTP {status}: {str(body)[:120]}")
    data = body.get("data") or {}
    out = {"planType": data.get("level")}
    for lim in data.get("limits") or []:
        unit, number = lim.get("unit"), lim.get("number")
        if not isinstance(number, (int, float)) or number <= 0:
            continue
        if lim.get("type") == "TIME_LIMIT" and unit == 5 and number == 1:
            minutes = 720  # Orca 特判：12 小时窗
        else:
            base_min = ZCODE_UNIT_MINUTES.get(unit)
            if base_min is None:
                continue
            minutes = base_min * number
        reset = lim.get("nextResetTime")
        w = window(lim.get("percentage"), minutes,
                   int(reset) if isinstance(reset, (int, float)) and reset > 0 else None)
        if minutes == 300:
            out["session"] = w
        elif minutes == 10080:
            out["weekly"] = w
        elif minutes == 720:
            out["monthly"] = w
        w["used"] = lim.get("currentValue")
        w["limit"] = lim.get("usage")
    return result("zcode", "ok", **out)


def zcode_balance():
    cfg = read_json(os.path.join(HOME, ".zcode", "cli", "config.json"))
    key = (cfg or {}).get("apiKey") or (cfg or {}).get("api_key")
    base = (cfg or {}).get("baseURL") or (cfg or {}).get("base_url")
    if key and base:
        return _zcode_query(key, base)
    api_key, base_url, err = _zcode_orca_key()
    if err:
        return not_local("zcode", err)
    return _zcode_query(api_key, base_url)


# ---------- 凭据在 Orca 加密存储/网页会话里的 provider ----------

def not_local(provider, reason):
    return result(provider, "unavailable", error=reason)


def gemini_balance():
    if not os.path.isdir(os.path.join(HOME, ".gemini")):
        return not_local("gemini", "~/.gemini not found")
    oauth = read_json(os.path.join(HOME, ".gemini", "oauth_creds.json")) or \
        read_json(os.path.join(HOME, ".gemini", "credentials.json"))
    if not oauth:
        return not_local("gemini", "no OAuth credentials in ~/.gemini (gemini CLI not logged in here)")
    # 需要先 loadCodeAssist 取 project id 再 retrieveUserQuota；登录后按 references 补实现
    return not_local("gemini", "gemini OAuth found but quota fetch not implemented yet")


def minimax_balance():
    enc = os.path.join(WIN_HOME, ".orca", "minimax-api-key.enc")
    if os.path.exists(enc):
        return not_local("minimax", "key present in .orca (same decrypt chain as zcode) "
                                    "but usage fetch not wired yet — see references")
    return not_local("minimax", "no .orca/minimax-api-key.enc (not configured in Orca)")


def opencode_go_balance():
    enc = os.path.join(WIN_HOME, ".orca", "opencode-go-api-key.enc")
    if os.path.exists(enc):
        return not_local("opencode-go", "key present in .orca but usage fetch not wired yet")
    return not_local("opencode-go", "no .orca/opencode-go-api-key.enc (not configured in Orca)")


def antigravity_balance():
    return not_local("antigravity", "no local Antigravity credentials found")


PROVIDERS = {
    "grok": grok_balance,
    "codex": codex_balance,
    "cursor": cursor_balance,
    "claude": claude_balance,
    "kimi": kimi_balance,
    "zcode": zcode_balance,
    "gemini": gemini_balance,
    "minimax": minimax_balance,
    "opencode-go": opencode_go_balance,
    "antigravity": antigravity_balance,
}

# ---------- output ----------


def fmt_pct(w):
    if not w:
        return "—"
    pct = w.get("usedPercent")
    reset = w.get("resetsAt")
    tail = ""
    if reset:
        dt = datetime.strptime(reset, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
        tail = f" (重置 {dt.strftime('%m-%d %H:%M')})"
    if pct is None:
        return "—"
    if w.get("used") is not None and w.get("limit") is not None:
        return f"{pct}% ({w['used']}/{w['limit']}){tail}"
    return f"{pct}%{tail}"


def print_table(rows):
    print(f"\n{'Agent':<12} {'Status':<11} {'Session(5h)':<26} {'Weekly':<26} {'Monthly':<20}")
    print("-" * 100)
    for r in rows:
        print(f"{r['provider']:<12} {r['status']:<11} {fmt_pct(r.get('session')):<26} "
              f"{fmt_pct(r.get('weekly')):<26} {fmt_pct(r.get('monthly')):<20}")
        if r.get("error"):
            print(f"{'':<12} └─ {r['error']}")
        for b in r.get("buckets") or []:
            print(f"{'':<12}    · {b['name']}: {fmt_pct(b)}")
        if r.get("onDemand"):
            od = r["onDemand"]
            print(f"{'':<12}    · On-demand: {od.get('used')} / {od.get('cap')}")
        if r.get("planType"):
            print(f"{'':<12}    · plan: {r['planType']}")
        if r.get("tokenFresh") is False:
            print(f"{'':<12}    · ⚠ token 已不新鲜（5 分钟内过期或已过期）")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="查询本机各编程 Agent 的额度/用量余额（与 Orca 状态栏同源）。")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--raw", action="store_true", help="include raw provider payloads")
    ap.add_argument("--provider", help="comma-separated subset, e.g. grok,codex")
    ap.add_argument("--output",
                    help="显式输出路径/目录（JSON 落盘；缺省只打印 stdout。"
                         "最终产物另存主库审计副本）")
    args = ap.parse_args()

    names = [p.strip() for p in args.provider.split(",")] if args.provider else list(PROVIDERS)
    rows = []
    for name in names:
        fn = PROVIDERS.get(name)
        if not fn:
            rows.append(result(name, "error", error="unknown provider"))
            continue
        try:
            rows.append(fn())
        except Exception as e:  # 单家失败不影响其余
            rows.append(result(name, "error", error=f"{type(e).__name__}: {e}"))
    if not args.raw:
        for r in rows:
            r.pop("raw", None)

    payload = {"generatedAt": iso(int(utcnow().timestamp() * 1000)), "providers": rows}
    if args.output:
        # 统一产物树（输出规范.md C-1/C-4）
        plan = plan_output("agentops", "agent-balances", "agent-balances.json",
                           explicit=args.output)
        plan.primary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        committed = plan.commit()
        print(f"已写入 {committed}")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
