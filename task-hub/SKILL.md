---
name: task-hub
description: >
  本地快捷任务管理与全局任务中心调度：跨会话管理任务、新建任务、自动识别依赖关系、查询待办与就绪任务、更新进度、导出报表及生成看板。当用户在任意会话需要新建任务、查看任务、推进任务状态（start/done/wait）、设置依赖关系、或询问任务安排/计划时使用本技能。
---

# task-hub · 跨会话快捷任务管理技能

本技能为所有会话提供对本地统一任务中心（`task-hub`）的安全、快捷调用。无论当前会话位于哪个工作目录，均可通过统一入口与确定性规则进行任务创建与生命周期管理。

---

## 核心设计与执行约定

1. **零服务 / 零数据库**：任务存储于 Markdown，单机本地 Git 自动追踪变更。
2. **唯一写入口**：所有写操作**一律经由 `taskctl`**，严禁 Agent 自行直接编辑 `tasks/*.md`，以确保事件流、时间推导与 Git 提交的完整性。
3. **全局调用路径**：
   - 默认 Hub 位置：`/home/dc/projects/task-hub`（可通过环境变量 `TASKHUB_DIR` 覆盖）。
   - 任何目录下统一执行：
     ```bash
     python3 /home/dc/projects/task-hub/taskctl <子命令> [参数...]
     ```

---

## 核心工作流

### 1. 新建任务（支持自动识别前置依赖）

新建任务时，系统会基于项目上下文、领域未完成链尾或显式 ID 引用自动推断前置依赖：

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
  # 承诺截止日
  python3 /home/dc/projects/task-hub/taskctl add "撰写结题报告" --track paper --due 2026-10-31 --pri 1

  # 排期区间
  python3 /home/dc/projects/task-hub/taskctl add "基建迁移" --track infra --schedule "2026-10-01 ~ 2026-10-07"
  ```

### 2. 查询与聚焦行动

- **查看当前完全就绪的任务**（无阻塞、前置均已完成、现在能动手做）：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl ready
  ```

- **查看任务清单（支持按项目/领域过滤）**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl ls
  python3 /home/dc/projects/task-hub/taskctl ls --project soul-spark
  python3 /home/dc/projects/task-hub/taskctl ls --track dsh
  ```

- **查看单一任务详情与依赖血缘**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl show <TASK_ID>
  ```

### 3. 状态流转与推进

| 意图 | 命令 | 说明 |
|---|---|---|
| **开工** | `taskctl start <ID>` | 状态转为「进行中」，自动 Git commit |
| **完成** | `taskctl done <ID>` | 状态转为「已完成」，记录完成日，**即时解锁下游任务** |
| **挂起/等待** | `taskctl wait <ID> "等待外部条件"` | 转为「等待中」，豁免逾期，**下游自动进入挂起状态** |
| **重新打开** | `taskctl reopen <ID>` | 状态重置为「待办」 |
| **打卡累加** | `taskctl progress <ID> 2 --unit 篇` | 累加度量进度 |
| **追加记录** | `taskctl comment <ID> "进展备注"` | 在任务下追加带时间戳的评论 |

### 4. 依赖关系手动调整与体检

- **增加依赖**：`taskctl dep <ID> --add <UPSTREAM_ID>`（可选加 `--soft` 作为弱依赖）
- **解除依赖**：`taskctl dep <ID> --rm <UPSTREAM_ID>`
- **全系统体检（查环/悬空前置/排期越界）**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl verify
  ```

### 5. 看板与报表

- **更新看板 HTML**：
  ```bash
  python3 /home/dc/projects/task-hub/taskctl board
  ```
  生成 `/home/dc/projects/task-hub/board.html`，可在浏览器中打开（或通过 Web 链接访问）。
- **生成周报 Markdown**：
  ```bash
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

*注：若使用 `--project <名称>` 新建任务，系统会自动将其归入 `projects` 领域并分配 `PRJ-XX` 编号。*
