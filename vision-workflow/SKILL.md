---
name: vision-workflow
description: 图像理解、网页截图核验、OCR 文字提取、科研绘图验证、剪贴板/会话附件识图与多图/批量图片分析的编排流程。当任务涉及看图、识图、图片内容理解、截图验证、图表解读、批量处理图片、识别剪贴板图片或识别 DSH 会话中发送的图片时使用本技能。
metadata:
  family: info
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Vision Workflow（视觉任务编排）

本技能编排 Vision MCP 工具（`vision_health` / `describe_image` / `clipboard_image` / `describe_images` / `analyze_screenshot` / `extract_text` / `verify_figure` / `batch_analyze`）。当模型本身无视觉能力时，通过调用这些工具完成视觉任务；当 new-api Bamboo 中继桥已开启图片自动识别时，日常发图由网关层完成，本技能工具聚焦**交叉验证与专业场景**（见下"分流原则"）。

> **DSH 会话中用户直接发送的图片**：DSH 会把图片持久化为无扩展名的 content-addressed 文件（`~/.dsh/attachments/v1/objects/xx/<sha256>`），模型收到的是 `[Unsupported Image]` 占位符。此时把附件路径传给 `describe_image` / `describe_images` 即可（服务端已支持魔数嗅探，无需复制改名）。多张截图一次发来用 `describe_images` 逐张识别。

> **Bamboo 中继桥自动识别（快速第一印象）**：new-api 已启用 Bamboo 中继桥 + 图片识别（`image_recognize_model=qwen3-vl-flash`，经 dashscope-vision 渠道）。用户在 DSH 会话直接发图时，图片会在**网关层**被自动识别成文字描述并注入上下文（历史图片替换为 `[image]` 标记），模型直接"看到"图片内容——**此时无需再调 Vision MCP 工具**。识别失败（hop 错误）会导致整个请求失败。

## 分流原则（Bamboo 桥 vs Vision MCP 交叉验证）

- **日常发图（快速第一印象）**：Bamboo 桥已自动完成（qwen3-vl-flash，~1.3s，稳定）——用户发图后模型可直接理解，**不要重复调 Vision MCP**。
- **关键场景（必须主动交叉验证）**：用户要求"分析/确认/核验/校验/帮我看看这张图/数据/文字/图表"或图片涉及数据/科研图/重要文档时，**主动对同一附件做双模型交叉**：
  1. 取附件路径（`~/.dsh/attachments/v1/objects/xx/<sha256>`，魔数嗅探已支持）
  2. 调 `describe_image` 两次（不同厂商，如 qwen3-vl-flash + glm-4.6v），或调 `verify_figure`（自动双模型）
  3. 两模型描述**一致** → 高置信，直接汇报 `meta.cross_validated`；**不一致** → 列出分歧点，标注待用户确认
- **禁止**：对已由 Bamboo 桥识别过的日常图片重复调用 Vision MCP（浪费且多余）；除非场景是关键验证。

## 流程总则

1. 首次使用时先调用 `vision_health()`（DSH/Claude Code 中的命名为 `mcp__vision__vision_health`）确认服务在线；返回 `mock: true` 表示当前为模拟模式，结果不可信，必须在汇报中注明。
2. 所有工具返回统一契约 JSON：`{"ok": bool, "data": ..., "error": str?, "code": str?, "meta": {...}}`。`ok=false` 时读取 `error` 向用户解释，不要重试超过 2 次。
3. 输出给用户时标注模型与模式：`meta.model`、`meta.mock`、`meta.cross_validated`。

## 分层路由（性价比 / 旗舰）

- **日常性价比**：`describe_image` / `clipboard_image` / `describe_images` / `analyze_screenshot` / `extract_text` / `batch_analyze` 默认走性价比模型（qwen3-vl-flash / glm-4.6v-flash），成本最低。
- **质量要求高 / 旗舰**：`verify_figure` 走旗舰模型（glm-4.6v + qwen3-vl-plus 跨厂商交叉验证），结果最稳。
- 策略引擎内置失败升级与跨厂商回退，无需人工干预；`meta.tier` / `meta.model` 标注实际档位与模型。

## 场景 SOP

### A. 单图理解
1. 调用 `describe_image(path, question)`；question 要具体（对象/关系/文字/异常点）。
2. 直接引用返回的 `data.description`，不要脱离工具结果另行编造。

### A-1. 剪贴板图片识别（用户刚 Ctrl+C 复制了图片）
1. 调用 `clipboard_image(question)`；工具自动从 Windows 剪贴板抓图识别，无需路径。
2. 剪贴板里是文本/路径时返回 `CLIPBOARD_NO_IMAGE`，提示用户先复制图片本身（截图或图片文件）再重试。

### A-2. 多图识别（一次发来多张截图/附件）
1. 调用 `describe_images(paths, question)`；paths 为路径数组（支持无扩展名附件）。
2. 模型按顺序逐张描述，汇报时按编号对应到每张图；适合"这几张截图分别是什么/对比差异"。

### B. 网页截图核验
1. 调用 `analyze_screenshot(path, checklist)`；checklist 用逗号分隔核验点（如"页面正常加载,无报错弹窗,关键元素可见"）。
2. 按 `data.results` 逐项汇报 pass/fail；fail 项附 note 并给出修复建议。

### C. OCR 文字提取
1. 调用 `extract_text(path, lang)`；lang 填 zh/en/自动。
2. 原样输出 `data.text`，不要润色；批量场景改用 batch_analyze。

### D. 科研绘图验证
1. 调用 `verify_figure(path, checks)`；checks 默认六项：title,axes,legend,trend,values,colors。
2. 汇报 `data.report`：逐项 pass/note、issues 清单；默认双模型交叉验证，结果见 `meta.cross_validated`。

#### D-1. 论文插图严格审查提示词模板（零容忍，必用）

论文插图（架构图/流程图/结构图等**无坐标轴**的图）用 `describe_image` 检查时，**必须**套用以下严格提示词，禁止宽松描述：

> 你是学术期刊插图审稿人，执行零容忍审查。逐项判定 PASS 或 FAIL，禁止使用「基本/整体/大致/尚可/轻微」等模糊词；任何一项瑕疵都必须判 FAIL，并指出具体节点或位置。检查项：(1)文字完整性——每个节点/容器文字是否完整、无截断、无溢出其边框？(2)文字与形状重叠——圆柱/数据库形状节点内，文字是否被顶部椭圆弧线穿过或与弧线重叠？(3)文字溢出——任何文字是否超出所在节点边界？(4)连线质量——是否有连线相互交叉、穿过节点、或路径杂乱？(5)对齐——同层节点是否严格对齐、容器边框是否规整？(6)配色——是否出现任何蓝色调（论文黑白图应只有黑/白/灰）？(7)分层逻辑——数据层是否位于架构底部而非顶部或中间？请严格如实，宁可误报不可漏报。

**关键（防视觉模型幻觉）**：视觉模型会编造精确数字（如「倾斜0.8°」「超2px」「像素级测量128px vs 130px」）。凡涉及「文字是否越界/重叠/截断」的几何判定，**必须用 SVG 坐标或像素做二次定量验证**——提取 shape 的 `path`/`rect` 坐标与 `text` 的 `x/y` 对比（如 cylinder 底部弧最低点 y vs 文字 baseline y），以坐标铁证为准，不得只采信模型口述。

### E. 批量图片分析
1. 调用 `batch_analyze(directory, pattern, question)`；工具返回摘要 + `report_path`（完整报告落盘 reports/）。
2. 用户需要详情时读取 report_path 对应的 JSON 文件，不要要求工具把全文回传。

## 失败回退

- `code=IMAGE_ERROR`：检查路径/格式/大小（上限 4MB），修正后重试。无扩展名附件已自动嗅探格式，若仍报错说明文件非图片。
- `code=CLIPBOARD_NO_IMAGE`：剪贴板无图片，引导用户复制图片后重试。
- `code=PROVIDER_ERROR` 或提示未配置 key：告知用户补 ZHIPU_API_KEY（repo 根目录 .env）并把 config.json 的 mock 改为 false；或说明当前为 mock 结果。
- 性价比模型失败会自动升级旗舰模型（策略引擎内置），无需人工干预。

## 智谱官方视觉 MCP（zai-mcp-server，2026-09-21 接入并移植）

官方 Local MCP（stdio，`npx -y @z_ai/mcp-server@latest`，v0.1.5，Apache-2.0）的 8 个工具
**已逐字移植进本地 `~/projects/Vision-MCP`**（提示词见 `vision_mcp/prompts/`，同步脚本
`scripts/sync_zai_prompts.mjs`），因此**不需要再单独挂载官方 MCP**：

| 工具 | 用途 | 档位 |
|---|---|---|
| `ui_to_artifact` | UI 截图 → 代码/提示词/设计规范/描述（`output_type`） | official |
| `extract_text_from_screenshot` | 代码/终端/文档截图 OCR（比 `extract_text` 更贴代码场景） | official |
| `diagnose_error_screenshot` | 报错弹窗/堆栈截图诊断（可带 `context`） | official |
| `understand_technical_diagram` | 架构/流程/UML/ER 图解读（可带 `diagram_type`） | official |
| `analyze_data_visualization` | 仪表盘/统计图表解读（可带 `analysis_focus`） | official |
| `ui_diff_check` | 两张 UI 截图差异对比（期望 → 实际） | official |
| `video_analysis` | MP4/MOV/M4V/WEBM（本地 ≤8MB，或远端 URL 透传） | official |

`official` 档 = `zhipu:glm-5.3-flash`（与官方默认模型一致），回退链
`zhipu:glm-4.6v` → `dashscope:qwen3-vl-plus`。参数名与官方一致，同时接受本地路径与远端 URL。

**验收（2026-09-21）**：视频（ffmpeg 生成 3s testsrc）正确识别彩条/白圆/彩虹带/数字框；
OCR 逐字还原 Python 堆栈；错误诊断正确定位 DB 连接失败；图表分析指出缺刻度无法量化；
UI diff 报出结构性差异。原有 8 个工具回归全部正常，本地共 **15 个工具**。

配置取舍：Grok 的 `~/.grok/config.toml` 中 `zai-vision` 已置 `enabled = false`（功能已并入本地，
保留配置便于对比官方实现）；`zai-web-search` 保持启用。

**环境约定**：环境变量 `Z_AI_API_KEY`（同 ZHIPU_API_KEY 值）+ `Z_AI_MODE=ZHIPU`；
密钥统一存 `~/.config/dsh/secrets.env`（600 权限）与 `~/.config/vision-ai/.env`，
配置里用 `${ZHIPU_API_KEY}` 引用，禁止明文。

**计费**：官方工具走 REST 直调（套餐积分），不经 npx 进程，省一层启动开销。

## Grok 内模型补充（2026-09-21 配置）

Grok `~/.grok/config.toml` 已配置智谱/第三方模型，视觉任务编排时可按需切换主对话模型：

| 模型 | 用途 |
|---|---|
| `glm-5.3-flash` / `glm-5.3`（NewAPI 中转） | 日常主力 / 旗舰 |
| `deepseek-v4.1-flash`（commandcode Provider API，模型 ID `deepseek/deepseek-v4.1-flash`） | 备用对话模型 |

注意：`api.commandcode.ai` 在本机代理节点下会 TLS 黑洞，`~/.bashrc` 的 `no_proxy` 已豁免
该域名（走 IPv6 直连）；若 Grok 内该模型超时，先检查 `no_proxy` 是否含 `api.commandcode.ai`。

## 输出规范

- 每条结论注明来源工具与模式；mock 结果必须标注「模拟数据」。
- 科研/截图验证类输出使用清单式 markdown，fail 项加粗。
