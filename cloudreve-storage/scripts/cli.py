#!/usr/bin/env python3
"""Cloudreve Storage CLI 入口。

提供网盘状态探测、文件浏览、文件传输、重命名、删除及统一产物树规范化备份/恢复。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from client import (  # noqa: E402
    CloudreveClient,
    CloudreveError,
    DangerousOperationError,
    SandboxViolationError,
    WebDAVError,
)
from config import CloudreveConfig, init_config_template  # noqa: E402
from manifest import backup_file, prune_backups, restore_file  # noqa: E402


def _get_client(args: argparse.Namespace) -> CloudreveClient:
    cfg = CloudreveConfig.resolve(
        config_path=args.config,
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        root_sandbox=args.root_sandbox,
        host_id=args.host_id,
    )
    return CloudreveClient(cfg)


def _emit_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _print_tree(nodes: list[dict[str, Any]], prefix: str = "") -> None:
    for i, node in enumerate(nodes):
        is_last = i == len(nodes) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        name_str = node["name"] + ("/" if node["is_dir"] else f" ({node['size_human']})")
        print(f"{prefix}{connector}{name_str}")
        if node.get("children"):
            _print_tree(node["children"], child_prefix)


def cmd_status(args: argparse.Namespace) -> int:
    client = _get_client(args)
    info = client.status()
    if args.json:
        _emit_json(info)
        return 0

    print("========================================")
    print("Cloudreve WebDAV 连接检测与空间配额")
    print("========================================")
    print(f"接口地址:    {info['endpoint']}")
    print(f"认证用户:    {info['username']}")
    print(f"主机标识:    {info['host_id']}")
    print(f"安全沙箱:    {info['root_sandbox']}")
    print(f"连接状态:    {info['status']} (HTTP 207)")
    print("空间容量 (七牛云主力存储 / 50 GB 资源包):")
    used_pct = info.get("used_percent", 0.0)
    avail_pct = info.get("available_percent", 100.0)
    print(f"  - 已用空间: {info['used_human']} ({info['quota_used_bytes']} 字节) [{used_pct:.2f}%]")
    print(f"  - 剩余空间: {info['available_human']} ({info['quota_available_bytes']} 字节) [{avail_pct:.2f}%]")
    print(f"  - 总计配额: {info['total_human']} ({info['quota_total_bytes']} 字节)")
    print("========================================")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    client = _get_client(args)
    path = args.remote_path or client.config.root_sandbox
    items = client.ls(path, depth=1, allow_outside=args.outside_sandbox)

    if args.json:
        _emit_json(items)
        return 0

    if not items:
        print(f"目录为空: {path}")
        return 0

    print(f"目录列表: {path} (共 {len(items)} 项)")
    print(f"{'类型':<6} {'大小':<12} {'修改时间':<26} {'名称'}")
    print("-" * 65)
    for it in sorted(items, key=lambda x: (not x["is_dir"], x["name"])):
        t_str = "DIR" if it["is_dir"] else "FILE"
        s_str = "-" if it["is_dir"] else it["size_human"]
        m_str = it["mtime"][:25] if it["mtime"] else "-"
        n_str = it["name"] + ("/" if it["is_dir"] else "")
        print(f"{t_str:<6} {s_str:<12} {m_str:<26} {n_str}")
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    client = _get_client(args)
    path = args.remote_path or client.config.root_sandbox
    nodes = client.tree(
        path, max_depth=args.max_depth, allow_outside=args.outside_sandbox
    )

    if args.json:
        _emit_json(nodes)
        return 0

    print(f"远端目录树: {path} (最大深度: {args.max_depth})")
    print(path)
    _print_tree(nodes)
    return 0


def cmd_cat(args: argparse.Namespace) -> int:
    client = _get_client(args)
    content = client.cat(
        args.remote_path,
        head_bytes=args.bytes,
        head_lines=args.head,
        allow_outside=args.outside_sandbox,
    )
    print(content, end="" if content.endswith("\n") else "\n")
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    client = _get_client(args)
    res = client.put(
        args.local_path,
        args.remote_path,
        overwrite=args.overwrite,
        allow_outside=args.outside_sandbox,
    )
    if args.json:
        _emit_json(res)
    else:
        print(f"文件上传成功: {res['local_path']} -> {res['remote_path']} ({res['size_human']})")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    client = _get_client(args)
    dest = client.get(
        args.remote_path,
        args.local_path,
        overwrite=args.overwrite,
        allow_outside=args.outside_sandbox,
    )
    if args.json:
        _emit_json({"remote_path": args.remote_path, "local_path": str(dest)})
    else:
        print(f"文件下载成功: {args.remote_path} -> {dest}")
    return 0


def cmd_mv(args: argparse.Namespace) -> int:
    client = _get_client(args)
    client.mv(
        args.src,
        args.dst,
        overwrite=args.overwrite,
        allow_outside=args.outside_sandbox,
    )
    if args.json:
        _emit_json({"status": "moved", "src": args.src, "dst": args.dst})
    else:
        print(f"移动/重命名成功: {args.src} -> {args.dst}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    client = _get_client(args)
    # 无论是否 dry-run，先检查沙箱与高危路径
    client.check_sandbox(args.remote_path, allow_outside=args.outside_sandbox)
    client.check_dangerous_path(args.remote_path)

    if args.dry_run:
        print(f"[dry-run] 将删除远端目标: {args.remote_path} (recursive={args.recursive})")
        return 0

    if not args.yes:
        # 终端简易防护确认
        if sys.stdin.isatty():
            confirm = input(f"确认删除远端目标 '{args.remote_path}'? [y/N]: ")
            if confirm.lower() not in ("y", "yes"):
                print("操作已取消。")
                return 0
        else:
            raise DangerousOperationError(
                f"非交互环境下删除远端目标 '{args.remote_path}' 必须显式传递 --yes 参数"
            )

    client.rm(
        args.remote_path,
        recursive=args.recursive,
        allow_outside=args.outside_sandbox,
    )
    if args.json:
        _emit_json({"status": "deleted", "remote_path": args.remote_path})
    else:
        print(f"已删除远端目标: {args.remote_path}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    client = _get_client(args)
    manifest_data, committed_manifest = backup_file(
        client=client,
        local_path=args.local_path,
        category=args.category,
        tag=args.tag,
        output_arg=args.output,
        overwrite=args.overwrite,
    )

    if args.json:
        _emit_json(manifest_data)
        return 0

    print("========================================")
    print("规范化备份成功")
    print("========================================")
    print(f"本地文件:    {manifest_data['source_file']['path']}")
    print(f"文件大小:    {manifest_data['source_file']['size_human']}")
    print(f"SHA256:      {manifest_data['source_file']['sha256']}")
    print(f"备份分类:    {manifest_data['category']}")
    print(f"快照标识:    {manifest_data['snapshot_id']}")
    print(f"远端存储:    {manifest_data['remote']['path']}")
    print(f"Manifest 落盘: {committed_manifest}")
    print("========================================")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    client = _get_client(args)
    result = restore_file(
        client=client,
        remote_path=args.remote_path,
        output_arg=args.output,
        overwrite=args.overwrite,
        allow_outside=args.outside_sandbox,
    )

    if args.json:
        _emit_json(result)
        return 0

    print("========================================")
    print("文件恢复成功")
    print("========================================")
    print(f"远端快照:    {result['remote_path']}")
    print(f"本地恢复路径: {result['restored_path']}")
    print(f"文件大小:    {result['size_human']}")
    print(f"SHA256:      {result['sha256']}")
    print("完整性校验:  通过")
    print("========================================")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    client = _get_client(args)
    if not args.yes and not args.dry_run:
        if sys.stdin.isatty():
            cat_hint = f"分类 '{args.category}'" if args.category else "所有分类"
            confirm = input(f"确认清理 {cat_hint} 下超过保留上限 ({args.keep}) 的过期快照? [y/N]: ")
            if confirm.lower() not in ("y", "yes"):
                print("操作已取消。")
                return 0
        else:
            raise DangerousOperationError(
                "非交互环境下清理过期快照必须显式传递 --yes 或 --dry-run 参数"
            )

    result = prune_backups(
        client=client,
        category=args.category,
        keep=args.keep,
        dry_run=args.dry_run,
        allow_outside=args.outside_sandbox,
    )

    if args.json:
        _emit_json(result)
        return 0

    mode_str = "[dry-run 预览]" if args.dry_run else "[执行完成]"
    print(f"快照保留清理 {mode_str}:")
    print(f"  设备主机标识: {result['host_id']}")
    print(f"  每类保留数量: {result['keep_per_category']}")
    print(f"  保留快照数:   {result['kept_count']}")
    print(f"  清理快照数:   {result['pruned_count']}")
    if result["pruned_snapshots"]:
        print("  拟清理/已清理快照路径:")
        for s in result["pruned_snapshots"]:
            print(f"    - {s}")
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    out = args.output
    path = init_config_template(target_path=out, overwrite=args.overwrite)
    print(f"配置文件已生成: {path} (权限: 0600)")
    print("请在文件中填入您的 Cloudreve 用户名与 WebDAV 应用专用密码。")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="显式指定配置文件路径（默认 ~/.config/cloudreve-storage/config.toml）")
    common.add_argument("--base-url", help="Cloudreve 站点根地址，默认 https://cloudreve.dc-sy.cn")
    common.add_argument("--username", help="WebDAV 用户名")
    common.add_argument("--password", help="WebDAV 应用专用密码")
    common.add_argument("--root-sandbox", help="安全沙箱根路径，默认 /AgentBackups/")
    common.add_argument("--host-id", help="主机标识（默认当前机器短主机名）")
    common.add_argument("--outside-sandbox", action="store_true", help="显式允许操作沙箱之外的网盘目录")
    common.add_argument("--output", help="显式指定输出产物路径/目录（对接 OUTPUT.md 统一产物树，最高优先级）")
    common.add_argument("--json", action="store_true", help="以 JSON 格式输出机器可读结果")

    parser = argparse.ArgumentParser(
        prog="cloudreve-storage",
        description="Cloudreve 云存储网盘操作与备份管理工具",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令")

    # status / doctor
    sub_status = subparsers.add_parser("status", parents=[common], help="检查网盘连接与配额")
    sub_doctor = subparsers.add_parser("doctor", parents=[common], help="检查网盘连接与配额（同 status）")

    # ls
    sub_ls = subparsers.add_parser("ls", parents=[common], help="列出目录内容")
    sub_ls.add_argument("remote_path", nargs="?", default="/AgentBackups/", help="远程目录路径（默认 /AgentBackups/）")

    # tree
    sub_tree = subparsers.add_parser("tree", parents=[common], help="递归打印树形结构")
    sub_tree.add_argument("remote_path", nargs="?", default="/AgentBackups/", help="远程目录路径（默认 /AgentBackups/）")
    sub_tree.add_argument("--max-depth", type=int, default=3, help="最大递归深度（默认 3）")

    # cat
    sub_cat = subparsers.add_parser("cat", parents=[common], help="读取文本文件内容")
    sub_cat.add_argument("remote_path", help="远程文件路径")
    sub_cat.add_argument("--head", type=int, help="读取前 N 行")
    sub_cat.add_argument("--bytes", type=int, help="读取前 N 字节")

    # put
    sub_put = subparsers.add_parser("put", parents=[common], help="上传本地文件到网盘")
    sub_put.add_argument("local_path", help="本地源文件路径")
    sub_put.add_argument("remote_path", help="远端目标路径")
    sub_put.add_argument("--overwrite", action="store_true", help="若远端文件已存在则覆盖")

    # get
    sub_get = subparsers.add_parser("get", parents=[common], help="从网盘下载文件到本地")
    sub_get.add_argument("remote_path", help="远端源文件路径")
    sub_get.add_argument("local_path", help="本地目标路径/目录")
    sub_get.add_argument("--overwrite", action="store_true", help="若本地文件已存在则覆盖")

    # mv
    sub_mv = subparsers.add_parser("mv", parents=[common], help="移动或重命名远端文件/目录")
    sub_mv.add_argument("src", help="源远程路径")
    sub_mv.add_argument("dst", help="目标远程路径")
    sub_mv.add_argument("--overwrite", action="store_true", help="若目标存在则覆盖")

    # rm
    sub_rm = subparsers.add_parser("rm", parents=[common], help="删除远端文件或目录")
    sub_rm.add_argument("remote_path", help="远端目标路径")
    sub_rm.add_argument("-r", "--recursive", action="store_true", help="递归删除非空目录")
    sub_rm.add_argument("-y", "--yes", action="store_true", help="无需确认直接删除")
    sub_rm.add_argument("--dry-run", action="store_true", help="预览待删除项而不实际执行")

    # backup
    sub_backup = subparsers.add_parser("backup", parents=[common], help="规范化文件备份并落 Manifest 审计")
    sub_backup.add_argument("local_path", help="待备份的本地文件路径")
    sub_backup.add_argument("--category", default="default", help="备份分类（如 db, configs, logs，默认 default）")
    sub_backup.add_argument("--tag", help="备份附加标签")
    sub_backup.add_argument("--overwrite", action="store_true", help="允许覆盖同名快照文件")

    # restore
    sub_restore = subparsers.add_parser("restore", parents=[common], help="恢复文件并校验 SHA256")
    sub_restore.add_argument("remote_path", help="远端快照文件或目录路径")
    sub_restore.add_argument("--overwrite", action="store_true", help="允许覆盖本地已有文件")

    # prune
    sub_prune = subparsers.add_parser("prune", parents=[common], help="清理过期备份快照")
    sub_prune.add_argument("--category", help="指定分类（留空则处理当前 host 下所有分类）")
    sub_prune.add_argument("--keep", type=int, default=5, help="每分类保留最新的快照数量（默认 5）")
    sub_prune.add_argument("-y", "--yes", action="store_true", help="无需交互确认")
    sub_prune.add_argument("--dry-run", action="store_true", help="仅预览待清理的快照")

    # init-config
    sub_init = subparsers.add_parser("init-config", parents=[common], help="生成本地配置模板文件")
    sub_init.add_argument("--overwrite", action="store_true", help="覆盖已存在的配置文件")

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    try:
        if args.subcommand in ("status", "doctor"):
            return cmd_status(args)
        elif args.subcommand == "ls":
            return cmd_ls(args)
        elif args.subcommand == "tree":
            return cmd_tree(args)
        elif args.subcommand == "cat":
            return cmd_cat(args)
        elif args.subcommand == "put":
            return cmd_put(args)
        elif args.subcommand == "get":
            return cmd_get(args)
        elif args.subcommand == "mv":
            return cmd_mv(args)
        elif args.subcommand == "rm":
            return cmd_rm(args)
        elif args.subcommand == "backup":
            return cmd_backup(args)
        elif args.subcommand == "restore":
            return cmd_restore(args)
        elif args.subcommand == "prune":
            return cmd_prune(args)
        elif args.subcommand == "init-config":
            return cmd_init_config(args)
        else:
            parser.print_help()
            return 1
    except (
        CloudreveError,
        SandboxViolationError,
        DangerousOperationError,
        WebDAVError,
        FileNotFoundError,
        FileExistsError,
        PermissionError,
        ValueError,
        IOError,
        RuntimeError,
    ) as e:
        if getattr(args, "json", False):
            _emit_json({"error": type(e).__name__, "message": str(e)})
        else:
            print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
