"""备份与恢复流水线支持模块。

负责 SHA256 计算、统一产物树审计对接、快照路径规范生成、完整性校验及快照保留清理。
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from common import OutputPlan, plan_output  # noqa: E402

from client import (  # noqa: E402
    CloudreveClient,
    format_bytes,
    normalize_remote_path,
    resolve_local_path,
)


def compute_file_sha256(path: Path) -> str:
    """计算本地文件的 SHA256 哈希值。"""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def backup_file(
    client: CloudreveClient,
    local_path: str | Path,
    category: str = "default",
    tag: Optional[str] = None,
    output_arg: Optional[str] = None,
    overwrite: bool = False,
) -> Tuple[dict[str, Any], Path]:
    """执行规范化文件备份流水线，并落 Manifest 审计记录。

    远端存储路径规范：
      /AgentBackups/<host_id>/<category>/<YYYYMMDD>/<HHMMSS>_<sha256前12位>/<filename>
    """
    client.config.validate_credentials()
    loc = resolve_local_path(local_path)
    if not loc.is_file():
        raise FileNotFoundError(f"待备份的本地文件不存在: {loc}")

    sha256 = compute_file_sha256(loc)
    sha12 = sha256[:12]
    size_bytes = loc.stat().st_size

    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    snapshot_id = f"{time_str}_{sha12}"
    filename = loc.name

    clean_category = re.sub(r"[^\w\-.]", "_", category).strip("_") or "default"
    sandbox_root = client.config.root_sandbox.rstrip("/")
    snapshot_dir = f"{sandbox_root}/{client.config.host_id}/{clean_category}/{date_str}/{snapshot_id}"
    remote_file_path = f"{snapshot_dir}/{filename}"

    # 上传主文件
    client.put(loc, remote_file_path, overwrite=overwrite)

    # 构造 Manifest 数据结构
    manifest_data: dict[str, Any] = {
        "schema_version": "1.0",
        "backup_id": f"{date_str}_{snapshot_id}",
        "snapshot_id": snapshot_id,
        "created_at": now.isoformat(timespec="seconds"),
        "host_id": client.config.host_id,
        "category": clean_category,
        "tag": tag or "",
        "source_file": {
            "path": str(loc),
            "name": filename,
            "size_bytes": size_bytes,
            "size_human": format_bytes(size_bytes),
            "sha256": sha256,
        },
        "remote": {
            "path": remote_file_path,
            "snapshot_dir": snapshot_dir,
            "endpoint": client.endpoint,
        },
        "status": "completed",
    }

    # 规范化落盘：对齐 OUTPUT.md 统一产物树
    plan: OutputPlan = plan_output(
        "agentops", "cloudreve-storage", "backup-manifest.json", explicit=output_arg
    )
    manifest_json = json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n"
    plan.primary.parent.mkdir(parents=True, exist_ok=True)
    plan.primary.write_text(manifest_json, encoding="utf-8")
    committed_path = plan.commit()

    # 尝试将 manifest 也同步一份至远程快照目录中，保持快照自解释
    try:
        client.put(plan.primary, f"{snapshot_dir}/manifest.json", overwrite=True)
    except Exception:
        pass

    return manifest_data, committed_path


def restore_file(
    client: CloudreveClient,
    remote_path: str,
    output_arg: Optional[str] = None,
    overwrite: bool = False,
    allow_outside: bool = False,
) -> dict[str, Any]:
    """从远端快照或指定路径恢复文件，并进行 SHA256 完整性与防覆盖检查。"""
    client.config.validate_credentials()
    clean_remote = client.check_sandbox(remote_path, allow_outside=allow_outside)

    # 若传入的是快照目录，自动定位目录下的真实备份文件
    target_remote_file = clean_remote
    if clean_remote.endswith("/"):
        children = client.ls(clean_remote, depth=1, allow_outside=allow_outside)
        data_files = [
            c for c in children
            if not c["is_dir"] and not c["name"].startswith(".") and c["name"] != "manifest.json"
        ]
        if not data_files:
            raise FileNotFoundError(f"快照目录下未找到有效备份数据文件: {clean_remote}")
        target_remote_file = data_files[0]["path"]

    filename = posixpath.basename(target_remote_file)

    # 确定本地保存路径
    if output_arg:
        p = resolve_local_path(output_arg)
        dest_file = p / filename if p.is_dir() else p
    else:
        dest_file = Path.cwd() / filename

    if dest_file.exists() and not overwrite:
        raise FileExistsError(
            f"本地恢复目标文件已存在且未指定 overwrite: {dest_file}\n"
            "若需覆盖请增加 `--overwrite` 参数。"
        )

    # 下载文件
    downloaded_path = client.get(
        target_remote_file, dest_file, overwrite=overwrite, allow_outside=allow_outside
    )

    # 计算还原文件的实际哈希
    actual_sha256 = compute_file_sha256(downloaded_path)

    # 寻找期望哈希以校验完整性
    expected_sha256: Optional[str] = None
    expected_sha12: Optional[str] = None

    # 1. 尝试读取快照同级目录的 manifest.json
    parent_dir = posixpath.dirname(target_remote_file)
    try:
        m_content = client.cat(f"{parent_dir}/manifest.json", allow_outside=allow_outside)
        m_obj = json.loads(m_content)
        expected_sha256 = m_obj.get("source_file", {}).get("sha256")
    except Exception:
        pass

    # 2. 从快照路径规范提取 SHA256 前12位
    match = re.search(r"\d{6}_([a-f0-9]{12})", target_remote_file)
    if match:
        expected_sha12 = match.group(1)

    # 校验
    if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
        raise IOError(
            f"SHA256 完整性校验失败！期望: {expected_sha256}，实际计算: {actual_sha256}"
        )
    if expected_sha12 and not actual_sha256.lower().startswith(expected_sha12.lower()):
        raise IOError(
            f"SHA256 前缀校验失败！期望前缀: {expected_sha12}，实际计算: {actual_sha256}"
        )

    return {
        "restored_path": str(downloaded_path),
        "remote_path": target_remote_file,
        "size_bytes": downloaded_path.stat().st_size,
        "size_human": format_bytes(downloaded_path.stat().st_size),
        "sha256": actual_sha256,
        "verified": True,
    }


def prune_backups(
    client: CloudreveClient,
    category: Optional[str] = None,
    keep: int = 5,
    dry_run: bool = False,
    allow_outside: bool = False,
) -> dict[str, Any]:
    """清理过期备份快照，默认每分类保留最新的 keep 个快照。"""
    if keep < 1:
        raise ValueError(f"keep 必须至少为 1（收到 {keep}），否则会清空全部快照。")
    client.config.validate_credentials()
    host_dir = f"{client.config.root_sandbox.rstrip('/')}/{client.config.host_id}"

    # 获取要处理的分类目录
    categories: list[str] = []
    if category:
        categories = [category]
    else:
        try:
            sub = client.ls(host_dir, depth=1, allow_outside=allow_outside)
            categories = [c["name"] for c in sub if c["is_dir"]]
        except FileNotFoundError:
            categories = []

    pruned_snapshots: list[str] = []
    kept_snapshots: list[str] = []
    failed_prunes: list[dict[str, str]] = []

    for cat in categories:
        cat_dir = f"{host_dir}/{cat}"
        try:
            date_dirs = client.ls(cat_dir, depth=1, allow_outside=allow_outside)
        except FileNotFoundError:
            continue

        snapshots: list[dict[str, Any]] = []
        for d in sorted(date_dirs, key=lambda x: x["name"]):
            if not d["is_dir"]:
                continue
            date_path = d["path"]
            try:
                snap_dirs = client.ls(date_path, depth=1, allow_outside=allow_outside)
            except FileNotFoundError:
                continue

            for s in snap_dirs:
                if not s["is_dir"]:
                    continue
                # 快照命名通常为 HHMMSS_<sha12>
                key = f"{d['name']}_{s['name']}"
                snapshots.append({
                    "key": key,
                    "path": s["path"],
                    "date_path": date_path,
                    "category": cat,
                })

        # 按时间排序（升序：旧在前，新在后）
        snapshots.sort(key=lambda x: x["key"])

        if len(snapshots) > keep:
            to_prune = snapshots[: len(snapshots) - keep]
            to_keep = snapshots[len(snapshots) - keep :]
        else:
            to_prune = []
            to_keep = snapshots

        for item in to_keep:
            kept_snapshots.append(item["path"])

        for item in to_prune:
            if not dry_run:
                try:
                    client.rm(item["path"], recursive=True, allow_outside=allow_outside)
                except Exception as e:
                    # 删除失败必须如实上报，不得计入已清理数量
                    failed_prunes.append({"path": item["path"], "error": str(e)})
                    continue
                # 检查日期目录是否已空，若空则顺带清理
                try:
                    rem = client.ls(item["date_path"], depth=1, allow_outside=allow_outside)
                    if len(rem) == 0:
                        client.rm(item["date_path"], recursive=True, allow_outside=allow_outside)
                except Exception:
                    pass
            pruned_snapshots.append(item["path"])

    return {
        "host_id": client.config.host_id,
        "keep_per_category": keep,
        "dry_run": dry_run,
        "pruned_count": len(pruned_snapshots),
        "pruned_snapshots": pruned_snapshots,
        "kept_count": len(kept_snapshots),
        "kept_snapshots": kept_snapshots,
        "failed_count": len(failed_prunes),
        "failed_prunes": failed_prunes,
    }
