# NewAPI REST API 完整参考

> 提取方式：从部署二进制（`xiaolfeng/newapi-fix:latest`，源码 `github.com/QuantumNous/new-api`）
> 逆向前端 axios 调用 + gin 路由注册串，精确对应当前运行版本，211+ 条管理接口。
> 客户端 `scripts/newapi_client.py` 已封装常用接口；未封装的可用 `_request()` 直接调用。

## 鉴权

```
Authorization: Bearer <令牌>
```
- access_token（长期）：用户「个人设置」生成的令牌，存 `users.access_token`（character(32)）
- session token：`POST /api/user/login` 所得（过期失效）
- 细粒度 RBAC：`authz_roles`/`casbin_rule` + `admin_permissions[resource][action]`；SUPER_ADMIN 全放行

## model.Channel 结构体与字段类型（源码权威）

```
Id, Type, Key, OpenAIOrganization, TestModel, Status, Name, Weight,
CreatedTime, TestTime, ResponseTime, BaseURL, Other, Balance,
BalanceUpdatedTime, Models, Group, UsedQuota, ModelMapping(*string),
StatusCodeMapping(*string), Priority, AutoBan, OtherInfo, Tag,
Setting(*string), ParamOverride(*string), HeaderOverride(*string), Remark,
ChannelInfo(结构体, gorm type:json), OtherSettings(json tag "settings"), Keys(json:"-")
```

**写入类型对照**（客户端自动转换，直接调 API 时必须遵守）：

| JSON 字段 | Go 类型 | 传参 | 说明 |
|---|---|---|---|
| `setting` | `*string` | JSON 字符串 | force_format/thinking_to_content/proxy/pass_through_body_enabled/system_prompt/system_prompt_override |
| `settings` | `string` | JSON 字符串 | allow_service_tier/allow_inference_geo/allow_speed/claude_beta_query/disable_task_polling_sleep/upstream_model_update_* |
| `model_mapping` | `*string` | JSON 字符串 | 模型重定向 |
| `status_code_mapping` | `*string` | JSON 字符串 | 状态码映射 |
| `param_override` | `*string` | JSON 字符串 | 参数覆写 |
| `header_override` | `*string` | JSON 字符串 | 请求头覆写 |
| `channel_info` | `ChannelInfo` | **JSON 对象** | is_multi_key/multi_key_size/multi_key_status_list/multi_key_polling_index/multi_key_mode |
| `other_info` | `string` | 字符串 | 状态原因（系统维护写） |

`ChannelInfo` 内嵌字段：`is_multi_key`、`multi_key_size`、`multi_key_status_list`、
`multi_key_disabled_reason`、`multi_key_disabled_time`、`multi_key_polling_index`、`multi_key_mode`。

## 渠道接口（33 个）

| 方法 | 路径 | 客户端方法 | 功能 |
|---|---|---|---|
| GET | `/api/channel/` | list_channels | 列表（分页/keyword/group/tag/status） |
| GET | `/api/channel/search` | search_channels | 搜索 |
| GET | `/api/channel/:id` | get_channel | 详情（key 脱敏） |
| POST | `/api/channel` | create_channel | 创建（须包 `{mode, channel}`） |
| PUT | `/api/channel/` | update_channel | 更新（平铺，含 id） |
| DELETE | `/api/channel/:id` | delete_channel | 删除 |
| POST | `/api/channel/status/batch` | set_channel_status | 改状态（ids+status，1=启用 2=手动禁用 3=自动禁用） |
| ~~PUT~~ | ~~`/api/channel/:id/status`~~ | — | **该 fork 未注册此路由**（返回 Invalid URL），单个改状态用上面的 batch 传单元素 |
| POST | `/api/channel/batch` | — | 批量创建 |
| POST | `/api/channel/batch/tag` | — | 批量打标签 |
| POST | `/api/channel/copy/:id` | copy_channel | 复制（返回新 id） |
| POST | `/api/channel/fix` | — | 修复异常渠道 |
| DELETE | `/api/channel/disabled` | — | 删除全部禁用渠道 |
| GET | `/api/channel/test/:id` | test_channel | 连通性测试 |
| GET | `/api/channel/update_balance/:id` | update_balance | 刷新余额 |
| GET | `/api/channel/fetch_models/:id` | fetch_models | 拉取上游模型 |
| POST | `/api/channel/fetch_models` | — | 批量拉取 |
| GET | `/api/channel/models` | — | 全部渠道模型聚合 |
| GET | `/api/channel/models_enabled` | — | 启用模型 |
| GET | `/api/channel/ops` | — | 渠道操作记录 |
| POST | `/api/channel/multi_key/manage` | — | 多 Key 管理 |
| POST | `/api/channel/upstream_updates/detect` | — | 检测上游模型更新 |
| POST | `/api/channel/upstream_updates/detect_all` | — | 批量检测 |
| POST | `/api/channel/upstream_updates/apply` | — | 应用上游更新 |
| POST | `/api/channel/upstream_updates/apply_all` | — | 批量应用 |
| PUT | `/api/channel/tag` | — | 标签增改 |
| POST | `/api/channel/tag/enabled` | — | 按标签启用 |
| POST | `/api/channel/tag/disabled` | — | 按标签禁用 |
| GET | `/api/channel/tag/models` | — | 标签下模型 |
| DELETE | `/api/channel/ollama/delete` | — | Ollama 模型删除 |
| POST | `/api/channel/ollama/pull/stream` | — | Ollama 拉模型（SSE） |
| POST | `/api/channel/:id/key` | — | 渠道 Key 操作 |
| POST | `/api/channel/:id/codex/usage/reset` | — | Codex 用量重置 |

## 创建模式（AddChannelRequest）

```go
type AddChannelRequest struct {
    Mode                      string                `json:"mode"`
    MultiKeyMode              constant.MultiKeyMode `json:"multi_key_mode"`
    BatchAddSetKeyPrefix2Name bool                  `json:"batch_add_set_key_prefix_2_name"`
    Channel                   *model.Channel        `json:"channel"`
}
```
- `single`：单建
- `batch`：key 按换行拆分，批量创建多个渠道
- `multi_to_single`：多 Key 轮询合并为一个渠道

校验（validateChannel）：channel 不能为 nil、key 不能为空（add）、
NewAPI/vLLM/SGLang 类型必须有 base_url、TaskPlugin 类型需要插件 key。

### 状态更新的内存缓存陷阱（已实测）

`common.MemoryCacheEnabled` 为 true 时，`model.UpdateChannelStatus` 会先
`CacheGetChannel(id)`，**缓存未命中直接 return false（不更新数据库）**。
而新建渠道（`BatchInsertChannels`）只写库不刷新缓存，`GetChannelById` 也不回填缓存。
后果：**新建渠道后立即改状态会静默失败**（changedCount=0，DB 未变）。

可靠触发缓存全量刷新（`InitChannelCache`）的操作：任意 `PUT /api/channel/`
（整体更新，即使只改 remark）。客户端 `set_channel_status` 已内置自动补偿：
首次 batch 若 changedCount=0 且当前状态确与目标不同，先 PUT 触发刷新再重试。

> 该 fork 有 18 处 `InitChannelCache` 调用点（删除/标签/更新等），均会顺带刷新。

## 日志与数据

| 方法 | 路径 | 客户端方法 | 功能 |
|---|---|---|---|
| GET | `/api/log/` | list_logs / get_log | 使用日志（admin；`/api/log/self` 查本人） |
| GET | `/api/log/channel_affinity_usage_cache` | — | 渠道亲和缓存 |
| GET | `/api/tool_log/` | — | 工具调用日志（`/self`） |
| GET | `/api/data/` | get_data | 数据看板（`/self`） |
| GET | `/api/data/flow` | — | 流量趋势（`/self`） |
| GET | `/api/data/users` | — | 用户维度数据 |
| GET | `/api/token_record/daily` | — | 每日 Token 消耗 |
| GET | `/api/token_record/recent` | — | 最近 Token 记录 |
| GET | `/api/perf-metrics` | — | 性能指标明细 |
| GET | `/api/perf-metrics/summary` | — | 性能汇总 |

日志检索维度：`channel_id`/`model_name`/`request_id`/`user_id`/`token_name`/
`group`/`ip`/时间戳范围/`type`（2=消费 3=充值 5=管理 7=错误）/分页。
`logs` 表字段：`record`（完整请求+响应原文，均长数十万字符）、`full_log`（上游错误）、
`other`（含 image_recognize 等 fork 扩展元数据）。

> bamboo 调试：是底层可观测库（`bamboo.Tool`/`bamboo.Usage`，请求序列化+拦截器），
> 产物经 `logs.record`/`full_log` 与 `perf-metrics` 暴露，非独立配置项。
> 网络检索/图片识别：渠道能力扩展，配置仍在 `setting`/`settings` 内。

## 其余管理域（简表）

- **异步任务**：`/api/mj`、`/api/task`（均 `/self`）、`/api/system-task/{list,current,:id,log-cleanup}`
- **令牌**：`/api/token/` 增删改查 + `/search` + `/batch` + `/batch/keys` + `/auto-groups`
- **模型**：`/api/models/` 增删改查 + `/search` + `/missing` + `/sync_upstream[/preview]`
- **用户**：`/api/user/` 全套（含 2FA/passkey/OAuth/会话管理）
- **系统设置**：`GET/PUT /api/option/`、`POST /api/option/rest_model_ratio`
- **分组**：`GET /api/group/`
- **兑换码**：`/api/redemption/` 全套
- **性能**：`/api/performance/{stats,logs,gc,reset_stats,disk_cache}`
- **实例**：`/api/system-info/instances`、`DELETE /stale-instances`
- **部署**：`/api/deployments/`（GPU 部署全套）
- **统计**：`/api/rankings`、`/api/pricing`、`/api/uptime/status`
- **公共（免鉴权）**：`/api/status`、`/api/notice`、`/api/about`、`/api/verification`、`/api/setup`

## OpenAI 兼容接口（终端用户侧）

`/v1/chat/completions`、`/v1/completions`、`/v1/embeddings`、`/v1/models`、
`/v1/images/generations`、`/v1/audio/*`、`/v1/messages`、`/v1/responses`、
`/v1/rerank`、`/mj/*` —— 用 Token 的 sk-* Key 调用，非管理 API。

## 数据库兜底

API 不足时（读脱敏 key、批量维护等）直连 PostgreSQL：
```bash
docker exec postgres psql -U newapi -d new-api -c 'select id,name,key from channels;'
```
注意：**数据库直写不刷新渠道缓存**，改完需重启容器或等缓存过期；走 API 则自动刷新。
