---
name: task-hub
description: >
  本地快捷任务管理与全局任务中心调度：跨会话管理任务、新建任务、编辑任务全字段、智能识别依赖关系、查询待办与就绪任务、更新进度、导出报表及生成看板。当用户在任意会话需要新建任务、编辑任务（标题/截止日/优先级/排期/依赖）、查看任务、推进任务状态（start/done/wait）、推断或调整依赖关系、或询问任务安排/计划时使用本技能。
---

# task-hub · 跨会话快捷任务管理技能

本技能为所有会话提供对本地统一任务中心（`task-hub`）的安全、快捷调用。无论当前会话位于哪个工作目录，均可通过统一入口、确定性拓扑规则与 LLM 语义理解进行任务全生命周期管理（创建、编辑、依赖智能推断、状态流转、看板呈现）。

---

## 核心设计与执行约定

1. **零服务 / 零数据库**：任务存储于 Markdown（一行一任务），本地 Git 自动原子提交追踪变更与事件流。
2. **唯一写入口**：所有写操作**一律经由 `taskctl`**，严禁 Agent 自行直接编辑 `tasks/*.md`，以确保事件流、时间推导与 Git 提交的完整性。
3. **全局调用路径**：
   - 默认 Hub 位置：`/home/dc/projects/task-hub`（可通过环境变量 `TASKHUB_DIR` 覆盖）。
   - 任何目录下统一执行：
     ```bash
     python3 /home/dc/projects/task-hub/taskctl <子命令> [参数...]
     ```

---

## 核心工作流

### 1. 新建任务（支持智能识别前置依赖）

- **快速新建并自动挂前置**（首选）：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl add "任务标题" --project <项目名> --auto-dep
  ```
  > 示例：`taskctl add "修复 soul-spark 登录闪退" --project soul-spark --auto-dep`
  > 系统将自动寻找 `soul-spark` 的未完成链尾（如 `PRJ-06`）并挂为前置依赖，回显 `[PRJ-08] 自动关联前置: PRJ-06`。

- **先看建议依赖，再确认创建**（谨慎模式）：
  ```bash
  # 查看候选依赖与依据（只读，不修改任何文件）
  python3 /home/dc/projects/task-hub/taskctl deps-suggest "任务标题" --project <项目名>

  # 确认后创建（或手动显式指定 --dep）
  python3 /home/dc/projects/task-hub/taskctl add "任务标题" --project <项目名> --dep PRJ-06
  ```

- **带时间组件创建**：
  ```bash
  # 承诺截止日（唯一能产生逾期的组件）
  python3 /home/dc/projects/task-hub/taskctl add "撰写结题报告" --track paper --due 2026-10-31 --pri 1

  # 排期区间（相对或绝对时间段）
  python3 /home/dc/projects/task-hub/taskctl add "基建迁移" --track infra --schedule "2026-10-01 ~ 2026-10-07"
  ```

---

### 2. 任务编辑（全字段修改与依赖重设）

任务建立后，可随时对任意字段（属性、时间、依赖）进行精细化调整：

- **综合字段编辑（`taskctl set`）**：
  ```bash
  # 修改标题与优先级
  python3 /home/dc/projects/task-hub/taskctl set <ID> --title "新标题" --pri 1

  # 调整截止承诺与排期
  python3 /home/dc/projects/task-hub/taskctl set <ID> --due 2026-10-15 --schedule "2026-10-08 ~ 2026-10-14"

  # 补充标签与备注
  python3 /home/dc/projects/task-hub/taskctl set <ID> --tag "核心,前端" --note "需要协同后端接口联调"

  # 重置前置依赖列表（全量覆盖）
  python3 /home/dc/projects/task-hub/taskctl set <ID> --dep "PRJ-02, PRJ-03(soft)"

  # 为已有任务智能补充前置依赖
  python3 /home/dc/projects/task-hub/taskctl set <ID> --auto-dep
  ```

- **依赖增量微调（`taskctl dep`）**：
  ```bash
  # 追加前置（支持 --soft 弱前置）
  python3 /home/dc/projects/task-hub/taskctl dep <ID> --add <UPSTREAM_ID> [--soft]

  # 移除前置
  python3 /home/dc/projects/task-hub/taskctl dep <ID> --rm <UPSTREAM_ID>
  ```

- **查看已有任务的智能依赖建议**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl deps-suggest <ID>
  ```

- **跨领域移动**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl move <ID> <new_track>
  ```

- **看板内可视化编辑**：
  运行 `taskctl serve` 启动本地写回桥后，在浏览器看板（`board.html`）点击任意任务节点，右侧面板即提供完整表单供可视化修改并实时回写。

---

### 3. 智能依赖识别机制

系统支持两层互补的智能：

1. **引擎规则与拓扑智能（确定性、可解释、防死锁）**：
   - **项目未完成链尾推断**：自动遍历项目任务拓扑，定位入度为 0 的未完成叶子任务（当前推进的“最后一棒”），作为新任务的自然前置。
   - **显式 ID / 项目引用扫描**：自动识别标题与备注中出现的已有任务 ID 或已知项目，优先关联。
   - **三色标记 DFS 环路防范**：在实际绑定前在拓扑沙箱中模拟，若检测到环依赖，自动在 stderr 告警并跳过该依赖，确保图结构的无环健康。
   - **级联推导（`derive`）**：自动推导最早开始时间（`est`）、预计完成时间（`eta`）、挂起状态（`suspended`）、逾期风险（`ovd`）。
2. **LLM 语义认知智能（Agent 会话层理解因果）**：
   - 当用户在跨会话中用自然语言提出任务时（如“我准备做登录页面，需要先等用户表结构迁移好”），会话中的 Agent 会通过语义理解分析任务间的业务逻辑因果，再调用 `taskctl add --dep ...` 或 `--auto-dep` 落地，形成「语义因果 + 拓扑校验」的双保险。

---

### 4. 查询与行动推进

- **查看当前完全就绪的任务**（无阻塞、前置均已完成、现在能动手做）：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl ready
  ```

- **查看任务清单（支持按项目/领域/状态过滤）**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl ls
  python3 /home/dc/projects/task-hub/taskctl ls --project soul-spark
  python3 /home/dc/projects/task-hub/taskctl ls --status doing
  ```

- **查看单一任务详情与上下游血缘**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl show <TASK_ID>
  ```

- **状态推进快捷指令**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl start <ID>                   # 开工
  python3 /home/dc/projects/task-hub/taskctl done <ID>                    # 完成（即时解锁下游任务）
  python3 /home/dc/projects/task-hub/taskctl wait <ID> "等待外部条件"     # 挂起（下游自动进入挂起）
  python3 /home/dc/projects/task-hub/taskctl progress <ID> 2 --unit 篇   # 打卡累加度量
  python3 /home/dc/projects/task-hub/taskctl comment <ID> "进展备注"     # 追加评论记录
  ```

- **全系统依赖体检**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl verify
  ```

- **生成/更新看板与周报**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl board
  python3 /home/dc/projects/task-hub/taskctl export --format advisor --days 7
  ```

---

## 领域（Track）与标识规则

| Track 代号 | 领域名称 | ID 前缀 | 存储文件 |
|---|---|---|---|
| `projects` | 项目任务 | `PRJ-` | `tasks/projects.md` |
| `dsh` | DSH 生态 | `DSH-` | `tasks/dsh.md` |
| `paper` | 论文科研 | `PAPER-` | `tasks/paper.md` |
| `infra` | 基础设施 | `INFRA-` | `tasks/infra.md` |
| `skills` | 技能工具 | `SKILL-` | `tasks/skills.md` |
| `inbox` | 收集箱 | `INBOX-` | `tasks/inbox.md` |
