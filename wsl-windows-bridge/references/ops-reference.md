# 参考：运维速查（场景/错误/限制/最佳实践）

> 本文件是 `SKILL.md` 的按需加载参考。遇到报错、边界场景或需要快速决策时读取。

---

## Common Scenarios (Summary)

### Scenario A: Run Windows Python (GPU or otherwise)

```bash
# 一行检查
cmd.exe /c "E:\venvs\marker\Scripts\python.exe -c \"import torch; print(torch.cuda.is_available())\""
```

### Scenario B: Check if a Windows Package is Available

```bash
cmd.exe /c "D:\Anconda\python.exe -c \"import win32com.client; print('pywin32 OK')\""
```

### Scenario C: Cross-Boundary Clipboard Operations

```bash
# WSL → Windows clipboard
dmesg | tail -20 | clip.exe               # ASCII only
echo "中文" > /tmp/clip.txt                # 中文：写文件方式
powershell.exe -Command 'Set-Clipboard -Value (Get-Content -Path "\\wsl.localhost\Ubuntu\tmp\clip.txt" -Raw -Encoding UTF8)'

# Windows clipboard → WSL
powershell.exe -Command "Get-Clipboard"
```

### Scenario D: System Inventory

```bash
powershell.exe -Command "
    Get-ComputerInfo | Select-Object CsName,WindowsVersion,OsArchitecture |
    ConvertTo-Json
"
```

```bash
# One-shot system snapshot
powershell.exe -Command "
    @{
        OS = (Get-ItemProperty 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion').ProductName
        CPU = (Get-CimInstance Win32_Processor).Name.Trim()
        RAM_GB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB, 1)
        Disks = (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { \"\$(\$_.DeviceID) \$([math]::Round(\$_.Size/1GB,1))GB\" }) -join ', '
    } | ConvertTo-Json
"
```

---

## Error Handling and Debugging

### Common Errors

| Error | Meaning | Fix |
|-------|---------|-----|
| `Exec format error` | WSLInterop not registered | Register with `binfmt_misc` |
| `The term 'X' is not recognized` | Windows PATH issue | Use full paths or activate env |
| `Access is denied` | UAC/permission issue | Run as Administrator or adjust ACLs |
| `COM initialization failed` | COM not available in context | Ensure Windows-side process can access COM |
| Command appears to hang | Process waiting for input | Use `-NoNewWindow` and redirect stdin |
| `base64: invalid input` | EncodedCommand requires UTF-16LE | Always pipe through `iconv -t UTF-16LE` first |
| pythonw 下 print 无输出/进程看似卡住 | sys.stdout=None → print 抛异常且 traceback 丢失 | worker 开头显式 sys.stdout=sys.stderr=open(日志,"a",encoding="utf-8",buffering=1)（Windows 路径） |
| 求解器（HiGHS 等）在 pythonw 下阻塞 | C 库写 fd1 遇无效/无重定向句柄 | 设 output_flag=False / log_search=False；Popen 重定向 stdout |
| 从 bash 传 Linux 路径给 Windows 进程（FileNotFoundError） | /mnt/e/... 不是 Windows 路径 | 用 wslpath -w 转 E:\\... |
| Windows venv py3.11 SyntaxError: f-string unmatched | f-string 嵌套同引号 3.12 前非法 | 统一 '' 单引号 |
| write/edit 工具在 /mnt/e 报 EPERM | NTFS 挂载 chmod 失败 | 改用 bash/python 读写文件 |
| MIP res.x 为 None（status 2/3/4） | HiGHS 无可行解/错误 | if res.status in (0,1) and res.x is not None 再解析 |
| WSL 侧 kill -0 检测不到 Windows worker 完成 | interop 包装进程未退出 | 改用 batch worker 内部循环 + done 清单续跑 |
| pkill -f 脚本 误杀自身 | 命令行含匹配串 | pgrep -f ... 排除自身 |
| `Cannot convert value to System.String` | PowerShell output type mismatch | Pipe through `Out-String` or `ConvertTo-Json` before capture |

### Debugging Strategy

1. **Test the command directly in Windows PowerShell first**
   If it doesn't work there, it won't work from WSL either.

2. **Capture stderr explicitly**
   ```bash
   powershell.exe -Command "..." 2>&1
   ```

3. **Use transcript logging**
   ```bash
   powershell.exe -Command '
       Start-Transcript -Path "E:\\logs\\debug.log";
       # ... your commands ...
       Stop-Transcript
   '
   ```

4. **Check exit codes**
   ```bash
   powershell.exe -Command "..."
   echo "Exit code: $?"
   ```

5. **For EncodedCommand: verify encoding**
   ```bash
   # Decode to check what was sent
   echo "$cmd_b64" | base64 -d | xxd | head
   # Should show UTF-16LE (alternating null bytes for ASCII)
   ```

6. **Use `$ErrorActionPreference = 'Stop'`** to surface hidden errors
   ```bash
   powershell.exe -Command '$ErrorActionPreference = "Stop"; ...'
   ```

---

## Limitations (Must Know)

1. **No Interactive Input**: Cannot use `Read-Host`, interactive prompts, or GUI dialogs (except via `[User32]::MessageBox` w/ P/Invoke).
2. **No State Persistence**: Each call is a fresh process. Variables don't persist between calls.
3. **Encoding Gap**: cmd.exe (C) 和 Direct EXE (B) 的 pipe 输出走系统 GBK 编码，中文会乱码。**解决**：① 用 pythonw.exe + UTF-8 三层防护（`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` + `-X utf8` + WSLENV 白名单）；② 写文件（`pythonw ... > out.txt` 然后从 WSL 读取）。
4. **cmd.exe UNC 路径**: 从 WSL `~/` 目录调用 `cmd.exe` 报 UNC 不支持。**`cwd="/mnt/e/temp"`** 解决。
5. **ps -Command 参数拆分**: `powershell.exe -Command '...' "hello world"` 会把 hello world 拆成两个参数。复杂参数用 `-File`。
6. **ps -EncodedCommand CLIXML**: 非 TTY 输出会包 CLIXML。不推荐用于数据交换。
7. **ps -File 退出码**: 子进程的退出码在 `-File` 模式下会被吞掉。不推荐用于需要检测退出码的场景。
8. **COM Object Boundaries**: COM 对象在 PowerShell 进程内创建和使用，不能传递给 WSL。
9. **Administrator Privileges**: `netsh.exe firewall`、`sc.exe config` 等需要管理员权限。

## Best Practices

- **Channel 优先级**: pythonw.exe > cmd.exe (C) > Direct EXE (B) > PowerShell (A)。GPU/Python 实验一律优先 pythonw.exe（无弹窗）。
- **GPU / Python 脚本**: 优先 `pythonw.exe`；仅当需要 shell 重定向 (`>` `2>`) 时降级到 `cmd.exe /c`，`cwd="/mnt/e/temp"`。
- **UTF-8 三层防护**: PYTHONUTF8=1 + PYTHONIOENCODING=utf-8 + pythonw.exe `-X utf8`。三者必须配 WSLENV 白名单才生效。
- **PowerShell 仅用于**: COM / WMI / Event Log / P/Invoke — 这些是 cmd.exe 做不到的。
- **中文输出**: 写文件（`> win_file` 然后从 WSL 读取），不要依赖 pipe 编码。
- **复杂 PS 脚本**: 写入 `/mnt/e/temp/*.ps1`，用 `-File` 调用，参数不会被拆分。
- **安装 `wslu`**: `wslview` (打开文件), `wslvar` (读 Windows 环境变量)。
- **用 `wslpath`** 做路径转换。
- **用 JSON** 输出数据（`ConvertTo-Json`）便于 WSL 侧 `jq` 解析。
- **GPU 规则**: 统一在 `python-channel.md` → "GPU 桥接" 章节。
- **PyTorch env var**: `PYTORCH_CUDA_ALLOC_CONF` 只接受 `garbage_collection_threshold:0.7`、`max_split_size_mb:N` 等少数选项。`per_process_memory_fraction` 是 **Python API**（`torch.cuda.set_per_process_memory_fraction()`），不是 env var。
- **无弹窗/HEADLESS**: 全程 pythonw.exe；`run_gpu_windows`/`launch_detached` 已内置强制校验。批量查进程/清理用集中式（一次 PowerShell/常驻代理），别循环调 tasklist/taskkill。
- **跨边界路径**: bash/Python → Windows 进程必须 `wslpath -w`；Windows → WSL 用 `/mnt/<盘符>/`。worker 内日志/输入路径一律 Windows 格式。
- **worker stdio**: pythonw 下显式 `sys.stdout=sys.stderr=open(日志,'a',encoding='utf-8',buffering=1)`，否则 print/traceback 静默丢失。
- **求解器禁日志**: HiGHS/OR-Tools 等 C 库写 fd1 在 pythonw 下可能阻塞，设 `output_flag=False`/`log_search=False`。
- **NTFS 挂载**: /mnt/e 下用 write/edit 原子替换工具会 EPERM(chmod 失败)——改用 bash/python 读写。
- **MIP/求解器健壮性**: 解可能为 None/infeasible，必须防护后处理，防数据静默丢失。

---

## Related Concepts

- **HTTP Bridge Pattern**: For complex automation, consider running a persistent HTTP server on the Windows side (like DrawForge's Visio Bridge) and calling it from WSL via HTTP. This avoids process-per-call overhead and provides session management.
- **SSH to Windows**: If WSLInterop fails, an alternative is running an SSH server on Windows and connecting via `ssh windows-host`.
- **WSLg (WSL GUI)**: Windows 11's WSLg allows Linux GUI apps to run natively on the Windows desktop, but the reverse (WSL controlling Windows GUI) still requires PowerShell bridge.
- **$env:WSLENV**: WSL shares this environment variable with Windows. Use it to pass values between environments: `export WSLENV=MYVAR/w` makes `$env:MYVAR` available in PowerShell.
