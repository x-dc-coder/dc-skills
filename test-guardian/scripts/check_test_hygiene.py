#!/usr/bin/env python3
"""check_test_hygiene.py — 防假绿（Anti-False-Pass）AST 规则检查器.

实现《跨项目测试工程规范》第三部分的七条铁律 R1-R7 + 命名门禁(naming)。
纯 stdlib，零依赖。

用法:
    python check_test_hygiene.py tests/ --own-packages app,wordprod
    python check_test_hygiene.py tests/ --rules R2,R4
    python check_test_hygiene.py tests/ --rules naming
    python check_test_hygiene.py tests/ --warn-only        # 存量摸底期不阻断

豁免: 违规行尾加  # hygiene: allow R2 reason="..."  （reason 必填非空）。
退出码: 0 = 通过; 1 = 有违规(block 模式); 2 = 用法/解析错误。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ALLOW_RE = re.compile(r"#\s*hygiene:\s*allow\s+(R\d|naming)\b\s+reason=\"([^\"]+)\"")
LEGACY_FILE_RE = re.compile(r"^test_.*(?:_|)(m|w|t)\d+.*\.py$")
FUNC_NAME_RE = re.compile(r"^test_[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}$")
FUZZY_TEXTS = {"成功", "欢迎", "提交", "确定", "完成", "失败", "ok", "success", "welcome", "submit", "done", "error", "loading"}
ISOLATION_FIXTURES = {"tmp_path", "tmp_path_factory", "memory_db", "memory_db_with_migration",
                      "clean_db", "clean_env", "sqlite_memory", "fresh_db", "isolated_db"}
DB_FACTORY_CALLS = {"connect", "create_engine", "Session", "sessionmaker", "client_from_url"}
HTTP_VERBS = {"get", "post", "put", "patch", "delete"}
SLEEP_FUNCS = {"sleep", "wait_for_timeout"}
BROAD_EXC = {"Exception", "BaseException"}


class Finding:
    __slots__ = ("path", "lineno", "rule", "msg")

    def __init__(self, path, lineno, rule, msg):
        self.path, self.lineno, self.rule, self.msg = path, lineno, rule, msg

    def __str__(self):
        return f"{self.path}:{self.lineno} [{self.rule}] {self.msg}"


def _name_of(node) -> str:
    """点号全名: mock.patch -> 'mock.patch'; foo -> 'foo'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _str_arg(call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


class TestFileVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source_lines: list[str], own_packages: set[str], rules: set[str]):
        self.path = path
        self.lines = source_lines
        self.own = own_packages
        self.rules = rules
        self.findings: list[Finding] = []
        self.test_funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def enabled(self, rule: str) -> bool:
        return rule in self.rules

    def allowed(self, lineno: int, rule: str) -> bool:
        if lineno is None or lineno > len(self.lines):
            return False
        m = ALLOW_RE.search(self.lines[lineno - 1])
        return bool(m and m.group(1) == rule and m.group(2).strip())

    def add(self, node, rule, msg):
        lineno = getattr(node, "lineno", 0)
        if self.enabled(rule) and not self.allowed(lineno, rule):
            self.findings.append(Finding(self.path, lineno, rule, msg))

    # ---------- 模块级 ----------
    def visit_Module(self, node):
        for stmt in node.body:  # R5: 模块作用域建连接
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and _name_of(sub.func).split(".")[-1] in DB_FACTORY_CALLS:
                    self.add(sub, "R5", f"模块级建立连接/Session: {_name_of(sub.func)}() — 必须改用隔离 fixture")
        self.generic_visit(node)

    # ---------- 测试函数收集 ----------
    def visit_FunctionDef(self, node):
        if node.name.startswith("test_"):
            self.test_funcs.append(node)
            self.check_function_name(node)
            self.check_r7_structure(node)
            self.walk_function_rules(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def check_function_name(self, fn):
        if not self.enabled("naming"):
            return
        if LEGACY_FILE_RE.match(fn.name + ".py") or re.search(r"(?:_|^)(?:m|w|t)\d+(?:_|$)", fn.name):
            self.add(fn, "naming", f"测试函数名含任务编号模式: {fn.name}")
        elif not FUNC_NAME_RE.match(fn.name):
            self.add(fn, "naming", f"测试函数名不符合 test_<action>_<condition>_<expected>: {fn.name}")

    # ---------- R7 结构 ----------
    def check_r7_structure(self, fn):
        body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if not body or all(isinstance(s, ast.Pass) for s in body):
            self.add(fn, "R7", f"空测试体: {fn.name}")
        for dec in fn.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            dname = _name_of(target)
            if isinstance(dec, ast.Call):
                if dname.endswith("skip") and not any(kw.arg == "reason" and kw.value for kw in dec.keywords):
                    self.add(dec, "R7", "skip 必须带 reason=")
                if dname.endswith("xfail"):
                    strict = any(kw.arg == "strict" for kw in dec.keywords) or (
                        dec.args and isinstance(dec.args[0], ast.Constant) and dec.args[0].value is True)
                    if not strict:
                        self.add(dec, "R7", "xfail 必须 strict=True，防止意外通过静默变绿")

    # ---------- 函数体规则遍历 ----------
    def walk_function_rules(self, fn):
        fixture_args = {a.arg for a in fn.args.args}
        asserts: list[ast.Assert] = []
        called_names: list[str] = []
        raises_bindings: list[tuple[ast.withitem, ast.With]] = []
        exc_handlers: list[ast.ExceptHandler] = []
        if_wrapped_assert = False
        fuzzy_text_checks: list[ast.Call] = []
        has_testid_anchor = False
        has_explicit_wait = False
        has_data_assert = False
        has_status_or_existence_assert = False
        has_called_assert = False
        http_client_call = False

        for node in ast.walk(fn):
            if isinstance(node, ast.Assert):
                asserts.append(node)
                src = ast.dump(node.test)
                if isinstance(node.test, ast.Constant) and node.test.value in (True, 1):
                    self.add(node, "R7", f"恒真断言: assert {node.test.value!r}")
                if (isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
                        and len(node.test.comparators) == 1 and isinstance(node.test.comparators[0], ast.Name)
                        and node.test.left.id == node.test.comparators[0].id):
                    self.add(node, "R7", "自比较断言 (x == x) 恒真")
                if "status_code" in src or _is_existence_assert(node.test):
                    has_status_or_existence_assert = True
                else:
                    has_data_assert = True
            if isinstance(node, ast.Call):
                cname = _name_of(node.func)
                called_names.append(cname)
                last = cname.split(".")[-1]
                if last in SLEEP_FUNCS and "wait_until" not in cname and "support" not in str(self.path):
                    self.add(node, "R6", f"裸 {cname}() 作为同步手段 — 改用 wait_until 等待显式终态")
                if last in HTTP_VERBS and any(p in cname for p in ("client", "http", "session", "requests")):
                    http_client_call = True
                if last.startswith("assert_called"):
                    has_called_assert = True
                if last in ("to_contain_text", "to_have_text", "inner_text", "text_content"):
                    arg = _str_arg(node)
                    if arg is not None and (arg.lower() in FUZZY_TEXTS or len(arg) < 4):
                        fuzzy_text_checks.append(node)
                if last in ("wait_for", "wait_for_selector", "wait_for_response", "wait_until", "expect_response"):
                    has_explicit_wait = True
                if last == "raises" and ("pytest" in cname or "raises" == cname):
                    if node.args:
                        exc_name = _name_of(node.args[0]).split(".")[-1]
                        if exc_name in BROAD_EXC:
                            self.add(node, "R4", f"pytest.raises({exc_name}) 过宽 — 必须用具体业务异常类")
            if "get_by_test_id" in _name_of(getattr(node, "func", node)) or (
                    isinstance(node, ast.Constant) and isinstance(node.value, str) and "data-testid" in node.value):
                has_testid_anchor = True
            if isinstance(node, ast.With):
                for item in node.items:
                    if (isinstance(item.context_expr, ast.Call)
                            and _name_of(item.context_expr.func).endswith("raises")):
                        raises_bindings.append((item, node))
            if isinstance(node, ast.ExceptHandler):
                exc_handlers.append(node)
            if isinstance(node, ast.If):
                if any(isinstance(s, ast.Assert) for s in node.body):
                    if_wrapped_assert = True

        # R4: raises 后必须校验 exc.value
        for item, with_node in raises_bindings:
            if item.optional_vars is None:
                self.add(with_node, "R4", "pytest.raises 未用 as exc 绑定，无法校验异常内容")
                continue
            vname = getattr(item.optional_vars, "id", None)
            checked = any(isinstance(s, ast.Assert) and vname and vname in ast.dump(s)
                          for s in _stmts_after(with_node, fn))
            if not checked:
                self.add(with_node, "R4", f"pytest.raises as {vname} 后缺少对 {vname}.value(错误码/消息) 的断言")

        # R4: except-pass 吞异常
        for h in exc_handlers:
            if all(isinstance(s, ast.Pass) for s in h.body):
                self.add(h, "R4", "except: pass 吞掉失败 — 测试可能假绿")

        # R7: 条件断言
        if if_wrapped_assert:
            self.add(fn, "R7", f"if 包裹的断言 — 条件为假时静默绿: {fn.name}")

        # R1: mock 边界
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            cname = _name_of(node.func)
            if cname.split(".")[-1] == "patch" and any(p in cname for p in ("mock", "patch")):
                target = _str_arg(node)
                if target and any(target.startswith(pkg) for pkg in self.own):
                    parts = target.split(".")
                    if any(p.startswith("_") for p in parts[1:]):
                        self.add(node, "R1", f"patch 本项目私有实现: {target} — mock 只许打在系统边界")
            if cname.split(".")[-1] == "object" and "patch" in cname:
                if node.args and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    if str(node.args[1].value).startswith("_"):
                        self.add(node, "R1", f"patch.object 私有方法 {node.args[1].value!r} — 属 Mock 被测对象内部实现")

        # R1: 仅断言 mock 调用次数（自证式测试）
        if has_called_assert and not has_data_assert:
            self.add(fn, "R1", f"{fn.name}: 仅断言 mock 调用次数，无数据面断言（自证式测试）")

        # R2: HTTP 测试断言强度
        if http_client_call:
            if asserts and not has_data_assert and has_status_or_existence_assert:
                self.add(fn, "R2", f"{fn.name}: 仅断言状态码/存在性 — 必须断言业务载荷字段值+持久化验证")
            if not asserts:
                self.add(fn, "R7", f"{fn.name}: 无任何断言")

        # R3: 模糊文案
        for node in fuzzy_text_checks:
            if not (has_testid_anchor and has_explicit_wait):
                arg = _str_arg(node)
                self.add(node, "R3", f"模糊文案断言 {arg!r} 可能命中静态布局 — 需 data-testid 锚点 + 显式 wait")

        # R5: 集成层隔离 fixture
        if "integration" in self.path.parts and self.enabled("R5"):
            if not (fixture_args & ISOLATION_FIXTURES):
                self.add(fn, "R5", f"{fn.name}: integration 层必须请求隔离 fixture (tmp_path/memory_db...)，当前: {sorted(fixture_args)}")

        # R7: 零断言（非 http 已在上面报）
        if not asserts and not http_client_call and not raises_bindings:
            self.add(fn, "R7", f"{fn.name}: 测试无断言 — 绿色不可解释")


def _stmts_after(with_node: ast.With, fn) -> list[ast.stmt]:
    """with 块之后（同层级）的语句。"""
    out: list[ast.stmt] = []
    for parent in ast.walk(fn):
        for field in ("body",):
            body = getattr(parent, field, None)
            if isinstance(body, list) and with_node in body:
                idx = body.index(with_node)
                out.extend(body[idx + 1:])
    return out


def _is_existence_assert(test) -> bool:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return False
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        right = test.comparators[0]
        if isinstance(op, (ast.IsNot, ast.Is)) and isinstance(right, ast.Constant) and right.value is None:
            return True
    if isinstance(test, ast.Name):
        return True  # assert resp
    return False


def check_file(path: Path, own_packages: set[str], rules: set[str]) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Finding(path, 0, "parse", f"无法读取: {e}")]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return [Finding(path, e.lineno or 0, "parse", f"语法错误: {e.msg}")]
    findings: list[Finding] = []
    if "naming" in rules:
        base = path.name
        if LEGACY_FILE_RE.match(base):
            findings.append(Finding(path, 1, "naming", f"文件名含任务编号模式: {base} — 按领域重命名"))
        elif not re.match(r"^test_[a-z0-9_]+\.py$", base):
            findings.append(Finding(path, 1, "naming", f"文件名不符合 test_<domain>_<surface>.py: {base}"))
    v = TestFileVisitor(path, source.splitlines(), own_packages, rules)
    v.visit(tree)
    return findings + v.findings


def _get_staged_test_files() -> list[Path]:
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=d"],
            text=True, stderr=subprocess.DEVNULL
        )
        return [Path(p.strip()) for p in out.splitlines() if p.strip().endswith(".py") and ("test_" in p or "conftest" in p)]
    except Exception:
        return []

def main() -> int:
    ap = argparse.ArgumentParser(description="Anti-False-Pass 测试卫生检查器 (R1-R7 + naming)")
    ap.add_argument("paths", nargs="*", default=[], help="测试目录或文件")
    ap.add_argument("--staged", action="store_true", help="只检查 git staged 暂存区的测试文件（pre-commit模式）")
    ap.add_argument("--own-packages", default="app", help="本项目顶层包名，逗号分隔 (默认: app)")
    ap.add_argument("--rules", default="R1,R2,R3,R4,R5,R6,R7,naming", help="启用的规则，逗号分隔")
    ap.add_argument("--warn-only", action="store_true", help="只警告不阻断（存量摸底期）")
    args = ap.parse_args()

    own = {p.strip() for p in args.own_packages.split(",") if p.strip()}
    rules = {r.strip() for r in args.rules.split(",") if r.strip()}
    files: list[Path] = []
    if args.staged:
        files.extend(_get_staged_test_files())
        if not files:
            print("check_test_hygiene: 无 staged 测试文件，跳过")
            return 0
    for p in args.paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(f for f in pp.rglob("test_*.py") if "__pycache__" not in f.parts))
        elif pp.is_file():
            files.append(pp)
    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(check_file(f, own, rules))

    for fd in all_findings:
        prefix = "WARN" if args.warn_only else "FAIL"
        print(f"{prefix} {fd}", file=sys.stderr if not args.warn_only else sys.stdout)
    by_rule: dict[str, int] = {}
    for fd in all_findings:
        by_rule[fd.rule] = by_rule.get(fd.rule, 0) + 1
    print(f"\nscanned {len(files)} files, {len(all_findings)} findings: "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_rule.items())))
    if all_findings and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())