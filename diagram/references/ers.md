
> **⚠️ 重大升级 (2026-07-19):** 默认渲染引擎切换为 **ECharts + Playwright**（视觉与 ECharts 参考实现一致：白底黑边、矩形/菱形/椭圆、底部图例、主键加粗下划线）。默认布局算法为 **`deterministic`** —— 由 Graphviz `neato`（Kamada-Kawai 应力最小化）在 Python 端计算无交叉初始坐标，传给 ECharts 用 `layout='none'` 渲染。实测可做到 **0 连线交叉**（详见下方"布局算法"一节）。旧版 Pillow 手动坐标引擎通过 `--engine pillow` 保留作为离线兜底。

# ER 图生成 Skill

## 核心目标

根据用户提供的实体和关系数据，生成 **Chen 风格 ER 图**，专用于本科毕业论文数据库设计章节。

## 渲染引擎

| 引擎 | 说明 | 使用场景 |
|------|------|----------|
| **echarts**（默认） | Playwright + Chromium + ECharts 5.5.0 渲染。默认配合 `deterministic` 布局（Graphviz neato 算坐标）实现 0 连线交叉 | 推荐使用，效果接近 ECharts 桌面应用 |
| **pillow**（兜底） | Pillow 手动坐标 + 矩形/菱形几何绘制 | 无 Chromium 环境或需要精确控制坐标 |

### ECharts 引擎视觉规范（与参考实现 `G:\Kimi_Agent_ECharts ER图定制` 完全一致）

- **实体**：矩形 100×60，白色填充 + 黑色边框（2px），加粗文字
- **关系**：菱形 90×70，白色填充 + 黑色边框（2px）
- **属性**：椭圆 90×50，白色填充 + 黑色边框（1.5px 或 2px 主键）
- **主键**：加粗 + 下划线 + 深色字（`#0f2b4d`）
- **实体↔关系连线**：浅灰实线，曲度 0.1，基数（1/n/m）作边标签，白底圆角 pill
- **属性↔实体连线**：浅灰虚线，无标签
- **图例**：底部居中，包含"实体/属性/关系"三项
- **背景**：纯白
- **布局**：默认 `deterministic`（Graphviz neato 算坐标 + ECharts `layout='none'` 渲染），可选 `force`（力导向）或 `none`（手动坐标）

## 设计风格

- **实体**：矩形，白色背景 + 黑色边框
- **关系**：菱形，白色背景 + 黑色边框
- **连线**：浅灰直线，标注基数（1、n、m）
- **属性**（可选）：椭圆，白色背景 + 黑色边框；主键加粗 + 下划线
- **背景**：白色

## 布局算法（重要）

ECharts 引擎支持 3 种布局算法，通过 `--layout` 选择：

| 算法 | 说明 | 适合场景 | 连线交叉 |
|------|------|---------|---------|
| **`deterministic`**（默认） | Python 端用 Graphviz `neato`（Kamada-Kawai 应力最小化）算无交叉坐标，传给 ECharts `layout='none'` 渲染 | 推荐！大部分 ER 图 | **0~1 处** |
| `force` | 纯 ECharts 力导向自动布局 | 节点非常多（>30）时备选 | 较多 |
| `none` | 完全用 JSON 中的 x/y 坐标 | 手动精确控制 | 取决于坐标 |

**为什么默认 `deterministic`**：实测在典型 ER 图（6 实体 + 5 关系 + 8 属性）上做到 0 交叉、9/10 可读性。`neato` 对"实体-菱形-实体-属性"这种星状拓扑特别擅长，能自然地把关联紧密的实体聚类。

## 工作流程

1. **整理实体与关系**
   - 列出系统中所有实体（对应数据库表）
   - 梳理实体之间的关系及基数
   - 可选：列出实体的属性，标记主键

2. **创建 JSON 文件**
   - 路径：`docs/er/json/<name>.json`
   - 默认 `deterministic` 布局自动计算坐标，**无需手动写 x/y**
   - 若需要精确控制布局，可加 `--layout none` 并在 JSON 中显式提供 x/y

3. **生成 ER 图**
   - 使用 CLI 工具生成 PNG 图片
   - 输出路径：`<项目>/thesis-output/diagram-ers/ers-diagram.png`（cwd 在项目时）
   - 或 `~/.claude/skills-output/diagram-ers/ers-diagram.png`（cwd 在 skills 目录时）

## 目录结构

```
docs/er/
├── json/       # JSON 数据文件

~/.claude/skills-output/diagram-ers/    # 生成的 ER 图 PNG（默认输出目录之一）
```

## JSON 文件规范

### 基本结构（ECharts 引擎，推荐）

```json
{
  "title": "电影院线上订票系统E-R图",
  "entities": [
    {"name": "用户"},
    {"name": "订单"},
    {"name": "管理员"},
    {"name": "电影"},
    {"name": "活动"}
  ],
  "relations": [
    {
      "name": "创建",
      "connects": [
        {"entity": "用户", "cardinality": "1"},
        {"entity": "订单", "cardinality": "n"}
      ]
    },
    {
      "name": "管理",
      "connects": [
        {"entity": "订单", "cardinality": "n"},
        {"entity": "管理员", "cardinality": "1"}
      ]
    }
  ],
  "attributes": [
    {"name": "用户ID", "entity": "用户", "isPrimary": true},
    {"name": "用户名", "entity": "用户"},
    {"name": "订单号", "entity": "订单", "isPrimary": true},
    {"name": "金额", "entity": "订单"}
  ]
}
```

### 字段说明

- `title`: 图标题（可选，仅用于文档说明，不渲染到图上）
- `entities`: 实体列表
  - `name`: 实体名称（对应数据库表名或中文名）
  - `x`, `y`: 可选像素坐标（默认 `deterministic` 布局忽略；加 `--layout none` 时使用）
- `relations`: 关系列表
  - `name`: 关系名称
  - `x`, `y`: 可选坐标（同上）
  - `connects`: 连接的实体及基数
    - `entity`: 实体名称（必须与 `entities` 中的 `name` 一致）
    - `cardinality`: 基数标注，如 `1`、`n`、`m`
- `attributes`: 可选属性列表
  - `name`: 属性名称
  - `entity`: 所属实体名（必须与 `entities` 中的 `name` 一致）
  - `isPrimary`: 布尔，是否主键（默认 `false`）。主键会加粗 + 下划线 + 深色字
  - `x`, `y`: 可选坐标（同上）
- `edges`: 可选，高级用法。直接提供完整的边列表，覆盖自动生成的边

### ECharts 引擎自动布局优势

默认 `deterministic` 布局由 Graphviz neato 自动计算坐标，**无需手动设计坐标**。你只需声明 `entities / relations / attributes`，引擎会自动：
- 让关联紧密的节点聚类
- 最小化连线交叉（实测可做到 0 交叉）
- 给图例和主键标记留出视觉空间

## 坐标设计指南（仅适用于 `--engine pillow` 或 `--layout none` 模式）

> 默认 `deterministic` 布局由 Graphviz neato 自动计算坐标，**不需要手动写坐标**。以下指南仅在以下两种场景使用：
> - 使用 `--engine pillow`（旧版 Pillow 引擎）
> - 使用 `--layout none` 让 ECharts 用 JSON 中的 x/y 坐标固定布局

为避免连线重叠，建议：

1. **先画草图**：在纸上或 draw.io 上大致排布实体位置
2. **使用网格坐标**：
   - 实体之间水平间距建议 **≥ 150**
   - 实体之间垂直间距建议 **≥ 120**
   - 关系菱形放在连线交叉点附近
3. **一对一关系**：关系菱形靠近实体连线的中点
4. **一对多关系**：关系菱形靠近 "1" 的一方
5. **多对多关系**：关系菱形放在两个实体的正中间

### 推荐坐标网格（以论文图4-9为参考）

设计原则：让关联的实体靠近，关系菱形放在两者中间。

```
        [留言]     [影院留言]    [回复]
          |            |           |
          1            n           n
          |            |           |
        [用户]---n[参加]m---[活动]---n[创建]1---[工作人员]
          |                         |
          n                         n
          |                         |
        [订单]---n[管理]1---[管理员]---n[创建]m---[日常工作/任务]
          |                                      |
          n                                      |
          |                                      |
        [关联]1---[排片]---n[新建]1---[电影]
                   |
                   1
                   |
                [影厅]
```

对应坐标示例（水平间距180~240，垂直间距150~200）：

```json
{
  "entities": [
    {"name": "用户", "x": 150, "y": 200},
    {"name": "订单", "x": 150, "y": 450},
    {"name": "管理员", "x": 550, "y": 450},
    {"name": "电影", "x": 800, "y": 650},
    {"name": "排片", "x": 450, "y": 650},
    {"name": "活动", "x": 450, "y": 200},
    {"name": "工作人员", "x": 850, "y": 200}
  ],
  "relations": [
    {"name": "参加", "x": 300, "y": 200, "connects": [
      {"entity": "用户", "cardinality": "n"},
      {"entity": "活动", "cardinality": "m"}
    ]},
    {"name": "创建", "x": 150, "y": 325, "connects": [
      {"entity": "用户", "cardinality": "1"},
      {"entity": "订单", "cardinality": "n"}
    ]},
    {"name": "管理", "x": 350, "y": 450, "connects": [
      {"entity": "订单", "cardinality": "n"},
      {"entity": "管理员", "cardinality": "1"}
    ]},
    {"name": "新建", "x": 625, "y": 650, "connects": [
      {"entity": "排片", "cardinality": "n"},
      {"entity": "电影", "cardinality": "1"}
    ]}
  ]
}
```

**坐标设计技巧**：
1. 水平间距 **≥ 180**（两实体之间），关系菱形放中间
2. 垂直间距 **≥ 150**（上下层实体之间）
3. 关系菱形坐标 = 两个实体坐标的平均值
4. 一对多关系：菱形更靠近 "1" 的一方约 1/3 处
5. 多对多关系：菱形严格放在两个实体正中间

## 样式说明

**ECharts 引擎**（默认）：
- 实体矩形 100×60，加粗字 13px
- 关系菱形 90×70，普通字 12px
- 属性椭圆 90×50，普通字 12px；主键加粗 + 下划线 + 深色字（`#0f2b4d`）
- 连线浅灰（`#8c8c8c`）1.5px 实线，曲度 0.1
- 属性↔实体连线虚线 1.0px
- 基数标签白底圆角 pill，位于连线中段
- 底部图例，含实体/属性/关系三项

**Pillow 引擎**（兜底，`--engine pillow`）：
- 实体矩形 100×50，文字居中
- 关系菱形 80×56，文字居中
- 连线 1px 黑色实线
- 基数标签 10px 字体，白色背景覆盖线条，位于连线 30% 处（靠近实体）

## ER 图生成命令

### 默认（推荐）：ECharts 引擎

```bash
cd ~/projects/dc-skills/diagram-ers
uv run python -m scripts.cli \
  --json-file docs/er/json/<name>.json
```

当输入文件使用**绝对路径**时，CLI 会自动推断项目目录（向上查找包含 `docs/` 或 `thesis-output/` 的目录），默认输出到 `<项目目录>/thesis-output/diagram-ers/ers-diagram.png`。如使用相对路径或无法推断，则回退到 `~/.claude/skills-output/diagram-ers/ers-diagram.png`。如需自定义路径：

```bash
cd ~/projects/dc-skills/diagram-ers
uv run python -m scripts.cli \
  --json-file docs/er/json/<name>.json \
  --out <自定义路径>.png
```

### 高级 ECharts 选项

```bash
# 调整画布尺寸与高清截图
uv run python -m scripts.cli --json-file er.json --width 2000 --height 1400 --pixel-ratio 3.0

# 切换为纯 ECharts 力导向布局（节点很多时备选，可能有连线交叉）
uv run python -m scripts.cli --json-file er.json --layout force

# 使用 JSON 中的坐标固定布局（手动控制）
uv run python -m scripts.cli --json-file er.json --layout none

# 力导向模式下调整参数（仅 --layout force 时生效）
uv run python -m scripts.cli --json-file er.json --layout force --repulsion 800 --edge-min 100 --edge-max 220

# 隐藏底部图例
uv run python -m scripts.cli --json-file er.json --no-legend

# 延长等待让力导向更充分收敛（仅 --layout force 时生效）
uv run python -m scripts.cli --json-file er.json --layout force --settle-ms 4000
```

### 兜底：Pillow 引擎（无需 Chromium）

```bash
cd ~/projects/dc-skills/diagram-ers
uv run python -m scripts.cli \
  --json-file docs/er/json/<name>.json \
  --engine pillow
```

Pillow 引擎不支持属性绘制，仅画实体矩形 + 关系菱形 + 连线；要求 JSON 中包含 x/y 坐标。

### 依赖说明

- **ECharts 引擎**：需要 Chromium。首次运行会自动尝试联网下载 `echarts.min.js` 至 `scripts/vendor/`（一次性 ~1MB，离线后即可永久使用）。Chromium 路径自动从 `~/.cache/ms-playwright/chromium-*/chrome-linux/chrome` 检测，可用环境变量 `DIAGRAMERS_CHROMIUM` 覆盖。
- **Pillow 引擎**：只需要 Pillow（已包含在统一环境中）。

## 示例

### 简单 ER 图（用户-订单-管理员）

```json
{
  "title": "订单管理系统E-R图",
  "entities": [
    {"name": "用户"},
    {"name": "订单"},
    {"name": "管理员"}
  ],
  "relations": [
    {
      "name": "创建",
      "connects": [
        {"entity": "用户", "cardinality": "1"},
        {"entity": "订单", "cardinality": "n"}
      ]
    },
    {
      "name": "管理",
      "connects": [
        {"entity": "订单", "cardinality": "n"},
        {"entity": "管理员", "cardinality": "1"}
      ]
    }
  ],
  "attributes": [
    {"name": "用户ID", "entity": "用户", "isPrimary": true},
    {"name": "用户名", "entity": "用户"},
    {"name": "订单号", "entity": "订单", "isPrimary": true},
    {"name": "金额", "entity": "订单"},
    {"name": "工号", "entity": "管理员", "isPrimary": true}
  ]
}
```

生成命令：

```bash
cd ~/projects/dc-skills/diagram-ers
uv run python -m scripts.cli \
  --json-file docs/er/json/simple.json
```

### 简化格式（直接使用）

```bash
cd ~/projects/dc-skills/diagram-ers
uv run python -m scripts.cli --json-file er.json
```

默认输出路径由 CLI 自动推断（绝对路径输入 → `<项目目录>/thesis-output/diagram-ers/ers-diagram.png`，否则回退到 `~/.claude/skills-output/diagram-ers/ers-diagram.png`）

## 分辨率说明

**ECharts 引擎**：通过 `--pixel-ratio` 控制截图倍率，默认 2.0（高清）。可调到 3.0 获得更高分辨率：

```bash
uv run python -m scripts.cli --json-file er.json --pixel-ratio 3.0
```

**Pillow 引擎**：默认输出高分辨率（4x 渲染，不下采样），与 diagram-usecase 行为一致。如需低分辨率，可加 `--downsample`：

```bash
uv run python -m scripts.cli --json-file er.json --engine pillow --downsample
```

## 环境管理（统一规范）

```bash
cd ~/projects/dc-skills
uv sync
```

- Python 依赖统一在根目录 `pyproject.toml` 管理，不在子目录单独安装
- 各 skill 子目录无需创建 `.venv`，`uv run` 会自动向上查找到根目录的虚拟环境
- 新增 `playwright` 依赖已自动添加到根 `pyproject.toml`

## 自检清单

输出前检查：
- [ ] 所有实体名称在 `entities` 和 `relations.connects` 中一致
- [ ] 每个关系至少连接 2 个实体
- [ ] 基数标注正确（1、n、m）
- [ ] 关系名称简洁（2-4字）
- [ ] 若使用 `--engine pillow` 或 `--layout none`：坐标已调整，连线不重叠
- [ ] 若声明 `attributes`：主键属性已标 `isPrimary: true`
