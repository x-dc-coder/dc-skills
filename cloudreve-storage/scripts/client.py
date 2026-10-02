"""Cloudreve WebDAV 与 REST 混合客户端封装。

提供连接测试、目录操作、文件读写、沙箱安全防御与原子上传/移动等核心能力。
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

import requests

from config import CloudreveConfig


def normalize_remote_path(remote_path: str) -> str:
    """规整化 WebDAV 远程路径（统一将 Windows 反斜杠转为正斜杠，并进行 posix 规整化）。"""
    s = str(remote_path).strip().replace("\\", "/")
    clean = posixpath.normpath(s)
    if not clean.startswith("/"):
        clean = "/" + clean
    return clean


def resolve_local_path(path_input: str | Path) -> Path:
    """解析本地文件系统路径，跨平台支持 Windows 盘符路径（在 WSL/Linux 下无缝转为 /mnt/<drive>/...）。"""
    s = str(path_input).strip("\"'")
    if not s:
        return Path(".").resolve()
    # 当在 Linux/WSL 环境下收到 Windows 盘符路径（如 C:\... 或 E:/...）时进行跨环境转换
    if os.name != "nt" and re.match(r"^[a-zA-Z]:[/\\]", s):
        # 优先使用 wslpath -u（适用于 WSL）
        try:
            res = subprocess.check_output(
                ["wslpath", "-u", s], text=True, stderr=subprocess.DEVNULL
            ).strip()
            if res:
                return Path(res).resolve()
        except Exception:
            pass
        # 降级备用：直接映射 /mnt/<drive>/...
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive}/{rest}").resolve()
    return Path(s).expanduser().resolve()


class CloudreveError(Exception):
    """Cloudreve 基础异常。"""


class SandboxViolationError(CloudreveError):
    """路径超出安全沙箱异常。"""


class DangerousOperationError(CloudreveError):
    """高危禁止操作异常（如删除根目录）。"""


class WebDAVError(CloudreveError):
    """WebDAV 协议操作错误。"""


def format_bytes(num_bytes: Optional[int]) -> str:
    """人类友好的字节大小格式化。"""
    if num_bytes is None or num_bytes < 0:
        return "未知"
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


class CloudreveClient:
    """Cloudreve WebDAV / 存储客户端。"""

    def __init__(self, config: CloudreveConfig) -> None:
        self.config = config
        self.endpoint = config.webdav_endpoint
        self.session = requests.Session()
        if self.config.username and self.config.password:
            self.session.auth = (self.config.username, self.config.password)
        self.session.headers.update({
            "User-Agent": "dc-skills/cloudreve-storage (1.0)",
        })

    def _clean_remote_path(self, href: str) -> str:
        """从 href 中解析并提取规范的远端绝对路径（剥离 /dav 前缀）。"""
        unquoted = urllib.parse.unquote(href)
        parsed = urllib.parse.urlparse(unquoted)
        path = parsed.path if parsed.path else unquoted

        # 去除可能存在的 webdav_path 根前缀（如 /dav）；必须按路径段边界匹配，
        # 避免把 /davbackup 这类同前缀目录误剥为 /backup
        dav_prefix = self.config.webdav_path.rstrip("/")
        if dav_prefix and (path == dav_prefix or path.startswith(dav_prefix + "/")):
            path = path[len(dav_prefix):]
        if not path.startswith("/"):
            path = "/" + path
        return path

    def build_url(self, remote_path: str) -> str:
        """构建 URL 编码的安全 WebDAV 请求地址。各路径段独立转义。"""
        clean = normalize_remote_path(remote_path)
        s = str(remote_path).strip().replace("\\", "/")
        ends_with_slash = s.endswith("/") and clean != "/"

        parts = [p for p in clean.split("/") if p]
        dav_prefix = self.config.webdav_path.strip("/")
        if parts and parts[0] == dav_prefix:
            parts = parts[1:]

        quoted_parts = [urllib.parse.quote(p, safe="") for p in parts]
        subpath = "/".join(quoted_parts)
        if ends_with_slash and subpath:
            subpath += "/"

        endpoint = self.endpoint.rstrip("/") + "/"
        return urllib.parse.urljoin(endpoint, subpath)

    def check_sandbox(self, remote_path: str, allow_outside: bool = False) -> str:
        """检查并规整化路径。若超出安全沙箱且未显式允许，抛出 SandboxViolationError。"""
        clean = normalize_remote_path(remote_path)
        sandbox = normalize_remote_path(self.config.root_sandbox)

        if not allow_outside:
            is_inside = clean == sandbox or clean.startswith(sandbox + "/")
            if not is_inside:
                raise SandboxViolationError(
                    f"安全防御：操作路径 '{clean}' 超出安全沙箱 '{self.config.root_sandbox}'。\n"
                    "若确需操作沙箱外目录，请在命令后附加 `--outside-sandbox` 参数。"
                )
        return clean

    def check_dangerous_path(self, remote_path: str) -> str:
        """严禁删除等危险操作命中根目录或沙箱根目录。"""
        clean = normalize_remote_path(remote_path)
        dav_root = normalize_remote_path(self.config.webdav_path)
        sandbox = normalize_remote_path(self.config.root_sandbox)

        dangerous = {"/", dav_root, sandbox}
        if clean in dangerous:
            raise DangerousOperationError(
                f"安全防御阻止：严禁对根目录或沙箱根目录 '{clean}' 执行破坏性操作（如删除）！"
            )
        return clean

    def status(self) -> dict[str, Any]:
        """探测 WebDAV 连接可用性并解析空间配额。"""
        self.config.validate_credentials()
        url = self.endpoint
        body = (
            '<?xml version="1.0" encoding="utf-8" ?>\n'
            '<D:propfind xmlns:D="DAV:">\n'
            "  <D:prop>\n"
            "    <D:quota-available-bytes/>\n"
            "    <D:quota-used-bytes/>\n"
            "    <D:resourcetype/>\n"
            "  </D:prop>\n"
            "</D:propfind>"
        )
        headers = {
            "Depth": "0",
            "Content-Type": "application/xml; charset=utf-8",
        }
        try:
            resp = self.session.request("PROPFIND", url, data=body, headers=headers, timeout=15)
        except requests.RequestException as e:
            raise CloudreveError(f"连接 Cloudreve 失败 ({url}): {e}") from e

        if resp.status_code == 401:
            raise PermissionError(
                "认证失败 (HTTP 401 Unauthorized)：请检查 WebDAV 用户名和应用专用密码是否正确。"
            )
        if resp.status_code != 207:
            raise WebDAVError(f"PROPFIND 响应异常: HTTP {resp.status_code}\n{resp.text[:200]}")

        avail_bytes: Optional[int] = None
        used_bytes: Optional[int] = None

        try:
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                if tag == "quota-available-bytes" and elem.text and elem.text.isdigit():
                    avail_bytes = int(elem.text)
                elif tag == "quota-used-bytes" and elem.text and elem.text.isdigit():
                    used_bytes = int(elem.text)
        except ET.ParseError as e:
            raise WebDAVError(f"解析 WebDAV 状态响应 XML 失败: {e}") from e

        total_bytes: Optional[int] = None
        if avail_bytes is not None and used_bytes is not None:
            total_bytes = avail_bytes + used_bytes

        return {
            "status": "connected",
            "endpoint": self.endpoint,
            "username": self.config.username,
            "host_id": self.config.host_id,
            "root_sandbox": self.config.root_sandbox,
            "quota_available_bytes": avail_bytes,
            "quota_used_bytes": used_bytes,
            "quota_total_bytes": total_bytes,
            "available_human": format_bytes(avail_bytes),
            "used_human": format_bytes(used_bytes),
            "total_human": format_bytes(total_bytes),
        }

    def mkdir_p(self, remote_path: str, allow_outside: bool = False) -> None:
        """逐级建立目录（处理 201/405 为成功，处理 409 Conflict）。"""
        self.config.validate_credentials()
        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)
        parts = [p for p in clean.split("/") if p]

        current = ""
        for part in parts:
            current = f"{current}/{part}"
            url = self.build_url(current + "/")
            try:
                resp = self.session.request("MKCOL", url, timeout=20)
            except requests.RequestException as e:
                raise WebDAVError(f"MKCOL 请求失败 ({current}): {e}") from e

            # 201 Created: 新建成功；405 Method Not Allowed: 目录已存在
            if resp.status_code in (201, 405):
                continue
            if resp.status_code == 409:
                # 冲突：通常是父目录在并发或特殊状态下未完成，尝试重试下一级
                continue
            raise WebDAVError(f"MKCOL 建立目录 '{current}' 失败: HTTP {resp.status_code} {resp.text[:100]}")

    def ls(self, remote_path: str, depth: int = 1, allow_outside: bool = False) -> list[dict[str, Any]]:
        """列出远程目录或文件详情。depth=1 返回直接子项。"""
        self.config.validate_credentials()
        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)
        url = self.build_url(clean)

        headers = {"Depth": str(depth)}
        try:
            resp = self.session.request("PROPFIND", url, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise WebDAVError(f"PROPFIND 请求失败 ({clean}): {e}") from e

        if resp.status_code == 404:
            raise FileNotFoundError(f"远端路径不存在: {clean}")
        if resp.status_code != 207:
            raise WebDAVError(f"列出目录失败: HTTP {resp.status_code} {resp.text[:100]}")

        items: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise WebDAVError(f"解析 PROPFIND 响应 XML 失败: {e}") from e

        target_norm = clean.rstrip("/")

        for resp_elem in root.iter():
            if not resp_elem.tag.endswith("response"):
                continue

            href_elem = None
            prop_elem = None
            for child in resp_elem:
                tag = child.tag.split("}")[-1]
                if tag == "href":
                    href_elem = child
                elif tag == "propstat":
                    for sub in child:
                        if sub.tag.split("}")[-1] == "prop":
                            prop_elem = sub
                            break

            if href_elem is None or not href_elem.text:
                continue

            item_path = self._clean_remote_path(href_elem.text)
            item_norm = item_path.rstrip("/")

            is_dir = False
            size = 0
            mtime = ""
            etag = ""
            display_name = ""

            if prop_elem is not None:
                for p in prop_elem:
                    ptag = p.tag.split("}")[-1]
                    if ptag == "resourcetype":
                        for r in p:
                            if r.tag.split("}")[-1] == "collection":
                                is_dir = True
                    elif ptag == "getcontentlength" and p.text and p.text.isdigit():
                        size = int(p.text)
                    elif ptag == "getlastmodified" and p.text:
                        mtime = p.text
                    elif ptag == "getetag" and p.text:
                        etag = p.text.strip('"\'')
                    elif ptag == "displayname" and p.text:
                        display_name = p.text

            if item_path.endswith("/"):
                is_dir = True

            name = display_name or posixpath.basename(item_norm)
            if not name:
                name = "/" if item_norm == "" else item_norm

            items.append({
                "name": name,
                "path": item_path if not is_dir else item_norm + "/",
                "is_dir": is_dir,
                "size": size,
                "size_human": format_bytes(size) if not is_dir else "-",
                "mtime": mtime,
                "etag": etag,
            })

        # 若请求的是目录，并且 depth=1，过滤掉根目录本身
        if depth >= 1 and len(items) > 1:
            filtered = [
                it for it in items
                if it["path"].rstrip("/") != target_norm
            ]
            return filtered

        return items

    def exists(self, remote_path: str, allow_outside: bool = False) -> bool:
        """检查远程文件或目录是否存在。"""
        try:
            self.ls(remote_path, depth=0, allow_outside=allow_outside)
            return True
        except FileNotFoundError:
            return False

    def cat(
        self,
        remote_path: str,
        head_bytes: Optional[int] = None,
        head_lines: Optional[int] = None,
        allow_outside: bool = False,
    ) -> str:
        """读取远端文本文件内容（流式 GET，自动跟随 302 重定向）。"""
        self.config.validate_credentials()
        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)
        url = self.build_url(clean)

        try:
            resp = self.session.get(url, stream=True, allow_redirects=True, timeout=30)
        except requests.RequestException as e:
            raise WebDAVError(f"GET 请求失败 ({clean}): {e}") from e

        with resp:
            if resp.status_code == 404:
                raise FileNotFoundError(f"远端文件不存在: {clean}")
            if resp.status_code != 200:
                raise WebDAVError(f"读取文件失败: HTTP {resp.status_code} {resp.text[:100]}")

            if head_bytes is not None and head_bytes > 0:
                raw = resp.raw.read(head_bytes)
                return raw.decode("utf-8", errors="replace")

            if head_lines is not None and head_lines > 0:
                lines: list[str] = []
                for line in resp.iter_lines():
                    if line is not None:
                        lines.append(line.decode("utf-8", errors="replace"))
                        if len(lines) >= head_lines:
                            break
                return "\n".join(lines)

            return resp.content.decode("utf-8", errors="replace")

    def put(
        self,
        local_path: str | Path,
        remote_path: str,
        overwrite: bool = False,
        allow_outside: bool = False,
    ) -> dict[str, Any]:
        """上传本地文件。原子性处理：先上传为 .part，成功后通过 MOVE 原子重命名。"""
        self.config.validate_credentials()
        loc = resolve_local_path(local_path)
        if not loc.is_file():
            raise FileNotFoundError(f"本地文件不存在: {loc}")

        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)

        # 检查远端目标是否已存在
        if not overwrite and self.exists(clean, allow_outside=allow_outside):
            raise FileExistsError(
                f"远端目标已存在且未指定 overwrite: {clean}\n"
                "若需覆盖请增加 `--overwrite` 参数。"
            )

        # 保证父级目录存在
        parent_dir = posixpath.dirname(clean)
        if parent_dir and parent_dir != "/":
            self.mkdir_p(parent_dir, allow_outside=allow_outside)

        filename = posixpath.basename(clean)
        part_name = f".{filename}.{uuid.uuid4().hex[:8]}.part"
        part_path = posixpath.join(parent_dir, part_name)

        part_url = self.build_url(part_path)
        dest_url = self.build_url(clean)

        file_size = loc.stat().st_size

        try:
            with open(loc, "rb") as f:
                resp = self.session.put(part_url, data=f, allow_redirects=True, timeout=60)
        except requests.RequestException as e:
            raise WebDAVError(f"PUT 上传失败 ({part_path}): {e}") from e

        if resp.status_code not in (200, 201, 204):
            raise WebDAVError(f"PUT 上传失败: HTTP {resp.status_code} {resp.text[:100]}")

        # 原子 MOVE 到最终文件名
        mv_headers = {
            "Destination": dest_url,
            "Overwrite": "T" if overwrite else "F",
        }
        try:
            mv_resp = self.session.request("MOVE", part_url, headers=mv_headers, timeout=20)
        except requests.RequestException as e:
            # 清理临时 part 文件
            try:
                self.session.delete(part_url)
            except Exception:
                pass
            raise WebDAVError(f"原子重命名请求失败: {e}") from e

        if mv_resp.status_code not in (200, 201, 204):
            try:
                self.session.delete(part_url)
            except Exception:
                pass
            raise WebDAVError(
                f"原子重命名至 '{clean}' 失败: HTTP {mv_resp.status_code} {mv_resp.text[:100]}"
            )

        return {
            "local_path": str(loc),
            "remote_path": clean,
            "size": file_size,
            "size_human": format_bytes(file_size),
        }

    def get(
        self,
        remote_path: str,
        local_path: str | Path,
        overwrite: bool = False,
        allow_outside: bool = False,
    ) -> Path:
        """流式下载文件（自动跟随 302 重定向），校验大小并原子落地。"""
        self.config.validate_credentials()
        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)

        dest = resolve_local_path(local_path)
        if dest.is_dir():
            dest = dest / posixpath.basename(clean)

        if dest.exists() and not overwrite:
            raise FileExistsError(
                f"本地目标已存在且未指定 overwrite: {dest}\n"
                "若需覆盖请增加 `--overwrite` 参数。"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        part_file = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")

        url = self.build_url(clean)
        try:
            resp = self.session.get(url, stream=True, allow_redirects=True, timeout=60)
        except requests.RequestException as e:
            raise WebDAVError(f"GET 下载失败 ({clean}): {e}") from e

        with resp:
            if resp.status_code == 404:
                raise FileNotFoundError(f"远端文件不存在: {clean}")
            if resp.status_code not in (200, 206):
                raise WebDAVError(f"下载失败: HTTP {resp.status_code} {resp.text[:100]}")

            try:
                with open(part_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)

                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > 0:
                    actual = part_file.stat().st_size
                    if actual != int(cl):
                        raise IOError(f"下载文件大小不一致：期望 {cl} 字节，实际得到 {actual} 字节")

                part_file.replace(dest)
            except Exception:
                if part_file.exists():
                    part_file.unlink(missing_ok=True)
                raise

        return dest

    def mv(
        self,
        src_path: str,
        dst_path: str,
        overwrite: bool = False,
        allow_outside: bool = False,
    ) -> None:
        """移动或重命名文件/目录（WebDAV MOVE 命令）。"""
        self.config.validate_credentials()
        src_clean = self.check_sandbox(src_path, allow_outside=allow_outside)
        dst_clean = self.check_sandbox(dst_path, allow_outside=allow_outside)

        src_url = self.build_url(src_clean)
        dst_url = self.build_url(dst_clean)

        # 确保目标父级目录存在
        dst_parent = posixpath.dirname(dst_clean)
        if dst_parent and dst_parent != "/":
            self.mkdir_p(dst_parent, allow_outside=allow_outside)

        headers = {
            "Destination": dst_url,
            "Overwrite": "T" if overwrite else "F",
        }
        try:
            resp = self.session.request("MOVE", src_url, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise WebDAVError(f"MOVE 请求失败: {e}") from e

        if resp.status_code == 404:
            raise FileNotFoundError(f"源路径不存在: {src_clean}")
        if resp.status_code == 412:
            raise FileExistsError(f"目标已存在且未开启覆盖 (HTTP 412 Precondition Failed): {dst_clean}")
        if resp.status_code not in (200, 201, 204):
            raise WebDAVError(f"MOVE 操作失败: HTTP {resp.status_code} {resp.text[:100]}")

    def rm(
        self,
        remote_path: str,
        recursive: bool = False,
        allow_outside: bool = False,
    ) -> None:
        """删除文件或目录（WebDAV DELETE 命令）。严禁危险路径删除。"""
        self.config.validate_credentials()
        clean = self.check_sandbox(remote_path, allow_outside=allow_outside)
        self.check_dangerous_path(clean)

        # 如果是非递归删除，检查是否为非空目录
        if not recursive:
            try:
                sub_items = self.ls(clean, depth=1, allow_outside=allow_outside)
                if len(sub_items) > 0:
                    raise RuntimeError(
                        f"目标 '{clean}' 是非空目录（含 {len(sub_items)} 个直接子项）。\n"
                        "若确认整体删除，请添加 `--recursive` 参数。"
                    )
            except FileNotFoundError:
                raise
            except RuntimeError:
                # 非空目录：原样抛出，由用户决定是否 --recursive
                raise
            # ls 失败（如网络抖动、XML 解析异常）时必须中止删除：
            # 否则 DELETE 会被服务端按递归语义执行，造成非预期整目录删除
            except Exception as e:
                raise WebDAVError(
                    f"无法确认 '{clean}' 是否为空目录（{e}），已中止删除以避免误删；"
                    "如确认需要递归删除，请显式添加 `--recursive`。"
                ) from e

        url = self.build_url(clean)
        try:
            resp = self.session.delete(url, timeout=20)
        except requests.RequestException as e:
            raise WebDAVError(f"DELETE 请求失败 ({clean}): {e}") from e

        if resp.status_code == 404:
            raise FileNotFoundError(f"远端目标不存在: {clean}")
        if resp.status_code not in (200, 204):
            raise WebDAVError(f"DELETE 操作失败: HTTP {resp.status_code} {resp.text[:100]}")

    def tree(
        self,
        remote_path: str,
        max_depth: int = 3,
        current_depth: int = 1,
        allow_outside: bool = False,
    ) -> list[dict[str, Any]]:
        """递归遍历远端目录树结构。"""
        if current_depth > max_depth:
            return []

        try:
            children = self.ls(remote_path, depth=1, allow_outside=allow_outside)
        except FileNotFoundError:
            return []

        tree_nodes: list[dict[str, Any]] = []
        for child in sorted(children, key=lambda x: (not x["is_dir"], x["name"])):
            node = dict(child)
            node["depth"] = current_depth
            if child["is_dir"] and current_depth < max_depth:
                node["children"] = self.tree(
                    child["path"],
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    allow_outside=allow_outside,
                )
            else:
                node["children"] = []
            tree_nodes.append(node)

        return tree_nodes
