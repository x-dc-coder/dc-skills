
# Diagram Draft — 终端 ASCII 架构图草稿工具

使用 `graph-easy` (Perl Graph::Easy) 从纯文本描述快速生成 ASCII/Unicode
架构图草稿，支持一键导出 PNG/SVG/HTML 等多种格式。

## 触发规则

当用户提到以下任何场景时触发本 skill：

- "架构草稿" "架构草图" "画个架构" "系统架构" "技术架构"
- "ASCII 架构" "终端架构图" "文本架构图"
- "快速架构" "草稿架构" "简易架构"
- "draw architecture" "ascii diagram" "architecture draft"
- 用户说"先画个草稿看看"或类似表述

**注意**：本 skill 专注于**快速草稿**。如果用户需要正式论文级架构图，
使用 `diagram-flow`（Mermaid，纯黑白）。

## 依赖

```bash
sudo apt install -y libgraph-easy-perl graphviz
```

## 语法速查

### 基本语法

```
[ 节点名 ] --> [ 目标节点 ]          # 实线箭头
[ 节点名 ] ..> [ 目标节点 ]          # 虚线箭头
[ 节点名 ] => [ 目标节点 ]           # 双线箭头
[ 节点名 ] -- 标签 --> [ 目标节点 ]   # 带标签的边
```

### 流向控制

```
graph { flow: south; }   # 上→下（默认）
graph { flow: east; }    # 左→右
graph { flow: west; }    # 右→左
graph { flow: north; }   # 下→上
```

### 节点样式

```
[ 节点 ] { shape: rounded; }     # 圆角矩形
[ 节点 ] { shape: ellipse; }     # 椭圆形
[ 节点 ] { shape: diamond; }     # 菱形
[ 节点 ] { color: red; }         # 红色高亮
[ 节点 ] { color: blue; }        # 蓝色高亮
[ 节点 ] { color: orange; }      # 橙色高亮
[ 节点 ] { color: green; }       # 绿色高亮
```

### 分组/边界

```
(组名:
  [ 节点A ] --> [ 节点B ]
)
```

### 多节点快捷写法

```
[ A ] --> [ B ], [ C ], [ D ]    # A 同时指向 B C D
[ A ], [ B ] --> [ C ]            # A B 同时指向 C
```

## 输出格式

| 命令 | 格式 | 用途 |
|------|------|------|
| `graph-easy input.txt` | ASCII | 终端直接看 |
| `graph-easy input.txt --as=boxart` | Unicode boxart | 终端美化版 |
| `graph-easy input.txt --as=dot \| dot -Tpng -o out.png` | PNG 图片 | 文档/论文 |
| `graph-easy input.txt --as=dot \| dot -Tpdf -o out.pdf` | PDF 矢量 | 论文插图 |
| `graph-easy input.txt --as=svg -o out.svg` | SVG 矢量 | 网页嵌入 |
| `graph-easy input.txt --as=html -o out.html` | HTML | 浏览器查看 |

## 工作流程

### 第一步：写描述文件

用上面的语法写出架构描述，保存为 `.txt` 文件。

### 第二步：终端预览

```bash
graph-easy arch.txt --as=boxart
```

### 第三步（可选）：导出多种格式

使用 `scripts/render.sh` 一键导出所有格式：

```bash
bash ~/projects/dc-skills/diagram-draft/scripts/render.sh arch.txt ./output/
```

## 示例模板

`examples/` 目录下有常见架构模板：
- `microservice.txt` — 微服务架构
- `pipeline.txt` — CI/CD 流水线
- `layered.txt` — 多层架构
- `event-driven.txt` — 事件驱动架构
- `network.txt` — 网络拓扑

可以直接复制修改：

```bash
cp ~/projects/dc-skills/diagram-draft/examples/microservice.txt my-arch.txt
# 编辑 my-arch.txt，然后
graph-easy my-arch.txt --as=boxart
```

## 限制与建议

- 3-5 个节点的简单图 ASCII 效果最佳
- 复杂图（10+ 节点）建议直接出 PNG
- 不支持 `cylinder`（数据库图标）、`cloud` 等特殊形状
- 中文节点标签在 graph-easy 0.76 中宽度计算可能有偏差
- 自动布局无法手动调整节点位置，如需精确控制用 `diagram-flow`（Mermaid）或拖拽工具（draw.io）
