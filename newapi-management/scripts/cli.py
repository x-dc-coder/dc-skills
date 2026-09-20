"""newapi-management CLI 入口。

用法示例:
  uv run python newapi-management/scripts/cli.py status
  uv run python newapi-management/scripts/cli.py channel list --page-size 50
  uv run python newapi-management/scripts/cli.py channel get 9
  uv run python newapi-management/scripts/cli.py channel create --config channel.json
  uv run python newapi-management/scripts/cli.py channel create --json '{"type":1,...}'
  uv run python newapi-management/scripts/cli.py channel update --json '{"id":9,"tag":"x"}'
  uv run python newapi-management/scripts/cli.py channel test 9
  uv run python newapi-management/scripts/cli.py log list --model-name qwen3.8-max --page-size 3
  uv run python newapi-management/scripts/cli.py log get 17178
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from newapi_client import NewAPIClient, NewAPIConfig, NewAPIError


def _load_json_arg(args: Any) -> dict[str, Any]:
    """从 --config 文件、--json 内联或 stdin(-) 读取渠道配置。"""
    if args.config:
        path = Path(args.config)
        if not path.is_file():
            raise SystemExit(f"配置文件不存在: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    if args.json:
        return json.loads(args.json)
    if args.config == "-" or (not args.config and not args.json and not sys.stdin.isatty()):
        return json.loads(sys.stdin.read())
    raise SystemExit("请通过 --config <文件>、--json '<JSON>' 或 stdin 提供配置")


def _emit(result: Any, output: str | None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        dest = Path(output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {dest}")
    else:
        print(text)


def _client(args: Any) -> NewAPIClient:
    return NewAPIClient(
        NewAPIConfig.resolve(
            base_url=args.base_url, token=args.token, token_file=args.token_file
        )
    )


def main(argv: list[str] | None = None) -> int:
    # 全局参数定义在 common parser 上，由顶层与各子命令通过 parents 继承，
    # 这样 --output/--token 等既可放在子命令前，也可放在子命令后。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", help="API 地址，默认 http://127.0.0.1:3000")
    common.add_argument("--token", help="访问令牌（或设 NEWAPI_TOKEN 环境变量）")
    common.add_argument(
        "--token-file", help="令牌文件路径（或设 NEWAPI_TOKEN_FILE 环境变量）"
    )
    common.add_argument("--output", help="结果写入指定文件（默认打印到 stdout）")

    parser = argparse.ArgumentParser(
        prog="newapi-management",
        description="NewAPI 渠道与日志管理（fork 版本格式铁律已在客户端内封装）",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="服务状态（无需鉴权也可探测）", parents=[common])

    ch = sub.add_parser("channel", help="渠道管理", parents=[common])
    ch_sub = ch.add_subparsers(dest="action", required=True)

    p = ch_sub.add_parser("list", help="渠道列表", parents=[common])
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=10)
    p.add_argument("--keyword")
    p.add_argument("--group")
    p.add_argument("--tag")
    p.add_argument("--status", type=int)

    p = ch_sub.add_parser("get", help="渠道详情", parents=[common])
    p.add_argument("id", type=int)

    p = ch_sub.add_parser("create", help="创建渠道（自动包裹 mode+channel 并转换字段类型）", parents=[common])
    p.add_argument("--config", help="渠道配置 JSON 文件（- 表示 stdin）")
    p.add_argument("--json", help="渠道配置 JSON 字符串")
    p.add_argument(
        "--mode",
        choices=["single", "batch", "multi_to_single"],
        default="single",
        help="添加模式：single 单建 | batch 按 key 换行批量建 | multi_to_single 多Key轮询",
    )

    p = ch_sub.add_parser("update", help="更新渠道（配置须含 id）", parents=[common])
    p.add_argument("--config", help="渠道配置 JSON 文件（- 表示 stdin）")
    p.add_argument("--json", help="渠道配置 JSON 字符串")

    p = ch_sub.add_parser("delete", help="删除渠道", parents=[common])
    p.add_argument("id", type=int)

    p = ch_sub.add_parser("set-status", help="启用/禁用渠道", parents=[common])
    p.add_argument("id", type=int)
    p.add_argument("status", type=int, help="1 启用 | 2 手动禁用 | 3 自动禁用")

    p = ch_sub.add_parser("test", help="测试渠道连通性（向上游发探测请求）", parents=[common])
    p.add_argument("id", type=int)
    p.add_argument("--model", help="指定测试模型")

    p = ch_sub.add_parser("copy", help="复制渠道（返回新渠道 id）", parents=[common])
    p.add_argument("id", type=int)

    p = ch_sub.add_parser("fetch-models", help="拉取上游可用模型列表", parents=[common])
    p.add_argument("id", type=int)

    lg = sub.add_parser("log", help="日志 / 会话", parents=[common])
    lg_sub = lg.add_subparsers(dest="action", required=True)

    p = lg_sub.add_parser("list", help="日志列表（可多条件检索）", parents=[common])
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=10)
    p.add_argument("--channel-id", type=int)
    p.add_argument("--model-name")
    p.add_argument("--request-id")
    p.add_argument("--user-id", type=int)
    p.add_argument("--token-name")
    p.add_argument("--group")
    p.add_argument("--ip")
    p.add_argument("--start", type=int, help="起始时间戳（秒）")
    p.add_argument("--end", type=int, help="截止时间戳（秒）")
    p.add_argument("--type", type=int, dest="log_type", help="2=消费 3=充值 5=管理 7=错误")

    p = lg_sub.add_parser("get", help="日志详情（含 record 完整会话原文）", parents=[common])
    p.add_argument("id", type=int)

    args = parser.parse_args(argv)

    try:
        client = _client(args)
        result: Any

        if args.cmd == "status":
            result = client.get_status()
        elif args.cmd == "channel":
            if args.action == "list":
                result = client.list_channels(
                    page=args.page,
                    page_size=args.page_size,
                    keyword=args.keyword,
                    group=args.group,
                    tag=args.tag,
                    status=args.status,
                )
            elif args.action == "get":
                result = client.get_channel(args.id)
            elif args.action == "create":
                cfg = _load_json_arg(args)
                client.create_channel(cfg, mode=args.mode)
                result = {"success": True, "message": "创建请求已受理；POST 接口不返回 id，请用 channel list 查看"}
            elif args.action == "update":
                cfg = _load_json_arg(args)
                client.update_channel(cfg)
                result = {"success": True}
            elif args.action == "delete":
                client.delete_channel(args.id)
                result = {"success": True, "deleted": args.id}
            elif args.action == "set-status":
                result = client.set_channel_status(args.id, args.status)
            elif args.action == "test":
                result = client.test_channel(args.id, model=args.model)
            elif args.action == "copy":
                result = client.copy_channel(args.id)
            elif args.action == "fetch-models":
                result = client.fetch_models(args.id)
            else:
                raise SystemExit(f"未知 channel 动作: {args.action}")
        elif args.cmd == "log":
            if args.action == "list":
                result = client.list_logs(
                    page=args.page,
                    page_size=args.page_size,
                    channel_id=args.channel_id,
                    model_name=args.model_name,
                    request_id=args.request_id,
                    user_id=args.user_id,
                    token_name=args.token_name,
                    group=args.group,
                    ip=args.ip,
                    start_timestamp=args.start,
                    end_timestamp=args.end,
                    log_type=args.log_type,
                )
            elif args.action == "get":
                result = client.get_log(args.id)
            else:
                raise SystemExit(f"未知 log 动作: {args.action}")
        else:
            raise SystemExit(f"未知命令: {args.cmd}")

        _emit(result, args.output)
        return 0
    except NewAPIError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"❌ JSON 解析失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
