"""tests/test_mcp_apply.py — mcp-apply 编译分发、安全标记块、凭据审计与回滚测试。

覆盖需求：
  1. # BEGIN/END dc-farm mcp 安全标记块在 TOML 中的生成与替换，验证标记外内容 100% 保留。
  2. 标记单侧缺失拒写（ValueError）。
  3. Claude JSON 中受控键合并与非受控服务保留。
  4. 明文敏感凭据拦截（退出码 2）。
  5. 备份生成、原子替换与 --rollback 回滚恢复。
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import tomli


class TestGrokTomlManagedBlock:
    def test_block_generation_and_replacement_preserves_external(self, mcp_apply):
        """测试在 TOML 中生成与替换安全标记块，验证标记外内容 100% 保留。"""
        initial_grok_toml = """[cli]
auto_update = true

[ui]
compact_mode = false

# BEGIN dc-farm mcp
# Managed by dc-skills mcp-apply. DO NOT EDIT THIS BLOCK MANUALLY.
[mcp_servers.old_mcp]
command = "old_cmd"
enabled = true
# END dc-farm mcp

[privacy]
privacy_banner_acked = "2026-09-21T10:50:51Z"
"""
        new_mcps = {
            "codegraph": {
                "transport": "stdio",
                "command": "codegraph",
                "args": ["serve", "--mcp"],
                "enabled": True,
            },
            "keenable": {
                "transport": "http",
                "url": "https://api.keenable.ai/mcp",
                "enabled": True,
            },
        }

        updated_toml, changed = mcp_apply.update_grok_config(initial_grok_toml, new_mcps)
        assert changed is True

        # 验证标记外内容完整保留
        assert "[cli]\nauto_update = true" in updated_toml
        assert "[ui]\ncompact_mode = false" in updated_toml
        assert '[privacy]\nprivacy_banner_acked = "2026-09-21T10:50:51Z"' in updated_toml

        # 验证新标记块已更新且旧的 old_mcp 已被替换
        assert "[mcp_servers.old_mcp]" not in updated_toml
        assert "[mcp_servers.codegraph]" in updated_toml
        assert "[mcp_servers.keenable]" in updated_toml
        assert mcp_apply.BEGIN_MARKER in updated_toml
        assert mcp_apply.END_MARKER in updated_toml

        # 验证 TOML 静态语法有效
        parsed = tomli.loads(updated_toml)
        assert parsed["cli"]["auto_update"] is True
        assert "codegraph" in parsed["mcp_servers"]
        assert "keenable" in parsed["mcp_servers"]

    def test_one_sided_marker_missing_raises_value_error(self, mcp_apply):
        """测试标记单侧缺失拒写（抛出 ValueError）。"""
        # 仅有 BEGIN 没有 END
        broken_begin = """[cli]
auto_update = true
# BEGIN dc-farm mcp
[mcp_servers.broken]
command = "test"
"""
        with pytest.raises(ValueError, match="检测到 BEGIN 或 END 单侧缺失"):
            mcp_apply.update_grok_config(broken_begin, {"test": {"command": "test"}})

        # 仅有 END 没有 BEGIN
        broken_end = """[cli]
auto_update = true
[mcp_servers.broken]
command = "test"
# END dc-farm mcp
"""
        with pytest.raises(ValueError, match="检测到 BEGIN 或 END 单侧缺失"):
            mcp_apply.update_grok_config(broken_end, {"test": {"command": "test"}})


class TestClaudeJsonControlledMerge:
    def test_claude_controlled_merge_preserves_uncontrolled(self, mcp_apply):
        """测试 Claude JSON 中受控键合并与非受控服务保留。"""
        initial_claude_json = {
            "numStartups": 100,
            "theme": "dark",
            "mcpServers": {
                "uncontrolled_custom_server": {
                    "type": "stdio",
                    "command": "custom_mcp_cmd",
                    "args": ["--dev"],
                },
                "codegraph": {
                    "type": "stdio",
                    "command": "old_codegraph",
                    "args": [],
                },
            },
        }

        claude_mcps = {
            "codegraph": {
                "transport": "stdio",
                "command": "codegraph",
                "args": ["serve", "--mcp"],
                "enabled": True,
            },
            "vision": {
                "transport": "http",
                "url": "http://127.0.0.1:8973/mcp",
                "enabled": True,
            },
        }

        new_data, changed = mcp_apply.update_claude_config(initial_claude_json, claude_mcps)
        assert changed is True

        # 非受控顶层键与其他服务保留
        assert new_data["numStartups"] == 100
        assert new_data["theme"] == "dark"
        assert "uncontrolled_custom_server" in new_data["mcpServers"]
        assert new_data["mcpServers"]["uncontrolled_custom_server"]["command"] == "custom_mcp_cmd"

        # 受控键更新与添加
        assert new_data["mcpServers"]["codegraph"]["command"] == "codegraph"
        assert new_data["mcpServers"]["codegraph"]["args"] == ["serve", "--mcp"]
        assert "vision" in new_data["mcpServers"]
        assert new_data["mcpServers"]["vision"]["url"] == "http://127.0.0.1:8973/mcp"


class TestCredentialSafety:
    def test_plaintext_credential_intercept(self, mcp_apply):
        """测试明文敏感凭据拦截（退出码 2）。"""
        # 安全的引用 ${VAR}
        safe_cfg = {
            "headers": {"Authorization": "Bearer ${MY_API_KEY}"},
            "env": {"API_KEY": "${OPENAI_KEY}"},
        }
        mcp_apply.validate_credential_safety("safe_mcp", safe_cfg)

        # 明文 API Key 在 headers
        bad_header_cfg = {
            "headers": {"Authorization": "Bearer sk-1234567890abcdef"},
        }
        with pytest.raises(SystemExit) as exc:
            mcp_apply.validate_credential_safety("bad_mcp", bad_header_cfg)
        assert exc.value.code == 2

        # 明文 API Key 在 env
        bad_env_cfg = {
            "env": {"SECRET_TOKEN": "my_plaintext_secret_token"},
        }
        with pytest.raises(SystemExit) as exc:
            mcp_apply.validate_credential_safety("bad_mcp", bad_env_cfg)
        assert exc.value.code == 2


class TestBackupAndRollback:
    def test_backup_generation_atomic_write_and_rollback(self, mcp_apply, tmp_path):
        """测试备份生成、原子替换与 rollback 回滚恢复。"""
        test_file = tmp_path / "config.toml"
        initial_content = '[test]\nkey = "initial_value"\n'
        test_file.write_text(initial_content, encoding="utf-8")

        # 生成备份
        bak_file = mcp_apply.backup_file(test_file)
        assert bak_file.exists()
        assert bak_file.read_text(encoding="utf-8") == initial_content
        assert ".bak-" in bak_file.name

        # 原子写入新内容
        updated_content = '[test]\nkey = "updated_value"\n'
        mcp_apply.atomic_write(test_file, updated_content)
        assert test_file.read_text(encoding="utf-8") == updated_content

        # 回滚恢复
        rollback_success = mcp_apply.rollback_file(test_file)
        assert rollback_success is True
        assert test_file.read_text(encoding="utf-8") == initial_content
