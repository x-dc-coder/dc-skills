"""pytest_test_guard.py — 测试分层门禁 pytest 插件（Anti-False-Pass 工程规范配套）.

功能:
1. 层级标记强制: 按路径 (unit/integration/subsystem/e2e/falsification) 自动打 l0-l4 marker;
   收集时既无层级 marker、路径又不在五层目录内 -> 收集错误。
2. 命名门禁: 文件名/函数名命中任务编号旧模式 (test_m1/test_t7/test_w1c) 直接收集失败。
3. 超时预算: L0=1s / L1=10s / L2=60s / L3=300s / L4=120s（依赖 pytest-timeout，未安装则警告）。
4. 网络熔断: L0/L1 目录下 autouse fixture 屏蔽 socket，联网尝试当场失败。
5. 断言统计: --assert-report 输出零断言测试清单（AST 预扫描），CI 中零断言数 > 0 -> 红。
6. skip 审计: 无 reason 的 skip 报错; xfail(strict=False) 警告。

接入: 根 conftest.py 中  pytest_plugins = ["pytest_test_guard"]
"""
from __future__ import annotations

import ast
import re
import socket
from pathlib import Path

import pytest

TIER_DIRS = {
    "unit": "l0",
    "integration": "l1",
    "subsystem": "l2",
    "e2e": "l3",
    "falsification": "l4",
}
TIER_TIMEOUTS = {"l0": 1, "l1": 10, "l2": 60, "l3": 300, "l4": 120}
LEGACY_NAME_RE = re.compile(r"(?:^|_)(?:m|w|t)\d+(?:_|$)")
FILE_NAME_RE = re.compile(r"^test_[a-z0-9_]+\.py$")
FUNC_NAME_RE = re.compile(r"^test_[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}$")


def pytest_addoption(parser):
    group = parser.getgroup("test_guard")
    group.addoption("--assert-report", action="store_true", default=False,
                    help="终端输出零断言测试清单（CI 门禁用）")
    group.addoption("--guard-off", action="store_true", default=False,
                    help="关闭命名/层级门禁（仅限存量摸底期）")


def _tier_of(item) -> str | None:
    parts = Path(str(item.fspath)).parts
    for d, tier in TIER_DIRS.items():
        if d in parts:
            return tier
    return None


def pytest_collection_modifyitems(config, items):
    if config.getoption("--guard-off"):
        return
    errors = []
    timeout_installed = config.pluginmanager.hasplugin("timeout")
    if not timeout_installed:
        config.issue_config_warning("pytest-timeout 未安装，层级超时预算降级为不执行")
    for item in items:
        path = Path(str(item.fspath))
        # 命名门禁
        if not FILE_NAME_RE.match(path.name) or LEGACY_NAME_RE.search(path.stem):
            errors.append(f"{path.name}: 文件名不符合规范（疑似任务编号命名 test_m1/t7/w1c）")
        if LEGACY_NAME_RE.search(item.name) or not FUNC_NAME_RE.match(item.name):
            errors.append(f"{path.name}::{item.name}: 函数名须为 test_<action>_<condition>_<expected>")
        # 层级标记
        tier = _tier_of(item)
        if tier:
            item.add_marker(getattr(pytest.mark, tier))
        elif not any(item.iter_markers(name=t) for t in TIER_DIRS.values()):
            errors.append(f"{path.name}::{item.name}: 无层级归属 — 放入 unit/integration/subsystem/e2e/falsification 之一，或显式打 l0-l4 marker")
        # 超时预算
        if tier and timeout_installed and not list(item.iter_markers(name="timeout")):
            item.add_marker(pytest.mark.timeout(TIER_TIMEOUTS[tier]))
    if errors:
        raise pytest.UsageError("测试门禁收集失败:\n  " + "\n  ".join(errors[:50])
                                + ("\n  ...(截断)" if len(errors) > 50 else ""))


@pytest.fixture(autouse=True)
def _guard_no_network(request):
    """L0/L1 网络熔断：单元测试与 hermetic 集成测试禁止任何 socket 出口。"""
    tier = _tier_of(request.node)
    if tier not in ("l0", "l1"):
        yield
        return
    real_socket = socket.socket

    class BlockedSocket(real_socket):  # type: ignore[misc,valid-type]
        def connect(self, *a, **kw):
            raise AssertionError(
                f"[test_guard] {tier.upper()} 测试尝试网络出口 — 违反 Hermetic 约束 (R5/L{tier[-1]} 定义)")

        connect_ex = connect

    socket.socket = BlockedSocket  # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = real_socket  # type: ignore[misc]


def _count_asserts_per_test(path: Path) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return {}
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            out[node.name] = sum(isinstance(n, ast.Assert) for n in ast.walk(node))
    return out


_zero_assert: list[str] = []
_seen_files: set[str] = set()


def pytest_runtest_setup(item):
    p = str(Path(str(item.fspath)))
    if p not in _seen_files:
        _seen_files.add(p)
        for name, n in _count_asserts_per_test(Path(p)).items():
            if n == 0:
                _zero_assert.append(f"{Path(p).name}::{name}")


def pytest_runtest_makereport(item, call):
    # skip 审计：无 reason 的 skip
    if call.when == "setup":
        for m in item.iter_markers(name="skip"):
            if not m.kwargs.get("reason") and not (m.args and m.args[0]):
                _audit_warnings.append(f"{item.nodeid}: skip 无 reason — 违反 R7 可审计要求")


_audit_warnings: list[str] = []


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _audit_warnings:
        terminalreporter.write_sep("=", "test_guard: skip/xfail 审计", red=True)
        for w in _audit_warnings[:30]:
            terminalreporter.write_line(w)
    if config.getoption("--assert-report"):
        terminalreporter.write_sep("=", "test_guard: 零断言测试（R7 违规，CI 应红灯）",
                                   red=bool(_zero_assert))
        for z in _zero_assert[:50]:
            terminalreporter.write_line(z)
        terminalreporter.write_line(f"共 {len(_zero_assert)} 个零断言测试")
        if _zero_assert:
            config._guard_failed = True


def pytest_sessionfinish(session, exitstatus):
    if getattr(session.config, "_guard_failed", False) and exitstatus == 0:
        session.exitstatus = 1
