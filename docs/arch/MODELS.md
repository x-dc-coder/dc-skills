# 模型登记

Grok 实际读取 `~/.grok/config.toml`。本文件只记已核实的渠道、协议、窗口、输出上限、思考档位和套餐到期。不记密钥。

改配置里上述字段时，同步改对应行。到期只写 `YYYY-MM-DD`，未知写 `—`。已打开的会话要新开进程才吃到新配置。

当前默认模型 `deepseek-v4.1-flash`，`default_reasoning_effort = max`（该模型菜单含 `max`）。Chat Completions 只有显式选档才发送 `reasoning_effort`（2026-09 测量，本次未重测）。

核实日期：2026-10-01。Gemini 档位结论仍是 2026-09-30 的测量。DeepSeek 改回 Chat Completions。Step 改回本地 shim 的 Chat Completions。Gemini、Atria、GLM 仍是 Responses。套餐到期日沿用原登记，本次未复核。

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
| Atria-Dawn-Preview  | `api.atria-asi.ai/v1` · `ASI_API_KEY`               | responses         | 256000  | 65536  | low / medium / high | 限时免费            | —          |
| step-5-preview      | `127.0.0.1:8321/v1` · `STEPFUN_API_KEY`             | chat_completions  | 1000000 | 65536  | low / medium / high | Plus Plan       | 2026-11-19 |
| glm-5.3             | `open.bigmodel.cn/api/v1` · `ZHIPU_API_KEY`         | responses         | 1048576 | 131072 | low / high / max    | Coding Plan（团队） | 2026-12-20 |
| glm-5.3-flash       | 同上                                                  | responses         | 1048576 | 131072 | low / high / max    | Coding Plan（团队） | 2026-12-20 |


上游模型名与目录名不同的只有 DeepSeek：`deepseek/deepseek-v4.1-flash`。GLM 的 `reasoning_summary` 为 `none`。Gemini、Atria、GLM 用 Responses 默认的 `concise`，网关接受。DeepSeek 与 Step 走 Chat Completions。

## 档位

- Gemini（Responses，2026-09-30）：菜单只放 `low` / `medium` / `high`。上游有效档位就是这三档。`none` 返回 200、推理 token 为 0，未进菜单。`minimal` 与 `xhigh` 返回 500。`max` 被网关改写成 `xhigh` 后同样 500。`previous_response_id` 续写返回 200 但不保留上文；Grok 不发这个字段，把上一轮 output 放回 input，两轮回忆可用。选 Responses 是因为推理和 `function_call` 是独立项。Chat Completions 也能完成同样调用，响应里另有 Grok 不认识的 `thinking_signature`。DSH 里同名渠道 `xlf-responses` 仍是 `openai-completions`，本次只改了 Grok。请求走代理 `http://127.0.0.1:7890`：2026-10-01 直连 TLS 收不到字节，约 50 秒超时；经代理的握手约 50ms。客户端里几十秒的回合是模型和上下文时间。本次未改 Gemini 配置。
- DeepSeek（Chat Completions，2026-10-01）：菜单 `low` / `high` / `max`。`base_url` 为 `https://api.commandcode.ai/provider/v1`，`api_backend = chat_completions`，上游模型 ID 保持 `deepseek/deepseek-v4.1-flash`。Command Code 官方 Provider 文档把开放模型放在 `POST /provider/v1/chat/completions`（示例为 `deepseek/deepseek-v4-flash`）；Claude 只用 `POST /provider/v1/messages`。Grok 1.0.44 读不了该网关的 Responses 流：会出现不认识的 `response.reasoning.delta`，同一次响应里 `annotations` 或 `text` 也会缺字段。`deepseek/deepseek-v4-flash-fast` 与 `Qwen/Qwen3.8-Flash` 打 `/responses` 会 400，网关要求改走 `/chat/completions`。流量走 `http://127.0.0.1:7890`。`api.commandcode.ai` 放进 `no_proxy` 后会改走 TUN 直连并超时。代理上的 TLS 仍可能一次 EOF，重试可以通。改成 Chat Completions 后，两次无头 `grok -p`（只要单词 pong，effort low）都返回了 pong，墙钟 17.8s 与 8.6s，没有序列化错误。同句下 `max` 推理 token 高于 `low` / `high`、`xhigh` 与 `high` 同级，是改协议前在 Responses 上测的，Chat Completions 本次未重测档位差。
- GLM 套餐 Key 只走 Responses，直连 `open.bigmodel.cn`。`low` / `high` / `max` 可用；`xhigh` 在套餐侧折成 `max`。
- Step（Chat Completions，2026-10-01）：`base_url` 为 `http://127.0.0.1:8321/v1`，`api_backend = chat_completions`，模型 ID `step-5-preview`。shim 在 `~/.local/share/stepfun-shim/server.py`，由 `~/.bashrc` 在 8321 空闲时拉起，转发到 `https://api.stepfun.com/step_plan`（Step Plan 的 Chat Completions / Messages）。公网 `https://api.stepfun.com/v1/responses` 的流含 `response.reasoning_part.added` 与 `response.reasoning_part.done`，Grok 1.0.44 不认识，整条流失败。直连 Messages 发出 thinking 的 `content_block_start` 时没有 `signature`，客户端报 missing field `signature`。shim 会补上空的 `signature`，并去掉空的 tool_call `id` / `type` / `name` 以及 `thinking=adaptive`；同一条会触发思考的提示经 shim 可以返回。保存的条目停在 Chat Completions。流式工具增量里的空 `id` 仍由 shim 清掉。
- Atria（Responses，2026-10-01）：官方 `https://api.atria-asi.ai/v1`，文档档位 `low` / `medium` / `high`，`concise` 可用。只收文本。短回复可用。原始流里也出现过 `reasoning_part` 事件，本次未改配置。本机 NewAPI `127.0.0.1:3000` 未监听。
- 菜单以外的档位，CLI 直接拒绝。
