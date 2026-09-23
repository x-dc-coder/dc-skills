# 参考：环境要求与通道选择

> 本文件是 `SKILL.md` 的按需加载参考。新机器/新环境配置、通道选择、WSLInterop 验证时读取。

## Channel Selection Guide

| 场景 | 用哪个通道 | 命令 | 原因 |
|------|-----------|------|------|
| **GPU / Python 脚本** | pythonw.exe (首选) | `pythonw.exe -u -X utf8 script.py` | ~50ms, **无弹窗**, 参数不拆分, stderr 干净 |
| **GPU / Python (需 shell 重定向)** | cmd.exe (fallback) | `cmd.exe /c "py ... > out.txt"` | ~55ms, 有弹窗, 仅当需 `>` `2>` `&` 语法时用 |
| **任意 EXE 调用** | cmd.exe (C) | `cmd.exe /c program.exe args` | 薄转发层, 无额外处理 |
| 简单注册表 | reg.exe (B) | `reg query HKLM\...` | ~10ms, 原生工具 |
| 服务启停 | sc.exe (B) | `sc start MyService` | 原生, 输出简洁 |
| 进程管理(简单) | tasklist/taskkill (B) | `taskkill /F /PID 12345` | 快速, 无需 PS |
| 进程管理(按命令行) | PowerShell (A) | `Get-CimInstance Win32_Process` | 只有 WMI 能按命令行过滤 |
| WMI/CIM 查询 | PowerShell (A) | `Get-CimInstance ...` | PowerShell 独有能力 |
| Event Log | PowerShell (A) | `Get-WinEvent ...` | PowerShell 独有能力 |
| COM 自动化 | PowerShell (A) | `New-Object -ComObject` | COM 必须用 PS |
| Win32 P/Invoke | PowerShell (A) | `Add-Type -TypeDefinition` | 动态编译 C# 必须用 PS |

## Environment Requirements

### 基础要求（所有能力的前置条件）

| 组件 | 最低版本 | 你的环境 | 检查命令 |
|------|---------|---------|---------|
| **Windows** | Windows 10 Build 19041+ | Windows 10/11 | `systeminfo.exe \| grep "OS Name"` |
| **WSL** | 2.0.0+ | 2.6.3 ✅ | `wsl.exe --version` |
| **WSLInterop** | enabled | enabled ✅ | `cat /proc/sys/fs/binfmt_misc/WSLInterop` |
| **Ubuntu** | 20.04 LTS+ | 22.04.5 ✅ | `lsb_release -a` |
| **PowerShell** | 5.1+ (Windows 内置) | 5.1.26100 ✅ | `powershell.exe -Command '$PSVersionTable.PSVersion'` |
| **iconv** | 任意 (GNU coreutils) | 已安装 ✅ | `which iconv` |
| **base64** | 任意 (GNU coreutils) | 已安装 ✅ | `which base64` |

### 可选工具安装

**wslu (WSL Utilities)** — 提供 `wslview`、`wslvar`、`wslsys` 等便捷工具：

```bash
# Ubuntu 22.04+ / Debian（apt 仓库）
sudo apt update && sudo apt install -y wslu

# 验证安装
wslview --version   # 应显示 3.2.3+
wslvar --version
```

> **环境要求**: Ubuntu 20.04 LTS+ 或 Debian 11+。其他发行版请参考 [wslu 官方文档](https://wslutiliti.es/wslu/install.html)。
> wslu 依赖: `desktop-file-utils`, `bc`（apt 会自动安装）。

**jq** — JSON 解析（配合 `pshj()` 使用）：

```bash
sudo apt install -y jq
```

## When to Use This Skill

- 需要在 WSL 中调用 Windows 侧能力（EXE / Python / GPU / 系统工具）
- 关键词: `cmd.exe`、`powershell.exe`、GPU、CUDA、torch、Windows venv、注册表、WMI、COM、Visio、Office
- **GPU 训练/推理**: 见 `python-channel.md` → "GPU 桥接" 章节（单一事实来源）
- **GPU 训练流式输出/后台任务**: 使用 `stream_gpu_windows()`（实时看进度）、`launch_detached()`（后台 + tail -f）、`win-launcher.py`（Job Object 孤儿清理）。详见下方"实时输出与进度条"章节。
- **关键词**: `stream_gpu_windows`、`launch_detached`、`use_wrapper`、`win-launcher`、`GpuLimits`、`GpuGovernor`、tqdm、进度条、孤儿进程、Job Object

## WSLInterop Quick Check

Before attempting any Windows calls, verify WSLInterop is working:

```bash
# Should print "Hello from Windows" if WSLInterop is enabled
powershell.exe -Command "Write-Host 'Hello from Windows'"
```

If this fails with `Exec format error`, WSLInterop is not registered.
Fix it with:

```bash
# Requires sudo; may need to re-run after WSL restart
echo ':WSLInterop:M::MZ::/init:' | sudo tee /proc/sys/fs/binfmt_misc/register
```

For persistence, add to `/etc/wsl.conf`:

```ini
[interop]
enabled=true
appendWindowsPath=true
```

---

## Encoding Notes

- **Channel C (cmd.exe)**: pipe 输出走系统 GBK 编码 → 非 ASCII 字符乱码。**写文件绕开**。
- **Channel B (Direct EXE)**: 同样 GBK 编码。用 `grep` 提取 ASCII 字段即可。
- **Channel A (PowerShell)**: 通过设置 `[Console]::OutputEncoding = UTF8` 可正确输出中文。

---

