# 参考：Python 通道（Channel C）— pythonw.exe / cmd.exe

> 本文件是 `SKILL.md` 的按需加载参考（渐进式披露）。需要 GPU/Python 调用、实时输出、
> 后台任务、venv 管理时读取。核心决策与安全规则见 SKILL.md 正文。

## Channel C: pythonw.exe / cmd.exe — GPU & Python（⭐ 首选）

### ⭐ 首选：pythonw.exe 直调（无弹窗）

`pythonw.exe` 是 Windows 原生无控制台 Python 解释器，被外部进程启动时**根本不会弹 cmd 窗口**。`python.exe` 自带控制台会弹窗。WSL 调 GPU/Python 实验一律优先 pythonw。

```python
import subprocess, os

def _run_windows_headless(pyw_exe, script_or_code, args=None, timeout=1800, cwd="/mnt/e/temp"):
    """pythonw.exe 直调，永不弹窗。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["WSLENV"] = "PYTHONUTF8/w:PYTHONIOENCODING/w"
    cmd = [pyw_exe, "-u", "-X", "utf8"]
    # 区分 -c code 模式 vs 脚本路径模式
    if script_or_code.lstrip().startswith("import ") or "\n" in script_or_code:
        cmd += ["-c", script_or_code]
    else:
        cmd += [script_or_code]
    cmd += (args or [])
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
        cwd=cwd, env=env,
    )

# 使用示例
r = _run_windows_headless(
    r"E:\venvs\marker\Scripts\pythonw.exe",
    "import torch; print('CUDA:', torch.cuda.is_available())",
)
```

### fallback：cmd.exe /c（仅当需要 shell 内置重定向时）

```python
import subprocess, os

def _run_windows(python_exe, code, args=None, timeout=1800):
    """cmd.exe /c fallback — 会弹 cmd 窗口，仅当需要 shell 重定向语法时用。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = ["cmd.exe", "/c", python_exe, "-c", code] + (args or [])
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
        cwd="/mnt/e/temp",   # 必须：避免 UNC 路径报错
        env=env,
    )
```

**注意事项：**
- `cwd` 必须设为 `/mnt/` 下共享盘路径，否则 `cmd.exe` 报 UNC 不支持（pythonw 直调没此限制但同样建议走 `/mnt` 路径）
- 中文输出需写文件（pipe 输出走系统 GBK 编码）：
  ```python
  log_f = open(win_log_path, "wb")  # WSL 端打开文件对象
  p = subprocess.Popen([pyw_exe, "-u", "-X", "utf8", script, *args],
                       stdout=log_f, stderr=log_f, env=env)
  # 完成后从 WSL 侧读 UTF-8 日志
  ```
- 退出码、参数传递、stderr 均正确（已验证）

### UTF-8 三层防护（必读，否则中文乱码）

| 层 | 设置 | 作用 |
|---|---|---|
| ① 进程级 | `PYTHONUTF8=1` | 启用 Python UTF-8 Mode (PEP 540) |
| ② stdout 级 | `PYTHONIOENCODING=utf-8` | 显式指 stdin/stdout/stderr 编码 |
| ③ 命令行级 | `pythonw.exe -X utf8 script.py` | 强制覆盖，优先级最高 |

> ⚠️ **WSLENV 白名单**：上述 env var 必须加入 `WSLENV` 白名单才能传到 Windows 侧 Python，否则静默失效。
> `env["WSLENV"] = "PYTHONUTF8/w:PYTHONIOENCODING/w"`
> 文档：<https://learn.microsoft.com/en-us/windows/wsl/filesystems#share-environment-variables-between-windows-and-wsl-with-wslenv>

### 后台长时间任务（会话关闭不中断）

`pythonw.exe` 或 `cmd.exe /c` 启动的 Windows 进程在 WSL 父进程被杀后不受影响（实测 SIGHUP/SIGKILL 存活）。用 `Popen` 非阻塞启动，输出重定向到文件：

```python
import subprocess

# 启动（不等待）— pythonw.exe 版（无弹窗）
p = subprocess.Popen(
    [pyw_exe, "-u", "-X", "utf8", script] + args,
    stdout=log_f, stderr=log_f,
    cwd="/mnt/e/temp", env=env,
)
# p.pid 是 WSL bash 进程 — 退出时不影响 Windows 子进程

# 让训练脚本自己写 Windows PID 到文件
# Python 中: open(r"E:\temp\train_pid.txt","w").write(f"{os.getpid()}\n")
```

**监控/终止**：
```bash
nvidia-smi                                        # 查看 GPU
taskkill.exe /F /PID $(cat /mnt/e/temp/pid.txt)  # 终止
tail -f /mnt/e/temp/train.log                     # 日志
```
### 实验编排与批处理（多任务/并发/断点续跑，2026-08-18 实战沉淀）

多算例/多任务并发的正确姿势（供批量实验、训练复现参考）：

1. **batch worker 内部循环**（推荐）：每算例一进程的调度器常见坑——WSL 侧 `kill -0 $pid` 检测不到 Windows worker 完成、orchestrator 子进程被 run_code abort 连带杀、pkill 匹配自身自杀。**更稳**：单个 worker 进程内 `for inst in instances: ...` 循环处理多个算例，结果 JSON 行逐条 append 到共享盘文件；用 `nohup bash launch.sh &`（内 `wait $P1 $P2 $P3`）启动 3-4 个 worker 并行。
2. **worker 内显式绑定 stdio**（必做）：pythonw 下 `sys.stdout=None`，`print()` 抛异常且 traceback 丢失（表现为"卡住"）。worker 开头：`sys.stdout = sys.stderr = open(log_path, 'a', encoding='utf-8', buffering=1)`（log_path 用 **Windows 路径**）。
3. **C 库/求解器禁日志**：HiGHS 等写 fd1 在 pythonw+无重定向下可能阻塞 → `milp options={..., "output_flag": False}`。
4. **MIP 返回防护**：`res.x` 可能为 None（HiGHS status 2/3/4，如无可行解）→ 必须 `if res.status in (0,1) and res.x is not None` 再解析，否则 worker 崩溃、数据静默丢失。
5. **内存/CPU**：WSL 13GB / Windows 34GB——大变量 MIP 先看进程内存再并行；多 worker 并发会比串行慢（先采样 CPU 增长判断'慢'还是'卡'，不要急着 kill）。
6. **断点续跑**：结果文件写 done 清单（`scenario` 一行），重启时跳过已完成，天然续跑。
7. **Python 版本兼容**：Windows venv 常用 3.11，`f"{{row["k"]}}"` 嵌套同引号会 SyntaxError（3.12+ 才允许）→ 统一用单引号 `row['k']`。
8. **单实例**：常驻调度进程必须单实例（`pgrep -f <脚本> | grep -v $$` 精确匹配并排除自身再 kill）。

### 实时输出与进度条（⭐ 长任务必读）

WSL interop 使用匿名管道（非 PTY）通信，训练脚本输出经常"卡住"：tqdm 检测到 stdout 非 TTY 后自动静默，`\r` 进度条在 pipe 中被缓冲。本节提供四种模式解决实时输出问题。

#### 四模式决策树

| 模式 | 调用方式 | 输出流向 | 孤儿进程防护 | 适用场景 |
|------|---------|---------|------------|---------|
| **stream** | `stream_gpu_windows(...)` | WSL 端 `select.select` 逐行读取 | 无（SIGHUP 后 Windows 进程继续） | 交互调试、需要实时看 tqdm |
| **detached** | `launch_detached(...)` → `tail -f /tmp/gpu-logs/<job>.log` | Linux FS 日志文件（inotify 实时） | 无 | 长时间训练、关闭 WSL 会话后继续 |
| **stream+wrapper** | `stream_gpu_windows(..., use_wrapper=True)` | 同 stream | ⭐ Job Object + KILL_ON_JOB_CLOSE | stream 模式 + 需自动清理 |
| **detached+wrapper** | `launch_detached(..., use_wrapper=True)` | 同 detached | ⭐ Job Object + KILL_ON_JOB_CLOSE | detached 模式 + 需自动清理 |

#### 进度条环境变量

`build_gpu_env()` 自动注入以下 5 个环境变量，在 `run_gpu_windows()` / `stream_gpu_windows()` / `launch_detached()` 内部自动调用（通过 `build_gpu_env()`）。手动覆盖场景见右列。

| 环境变量 | 目的 | 默认值 | 覆盖时机 |
|---------|------|-------|---------|
| `PYTHONUNBUFFERED` | 禁用 Python stdout 缓冲 | `1`（自动设置） | 不需要覆盖 |
| `TQDM_DISABLE` | 启用 tqdm 进度条 | 不设（tqdm 默认 disable=False→启用） | 用户想关进度条时设 `TQDM_DISABLE=1` |
| `TTY_COMPATIBLE` | tqdm 启用 `\r` 进度条 refresh | `1` | 不需要覆盖 |
| `TTY_INTERACTIVE` | 标记非交互式，tqdm 走 \r 行内刷新 | `0` | 不需要覆盖 |
| `HF_HUB_DISABLE_PROGRESS_BARS` | 禁用 HuggingFace hub 进度条（与 tqdm 冲突时） | `1`（禁用 HF 进度条） | 单独下载模型时改 `0` |

> **原理**：WSL interop 的 `CreatePipe` 返回匿名管道，`isatty()` 为 False。tqdm 源码 `tqdm/std.py#L118-120` 检测到非 TTY 时自动 `disable=None` 导致无输出。`TTY_COMPATIBLE=1` 强制 tqdm 认为 stdout 可交互，恢复 `\r` 进度条刷新。

#### stream 模式（直读实时输出）

```python
from gpu_safe_subprocess import GpuLimits, stream_gpu_windows

limits = GpuLimits(gpu_memory_fraction=0.4, cpu_threads=6)

# 逐行实时输出 — select.select 轮询 + deadline 检查
for line in stream_gpu_windows(
    py_exe=r"E:\venvs\train\Scripts\pythonw.exe",
    code="import train; train.main()",
    limits=limits,
    timeout=3600,
):
    # line 已经过 \r 清洗：多个 \r 覆盖的行合并为最终状态
    # 例如 tqdm 的 " 30%|███       | 30/100 [00:15<00:35, 2.00it/s]"
    print(line.text, end="", flush=True)
```

**`\r` 清洗说明**：管道传输保留了原始 `\r` 字符。`stream_gpu_windows` 内部以 `\r` 和 `\n` 为分隔符拆行，同一 `\r` 段内只保留最后一段文本（即该行当前状态），避免日志文件中出现同一进度条的多份残留副本。调用方收到的每条 `line` 已经是最终可读文本。

#### detached 模式（后台静默 + tail -f）

```bash
# 一键：启动后台训练 + 监控日志 + 终止
python3 -c "
from gpu_safe_subprocess import GpuLimits, launch_detached
launch_detached(
    py_exe=r'E:\venvs\train\Scripts\pythonw.exe',
    code='import train; train.main()',
    limits=GpuLimits(gpu_memory_fraction=0.4, cpu_threads=6),
    job_name='train_bert',   # → /tmp/gpu-logs/train_bert_<timestamp>.log
)
" && tail -f /tmp/gpu-logs/train_bert_*.log
# Ctrl+C 后 Windows 进程继续运行。终止：
taskkill.exe /F /PID $(cat /tmp/gpu-logs/train_bert_*.pid)
```

> ⚠️ **WSL#4739**：`/mnt/` 路径下的 `tail -f` 使用 polling 模式（~1s 延迟），因为 WSL 的 `/mnt/` 是 DrvFs 挂载，不支持 inotify。**必须**将日志写到 Linux 原生文件系统（`/tmp/gpu-logs/`）才能获得 inotify 实时推送。`launch_detached` 默认写到 `/tmp/gpu-logs/`，自动满足此要求。

#### wrapper + Job Object（⭐ 孤儿进程防护）

默认情况下，WSL 端父进程退出后 Windows 侧子进程继续运行（成为孤儿），需手动 `taskkill.exe` 清理。对于频繁启停的调参场景或多进程训练，这会导致 GPU 显存泄漏。

**解决方案**：Windows 侧 `win-launcher.py` 创建 Job Object（通过 ctypes 调 `CreateJobObjectW` + `SetInformationJobObject`），设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 标志。WSL 端通过管道监控 wrapper 进程，wrapper 退出时 OS 级强制终止整个 Job Object 内的所有子进程。

```python
# stream + wrapper：调试时 Ctrl+C 自动清理 Windows 进程
for line in stream_gpu_windows(
    py_exe=r"E:\venvs\train\Scripts\pythonw.exe",
    code="import train; train.main()",
    limits=limits,
    use_wrapper=True,  # ← 启用 Job Object 防护
):
    print(line.text, end="", flush=True)
# WSL 端 Ctrl+C → wrapper 退出 → Job Object 内所有进程被 OS 终止

# detached + wrapper：后台运行 + 会话关闭自动清理
launch_detached(
    py_exe=r"E:\venvs\train\Scripts\pythonw.exe",
    code="import train; train.main()",
    limits=limits,
    use_wrapper=True,  # ← 启用 Job Object 防护
    job_name="train_bert",
)
# WSL 会话关闭 → wrapper 管道断开 → Job Object 子进程被 OS 回收
```

**四模式扩展决策（含 wrapper）**：

| 模式 | 适用场景 | 孤儿防护 |
|------|---------|---------|
| stream（无 wrapper） | 短时一次性任务、手动管理生命周期 | 无 |
| detached（无 wrapper） | 有监控脚本定时清理、单次训练 | 无 |
| stream + wrapper | 频繁 Ctrl+C 的调试/调参 | ⭐ Job Object |
| detached + wrapper | 长时间无人值守训练、会话可能异常断开 | ⭐ Job Object |

**何时使用 wrapper**：
- ✅ 频繁启停（调参、debug）→ 避免 `nvidia-smi` 里残留僵尸进程
- ✅ 长时间训练（>1h）→ 会话意外断开时自动释放 GPU
- ✅ 多进程编排 → 一个 Job Object 管一组进程，统一生命周期

**何时不使用 wrapper**：
- ❌ 短时一次性任务（<5min）→ 手动 `taskkill.exe` 更快
- ❌ 需要进程在 WSL 退出后继续 → detached 模式无 wrapper 即是此用途

#### AI 调用规则（⭐ 自动决策）

当用户请求"跑 GPU 训练"时，AI 按以下流程决策：

**必须由用户提供（缺一不可）**：

1. **Windows pythonw.exe 路径**：用户的项目 venv 路径，如 `E:\projects\xxx\.venv\Scripts\pythonw.exe`
   - 自动探测：`ls /mnt/e/venvs/*/Scripts/pythonw.exe 2>/dev/null` 和 `ls /mnt/e/projects/*/.venv/Scripts/pythonw.exe 2>/dev/null`
   - 找到 1 个 → 直接用并告知用户
   - 找到多个 → 列出让用户选
   - 找到 0 个 → **AI 自动创建 venv**（见下方"Windows 侧 venv 管理"章节），无需用户手动操作
   - 用户也可直接指定路径（如"E:\projects\myproj\.venv"），AI 直接用
   - ⚠️ **禁止用裸名 `pythonw.exe`**：WSL interop 会按 Windows PATH 命中 `D:\Python311`（未装 torch）或 `D:\Anconda`（conda），导致缺包或环境混乱。**必须用 uv venv 的绝对路径。**
   - ✅ **强制 pythonw.exe**：必须用 `pythonw.exe`；严禁 `python.exe`（控制台子系统必弹窗）。若只找到 python.exe，`run_gpu_windows`/`_ensure_pythonw` 会自动替换同目录 pythonw.exe（HEADLESS 铁律，见上），替换失败则报错。
   - ⚠️ **禁止用 conda**：此 SKILL 一律用 uv 管理 venv。conda 的 27 个环境（`D:\Anconda\envs\`）是旧项目用的，不参与 GPU 训练。需要创建新环境时用 `uv.exe venv`（见下方"Windows 侧 venv 管理"章节）。
2. **训练代码**：脚本内容或脚本路径

**AI 自动推断（不需问用户）**：

| 参数 | 推断规则 | 默认值 |
|------|---------|--------|
| 函数选择 | 用户说"关终端/过夜/后台" → `launch_detached`；否则 → `stream_gpu_windows` | stream |
| `use_wrapper` | 任务 >10 分钟 → `True`；否则 `False` | False |
| `gpu_memory_fraction` | `nvidia-smi` 查显存，单任务 0.4；并发 N 个 → `0.9/N` | 0.4 |
| `cpu_threads` | `nproc` 查核数，`核数/并发数`，最小 2 | 6 |
| `timeout` | stream 模式 3600s；detached 不设 | 3600 |
| `log_dir` | cwd 在项目目录 → `Path("log")`；否则 → `/tmp/gpu-logs` | 自适应 |

**调用后必须告知用户**：
- 用的哪个函数 + 哪个 venv
- 日志路径（方便 `tail -f`）
- PID（方便 `taskkill.exe /F /PID <pid>`）
- 预计完成时间（如代码含 epoch 数）

**Windows 侧手动验证清单**（确认 wrapper 生效）：

1. 启动 wrapper 后，打开 Windows 任务管理器 → 详细信息 → 查找 `pythonw.exe`（应出现在 Job Object 内）
2. WSL 端 Ctrl+C 或关闭终端 → 等待 3 秒 → 任务管理器中对应 `pythonw.exe` 进程消失
3. 在 WSL 中运行 `nvidia-smi` → 确认 `No running processes found`（显存释放）
4. 异常断开测试：`kill -9` WSL 父 bash 进程 → 10 秒内 Windows 侧进程自动终止（wrapper 管道断开触发）

### GPU 环境检查 & 规则

> **GPU 相关全部统一在 `/home/dc/CLAUDE.md` → "GPU 桥接" 章节**，此处不再重复。
> 特别注意 **"GPU 多路并发铁律"**（≥2 个 GPU 子进程时必读，防 OOM 卡死系统）。

快速验证（pythonw.exe 版，无弹窗 + UTF-8）：
```bash
python3 -c "
import subprocess, os
env = os.environ.copy()
env['PYTHONUTF8']='1'; env['PYTHONIOENCODING']='utf-8'
env['WSLENV']='PYTHONUTF8/w:PYTHONIOENCODING/w'
r = subprocess.run(
    [r'E:\venvs\marker\Scripts\pythonw.exe', '-u', '-X', 'utf8', '-c',
     'import torch; print(\"CUDA:\", torch.cuda.is_available(), \"| GPU:\", torch.cuda.get_device_name(0))'],
    capture_output=True, text=True, encoding='utf-8', errors='replace',
    cwd='/mnt/e/temp', env=env, timeout=30)
print(r.stdout)
"
```

### 资源限制（多路 GPU 子进程必备）

当一次启动 ≥2 个 Windows GPU 子进程时，**必须**给每个子进程注入显存配额 + CPU 线程约束。规则全文在 `/home/dc/CLAUDE.md` → "GPU 多路并发铁律"，此 skill 提供现成封装。

**通用模块**：`~/projects/dc-skills/wsl-windows-bridge/scripts/gpu_safe_subprocess.py`

```python
from gpu_safe_subprocess import GpuLimits, run_gpu_windows, acquire_gpu_slot

# 每个 GPU 子进程上限：40% 显存 + 6 CPU 线程
limits = GpuLimits(gpu_memory_fraction=0.4, cpu_threads=6)

# 串行：直接调用
r = run_gpu_windows(
    py_exe=r"E:\venvs\marker\Scripts\pythonw.exe",  # 优先 pythonw，无弹窗
    code="from marker.scripts.convert_single import convert_single_cli; import sys; sys.exit(convert_single_cli())",
    args=[win_pdf, "--output_dir", win_out],
    limits=limits,
    timeout=1800,
)

# 并发：用 Semaphore 限流
with acquire_gpu_slot(max_concurrent=2):
    run_gpu_windows(...)
```

模块原理：env var 层注入 `PYTORCH_CUDA_ALLOC_CONF=garbage_collection_threshold:0.7`（PyTorch 实测接受的选项），Python API 层通过 bootstrap 调 `torch.cuda.set_per_process_memory_fraction(0.4)`。

> ⚠️ **不要写** `PYTORCH_CUDA_ALLOC_CONF=per_process_memory_fraction:0.4` —— 这是 Python API 不是 env var，PyTorch 会报 `Unrecognized CachingAllocator option`。详见 CLAUDE.md "GPU 显存配额" 段。

### Windows 侧 Python venv 管理（⭐ uv 唯一，禁止 conda）

> **铁律：此 SKILL 一律使用 `uv` 管理 Windows 侧 Python venv，禁止使用 conda。**
> 用户机器虽然有 Anaconda（`D:\Anconda`，27 个 conda 环境），但那些是旧项目用的，
> GPU 训练/推理一律走 uv venv。conda 环境不参与此 SKILL 的任何流程。

#### 为什么禁用 conda

| 问题 | conda | uv |
|------|-------|-----|
| WSL 调用延迟 | ~600ms（conda 是 Python 脚本） | ~50ms（uv 是 Rust 二进制） |
| 安装速度 | 慢（Python solver，串行） | 极快（Rust，并行下载） |
| CUDA PyTorch | `conda install pytorch pytorch-cuda=12.8 -c pytorch -c nvidia` | `uv pip install torch --index-url https://download.pytorch.org/whl/cu128` |
| venv 隔离 | `envs/` 目录，元数据庞大 | 标准 `.venv/`，轻量 |
| PATH 污染 | conda 在 PATH 加了 7 个条目，优先级高 | uv 只加 1 个 `.local/bin` |
| 跨工具兼容 | conda activate 会修改 PATH，与 WSL interop 冲突 | uv venv 不动 PATH |

#### ⚠️ PATH 陷阱（AI 必读）

用户机器的 Windows PATH 有以下 Python：

| 优先级 | 路径 | 说明 |
|:------:|------|------|
| 🥇 最高 | `D:\Python311\pythonw.exe` | 裸 Python 3.11，**未装 torch**，pip 全局污染 |
| 🥈 | `D:\Anconda\pythonw.exe` | conda base，**27 个 conda 环境**，不走 uv |
| 🥉 | `C:\Users\32841\...\Python312\pythonw.exe` | Python 3.12，很少用 |

**当 AI 写 `pythonw.exe -c "import torch"` 时，WSL interop 按 PATH 命中 `D:\Python311`，那里没装 torch → `ModuleNotFoundError`。**

**正确做法**：始终用 **绝对路径** 指定 uv venv 的 pythonw.exe：
```python
# ✅ 正确：绝对路径，不依赖 PATH
stream_gpu_windows(py_exe=r"E:\venvs\marker\Scripts\pythonw.exe", ...)

# ❌ 错误：裸名走 PATH，命中 D:\Python311 → 缺包
stream_gpu_windows(py_exe="pythonw.exe", ...)
```

#### uv.exe 路径

```
C:\Users\32841\.local\bin\uv.exe
```
版本：0.11.28（2026-07-07）。也有 WinGet 安装的副本，用哪个都行（同版本）。

#### 探测已有 uv venv

```bash
# 探测 E:\venvs\ 下的 uv venv
ls /mnt/e/venvs/*/Scripts/pythonw.exe 2>/dev/null

# 探测项目 .venv
ls /mnt/e/projects/*/.venv/Scripts/pythonw.exe 2>/dev/null
```

#### 创建新 venv + 安装 PyTorch（AI 自动化流程）

当探测不到已有 venv 时，AI 直接执行以下命令创建——**全程用 uv，不用 conda**：

```bash
# 1. 创建 venv（Python 3.11，稳定）
powershell.exe -Command "& 'C:\Users\32841\.local\bin\uv.exe' venv E:\projects\<projname>\.venv --python 3.11"

# 2. 安装 CUDA 版 PyTorch（cu128 稳定，cu130 有 DLL 兼容问题）
powershell.exe -Command "& 'C:\Users\32841\.local\bin\uv.exe' pip install torch torchvision --python E:\projects\<projname>\.venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cu128"

# 3. 安装常用训练依赖
powershell.exe -Command "& 'C:\Users\32841\.local\bin\uv.exe' pip install tqdm transformers numpy --python E:\projects\<projname>\.venv\Scripts\python.exe"

# 4. 验证 CUDA 可用
powershell.exe -Command "& 'E:\projects\<projname>\.venv\Scripts\pythonw.exe' -c 'import torch; print(\"CUDA:\", torch.cuda.is_available(), torch.cuda.get_device_name(0))'"
```

> ⚠️ **CUDA 版本选择**：
> - `cu128`（CUDA 12.8）：**推荐**，Windows 稳定，RTX 40/50 系列全支持
> - `cu130`（CUDA 13.0）：有 Windows DLL 兼容问题，暂不推荐
> - PyTorch 必须用官方 `download.pytorch.org` 源（清华镜像没有 CUDA wheel）

#### pip 换源（国内安装慢时）

PyTorch 本身必须用官方源（`download.pytorch.org`），但普通依赖可换清华镜像：
```bash
powershell.exe -Command "& 'C:\Users\32841\.local\bin\uv.exe' pip install tqdm numpy --python E:\projects\<projname>\.venv\Scripts\python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple"
```

#### venv 路径约定

| 场景 | 推荐路径 | 说明 |
|------|---------|------|
| 项目专用 | `E:\projects\<projname>\.venv\` | 随项目走，gitignore |
| 多项目共享 | `E:\venvs\<name>\` | 如 marker、mineru 等重型 venv |
| 临时实验 | `E:\venvs\temp\` | 用完可删 |
