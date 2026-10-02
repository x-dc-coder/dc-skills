"""Tests for cloudreve-storage CLI, config, client, and manifest pipelines."""

from __future__ import annotations

import io
import json
import os
import posixpath
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 保证能够正确定位并导入技能模块与主库脚本
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
MASTER_SCRIPTS_DIR = SCRIPTS_DIR.parent.parent / "scripts"
if str(MASTER_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(MASTER_SCRIPTS_DIR))

from client import (  # noqa: E402
    CloudreveClient,
    DangerousOperationError,
    SandboxViolationError,
    WebDAVError,
    format_bytes,
)
from config import CloudreveConfig, init_config_template  # noqa: E402
from manifest import backup_file, compute_file_sha256, prune_backups, restore_file  # noqa: E402

CLI = SCRIPTS_DIR / "cli.py"


def test_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    assert "Cloudreve" in r.stdout.decode()


@pytest.mark.parametrize(
    "subcmd",
    [
        "status",
        "doctor",
        "ls",
        "tree",
        "cat",
        "put",
        "get",
        "mv",
        "rm",
        "backup",
        "restore",
        "prune",
        "init-config",
    ],
)
def test_subcommand_help_exits_zero(subcmd: str) -> None:
    r = subprocess.run([sys.executable, str(CLI), subcmd, "--help"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_format_bytes() -> None:
    assert format_bytes(None) == "未知"
    assert format_bytes(-1) == "未知"
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 * 1024) == "1.00 MB"
    assert format_bytes(1024 * 1024 * 1024 * 2) == "2.00 GB"


def test_config_defaults_and_validation() -> None:
    cfg = CloudreveConfig()
    assert cfg.base_url == "https://cloudreve.dc-sy.cn"
    assert cfg.webdav_path == "/dav/"
    assert cfg.webdav_endpoint == "https://cloudreve.dc-sy.cn/dav/"
    assert cfg.root_sandbox == "/AgentBackups/"
    assert len(cfg.host_id) > 0

    # 凭据为空时校验应当抛出 ValueError
    with pytest.raises(ValueError, match="未配置 Cloudreve WebDAV 认证凭据"):
        cfg.validate_credentials()

    # 配置凭据后校验应通过
    cfg.username = "test@example.com"
    cfg.password = "secret"
    cfg.validate_credentials()


def test_config_resolution_priority() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = Path(tmpdir) / "config.toml"
        cfg_path.write_text(
            'base_url = "https://file-cfg.com"\n'
            'username = "user_file"\n'
            'password = "pass_file"\n'
            'root_sandbox = "/SandboxFile/"\n',
            encoding="utf-8",
        )

        # 1. 从文件解析
        c1 = CloudreveConfig.resolve(config_path=cfg_path)
        assert c1.base_url == "https://file-cfg.com"
        assert c1.username == "user_file"
        assert c1.password == "pass_file"
        assert c1.root_sandbox == "/SandboxFile/"

        # 2. 环境变量覆盖文件
        with patch.dict(
            os.environ,
            {
                "CLOUDREVE_USERNAME": "env_user",
                "CLOUDREVE_PASSWORD": "env_pass",
            },
        ):
            c2 = CloudreveConfig.resolve(config_path=cfg_path)
            assert c2.username == "env_user"
            assert c2.password == "env_pass"

        # 3. CLI 参数覆盖环境变量与文件
        with patch.dict(os.environ, {"CLOUDREVE_USERNAME": "env_user"}):
            c3 = CloudreveConfig.resolve(
                config_path=cfg_path,
                username="cli_user",
                base_url="https://cli.com",
            )
            assert c3.username == "cli_user"
            assert c3.base_url == "https://cli.com"


def test_init_config_template() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "cfg" / "config.toml"
        p = init_config_template(target_path=target)
        assert p.is_file()
        content = p.read_text(encoding="utf-8")
        assert "base_url =" in content
        assert "webdav_path =" in content
        assert "your_username@example.com" in content

        # 检查权限为 0600
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600

        # 重复创建无 overwrite 时报错
        with pytest.raises(FileExistsError):
            init_config_template(target_path=target, overwrite=False)

        # 开启 overwrite 成功覆盖
        p2 = init_config_template(target_path=target, overwrite=True)
        assert p2 == p


def test_client_url_building() -> None:
    cfg = CloudreveConfig(
        base_url="https://cloudreve.dc-sy.cn",
        webdav_path="/dav/",
        username="u",
        password="p",
    )
    client = CloudreveClient(cfg)

    # 基础路径
    assert client.build_url("/AgentBackups/file.txt") == "https://cloudreve.dc-sy.cn/dav/AgentBackups/file.txt"
    # 目录保留结尾斜杠
    assert client.build_url("/AgentBackups/dir/") == "https://cloudreve.dc-sy.cn/dav/AgentBackups/dir/"
    # 包含特殊字符和空格转义
    assert (
        client.build_url("/AgentBackups/my dir/file#1.txt")
        == "https://cloudreve.dc-sy.cn/dav/AgentBackups/my%20dir/file%231.txt"
    )
    # 重复包含 /dav 前缀时能正确剔除
    assert client.build_url("/dav/AgentBackups/file.txt") == "https://cloudreve.dc-sy.cn/dav/AgentBackups/file.txt"


def test_sandbox_and_dangerous_checks() -> None:
    cfg = CloudreveConfig(
        root_sandbox="/AgentBackups/",
        username="u",
        password="p",
    )
    client = CloudreveClient(cfg)

    # 沙箱内正常通过
    assert client.check_sandbox("/AgentBackups/sub/1.txt") == "/AgentBackups/sub/1.txt"
    assert client.check_sandbox("/AgentBackups/") == "/AgentBackups"

    # 超出沙箱默认拦截
    with pytest.raises(SandboxViolationError):
        client.check_sandbox("/MyPhotos/image.jpg")

    with pytest.raises(SandboxViolationError):
        client.check_sandbox("/etc/passwd")

    # 显式允许沙箱外
    assert client.check_sandbox("/MyPhotos/image.jpg", allow_outside=True) == "/MyPhotos/image.jpg"

    # 危险操作（如删除）拦截根目录与沙箱根目录
    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path("/")

    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path("/dav")

    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path("/AgentBackups")

    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path("/AgentBackups/")


def test_status_mock() -> None:
    cfg = CloudreveConfig(username="test_user", password="secret_password")
    client = CloudreveClient(cfg)

    xml_response = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<D:multistatus xmlns:D="DAV:">\n'
        "  <D:response>\n"
        "    <D:href>/dav/</D:href>\n"
        "    <D:propstat>\n"
        "      <D:prop>\n"
        "        <D:quota-available-bytes>10737418240</D:quota-available-bytes>\n"
        "        <D:quota-used-bytes>104857600</D:quota-used-bytes>\n"
        "      </D:prop>\n"
        "      <D:status>HTTP/1.1 200 OK</D:status>\n"
        "    </D:propstat>\n"
        "  </D:response>\n"
        "</D:multistatus>"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 207
    mock_resp.content = xml_response.encode("utf-8")

    with patch.object(client.session, "request", return_value=mock_resp):
        info = client.status()
        assert info["status"] == "connected"
        assert info["username"] == "test_user"
        assert info["quota_available_bytes"] == 10737418240
        assert info["quota_used_bytes"] == 104857600
        assert info["quota_total_bytes"] == 10737418240 + 104857600
        assert "10.00 GB" in info["available_human"]
        assert "100.00 MB" in info["used_human"]


def test_ls_mock() -> None:
    cfg = CloudreveConfig(username="test_user", password="secret_password")
    client = CloudreveClient(cfg)

    xml_response = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<D:multistatus xmlns:D="DAV:">\n'
        "  <D:response>\n"
        "    <D:href>/dav/AgentBackups/</D:href>\n"
        "    <D:propstat>\n"
        "      <D:prop>\n"
        "        <D:resourcetype><D:collection/></D:resourcetype>\n"
        "      </D:prop>\n"
        "      <D:status>HTTP/1.1 200 OK</D:status>\n"
        "    </D:propstat>\n"
        "  </D:response>\n"
        "  <D:response>\n"
        "    <D:href>/dav/AgentBackups/docs/</D:href>\n"
        "    <D:propstat>\n"
        "      <D:prop>\n"
        "        <D:resourcetype><D:collection/></D:resourcetype>\n"
        "        <D:displayname>docs</D:displayname>\n"
        "      </D:prop>\n"
        "      <D:status>HTTP/1.1 200 OK</D:status>\n"
        "    </D:propstat>\n"
        "  </D:response>\n"
        "  <D:response>\n"
        "    <D:href>/dav/AgentBackups/backup.tar.gz</D:href>\n"
        "    <D:propstat>\n"
        "      <D:prop>\n"
        "        <D:getcontentlength>204800</D:getcontentlength>\n"
        "        <D:getlastmodified>Thu, 02 Oct 2026 01:00:00 GMT</D:getlastmodified>\n"
        "        <D:getetag>\"abc123etag\"</D:getetag>\n"
        "      </D:prop>\n"
        "      <D:status>HTTP/1.1 200 OK</D:status>\n"
        "    </D:propstat>\n"
        "  </D:response>\n"
        "</D:multistatus>"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 207
    mock_resp.content = xml_response.encode("utf-8")

    with patch.object(client.session, "request", return_value=mock_resp):
        items = client.ls("/AgentBackups/", depth=1)
        # 自身 /AgentBackups/ 被过滤，应剩 2 个子项
        assert len(items) == 2
        dir_item = next(it for it in items if it["is_dir"])
        assert dir_item["name"] == "docs"
        assert dir_item["path"] == "/AgentBackups/docs/"

        file_item = next(it for it in items if not it["is_dir"])
        assert file_item["name"] == "backup.tar.gz"
        assert file_item["size"] == 204800
        assert file_item["etag"] == "abc123etag"


def test_backup_and_restore_pipeline() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "data.txt"
        test_content = "Hello Cloudreve Backup Pipeline 2026!"
        src_file.write_text(test_content, encoding="utf-8")
        src_sha256 = compute_file_sha256(src_file)

        cfg = CloudreveConfig(
            username="test_user",
            password="secret_password",
            host_id="testhost",
            root_sandbox="/AgentBackups/",
        )
        client = CloudreveClient(cfg)

        # 模拟上传成功
        uploaded_remote_files: dict[str, bytes] = {}

        def fake_put(local_path, remote_path, overwrite=False, allow_outside=False):
            with open(local_path, "rb") as f:
                uploaded_remote_files[remote_path] = f.read()
            return {"local_path": str(local_path), "remote_path": remote_path, "size": len(uploaded_remote_files[remote_path])}

        def fake_get(remote_path, local_path, overwrite=False, allow_outside=False):
            dest = Path(local_path)
            if dest.is_dir():
                dest = dest / posixpath.basename(remote_path)
            content = uploaded_remote_files.get(remote_path, b"")
            dest.write_bytes(content)
            return dest

        def fake_cat(remote_path, allow_outside=False, **kwargs):
            return uploaded_remote_files.get(remote_path, b"").decode("utf-8")

        with patch.object(client, "put", side_effect=fake_put), \
             patch.object(client, "get", side_effect=fake_get), \
             patch.object(client, "cat", side_effect=fake_cat):

            # 1. 执行备份
            manifest_out = Path(tmpdir) / "output" / "backup-manifest.json"
            manifest_data, committed_manifest = backup_file(
                client=client,
                local_path=src_file,
                category="testdb",
                tag="v1.0",
                output_arg=str(manifest_out),
            )

            assert manifest_data["status"] == "completed"
            assert manifest_data["host_id"] == "testhost"
            assert manifest_data["category"] == "testdb"
            assert manifest_data["source_file"]["sha256"] == src_sha256
            assert committed_manifest.is_file()

            remote_path = manifest_data["remote"]["path"]
            assert "/AgentBackups/testhost/testdb/" in remote_path
            assert src_file.name in remote_path
            assert remote_path in uploaded_remote_files

            # 2. 执行恢复
            restored_target = Path(tmpdir) / "restored.txt"
            res = restore_file(
                client=client,
                remote_path=remote_path,
                output_arg=str(restored_target),
            )

            assert res["verified"] is True
            assert res["sha256"] == src_sha256
            assert restored_target.is_file()
            assert restored_target.read_text(encoding="utf-8") == test_content

            # 3. 校验防覆盖机制
            with pytest.raises(FileExistsError):
                restore_file(
                    client=client,
                    remote_path=remote_path,
                    output_arg=str(restored_target),
                    overwrite=False,
                )


def test_prune_backups_logic() -> None:
    cfg = CloudreveConfig(
        username="u",
        password="p",
        host_id="myhost",
        root_sandbox="/AgentBackups/",
    )
    client = CloudreveClient(cfg)

    # 构造 4 个快照
    fake_items = {
        "/AgentBackups/myhost": [
            {"name": "db", "path": "/AgentBackups/myhost/db/", "is_dir": True}
        ],
        "/AgentBackups/myhost/db": [
            {"name": "20261001", "path": "/AgentBackups/myhost/db/20261001/", "is_dir": True},
            {"name": "20261002", "path": "/AgentBackups/myhost/db/20261002/", "is_dir": True},
        ],
        "/AgentBackups/myhost/db/20261001": [
            {"name": "100000_111111111111", "path": "/AgentBackups/myhost/db/20261001/100000_111111111111/", "is_dir": True},
            {"name": "120000_222222222222", "path": "/AgentBackups/myhost/db/20261001/120000_222222222222/", "is_dir": True},
        ],
        "/AgentBackups/myhost/db/20261002": [
            {"name": "100000_333333333333", "path": "/AgentBackups/myhost/db/20261002/100000_333333333333/", "is_dir": True},
            {"name": "120000_444444444444", "path": "/AgentBackups/myhost/db/20261002/120000_444444444444/", "is_dir": True},
        ],
    }

    def fake_ls(path, depth=1, allow_outside=False):
        norm = path.rstrip("/")
        if norm in fake_items:
            return fake_items[norm]
        return []

    deleted_paths: list[str] = []

    def fake_rm(path, recursive=False, allow_outside=False):
        deleted_paths.append(path)

    with patch.object(client, "ls", side_effect=fake_ls), \
         patch.object(client, "rm", side_effect=fake_rm):

        # keep=2，总共 4 个，应删除最旧的 2 个（20261001 下的两个）
        result = prune_backups(client=client, category="db", keep=2, dry_run=False)
        assert result["pruned_count"] == 2
        assert result["kept_count"] == 2
        assert len(deleted_paths) >= 2
        assert "/AgentBackups/myhost/db/20261001/100000_111111111111/" in result["pruned_snapshots"]
        assert "/AgentBackups/myhost/db/20261001/120000_222222222222/" in result["pruned_snapshots"]


# ---------------------------------------------------------------------------
# 补充边缘场景测试（路径遍历 / URL 转义 / 0 字节 / 截断 / 故障注入 / dry-run）
# ---------------------------------------------------------------------------

def _make_client(password: str = "s3cretLEAK") -> CloudreveClient:
    cfg = CloudreveConfig(username="tester", password=password, host_id="testhost")
    return CloudreveClient(cfg)


@pytest.mark.parametrize(
    "evil_path",
    [
        "/AgentBackups/../../etc/passwd",
        "/AgentBackups/sub/../../etc/shadow",
        "/AgentBackups/../AgentBackups2/x",
        "../etc/passwd",
        "..",
        "/../../",
        "/AgentBackups/./../../..",
    ],
)
def test_path_traversal_blocked(evil_path: str) -> None:
    """路径遍历穿透必须被沙箱拦截，无论 .. 出现在哪个位置。"""
    client = _make_client()
    with pytest.raises(SandboxViolationError):
        client.check_sandbox(evil_path)


def test_path_traversal_normalized_stays_inside() -> None:
    """沙箱内部的 .. 自引用规整化后仍落在沙箱内，应放行且不越界。"""
    client = _make_client()
    clean = client.check_sandbox("/AgentBackups/sub/../file.txt")
    assert clean == "/AgentBackups/file.txt"
    assert clean.startswith("/AgentBackups")


def test_build_url_special_characters() -> None:
    """空格/中文/特殊字符必须逐段转义，且不破坏 URL 结构。"""
    client = _make_client()
    cases = {
        "/AgentBackups/my dir/a b.txt": "/dav/AgentBackups/my%20dir/a%20b.txt",
        "/AgentBackups/中文 目录/报告 v2.txt": "/dav/AgentBackups/%E4%B8%AD%E6%96%87%20%E7%9B%AE%E5%BD%95/%E6%8A%A5%E5%91%8A%20v2.txt",
        "/AgentBackups/file#1.txt": "/dav/AgentBackups/file%231.txt",
        "/AgentBackups/what?name=x": "/dav/AgentBackups/what%3Fname%3Dx",
        "/AgentBackups/100%.txt": "/dav/AgentBackups/100%25.txt",
        "/AgentBackups/a&b=c+d.txt": "/dav/AgentBackups/a%26b%3Dc%2Bd.txt",
        # build_url 自身会 normpath 解析 ..（沙箱检查在 check_sandbox 层负责拦截）
        "/AgentBackups/../etc/passwd": "/dav/etc/passwd",
    }
    for raw, encoded_subpath in cases.items():
        url = client.build_url(raw)
        assert url == "https://cloudreve.dc-sy.cn" + encoded_subpath, raw
        # 编码后不得残留裸露的特殊字符（/ 分隔符除外）
        tail = url.split("/dav/", 1)[1]
        for ch in "#?%&+ ":
            if ch == "%":
                # % 只允许出现在合法转义序列中
                import re
                assert re.fullmatch(r"(%[0-9A-Fa-f]{2}|[^%])*", tail), raw
            else:
                assert ch not in tail, (raw, ch)


def test_clean_remote_path_prefix_boundary() -> None:
    """href 前缀剥离必须按路径段边界匹配，/davbackup 不可被误剥为 /backup。"""
    client = _make_client()
    assert client._clean_remote_path("/dav/AgentBackups/a.txt") == "/AgentBackups/a.txt"
    assert client._clean_remote_path("/dav/") == "/"
    # 同前缀目录不被误剥
    assert client._clean_remote_path("/davbackup/file.txt") == "/davbackup/file.txt"
    # URL 编码的中文 href 正确解码
    assert client._clean_remote_path("/dav/AgentBackups/%E4%B8%AD%E6%96%87.txt") == "/AgentBackups/中文.txt"


def test_cat_head_bytes_exact_truncation() -> None:
    """cat --bytes N 必须精确截取前 N 字节，长于/短于文件均不出错。"""
    client = _make_client()
    payload = "A" * 100 + "中文内容测试" + "B" * 100

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raw = io.BytesIO(payload.encode("utf-8"))

    with patch.object(client.session, "get", return_value=mock_resp) as mget:
        out = client.cat("/AgentBackups/big.txt", head_bytes=50)
        assert len(out.encode("utf-8")) == 50
        assert out == payload[:50]
        mget.assert_called_once()
        assert mget.call_args.kwargs.get("allow_redirects") is True

    # 请求字节数超过文件长度：返回全部内容
    mock_resp.raw = io.BytesIO(payload.encode("utf-8"))
    with patch.object(client.session, "get", return_value=mock_resp):
        out = client.cat("/AgentBackups/big.txt", head_bytes=10**9)
        assert out == payload


def test_cat_head_lines_truncation() -> None:
    """cat --head N 只返回前 N 行。"""
    client = _make_client()
    lines = [f"line-{i}" for i in range(10)]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines = lambda: iter([ln.encode("utf-8") for ln in lines])

    with patch.object(client.session, "get", return_value=mock_resp):
        out = client.cat("/AgentBackups/log.txt", head_lines=3)
        assert out == "line-0\nline-1\nline-2"


def test_webdav_401_403_clear_error_no_credential_leak() -> None:
    """401/403 抛出明确异常，且异常文本绝不泄露用户名/密码。"""
    for code in (401, 403):
        client = _make_client(password="TOPSECRET-pass")
        mock_resp = MagicMock()
        mock_resp.status_code = code
        mock_resp.text = "Unauthorized"
        with patch.object(client.session, "request", return_value=mock_resp):
            with pytest.raises((PermissionError, WebDAVError)) as ei:
                client.status()
        msg = str(ei.value)
        assert "TOPSECRET-pass" not in msg
        assert "tester" not in msg
        assert msg  # 必须有明确错误说明


def test_webdav_404_raises_filenotfound() -> None:
    """404 统一转为 FileNotFoundError，供上层 exists()/调用方捕获。"""
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.object(client.session, "request", return_value=mock_resp):
        with pytest.raises(FileNotFoundError):
            client.ls("/AgentBackups/missing/")
        assert client.exists("/AgentBackups/missing/") is False


@pytest.mark.parametrize("code", [500, 502, 504])
def test_webdav_server_errors_raise_clearly(code: int) -> None:
    """50x 网络抖动/网关故障应抛出 WebDAVError 而非静默或误判为 404。"""
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.status_code = code
    mock_resp.text = "Bad Gateway" if code == 502 else "Error"
    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(WebDAVError) as ei:
            client.cat("/AgentBackups/f.txt")
    assert f"HTTP {code}" in str(ei.value)


def test_get_follows_302_redirect() -> None:
    """下载 GET 必须允许跟随 302 重定向（Cloudreve 签名下载地址场景）。"""
    client = _make_client()
    body = b"redirected-content"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content = lambda chunk_size: iter([body])
    mock_resp.headers = {"Content-Length": str(len(body))}

    with patch.object(client.session, "get", return_value=mock_resp) as mget:
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "out.bin"
            client.get("/AgentBackups/f.txt", dest)
            assert dest.read_bytes() == body
    assert mget.call_args.kwargs.get("allow_redirects") is True


def test_zero_byte_file_upload_download_roundtrip() -> None:
    """0 字节空文件的上传、下载、校验全链路。"""
    client = _make_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        empty = Path(tmpdir) / "empty.dat"
        empty.write_bytes(b"")
        assert compute_file_sha256(empty) == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

        remote_store: dict[str, bytes] = {}

        def fake_put(local_path, remote_path, overwrite=False, allow_outside=False):
            remote_store[remote_path] = Path(local_path).read_bytes()
            return {"remote_path": remote_path, "size": 0}

        def fake_get(remote_path, local_path, overwrite=False, allow_outside=False):
            Path(local_path).write_bytes(remote_store.get(remote_path, b""))
            return Path(local_path)

        with patch.object(client, "put", side_effect=fake_put), \
             patch.object(client, "get", side_effect=fake_get):
            manifest_data, _ = backup_file(
                client=client, local_path=empty,
                category="empty-test", output_arg=str(Path(tmpdir) / "m.json"),
            )
            assert manifest_data["source_file"]["size_bytes"] == 0
            assert manifest_data["source_file"]["sha256"].startswith("e3b0c442")

            restored = Path(tmpdir) / "restored-empty.dat"
            res = restore_file(
                client=client,
                remote_path=manifest_data["remote"]["path"],
                output_arg=str(restored),
            )
            assert restored.read_bytes() == b""
            assert res["verified"] is True
            assert res["size_bytes"] == 0


def test_prune_dry_run_never_deletes() -> None:
    """prune --dry-run 只列计划，绝不触发任何远程 DELETE。"""
    client = _make_client()
    snapshots = [
        {"name": f"{s}", "path": f"/AgentBackups/testhost/db/20261001/{s}/", "is_dir": True}
        for s in ("100000_a", "110000_b", "120000_c")
    ]

    def fake_ls(path, depth=1, allow_outside=False):
        if path.rstrip("/") == "/AgentBackups/testhost/db":
            return [{"name": "20261001", "path": "/AgentBackups/testhost/db/20261001/", "is_dir": True}]
        return snapshots

    with patch.object(client, "ls", side_effect=fake_ls), \
         patch.object(client, "rm", side_effect=AssertionError("dry-run 不得调用 rm!")):
        result = prune_backups(client=client, category="db", keep=1, dry_run=True)

    assert result["dry_run"] is True
    assert result["pruned_count"] == 2
    assert result["kept_count"] == 1
    assert "/AgentBackups/testhost/db/20261001/100000_a/" in result["pruned_snapshots"]


def test_prune_yes_deletes_and_counts_failures() -> None:
    """prune 执行模式：成功删除计入 pruned；rm 失败必须如实上报为失败。"""
    client = _make_client()
    snapshots = [
        {"name": f"{s}", "path": f"/AgentBackups/testhost/db/20261001/{s}/", "is_dir": True}
        for s in ("100000_a", "110000_b", "120000_c")
    ]

    def fake_ls(path, depth=1, allow_outside=False):
        if path.rstrip("/") == "/AgentBackups/testhost/db":
            return [{"name": "20261001", "path": "/AgentBackups/testhost/db/20261001/", "is_dir": True}]
        if path.rstrip("/") == "/AgentBackups/testhost/db/20261001":
            return snapshots
        return []

    deleted: list[str] = []

    def fake_rm(path, recursive=False, allow_outside=False):
        if "100000_a" in path:
            raise WebDAVError("HTTP 502 Bad Gateway")
        deleted.append(path)

    with patch.object(client, "ls", side_effect=fake_ls), \
         patch.object(client, "rm", side_effect=fake_rm):
        result = prune_backups(client=client, category="db", keep=1, dry_run=False)

    assert result["pruned_count"] == 1  # 只有 110000_b 真正删除成功
    assert "/AgentBackups/testhost/db/20261001/110000_b/" in result["pruned_snapshots"]
    assert result["failed_count"] == 1
    assert result["failed_prunes"][0]["path"].endswith("100000_a/")
    assert "502" in result["failed_prunes"][0]["error"]
    assert "/AgentBackups/testhost/db/20261001/110000_b/" in deleted


def test_prune_keep_zero_rejected() -> None:
    """keep<1 属危险配置，必须显式拒绝而非清空全部快照。"""
    client = _make_client()
    with pytest.raises(ValueError, match="keep"):
        prune_backups(client=client, category="db", keep=0, dry_run=True)


def test_cli_rm_dry_run_makes_no_network_request() -> None:
    """CLI rm --dry-run 全程零网络请求（连 client 都不得发出 DELETE）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = Path(tmpdir) / "config.toml"
        cfg_path.write_text(
            'base_url = "https://invalid.local"\n'
            'username = "u"\n'
            'password = "p"\n',
            encoding="utf-8",
        )
        r = subprocess.run(
            [sys.executable, str(CLI), "rm", "/AgentBackups/x.txt", "--dry-run", "--config", str(cfg_path)],
            capture_output=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "[dry-run]" in r.stdout.decode()


def test_rm_non_recursive_ls_failure_aborts_delete() -> None:
    """非递归删除时若 ls 检查失败（如 502），必须中止 DELETE，防止服务端递归误删。"""
    client = _make_client()

    def fake_request(method, url, **kwargs):
        mock = MagicMock()
        if method == "PROPFIND":
            mock.status_code = 502
            mock.text = "Bad Gateway"
        else:  # DELETE 不应发生
            raise AssertionError("ls 失败后不得发起 DELETE!")
        return mock

    with patch.object(client.session, "request", side_effect=fake_request):
        with pytest.raises(WebDAVError, match="中止删除"):
            client.rm("/AgentBackups/dir", recursive=False)


def test_rm_non_recursive_nonempty_dir_blocked() -> None:
    """非递归删除非空目录应被拦截，必须显式 --recursive。"""
    client = _make_client()

    listing = '<D:multistatus xmlns:D="DAV:"><D:response><D:href>/dav/AgentBackups/dir/</D:href></D:response><D:response><D:href>/dav/AgentBackups/dir/a.txt</D:href></D:response></D:multistatus>'
    called = {"delete": 0}

    def fake_request(method, url, **kwargs):
        mock = MagicMock()
        if method == "PROPFIND":
            mock.status_code = 207
            mock.content = listing.encode()
        else:
            called["delete"] += 1
            mock.status_code = 204
        return mock

    with patch.object(client.session, "request", side_effect=fake_request):
        with pytest.raises(RuntimeError, match="非空目录"):
            client.rm("/AgentBackups/dir", recursive=False)
    assert called["delete"] == 0


def test_restore_overwrite_flag_semantics() -> None:
    """restore 目标已存在默认报错；--overwrite 时才替换且内容正确。"""
    client = _make_client()
    content = b"restore-me-content"

    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "out.txt"
        target.write_bytes(b"old-content")

        with patch.object(client, "get", return_value=target), \
             patch.object(client, "cat", side_effect=FileNotFoundError("no manifest")):
            with pytest.raises(FileExistsError):
                restore_file(client=client, remote_path="/AgentBackups/h/test/x.txt",
                             output_arg=str(target), overwrite=False)

            def fake_get(remote_path, local_path, overwrite=False, allow_outside=False):
                assert overwrite is True
                Path(local_path).write_bytes(content)
                return Path(local_path)

            with patch.object(client, "get", side_effect=fake_get), \
                 patch.object(client, "cat", side_effect=FileNotFoundError("no manifest")):
                res = restore_file(client=client, remote_path="/AgentBackups/h/test/x.txt",
                                   output_arg=str(target), overwrite=True)
        assert target.read_bytes() == content
        assert res["verified"] is True


def test_put_existing_target_requires_overwrite() -> None:
    """put 目标已存在时默认抛 FileExistsError；overwrite=True 走覆盖路径。"""
    client = _make_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "f.txt"
        src.write_bytes(b"data")

        with patch.object(client, "exists", return_value=True), \
             patch.object(client, "mkdir_p"), \
             pytest.raises(FileExistsError):
            client.put(src, "/AgentBackups/f.txt", overwrite=False)

        # overwrite 时不再前置检查拦截，进入上传流程
        upload_called = {"put": False}

        def fake_session_put(url, data=None, **kwargs):
            upload_called["put"] = True
            mock = MagicMock()
            mock.status_code = 201
            return mock

        move_mock = MagicMock()
        move_mock.status_code = 201

        with patch.object(client, "exists", return_value=True), \
             patch.object(client, "mkdir_p"), \
             patch.object(client.session, "put", side_effect=fake_session_put), \
             patch.object(client.session, "request", return_value=move_mock):
            res = client.put(src, "/AgentBackups/f.txt", overwrite=True)
        assert upload_called["put"] is True
        assert res["remote_path"] == "/AgentBackups/f.txt"


def test_put_part_cleanup_on_move_failure() -> None:
    """MOVE 失败时必须清理 .part 临时文件，不留远端垃圾。"""
    client = _make_client()
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "f.txt"
        src.write_bytes(b"data")
        deleted: list[str] = []

        move_resp = MagicMock()
        move_resp.status_code = 412

        def fake_delete(url, **kwargs):
            deleted.append(url)
            m = MagicMock()
            m.status_code = 204
            return m

        with patch.object(client, "exists", return_value=False), \
             patch.object(client, "mkdir_p"), \
             patch.object(client.session, "put", return_value=MagicMock(status_code=201)), \
             patch.object(client.session, "request", return_value=move_resp), \
             patch.object(client.session, "delete", side_effect=fake_delete):
            with pytest.raises(WebDAVError, match="原子重命名"):
                client.put(src, "/AgentBackups/f.txt", overwrite=True)
        assert len(deleted) == 1
        assert ".part" in deleted[0]


def test_windows_remote_paths_normalization() -> None:
    """测试远程路径中的 Windows 反斜杠能够被统一转为正斜杠并在沙箱中正常校验。"""
    from client import normalize_remote_path
    client = _make_client()

    assert normalize_remote_path(r"\AgentBackups\configs\app.ini") == "/AgentBackups/configs/app.ini"
    assert normalize_remote_path(r"AgentBackups\media\test.png") == "/AgentBackups/media/test.png"
    assert normalize_remote_path(r"/AgentBackups\sub\file.txt") == "/AgentBackups/sub/file.txt"

    # check_sandbox 能够接纳反斜杠形式
    clean = client.check_sandbox(r"\AgentBackups\sub\file.txt")
    assert clean == "/AgentBackups/sub/file.txt"

    # check_dangerous_path 正确拦截反斜杠形式的根路径
    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path(r"\AgentBackups")
    with pytest.raises(DangerousOperationError):
        client.check_dangerous_path("\\")


def test_windows_local_drive_paths_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""测试 Windows 盘符路径（如 C:\... 或 E:/...）的跨环境解析支持。"""
    from client import resolve_local_path

    with tempfile.TemporaryDirectory() as tmpdir:
        # 普通相对/绝对路径正常解析
        p = resolve_local_path(tmpdir)
        assert p.is_dir()

        # 模拟 WSL 下的盘符解析降级逻辑
        test_win_path = r"E:\Projects\test\file.txt"
        resolved = resolve_local_path(test_win_path)
        # 在非 Windows 环境下应映射至 /mnt/e/Projects/test/file.txt
        if os.name != "nt":
            assert str(resolved).startswith("/mnt/e/Projects/test")

