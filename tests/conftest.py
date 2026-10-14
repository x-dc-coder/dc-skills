"""tests/conftest.py — 动态脚本安全加载器与测试环境隔离 fixture。

使用 SourceFileLoader 安全加载 scripts/ 目录下无 .py 后缀或带连字符的脚本，
并提供隔离的 tmp_workspace fixture，防止对真实主库或用户 $HOME 产生副作用。
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import sys
from typing import Any
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"


def load_script_module(script_name: str, module_name: str | None = None, reload: bool = False) -> Any:
    """基于 SourceFileLoader 安全动态加载 scripts/ 下的脚本（包括无后缀的 assets-doctor, skills-sync 等）。"""
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        # 尝试带 .py 加载
        if not script_name.endswith(".py") and (SCRIPTS_DIR / f"{script_name}.py").exists():
            script_path = SCRIPTS_DIR / f"{script_name}.py"
        else:
            raise FileNotFoundError(f"未找到脚本: {script_path}")

    mod_name = module_name or script_path.stem.replace("-", "_")
    if not reload and mod_name in sys.modules:
        return sys.modules[mod_name]

    loader = SourceFileLoader(mod_name, str(script_path))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {script_path} 创建 module spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture
def load_script():
    """提供动态脚本加载函数 fixture。"""
    return load_script_module


@pytest.fixture
def farm_model(load_script):
    """加载 farm_model 模块。"""
    return load_script("farm_model.py", "farm_model")


@pytest.fixture
def family_apply(load_script):
    """加载 family-apply.py 模块。"""
    return load_script("family-apply.py", "family_apply")


@pytest.fixture
def mcp_apply(load_script):
    """加载 mcp-apply.py 模块。"""
    return load_script("mcp-apply.py", "mcp_apply")


@pytest.fixture
def skills_sync(load_script):
    """加载 skills-sync 模块。"""
    return load_script("skills-sync", "skills_sync")


@pytest.fixture
def assets_doctor(load_script):
    """加载 assets-doctor 模块。"""
    return load_script("assets-doctor", "assets_doctor")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """隔离 $HOME，防止对真实 ~/.grok, ~/.claude 等配置产生副作用。"""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home
