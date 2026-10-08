# 参考：PowerShell 通道（Channel A）— COM / WMI / P/Invoke / Event Log / 计划任务

> 本文件是 `SKILL.md` 的按需加载参考。需要 COM 自动化、WMI/CIM 查询、Win32 P/Invoke、
# Event Log、计划任务、剪贴板等 PowerShell 独有能力时读取。

## Channel A: PowerShell — 双引擎架构（pwsh.exe 7.x 优先，powershell.exe 5.1 兜底）

> **仅当 cmd.exe / 原生 EXE 无法胜任时使用**：COM 对象、WMI 查询、Event Log、P/Invoke 动态编译 C#、高级 JSON 处理。

### 引擎对比与特性矩阵

| 特性维度 | PowerShell 7.x (`pwsh.exe`) | Windows PowerShell 5.1 (`powershell.exe`) | 桥接工程建议 |
|---|---|---|---|
| **定位与分发** | **首选**（推荐安装 7.2+ LTS，当前 7.6.6） | **兼容兜底**（Windows 10/11 内置） | 脚本与助手优先调用 `pwsh.exe`，缺失时自动回退 `powershell.exe` |
| **冷启动延迟 (`-NoProfile`)** | ~340ms（CoreCLR JIT + 程序集装载） | ~135ms（.NET Framework NGEN） | 维持“非独有能力走 Channel B/C、批量集中化”的铁律 |
| **管道链式操作符 (`&&`, `||`)** | 支持（`cmd1 && cmd2`） | 不支持（报 ParserError） | 复杂单行桥接命令推荐使用 `&&` 与 `||` 编排 |
| **现代语言操作符** | 支持三元 `?:`、空值合并 `??`、赋值 `??=`、安全导航 `?.` | 不支持（语法解析错误） | 简化条件分支与安全字段提取 |
| **JSON 单元素数组** | `ConvertTo-Json -AsArray` | 不支持（单元素自动降为标量） | 涉及列表返回时使用 `-AsArray` 确保 JSON 结构稳定 |
| **JSON 哈希表解析** | `ConvertFrom-Json -AsHashtable` | 不支持（仅能转 PSCustomObject） | 需要字典索引 `$json['key']` 时使用 `-AsHashtable` |
| **多线程并行管道** | `ForEach-Object -Parallel` | 不支持（需手动写 RunspacePool） | 跨文件批量哈希/属性批量采集可单进程多线程并发 |
| **工作目录开关** | `-WorkingDirectory` 生效 | 静默忽略（保持 WSL UNC 路径） | pwsh 下可用 `-WorkingDirectory` 降低 UNC 相对路径误写风险 |
| **文件输出编码** | 默认 UTF-8 (无 BOM) | 默认 ANSI (GBK) 或 UTF-16LE | 重定向 `>` 与 `Out-File` 在 pwsh 下原生契合 Linux 生态 |
| **跨边界管道输出** | Interop pipe 仍受 OEM 代码页约束 | 默认为 GBK | 两者均应配置 `[Console]::OutputEncoding = UTF8` 前缀 |
| **COM 线程单元** | Windows 宿主下默认 STA | 默认 STA | Office 与剪贴板自动化无需特殊切换，兼容运行 |

---

### Calling Patterns

**简单单行（使用单引号避免 bash 展开，带 -NoProfile 提升启动速度）：**
```bash
# 首选 pwsh.exe (PowerShell 7.x)
pwsh.exe -NoProfile -Command 'Get-Process -Name code | ConvertTo-Json -AsArray'

# 兼容 powershell.exe (PS 5.1)
powershell.exe -NoProfile -Command 'Get-Process -Name code | ConvertTo-Json'
```

**现代链式操作与安全访问（PowerShell 7.x 专属）：**
```bash
# 存在文件时才读取，失败时降级
pwsh.exe -NoProfile -Command 'Test-Path "E:\data\summary.json" && (Get-Content "E:\data\summary.json" -Raw | ConvertFrom-Json -AsHashtable) || @{}'
```

**并发批量处理（PowerShell 7.x 专属）：**
```bash
# 4 线程并行检查多个服务状态
pwsh.exe -NoProfile -Command '"wuauserv","Dhcp","EventLog" | ForEach-Object -Parallel { (Get-Service -Name $_).Status } -ThrottleLimit 4'
```

**复杂脚本（写 `.ps1` 文件，通过 `-File` 调用）：**
```bash
# 从 WSL 写入 PS1 到本地 Windows 磁盘（规避 UNC Internet Zone 执行限制）
cat > /mnt/e/temp/task.ps1 << 'EOF'
param([string]$Name)
$r = Get-CimInstance Win32_Process | Where-Object CommandLine -like "*$Name*"
$r | Select-Object ProcessId,CommandLine | ConvertTo-Json
EOF

# 调用（使用绝对路径，显式 Bypass 执行策略）
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "E:\temp\task.ps1" -Name "python"
```

**注意**：
- **参数拆分规避**：两代 PowerShell 的 `-Command` 在接收含空格参数时均可能发生拆分。复杂参数建议使用 `-File` 或以 JSON 字符串传递。
- **编码与 CLIXML**：两代引擎的 `-EncodedCommand` 均严格要求 Base64(UTF-16LE) 编码；错误流输出可能包含 CLIXML 包装，需做行过滤。
- **路径第一防线**：无论使用哪个引擎，跨边界文件写入一律传入绝对 Windows 路径（`wslpath -w`）。

---

---

## Channel A: PowerShell Bridge — Core Capabilities

> **以下能力需要 PowerShell 桥接通道**（启动开销 ~300ms，适用于复杂操作、结构化输出、COM/WMI/P/Invoke）。
> 对于简单快速的注册表/服务/进程操作，请先用 [Channel B](exe-channel.md#channel-b-direct-windows-exe-tools-zero-powershell-overhead) 的直调工具。

### Capability 1: Registry Access

Read and write the Windows Registry — essential for discovering installed software,
system configuration, file associations, and COM class registration.

```bash
# Read: list installed software
powershell.exe -Command "
    Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' |
    Where-Object DisplayName -ne \$null |
    Select-Object DisplayName, DisplayVersion, Publisher |
    ConvertTo-Json
"

# Read: Windows version info
powershell.exe -Command "
    Get-ItemProperty 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion' |
    Select-Object ProductName, CurrentBuild, EditionID |
    ConvertTo-Json
"

# Read: find Java home from registry
powershell.exe -Command "
    Get-ItemProperty 'HKLM:\Software\JavaSoft\JDK\*' |
    Select-Object JavaHome |
    ConvertTo-Json
"

# Write: set a user-level registry value
powershell.exe -Command "
    New-Item -Path 'HKCU:\Software\MyBridge' -Force | Out-Null;
    Set-ItemProperty -Path 'HKCU:\Software\MyBridge' -Name 'LastRun' -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
"

# Delete: remove a registry key
powershell.exe -Command "Remove-ItemProperty -Path 'HKCU:\Software\MyBridge' -Name 'TempSetting' -ErrorAction SilentlyContinue"
```

**Registry hive mappings:**
| Abbreviation | Full Path |
|-------------|-----------|
| `HKLM:` | `HKEY_LOCAL_MACHINE` |
| `HKCU:` | `HKEY_CURRENT_USER` |
| `HKCR:` | `HKEY_CLASSES_ROOT` |
| `HKU:` | `HKEY_USERS` |
| `HKCC:` | `HKEY_CURRENT_CONFIG` |

### Capability 2: Clipboard Integration

Exchange data between WSL and the Windows clipboard seamlessly.

```bash
# WSL → Windows clipboard (使用 Set-Clipboard 确保中文不乱码)
echo "Copied from WSL terminal" | powershell.exe -Command "Set-Clipboard -Value (Get-Content -Path '\$(wslpath -w /tmp/clip.txt)' -Raw -Encoding UTF8)"
# Or use the psh helper: echo "hello" | win-clip

# The safer pattern via temp file:
echo "中文内容 from WSL" > /tmp/clip.txt
powershell.exe -Command "${POWERSHELL_UTF8} Set-Clipboard -Value (Get-Content -Path '\\wsl.localhost\Ubuntu\tmp\clip.txt' -Raw -Encoding UTF8)"

# Windows clipboard → WSL
powershell.exe -Command "Get-Clipboard"
powershell.exe -Command "Get-Clipboard" > /tmp/clipboard_content.txt

# WSL → Windows clipboard (simple ASCII only — 中文会乱码!)
echo "ASCII only text" | clip.exe

# Get image from clipboard (saves to Windows path)
powershell.exe -Command "
    Add-Type -AssemblyName System.Windows.Forms;
    \$img = [System.Windows.Forms.Clipboard]::GetImage();
    if (\$img) { \$img.Save('E:\temp\clipboard_image.png') }
"
```

### Capability 3: WMI / CIM Queries

Query system hardware, OS configuration, process details, and network info
via WMI (Windows Management Instrumentation). **Use `Get-CimInstance`** (PowerShell 5+)
— it's the modern, WSMan-based replacement for the deprecated `Get-WmiObject`.

```bash
# System hardware info
powershell.exe -Command "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory | ConvertTo-Json"

# CPU info
powershell.exe -Command "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,MaxClockSpeed | ConvertTo-Json"

# Disk info
powershell.exe -Command "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,@{N='SizeGB';E={[math]::Round(\$_.Size/1GB,1)}},@{N='FreeGB';E={[math]::Round(\$_.FreeSpace/1GB,1)}} | ConvertTo-Json"

# Network adapter info
powershell.exe -Command "Get-CimInstance Win32_NetworkAdapter -Filter 'NetEnabled=True' | Select-Object Name,MACAddress,AdapterType | ConvertTo-Json"

# Find process by command line (!! crucial for precise process management)
powershell.exe -Command "Get-CimInstance Win32_Process | Where-Object CommandLine -like '*uvicorn*app:app*' | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json"

# List all services and their states
powershell.exe -Command "Get-CimInstance Win32_Service | Select-Object Name,State,StartMode,DisplayName | ConvertTo-Json"

# Check Windows Update history
powershell.exe -Command "Get-CimInstance Win32_QuickFixEngineering | Select-Object HotFixID,InstalledOn,Description | ConvertTo-Json"
```

**Common WMI classes:**
| Class | Use For |
|-------|---------|
| `Win32_ComputerSystem` | Manufacturer, model, RAM, domain |
| `Win32_Processor` | CPU name, cores, clock speed |
| `Win32_LogicalDisk` | Drive letters, size, free space |
| `Win32_NetworkAdapter` | NIC names, MAC, type |
| `Win32_Process` | Process details **including command line** |
| `Win32_Service` | Windows services and their states |
| `Win32_Product` | MSI-installed products (slow — prefer Registry for listing) |
| `Win32_QuickFixEngineering` | Windows Update / hotfix history |
| `Win32_OperatingSystem` | OS version, install date, last boot |
| `Win32_BIOS` | BIOS version, serial number |

### Capability 4: Win32 API via Add-Type (P/Invoke)

Call **any** Win32 API function by compiling C# on-the-fly inside PowerShell.
This unlocks capabilities that have no PowerShell cmdlet equivalent.

```bash
# Call kernel32!GetTickCount
cmd_b64=$(cat <<'EOF' | iconv -t UTF-16LE | base64 -w0
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Kernel32 {
    [DllImport("kernel32.dll")]
    public static extern uint GetTickCount();
}
"@
[Kernel32]::GetTickCount()
EOF
)
powershell.exe -EncodedCommand "$cmd_b64"

# Show a Windows MessageBox (GUI — useful for notifications from WSL)
cmd_b64=$(cat <<'EOF' | iconv -t UTF-16LE | base64 -w0
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class User32 {
    [DllImport("user32.dll", CharSet=CharSet.Auto)]
    public static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);
}
"@
[User32]::MessageBox([IntPtr]::Zero, "Build completed from WSL!", "WSL Notification", 0)
EOF
)
powershell.exe -EncodedCommand "$cmd_b64"

# Get monitor count / screen resolution
cmd_b64=$(cat <<'EOF' | iconv -t UTF-16LE | base64 -w0
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class User32 {
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);
}
"@
@{
    ScreenWidth = [User32]::GetSystemMetrics(0)
    ScreenHeight = [User32]::GetSystemMetrics(1)
    MonitorCount = [User32]::GetSystemMetrics(80)
} | ConvertTo-Json
EOF
)
powershell.exe -EncodedCommand "$cmd_b64"

# Check if a DLL is loaded in a process
cmd_b64=$(cat <<'EOF' | iconv -t UTF-16LE | base64 -w0
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Psapi {
    [DllImport("psapi.dll")]
    public static extern bool EnumProcessModules(IntPtr hProcess, IntPtr[] lphModule, uint cb, out uint lpcbNeeded);
}
"@
Write-Host "PSAPI loaded — EnumProcessModules available"
EOF
)
powershell.exe -EncodedCommand "$cmd_b64"
```

**Common Win32 DLLs to P/Invoke from:**
| DLL | What It Provides |
|-----|-----------------|
| `kernel32.dll` | Process/thread management, memory, file ops, timing |
| `user32.dll` | Windows, messages, clipboard (advanced), input, display |
| `advapi32.dll` | Registry (low-level), security, services, Event Log |
| `shell32.dll` | Shell execute, file associations, special folders |
| `psapi.dll` | Process enumeration, memory usage |
| `gdi32.dll` | Drawing, fonts, printing |

### Capability 5: Windows Environment Variables

Discover Windows-side paths (VS, Python, Java, Node installations) from WSL.

```bash
# Get all Windows environment variables
powershell.exe -Command 'Get-ChildItem Env: | Select-Object Name,Value | ConvertTo-Json'

# Get a specific variable
powershell.exe -Command '$env:JAVA_HOME'
powershell.exe -Command '$env:USERPROFILE'
powershell.exe -Command '$env:Path -split ";" | ConvertTo-Json'

# Find Visual Studio installation
powershell.exe -Command '
    $vsPath = ${env:ProgramFiles(x86)};
    Get-ChildItem "$vsPath\Microsoft Visual Studio" -ErrorAction SilentlyContinue |
    Select-Object Name
'

# Get Windows user profile path
powershell.exe -Command '[Environment]::GetFolderPath("Desktop")'
powershell.exe -Command '[Environment]::GetFolderPath("MyDocuments")'
```

### Capability 6: COM Object Automation

Automate Windows applications via COM (Component Object Model).

```bash
# Visio
powershell.exe -Command "
    \$visio = New-Object -ComObject Visio.Application;
    Write-Output \$visio.Version;
    \$visio.Quit()
"

# Excel (create a workbook, write data)
powershell.exe -Command "
    \$excel = New-Object -ComObject Excel.Application;
    \$excel.Visible = \$false;
    \$wb = \$excel.Workbooks.Add();
    \$ws = \$wb.Worksheets.Item(1);
    \$ws.Cells.Item(1,1) = 'Hello from WSL';
    \$wb.SaveAs('E:\\temp\\wsl_output.xlsx');
    \$excel.Quit()
"

# Word (open doc, extract text)
powershell.exe -Command "
    \$word = New-Object -ComObject Word.Application;
    \$word.Visible = \$false;
    \$doc = \$word.Documents.Open('E:\\docs\\report.docx');
    \$text = \$doc.Content.Text;
    Write-Output \$text.Substring(0, [Math]::Min(500, \$text.Length));
    \$doc.Close();
    \$word.Quit()
"

# Internet Explorer (navigate, get page content — for legacy systems)
powershell.exe -Command "
    \$ie = New-Object -ComObject InternetExplorer.Application;
    \$ie.Visible = \$false;
    \$ie.Navigate('https://example.com');
    while (\$ie.Busy -or \$ie.ReadyState -ne 4) { Start-Sleep -Milliseconds 100 };
    Write-Output \$ie.Document.body.innerText;
    \$ie.Quit()
"
```

**Critical Note:** COM objects cannot be passed across the WSL-Windows boundary.
They must be created, used, and destroyed entirely within the Windows-side process.

**COM 线程单元与引擎兼容性**：
- 在 Windows 宿主环境下，PowerShell 7 (`pwsh.exe`) 与 5.1 (`powershell.exe`) 默认均运行于 STA (Single-Threaded Apartment) 模式，与 Word/Excel/Visio 等桌面 Office COM 接口兼容。
- 启动独立 PS 自动化脚本时，可显式传递 `-STA` 参数（例如 `pwsh.exe -STA -File ...`）提供显式线程单元保障。
- 自动化结束时务必显式调用 `Quit()` 并退出进程，避免残余后台 COM 宿主占用系统句柄。

**Common COM ProgIDs:**
| ProgID | Application |
|--------|------------|
| `Visio.Application` | Microsoft Visio |
| `Word.Application` | Microsoft Word |
| `Excel.Application` | Microsoft Excel |
| `PowerPoint.Application` | Microsoft PowerPoint |
| `Outlook.Application` | Microsoft Outlook |
| `InternetExplorer.Application` | Internet Explorer |
| `Shell.Application` | Windows Shell |
| `Scripting.FileSystemObject` | File system operations |
| `WScript.Shell` | Shell execute, shortcuts, env vars |
| `MSXML2.DOMDocument` | XML parsing |

### Capability 7: Process Management (Enhanced)

Start, monitor, and stop Windows processes with precision.

```bash
# === START ===

# Simple: launch a process
powershell.exe -Command "Start-Process notepad.exe"

# Launch with working directory + env vars
powershell.exe -Command '
    cd E:\AllProjects\DrawForge\agent\skills\visioskills\bridge_server;
    $env:VISIO_BRIDGE_TOKEN="drawforge-test-token-2026";
    . .venv\Scripts\Activate.ps1;
    Start-Process -NoNewWindow uvicorn -ArgumentList "app:app","--host","0.0.0.0","--port","18761"
'

# Launch and capture PID for later management
powershell.exe -Command '
    $p = Start-Process -PassThru -FilePath "python.exe" -ArgumentList "-m http.server 8888";
    $p.Id | Out-File -FilePath "E:\temp\wsl_managed_pid.txt";
    Write-Output $p.Id
'

# === MONITOR ===

# Check if a specific PID is running
powershell.exe -Command "Get-Process -Id 12345 -ErrorAction SilentlyContinue | Select-Object Id,ProcessName"

# Find process by command line pattern (PRECISE — won't match unrelated processes)
powershell.exe -Command "
    Get-CimInstance Win32_Process |
    Where-Object CommandLine -like '*uvicorn*app:app*' |
    Select-Object ProcessId,Name,CommandLine |
    ConvertTo-Json
"

# Check process resource usage
powershell.exe -Command "
    Get-Process -Name uvicorn -ErrorAction SilentlyContinue |
    Select-Object Id,ProcessName,@{N='CPU_s';E={[math]::Round(\$_.CPU,1)}},@{N='MemMB';E={[math]::Round(\$_.WorkingSet64/1MB,1)}}
"

# === STOP ===

# Stop by PID (preferred — most precise)
powershell.exe -Command "Stop-Process -Id 12345 -Force"

# Stop by PID file (the safe pattern)
pid=$(powershell.exe -Command 'Get-Content "E:\temp\wsl_managed_pid.txt" -ErrorAction SilentlyContinue')
if [ -n "$pid" ]; then
    powershell.exe -Command "Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue"
    powershell.exe -Command 'Remove-Item "E:\temp\wsl_managed_pid.txt" -ErrorAction SilentlyContinue'
fi

# Stop by name (use cautiously — may affect unrelated processes!)
powershell.exe -Command "Get-Process -Name uvicorn -ErrorAction SilentlyContinue | Stop-Process -Force"

# === BACKGROUND LIFECYCLE ===

# The proper way to manage daemon-like processes from WSL:
# 1. Start → capture PID to file
# 2. Check → verify PID is still running + command line matches
# 3. Stop → kill by PID, clean up PID file
```

### Capability 8: File Operations Between WSL and Windows

```bash
# WSL path → Windows path conversion
wslpath -w /home/user/project/file.txt
# Output: \\wsl.localhost\Ubuntu\home\user\project\file.txt

# Windows path → WSL path
wslpath -u "E:\AllProjects\DrawForge"
# Output: /mnt/e/AllProjects/DrawForge

# Copy from WSL to Windows
powershell.exe -Command "Copy-Item '\\wsl.localhost\Ubuntu\home\dc\file.txt' 'E:\\dest\\'"

# Copy a directory recursively
powershell.exe -Command "Copy-Item '\\wsl.localhost\Ubuntu\home\dc\project' 'E:\\backup\\' -Recurse"

# Create a Windows directory
powershell.exe -Command "New-Item -ItemType Directory -Path 'E:\\temp\\wsl_output' -Force"

# List Windows directory contents from WSL
ls /mnt/e/AllProjects/

# Open a file with its Windows default application
powershell.exe -Command "Start-Process 'E:\\docs\\report.docx'"
cmd.exe /c start "" "E:\\docs\\report.docx"

# Open Explorer at a specific path
powershell.exe -Command "Invoke-Item 'E:\\AllProjects'"
```

### Capability 9: Windows Event Log Access

Query Windows Event Logs for diagnostics, error investigation, and system monitoring.

```bash
# Recent System errors (last 24 hours)
powershell.exe -Command '
    Get-WinEvent -FilterHashtable @{LogName="System"; Level=2; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 10 |
    Select-Object TimeCreated,Id,LevelDisplayName,Message |
    ConvertTo-Json -Depth 2
'

# Recent Application errors
powershell.exe -Command '
    Get-WinEvent -FilterHashtable @{LogName="Application"; Level=2; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20 |
    Select-Object TimeCreated,Id,ProviderName,Message |
    ConvertTo-Json -Depth 2
'

# Security audit failures
powershell.exe -Command '
    Get-WinEvent -FilterHashtable @{LogName="Security"; StartTime=(Get-Date).AddHours(-1)} -MaxEvents 50 |
    Where-Object {$_.Id -eq 4625} |
    Select-Object TimeCreated,Id,Message |
    ConvertTo-Json -Depth 2
'

# Check if a specific service crashed
powershell.exe -Command '
    Get-WinEvent -FilterHashtable @{LogName="System"; Level=1,2; StartTime=(Get-Date).AddHours(-1)} -MaxEvents 100 |
    Where-Object {$_.Message -match "uvicorn|python"} |
    Select-Object TimeCreated,LevelDisplayName,Message
'

# List available event logs
powershell.exe -Command "Get-WinEvent -ListLog * | Select-Object LogName,RecordCount | Where-Object RecordCount -gt 0"
```

### Capability 10: Windows Task Scheduler

Create, query, and manage scheduled tasks for persistent automation.

```bash
# List all scheduled tasks
powershell.exe -Command "Get-ScheduledTask | Select-Object TaskName,State | ConvertTo-Json"

# Get details of a specific task
powershell.exe -Command "Get-ScheduledTask -TaskName 'MyBackup' | Get-ScheduledTaskInfo"

# Create a simple daily task
powershell.exe -Command '
    $action = New-ScheduledTaskAction -Execute "D:\Anconda\python.exe" -Argument "E:\scripts\daily_backup.py";
    $trigger = New-ScheduledTaskTrigger -Daily -At "02:00AM";
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest;
    Register-ScheduledTask -TaskName "WSL_Backup" -Action $action -Trigger $trigger -Principal $principal -Description "Backup triggered from WSL"
'

# Run a task immediately
powershell.exe -Command "Start-ScheduledTask -TaskName 'WSL_Backup'"

# Disable / Enable a task
powershell.exe -Command "Disable-ScheduledTask -TaskName 'WSL_Backup'"
powershell.exe -Command "Enable-ScheduledTask -TaskName 'WSL_Backup'"

# Delete a task
powershell.exe -Command "Unregister-ScheduledTask -TaskName 'WSL_Backup' -Confirm:`$false"
```
