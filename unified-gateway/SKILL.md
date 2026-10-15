---
name: unified-gateway
description: >
  统一大模型网关运维：新增渠道、连通性与TTFT/TPS测速、刷新冷却熔断、调整路由权重。当用户显式调用 /unified-gateway 或需要添加大模型渠道、测试模型响应速度与流式吞吐、刷新或复位网关冷却状态、调整候选模型优先级与权重、或同步配置到 Grok CLI 时使用。Use when managing local model gateway channels, benchmarking TTFT and streaming decode TPS, clearing channel cooldowns, tuning routing weights, or updating Grok model configurations. 只负责本机 8320 网关与 Grok 客户端配置；不负责模型权重训练与外部中转服务搭建。
metadata:
  version: "1.0"
  role: standalone
  load-mode: manual
disable-model-invocation: true
---

# unified-gateway — 本地统一大模型网关运维

本技能为独立运维技能（standalone），采用显式手动加载模式（manual），不占用日常 Agent 的默认启动上下文。
专门负责本机大模型网关（监听 `127.0.0.1:8320`）及下游 Grok CLI 的渠道管理、路由编排、性能测速与熔断自愈。

---

## 1. 架构拓扑与关键路径

```
[客户端: Grok CLI / 交互会话]
       │
       ▼ (本地无代理直连 127.0.0.1:8320)
[本地统一网关: gateway.py (PID 守护进程)]
       │
       ├─ 配置驱动: ~/.local/share/model-gateway/config.json (文件 mtime 热重载)
       ├─ 日志审计: ~/.local/share/model-gateway/logs/gateway-YYYY-MM-DD.jsonl
       │
       ▼ (经系统代理 127.0.0.1:7899 或境外直连)
[上游服务提供商: 端砚 / 智谱 / StepFun / XLF / CommandCode / AgentRouter 等]
```

- **网关核心目录**：`/home/dc/.local/share/model-gateway`
- **网关配置路径**：`/home/dc/.local/share/model-gateway/config.json`
- **客户端配置**：`/home/dc/.grok/config.toml`
- **本地代理出口**：直连探测上游接口时走 `http://127.0.0.1:7899`，访问本地网关 `127.0.0.1:8320` 时绕过代理。

---

## 2. 核心操作规范

### 2.1 新增与更新模型渠道 (`config.json -> channels`)

所有渠道配置均位于 `config.json` 的 `channels` 字典中。修改后网关通过文件 `st_mtime` 自动热重载，无需重启进程。

1. **配置字段模板**：
   ```json
   "my_new_channel": {
     "id": "my_new_channel",
     "base_url": "https://api.example.com/v1",
     "key": "sk-xxxxxxxxxxxxxxxx",
     "user_agent": "",
     "expire_timestamp": 0,
     "max_concurrency": 5,
     "extra_headers": {},
     "upstream_protocol": "chat_completions",
     "context_limit": 131072
   }
   ```
2. **字段注意事项**：
   - `upstream_protocol`：支持 `"chat_completions"`（默认）或 `"responses"`（如智谱套餐 Key 仅开放 `/responses` 路径）。
   - `context_limit`：窗口硬上限（Token 数），网关上下文守卫据此自动拦截超窗请求并调度至备用渠道。
   - `expire_timestamp`：渠道免打扰过期时间戳（秒）。长期有效填 `0`。

---

### 2.2 编排模型路由候选与权重 (`config.json -> models`)

网关采用「优先级分层（Priority）+ 同级加权轮询（Weight）」的两级调度策略。

```json
"glm-5.3-flash": {
  "client_model": "glm-5.3-flash",
  "candidates": [
    {
      "channel_id": "xlf_free",
      "remote_model": "glm-5.3-flashx",
      "priority": 0,
      "weight": 100
    },
    {
      "channel_id": "zhipu_official",
      "remote_model": "glm-5.3-flash",
      "priority": 1,
      "weight": 100
    }
  ]
}
```

- **优先级规则**：`priority: 0` 为主用（P0），仅当 P0 渠道全部熔断冷却、超窗或限流时，网关自动降级至 `priority: 1`（P1）。
- **权重分流**：同优先级内按 `weight` 比例分配流量（如双候选各自 50 实现均衡分担）。
- **模型名映射**：通过 `remote_model` 字段实现客户端请求名与上游实际模型标识的动态映射。

---

### 2.3 测试模型连通性与性能体验测速

测速聚焦于**使用体验**，核心度量首字响应（TTFT）与解码输出速率（TPS），分为直连上游与端到端两类测试。

#### A. 上游接口直连探活（绕过网关，排查上游基础可用性）
```bash
python3 -c '
import urllib.request, json
proxy = urllib.request.ProxyHandler({"https": "http://127.0.0.1:7899"})
opener = urllib.request.build_opener(proxy)
req = urllib.request.Request(
    "https://api.example.com/v1/models",
    headers={"Authorization": "Bearer sk-xxx"}
)
with opener.open(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
    print("HTTP Status:", resp.status, "Models count:", len(data.get("data", [])))
'
```

#### B. 网关端到端流式体验测速（测量 TTFT 与流式解码 TPS）
```bash
python3 - << 'EOF'
import urllib.request, json, time

payload = {
    "model": "glm-5.3-flashx",
    "messages": [{"role": "user", "content": "9.11 和 9.9 哪个大？简要说明。"}],
    "stream": True,
    "max_tokens": 800,
    "temperature": 0.1
}
req = urllib.request.Request(
    "http://127.0.0.1:8320/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
t0 = time.time()
ttft = None
chunks = 0
reasoning_chars = 0
content_chars = 0

with urllib.request.urlopen(req, timeout=60) as resp:
    chan = resp.headers.get("X-Gateway-Channel")
    for line in resp:
        line_str = line.decode("utf-8", "replace").strip()
        if not line_str.startswith("data: ") or line_str[6:].strip() == "[DONE]":
            continue
        try:
            d = json.loads(line_str[6:])
            delta = d.get("choices", [{}])[0].get("delta", {})
            r = delta.get("reasoning_content") or ""
            c = delta.get("content") or ""
            if (r or c) and ttft is None:
                ttft = time.time() - t0
            reasoning_chars += len(r)
            content_chars += len(c)
            chunks += 1
        except Exception:
            pass

dur = time.time() - t0
dec_t = max(0.01, dur - (ttft or 0))
tps = chunks / dec_t if ttft else 0.0
print(f"渠道: {chan} | 总耗时: {dur:.2f}s | TTFT: {ttft:.2f}s | 解码 TPS: {tps:.1f}")
print(f"思考字符: {reasoning_chars} | 正文字符: {content_chars}")
EOF
```

---

### 2.4 渠道冷却排查与即时刷新

当渠道返回 429、5xx 或网络异常时，网关将其置入退避冷却队列。

1. **查看渠道状态**：
   ```bash
   curl -s http://127.0.0.1:8320/health | jq '{status, channels: [.channels | to_entries[] | {name: .key, status: .value.status, cooldown_left: .value.cooldown_left_sec}]}'
   ```
2. **错误根因定位原则**：
   - **平台集群全局限流**：返回 `Cluster RPM rate limit exceeded`，属于上游公共入口拥塞，需调大请求间隔。
   - **账户额度耗尽**：返回 `insufficient_quota` 或 `402`，表明需要充值充币。
   - **协议拒批**：返回 `403` 或 `model_not_found`，需检查 `upstream_protocol` 是否需切为 `responses`。
3. **即时清空冷却（平稳重启网关）**：
   在额度已充值或故障恢复后，网关内存中可能残留数千秒退避。通过重启服务快速归零：
   ```bash
   PID=$(pgrep -f "gateway.py" | grep -v $$ | head -n 1)
   [ -n "$PID" ] && kill "$PID" && sleep 1
   nohup python3 /home/dc/.local/share/model-gateway/gateway.py > /home/dc/.local/share/model-gateway/gateway.log 2>&1 &
   sleep 1
   curl -s http://127.0.0.1:8320/health | jq .status
   ```

---

### 2.5 同步配置至 Grok 客户端 (`~/.grok/config.toml`)

在网关新增模型后，需在 Grok 客户端登记以供 CLI 使用：

1. **在 `~/.grok/config.toml` 中添加模型块**：
   ```toml
   [model."glm-5.3-flashx"]
   model = "glm-5.3-flashx"
   base_url = "http://127.0.0.1:8320/v1"
   name = "GLM 5.3 FlashX (Unified GW)"
   api_key = "local-gateway"
   api_backend = "responses"
   reasoning_summary = "none"
   context_window = 1048576
   max_completion_tokens = 131072
   reasoning_efforts = [
       "low",
       "high",
       "max",
   ]
   ```
2. **校验与验证**：
   ```bash
   python3 -c 'import tomli; tomli.load(open("/home/dc/.grok/config.toml", "rb")); print("TOML 校验通过")'
   grok models | grep "glm-5.3-flashx"
   grok --cwd /tmp -m glm-5.3-flashx --max-turns 1 -p "Reply with single word: PONG"
   ```

---

## 3. 日常巡检与自动化回归门禁

任何配置与代码改动后，需进入网关仓库运行单元测试验证整体管道安全：
```bash
cd /home/dc/.local/share/model-gateway && pytest tests/ -q
```
全部通过即确认请求清洗规则、SSE 管道拆包、会话粘滞及指标统计逻辑完好。
