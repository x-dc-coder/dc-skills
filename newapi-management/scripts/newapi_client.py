"""NewAPI (fork: QuantumNous/new-api, xiaolfeng/newapi-fix) REST API client.

封装官方 REST API 的调用细节，特别是 fork 版本的字段类型铁律：
  - setting/settings/model_mapping/status_code_mapping/param_override/header_override
    在 Go 结构体中是 *string，必须传 JSON 字符串（不能是嵌套对象）
  - channel_info 是结构体，必须传对象
本客户端自动完成 dict<->JSON 字符串转换，调用者只需用 dict。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import requests

# Go model.Channel 中声明为 *string 的字段：API 写入必须传 JSON 字符串
STRING_JSON_FIELDS: tuple[str, ...] = (
    "setting",
    "settings",
    "model_mapping",
    "status_code_mapping",
    "param_override",
    "header_override",
)

# Go model.Channel 中声明为结构体/json 的字段：API 传对象
OBJECT_FIELDS: tuple[str, ...] = ("channel_info",)


class NewAPIError(RuntimeError):
    """NewAPI 返回 success=false 或 HTTP 层失败时抛出。"""


@dataclass
class NewAPIConfig:
    base_url: str = "http://127.0.0.1:3000"
    token: str = ""
    timeout: float = 30.0

    @classmethod
    def resolve(
        cls,
        base_url: str | None = None,
        token: str | None = None,
        token_file: str | None = None,
    ) -> "NewAPIConfig":
        """配置发现优先级：显式参数 > 环境变量 > 环境变量指向的文件。"""
        url = base_url or os.environ.get("NEWAPI_BASE_URL", "http://127.0.0.1:3000")
        tok = token or os.environ.get("NEWAPI_TOKEN", "")
        if not tok:
            tf = token_file or os.environ.get("NEWAPI_TOKEN_FILE", "")
            if tf:
                try:
                    with open(tf, encoding="utf-8") as fh:
                        tok = fh.read().strip()
                except OSError as exc:
                    raise NewAPIError(f"读取 token 文件失败 {tf}: {exc}") from exc
        if not tok:
            raise NewAPIError(
                "缺少访问令牌：请传 --token，或设置环境变量 NEWAPI_TOKEN / "
                "NEWAPI_TOKEN_FILE（指向令牌文件路径）"
            )
        return cls(base_url=url.rstrip("/"), token=tok)


def _stringify_json_fields(config: dict[str, Any]) -> dict[str, Any]:
    """dict/list -> JSON 字符串（写入库时调用，对齐 Go *string 字段）。"""
    out = dict(config)
    for name in STRING_JSON_FIELDS:
        value = out.get(name)
        if isinstance(value, (dict, list)):
            out[name] = json.dumps(value, ensure_ascii=False)
    return out


def _parse_json_fields(config: dict[str, Any]) -> dict[str, Any]:
    """JSON 字符串 -> dict（读取时调用，方便消费）。解析失败保留原字符串。"""
    out = dict(config)
    for name in STRING_JSON_FIELDS:
        value = out.get(name)
        if isinstance(value, str) and value.strip():
            try:
                out[name] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return out


@dataclass
class NewAPIClient:
    config: NewAPIConfig

    @property
    def base_url(self) -> str:
        return self.config.base_url

    def _request(
        self, method: str, path: str, *, params: dict | None = None, body: Any = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=body,
                timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            raise NewAPIError(f"请求 {method} {url} 网络失败: {exc}") from exc
        if resp.status_code == 401:
            raise NewAPIError("认证失败（401）：令牌无效或已过期")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise NewAPIError(
                f"{method} {url} 返回非 JSON（HTTP {resp.status_code}）: {resp.text[:120]}"
            ) from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            raise NewAPIError(f"API 拒绝: {payload.get('message', '未知错误')}")
        return payload.get("data", payload)

    # ---------- 健康检查 ----------

    def get_status(self) -> Any:
        return self._request("GET", "/api/status")

    # ---------- 渠道 ----------

    def list_channels(
        self,
        page: int = 1,
        page_size: int = 10,
        keyword: str | None = None,
        group: str | None = None,
        tag: str | None = None,
        status: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if keyword:
            params["keyword"] = keyword
        if group:
            params["group"] = group
        if tag:
            params["tag"] = tag
        if status is not None:
            params["status"] = status
        return self._request("GET", "/api/channel/", params=params)

    def search_channels(self, keyword: str) -> Any:
        return self._request("GET", "/api/channel/search", params={"keyword": keyword})

    def get_channel(self, channel_id: int) -> dict[str, Any]:
        data = self._request("GET", f"/api/channel/{channel_id}")
        if isinstance(data, dict):
            return _parse_json_fields(data)
        return data

    def create_channel(
        self, config: dict[str, Any], mode: str = "single"
    ) -> None:
        """创建渠道。config 为平铺的渠道字段（dict 形式的 fork 字段会自动转换）。

        mode: single 单建 | batch 按 key 换行批量建 | multi_to_single 多Key轮询
        POST 成功不返回 id（官方接口设计），需要 id 请调 list_channels 或 copy_channel。
        """
        if mode not in ("single", "batch", "multi_to_single"):
            raise NewAPIError(f"不支持的添加模式: {mode}")
        body = {"mode": mode, "channel": _stringify_json_fields(config)}
        self._request("POST", "/api/channel", body=body)

    def update_channel(self, config: dict[str, Any]) -> None:
        """更新渠道。config 必须含 id，其余为待更新字段。"""
        if not config.get("id"):
            raise NewAPIError("更新渠道必须提供 id")
        self._request("PUT", "/api/channel/", body=_stringify_json_fields(config))

    def delete_channel(self, channel_id: int) -> None:
        self._request("DELETE", f"/api/channel/{channel_id}")

    def set_channel_status(self, channel_id: int, status: int) -> Any:
        """启用/禁用渠道。status: 1=启用 2=手动禁用 3=自动禁用。返回 changedCount。

        该 fork 有两个陷阱，客户端已自动补偿：
          1. 未注册 PUT /api/channel/:id/status 路由，统一走批量接口传单元素；
          2. 内存缓存时序：渠道不在缓存时状态更新会静默失败（changedCount=0 且
             数据库未变）——新建渠道尤甚（创建不刷新缓存）。此时先做一次 PUT
             触发 InitChannelCache 全量刷新，再重试。
        """
        result = self._request(
            "POST",
            "/api/channel/status/batch",
            body={"ids": [channel_id], "status": status},
        )
        if result == 0:  # 未改变：可能状态本就相同，或缓存未命中
            current = self.get_channel(channel_id)
            if isinstance(current, dict) and current.get("status") != status:
                # PUT 任意字段触发全量缓存刷新（remark 传回原值，不改动数据）
                self.update_channel(
                    {"id": channel_id, "remark": current.get("remark") or ""}
                )
                result = self._request(
                    "POST",
                    "/api/channel/status/batch",
                    body={"ids": [channel_id], "status": status},
                )
        return result

    def test_channel(self, channel_id: int, model: str | None = None) -> Any:
        params = {"model": model} if model else None
        return self._request("GET", f"/api/channel/test/{channel_id}", params=params)

    def copy_channel(self, channel_id: int) -> Any:
        return self._request("POST", f"/api/channel/copy/{channel_id}", body=None)

    def fetch_models(self, channel_id: int) -> Any:
        return self._request("GET", f"/api/channel/fetch_models/{channel_id}")

    def update_balance(self, channel_id: int) -> Any:
        return self._request("GET", f"/api/channel/update_balance/{channel_id}")

    # ---------- 日志 / 会话 ----------

    def list_logs(
        self,
        page: int = 1,
        page_size: int = 10,
        channel_id: int | None = None,
        model_name: str | None = None,
        request_id: str | None = None,
        user_id: int | None = None,
        token_name: str | None = None,
        group: str | None = None,
        ip: str | None = None,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        log_type: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        mapping = {
            "channel_id": channel_id,
            "model_name": model_name,
            "request_id": request_id,
            "user_id": user_id,
            "token_name": token_name,
            "group": group,
            "ip": ip,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "type": log_type,
        }
        for key, value in mapping.items():
            if value is not None and value != "":
                params[key] = value
        data = self._request("GET", "/api/log/", params=params)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data["items"] = [_parse_json_fields(i) for i in data["items"]]
        return data

    def get_log(self, log_id: int) -> dict[str, Any]:
        """取单条日志（含 record 完整会话原文、full_log 上游错误明细）。"""
        data = self._request("GET", "/api/log/", params={"id": log_id})
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
            if not items:
                raise NewAPIError(f"日志 {log_id} 不存在")
            return _parse_json_fields(items[0])
        if isinstance(data, dict):
            return _parse_json_fields(data)
        return data

    # ---------- 令牌 / 数据 ----------

    def list_tokens(self, page: int = 1, page_size: int = 10) -> Any:
        return self._request(
            "GET", "/api/token/", params={"page": page, "page_size": page_size}
        )

    def get_data(
        self,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        self_only: bool = False,
    ) -> Any:
        params: dict[str, Any] = {}
        if start_timestamp:
            params["start_timestamp"] = start_timestamp
        if end_timestamp:
            params["end_timestamp"] = end_timestamp
        path = "/api/data/self" if self_only else "/api/data/"
        return self._request("GET", path, params=params)


__all__ = [
    "NewAPIClient",
    "NewAPIConfig",
    "NewAPIError",
    "STRING_JSON_FIELDS",
    "OBJECT_FIELDS",
    "_stringify_json_fields",
    "_parse_json_fields",
]
