---
name: db-skill
description: >
  项目内 MySQL / PostgreSQL 的查询与写入。当用户要跑 CRUD、从项目配置读取连接、
  限制返回行数、把结果落成 JSON 时使用。NewAPI 网关读 newapi-management
  （入口是 orca-cli）。WSL 调用 Windows、GPU、注册表读 wsl-windows-bridge。
metadata:
  family: db
  role: entry
  load-mode: auto
---
# DB Skill

## 别的入口

本族没有成员。下面两件事有自己的技能，不要用数据库脚本去处理：

| 请求 | 去读 |
|---|---|
| NewAPI 渠道、会话日志 | `~/projects/dc-skills/newapi-management/SKILL.md` |
| WSL 调用 Windows、GPU、注册表、COM | `~/projects/dc-skills/wsl-windows-bridge/SKILL.md` |


## Supported Databases

| Database | Script | Driver |
|----------|--------|--------|
| MySQL | `scripts/mysql_tool.py` | mysqlclient / pymysql / mysql-connector-python |
| PostgreSQL | `scripts/pg_tool.py` | psycopg2-binary |

## Quick Workflow

1. Resolve config path in this order:
   - Use `--config <path>` when explicitly provided.
   - Else use env `DB_SKILL_CONFIG`.
   - Else auto-discover from current directory upward:
     - `.db-skill/mysql.json` or `.db-skill/pg.json`
     - `.db-skill.json`
     - `config/mysql.json` or `config/pg.json`

2. Run SQL through the Python runner:
   - **MySQL**: `uv run python scripts/mysql_tool.py run --sql "SELECT * FROM users"`
   - **PostgreSQL**: `uv run python scripts/pg_tool.py run --sql "SELECT * FROM users"`

3. Keep query output bounded:
   - SELECT/CTE queries are wrapped and limited automatically.
   - Results are always written to a temp JSON file.
   - Terminal output returns a compact summary and optional jq preview.

## MySQL Commands

- Query with default row limit:
```bash
uv run python scripts/mysql_tool.py run --sql "SELECT * FROM orders"
```

- Query with custom limit and jq filter:
```bash
uv run python scripts/mysql_tool.py run \
  --sql "SELECT * FROM orders WHERE status='paid' ORDER BY id DESC" \
  --limit 200 \
  --jq '.[].id'
```

- Execute DML (insert/update/delete):
```bash
uv run python scripts/mysql_tool.py run --sql "DELETE FROM sessions WHERE expired=1" --confirm-write
```

- Read SQL from file:
```bash
uv run python scripts/mysql_tool.py run --sql-file ./sql/report.sql --jq '.[0:10]'
```

## PostgreSQL Commands

- Query with default row limit:
```bash
uv run python scripts/pg_tool.py run --sql "SELECT * FROM orders"
```

- Query with custom limit and jq filter:
```bash
uv run python scripts/pg_tool.py run \
  --sql "SELECT * FROM orders WHERE status='paid' ORDER BY id DESC" \
  --limit 200 \
  --jq '.[].id'
```

- Execute DML (insert/update/delete):
```bash
uv run python scripts/pg_tool.py run --sql "DELETE FROM sessions WHERE expired=1" --confirm-write
```

- Read SQL from file:
```bash
uv run python scripts/pg_tool.py run --sql-file ./sql/report.sql --jq '.[0:10]'
```

## Behavior Rules

- Treat SELECT and WITH queries as read operations.
- Require user second confirmation before any non-read SQL.
- Require `--confirm-write` for any non-read SQL; reject execution when missing.
- Enforce a hard cap for read rows (`max_limit`) from config, default 1000.
- Store full read results in `<cwd>/skills-output/db/db-skill/<时间戳>/mysql-result.json`（或 `pg-result.json`；固定名，`--output` 优先）——docs/specs/OUTPUT.md C-1/C-9。
- Print only compact metadata + jq preview to control context size.
- Commit only write operations.

## Resources

- MySQL Script: `scripts/mysql_tool.py`
- PostgreSQL Script: `scripts/pg_tool.py`
- MySQL Config reference: `references/mysql-config.md`
- PostgreSQL Config reference: `references/pg-config.md`
