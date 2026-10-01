"""Cloudreve 云存储配置与凭据解析模块。

优先级：CLI 参数 > 环境变量 > 配置文件 (~/.config/cloudreve-storage/config.toml) > 内置默认值。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import tomli as tomllib

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "cloudreve-storage"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"

DEFAULT_BASE_URL = "https://cloudreve.dc-sy.cn"
DEFAULT_WEBDAV_PATH = "/dav/"
DEFAULT_ROOT_SANDBOX = "/AgentBackups/"


def get_default_host_id() -> str:
    """获取本机简短主机名（去掉域名后缀）。"""
    hostname = socket.gethostname()
    return hostname.split(".")[0] if hostname else "localhost"


@dataclass
class CloudreveConfig:
    """Cloudreve 客户端配置数据类。"""

    base_url: str = DEFAULT_BASE_URL
    webdav_path: str = DEFAULT_WEBDAV_PATH
    username: str = ""
    password: str = ""
    root_sandbox: str = DEFAULT_ROOT_SANDBOX
    host_id: str = ""

    def __post_init__(self) -> None:
        if not self.host_id:
            self.host_id = get_default_host_id()
        # 规整化 URL 与路径
        self.base_url = self.base_url.rstrip("/")
        if not self.webdav_path.startswith("/"):
            self.webdav_path = "/" + self.webdav_path
        if not self.webdav_path.endswith("/"):
            self.webdav_path = self.webdav_path + "/"
        if not self.root_sandbox.startswith("/"):
            self.root_sandbox = "/" + self.root_sandbox
        if not self.root_sandbox.endswith("/"):
            self.root_sandbox = self.root_sandbox + "/"

    @property
    def webdav_endpoint(self) -> str:
        """完整 WebDAV 根接口地址，如 https://cloudreve.dc-sy.cn/dav/。"""
        return f"{self.base_url}{self.webdav_path}"

    def validate_credentials(self) -> None:
        """检查必要认证凭据是否存在，缺失时抛出友好的错误提示。"""
        if not self.username or not self.password:
            raise ValueError(
                "未配置 Cloudreve WebDAV 认证凭据（username 或 password 为空）。\n"
                "请执行 `init-config` 命令初始化配置文件：\n"
                "  cd ~/projects/dc-skills && uv run python cloudreve-storage/scripts/cli.py init-config\n"
                f"并在 {DEFAULT_CONFIG_PATH} 中填入应用专用密码；\n"
                "或通过环境变量 CLOUDREVE_USERNAME / CLOUDREVE_PASSWORD，或 CLI 参数 --username / --password 传入。"
            )

    @classmethod
    def resolve(
        cls,
        config_path: Optional[str | Path] = None,
        base_url: Optional[str] = None,
        webdav_path: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        root_sandbox: Optional[str] = None,
        host_id: Optional[str] = None,
    ) -> CloudreveConfig:
        """多级凭据解析：CLI 参数 > 环境变量 > 配置文件 > 默认值。"""
        file_cfg: dict[str, Any] = {}

        # 确定配置文件路径
        target_file: Optional[Path] = None
        if config_path:
            target_file = Path(config_path).expanduser().resolve()
            if not target_file.is_file():
                raise FileNotFoundError(f"指定的配置文件不存在: {target_file}")
        elif os.environ.get("CLOUDREVE_CONFIG"):
            target_file = Path(os.environ["CLOUDREVE_CONFIG"]).expanduser().resolve()
            if not target_file.is_file():
                raise FileNotFoundError(f"环境变量 CLOUDREVE_CONFIG 指定的文件不存在: {target_file}")
        elif DEFAULT_CONFIG_PATH.is_file():
            target_file = DEFAULT_CONFIG_PATH

        if target_file and target_file.is_file():
            with open(target_file, "rb") as f:
                file_cfg = tomllib.load(f)

        resolved_base_url = (
            base_url
            or os.environ.get("CLOUDREVE_BASE_URL")
            or file_cfg.get("base_url")
            or DEFAULT_BASE_URL
        )
        resolved_webdav_path = (
            webdav_path
            or os.environ.get("CLOUDREVE_WEBDAV_PATH")
            or file_cfg.get("webdav_path")
            or DEFAULT_WEBDAV_PATH
        )
        resolved_username = (
            username
            or os.environ.get("CLOUDREVE_USERNAME")
            or file_cfg.get("username")
            or ""
        )
        resolved_password = (
            password
            or os.environ.get("CLOUDREVE_PASSWORD")
            or file_cfg.get("password")
            or ""
        )
        resolved_root_sandbox = (
            root_sandbox
            or os.environ.get("CLOUDREVE_ROOT_SANDBOX")
            or file_cfg.get("root_sandbox")
            or DEFAULT_ROOT_SANDBOX
        )
        resolved_host_id = (
            host_id
            or os.environ.get("CLOUDREVE_HOST_ID")
            or file_cfg.get("host_id")
            or get_default_host_id()
        )

        return cls(
            base_url=resolved_base_url,
            webdav_path=resolved_webdav_path,
            username=resolved_username,
            password=resolved_password,
            root_sandbox=resolved_root_sandbox,
            host_id=resolved_host_id,
        )


def init_config_template(
    target_path: Optional[str | Path] = None, overwrite: bool = False
) -> Path:
    """生成本地示例配置文件，并设置文件权限为 0600。"""
    path = Path(target_path).expanduser().resolve() if target_path else DEFAULT_CONFIG_PATH
    if path.exists() and not overwrite:
        raise FileExistsError(f"配置文件已存在: {path}（若需重新生成，请加 --overwrite）")

    path.parent.mkdir(parents=True, exist_ok=True)

    template = (
        "# Cloudreve 云存储网盘与 WebDAV 配置文件\n"
        "# 权限建议保持 0600 (chmod 600)\n\n"
        'base_url = "https://cloudreve.dc-sy.cn"\n'
        'webdav_path = "/dav/"\n'
        'username = "your_username@example.com"\n'
        'password = "your_app_specific_password_here"\n'
        'root_sandbox = "/AgentBackups/"\n\n'
        "# 可选：当前设备标识，默认留空自动读取短主机名\n"
        f'# host_id = "{get_default_host_id()}"\n'
    )

    path.write_text(template, encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass

    return path
