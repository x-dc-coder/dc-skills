"""Shared utilities for diagram-* / db-skill skills + 统一产物树（OUTPUT.md）。"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: 环境变量名：显式指定 db-skill 配置文件路径
DEFAULT_CONFIG_ENV = "DB_SKILL_CONFIG"

#: 主库根（本文件位于 <master>/scripts/）。输出路径判定与审计副本的唯一基准。
#: 经农场软链（~/.claude/skills/<skill> 等）进入时，resolve() 回到主库，判定依然成立。
MASTER_ROOT = Path(__file__).resolve().parent.parent

#: 统一产物树根目录名（OUTPUT.md C-1）
OUTPUT_ROOT_NAME = "skills-output"

#: 时间戳格式（OUTPUT.md C-2：进程级单次，无冒号保证 Windows 安全）
TIMESTAMP_FMT = "%Y%m%d-%H%M%S"

_RUN_TS: Optional[str] = None


def is_under(path: Path, root: Path) -> bool:
    """path（解析后）是否位于 root 之下。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def is_under_master(path: Path) -> bool:
    """path（解析后）是否位于主库根之下。"""
    return is_under(path, MASTER_ROOT)


def run_timestamp() -> str:
    """进程级单次时间戳（一个 CLI 进程的所有落盘共享）。"""
    global _RUN_TS
    if _RUN_TS is None:
        _RUN_TS = datetime.now().strftime(TIMESTAMP_FMT)
    return _RUN_TS


def skill_family(skill_name: str) -> str:
    """从主库 <skill_name>/SKILL.md 的 metadata.family 读族群（OUTPUT.md C-3）。
    脚本目录（无 SKILL.md）回落约定：diagram-* → drawing；其余 → standalone。"""
    skill_md = MASTER_ROOT / skill_name / "SKILL.md"
    if skill_md.is_file():
        m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", skill_md.read_text(encoding="utf-8"))
        if m:
            fm_line = re.search(r"^\s+family:\s*(\S+)", m.group(1), re.M)
            if fm_line:
                return fm_line.group(1).strip().strip('"\'')
    if skill_name.startswith("diagram-"):
        return "drawing"
    return "standalone"


def output_root(cwd: Optional[Path] = None) -> Path:
    """统一产物树根：<cwd>/skills-output（OUTPUT.md C-1）。"""
    return (Path(cwd) if cwd else Path.cwd()).resolve() / OUTPUT_ROOT_NAME


def run_dir(family: str, skill_name: str, ts: Optional[str] = None,
            cwd: Optional[Path] = None) -> Path:
    """本次运行目录：<cwd>/skills-output/<family>/<skill_name>/<ts>/（自动创建）。"""
    d = output_root(cwd) / family / skill_name / (ts or run_timestamp())
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_dir(family: str, skill_name: str, ts: Optional[str] = None) -> Path:
    """主库审计副本目录：<master>/skills-output/<family>/<skill_name>/<ts>/（C-4）。"""
    d = MASTER_ROOT / OUTPUT_ROOT_NAME / family / skill_name / (ts or run_timestamp())
    d.mkdir(parents=True, exist_ok=True)
    return d


class OutputPlan:
    """一次运行的产物计划（OUTPUT.md C-1/C-4）。

    primary：主产物路径（显式 --output 或统一树）；audit：主库审计副本路径
    （主产物已在主库统一树内时为 None，不重复拷贝）。commit() 在落盘后调用。
    """

    def __init__(self, primary: Path, audit: Optional[Path]) -> None:
        self.primary = primary
        self.audit = audit

    def commit(self) -> Path:
        """最终产物落盘后调用：按需复制审计副本到主库统一树，返回主路径。"""
        if self.audit and self.primary.is_file():
            self.audit.parent.mkdir(parents=True, exist_ok=True)
            if self.audit.resolve() != self.primary.resolve():
                shutil.copy2(self.primary, self.audit)
        return self.primary


def plan_output(family: str, skill_name: str, default_name: str,
                explicit: Optional[str] = None,
                cwd: Optional[Path] = None) -> OutputPlan:
    """规划一次产物落点（OUTPUT.md C-1/C-4）。

    - explicit（--output）为文件路径时主产物=该路径；为目录时主产物=该目录/default_name
    - 默认主产物 = <cwd>/skills-output/<family>/<skill_name>/<ts>/default_name
    - 主产物不在主库统一树内时，audit = <master>/skills-output/<family>/<skill_name>/<ts>/
    """
    ts = run_timestamp()
    if explicit:
        p = Path(explicit).expanduser()
        primary = p if p.suffix else p / default_name
        primary.parent.mkdir(parents=True, exist_ok=True)
        audit = audit_dir(family, skill_name, ts) / primary.name
        return OutputPlan(primary, audit)
    primary = run_dir(family, skill_name, ts, cwd) / default_name
    primary.parent.mkdir(parents=True, exist_ok=True)
    audit = None if is_under(primary, MASTER_ROOT / OUTPUT_ROOT_NAME) \
        else audit_dir(family, skill_name, ts) / default_name
    return OutputPlan(primary, audit)


# ─── db-skill 共享实现（mysql_tool.py / pg_tool.py 原先各自复制一份）───────────
#
# 两个引擎的这三段逻辑逐字节相同，只有「候选配置文件名」不同（mysql.json / pg.json），
# 因此把实现收在 common.py，由各引擎模块传入自己的候选列表。
# 原先 27 + 12 + 8 = 47 行 × 2 份重复，现为 1 份实现 + 各 1 个薄包装。


def resolve_config_path(
    explicit: Optional[str],
    candidate_files: Tuple[str, ...],
    env_var: str = DEFAULT_CONFIG_ENV,
) -> Path:
    """解析 db-skill 配置文件路径。

    优先级：显式 --config 参数 → 环境变量（默认 DB_SKILL_CONFIG）→ 从 cwd 向上逐级
    查找 candidate_files 中的相对路径。
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        return path

    env_path = os.environ.get(env_var)
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Config from {env_var} not found: {path}")
        return path

    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        for rel in candidate_files:
            candidate = base / rel
            if candidate.exists():
                return candidate

    checked = ", ".join(candidate_files)
    raise FileNotFoundError(
        "No config file found. Use --config, set %s, or create one of: %s" % (env_var, checked)
    )


def choose_limit(user_limit: Optional[int], config: Dict[str, Any]) -> Tuple[int, int]:
    """把用户传入的 limit 夹到 [1, config.limits.max_limit]，返回 (chosen, max_limit)。"""
    default_limit = int(config["limits"].get("default_limit", 200))
    max_limit = int(config["limits"].get("max_limit", 1000))

    chosen = default_limit if user_limit is None else int(user_limit)
    if chosen < 1:
        chosen = 1
    if chosen > max_limit:
        chosen = max_limit
    return chosen, max_limit


def shutil_which(name: str) -> Optional[str]:
    """在 PATH 中查找可执行文件（不依赖 shutil.which 的版本差异）。"""
    for p in os.environ.get("PATH", "").split(os.pathsep):
        full = Path(p) / name
        if full.exists() and os.access(full, os.X_OK):
            return str(full)
    return None

