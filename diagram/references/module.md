
> **⚠️ BREAKING CHANGE (2026-07-19):** `--downsample` 参数语义已统一。默认输出**高分辨率**（4x 渲染，不下采样），与 diagram-usecase 行为一致。原默认 1x 低分辨率输出已被移除。如需低分辨率，请显式添加 `--downsample`。回滚方案：如用户抱怨输出过大，可在 CLI 中追加 `--downsample` 恢复旧行为。

# 功能模块图生成 Skill

## 工作流程

生成功能模块图的标准流程：

1. **获取模块结构**
   - 基于用户提供的系统功能模块信息
   - 整理为树形层次结构

2. **创建 JSON 文件**
   - 路径：`docs/module/json/<module_name>.json`
   - 使用树形结构描述功能模块层次

3. **生成模块图**
   - 使用本项目的 CLI 工具生成 PNG 图片
   - 输出路径：`<cwd>/skills-output/drawing/diagram-module/<时间戳>/module-diagram.png`（默认，`--output` 可自定义）

## 目录结构

```
docs/module/
├── json/       # JSON 数据文件

<cwd>/skills-output/drawing/diagram-module/<时间戳>/    # 生成的模块图 PNG（默认输出目录）
```

## JSON 文件规范

### 基本结构
```json
{
  "tree": {
    "name": "系统名称",
    "children": [
      {
        "name": "模块A",
        "children": [
          {"name": "子模块A1"},
          {"name": "子模块A2"}
        ]
      },
      {"name": "模块B"}
    ]
  }
}
```

### 字段说明
- `name`: 模块名称（简洁明了）
- `children`: 子模块数组（可选）
- 支持多级嵌套，建议不超过 3 层

## 样式说明

- **根节点（第一层）**：水平文字
- **子节点（第二层及以下）**：竖直文字（从上到下排列）
- **学术紧凑风格**：纯黑色边框，白色背景

## 模块图生成命令

```bash
cd ~/projects/dc-skills/diagram-module
uv run python -m scripts.cli \
  --json-file docs/module/json/<module_name>.json
```

产物默认落**统一产物树**（docs/specs/OUTPUT.md C-1）：`<cwd>/skills-output/drawing/diagram-module/<时间戳>/module-diagram.png`——`<cwd>` 即运行时的当前目录，无项目推断、无兜底分叉。如需自定义路径：

```bash
cd ~/projects/dc-skills/diagram-module
uv run python -m scripts.cli \
  --json-file docs/module/json/<module_name>.json \
  --out <自定义路径>.png
```

## 示例

### 电商系统功能模块
```json
{
  "tree": {
    "name": "电商系统",
    "children": [
      {
        "name": "用户管理",
        "children": [
          {"name": "用户注册"},
          {"name": "用户登录"},
          {"name": "个人信息"}
        ]
      },
      {
        "name": "商品管理",
        "children": [
          {"name": "商品浏览"},
          {"name": "商品搜索"},
          {"name": "商品详情"}
        ]
      },
      {
        "name": "订单管理",
        "children": [
          {"name": "购物车"},
          {"name": "订单创建"},
          {"name": "订单查询"}
        ]
      }
    ]
  }
}
```

生成命令：
```bash
cd ~/projects/dc-skills/diagram-module
uv run python -m scripts.cli \
  --json-file docs/module/json/ecommerce.json
```

## 简化格式（直接使用）

也可以直接使用简化格式，省略 `--out` 参数：

```bash
cd ~/projects/dc-skills/diagram-module
uv run python -m scripts.cli --json-file module.json
```

默认输出路径：`<cwd>/skills-output/drawing/diagram-module/<时间戳>/module-diagram.png`（统一产物树，docs/specs/OUTPUT.md C-1）

## 分辨率说明

**默认输出高分辨率图片**（推荐用于论文），渲染后**不下采样**。如需标准分辨率输出，可添加 `--downsample` 参数：

```bash
uv run python -m scripts.cli --json-file module.json --downsample
```

## 依赖安装

```bash
cd ~/projects/dc-skills
uv sync
```
