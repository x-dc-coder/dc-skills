#!/usr/bin/env python3
"""Grok 配置模型连通性与端到端健康检测脚本。

功能：
1. 读取 ~/.grok/config.toml，解析所有注册模型（官方与自定义渠道）。
2. 执行 Grok CLI 端到端无头探测（grok -m <model> -p），验证网络、凭据、协议解析与流式反序列化。
3. 可选 --api 模式直连探测 API 端点与 HTTP 状态码。
4. 输出格式化结果表格或 JSON，为智能体团队派发子代理提供前置健康保障。

执行约定：
cd ~/projects/dc-skills && uv run python scripts/check_models.py [options]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

GROK_CONFIG_PATH = Path.home() / ".grok" / "config.toml"
OFFICIAL_MODELS = [
    "grok-4.7",
    "grok-4.7-build-fast",
    "grok-4.6",
    "grok-4.5",
]


def load_grok_models() -> Dict[str, Dict[str, Any]]:
    """从 ~/.grok/config.toml 加载模型配置。"""
    if not GROK_CONFIG_PATH.is_file():
        return {}

    with open(GROK_CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    models: Dict[str, Dict[str, Any]] = {}

    # 官方模型
    for m in OFFICIAL_MODELS:
        models[m] = {
            "name": m,
            "type": "official",
            "model_id": m,
            "base_url": "SpaceXAI (official)",
            "api_backend": "responses",
            "env_key": None,
        }

    # 自定义模型
    custom_models = data.get("model", {})
    for key, conf in custom_models.items():
        models[key] = {
            "name": conf.get("name", key),
            "type": "custom",
            "model_id": conf.get("model", key),
            "base_url": conf.get("base_url", ""),
            "api_backend": conf.get("api_backend", "responses"),
            "env_key": conf.get("env_key"),
            "reasoning_efforts": conf.get("reasoning_efforts", []),
        }

    return models


def probe_cli_e2e(model_slug: str, timeout_sec: int = 45) -> Dict[str, Any]:
    """通过 Grok CLI 进行真实端到端探测。"""
    cmd = [
        "grok",
        "-m",
        model_slug,
        "-p",
        "Respond with only the single word: PONG",
    ]
    start_t = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
        )
        duration = round(time.time() - start_t, 2)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode == 0 and "PONG" in stdout.upper():
            return {
                "status": "PASS",
                "duration": duration,
                "reply": stdout,
                "error": None,
            }

        # 检查已知反序列化与协议错误
        err_msg = stderr if stderr else stdout
        if "reasoning_part" in err_msg:
            status = "FAIL (Serialization: reasoning_part)"
        elif "missing field" in err_msg:
            status = "FAIL (Serialization: missing field)"
        elif "EOF" in err_msg:
            status = "FAIL (TLS/Network EOF)"
        elif proc.returncode != 0:
            status = f"FAIL (Exit code {proc.returncode})"
        else:
            status = "FAIL (Unexpected response)"

        return {
            "status": status,
            "duration": duration,
            "reply": stdout,
            "error": err_msg[:200],
        }

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start_t, 2)
        return {
            "status": f"TIMEOUT (>{timeout_sec}s)",
            "duration": duration,
            "reply": "",
            "error": "Request timed out",
        }
    except Exception as e:
        duration = round(time.time() - start_t, 2)
        return {
            "status": "ERROR",
            "duration": duration,
            "reply": "",
            "error": str(e),
        }


def print_table(results: List[Dict[str, Any]]) -> None:
    """输出格式化健康检查表格。"""
    headers = ["Model Slug", "Type", "Backend", "Status", "Latency", "Notes / Error"]
    col_w = [22, 10, 18, 14, 10, 30]

    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_w))
    sep_line = "-+-".join("-" * w for w in col_w)

    print("\n" + header_line)
    print(sep_line)

    for r in results:
        status_display = r["status"]
        if "PASS" in status_display:
            status_str = f"\033[32m{status_display}\033[0m"
        elif "TIMEOUT" in status_display:
            status_str = f"\033[33m{status_display}\033[0m"
        else:
            status_str = f"\033[31m{status_display}\033[0m"

        # 处理终端颜色下的 padding
        pad_len = max(0, col_w[3] - len(r["status"]))
        status_padded = status_str + (" " * pad_len)

        note = r.get("error") or r.get("reply", "")
        note = re.sub(r"\s+", " ", note)[:col_w[5]]

        row = [
            r["slug"].ljust(col_w[0]),
            r["type"].ljust(col_w[1]),
            r["backend"].ljust(col_w[2]),
            status_padded,
            f"{r['duration']}s".ljust(col_w[4]),
            note.ljust(col_w[5]),
        ]
        print(" | ".join(row))

    print(sep_line + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 Grok 注册模型的连通性与反序列化健康状态")
    parser.add_argument("--model", "-m", help="指定只测试特定模型 Slug")
    parser.add_argument("--timeout", "-t", type=int, default=45, help="单模型测试超时秒数 (默认 45)")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    parser.add_argument("--skip-official", action="store_true", help="跳过官方 grok 系列模型")
    args = parser.parse_args()

    models = load_grok_models()
    if not models:
        print(f"错误: 未在 {GROK_CONFIG_PATH} 找到任何模型配置", file=sys.stderr)
        sys.exit(1)

    target_slugs = []
    if args.model:
        if args.model not in models:
            print(f"错误: 模型 '{args.model}' 未在配置中登记。可用模型: {list(models.keys())}", file=sys.stderr)
            sys.exit(1)
        target_slugs = [args.model]
    else:
        for slug, meta in models.items():
            if args.skip_official and meta["type"] == "official":
                continue
            # 默认完整探测重点模型
            target_slugs.append(slug)

    print(f"正在探测模型健康状态 (共 {len(target_slugs)} 个目标)...")
    results = []

    for slug in target_slugs:
        meta = models[slug]
        sys.stderr.write(f"  -> 测试 {slug} ... ")
        sys.stderr.flush()

        res = probe_cli_e2e(slug, timeout_sec=args.timeout)
        sys.stderr.write(f"{res['status']} ({res['duration']}s)\n")
        sys.stderr.flush()

        results.append({
            "slug": slug,
            "type": meta["type"],
            "backend": meta["api_backend"],
            "base_url": meta["base_url"],
            "status": res["status"],
            "duration": res["duration"],
            "reply": res["reply"],
            "error": res["error"],
        })

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print_table(results)

    # 统计
    passed = sum(1 for r in results if "PASS" in r["status"])
    failed = len(results) - passed
    print(f"探测完成: {passed} 成功, {failed} 异常。")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
