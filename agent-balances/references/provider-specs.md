# agent-balances · Provider 抓取规格与扩展流程

> 本文档记录从 Orca app.asar（1.4.224）逆向提取的各 provider 抓取细节，以及新增
> provider 的扩展流程。SKILL.md 只保留工作流；改动抓取逻辑时同步更新此处。

## 逆向来源与方法

- Orca 主进程 bundle：`/mnt/c/Users/32841/AppData/Local/Programs/orca/resources/app.asar`
  （`npx @electron/asar extract` 解包；升级 Orca 后字段可能漂移，需重新核对）。
- 关键函数：grok=`Tko/Cko/_ko/vko`，codex=`bTo/oTo/vTo/sTo`，cursor=`keo/lTe/Zko/Rko`，
  claude=`FDo/q8`，kimi=`XMo`，zcode=`q9a/K9a/V9a`。
- Orca 自身**不开放**额度读取面：CLI 无 usage 命令（`orca --help`+`agent-context` 已核实）、
  daemon 仅终端宿主协议、额度不落盘（profile/leveldb/IndexedDB 均无）、主进程本地 HTTP
  仅 OIDC 回调与浏览器调试器。

## 各 provider 请求规格

### grok
- 读 `~/.grok/auth.json`，取 key 以 `https://auth.x.ai` 开头的条目。
- `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`
  - `Authorization: Bearer <key>`；`X-XAI-Token-Auth: xai-grok-cli`；`Accept: application/json`；`x-userid: <user_id>`
- 响应（外层可能包 `config`）：`creditUsagePercent`（weekly 已用百分比）、
  `currentPeriod.{start,end}`、`billingPeriodStart/End`、`monthlyLimit/used`（`{val}` 字符串
  数值对象）、`onDemandCap/onDemandUsed`、`prepaidBalance`、`subscriptionTier`。
- Orca 新鲜度门槛：`expires_at - now > 5min`；base URL 可用 `GROK_CLI_CHAT_PROXY_BASE_URL` 覆盖。

### codex
- 读 `~/.codex/auth.json` 的 `tokens.access_token` / `tokens.account_id`。
- `GET https://chatgpt.com/backend-api/wham/usage`
  - `Authorization: Bearer`；`User-Agent: codex-cli`；`OpenAI-Beta: codex-1`；`originator: Codex Desktop`；`ChatGPT-Account-Id: <account_id>`
- 响应：`plan_type`；`rate_limit.primary_window/secondary_window` 各含
  `used_percent`、`limit_window_seconds`、`reset_at`（**epoch 秒**）。
- primary→session（默认 300 分钟），secondary→weekly（默认 10080 分钟）。

### cursor
- 复制 `<WIN>/AppData/Roaming/Cursor/User/globalStorage/state.vscdb`（含 -wal/-shm）到临时目录后：
  `SELECT key, value FROM ItemTable WHERE key IN ('cursorAuth/accessToken','cursorAuth/cachedEmail','cursorAuth/stripeMembershipType','cursorAuth/stripeSubscriptionStatus')`
- accessToken 是 JWT（sub/exp）；cookie = `WorkosCursorSessionToken=<sub>%3A%3A<raw token>`。
- `GET https://cursor.com/api/usage-summary`（`Cookie`、`Accept: application/json`、
  `Origin/Referer: https://cursor.com`，redirect manual）。401 = 会话过期。
- 响应：`individualUsage.plan.{enabled,used,limit,totalPercentUsed,autoPercentUsed,apiPercentUsed}`、
  `individualUsage.onDemand.{enabled,totalPercentUsed}`、`membershipType`、`isUnlimited`、
  `billingCycleStart/End`。分桶名：Cursor Models（auto）/ Other Models（api）/ On-demand。

### claude
- 读 `.claude/.credentials.json`（WSL 优先，Windows 侧兜底）的 `claudeAiOauth.accessToken`。
- `GET https://api.anthropic.com/api/oauth/usage`
  - `Authorization: Bearer`；`anthropic-beta: oauth-2025-04-20`；`User-Agent: claude-code/2.1.0`
- 响应：`five_hour`→session(300)；`seven_day`→weekly(10080)；各含
  `utilization` 或 `used_percentage`、`resets_at`（ISO 或 epoch；>1e10 视为 ms）。
  另有 `fable_weekly`/`limits[weekly_scoped]`、`extraUsage`。

### kimi
- 读 `~/.kimi-code/credentials/kimi-code.json` 的 `access_token`（base 可用
  `KIMI_CODE_BASE_URL` 覆盖，默认 `https://api.kimi.com/coding/v1`）。
- `GET <base>/usages`（`Authorization: Bearer`、`Accept: application/json`）。

### zcode / GLM Coding Plan
- 本地配置优先：`~/.zcode/cli/config.json` 的 `apiKey`+`baseURL`（host 白名单：
  api.z.ai / open.bigmodel.cn / dev.bigmodel.cn）。
- Orca 存储的 key 解密链（本机 2026-10-15 实测）：
  1. 文件 `<WIN>/.orca/zcode-plan-api-key.enc`，信封 `orca-zcode-plan-api-key:v1:encrypted:<base64>`。
     **注意**：这是 Windows profile 下的 `.orca`（Electron homedir），不是 WSL 的 `~/.orca`。
  2. payload = Chromium safeStorage 格式：`v10` + 12B IV + 密文 + 16B GCM tag。
  3. AES 密钥：`<WIN>/AppData/Roaming/orca/Local State` 的 `os_crypt.encrypted_key`（base64）=
     `"DPAPI"` 5 字节前缀 + CryptProtectData 输出——**从偏移 5 起** DPAPI Unprotect
     （CurrentUser，经 powershell）得 32 字节密钥。偏移 9/25 均报"参数无效"。
  4. AES-256-GCM 解密（node crypto；pwsh 7 直接调 `AesGcm.Decrypt` 会因重载绑定报
     nonce 尺寸错，故用 node）。
- 站点映射：`{zai: https://api.z.ai, bigmodel: https://open.bigmodel.cn}`（Orca 设置
  `zcodePlanSite`，读 profile-state.db 的 settings 文档）。
- `GET <base>/api/monitor/usage/quota/limit`，头 `Authorization: <裸 key>`（**无 Bearer**）、
  `Accept-Language: en-US,en`、`Content-Type: application/json`。
- 响应：`{success, code, msg, data:{level, limits[]}}`；limit 含
  `type`(TOKENS_LIMIT/CREDIT_LIMIT/TIME_LIMIT)、`unit`、`number`、`usage`(上限)、
  `currentValue`(已用)、`remaining`、`percentage`、`nextResetTime`(ms)。
- 窗口分钟数 = `unit_minutes × number`，映射 `{1:1440, 3:60, 5:1, 6:10080}`；
  特判：`TIME_LIMIT` 且 unit=5 且 number=1 → 720（12 小时窗）。300→session，10080→weekly。

### gemini（未接线，规格备好）
1. `POST https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist`，
   body `{metadata:{ideType:'GEMINI_CLI',pluginType:'GEMINI'}}` → `cloudaicompanionProject`。
2. `POST https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`，
   body `{project}` → buckets：`{modelId, remainingFraction, resetTime}`，
   usedPercent = round((1-remainingFraction)*100)，60 分钟窗，按模型分桶。

### minimax / opencode-go（未接线）
- key 若配置，与 zcode 同链可读：`<WIN>/.orca/minimax-api-key.enc`（前缀
  `orca-minimax-api-key:v1:`）、`<WIN>/.orca/opencode-go-api-key.enc`。
- minimax 用量：`/v1/api/openplatform/coding_plan/remains`（海外 platform.minimax.io /
  国内 platform.minimaxi.com）；控制台用量走浏览器 cookie（`_token` 等）。
- opencode-go：`GET https://opencode.ai/zen/go/v1/usage`（响应是双重 JSON 字符串，
  `access.meters.{fiveHour,week,month}`；401/403 + EntitlementError = 无订阅）。

## 新增 provider 的扩展流程

1. 升级/变动 Orca 后先解包 asar，grep 目标 provider 的 fetcher 函数与端点常量。
2. 确认凭据在本机的实际位置（`ls` + 读字段），评估可达性（代理/时效）。
3. 在 `cli.py` 增加 `xxx_balance()`，返回统一 `result()` 结构；失败只降级不抛出。
4. 登记进 `PROVIDERS` 字典；SKILL.md 覆盖矩阵加一行；本文件补请求规格。
5. 跑 `uv run python agent-balances/scripts/cli.py --provider xxx` 实测，再全量回归。
6. 提交前过 `validate.py` 与 `skillctl lint`。

## 采集接入（monitor-panel）

```bash
# 定时采集示例（crontab -e）：每 10 分钟落一次 JSON
*/10 * * * * cd ~/projects/dc-skills && uv run python agent-balances/scripts/cli.py \
  --output >/dev/null 2>&1
```

产物路径 `<cwd>/skills-output/agentops/agent-balances/<时间戳>/agent-balances.json`；
采集侧读 `providers[].status` 判别可用性，读 `session/weekly/monthly.usedPercent`
取用量，`resetsAt` 为 UTC ISO（展示时转本地）。
