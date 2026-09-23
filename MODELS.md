# 模型登记

Grok 实际读取 `~/.grok/config.toml`。本文件只记已核实的渠道、协议、窗口、输出上限、思考档位和套餐到期。不记密钥。

改配置里上述字段时，同步改对应行。到期只写 `YYYY-MM-DD`，未知写 `—`。已打开的会话要新开进程才吃到新配置。

当前默认模型 `deepseek-v4.1-flash`，`default_reasoning_effort = high`。Chat Completions 模型只有显式选档才发送 `reasoning_effort`。

核实日期：2026-09-23。

## 官方模型

协议 `responses`，渠道 SpaceXAI（`grok login`）。`grok-4.7-build-fast` 为同代快速档，价格更高。

| 模型 | 上下文 | 最大输出 | 思考档位 | 套餐 | 到期 |
|---|---:|---:|---|---|---|
| grok-4.7 | 500000 | — | low / medium / high / xhigh | — | — |
| grok-4.7-build-fast | 500000 | — | low / medium / high / xhigh | — | — |
| grok-4.6 | 500000 | — | low / medium / high / xhigh | — | — |
| grok-4.5 | 500000 | — | low / medium / high | — | — |

## 自定义模型

| 模型 | 渠道 | 协议 | 上下文 | 最大输出 | 思考档位 | 套餐 | 到期 |
|---|---|---|---:|---:|---|---|---|
| gemini-3.8-flash | `ai.x-lf.com` · `GEMINI_API_KEY` | chat_completions | 1000000 | 65536 | low / medium / high | — | — |
| deepseek-v4.1-flash | `api.commandcode.ai` · `COMMANDCODE_API_KEY` | chat_completions | 1000000 | 393216 | low / high / max | GOAT Plan | 2026-10-11 |
| Atria-Dawn-Preview | NewAPI `127.0.0.1:3000` · `NEWAPI_API_KEY` | chat_completions | 256000 | 65536 | — | 限时免费 | — |
| step-5-preview | shim `127.0.0.1:8321` → StepFun · `STEPFUN_API_KEY` | chat_completions | 1000000 | 65536 | low / medium / high | Plus Plan | 2026-11-19 |
| glm-5.3 | `open.bigmodel.cn/api/v1` · `ZHIPU_API_KEY` | responses | 1048576 | 131072 | low / high / max | Coding Plan（团队） | 2026-12-20 |
| glm-5.3-flash | 同上 | responses | 1048576 | 131072 | low / high / max | Coding Plan（团队） | 2026-12-20 |

上游模型名与目录名不同的只有 DeepSeek：`deepseek/deepseek-v4.1-flash`。GLM 的 `reasoning_summary` 为 `none`。

## 档位

- Gemini 最高是 `high`。`xhigh` 被上游拒绝；`max` 会被网关改写成 `xhigh`，同样失败。
- DeepSeek 官方三档是 `low` / `high` / `max`。`medium` 和 `xhigh` 都折成 `high`。
- GLM 套餐 Key 只走 Responses。`low` / `high` / `max` 可用；`xhigh` 在套餐侧折成 `max`。
- Atria 未声明思考菜单，`--effort` 不生效。
- 菜单以外的档位，CLI 直接拒绝。
