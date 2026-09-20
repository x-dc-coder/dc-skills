"""newapi-management 测试。

分两类：
  1. 单元测试（无网络）：字段类型转换、payload 构造、配置发现、参数校验
  2. 集成测试（连真实 NewAPI）：全链路 CRUD + 日志，验证所有能力可达；
     默认 http://127.0.0.1:3000 不可达或无令牌时自动 skip。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main as cli_main  # noqa: E402
from newapi_client import (  # noqa: E402
    NewAPIClient,
    NewAPIConfig,
    NewAPIError,
    _parse_json_fields,
    _stringify_json_fields,
)

# ---------- 单元测试 ----------


def test_stringify_converts_dict_to_json_string():
    cfg = {
        "setting": {"force_format": True, "system_prompt": "你好"},
        "model_mapping": {"gpt-4": "gpt-4o"},
        "param_override": {"temperature": 0.7},
    }
    out = _stringify_json_fields(cfg)
    assert out["setting"] == '{"force_format": true, "system_prompt": "你好"}'
    assert out["model_mapping"] == '{"gpt-4": "gpt-4o"}'
    assert out["param_override"] == '{"temperature": 0.7}'


def test_stringify_keeps_string_and_object_fields():
    cfg = {
        "setting": "already-a-string",  # 已是字符串，保持不变
        "channel_info": {"is_multi_key": True},  # 对象字段保持对象
        "name": "普通字段",
    }
    out = _stringify_json_fields(cfg)
    assert out["setting"] == "already-a-string"
    assert out["channel_info"] == {"is_multi_key": True}
    assert out["name"] == "普通字段"


def test_parse_converts_json_string_to_dict():
    cfg = {
        "setting": '{"force_format": true}',
        "model_mapping": '{"gpt-4": "gpt-4o"}',
        "empty_field": "",
        "non_json": "plain text",
    }
    out = _parse_json_fields(cfg)
    assert out["setting"] == {"force_format": True}
    assert out["model_mapping"] == {"gpt-4": "gpt-4o"}
    assert out["empty_field"] == ""  # 空字符串保持
    assert out["non_json"] == "plain text"  # 非 JSON 保持原样


def test_create_channel_wraps_mode_and_channel(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        NewAPIClient,
        "_request",
        lambda self, method, path, **kw: captured.update(
            method=method, path=path, body=kw.get("body")
        ) or {},
    )
    client = NewAPIClient(NewAPIConfig(base_url="http://x", token="t"))
    client.create_channel(
        {"type": 1, "name": "n", "key": "k", "setting": {"a": 1}}, mode="single"
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/channel"
    # 必须包裹 mode + channel，且 dict 字段已转字符串
    assert captured["body"] == {
        "mode": "single",
        "channel": {
            "type": 1,
            "name": "n",
            "key": "k",
            "setting": '{"a": 1}',
        },
    }


def test_create_channel_rejects_bad_mode():
    client = NewAPIClient(NewAPIConfig(base_url="http://x", token="t"))
    with pytest.raises(NewAPIError, match="不支持的添加模式"):
        client.create_channel({"name": "n"}, mode="invalid")


def test_update_channel_requires_id():
    client = NewAPIClient(NewAPIConfig(base_url="http://x", token="t"))
    with pytest.raises(NewAPIError, match="必须提供 id"):
        client.update_channel({"name": "no-id"})


def test_config_resolve_prefers_explicit(monkeypatch):
    monkeypatch.setenv("NEWAPI_TOKEN", "env-token")
    cfg = NewAPIConfig.resolve(base_url="http://explicit", token="explicit")
    assert cfg.base_url == "http://explicit"
    assert cfg.token == "explicit"


def test_config_resolve_uses_env(monkeypatch):
    monkeypatch.setenv("NEWAPI_TOKEN", "env-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://env-host:3000")
    cfg = NewAPIConfig.resolve()
    assert cfg.base_url == "http://env-host:3000"
    assert cfg.token == "env-token"


def test_config_resolve_token_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWAPI_TOKEN", raising=False)
    tf = tmp_path / "tok"
    tf.write_text("file-token\n", encoding="utf-8")
    cfg = NewAPIConfig.resolve(token_file=str(tf))
    assert cfg.token == "file-token"


def test_config_resolve_missing_token(monkeypatch):
    monkeypatch.delenv("NEWAPI_TOKEN", raising=False)
    monkeypatch.delenv("NEWAPI_TOKEN_FILE", raising=False)
    with pytest.raises(NewAPIError, match="缺少访问令牌"):
        NewAPIConfig.resolve()


def test_client_methods_hit_correct_endpoints(monkeypatch):
    """验证客户端各公开方法的 method/path/params 构造正确（覆盖剩余公开方法）。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        NewAPIClient,
        "_request",
        lambda self, method, path, **kw: calls.append((method, path, kw)) or {},
    )
    client = NewAPIClient(NewAPIConfig(base_url="http://x", token="t"))

    client.search_channels("关键词")
    client.fetch_models(7)
    client.update_balance(7)
    client.list_tokens(page=2, page_size=5)
    client.get_data(start_timestamp=1000, end_timestamp=2000)
    client.get_data(self_only=True)

    assert calls[0] == ("GET", "/api/channel/search", {"params": {"keyword": "关键词"}})
    assert calls[1] == ("GET", "/api/channel/fetch_models/7", {})
    assert calls[2] == ("GET", "/api/channel/update_balance/7", {})
    assert calls[3] == ("GET", "/api/token/", {"params": {"page": 2, "page_size": 5}})
    assert calls[4] == (
        "GET",
        "/api/data/",
        {"params": {"start_timestamp": 1000, "end_timestamp": 2000}},
    )
    assert calls[5] == ("GET", "/api/data/self", {"params": {}})


def test_set_channel_status_retries_on_cache_miss(monkeypatch):
    """缓存未命中时先 PUT 触发刷新再重试。"""
    calls: list[tuple] = []
    batch_returns = [0, 1]  # 第一次失败（缓存未命中），重试成功

    def fake_request(self, method, path, **kw):
        calls.append((method, path, kw.get("body")))
        if path == "/api/channel/status/batch":
            return batch_returns[len([c for c in calls if c[1] == path]) - 1]
        if path.startswith("/api/channel/") and method == "GET":
            return {"status": 1, "remark": "原备注"}  # 当前状态与目标不同
        return {}

    monkeypatch.setattr(NewAPIClient, "_request", fake_request)
    client = NewAPIClient(NewAPIConfig(base_url="http://x", token="t"))

    result = client.set_channel_status(42, 2)
    assert result == 1
    # 顺序：batch 失败 → GET 现状 → PUT 触发刷新 → batch 重试成功
    assert [c[0] for c in calls] == ["POST", "GET", "PUT", "POST"]
    assert calls[2] == (  # PUT 回写原 remark，不改动数据
        "PUT",
        "/api/channel/",
        {"id": 42, "remark": "原备注"},
    )


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0


def test_cli_output_flag_works_after_subcommand(tmp_path, monkeypatch):
    """--output 可放在子命令之后（子解析器通过 parents 继承全局参数）。"""
    out = tmp_path / "result.json"
    monkeypatch.setattr(
        "cli.NewAPIClient",
        lambda cfg: type("Fake", (), {"get_status": lambda self: {"ok": True}})(),
    )
    rc = cli_main(["status", "--token", "t", "--output", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"ok": True}


# ---------- 集成测试：全链路验证 ----------

_INTEGRATION_MARK = pytest.mark.skipif(
    not (os.environ.get("NEWAPI_TOKEN") or os.environ.get("NEWAPI_TOKEN_FILE")),
    reason="未设置 NEWAPI_TOKEN/NEWAPI_TOKEN_FILE，跳过集成测试",
)


@_INTEGRATION_MARK
class TestIntegration:
    """连真实 NewAPI 跑全链路：创建→读取→更新→测试→复制→日志→删除。"""

    @pytest.fixture(scope="class")
    @classmethod
    def client(cls) -> NewAPIClient:
        return NewAPIClient(NewAPIConfig.resolve())

    @pytest.fixture
    def temp_channel(self, client: NewAPIClient):
        """创建一个临时渠道，测试结束自动删除。"""
        stamp = int(time.time())
        config = {
            "type": 1,
            "name": f"skill-selftest-{stamp}",
            "key": f"sk-selftest-{stamp}",
            "base_url": "https://api.example.invalid",
            "models": "gpt-4o,gpt-4o-mini",
            "group": "default",
            "tag": "selftest",
            "setting": {"force_format": True, "system_prompt": "自测"},
            "settings": {"allow_service_tier": True},
            "channel_info": {"is_multi_key": False},
        }
        client.create_channel(config, mode="single")
        channel_id = self._find_id(client, config["name"])
        assert channel_id, "创建后未能在列表中找到新渠道"
        yield channel_id, config
        client.delete_channel(channel_id)

    @staticmethod
    def _find_id(client: NewAPIClient, name: str) -> int | None:
        data = client.list_channels(page=1, page_size=50, keyword=name)
        for item in data.get("items", []):
            if item.get("name") == name:
                return item["id"]
        return None

    def test_status(self, client: NewAPIClient):
        data = client.get_status()
        assert isinstance(data, dict)

    def test_channel_list(self, client: NewAPIClient):
        data = client.list_channels(page=1, page_size=5)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_create_get_update_delete(self, client: NewAPIClient, temp_channel):
        channel_id, config = temp_channel

        # 读取验证：fork 字段被解析回 dict
        got = client.get_channel(channel_id)
        assert got["name"] == config["name"]
        assert got["tag"] == "selftest"
        assert got["setting"]["force_format"] is True
        assert got["setting"]["system_prompt"] == "自测"
        assert got["settings"]["allow_service_tier"] is True

        # 更新
        client.update_channel(
            {
                "id": channel_id,
                "tag": "selftest-updated",
                "setting": {"force_format": False, "proxy": "http://127.0.0.1:7890"},
            }
        )
        got = client.get_channel(channel_id)
        assert got["tag"] == "selftest-updated"
        assert got["setting"]["proxy"] == "http://127.0.0.1:7890"
        assert got["setting"]["force_format"] is False

    def test_set_status(self, client: NewAPIClient, temp_channel):
        channel_id, _ = temp_channel
        client.set_channel_status(channel_id, 2)  # 手动禁用
        got = client.get_channel(channel_id)
        assert got["status"] == 2
        client.set_channel_status(channel_id, 1)  # 恢复启用
        assert client.get_channel(channel_id)["status"] == 1

    def test_copy_channel(self, client: NewAPIClient, temp_channel):
        channel_id, _ = temp_channel
        result = client.copy_channel(channel_id)
        new_id = result.get("id") if isinstance(result, dict) else None
        assert new_id, "复制接口应返回新渠道 id"
        try:
            got = client.get_channel(new_id)
            assert got["setting"]["force_format"] is True  # fork 字段应保真复制
        finally:
            client.delete_channel(new_id)

    def test_log_list_and_get(self, client: NewAPIClient):
        data = client.list_logs(page=1, page_size=1)
        assert "items" in data
        items = data["items"]
        if not items:
            pytest.skip("当前无日志记录")
        log_id = items[0]["id"]
        got = client.get_log(log_id)
        assert got["id"] == log_id
        # record 是完整会话原文（可能为空字符串，但字段必须存在）
        assert "record" in got
        assert "full_log" in got
