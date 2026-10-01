---
name: cloudreve-storage
description: >
  Cloudreve 云存储网盘操作与备份管理工具：支持文件浏览(ls/tree)、读取(cat)、上传下载(put/get)、移动重命名(mv)、删除(rm)及基于统一产物树的规范化备份与恢复(backup/restore/prune)。当用户需要上传、下载、浏览 Cloudreve 网盘文件，或对生成产物与状态文件进行云端归档与恢复时触发。
metadata:
  version: "0.1"
  family: agentops
  role: member
  load-mode: manual
disable-model-invocation: true
---

# Cloudreve Storage

Cloudreve 云存储网盘操作与备份管理工具，基于 WebDAV / REST 协议封装，提供安全的云盘管理与自动化归档流水线支持。

## 能力概述

- **状态与配额探测** (`status` / `doctor`)：检测与 Cloudreve 的 WebDAV 连接连通性，自动解析展示已用、剩余及总计存储空间容量。
- **文件与目录浏览** (`ls` / `tree`)：多层级目录枚举与递归目录树展示，支持 JSON 结构化输出。
- **文件流式读写** (`cat` / `put` / `get`)：支持流式读取文本内容（分行或分字节截断）；支持本地大文件流式上传下载，自动跟随 COS 签名重定向。
- **原子性与防护**：上传采用 `.part` 临时文件先行上传再通过 WebDAV MOVE 原子重命名；下载采用本地临时文件校验后原子替换，防止网络中断产生残损半文件。
- **文件整理与清理** (`mv` / `rm`)：远端移动/重命名、文件删除、递归目录删除及 `--dry-run` 预览防护。
- **规范化备份与恢复** (`backup` / `restore` / `prune`)：按照统一命名规范备份至云端沙箱，自动计算 SHA256，生成审计 Manifest 并对齐 `OUTPUT.md` 统一产物树；提供过期快照保留清理。

## 触发场景

- 当用户需要将本地生成的成果物、日志、数据库导出或配置归档至 Cloudreve 网盘时。
- 当用户需要查看 Cloudreve 网盘存储空间配额、浏览目录文件树或下载云端文件时。
- 当用户需要从云端历史备份快照恢复特定版本文件，并校验 SHA256 完整性时。
- 当用户需要定期清理远端过期备份快照以节省云存储配额时。

## 核心安全防御机制

为防止意外误操作破坏用户个人网盘中的其他私有数据，本技能设立两道防御红线：

1. **默认安全沙箱隔离**：
   - 默认操作限定在沙箱路径（默认为 `/AgentBackups/`，可配置）。
   - 任何涉及读取、上传、删除、移动超出沙箱范围的路径，客户端默认直接拦截并拒绝执行。
   - 若用户确需操作其他目录（如 `/公共资源/`），必须显式附加 `--outside-sandbox` 标志。
2. **高危删除阻断**：
   - 严禁对根目录 `/`、WebDAV 根 `/dav/` 或沙箱根目录 `/AgentBackups/` 执行删除操作。
   - 非空目录删除必须显式附加 `-r` / `--recursive`；删除前可使用 `--dry-run` 进行预览。

## 配置与凭据管理

配置解析优先级：**CLI 参数 > 环境变量 > 配置文件 > 内置默认值**。

### 1. 配置文件

默认读取 `~/.config/cloudreve-storage/config.toml`（建议文件权限 `0600`）。可通过以下命令快速生成模板：

```bash
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py init-config
```

配置文件示例内容：

```toml
base_url = "https://cloudreve.dc-sy.cn"
webdav_path = "/dav/"
username = "dcsy1314@qq.com"
password = "your_app_specific_password_here"
root_sandbox = "/AgentBackups/"
# host_id = "my-host"  # 留空自动使用当前机器短主机名
```

### 2. 环境变量

- `CLOUDREVE_CONFIG`: 显式配置文件路径
- `CLOUDREVE_BASE_URL`: Cloudreve 站点地址（默认 `https://cloudreve.dc-sy.cn`）
- `CLOUDREVE_WEBDAV_PATH`: WebDAV 挂载路径（默认 `/dav/`）
- `CLOUDREVE_USERNAME`: WebDAV 用户名
- `CLOUDREVE_PASSWORD`: WebDAV 应用专用密码
- `CLOUDREVE_ROOT_SANDBOX`: 沙箱根目录（默认 `/AgentBackups/`）
- `CLOUDREVE_HOST_ID`: 设备主机标识

## 快速使用指南

所有命令必须遵循执行规则：先进入 `~/projects/dc-skills` 目录再通过 `uv run` 执行。

### 1. 连接状态与空间配额检测

```bash
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py status
```

### 2. 浏览与文件查看

```bash
# 列出沙箱根目录内容
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py ls /AgentBackups/

# 递归展示目录树结构（深度 3）
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py tree /AgentBackups/ --max-depth 3

# 读取远端文本文件内容（前 20 行）
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py cat /AgentBackups/notes.txt --head 20
```

### 3. 普通上传与下载

```bash
# 上传本地文件到云盘（原子上传）
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py put ./report.pdf /AgentBackups/docs/report.pdf

# 下载远端文件到本地目录
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py get /AgentBackups/docs/report.pdf ./downloads/
```

### 4. 规范化备份流水线 (backup)

将本地重要文件备份至远端设备隔离的快照路径：
远端规范路径：`/AgentBackups/<host_id>/<category>/<YYYYMMDD>/<HHMMSS>_<sha256前12位>/<filename>`

```bash
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py backup ./database.dump --category db --tag nightly
```

执行后将自动：
- 计算本地源文件 SHA256 哈希值与大小。
- 上传并归档至特定分类与日期的快照目录。
- 遵循 `OUTPUT.md` 规约在 `<cwd>/skills-output/agentops/cloudreve-storage/<时间戳>/backup-manifest.json` 生成 Manifest，并在主库同步审计副本。

### 5. 从云端快照恢复 (restore)

```bash
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py restore /AgentBackups/myhost/db/20261002/120000_3a8b2c/database.dump --output ./restored.dump
```

恢复流程自动比对原快照记录的 SHA256，确保数据未在传输或存储期间发生比特损坏。

### 6. 清理过期备份 (prune)

```bash
# 预览清理拟删除的快照列表（每分类保留最新的 5 份）
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py prune --category db --keep 5 --dry-run

# 确认执行清理
cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py prune --category db --keep 5 -y
```

## 输出规范说明 (OUTPUT.md)

本技能产物完全遵循 `docs/specs/OUTPUT.md` 统一产物树规约：
- 主输出参数统一为 `--output`。
- 默认 Manifest 落盘路径为 `<cwd>/skills-output/agentops/cloudreve-storage/<时间戳>/backup-manifest.json`。
- 若在外部工作项目目录执行，产物落盘后自动向主库统一树 `<master>/skills-output/agentops/cloudreve-storage/<时间戳>/` 复制审计副本（`commit_final`），形成设备操作追溯日志。
