---
name: proxy-ops
description: >
  当用户要排查或切换本机梯子、mihomo、Taishan 或系统代理时使用。
  只处理 7899 与 7890 出口、mihomo 规则热加载，以及系统代理被改写后的看门狗复位。
metadata:
  version: "1.0"
  role: standalone
  load-mode: manual
disable-model-invocation: true
---
# proxy-ops — 本机梯子网络管理与节点切换

本技能为独立运维技能（standalone），采用手动按需加载模式（manual），不进入 Agent 默认启动上下文。
只处理本机 7899（mihomo）与 7890（泰山）出口、mihomo 规则热加载，以及 Windows 系统代理被改写后的看门狗复位。

---

## 1. 链路拓扑与端口职责

本机梯子体系由两个独立程序组成，mihomo 作为统一分流内核，泰山客户端作为节点出口：

| 程序 | 位置 | 角色 | 监听端口 | 说明 |
|---|---|---|---|---|
| **mihomo** | `D:\mihomo\mihomo.exe` | 规则分流 + 唯一代理入口 | 7899（代理）/ 9090（面板+API） | 纯后台进程，无原生 GUI，Web 面板为 zashboard |
| **泰山 TaiShanNet** | `D:\TaishanNet2\` | 流量出口，选节点 | 7890（HTTP 代理） | 外部托管，路径由安装器写死，**严禁移动或改动** |

流量流向链路：
```
所有程序 / 终端 / WSL → 系统代理 127.0.0.1:7899 (mihomo)
                                ↓ 按规则分流
            ├─ 直连规则命中 → 本地 DIRECT 直出（国内站点）
            └─ 其余流量 → PROXY 组 → TaiShan-Outbound → 127.0.0.1:7890 (泰山) → 节点出口
```

- WSL 侧环境变量（`~/.bashrc`）：`http_proxy/https_proxy/HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7899`，`no_proxy/NO_PROXY=localhost,127.0.0.1,::1`。
- Windows 侧用户环境变量及系统代理（WinInet）：统一收敛至 `127.0.0.1:7899`。
- API 鉴权：`127.0.0.1:9090` 外部控制器使用 Bearer secret（明文在 `D:\mihomo\config.yaml` 末尾与 `state.json`）。

---

## 2. 添加/修改分流规则步骤（四步法）

当国内域名被误代理导致访问异常，或需要新增直连规则时，严格执行以下流程：

```bash
# 步骤 1：备份当前配置（⚠ 名字必须用 manual- 前缀，严禁使用 config- 前缀，否则会被轮转自动清理）
cp /mnt/d/mihomo/config.yaml /mnt/d/mihomo/backups/manual-$(date +%Y%m%d-%H%M%S).yaml

# 步骤 2：编辑 /mnt/d/mihomo/config.yaml 中的 rules: 列表
#    ⚠ 必须同步修改 /mnt/d/mihomo/scripts/taishan-sub-refresh.py 中的 RULES 列表，防止未来刷新丢失

# 步骤 3：语法预检（退出码为 0 才允许继续）
/mnt/d/mihomo/mihomo.exe -t -d /mnt/d/mihomo -f /mnt/d/mihomo/config.yaml

# 步骤 4：热加载（无需重启进程）
SEC=$(grep '^secret:' /mnt/d/mihomo/config.yaml | awk '{print $2}' | tr -d '"\r')
curl -s -X PUT -H "Authorization: Bearer $SEC" -H "Content-Type: application/json" \
  -d '{"path":"D:\\mihomo\\config.yaml"}' 'http://127.0.0.1:9090/configs?force=true'
```

### 规则编写规范
1. **顺序即优先级**：直连规则（`DIRECT`）必须置于 `GEOSITE,geolocation-!cn,PROXY` 及兜底规则之前。
2. **`DOMAIN-SUFFIX` 按后缀严格整体匹配**：例如 `intern-ai.org` 不会覆盖 `intern-ai.org.cn`；`npmjs.com` 不会覆盖 `npmjs.org`，跨后缀域名必须分别添加。
3. **改完旧连接不换路由**：mihomo 仅在 TCP 连接建立时评估规则；长连接保持旧路由，客户端需要硬刷新（Ctrl+Shift+R）或重启对应进程。

---

## 3. 四路排查法（Site Unreachable 定位）

当用户报告某个站点访问超时、被拦截或 Cloudflare/DataDome 阻断时，按以下四路对比排查：

```bash
TARGET_URL="https://example.com"

# 1. DNS / 直连探测（绕过代理）
curl -I -m 5 --noproxy '*' "$TARGET_URL"

# 2. 经由 mihomo (7899) 探测
curl -I -m 5 -x http://127.0.0.1:7899 "$TARGET_URL"

# 3. 经由泰山 (7890) 探测
curl -I -m 5 -x http://127.0.0.1:7890 "$TARGET_URL"

# 4. 对照组探测（确认代理链路自身是否存活）
curl -I -m 5 -x http://127.0.0.1:7899 https://www.baidu.com
curl -s -x http://127.0.0.1:7899 https://www.cloudflare.com/cdn-cgi/trace
```

### 连接与分流实时验证
**切勿依赖 `logs/mihomo.log`**（运行时日志级别被调高，文件不写入）。通过 REST API 实时查询连接状态与命中规则：
```bash
SEC=$(grep '^secret:' /mnt/d/mihomo/config.yaml | awk '{print $2}' | tr -d '"\r')
curl -s -H "Authorization: Bearer $SEC" http://127.0.0.1:9090/connections | jq '.connections[] | {host: .metadata.host, rule: .rule, rulePayload: .rulePayload, chains: .chains}'
```

---

## 4. 系统代理看门狗守护

泰山客户端每次重启（或崩溃拉起）时，都会强制重写 Windows 注册表，将系统代理篡改为 `127.0.0.1:7890`（且其客户端内没有关闭选项）。
本机部署了计划任务看门狗进行守护纠正：

- **任务名称**：`mihomo-proxy-watchdog`（用户级计划任务，无需管理员权限）。
- **运行频率**：每 2 分钟执行一次检查。
- **触发机制**：通过 `wscript.exe "D:\mihomo\scripts\proxy-watchdog.vbs"` 静默启动 PowerShell（`SW_HIDE`），杜绝终端黑框闪烁弹窗。
- **纠正目标**：将 `ProxyServer` 重设为 `127.0.0.1:7899`，`ProxyEnable` 设为 1，并调用 `InternetSetOption` 刷新 WinInet。
- **管理命令**（Windows PowerShell）：
  ```powershell
  # 查看状态
  Get-ScheduledTask -TaskName 'mihomo-proxy-watchdog'
  # 手动触发一次
  Start-ScheduledTask -TaskName 'mihomo-proxy-watchdog'
  # 查看日志
  Get-Content "D:\mihomo\scripts\proxy-watchdog.log" -Tail 20
  ```

---

## 5. 已知踩坑与绝对禁止项

1. **绝对禁止随意运行 `taishan-sub-refresh.py`**：
   - 现有 `D:\mihomo\config.yaml` 是手工维护的链路拓扑（走 `TaiShan-Outbound` 7890 链式代理 + fake-ip DNS）。
   - `taishan-sub-refresh.py` 会生成直连订阅节点拓扑（83 个节点 + normal DNS），直接运行会彻底覆盖当前配置！
2. **严禁在 `config.yaml` 中硬编码 `interface-name`**：
   - 曾发生硬编码 `interface-name: WLAN`，在主机切换至以太网卡（`以太网 2`）后导致全部 `DIRECT` 规则出站 100% 失败并引发连锁熔断。必须保持不设置 `interface-name`，让 mihomo 自动跟随操作系统默认路由。
3. **严禁改动或迁移 `D:\TaishanNet2\` 目录**：
   - 泰山客户端路径硬编码于 Windows 服务和注册表 Run 项中，移动将导致链路断裂。
4. **日志不更新属于正常现象**：
   - 验证规则命中务必通过 `GET /connections` API，严禁以 `logs/mihomo.log` 的修改时间或内容作为判定依据。
