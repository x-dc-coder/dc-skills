#!/usr/bin/env bash
#
# win-ssh-gate.sh - WSL 经由 SSH + schtasks 投递任务至 Windows 交互会话执行
#
# 解决的问题：
#   Windows OpenSSH 服务运行在 Session 0（服务会话），无法直接运行带有可见窗口的
#   GUI 自动化测试（如 pywinauto、Qt 控件树探测）。
#   本脚本通过 SSH（管理员令牌）在 Windows 上排程并触发 Session 1 计划任务，
#   等待其在用户真实桌面执行完毕并回收结果。
#

set -euo pipefail

# 默认参数
SSH_KEY="${HOME}/.ssh/wp_win_sshd_ed25519"
SSH_USER="32841"
SSH_HOST="127.0.0.1"
SSH_PORT="22"
TIMEOUT_S=300
TASK_NAME="WP-SSH-Gate"
WORKDIR_WIN='E:\AllProjects202604\Words-Production'
WORKDIR_WSL='/mnt/e/AllProjects202604/Words-Production'

usage() {
    cat <<EOF
用法: $0 [选项] -- <Windows 命令行>

选项:
  --key PATH         SSH 私钥路径 (默认: $SSH_KEY)
  --user USER        Windows 用户名 (默认: $SSH_USER)
  --host HOST        SSH 地址 (默认: $SSH_HOST)
  --port PORT        SSH 端口 (默认: $SSH_PORT)
  --timeout SEC      等待超时秒数 (默认: $TIMEOUT_S)
  --task-name NAME   一次性计划任务名称 (默认: $TASK_NAME)
  --workdir PATH     Windows 工作目录 (默认: $WORKDIR_WIN)
  -h, --help         显示本帮助信息

示例:
  $0 -- ".venv-win\\Scripts\\python.exe -m pytest tests\\workbench\\test_ui_driver.py -q"
EOF
    exit 1
}

# 解析参数
CMD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key) SSH_KEY="$2"; shift 2 ;;
        --user) SSH_USER="$2"; shift 2 ;;
        --host) SSH_HOST="$2"; shift 2 ;;
        --port) SSH_PORT="$2"; shift 2 ;;
        --timeout) TIMEOUT_S="$2"; shift 2 ;;
        --task-name) TASK_NAME="$2"; shift 2 ;;
        --workdir) WORKDIR_WIN="$2"; shift 2 ;;
        --) shift; CMD_ARGS=("$@"); break ;;
        -h|--help) usage ;;
        *) CMD_ARGS=("$@"); break ;;
    esac
done

if [[ ${#CMD_ARGS[@]} -eq 0 ]]; then
    echo "[ERROR] 未指定要执行的 Windows 命令" >&2
    usage
fi

TARGET_CMD="${CMD_ARGS[*]}"

# 检查私钥
if [[ ! -f "$SSH_KEY" ]]; then
    echo "[ERROR] SSH 私钥不存在: $SSH_KEY" >&2
    exit 1
fi

SSH_BASE=(
    ssh
    -i "$SSH_KEY"
    -p "$SSH_PORT"
    -o IdentitiesOnly=yes
    -o BatchMode=yes
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
    -o ConnectTimeout=10
    "${SSH_USER}@${SSH_HOST}"
)

# 1. 验证 SSH 连通性
if ! "${SSH_BASE[@]}" "cmd /c echo ping" >/dev/null 2>&1; then
    echo "[ERROR] 无法通过 SSH 连接至 ${SSH_USER}@${SSH_HOST}:${SSH_PORT}" >&2
    exit 2
fi

# 2. 准备临时批处理与状态文件
STAMP="$(date +%Y%m%d_%H%M%S)_$$"
TMP_DIR_WSL="${WORKDIR_WSL}/.wp/tmp/ssh_gate_${STAMP}"
TMP_DIR_WIN="${WORKDIR_WIN}\\.wp\\tmp\\ssh_gate_${STAMP}"

mkdir -p "$TMP_DIR_WSL"

RUN_CMD_WSL="${TMP_DIR_WSL}/run.cmd"
EXIT_TXT_WSL="${TMP_DIR_WSL}/exit.txt"
LOG_TXT_WSL="${TMP_DIR_WSL}/output.log"

RUN_CMD_WIN="${TMP_DIR_WIN}\\run.cmd"
EXIT_TXT_WIN="${TMP_DIR_WIN}\\exit.txt"
LOG_TXT_WIN="${TMP_DIR_WIN}\\output.log"

cat > "$RUN_CMD_WSL" <<EOF
@echo off
cd /d ${WORKDIR_WIN}
${TARGET_CMD} > "${LOG_TXT_WIN}" 2>&1
echo %ERRORLEVEL% > "${EXIT_TXT_WIN}"
EOF

# 确保退出时清理计划任务和临时目录
cleanup() {
    "${SSH_BASE[@]}" "schtasks /Delete /F /TN ${TASK_NAME} >nul 2>&1" || true
    rm -rf "$TMP_DIR_WSL" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# 3. 经由 SSH 创建并立即启动计划任务（在 Session 1 桌面环境运行）
"${SSH_BASE[@]}" "schtasks /Create /F /TN ${TASK_NAME} /TR \"cmd /c ${RUN_CMD_WIN}\" /SC ONCE /ST 23:59 >nul && schtasks /Run /TN ${TASK_NAME} >nul"

# 4. 轮询等待任务完成
ELAPSED=0
INTERVAL=2
while [[ $ELAPSED -lt $TIMEOUT_S ]]; do
    if [[ -f "$EXIT_TXT_WSL" && -s "$EXIT_TXT_WSL" ]]; then
        break
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [[ ! -f "$EXIT_TXT_WSL" || ! -s "$EXIT_TXT_WSL" ]]; then
    echo "[ERROR] 执行超时 (${TIMEOUT_S}s)，任务未在预期时间内完成" >&2
    exit 124
fi

# 5. 输出执行日志并获取远程退出码
if [[ -f "$LOG_TXT_WSL" ]]; then
    cat "$LOG_TXT_WSL"
fi

REMOTE_EXIT="$(tr -d '\r\n ' < "$EXIT_TXT_WSL")"
exit "${REMOTE_EXIT:-1}"
