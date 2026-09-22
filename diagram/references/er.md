
> **⚠️ BREAKING CHANGE (2026-07-19):** `--downsample` 参数语义已统一。默认输出**高分辨率**（4x 渲染，不下采样），与 diagram-usecase 行为一致。原默认 1x 低分辨率输出已被移除。如需低分辨率，请显式添加 `--downsample`。回滚方案：如用户抱怨输出过大，可在 CLI 中追加 `--downsample` 恢复旧行为。

# ER 图生成 Skill

## 工作流程

生成单表 ER 图的标准流程：

1. **获取表结构**
   - 优先使用 MCP 工具（如数据库连接工具）读取准确的表结构
   - 如无 MCP，可基于用户提供的表结构信息或 SQL DDL

2. **创建 SQL 文件**
   - 路径：`docs/er/sql/<table_name>.sql`
   - 为每个字段添加简短的注释（COMMENT），说明字段用途
   - 使用标准 MySQL DDL 语法

3. **生成 ER 图**
   - 使用本项目的 CLI 工具生成 PNG 图片
   - 输出路径：`~/.claude/skills-output/diagram-er/er-diagram.png`（默认，可自定义）

## 目录结构

```
docs/er/
├── sql/       # SQL DDL 文件

~/.claude/skills-output/diagram-er/   # 生成的 ER 图 PNG（默认输出目录）
```

## SQL 文件规范

### 基本结构
```sql
CREATE TABLE users (
    id INT PRIMARY KEY COMMENT '用户ID',
    username VARCHAR(50) NOT NULL COMMENT '用户名',
    email VARCHAR(100) COMMENT '邮箱地址',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
);
```

### 字段注释原则
- 简短清晰（2-10 个字符为佳）
- 说明字段的业务含义

### 支持的语法
- `CREATE TABLE` 语句
- 列级约束：`PRIMARY KEY` / `UNIQUE` / `NOT NULL` / `DEFAULT`
- 列注释：`COMMENT '...'`
- 表级约束：`PRIMARY KEY (...)`

## ER 图生成命令

```bash
cd ~/projects/dc-skills/diagram-er
uv run python -m scripts.cli \
  --sql-file docs/er/sql/<table_name>.sql
```

当输入文件使用**绝对路径**时，CLI 会自动推断项目目录（向上查找包含 `docs/` 或 `thesis-output/` 的目录），默认输出到 `<项目目录>/thesis-output/diagram-er/er-diagram.png`。如使用相对路径或无法推断，则回退到 `~/.claude/skills-output/diagram-er/er-diagram.png`。如需自定义路径：

```bash
cd ~/projects/dc-skills/diagram-er
uv run python -m scripts.cli \
  --sql-file docs/er/sql/<table_name>.sql \
  --out <自定义路径>.png
```

## 主键显示规则

- 列级主键：`id INT PRIMARY KEY` —— 属性名自动加下划线
- 表级主键：`PRIMARY KEY (id)` —— 同样会加下划线
- 有 `COMMENT` 的字段优先显示注释内容

## 示例

### 用户表
```sql
-- docs/er/sql/users.sql
CREATE TABLE users (
    id INT PRIMARY KEY COMMENT '用户ID',
    name VARCHAR(50) COMMENT '姓名',
    email VARCHAR(100) COMMENT '邮箱',
    created_at TIMESTAMP COMMENT '创建时间'
);
```

生成命令：
```bash
cd ~/projects/dc-skills/diagram-er
uv run python -m scripts.cli \
  --sql-file docs/er/sql/users.sql
```

## 分辨率说明

**默认输出高分辨率图片**（推荐用于论文），渲染后**不下采样**。如需标准分辨率输出，可添加 `--downsample` 参数：

```bash
uv run python -m scripts.cli --sql-file users.sql --downsample
```

## 依赖安装

```bash
cd ~/projects/dc-skills
uv sync
```
