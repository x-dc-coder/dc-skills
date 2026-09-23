---
name: wsl-windows-bridge
description: >
  WSL→Windows 跨边界框架：三层调用通道（pythonw.exe 直调无弹窗首选 / cmd.exe fallback / 直接 EXE）+ GPU 资源治理（GpuLimits 单进程配额 + GpuGovernor 跨进程协调防 OOM）。覆盖 GPU 训练/推理、Windows Python 环境、注册表、WMI、COM、Event Log。涉及跨系统调用、Windows 侧程序、GPU 任务或注册表/COM 操作时使用。
metadata:
  family: infra
  role: member
  load-mode: manual
disable-model-invocation: true
---
# WSL → Windows 桥接 Skill

> **使用方式（渐进式披露）**：本文件是导航页——先读它确定通道与铁律；执行具体操作时按需加载
> `references/` 下对应参考文件（完整命令模板、环境要求、排错表都在那里）。

## Purpose

WSL2 runs a Linux kernel — Windows-only capabilities (COM, Win32 API, Registry, WMI, GPU) are **not directly accessible**. WSL provides `WSLInterop` to launch Windows executables from Linux.

**三条通道按速度与简单度排序：**

| Channel | Launcher | Overhead | When |
|---------|----------|----------|------|
| **C: pythonw.exe** | `pythonw.exe` 直调 | ~50ms | **GPU / Python scripts / 无弹窗要求**（⭐ 首选） |
| **C': cmd.exe** | `cmd.exe /c` | ~55ms | 需要 shell 重定向 (`>` `2>` `&`)，会弹窗 |
| **B: Direct EXE** | `reg.exe`, `sc.exe`... | ~10ms | 简单系统工具（注册表/服务/进程） |
| **A: PowerShell** | `powershell.exe` | ~600ms | COM / WMI / P/Invoke / Event Log |

**核心原则：pythonw 优先于 cmd，能 cmd 不用 ps，能直调不套壳。**

## ⚠️ 无弹窗强制规则（HEADLESS，代码级强制 → `scripts/gpu_safe_subprocess.py::_ensure_pythonw`）

1. **全程 pythonw.exe**：任何 Windows Python 子进程必须用 `pythonw.exe`（GUI 子系统，**永不弹窗**）。严禁直接用 `python.exe`（控制台子系统，**必弹 conhost**）——即使输出已重定向。
2. **拒绝裸名**：`py_exe` 必须绝对路径，禁止 `pythonw`/`python` 裸名（WSL interop 会命中 `D:\Python311` 等缺包环境）。
3. **自动替换**：误传 `python.exe` 时，`_ensure_pythonw` 自动替换为同目录 `pythonw.exe` 并告警；同目录无 pythonw 或传未知解释器 → 抛 `ValueError`（strict 模式）。
4. **不经 cmd.exe 中转**：`run_gpu_windows` 已改为 pythonw 直调（`_headless_cmd`），杜绝 conhost 弹窗；`cmd.exe /c` 仅限需 shell 重定向的显式 fallback（**会弹窗**）。
5. **回归测试**：改脚本后必跑 `python3 scripts/test_headless.py`（17 项：强制规则 + 现有能力回归，须 0 FAIL）。
6. **worker 内绑定 stdio**：pythonw 下 `sys.stdout=None`（print 抛异常且 traceback 丢失），worker 开头必须 `sys.stdout = sys.stderr = open(日志, 'a', encoding='utf-8', buffering=1)`（日志用 **Windows 路径**）。
7. **高精度求解器禁日志**：HiGHS 等 C 库写 fd 1 在 pythonw 下可能阻塞 → 求解器 options 设 `output_flag=False` 等关闭日志。

### 🪟 窗口机制与闪屏抑制（2026-08-18 实证）

- WSL interop（wslhost）**默认以 headless 方式启动控制台程序**：会创建 conhost 进程，但 `MainWindowHandle=0`、不渲染任何窗口。"碰巧看不见"不代表没有控制台副作用（stdio 句柄、C 库写 fd1 等异常依然存在）。
- **CREATE_NEW_CONSOLE (0x10)**：强制分配可见控制台窗口。仅当你确实需要可见窗口时显式使用。
- **CREATE_NO_WINDOW (0x08000000)**：对**新控制的控制台程序**抑制窗口创建（对 pythonw/GUI 无效，且经 wslhost 时行为不稳定——曾被观察到 worker 卡滞，谨慎使用）。
- **"窗口一闪即关"的真相**：几乎全部来自**高频调用的控制台工具**（`tasklist` / `taskkill` / `powershell` / `cmd` / `wmic`）——每次 interop 调用临时创建 conhost，结束即销毁。
- **闪屏抑制**：批量进程查询/清理时**集中化**——单次 PowerShell `Get-CimInstance Win32_Process` 批量过滤 + 按 PID 一次 `Stop-Process`；或用一个常驻 pythonw 代理。避免循环里逐条调 `taskkill`/`tasklist`。
- 判断"当前是否可见"：查 conhost 的 `MainWindowHandle`（0 = headless 不可见）；会话状态：`qwinsta | iconv -f GBK`。

## ⭐ GPU / Python 快速决策（AI 调用规则）

当用户请求"跑 GPU 训练"时按以下流程（完整命令模板见 `references/python-channel.md`）：

1. **必须由用户提供**：① Windows pythonw.exe 绝对路径（自动探测 `ls /mnt/e/venvs/*/Scripts/pythonw.exe` 与 `ls /mnt/e/projects/*/.venv/Scripts/pythonw.exe`；0 个 → AI 自动建 uv venv；多个 → 让用户选）；② 训练代码。
2. **AI 自动推断**：函数选择（"关终端/过夜/后台"→ `launch_detached`；否则 → `stream_gpu_windows`）；`use_wrapper`（>10 分钟 → True）；`gpu_memory_fraction`（单任务 0.4；并发 N 个 → 0.9/N）；`cpu_threads`（核数/并发，最小 2）；`timeout`（stream 3600s）。
3. **调用后必须告知用户**：用的函数 + venv、日志路径、PID、预计完成时间。

**venv 铁律：一律用 `uv` 管理 Windows 侧 venv，禁止 conda**（conda 27 个环境是旧项目遗留，不参与 GPU 流程）；pythonw 必须绝对路径（`E:\venvs\<name>\Scripts\pythonw.exe`），禁止裸名。详见 `references/python-channel.md` → "Windows 侧 Python venv 管理"。

## ⭐ GPU 多路并发铁律（≥2 个 GPU 子进程时必读）

防 OOM 卡死系统。完整规则（显存配额/CPU 线程/进程上限/Job Object/GpuGovernor）见 `references/python-channel.md` → "资源限制" 与 `references/ops-reference.md`。

要点：
1. **显存配额**（最有效）：env var 层 `PYTORCH_CUDA_ALLOC_CONF=garbage_collection_threshold:0.7`（**不要写** `per_process_memory_fraction`——那是 Python API 不是 env var，会报 `Unrecognized CachingAllocator option`）；Python API 层 `torch.cuda.set_per_process_memory_fraction(0.4)`。
2. **CPU 线程约束**：`OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `TOKENIZERS_PARALLELISM=false`（防死锁）。
3. **进程数硬上限**：`sum(每进程 fraction) ≤ 1`（2 进程 × 0.4 = 80% ✅；4 进程 × 0.4 = 160% ❌ 必 OOM）。
4. **Job Object 孤儿防护**（最彻底）：`win-launcher.py` 用 ctypes 调 Job Object API（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），父进程崩溃自动清理子进程。
5. **GpuGovernor 设备级协调**：多任务跨进程总显存 ≤90%，基于 `~/.cache/gpu-governor/ledger.json` + fcntl 文件锁；**只防 OOM 不防算力变慢**（单 GPU 无 MPS，并发任务各慢 30-50% 是物理限制）；**依赖所有 GPU 任务自觉 acquire**（绕过 governor 的进程会破坏不变量）。
6. 现成封装：`scripts/gpu_safe_subprocess.py`（`GpuLimits` / `build_gpu_env` / `run_gpu_windows` / `stream_gpu_windows` / `launch_detached` / `acquire_gpu_slot` / `GpuGovernor`），测试 `test_gpu_governor.py`（12 TDD 测试）。paper-reader 已默认启用 governor。

## UTF-8 三层防护（必读，否则中文乱码）

| 层 | 设置 | 作用 |
|---|---|---|
| ① 进程级 | `PYTHONUTF8=1` | 启用 Python UTF-8 Mode (PEP 540) |
| ② stdout 级 | `PYTHONIOENCODING=utf-8` | 显式指 stdin/stdout/stderr 编码 |
| ③ 命令行级 | `pythonw.exe -X utf8 script.py` | 强制覆盖，优先级最高 |

> ⚠️ **WSLENV 白名单**：上述 env var 必须加入 `WSLENV` 白名单才能传到 Windows 侧，否则静默失效：
> `env["WSLENV"] = "PYTHONUTF8/w:PYTHONIOENCODING/w"`（文档：<https://learn.microsoft.com/en-us/windows/wsl/filesystems#share-environment-variables-between-windows-and-wsl-with-wslenv>）

## When to Use This Skill

- 需要在 WSL 中调用 Windows 侧能力（EXE / Python / GPU / 系统工具）
- 关键词: `cmd.exe`、`powershell.exe`、GPU、CUDA、torch、Windows venv、注册表、WMI、COM、Visio、Office
- GPU 训练流式输出/后台任务: `stream_gpu_windows()`（实时）、`launch_detached()`（后台 + tail -f）、`win-launcher.py`（Job Object 孤儿清理）

## WSLInterop Quick Check

```bash
powershell.exe -Command "Write-Host 'Hello from Windows'"
# 失败（Exec format error）→ WSLInterop 未注册：
echo ':WSLInterop:M::MZ::/init:' | sudo tee /proc/sys/fs/binfmt_misc/register
# 持久化：/etc/wsl.conf → [interop] enabled=true appendWindowsPath=true
```

## Encoding Notes

- **Channel C (cmd.exe) / B (Direct EXE)**: pipe 输出走系统 GBK 编码 → 非 ASCII 乱码。**写文件绕开**。
- **Channel A (PowerShell)**: 设置 `[Console]::OutputEncoding = UTF8` 可正确输出中文。

## 常见错误速查（完整表见 references/ops-reference.md）

| Error | Meaning | Fix |
|-------|---------|-----|
| `Exec format error` | WSLInterop not registered | Register with `binfmt_misc` |
| pythonw 下 print 无输出/卡住 | sys.stdout=None → print 抛异常且 traceback 丢失 | worker 开头显式 `sys.stdout=sys.stderr=open(日志,"a",encoding="utf-8",buffering=1)`（Windows 路径） |
| 求解器（HiGHS 等）在 pythonw 下阻塞 | C 库写 fd1 遇无效句柄 | 设 `output_flag=False`；Popen 重定向 stdout |
| 从 bash 传 Linux 路径给 Windows 进程（FileNotFoundError） | /mnt/e/... 不是 Windows 路径 | 用 `wslpath -w` 转 `E:\\...` |
| `base64: invalid input` | EncodedCommand 需要 UTF-16LE | 先 `iconv -t UTF-16LE` |
| write/edit 工具在 /mnt/e 报 EPERM | NTFS 挂载 chmod 失败 | 改用 bash/python 读写文件 |
| Windows venv py3.11 SyntaxError: f-string unmatched | f-string 嵌套同引号 3.12 前非法 | 统一用单引号 `row['k']` |

## 参考文件索引（按需加载）

| 需要什么 | 读哪个文件 |
|---|---|
| pythonw/cmd 调用模板、后台任务、batch 编排、实时输出（stream/detached/wrapper）、AI 决策详表、GPU 检查、资源限制、venv 管理（uv/CUDA/换源/路径约定） | `references/python-channel.md` |
| PowerShell 独有能力：COM/WMI/P/Invoke/Event Log/剪贴板/计划任务/环境变量 + 调用模式（-Command/-File/-EncodedCommand） | `references/powershell-channel.md` |
| 快速直调工具：reg/sc/tasklist/taskkill/netsh/certutil/schtasks/icacls/takeown/systeminfo + wslu + wsl.exe 自管理 | `references/exe-channel.md` |
| 通道选择详表、环境要求（版本/检查命令）、wslu 安装、WSLInterop 配置 | `references/environment.md` |
| 场景汇总、完整错误处理表、调试策略、限制（9 条必知）、最佳实践（12+ 条） | `references/ops-reference.md` |

## 相关概念

- **HTTP Bridge Pattern**: 复杂自动化考虑 Windows 侧常驻 HTTP server（如 DrawForge's Visio Bridge），避免 process-per-call 开销并提供会话管理。
- **SSH to Windows**: WSLInterop 失败时的替代方案。
- **$env:WSLENV**: WSL 与 Windows 共享的环境变量通道：`export WSLENV=MYVAR/w` 使 `$env:MYVAR` 在 PowerShell 可用。