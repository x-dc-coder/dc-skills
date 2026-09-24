---
name: newapi-management
description: >
  管理 NewAPI（QuantumNous/new-api fork，如 xiaolfeng/newapi-fix）的渠道与日志：创建/修改/删除/测试渠道、
  查询使用日志并提取会话原文。当用户需要配置 NewAPI 渠道（建渠道、改渠道、批量管理、多Key轮询、
  系统提示词注入、参数/请求头覆写、上游模型同步）、或排查某次对话/会话问题需要拿 NewAPI 日志分析时使用。
  客户端已内置 fork 版本字段类型铁律，调用者只需提供平铺的渠道配置 dict。
metadata:
  family: agentops
  role: member
  load-mode: manual
  version: 1.0.0
  requires:
    bins: []
disable-model-invocation: true
---
# NewAPI 渠道与日志管理

针对 fork 版 NewAPI（`github.com/QuantumNous/new-api`，镜像如 `xiaolfeng/newapi-fix`）的官方
REST API 操作工具。**客户端封装了 fork 版本的字段类型铁律**——`setting`/`settings`/
`model_mapping`/`param_override` 等 Go 结构体中为 `*string` 的字段必须传 JSON 字符串，
`channel_info` 必须传对象——调用方只需按自然 dict 形式提供配置，转换由客户端自动完成。

## 触发场景

- 「帮我在 NewAPI 建个渠道」「改一下某个渠道的配置/标签/优先级」
- 「批量建一批渠道」「配置多 Key 轮询」「给渠道加系统提示词注入」
- 「某个会话/某次请求有问题，帮我拿 NewAPI 日志看看」
- 「查某个渠道的上游可用模型」「测试渠道连通性」

## 环境

- A 类（统一共享 `.venv`），仅依赖 `requests`（已在 skills 项目的 `pyproject.toml`）。
- 无外部二进制依赖。
- 执行遵循 AGENT.md：`cd ~/projects/dc-skills && uv run python newapi-management/scripts/cli.py ...`

## 凭据发现（优先级从高到低）

1. CLI 参数 `--token` / `--token-file` / `--base-url`
2. 环境变量 `NEWAPI_TOKEN`（令牌明文）、`NEWAPI_TOKEN_FILE`（令牌文件路径）、`NEWAPI_BASE_URL`
3. 默认 `base_url=http://127.0.0.1:3000`；令牌无默认值，缺失时报错引导

> 令牌是 32 字符的 `users.access_token`（用户个人设置生成的系统访问令牌）。
> 首次使用需在 NewAPI 管理面板生成，或由管理员写入 `users` 表。

## 快速用法

```bash
cd ~/projects/dc-skills

# 服务状态（快速探测）
uv run python newapi-management/scripts/cli.py status --token-file /path/to/.api-token

# 渠道列表（默认分页 10，可调大）
uv run python newapi-management/scripts/cli.py channel list --page-size 50 --token-file /path/to/.api-token

# 渠道详情（fork 字段已自动解析为 dict）
uv run python newapi-management/scripts/cli.py channel get 9

# 创建渠道（配置中 dict 形式的 setting 等会被自动转为 JSON 字符串）
uv run python newapi-management/scripts/cli.py channel create --config channel.json
uv run python newapi-management/scripts/cli.py channel create --json '{"type":1,"name":"新渠道","key":"sk-xxx","base_url":"https://api.example.com","models":"gpt-4o","setting":{"system_prompt":"你是助手"}}'

# 更新渠道（必须含 id）
uv run python newapi-management/scripts/cli.py channel update --json '{"id":9,"tag":"新标签"}'

# 测试渠道连通性 / 复制渠道 / 拉取上游模型
uv run python newapi-management/scripts/cli.py channel test 9
uv run python newapi-management/scripts/cli.py channel copy 9
uv run python newapi-management/scripts/cli.py channel fetch-models 9

# 日志检索（多条件）
uv run python newapi-management/scripts/cli.py log list --model-name qwen3.8-max --page-size 5
uv run python newapi-management/scripts/cli.py log list --request-id 202609200349254956229128268d9d63G5iDjCQ
# 日志详情（含 record 完整会话原文）
uv run python newapi-management/scripts/cli.py log get 17178
```

所有命令支持 `--output <文件>` 将 JSON 结果写入文件（避免大日志刷屏）。

## 渠道配置字段速查

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | int | 渠道类型：1=OpenAI 14=Anthropic 43=DeepSeek 26=智谱 16=OpenRouter 等 |
| `name`/`key`/`base_url`/`models` | str | 基础项（逗号分隔模型列表） |
| `group`/`tag`/`remark` | str | 分组/标签/备注 |
| `priority`/`weight`/`auto_ban` | int | 优先级/权重/自动禁用 |
| `model_mapping` | dict | 模型重定向，如 `{"gpt-4":"deepseek-v4"}` |
| `setting` | dict | force_format/thinking_to_content/proxy/pass_through_body_enabled/system_prompt/system_prompt_override |
| `settings` | dict | allow_service_tier/allow_speed/claude_beta_query/upstream_model_update_* |
| `param_override`/`header_override` | dict | 请求参数/请求头覆写 |
| `channel_info` | dict | 多Key：is_multi_key/multi_key_size/multi_key_mode |

> 完整字段与 Go 结构体对照见 `references/api-reference.md`。

## 接口行为要点（避坑）

1. **POST 创建不返回新渠道 id**（官方设计）。需要 id 请 `channel list` 查询，或用 `copy`（返回 id）。
2. **`key` 读取时脱敏**（API 返回空）。真实 key 只能从数据库读。
3. 创建支持三种模式：`single`（单建）、`batch`（key 换行分隔批量建）、
   `multi_to_single`（多 Key 轮询成一个渠道）。
4. **API 写入会自动刷新渠道缓存；数据库直写不会**（需重启或等过期）。
5. 日志接口 `record` 字段是完整请求+响应原文（平均数十万字符），用 `--output` 落盘再分析。

## 数据库兜底

当 API 不满足时（如需读取脱敏的 key、或批量维护），可直接查 PostgreSQL。
本机部署：库 `new-api`（容器 `postgres`），表 `channels` / `logs`。
```bash
docker exec postgres psql -U newapi -d new-api -c 'select id,name,status from channels;'
```

## 测试

```bash
cd ~/projects/dc-skills
# 单元测试（无网络）
uv run pytest newapi-management/scripts -q
# 集成测试（连真实 NewAPI，验证全链路可达）
NEWAPI_TOKEN_FILE=/path/to/.api-token uv run pytest newapi-management/scripts -q
```
集成测试会创建并清理临时渠道（名称 `skill-selftest-<时间戳>`），不留痕迹。
