---
name: drawio-xml
description: >
  生成高质量 draw.io 原生 .drawio 图表文件（XML）：SKILL 质量规则 + 官方 MCP server 协同，支持 ER/类图/时序/用例/流程图、泳道图、架构图，交付可继续编辑的文件，也支持截图复刻为可编辑图表。当用户需要 .drawio 文件、可编辑图表或图片转 drawio 时使用；与 diagram-*（Mermaid 渲染 PNG）互补。
metadata:
  family: drawing
  role: member
  load-mode: manual
disable-model-invocation: true
---
# draw.io 图表生成 Skill（SKILL + MCP 协同）

## 核心目标

根据用户需求生成**高质量、可编辑**的 draw.io 原生 `.drawio` 文件，通过 **SKILL（质量知识层）+ MCP（执行层）协同**，让最终图表尽可能逼近用户需求。

分工：
- **SKILL 负责**：质量规则（7 条边路由、布局约束、学术样式）、图表类型知识、流程编排、交付前布局质检（`check-quality.py`）
- **MCP 负责**：会话状态、浏览器实时预览、**编辑门控**（防覆盖用户手工修改）、**结构校验**（XML 合法性）、多页管理、导出多格式
- **协同关键**：MCP 每次写入都做结构校验；SKILL 在关键节点跑布局质检；两者互补形成双层质量保障

## 协同工作流（默认流程）

```
规划 → 建会话 → 初稿 → 迭代精修（协同闭环） → 质检 → 导出交付
```

### 第 1 步：规划（SKILL）
生成前必做，2-3 句：确定流向（上下/左右）、分区（列/行）、节点间距、图表类型样式（查"图表类型速查"）。复杂图先骨架后细节，分步生成。

### 第 2 步：建立会话（MCP）
调用 `start_session` 打开浏览器实时预览（**必须先调用**，否则报 "No active session"）。通知用户预览已打开，可随时手动拖拽调整。

### 第 3 步：初稿（MCP 写 + 结构校验）
- 新图：`create_new_diagram`（xml 参数，完整 mxfile 或裸 mxGraphModel）
- 改已有文件：`load_diagram`（filePath，自动解压）
- MCP 内置 `validateAndFixXml` 做结构校验（重复 id/标签配对/转义），失败会拒绝并提示修复

### 第 4 步：迭代精修（协同闭环核心）
多轮循环，直到逼近用户需求：
1. `get_diagram` 获取**当前最新状态**（含用户在浏览器/桌面端的手工修改）
2. 对比用户改动，用 `edit_diagram` 按 id 精修（update/add/delete 操作）
3. **编辑门控**：若用户在我上次读取后改过图，MCP 会拒绝基于过期状态的编辑并提示重新 `get_diagram` —— 这是防数据丢失的关键，**不要绕过**，重新读取后再编辑
4. 结构性大改（重排布局）时用 `create_new_diagram` 重写

用户反馈循环：用户看完预览提出修改意见 → 回到步骤 4。每轮修改前先 `get_diagram` 感知现状，再编辑。

### 第 5 步：质检（SKILL 布局质检 + MCP 结构校验）
```bash
python3 scripts/check-quality.py <输出文件.drawio>
# 大图/自定义视口：--viewport <宽> <高>
# UI 弹窗等设计性覆盖：--allow-overlap <idA,idB>（可多次）
```
检查：节点重叠、越界（默认 850x1100 视口）、孤立边、重复 id、XML 注释。**有问题必须修复后重新质检，通过才算交付**。MCP 结构校验在写入时已兜底，此步补布局层面的检查。

注意：质检脚本已处理容器内子元素的相对坐标（沿 parent 链转绝对坐标）与祖先-后代包含关系；UI 复刻场景中弹窗（modal）覆盖主内容区属于设计意图，用 `--allow-overlap` 显式豁免并在交付说明中注明。

### 第 6 步：导出交付（MCP）
- `export_diagram` 导出：参数名是 **`path`**（不是 filePath），支持 `.drawio`（XML）/`.png`/`.svg`，格式由扩展名自动识别
- 多页场景：`list_pages` 发现页 → `add_page`/`rename_page`/`delete_page` 管理；页选择器（page_id/page_name/page_index）可用于 edit/get/export
- 交付 .drawio 文件路径 + 图表统计（节点/边数）+ 打开方式提示

## 环境降级（无 MCP 时）

环境无 MCP server 时，跳过第 2/3/6 步的 MCP 部分，直接：
1. 按质量规则编写完整 `<mxfile>` 文档（需自含包装 `<mxfile><diagram><mxGraphModel><root>` + 哨兵 id="0"/"1"）
2. 跑 `check-quality.py` 质检
3. 交付文件

此时无编辑门控/状态感知/实时预览，**务必交付后提示用户**：后续若在桌面端手工修改过图，再次让 AI 修改前应重新提供最新文件，避免覆盖。

## 图片复刻（vision + SKILL + MCP 三方协同）

用户提供图片（架构图截图/手绘草图/系统截图），要求转成可编辑 draw.io 图表时使用。**MCP 无图片输入工具**，此能力靠视觉工具分析 + 本 skill 规则生成 + MCP 渲染，三方协同复刻主应用"上传图片生成图表"功能。

流程：
1. **视觉分析**（vision-workflow skill 的 `describe_image` 工具，路径用绝对路径）：
   - question 必须要求结构化输出：节点/形状类型、每个节点的文字内容、相对位置与大致尺寸、箭头/连线方向与样式（直线/曲线/正交）、容器/分组关系、配色
   - 若图片是论文插图类（架构图/流程图），按 vision-workflow 学术模式严格审查提示词补充描述要求
2. **转 XML**（本 skill 规则）：
   - 按"图表类型速查"选型（流程图/架构图/ER 等），按质量规则生成 XML
   - 复刻要点（主应用 prompt 原话）：**尽量匹配原图样式与布局——连线是直线还是曲线、形状是圆角还是方角、容器分组关系、文字内容逐一对应**
   - 手绘/模糊区域：合理推断结构，宁简勿乱；无法辨认的文字标注 [推测] 并在交付说明中列出
3. **MCP 渲染**：`start_session` → `create_new_diagram`（结构校验）→ 浏览器预览
4. **质检交付**：`check-quality.py` 通过后 `export_diagram` 导出；交付时附"与原图差异说明"
5. **视觉复核（可选，高质量要求时）**：导出 PNG 后用 vision `analyze_screenshot`/`describe_image` 复核关键节点是否齐全、文字是否完整

注意：vision 工具返回 `mock: true` 时结果不可信，必须注明并请用户确认；`describe_image` 的 meta.model 需在交付中标注。


## 论文级复杂图模式（架构图 / 模型结构图 / 机制传导图）

> 适用：用户要求"论文插图 / 架构图 / 模型结构图 / 机制传导图 / 复杂系统图"时启用。
> 完整模板见 `references/paper-diagram-prompts.md`（六段式 + 三类专项 + 精调速查表）。

### 流程（在默认协同工作流基础上）
1. **规划分层**：先画骨架（顶层容器/分区/主流向）再填细节；确定流向（左右/上下/混合）与分区（列/行）
2. **容器化**：重复块/模块边界用透明容器（`fillColor=none`）+ 虚线框 + "×N" 标注；层/泳道用 swimlane
3. **复杂连线**：跨模块/跨层连线必走外围 waypoint（L/U 形），禁穿任何中间形状；同一对节点多边错开 exitY
4. **生成 → 渲染 → 视觉质检闭环**（论文级必做）：
   - create_new_diagram 后导出 PNG（`DRAWIO_EXPORT_SCALE=4` 起，300DPI）
   - 用 vision 工具核验渲染图：重叠 / 穿线 / 文字完整 / 元素齐全 / 布局（对应主应用 VLM 校验）
   - 发现问题 → `get_diagram` → `edit_diagram` 按 id 精修 → 重新导出复检（最多 3 轮）
5. **交付**：.drawio（可编辑）+ PNG（300DPI 论文引用）+ SVG（矢量备用）+ 质检报告

### 复杂度分级
| 级别 | 节点规模 | 策略 |
|---|---|---|
| 简单（流程图/时序） | < 15 | 单页直接生成 |
| 中等（架构图/ER/类图） | 15-30 | 分区+容器，注意边路由 |
| 复杂（模型结构/机制传导） | 30-60 | 骨架先行，分步生成，容器嵌套，多轮质检 |
| 超大（完整系统/多页） | > 60 | 拆多页（add_page），每页一个主题 |

### 视口一致性（重要）
- 论文复杂图质检统一用 **850×1100**（check-quality.py 默认视口）
- 推荐元素范围 x∈[40,800]、y∈[40,980]；容器最大 700×550
- 超宽/超高图：质检时用 `--viewport W H` 匹配实际画布，避免误报
- 生成前在模板中写死视口，生成后质检同参数，两层保持一致

### 环境注意（无 DISPLAY / 无浏览器）
- `BROWSER=echo` 启动 server；用 Playwright/Chromium 打开预览 URL 充当渲染器
- 浏览器必须保持打开，否则 export 超时（详见 references/mcp-usage.md 7.1）

## 图表类型速查（draw.io 内建样式，无需形状库）


| 类型 | 关键样式 | 要点 |
|---|---|---|
| ER 图 | `swimlane`（实体表）+ `text`（字段）+ `endArrow=ERone/ERmany` | 表头 26px；1:N 用 ERone-ERmany |
| UML 类图 | `swimlane` 三段式 | 类名/属性/方法分区 |
| 时序图 | lifeline 竖线 + 消息水平边 | 参与者顶部矩形 + 激活条 |
| 用例图 | `ellipse`（用例）+ `shape=umlActor`（参与者） | 参与者 draw.io 内建 |
| 流程图 | `rounded=1`（处理）/`rhombus`（判断）/`ellipse`（起止） | 判断分支标注是/否 |
| 泳道图 | `swimlane`（泳道）+ 节点 parent 指向泳道 | 泳道横向排列 |

云架构图（AWS/Azure/GCP/K8s）需要图标库语法，查 `references/drawio-quality-rules.md` 第六节，禁止猜测图标语法。

## 必须遵守的硬规则（摘要，完整版见 references/drawio-quality-rules.md）

1. 完整包装：`<mxfile><diagram><mxGraphModel><root>` + 哨兵 `id="0"`/`id="1"`；mxCell 全部平级
2. id 唯一从 "2" 起；顶层 `parent="1"`；**禁止 XML 注释**
3. 布局：x ∈ [40,760]、y ∈ [40,560] 单页紧凑；间距 150-200px
4. 边路由 7 规则：显式 exitX/exitY/entryX/entryY；多边不共路；双向对侧进出；绕障加 waypoint；禁角落连接
5. 容器必须 `fillColor=none` 透明
6. 特殊字符转义：`&lt;` `&gt;` `&amp;` `&quot;`

## MCP 工具速查（完整手册见 references/mcp-usage.md）

| 工具 | 用途 | 要点 |
|---|---|---|
| `start_session` | 开浏览器预览 | 必须先调用 |
| `create_new_diagram` | 新图（替换全部） | xml 参数 |
| `load_diagram` | 加载已有 .drawio | filePath，自动解压 |
| `get_diagram` | 取最新状态（含用户改动） | 编辑前必调 |
| `edit_diagram` | 按 id 精修 | operations；编辑门控会拒过期编辑 |
| `export_diagram` | 导出 | **path 参数**；.drawio/.png/.svg |
| `list_pages`/`add_page`/`rename_page`/`delete_page` | 多页管理 | 拒绝删最后一页 |

## 交付格式

- 文件保存为 `<项目>/<名称>.drawio`（UTF-8）
- 回复中给出：文件路径、图表统计（节点/边数）、类型说明、打开方式提示
- 用户用 Windows 打开 WSL 文件：`\\wsl.localhost\<发行版>\<路径>` 或先拷贝到 Windows 侧
