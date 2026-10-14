#!/usr/bin/env python3
"""mcp-apply — 全机本地 MCP 编译器与配置分发工具。

从 agent-map.yaml 的 mcps 段读取多客户端 MCP 配置，编译并分发到对应宿主客户端配置文件：
  - Grok: ~/.grok/config.toml（受安全标记块 BEGIN/END 保护，绝不触碰块外配置）
  - Claude Code: ~/.claude.json（受控合并 mcpServers 键，严格保留其他顶层字段与未受控服务）

特性：
  - 安全标记块保护与完整性检验（缺失 END 时拒绝写入）
  - 凭据安全审计（敏感环境变量必须以 ${VAR} 或 Bearer ${VAR} 声明，明文直接退出码 2）
  - 事务性写入：临时文件先行校验（tomli/json），原子替换（os.replace）
  - 带时间戳备份与回滚机制（--apply 时生成 .bak-<timestamp>，--rollback 回滚最新备份）
  - 幂等性与预检：--check（默认，0 一致，1 漂移）/ --apply / --rollback
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import tomli
import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "agent-map.yaml"

GROK_CONFIG_PATH = Path.home() / ".grok" / "config.toml"
CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"

BEGIN_MARKER = "# BEGIN dc-farm mcp"
HEADER_COMMENT = "# Managed by dc-skills mcp-apply. DO NOT EDIT THIS BLOCK MANUALLY."
END_MARKER = "# END dc-farm mcp"

SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(key|secret|token|password|auth|credential|api_key|access_token)"
)
SAFE_ENV_VAR_PATTERN = re.compile(r"^(Bearer\s+)?\$\{[A-Za-z0-9_]+\}$")


def validate_credential_safety(mcp_name: str, config: Mapping[str, Any]) -> None:
    """检查 MCP 配置中的敏感字段，发现明文凭据直接以退出码 2 退出。"""
    # 检查 headers
    headers = config.get("headers") or {}
    if isinstance(headers, dict):
        for k, v in headers.items():
            if isinstance(v, str):
                if SENSITIVE_KEY_PATTERN.search(k) or k.lower() in ("authorization", "x-api-key"):
                    if not SAFE_ENV_VAR_PATTERN.match(v.strip()):
                        print(
                            f"🚨 安全拦截 [MCP {mcp_name}]: header {k!r} 包含非环境引用明文凭据: {v!r}\n"
                            f"   只允许形如 '${{VAR}}' 或 'Bearer ${{VAR}}' 的环境变量引用形式。",
                            file=sys.stderr,
                        )
                        sys.exit(2)

    # 检查 env
    env = config.get("env") or {}
    if isinstance(env, dict):
        for k, v in env.items():
            if isinstance(v, str):
                if SENSITIVE_KEY_PATTERN.search(k):
                    if not SAFE_ENV_VAR_PATTERN.match(v.strip()):
                        print(
                            f"🚨 安全拦截 [MCP {mcp_name}]: 环境变量 {k!r} 包含非环境引用明文凭据: {v!r}\n"
                            f"   只允许形如 '${{VAR}}' 或 'Bearer ${{VAR}}' 的环境变量引用形式。",
                            file=sys.stderr,
                        )
                        sys.exit(2)


def render_grok_toml_block(grok_mcps: Mapping[str, Any]) -> str:
    """根据目标 MCP 生成 Grok config.toml 中的安全标记块。"""
    lines: list[str] = [BEGIN_MARKER, HEADER_COMMENT]
    for name, cfg in sorted(grok_mcps.items()):
        lines.append(f"[mcp_servers.{name}]")
        transport = cfg.get("transport", "stdio")
        emit = cfg.get("emit", "full")
        enabled = False if emit == "disabled" else bool(cfg.get("enabled", True))

        if transport in ("http", "sse"):
            url = cfg.get("url", "")
            lines.append(f'url = "{url}"')
        else:
            command = cfg.get("command", "")
            lines.append(f'command = "{command}"')
            args = cfg.get("args") or []
            if args:
                args_repr = ", ".join(f'"{a}"' for a in args)
                lines.append(f"args = [{args_repr}]")

        lines.append(f"enabled = {'true' if enabled else 'false'}")
        if "startup_timeout_sec" in cfg:
            lines.append(f"startup_timeout_sec = {cfg['startup_timeout_sec']}")

        headers = cfg.get("headers") or {}
        if headers:
            lines.append(f"[mcp_servers.{name}.headers]")
            for hk, hv in sorted(headers.items()):
                lines.append(f'{hk} = "{hv}"')

        env = cfg.get("env") or {}
        if env:
            lines.append(f"[mcp_servers.{name}.env]")
            for ek, ev in sorted(env.items()):
                lines.append(f'{ek} = "{ev}"')

        lines.append("")  # 空行分隔段落

    if lines[-1] == "":
        lines.pop()

    lines.append(END_MARKER)
    return "\n".join(lines)


def update_grok_config(current_content: str, grok_mcps: Mapping[str, Any]) -> tuple[str, bool]:
    """更新 Grok config.toml 内容。返回 (新内容, 是否发生变更)。"""
    has_begin = BEGIN_MARKER in current_content
    has_end = END_MARKER in current_content

    if has_begin != has_end:
        raise ValueError(
            "Grok 配置文件标记块损坏：检测到 BEGIN 或 END 单侧缺失，拒绝继续操作以防配置损毁！"
        )

    # 如果没有需要管理的 grok mcp，且原来没有标记块，则无变动
    if not grok_mcps and not has_begin:
        return current_content, False

    # 生成期望块（如果没有要管理的 mcp，则期望块为空字符串）
    expected_block = render_grok_toml_block(grok_mcps) if grok_mcps else ""

    if has_begin:
        # 替换已有标记块
        pattern = re.compile(
            rf"{re.escape(BEGIN_MARKER)}[\s\S]*?{re.escape(END_MARKER)}\r?\n?",
            re.MULTILINE,
        )
        if expected_block:
            new_content = pattern.sub(expected_block + "\n", current_content)
        else:
            new_content = pattern.sub("", current_content)
    else:
        # 首次插入：追加到文件末尾
        prefix = current_content
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        new_content = prefix + expected_block + "\n"

    # 规范化多余空行
    return new_content, (new_content != current_content)


def build_claude_server_entry(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """为 Claude .claude.json 构建单一 MCP 服务条目。"""
    transport = cfg.get("transport", "stdio")
    entry: dict[str, Any] = {}
    if transport in ("http", "sse"):
        entry["type"] = transport
        entry["url"] = cfg.get("url", "")
        headers = cfg.get("headers")
        if headers:
            entry["headers"] = dict(headers)
    else:
        entry["type"] = "stdio"
        entry["command"] = cfg.get("command", "")
        args = cfg.get("args") or []
        entry["args"] = list(args)
        env = cfg.get("env")
        if env:
            entry["env"] = dict(env)
    return entry


def update_claude_config(
    current_data: dict[str, Any],
    claude_mcps: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """更新 Claude .claude.json 字典。返回 (新数据字典, 是否发生变更)。

    受控合并契约：
      - mcpServers 中属于 claude_mcps 的受控键被更新/添加；
      - 若某受控键不再存在于 claude_mcps，但曾被本工具标记记录（或在此次清单中未列入但 clients 移除），
        此处严格以受控清单管理：保留其他非受控键，仅对受控键进行精确增量/覆盖。
    """
    new_data = dict(current_data)
    servers = dict(new_data.get("mcpServers") or {})
    changed = False

    for name, cfg in claude_mcps.items():
        emit = cfg.get("emit", "full")
        enabled = False if emit == "disabled" else bool(cfg.get("enabled", True))
        if not enabled:
            # 禁用状态：若已存在则移除或标记
            if name in servers:
                del servers[name]
                changed = True
            continue

        target_entry = build_claude_server_entry(cfg)
        if servers.get(name) != target_entry:
            servers[name] = target_entry
            changed = True

    if changed:
        new_data["mcpServers"] = servers

    return new_data, changed


def backup_file(file_path: Path) -> Path:
    """创建时间戳备份文件。"""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_path = file_path.with_name(f"{file_path.name}.bak-{ts}")
    bak_path.write_bytes(file_path.read_bytes())
    return bak_path


def atomic_write(file_path: Path, content: str | bytes) -> None:
    """带临时文件与静态语法校验的原子写入。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(f"{file_path.name}.tmp.{os.getpid()}")
    try:
        if isinstance(content, str):
            tmp_path.write_text(content, encoding="utf-8")
        else:
            tmp_path.write_bytes(content)

        # 语法完整性自检
        if file_path.suffix == ".toml" or file_path.name.endswith(".toml"):
            with open(tmp_path, "rb") as f:
                tomli.load(f)
        elif file_path.suffix == ".json" or file_path.name.endswith(".json"):
            with open(tmp_path, "r", encoding="utf-8") as f:
                json.load(f)

        tmp_path.replace(file_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def find_latest_backup(file_path: Path) -> Path | None:
    """查找最近的一个备份文件。"""
    parent = file_path.parent
    if not parent.exists():
        return None
    prefix = f"{file_path.name}.bak-"
    baks = [p for p in parent.iterdir() if p.name.startswith(prefix)]
    if not baks:
        return None
    baks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return baks[0]


def rollback_file(file_path: Path) -> bool:
    """将文件回滚至最新备份。"""
    bak = find_latest_backup(file_path)
    if not bak:
        print(f"未找到可用备份: {file_path}")
        return False
    print(f"回滚: {file_path} <- {bak.name}")
    content = bak.read_bytes()
    atomic_write(file_path, content)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="全机本地 MCP 编译器与配置分发工具")
    parser.add_argument("--check", action="store_true", help="检查漂移，不修改任何文件（默认）")
    parser.add_argument("--apply", action="store_true", help="原子写入目标配置并保留带时间戳备份")
    parser.add_argument("--rollback", action="store_true", help="将目标配置回滚至最近一份备份")
    args = parser.parse_args()

    if args.rollback:
        rb_grok = rollback_file(GROK_CONFIG_PATH) if GROK_CONFIG_PATH.exists() else False
        rb_claude = rollback_file(CLAUDE_CONFIG_PATH) if CLAUDE_CONFIG_PATH.exists() else False
        print("回滚完成" if (rb_grok or rb_claude) else "未执行任何回滚")
        sys.exit(0)

    # 默认模式为 check（dry-run）
    is_apply = bool(args.apply)

    manifest_data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    mcps_data = manifest_data.get("mcps") or {}

    # 1. 安全凭据检测
    for name, cfg in mcps_data.items():
        if isinstance(cfg, dict):
            validate_credential_safety(name, cfg)

    # 2. 按目标受体分类
    grok_mcps: dict[str, Any] = {}
    claude_mcps: dict[str, Any] = {}

    for name, cfg in mcps_data.items():
        if not isinstance(cfg, dict):
            continue
        clients = cfg.get("clients") or []
        if "grok" in clients:
            grok_mcps[name] = cfg
        if "claude" in clients:
            claude_mcps[name] = cfg

    drift_found = False

    # 3. 检查 / 更新 Grok 配置
    if GROK_CONFIG_PATH.exists():
        grok_raw = GROK_CONFIG_PATH.read_text(encoding="utf-8")
        try:
            new_grok_raw, grok_changed = update_grok_config(grok_raw, grok_mcps)
        except ValueError as err:
            sys.exit(f"🚨 Grok 配置处理失败: {err}")

        if grok_changed:
            drift_found = True
            if is_apply:
                bak = backup_file(GROK_CONFIG_PATH)
                atomic_write(GROK_CONFIG_PATH, new_grok_raw)
                print(f"[写入] Grok 配置更新成功（已备份至 {bak.name}）")
            else:
                print(f"[漂移] Grok 配置（{GROK_CONFIG_PATH}）存在漂移，需要同步")
        else:
            print(f"[一致] Grok 配置（{GROK_CONFIG_PATH}）与清单一致")
    else:
        if grok_mcps:
            drift_found = True
            print(f"[提示] Grok 配置文件不存在: {GROK_CONFIG_PATH}")

    # 4. 检查 / 更新 Claude 配置
    if CLAUDE_CONFIG_PATH.exists():
        claude_raw = CLAUDE_CONFIG_PATH.read_text(encoding="utf-8")
        try:
            claude_data = json.loads(claude_raw)
        except json.JSONDecodeError as err:
            sys.exit(f"🚨 Claude 配置文件 JSON 损坏: {err}")

        new_claude_data, claude_changed = update_claude_config(claude_data, claude_mcps)

        if claude_changed:
            drift_found = True
            if is_apply:
                bak = backup_file(CLAUDE_CONFIG_PATH)
                formatted = json.dumps(new_claude_data, indent=2, ensure_ascii=False) + "\n"
                atomic_write(CLAUDE_CONFIG_PATH, formatted)
                print(f"[写入] Claude 配置更新成功（已备份至 {bak.name}）")
            else:
                print(f"[漂移] Claude 配置（{CLAUDE_CONFIG_PATH}）存在漂移，需要同步")
        else:
            print(f"[一致] Claude 配置（{CLAUDE_CONFIG_PATH}）与清单一致")
    else:
        if claude_mcps:
            drift_found = True
            print(f"[提示] Claude 配置文件不存在: {CLAUDE_CONFIG_PATH}")

    if not is_apply:
        if drift_found:
            print("\n发现配置漂移（加 --apply 执行写入同步）")
            sys.exit(1)
        else:
            print("\n全机 MCP 配置一致，无漂移")
            sys.exit(0)
    else:
        print("\n全机 MCP 编译与分发完成")
        sys.exit(0)


if __name__ == "__main__":
    main()
