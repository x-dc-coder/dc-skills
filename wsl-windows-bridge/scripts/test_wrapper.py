"""test_wrapper.py — TDD tests for win-launcher.py + use_wrapper integration.

Tests cover: subprocess execution through win-launcher.py, exit code propagation,
environment variable injection, graceful degradation on Linux, and use_wrapper
flag routing in stream_gpu_windows().

Run:
    cd ~/projects/dc-skills && uv run pytest wsl-windows-bridge/scripts/test_wrapper.py -v
"""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

# Path to win-launcher.py (run as subprocess, not imported)
WIN_LAUNCHER = Path(__file__).resolve().parent / "win-launcher.py"

# Import for tests 5-6
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_safe_subprocess import (  # noqa: E402
    StreamingLine,
    stream_gpu_windows,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Run win-launcher.py as subprocess: verify child stdout is forwarded
# ═══════════════════════════════════════════════════════════════════════════════
def test_wrapper_runs_subprocess():
    code = b"print('hello from wrapper')"
    encoded = base64.b64encode(code).decode("ascii")
    r = subprocess.run(
        [
            sys.executable, str(WIN_LAUNCHER),
            "--py-exe", sys.executable,
            "--code", encoded,
            "--gpu-fraction", "0.4",
            "--cpu-threads", "6",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert "hello from wrapper" in r.stdout, (
        f"expected 'hello from wrapper' in stdout, got: {r.stdout!r}"
    )
    assert r.returncode == 0, (
        f"expected returncode 0, got {r.returncode}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Child exit code propagated through wrapper
# ═══════════════════════════════════════════════════════════════════════════════
def test_wrapper_propagates_exit_code():
    code = b"import sys; sys.exit(7)"
    encoded = base64.b64encode(code).decode("ascii")
    r = subprocess.run(
        [
            sys.executable, str(WIN_LAUNCHER),
            "--py-exe", sys.executable,
            "--code", encoded,
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 7, (
        f"expected returncode 7, got {r.returncode}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. win-launcher.py sets PYTHONUNBUFFERED=1 in child environment
# ═══════════════════════════════════════════════════════════════════════════════
def test_wrapper_env_set_natively():
    code = b"import os; print(os.environ.get('PYTHONUNBUFFERED'))"
    encoded = base64.b64encode(code).decode("ascii")
    r = subprocess.run(
        [
            sys.executable, str(WIN_LAUNCHER),
            "--py-exe", sys.executable,
            "--code", encoded,
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert "1" in r.stdout, (
        f"expected PYTHONUNBUFFERED=1 in child output, got stdout: {r.stdout!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. On Linux: Job Object unavailable → warning to stderr, child still runs
# ═══════════════════════════════════════════════════════════════════════════════
def test_wrapper_job_object_degrades_gracefully():
    code = b"import sys; sys.exit(42)"
    encoded = base64.b64encode(code).decode("ascii")
    r = subprocess.run(
        [
            sys.executable, str(WIN_LAUNCHER),
            "--py-exe", sys.executable,
            "--code", encoded,
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert "Job Object unavailable" in r.stderr, (
        f"expected degradation warning in stderr, got: {r.stderr!r}"
    )
    assert r.returncode == 42, (
        f"expected child exit code 42 propagated, got {r.returncode}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. use_wrapper flag routes to win-launcher.py in stream_gpu_windows()
# ═══════════════════════════════════════════════════════════════════════════════
def test_use_wrapper_flag_in_stream_gpu_windows(monkeypatch):
    recorded_cmds: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            recorded_cmds.append(list(cmd))
            raise RuntimeError("fake")

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    # ── use_wrapper=True: cmd should route through win-launcher.py ──────────
    try:
        for _ in stream_gpu_windows(
            py_exe=sys.executable, code="pass",
            use_wrapper=True, timeout=5,
        ):
            pass
    except RuntimeError:
        pass

    assert any("win-launcher.py" in str(cmd) for cmd in recorded_cmds), (
        f"use_wrapper=True should route through win-launcher.py, "
        f"recorded cmds: {recorded_cmds}"
    )

    # ── use_wrapper=False: cmd should NOT contain win-launcher.py ───────────
    recorded_cmds.clear()
    try:
        for _ in stream_gpu_windows(
            py_exe=sys.executable, code="pass",
            use_wrapper=False, timeout=5,
        ):
            pass
    except RuntimeError:
        pass

    assert not any("win-launcher.py" in str(cmd) for cmd in recorded_cmds), (
        f"use_wrapper=False should NOT route through win-launcher.py, "
        f"recorded cmds: {recorded_cmds}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. use_wrapper=False regression: normal streaming works, no crash
# ═══════════════════════════════════════════════════════════════════════════════
def test_use_wrapper_false_regression():
    code = "print('line1'); print('line2')"
    lines: list[StreamingLine] = list(stream_gpu_windows(
        py_exe=sys.executable,
        code=code,
        use_wrapper=False,
        timeout=5,
    ))
    assert len(lines) == 2, (
        f"expected 2 lines from stream_gpu_windows, got {len(lines)}"
    )
    assert "line1" in lines[0].text, (
        f"first line should contain 'line1', got: {lines[0].text!r}"
    )
    assert "line2" in lines[1].text, (
        f"second line should contain 'line2', got: {lines[1].text!r}"
    )
