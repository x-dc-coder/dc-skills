---
name: lark-cli
description: >-
  飞书/Lark 全家桶统一入口（聚合原 lark-* 全系 28 个技能）。覆盖：审批、妙搭/Spark/Miaoda 应用开发托管、
  考勤打卡、多维表格/Base/bitable、日历/日程/会议室预订、通讯录/联系人解析、云文档 Docx/Wiki/思维笔记、
  云盘/Drive 上传下载/权限/评论/版本/导入导出、实时事件监听、IM 消息/群聊/卡片/Feed/加急、邮箱/邮件/草稿/
  模板/收信规则、Markdown 文件、视频会议/妙记/纪要/逐字稿/机器人参会、OKR、原生 OpenAPI、电子表格/Sheet/
  公式/图表/透视表、幻灯片/PPT、任务/待办/清单/任务智能体、画板/Whiteboard、知识库/空间/节点、日程待办摘要。
  用户提到飞书/Lark/Feishu 或上述任一场景（发消息、查日程、约会议室、审批、建表、读写表格、收发邮件、
  读写文档/wiki、云盘文件、会议纪要、妙搭应用、OKR、白板、任务、考勤、通讯录、事件监听、认证登录/授权、
  CLI 安装更新）时使用本技能：先按路由表选域执行 lark-cli <domain>，复杂域先 lark-cli skills read <域>
  获取权威规程；认证、输出契约与高风险操作遵守共享底座规则。
metadata:
  requires:
    bins: ["lark-cli"]
---

# lark-cli — 飞书/Lark 全家桶统一入口（聚合技能）

本技能聚合原 28 个 `lark-*` 独立技能（approval / apps / attendance / base / calendar / contact / doc /
drive / event / im / mail / markdown / meeting / okr / openapi-explorer / shared / sheets /
skill-maker / slides / task / vc / vc-agent / minutes / note / whiteboard / wiki /
workflow-meeting-summary / workflow-standup-report），模型目录只保留本一个条目；各域权威规程内嵌于 lark-cli 二进制，
随 CLI 版本自动同步，用 `lark-cli skills read` 按需读取。

## 0. 总则（每次操作前）

1. **身份先行**：先 `lark-cli whoami` 确认当前身份与登录态。`--as user` 代表用户本人（可操作个人资源），
   `--as bot` 代表应用（仅应用资源，查用户资源返回空成功而非报错）。未登录先 `lark-cli auth login`。
2. **域规程必读**：每个域都有权威 SKILL.md（内嵌）。执行前先 `lark-cli skills read <域>`（如
   `lark-sheets`）获取完整规程与命令矩阵；SKILL.md 引用的子文件用
   `lark-cli skills read <域>/references/<file>` 读取。内嵌文本中的 `../lark-shared/SKILL.md` 引用
   以本技能 §2 共享底座替代（或直接 `lark-cli skills read lark-shared`）。
3. **输出契约**：默认 `--format json`，成功判断用 `ok == true`（或退出码 0），**不是** `code == 0`
   （成功信封无顶层 code/msg，code 只在错误信封内）。长输出加 `--jq <expr>` 过滤，防上下文膨胀。
4. **安全红线**：写入/删除前必须确认用户意图；目标命令支持 `--dry-run` 时先预览；**退出码 10 是高风险
   确认门禁（high-risk-write）**——停下 → 向用户展示 action/risk/关键参数 → 取得显式同意后把确认 flag
   追加到原 argv 末尾重试，绝不静默绕过；禁止输出 appSecret/accessToken 等密钥明文。
5. **路径与数据**：`--file`/`--output`/`--output-dir`/`@file` 等只接受 cwd 相对路径（绝对路径报
   `unsafe file path`）；大数据输入优先 stdin。
6. **授权 URL 必须配二维码**：输出含 `verification_url`/`verification_uri_complete`/`console_url` 时，
   用 `lark-cli auth qrcode` 生成二维码与 URL 一起展示（优先 PNG，用户要求才用 --ascii）。

## 1. 路由表（意图 → 域 → 入口）

| 用户意图 | 域 | 命令入口 | 权威规程 |
|---|---|---|---|
| 审批待办/已办/实例查询、发起/同意/拒绝/转交/催办 | approval | `lark-cli approval` | skills read lark-approval |
| 妙搭/Spark/Miaoda 应用开发、部署、HTML 页面、创意设计、环境变量/日志 | apps | `lark-cli apps` | skills read lark-apps |
| 考勤打卡记录查询 | attendance | `lark-cli attendance` | skills read lark-attendance |
| 多维表格/Base/bitable：建表/字段/记录/视图/统计/公式/仪表盘/表单/workflow | base | `lark-cli base` | skills read lark-base |
| 日历：日程查看/创建/更新、忙闲查询、会议室预订 | calendar | `lark-cli calendar` | skills read lark-calendar |
| 通讯录：姓名/邮箱→open_id、反查、搜索机器人/智能体 | contact | `lark-cli contact` | skills read lark-contact |
| 云文档 Docx/Wiki 内容读取/创建/编辑、图片、思维笔记 | doc | `lark-cli docs` | skills read lark-doc |
| 云盘 Drive：上传/下载/导入/导出/权限/评论/版本/搜索/复制移动 | drive | `lark-cli drive` | skills read lark-drive |
| 实时事件监听/订阅/消费（IM/审批/任务/会议等） | event | `lark-cli event consume <EventKey>` | skills read lark-event |
| IM：发消息/群聊管理/卡片/Feed/表情/加急/聊天记录 | im | `lark-cli im` | skills read lark-im |
| 邮箱：收发邮件/草稿/模板/签名/收信规则/HTML 邮件 | mail | `lark-cli mail` | skills read lark-mail |
| 云盘原生 Markdown 文件创建/读取/patch/比较 | markdown | `lark-cli markdown` | skills read lark-markdown |
| 视频会议/妙记/纪要/逐字稿/机器人参会/会中互动 | meeting | `lark-cli vc`/`minutes`/`note` | skills read lark-meeting |
| 会议纪要整理、指定时间范围纪要汇总周报 | workflow | 编排 calendar +agenda 等 | skills read lark-workflow-meeting-summary |
| OKR：目标/关键结果/对齐/指标/进展记录 | okr | `lark-cli okr` | skills read lark-okr |
| 原生 OpenAPI 探索（现有命令无法满足时） | openapi | `lark-cli schema`/`api GET <path>` | skills read lark-openapi-explorer |
| 电子表格：读写单元格/样式/公式/图表/透视表/筛选/批注 | sheets | `lark-cli sheets` | skills read lark-sheets |
| 把飞书 API 操作封装成自定义 Skill | skill-maker | 按规程创建 | skills read lark-skill-maker |
| 幻灯片/PPT：创建/编辑/页管理/替换/截图 | slides | `lark-cli slides` | skills read lark-slides |
| 任务/待办/清单/子任务/附件/任务智能体 | task | `lark-cli task` | skills read lark-task |
| 画板/Whiteboard：查看/导出/编辑 | whiteboard | `lark-cli whiteboard` | skills read lark-whiteboard |
| 知识库：空间/节点/成员/移动/快捷方式 | wiki | `lark-cli wiki` | skills read lark-wiki |
| 日程+待办摘要（今天/明天/本周安排） | workflow | 编排 calendar +agenda + task +get-my-tasks | skills read lark-workflow-standup-report |

兼容占位：`lark-minutes` / `lark-note` / `lark-vc` / `lark-vc-agent` 为历史兼容名，统一走 meeting 域。

## 2. 共享底座（原 lark-shared，全域适用）

- **身份与权限**：身份模型（user/bot）、scope 缺失（missing_scopes）、授权、权限管理 →
  `lark-cli skills read lark-shared/references/lark-shared-identity-and-permissions.md`；
  首次使用运行 `lark-cli config init` 完成应用配置（提示 `config init --new` 时使用）。
- **输出契约**：成功/失败信封结构、stdout/stderr 约定、封装脚本 →
  `lark-cli skills read lark-shared/references/lark-shared-output-contract.md`。
- **高风险审批流**：risk 分级、exit 10 门禁、confirmation 后重试 →
  `lark-cli skills read lark-shared/references/lark-shared-high-risk-approval.md`。
- **更新通知**：输出含 `_notice`（CLI 升级 / skills 落后 / 废弃命令）时按其指引处理 →
  `lark-cli skills read lark-shared/references/lark-shared-update-notice.md`。
- **Wiki token 路由**：wiki 链接 token 与 drive token 区分 →
  `lark-cli skills read lark-shared/references/lark-wiki-token-routing.md`。

## 3. 磁盘资源映射（CLI 内嵌版不含机器资源）

以下资源不随 CLI 内嵌，已存放在本技能目录 `resources/` 下；内嵌 SKILL.md 中的相对引用按下表定位：

| 内嵌引用（相对域根） | 磁盘位置 |
|---|---|
| lark-sheets: `scripts/lark_*.py`（表格画像/子表检测/结构摘要，只读增强） | `resources/lark-sheets-scripts/` |
| lark-slides: `scripts/xml_lint.py` 等（XML 校验/图标工具） | `resources/lark-slides-scripts/` |
| lark-apps: `creative-design/`（创意设计完整工作流 + starter 组件） | `resources/lark-apps-creative-design/` |
| lark-mail: `assets/templates/`（官方邮件模板库） | `resources/lark-mail-assets/templates/` |
| lark-whiteboard: `elements/*.md`（元素 schema/样式/布局规范） | `resources/lark-whiteboard-elements/` |

> lark-slides 提交完整 slide XML 前 **MUST** 用 `resources/lark-slides-scripts/xml_lint.py` 校验
> （error_count 必须为 0）；本地脚本不存在时按内嵌 SKILL.md 中给出的 CLI 等价路径执行。

## 4. 维护与更新（常用命令）

- 健康检查：`lark-cli doctor`（config/auth/连通性一站式）
- 认证管理：`lark-cli auth status` / `lark-cli auth login` / `lark-cli auth logout`；`lark-cli whoami`
- 配置档案：`lark-cli config` / `lark-cli profile`（多档切换用 `--profile`）
- 升级 CLI：`lark-cli update` —— **内嵌技能随 CLI 升级自动同步，无需单独安装**
- 技能查看：`lark-cli skills list` / `lark-cli skills list <域>`（列一层）/ `lark-cli skills read <域>`
- 探索 API：`lark-cli schema <service.resource.method>`（参数/类型/scope/示例）；
  `lark-cli api GET|POST <path> [--params <json>] [--data <json>]`（逃生舱）
- 回退到分散技能模式（如官方恢复独立技能）：`npx skills add larksuite/cli -g -y`（重装 28 个独立 SKILL.md
  到 `~/projects/dc-skills/`，本聚合技能可自行决定去留）
