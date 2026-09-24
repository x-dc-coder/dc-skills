
> **ℹ️ 统一行为 (2026-07-19):** `--downsample` 参数语义已跨 4 个 diagram-* skill 统一（er, ers, module, usecase）。所有 skill 默认输出**高分辨率**，`--downsample` 为 opt-in 降至 1x。diagram-usecase 无变更（原本即正确实现）。

# 用例图生成 Skill

## 工作流程

生成用例图的标准流程：

1. **获取用例信息**
   - 确定系统的参与者（Actor）
   - 收集该参与者的所有用例（UseCase）

2. **创建 JSON 文件**
   - 路径：`docs/usecase/json/<usecase_name>.json`
   - 使用简洁格式（单参与者）

3. **生成用例图**
   - 使用本项目的 CLI 工具生成 PNG 图片
   - 输出路径：`<cwd>/skills-output/drawing/diagram-usecase/<时间戳>/usecase-diagram.png`（默认，`--output` 可自定义）

## 目录结构

```
docs/usecase/
├── json/       # JSON 数据文件

<cwd>/skills-output/drawing/diagram-usecase/<时间戳>/    # 生成的用例图 PNG（默认输出目录）
```

## JSON 文件规范

### 简洁格式（推荐，单参与者）
```json
{
  "actor": "用户",
  "usecases": ["登录系统", "重置密码", "商品下单", "退出系统"]
}
```

### 字段说明
- `actor`: 参与者名称（单数，如"用户"、"管理员"）
- `usecases`: 用例名称数组，**限制 3-6 个用例**（不得低于 3 个，不得超过 6 个）

## 样式说明

- **参与者（Actor）**：火柴人图标 + 下方名称
- **用例（UseCase）**：水平椭圆 + 内部居中文字
- **关联**：带箭头的直线（从参与者指向用例）
- **学术风格**：黑白配色，清晰简洁

## 学术规范

本科论文中，**一个用例图只展示一个参与者**（Actor）及其相关用例：
- 保持图表简洁清晰
- 每图聚焦一个用户角色
- 多个角色分多个图展示
- **用例数量限制**：每个参与者的用例数量应控制在 **3-6 个** 之间，不得少于 3 个，也不宜超过 6 个

## 输出质量

- 默认输出**高分辨率** PNG（8x 渲染，不下采样），嵌入 150 DPI 元数据
- Word 导入后自动缩放至页面宽度，等效 ~400 DPI，确保打印清晰
- 如需低分辨率输出（1x 下采样），可加 `--downsample` 参数

## 用例图生成命令

```bash
cd ~/projects/dc-skills/diagram-usecase
uv run python -m scripts.cli \
  --json-file docs/usecase/json/<usecase_name>.json
```

产物默认落**统一产物树**（docs/specs/OUTPUT.md C-1）：`<cwd>/skills-output/drawing/diagram-usecase/<时间戳>/usecase-diagram.png`——`<cwd>` 即运行时的当前目录，无项目推断、无兜底分叉。如需自定义路径：

```bash
cd ~/projects/dc-skills/diagram-usecase
uv run python -m scripts.cli \
  --json-file docs/usecase/json/<usecase_name>.json \
  --out <自定义路径>.png
```

## 示例

### 用户用例
```json
{
  "actor": "用户",
  "usecases": ["登录系统", "重置密码", "商品下单", "退出系统"]
}
```

生成命令：
```bash
cd ~/projects/dc-skills/diagram-usecase
uv run python -m scripts.cli \
  --json-file docs/usecase/json/user.json
```

### 管理员用例（另一个图）
```json
{
  "actor": "管理员",
  "usecases": ["用户管理", "订单审核", "数据统计", "系统配置"]
}
```

生成命令：
```bash
cd ~/projects/dc-skills/diagram-usecase
uv run python -m scripts.cli \
  --json-file docs/usecase/json/admin.json
```

## 简化格式（直接使用）

也可以直接使用简化格式，省略 `--out` 参数：

```bash
cd ~/projects/dc-skills/diagram-usecase
uv run python -m scripts.cli --json-file usecase.json
```

默认输出路径：`<cwd>/skills-output/drawing/diagram-usecase/<时间戳>/usecase-diagram.png`（统一产物树，docs/specs/OUTPUT.md C-1）

## 多参与者格式（向后兼容）

如需多参与者，使用完整格式：

```json
{
  "actors": ["用户", "管理员"],
  "usecases": ["登录系统", "重置密码", "商品下单", "退出系统"],
  "relations": [
    ["用户", "登录系统"],
    ["用户", "重置密码"],
    ["管理员", "商品下单"]
  ]
}
```

## 依赖安装

```bash
cd ~/projects/dc-skills
uv sync
```
