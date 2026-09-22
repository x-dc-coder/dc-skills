#!/usr/bin/env python3
"""cli.py — test-guardian 统一命令行入口（Agent-agnostic & Standalone）.

支持任何 Agent（Claude Code, Cursor, Copilot, DSH 等）或人类工程师、CI 脚本调用。
纯 Python 标准库，零外部依赖。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def cmd_audit(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(HERE / "check_test_hygiene.py")]
    if args.staged:
        cmd.append("--staged")
    if args.warn_only:
        cmd.append("--warn-only")
    if args.own_packages:
        cmd.extend(["--own-packages", args.own_packages])
    if args.rules:
        cmd.extend(["--rules", args.rules])
    cmd.extend(args.paths)
    return subprocess.call(cmd)

def cmd_self_test(args: argparse.Namespace) -> int:
    test_file = HERE / "test_self.py"
    print(f"Running self-test: {test_file}")
    return subprocess.call([sys.executable, "-m", "pytest", str(test_file), "-v"])

def cmd_install_guard(args: argparse.Namespace) -> int:
    target = Path(args.project_dir).resolve()
    if not target.is_dir():
        print(f"Error: {target} 不是有效目录", file=sys.stderr)
        return 1
    tests_dir = target / "tests"
    if not tests_dir.is_dir():
        print(f"Error: 未在 {target} 下发现 tests/ 目录", file=sys.stderr)
        return 1

    dest_plugin = tests_dir / "pytest_test_guard.py"
    src_plugin = HERE / "pytest_test_guard.py"
    import shutil
    shutil.copy2(src_plugin, dest_plugin)
    print(f"✓ 已复制门禁插件到: {dest_plugin}")

    conftest = tests_dir / "conftest.py"
    header = 'pytest_plugins = ["pytest_test_guard"]'
    if conftest.exists():
        content = conftest.read_text(encoding="utf-8")
        if "pytest_test_guard" not in content:
            conftest.write_text(header + "\n" + content, encoding="utf-8")
            print(f"✓ 已向 {conftest} 注入 pytest_plugins 声明")
        else:
            print(f"i {conftest} 已包含 pytest_test_guard，无需重复配置")
    else:
        conftest.write_text(header + "\n", encoding="utf-8")
        print(f"✓ 已创建 {conftest} 并启用 pytest_test_guard")
    print("\n[完成] 可以在该项目下运行 pytest 验证门禁！")
    return 0

def cmd_cheat_sheet(args: argparse.Namespace) -> int:
    sheet = """
==================== test-guardian 防假绿速查卡 ====================
【L0-L4 测试金字塔】
  L0 纯粹单元 (Unit):       零 IO、零 Mock、单测 <=50ms (内存算法/数据变换)
  L1 契约集成 (Contract):   真实 SQLite/tmp_path/lxml, Hermetic 隔离, 单测 <=2s
  L2 子系统流转 (Subsystem):单服务完整链, 仅在边界受控契约 Mock 外部 SaaS, <=5s
  L3 真机桥接 (E2E/Bridge): 真实 Word COM / Playwright 全 SPA, 夜间/发版前
  L4 反证与变异 (Fault/Mut):故障注入、错误码负向链路、mutmut 变异测试 (杀死率 >=80%)

【七条防假绿铁律 (R1-R7)】
  R1 严禁 Mock 被测对象内部 (patch 仅打外部 SaaS 边界, 严禁私有函数 mock)
  R2 严禁仅断言 HTTP 状态码 (必须断言 Payload 业务字段值 + DB 落盘确认)
  R3 严禁命中静态布局的模糊文案 (严禁 assert "待办" in body 命中文本文案)
  R4 异常断言双校验 (pytest.raises 必须校验 exc.value 错误码或信息, 禁裸 except: pass)
  R5 集成测试必须隔离 fixture (setup 断言初始 count==0 干净态, 禁止跨测试共享连接)
  R6 异步与队列必须终态化 (严禁裸 sleep, 强制 wait_until 显式状态机终态)
  R7 每个测试必须可证伪 (禁止恒真断言, 变异体注入必须能让测试转红 RED)

【双盲 RGF 闭环】
  RED 准入 (空桩上必须变红) -> GREEN 实现 (仅改实现代码) -> FALSIFICATION 变异 (微变异必须能转红)
===================================================================
"""
    print(sheet)
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description="test-guardian CLI 工具")
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_audit = sub.add_parser("audit", help="静态 AST 规则扫描")
    p_audit.add_argument("paths", nargs="*", default=[], help="扫描路径")
    p_audit.add_argument("--staged", action="store_true", help="只检查 git staged 文件")
    p_audit.add_argument("--warn-only", action="store_true", help="只警告不阻断")
    p_audit.add_argument("--own-packages", default="app", help="本项目包名")
    p_audit.add_argument("--rules", default="R1,R2,R3,R4,R5,R6,R7,naming", help="启用规则")
    p_audit.set_defaults(func=cmd_audit)

    p_inst = sub.add_parser("install-guard", help="为指定项目接入 pytest_test_guard")
    p_inst.add_argument("project_dir", help="目标工程根目录")
    p_inst.set_defaults(func=cmd_install_guard)

    p_test = sub.add_parser("self-test", help="运行工具自测用例")
    p_test.set_defaults(func=cmd_self_test)

    p_sheet = sub.add_parser("cheat-sheet", help="查看防假绿速查卡")
    p_sheet.set_defaults(func=cmd_cheat_sheet)

    args = ap.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
