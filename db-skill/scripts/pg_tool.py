#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# psycopg2 is imported lazily in connect_pg to keep import-time dependencies minimal.

# ── 共享实现（顶层 scripts/common.py）─────────────────────────────────────────
# resolve_config_path / choose_limit / shutil_which 原为 mysql_tool.py 与 pg_tool.py
# 各存一份逐字节相同的副本；现统一收在 scripts/common.py，本模块只保留
# 「候选配置文件名」这一处引擎差异。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import choose_limit, shutil_which  # noqa: E402
from common import fallback_output_dir  # noqa: E402
from common import resolve_config_path as _resolve_config_path  # noqa: E402


CANDIDATE_CONFIG_FILES = (
    ".db-skill/pg.json",
    ".db-skill.json",
    "config/pg.json",
)


def resolve_config_path(explicit: Optional[str]) -> Path:
    """PostgreSQL 专用包装：传入本引擎的候选配置文件名。"""
    return _resolve_config_path(explicit, CANDIDATE_CONFIG_FILES)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded PostgreSQL operations with JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a SQL statement")
    run.add_argument("--sql", help="SQL text to run")
    run.add_argument("--sql-file", help="Read SQL from file")
    run.add_argument("--config", help="Path to config JSON")
    run.add_argument("--limit", type=int, default=None, help="Row limit for SELECT-like queries")
    run.add_argument("--jq", default=".data[0:20]", help="jq filter for preview")
    run.add_argument("--no-preview", action="store_true", help="Skip jq preview output")
    run.add_argument("--output", help="Write results to this JSON file instead of temp file")
    run.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required for non-read SQL (INSERT/UPDATE/DELETE/DDL).",
    )
    return parser.parse_args()



def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "pg" not in cfg or not isinstance(cfg["pg"], dict):
        raise ValueError("Config must include a 'pg' object")

    limits = cfg.get("limits", {})
    if not isinstance(limits, dict):
        raise ValueError("'limits' must be an object when provided")

    cfg.setdefault("limits", {})
    cfg["limits"].setdefault("default_limit", 200)
    cfg["limits"].setdefault("max_limit", 1000)
    return cfg


def read_sql(sql: Optional[str], sql_file: Optional[str]) -> str:
    if bool(sql) == bool(sql_file):
        raise ValueError("Provide exactly one of --sql or --sql-file")

    if sql:
        text = sql
    else:
        with open(sql_file, "r", encoding="utf-8") as f:
            text = f.read()

    text = text.strip().rstrip(";").strip()
    if not text:
        raise ValueError("SQL is empty")
    return text


def is_read_query(sql: str) -> bool:
    head = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL).lstrip()
    head = re.sub(r"^(--.*\n)+", "", head)
    return bool(re.match(r"(?is)^(select|with)\b", head))


def bounded_select_sql(sql: str, limit: int) -> str:
    return f"SELECT * FROM ({sql}) AS __db_skill_q LIMIT {int(limit)}"



def connect_pg(pg_cfg: Dict[str, Any]):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    dsn_params = {
        "host": pg_cfg.get("host", "127.0.0.1"),
        "port": int(pg_cfg.get("port", 5432)),
        "user": pg_cfg["user"],
        "password": pg_cfg.get("password", ""),
        "dbname": pg_cfg.get("database") or pg_cfg.get("dbname"),
    }
    connect_timeout = int(pg_cfg.get("connect_timeout", 5))

    try:
        conn = psycopg2.connect(
            **{k: v for k, v in dsn_params.items() if v is not None},
            connect_timeout=connect_timeout,
        )
        conn.autocommit = False
        return conn, "psycopg2"
    except Exception as e:
        raise RuntimeError("Cannot connect to PostgreSQL. Ensure psycopg2-binary is installed.") from e


def ensure_jsonable_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    return v


def ensure_jsonable_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ensure_jsonable_value(v) for k, v in row.items()}


def make_output_path(explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # Two-level fallback（docs/specs/OUTPUT.md C-1）：基准是主库根而非农场目录——
    # 经 ~/.claude/skills 软链进入时 resolve() 回到主库，同样走兜底。
    root = fallback_output_dir("db-skill", "db-output")
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="pg-result-", suffix=".json", dir=root)
    os.close(fd)
    return Path(name)


def jq_preview(path: Path, jq_filter: str) -> Tuple[bool, str]:
    jq_bin = shutil_which("jq")
    if not jq_bin:
        return False, "jq not found; skip preview"

    cmd = [jq_bin, "-c", jq_filter, str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return False, p.stderr.strip() or "jq preview failed"

    preview = p.stdout.strip()
    if len(preview) > 6000:
        preview = preview[:6000] + "\n...<truncated>"
    return True, preview



def run_sql(args: argparse.Namespace) -> int:
    sql = read_sql(args.sql, args.sql_file)
    cfg_path = resolve_config_path(args.config)
    cfg = load_config(cfg_path)
    limit, max_limit = choose_limit(args.limit, cfg)

    read_mode = is_read_query(sql)
    final_sql = bounded_select_sql(sql, limit) if read_mode else sql
    if not read_mode and not args.confirm_write:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Non-read SQL requires explicit --confirm-write",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    conn, driver = connect_pg(cfg["pg"])
    output_path = make_output_path(args.output) if read_mode else None

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(final_sql)

        if read_mode:
            rows = cur.fetchall()
            normalized = [ensure_jsonable_row(dict(r)) for r in rows]
            payload = {
                "meta": {
                    "config": str(cfg_path),
                    "driver": driver,
                    "read_mode": True,
                    "limit_applied": limit,
                    "max_limit": max_limit,
                    "rows": len(normalized),
                },
                "data": normalized,
            }
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

            result = {
                "ok": True,
                "type": "query",
                "rows": len(normalized),
                "limit_applied": limit,
                "output_json": str(output_path),
            }

            if not args.no_preview:
                ok, preview = jq_preview(output_path, args.jq)
                result["preview_with_jq"] = ok
                result["preview"] = preview

            print(json.dumps(result, ensure_ascii=False))
        else:
            affected = cur.rowcount
            conn.commit()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "type": "exec",
                        "affected_rows": affected,
                        "config": str(cfg_path),
                        "driver": driver,
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return 0


def main() -> int:
    args = parse_args()
    if args.command == "run":
        return run_sql(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
