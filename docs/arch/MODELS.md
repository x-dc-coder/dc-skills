# 模型登记

Grok 实际读取 `~/.grok/config.toml`。本文件只记已核实的渠道、协议、窗口、输出上限、思考档位和套餐到期。不记密钥。

改配置里上述字段时，同步改对应行。到期只写 `YYYY-MM-DD`，未知写 `—`。已打开的会话要新开进程才吃到新配置。

当前默认模型 `deepseek-v4.1-flash`，`default_reasoning_effort = max`（该模型菜单含 `max`）。Chat Completions 只有显式选档才发送 `reasoning_effort`（2026-09 测量，本次未重测）。

核实日期：2026-10-04。全机配置模型（gemini-3.8-flash、deepseek-v4.1-flash、Atria-Dawn-Preview、step-5-preview、glm-5.3、glm-5.3-flash、grok-4.7）已全量完成端到端无头连通性验证（返回 PONG 且无序列化报错）。Gemini 档位结论仍是 2026-09-30 的测量。DeepSeek 改回 Chat Completions。Step 改回本地 shim 的 Chat Completions。Gemini、Atria、GLM 仍是 Responses。套餐到期日沿用原登记。

## grok-官方模型

协议 `responses`，渠道 SpaceXAI（`grok login`）。`grok-4.7-build-fast` 为同代快速档，价格更高。


| 模型                  | 上下文    | 最大输出 | 思考档位                        | 套餐  | 到期  |
| ------------------- | ------: | ----: | --------------------------- | --- | --- |
| grok-4.7            | 500000 | —    | low / medium / high / xhigh | —   | —   |
| grok-4.7-build-fast | 500000 | —    | low / medium / high / xhigh | —   | —   |
| grok-4.6            | 500000 | —    | low / medium / high / xhigh | —   | —   |
| grok-4.5            | 500000 | —    | low / medium / high         | —   | —   |


## 自定义模型


| 模型                  | 渠道                                                  | 协议                | 上下文     | 最大输出   | 思考档位                | 套餐              | 到期         |
| ------------------- | --------------------------------------------------- | ----------------- | -------: | ------: | ------------------- | --------------- | ---------- |
| gemini-3.8-flash    | `ai.x-lf.com/v1` · `GEMINI_API_KEY`                 | responses         | 1000000 | 65536  | low / medium / high | —               | —          |
| deepseek-v4.1-flash | `api.commandcode.ai/provider/v1` · `COMMANDCODE_API_KEY` | chat_completions | 1000000 | 393216 | low / high / max    | GOAT Plan       | 2026-10-11 |
| Atria-Dawn-Preview  | `api.atria-asi.ai/v1` · `ASI_API_KEY`               | chat_completions  | 256000  | 65536  | low / medium / high | 限时免费            | —          |
| step-5-preview      | `127.0.0.1:8321/v1` · `STEPFUN_API_KEY`             | chat_completions  | 1000000 | 65536  | low / medium / high | Plus Plan       | 2026-11-19 |
| glm-5.3             | `open.bigmodel.cn/api/v1` · `ZHIPU_API_KEY`         | responses         | 1048576 | 131072 | low / high / max    | Coding Plan（团队） | 2026-12-20 |
| glm-5.3-flash       | 同上                                                  | responses         | 1048576 | 131072 | low / high / max    | Coding Plan（团队） | 2026-12-20 |


上游模型名与目录名不同的只有 DeepSeek：`deepseek/deepseek-v4.1-flash`。GLM 的 `reasoning_summary` 为 `none`。Gemini、GLM 用 Responses 默认的 `concise`，网关接受。DeepSeek、Step 与 Atria 走 Chat Completions。

## 档位

- Gemini（Responses，2026-09-30）：菜单只放 `low` / `medium` / `high`。上游有效档位就是这三档。`none` 返回 200、推理 token 为 0，未进菜单。`minimal` 与 `xhigh` 返回 500。`max` 被网关改写成 `xhigh` 后同样 500。`previous_response_id` 续写返回 200 但不保留上文；Grok 不发这个字段，把上一轮 output 放回 input，两轮回忆可用。选 Responses 是因为推理和 `function_call` 是独立项。Chat Completions 也能完成同样调用，响应里另有 Grok 不认识的 `thinking_signature`。DSH 里同名渠道 `xlf-responses` 仍是 `openai-completions`，本次只改了 Grok。请求走代理 `http://127.0.0.1:7890`：2026-10-01 直连 TLS 收不到字节，约 50 秒超时；经代理的握手约 50ms。客户端里几十秒的回合是模型和上下文时间。本次未改 Gemini 配置。
- DeepSeek（Chat Completions，2026-10-01）：菜单 `low` / `high` / `max`。`base_url` 为 `https://api.commandcode.ai/provider/v1`，`api_backend = chat_completions`，上游模型 ID 保持 `deepseek/deepseek-v4.1-flash`。Command Code 官方 Provider 文档把开放模型放在 `POST /provider/v1/chat/completions`（示例为 `deepseek/deepseek-v4-flash`）；Claude 只用 `POST /provider/v1/messages`。Grok 1.0.44 读不了该网关的 Responses 流：会出现不认识的 `response.reasoning.delta`，同一次响应里 `annotations` 或 `text` 也会缺字段。`deepseek/deepseek-v4-flash-fast` 与 `Qwen/Qwen3.8-Flash` 打 `/responses` 会 400，网关要求改走 `/chat/completions`。流量走 `http://127.0.0.1:7890`。`api.commandcode.ai` 放进 `no_proxy` 后会改走 TUN 直连并超时。代理上的 TLS 仍可能一次 EOF，重试可以通。改成 Chat Completions 后，两次无头 `grok -p`（只要单词 pong，effort low）都返回了 pong，墙钟 17.8s 与 8.6s，没有序列化错误。同句下 `max` 推理 token 高于 `low` / `high`、`xhigh` 与 `high` 同级，是改协议前在 Responses 上测的，Chat Completions 本次未重测档位差。
- GLM 套餐 Key 只走 Responses，直连 `open.bigmodel.cn`。`low` / `high` / `max` 可用；`xhigh` 在套餐侧折成 `max`。
- Step（Chat Completions，2026-10-01）：`base_url` 为 `http://127.0.0.1:8321/v1`，`api_backend = chat_completions`，模型 ID `step-5-preview`。shim 在 `~/.local/share/stepfun-shim/server.py`，由 `~/.bashrc` 在 8321 空闲时拉起，转发到 `https://api.stepfun.com/step_plan`（Step Plan 的 Chat Completions / Messages）。公网 `https://api.stepfun.com/v1/responses` 的流含 `response.reasoning_part.added` 与 `response.reasoning_part.done`，Grok 1.0.44 不认识，整条流失败。直连 Messages 发出 thinking 的 `content_block_start` 时没有 `signature`，客户端报 missing field `signature`。shim 会补上空的 `signature`，并去掉空的 tool_call `id` / `type` / `name` 以及 `thinking=adaptive`；同一条会触发思考的提示经 shim 可以返回。保存的条目停在 Chat Completions。流式工具增量里的空 `id` 仍由 shim 清掉。
- Atria（Chat Completions，2026-10-04）：官方 `https://api.atria-asi.ai/v1`，`api_backend = chat_completions`。此前配置为 Responses 协议时，在执行复杂推理任务的上游流中会下发 `response.reasoning_part.added` 与 `response.reasoning_part.done` 事件，Grok 客户端 Responses 解析器（1.0.44）不包含该 variant，导致抛出 `serialization error: unknown variant response.reasoning_part.added` 崩溃。改走官方原生支持的 `POST /v1/chat/completions` 后，流式下发标准的 `chat.completion.chunk`（`delta` 中携带 `reasoning_content`），Grok 端到端推理与工具流完全正常，彻底消除序列化崩溃。
- 菜单以外的档位，CLI 直接拒绝。

## 智能体团队调度分层与额度原则

在多智能体与子代理任务派发中，遵循**额度感知分层**与**双模型交叉验证**原则：

### 1. 梯队分层定位
* **最高决策与核心分析层（稀缺额度 / 关键裁决）**：
  * `grok-4.7`（SpaceXAI 官方）：最高决策官。负责系统架构终审、宏观方案权衡、极限复杂跨系统疑难定位、前沿技术演进定调。
  * `glm-5.3`（Z.ai Coding Plan 团队套餐）：核心分析官。负责代码级深层排障与根因裁决、并发与数据流分析、落地工程可行性审计。
* **主力执行层（充足额度 / 高吞吐主力）**：
  * `deepseek-v4.1-flash`（CommandCode GOAT 套餐）：核心执行主力 A。负责核心业务代码实现、详细排查步骤执行、测试用例编写，承担大体量代码生成。
  * `gemini-3.8-flash`（xlf 渠道）：核心执行主力 B。负责 100万 tokens 大上下文吞吐、前端与组件开发、UI 多模态视觉验收、快速 Diff 初查。
* **专项质检与探索层（专项能力 / 零额度补充）**：
  * `step-5-preview`（StepFun 本地 shim）：对抗质检工程师。专精红绿准入测试、变异反证（Mutation Testing）与极端边界条件推导。
  * `Atria-Dawn-Preview`（上海 AI 实验室 744B MoE，限时免费）：长程调研员与二线分析员。专精长周期工具调用、开源方案调研与学术文献提炼；作为免费的二线分析与初审节点，有效节约付费额度。
* **外部终审与逃生通道（Human-in-the-Loop Escape Hatch）**：
  * `gpt6Astra`（ChatGPT Plus 会员客户端）：最高仲裁顾问。当遇到团队内部反复震荡、产生重大架构分歧、或现有模型均无法定夺的极限问题时，主智能体负责生成标准化的《外部专家咨询卡》（含问题背景、分歧要点、候选方案与待决问题），提示用户手动前往客户端向 `gpt6Astra` 提问，再将结论带回仓库继续执行。

### 2. 双模型交叉审查机制（Dual-Model Cross-Verification）
关键任务均采用「主执行模型落地 + 异构模型交叉对抗」的闭环协作：
* **主执行模型**生成产物草案（方案、代码、排查结论），存入对应产物目录或工作树。
* **交叉审查模型**基于异构模型架构独立介入，以对抗视角查找隐藏缺陷、未覆盖边界或逻辑漏洞。
* 主智能体综合审查意见完成确认或定向修正。

---

## 全生命周期任务类型与模型配置矩阵

| 任务类型 | 场景说明 | 主执行模型（Primary） | 分析/决策/交叉模型（Review & Decision） | 交叉审查关注重点 |
|---|---|---|---|---|
| **1. 全局系统架构与重构决策** | 架构重构、服务拆分、技术选型 | `deepseek-v4.1-flash` (`high`) | `grok-4.7` (`high`) 分析决策<br>`glm-5.3` (`high`) 交叉审查 | 宏观设计权衡、工程落地可行性、隐藏技术债 |
| **2. 数据模型与通信契约设计** | 数据库 DDL、OpenAPI、RPC 契约 | `deepseek-v4.1-flash` (`high`) | `glm-5.3` (`high`) 分析决策<br>`Atria-Dawn-Preview` (`high`) 交叉 | 跨服务一致性、并发锁机制、错误状态码完备性 |
| **3. 常规深层排障与根因定位** | 隐蔽逻辑缺陷、调用栈断裂、数据污染 | `deepseek-v4.1-flash` (`high`) | `glm-5.3` (`max`) 根因分析<br>`Atria-Dawn-Preview` (`high`) 交叉 | 证据链完整性、排除伪因果、提出替代假设 |
| **4. 极限高复杂度系统级排障** | 跨语言边界崩溃、偶发死锁、平台兼容 | `deepseek-v4.1-flash` (`high`) | `grok-4.7` (`xhigh`) 极限推演<br>`glm-5.3` (`max`) 交叉审查 | 底层机制验证、跨模块副作用、可复现性 |
| **5. 网络链路与代理网关排查** | TLS 握手超时、代理路由、DNS 漂移 | `gemini-3.8-flash` (`medium`) | `glm-5.3` (`high`) 协议分析<br>`grok-4.7` (`high`) 交叉审查 | 中间件改写规则、网络安全策略、边界因果链 |
| **6. 性能瓶颈与高并发诊断** | 线程泄漏、GC 优化、复杂锁争抢 | `deepseek-v4.1-flash` (`high`) | `glm-5.3` (`max`) 分析决策<br>`step-5-preview` (`high`) 交叉 | 堆转储分析、并发时序反例构造、高负载临界值 |
| **7. 核心算法与高难度业务实现** | 计算密集型算法、状态机、规则引擎 | `deepseek-v4.1-flash` (`high`) | `glm-5.3` (`high`) 设计指导<br>`step-5-preview` (`high`) 交叉 | 算法复杂度、极端空值与溢出边界反证 |
| **8. 敏捷补丁与常规功能开发** | 日常 CRUD、工具函数、胶水代码 | `deepseek-v4.1-flash` (`high`)<br>或 `gemini-3.8-flash` (`low`) | `Atria-Dawn-Preview` (`medium`) 交叉审查 | 命名规范、破坏性变更检查、代码坏味道 |
| **9. 前端页面开发与交互逻辑** | React/Vue/Solid 组件、样式布局 | `gemini-3.8-flash` (`medium`) | `deepseek-v4.1-flash` (`high`) 交叉审查 | 状态管理健壮性、TypeScript 类型定义严密性 |
| **10. 红绿对抗质检（RGF 闭环）** | 契约驱动测试、准入红测试、变异反证 | `deepseek-v4.1-flash` (`high`) 实现 | `step-5-preview` (`high`) 质检反证<br>`glm-5.3` (`high`) 争议裁定 | 测试先验红准入、变异体捕获率、断言力度等级 |
| **11. 前端多模态与视觉验收** | 截图对比、DOM 审查、响应式适配 | `gemini-3.8-flash` (`high`) 视觉摄取 | `grok-4.7` (`medium`) 交叉审查 | 跨视口可用性、设计系统还原度、交互体验 |
| **12. 端到端系统集成测试** | 跨服务联调、Playwright 自动化链路 | `step-5-preview` (`high`) 用例编写 | `glm-5.3` (`high`) 交叉审查 | 超时熔断机制、外部依赖打桩隔离度 |
| **13. 代码审查与代码味修剪** | PR Diff 审计、过度封装修剪 | `gemini-3.8-flash` (`high`) 全量通读 | `glm-5.3` (`high`) 深度裁决 | 架构规范、反过度工程（YAGNI）、行为无漂移 |
| **14. 威胁建模与安全漏洞审计** | 注入漏洞、认证越权、反序列化风险 | `deepseek-v4.1-flash` (`high`) 逐行审计 | `grok-4.7` (`high`) 威胁推演<br>`glm-5.3` (`max`) 漏洞交叉 | 攻击者视角的攻击路径推演、输入清洗彻底性 |
| **15. 超大代码库探索与全景理解** | 百万行大仓通读、模块依赖拓扑分析 | `gemini-3.8-flash` (`medium`) 吞吐 | `Atria-Dawn-Preview` (`high`) 交叉归纳 | 调用链准确度、隐藏全局副作用、关键链路覆盖 |
| **16. 前沿技术调研与文献研读** | 方案前瞻对比、学术论文提炼 | `Atria-Dawn-Preview` (`high`) 深度研读 | `grok-4.7` (`high`) 决策定调 | 理论论证严密性、与现有技术栈契合度 |

