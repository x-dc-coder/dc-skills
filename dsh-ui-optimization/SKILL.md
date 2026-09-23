---
name: dsh-ui-optimization
description: >
  DSH 插件前端优化（薄壳编排 skill）：优化 DSH client 插件注入的界面（conversation.view 面板 /
  settings.plugin.item 设置卡片 / 第三方 section），遵循 --dsw-* token 与 slots 契约、bundle
  lazy-CJS 格式、验收闭环红线（锚定当前 URL + vision 核验）。触发：优化 DSH 插件界面、改 client
  插件 UI、lab-monitor 面板美化、插件前端丑、调 DSH 插件样式。美学理论不重复造——按需转调
  design-ui（风格选型）/ design-dataviz（状态色/间距/正确性）/ vision-workflow（截图核验）。
metadata:
  family: dshplugin
  role: member
  load-mode: manual
disable-model-invocation: true
---
# DSH 插件前端优化

## 0. 先读什么（顺序）

1. **本速查（唯一知识源）**：`~/.dsh/.agent-presets/plugin-specialist/docs/local/ui-optimization-quickref.md`——完整机制/契约/SOP 都在那里，本 SKILL 不重复正文，只做编排。
2. **新鲜度前置**：调 `plugin_doctor_health` 确认宿主与 docs 快照新鲜度（本 skill 依赖的 quickref 是本机实测沉淀，fresh 即用；涉及上游文档细节先仲裁）。
3. **项目上下文**：目标插件工程（package.json 的 dsh.client / exports["./client"] / tsdown 配置 / src/client.ts 入口）。

## 1. 触发与边界

- 触发词：优化 DSH 插件界面 / 改 client 插件 UI / lab-monitor 面板 / 插件前端丑 / 调插件样式。
- 不接的活：内置壳（dsh-web-frontend）重建——默认归"重路径"，先给 token 覆盖方案，用户明确接受成本才动手；与 DSH 无关的通用前端页面 → 应转 design-ui。
- 全程红线：不自重启 DSH（刷新/重启交用户）；不改部署自带预设；不引组件库；验收锚定当前 URL（3080）。

## 2. 工作流（改 → 建 → 刷 → 验 → 迭代）

1. **读现状**：目标界面截图（browser MCP 导航 3080 → 截图存 /home/dc/dsh-gui-*.png）+ 源码定位（哪个出口渲染的：conversation.view / 卡片 / 第三方 section）。
2. **问题清单**：用 vision MCP `analyze_screenshot`（checklist 给：状态色语义/对齐/间距/控件反馈/可读性）拿具体缺陷；需要风格方案时**转调 design-ui**（数据密集型面板默认 Data-Dense 哲学），颜色正确性**转调 design-dataviz**（状态色/调色板公式）。
3. **小步改**：单点改动（对齐/间距/状态色语义优先），遵守 quickref §4 token 规则；记录改动便于回滚。
4. **构建**：`cd <插件工程> && npm run build`（tsc + tsdown）。
5. **刷新**：**请用户刷新 GUI（3080）**——不自行重启；HMR 未实证前不承诺免刷新热更。
6. **验收**：browser 截图 → vision `analyze_screenshot` 逐项核验（6/6 类 checklist）→ 不满意回 3。
7. **交付**：改动清单（文件/行/slot/样式）+ 核验证据（前后截图对照）+ 是否保留建议；git 提交或回滚由用户确认。

## 3. 速查卡（详细在 quickref）

| 主题 | 要点 |
|---|---|
| 双层架构 | 内置壳只引导；实际 UI 全是插件 client-modules → 改插件页是轻路径 |
| slots 契约 | `ctx.get('slots')` → inject('conversation.view', () => register({name,id,order,label}, C))；裸 register 抛错 |
| token | 只用 `--dsw-alias-*` 语义别名；禁组件库/Tailwind；全局观感=注册第三方面板覆盖 alias |
| bundle | client.js 须 `window.__ModuleLoader__.load({id, factory})` lazy-CJS；tsdown banner/footer 复刻；`head -c 600 lib/client.js` 验证 |
| 验收 | 锚定 3080；裸 Vite 白屏陷阱；每一步 vision 核验 |

## 4. 交付物形态

- 改动 diff 摘要 + 前后截图（/home/dc/dsh-gui-*.png）+ 核验 checklist 结果。
- 若用户要规范化：把调整沉淀进 quickref（§4 样式约定段），保持知识单源。