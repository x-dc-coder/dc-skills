# Windows SSH 特权与 Agent 门禁通道（Channel S）

> **适用场景**：系统级特权操作（High Integrity Token）、彻底杜绝环境变量污染、驱动 Windows 原生 Agent（如 `grok.exe`）、真实桌面 UI 自动化与 Word COM 门禁测试（经由 `schtasks` 投递至 Session 1）。

---

## 1. 架构拓扑与双态执行模型

WSL Linux 客户端通过 TCP（端口 22，`127.0.0.1`）连接至 Windows 原生 OpenSSH 服务（`sshd.exe`，以 `LocalSystem` 运行在 Session 0）。管理员登录后直接获得**完整管理员令牌**（High Integrity Level，不产生 UAC 降权）。

由于 Windows Session 0 属于非交互式服务会话，不存在可见桌面窗口站 `WinSta0\Default`，系统按两态分流执行：

```
[WSL2 Linux 端]
       │
       ▼ (ssh -i ~/.ssh/wp_win_sshd_ed25519 32841@127.0.0.1)
[Windows sshd (Session 0, High Integrity Token)]
       │
       ├─────────────────────────────────┬─────────────────────────────────┐
       ▼                                 ▼                                 ▼
【模式 1：特权 CLI 直跑】         【模式 2：GUI 跨会话投递】        【模式 3：Windows Agent】
直接执行 Windows CLI/pytest       schtasks /Create /Run             调用 grok.exe -p
用于非 GUI 测试/服务/系统管理      投递至 Session 1 (活动桌面)       执行环境巡检与自主门禁
       │                                 │                                 │
       ▼                                 ▼                                 ▼
 实时 stdout/stderr 回传           输出写入共享 NTFS (E:\...)        返回结构化 JSON 信封
                                         │                                 │
                                         └────────────────┬────────────────┘
                                                          ▼
                                            [WSL 独立解析检验真值]
                                            直接读取 /mnt/e/.../junit.xml
```

---

## 2. 凭据与服务端加固规范

### 2.1 专用密钥与编码规范（硬性铁律）

* **WSL 客户端密钥**：`~/.ssh/wp_win_sshd_ed25519`（专用 Ed25519 密钥对）。
* **Windows 服务端公钥库**：`C:\ProgramData\ssh\administrators_authorized_keys`。
* **编码铁律**：该文件必须采用 **ASCII / UTF-8 无 BOM 编码**。PowerShell 5.1 默认重定向或 `Out-File` 采用 UTF-16LE 编码，会导致 OpenSSH 无法解析并静默拒绝所有公钥认证。
* **写入命令（单行分号，防止控制台粘贴倒序）**：
  ```powershell
  $f='C:\ProgramData\ssh\administrators_authorized_keys'; $k='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL33ooSh6hVaWMO2sKAfE4GsiEDw25S7Eh0eyJNaaQ8+ wp-wsl-to-windows-sshd'; Set-Content -Path $f -Value $k -Encoding ascii; icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null; Restart-Service sshd
  ```

### 2.2 sshd_config 安全基线

在 `C:\ProgramData\ssh\sshd_config` 的 `Match Group administrators` 之前配置安全策略：

```sshd_config
# --- SSH hardening ---
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
MaxAuthTries 4
LoginGraceTime 30
AllowUsers 32841
# --- end ---

Match Group administrators
       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

配置后执行 `sshd -t` 语法校验，无报错后执行 `Restart-Service sshd`。

### 2.3 账户防爆破策略

配合公网端口映射（如 FRP），锁定阈值配置为 5 次尝试锁定 15 分钟：
```powershell
net accounts /lockoutthreshold:5 /lockoutduration:15 /lockoutwindow:15
```

---

## 3. 三种标准调用模式

### 模式 1：Session 0 内部特权 CLI 调用

适用于依赖管理员权限的系统检测、服务启停、非 GUI pytest 单元测试：

```bash
ssh -i ~/.ssh/wp_win_sshd_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  32841@127.0.0.1 \
  "cd /d E:\\AllProjects202604\\Words-Production && .venv-win\\Scripts\\python.exe -m pytest tests/unit -q"
```

### 模式 2：跨会话投递至 Session 1 驱动 GUI 测试

GUI 自动化测试（如 `pywinauto` 驱动、工作台视觉回归）必须在交互式会话（Session 1）中执行。通过特权创建一次性计划任务拉起批处理包装：

```bash
# 1. 编写任务批处理 (写入 E:\... 共享盘)
cat > /mnt/e/AllProjects202604/Words-Production/.wp/tmp/run_ui.cmd <<'EOF'
@echo off
cd /d E:\AllProjects202604\Words-Production
.venv-win\Scripts\python.exe -m pytest tests\workbench\test_ui_driver.py -q --junitxml=.wp\tmp\ui.xml > .wp\tmp\ui.log 2>&1
echo %ERRORLEVEL% > .wp\tmp\ui_exit.txt
EOF

# 2. 经 SSH 创建并在桌面会话触发任务
ssh -i ~/.ssh/wp_win_sshd_ed25519 32841@127.0.0.1 \
  'schtasks /Create /F /TN WP-UI-Gate /TR "cmd /c E:\AllProjects202604\Words-Production\.wp\tmp\run_ui.cmd" /SC ONCE /ST 23:59 && schtasks /Run /TN WP-UI-Gate'

# 3. WSL 侧轮询结果并清理
# 任务执行完成后，经 SSH 删除任务定义：
ssh -i ~/.ssh/wp_win_sshd_ed25519 32841@127.0.0.1 'schtasks /Delete /F /TN WP-UI-Gate'
```

### 模式 3：驱动 Windows 原生 Agent 闭环测试

驱动 Windows 侧 `grok.exe` 执行自主测试并回收结构化数据：

```bash
ssh -i ~/.ssh/wp_win_sshd_ed25519 32841@127.0.0.1 \
  'env -u CODEX_HOME C:\Users\32841\.grok\bin\grok.exe -p "Act as gate runner in E:\AllProjects202604\Words-Production. Run .venv-win\Scripts\python.exe -m pytest tests\workbench\test_ui_driver.py -q --junitxml=.wp\tmp\agent.xml. Output ONLY a compact JSON with keys: exit_code, passed, failed." --permission-mode bypassPermissions'
```

> **调用准则**：Windows 原生 Agent 仅充当“有特权的执行者”，最终结论必须由 WSL 侧直接读取本地挂载的 `junit.xml` 或 `envelope.json` 进行校验，严禁单独以 Agent 返回的自然语言文本作为门禁判定依据。

---

## 4. 常见问题与排错表

| 故障现象 | 根因分析 | 修复方案 |
|---|---|---|
| `Permission denied (publickey)` | `administrators_authorized_keys` 采用 UTF-16 编码或权限过宽 | 重新以 ASCII 写入公钥，运行 `icacls ... /inheritance:r /grant Administrators:F /grant SYSTEM:F` |
| `pywinauto` 抛 `ElementNotFound` 或找不到桌面 | 直接在 SSH 会话（Session 0）内启动了 GUI 程序 | 使用模式 2，通过 `schtasks` 投递至已登录的 Session 1 交互式桌面 |
| 无法删除 `WP-UI-Gate` 计划任务（拒绝访问） | 计划任务由 SSH 管理员令牌创建，普通权限的 WSLInterop 无法删除 | 必须同样通过 SSH 管理员连接执行 `schtasks /Delete /F /TN <TaskName>` |
| Windows 侧 Agent 报错 `CODEX_HOME` 路径不存在 | WSL 侧的 `WSLENV` 白名单把 Linux 路径泄漏至 Windows | 调用 Windows 工具前清理泄漏变量：`env -u CODEX_HOME` |
| FRP 隧道下大量 `invalid format` 或暴力破解日志 | 公网扫描器探测暴露的 SSH 端口 | 维持 `PasswordAuthentication no`；针对 FRP 映射端配置安全组白名单或限速规则 |
