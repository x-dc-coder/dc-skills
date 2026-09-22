"""test_detached.py — TDD tests for launch_detached().

Tests validate the detached subprocess launch path without depending
on a real GPU or a Windows Python binary.  ``sys.executable`` (the
current WSL Linux python3) stands in for ``py_exe``.

Because ``launch_detached`` calls ``wslpath -w`` to convert WSL paths
into UNC paths that a **Windows** child can write to, and our test
child runs as a **Linux** process, we monkeypatch ``subprocess.check_output``
to return the original WSL path (passthrough).  Test 5 overrides this
with a fake UNC-path mock to cover the conversion path.

Run:
    cd ~/projects/dc-skills && uv run pytest wsl-windows-bridge/scripts/test_detached.py -v
"""
from __future__ import annotations

import os
import subprocess as _sp
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_safe_subprocess as _mod  # noqa: E402  — for monkeypatch.setattr
from gpu_safe_subprocess import DetachedHandle, GpuLimits, launch_detached  # noqa: E402


# ────────────────────────────────────────────────────────────────────────
# Fixture — wslpath passthrough (Linux child can't open UNC paths)
# ────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _passthrough_wslpath(monkeypatch) -> None:
    """Mock ``wslpath -w`` to return the WSL path unchanged so the Linux
    child process can open it (UNC paths only work for Windows children,
    and our tests use ``sys.executable``, a Linux python3)."""
    _real = _sp.check_output

    def _mock(cmd, *a, **kw):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "wslpath" and cmd[1] == "-w":
            return cmd[-1].encode()
        return _real(cmd, *a, **kw)

    monkeypatch.setattr(_sp, "check_output", _mock)


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _kill_win_pid(win_pid: int) -> None:
    """Best-effort kill of the child process identified by *win_pid*."""
    try:
        os.kill(win_pid, 9)
    except (OSError, ProcessLookupError):
        pass  # already gone


# ────────────────────────────────────────────────────────────────────────
# Test 1 — log output
# ────────────────────────────────────────────────────────────────────────

def test_launches_and_writes_log_output(tmp_path: Path) -> None:
    """Launch a detached child that prints a sentinel, then assert it
    appears in the log file."""
    log_dir = tmp_path / "logs" / "gpu"
    log_dir.mkdir(parents=True, exist_ok=True)

    code = 'print("hello from detached"); import time; time.sleep(2)'
    handle: DetachedHandle = launch_detached(
        py_exe=sys.executable,
        code=code,
        job_name="test-logoutput",
        log_dir=log_dir,
    )

    try:
        # Give the child enough time to finish (poll ≤ 3 s + sleep 2 s)
        time.sleep(4)
        content = handle.wsl_log_path.read_text(encoding="utf-8")
        assert "hello from detached" in content, (
            f"Log file {handle.wsl_log_path} missing sentinel output"
        )
    finally:
        _kill_win_pid(handle.win_pid)


# ────────────────────────────────────────────────────────────────────────
# Test 2 — log file location
# ────────────────────────────────────────────────────────────────────────

def test_log_file_created_on_linux_fs(tmp_path: Path) -> None:
    """Assert the log path lives under the supplied *log_dir* (i.e. on the
    Linux filesystem, not ``/mnt/`` or a raw UNC path)."""
    log_dir = tmp_path / "logs" / "gpu"
    log_dir.mkdir(parents=True, exist_ok=True)

    code = "import time; time.sleep(1)"
    handle: DetachedHandle = launch_detached(
        py_exe=sys.executable,
        code=code,
        job_name="test-linuxfs",
        log_dir=log_dir,
    )

    try:
        assert str(handle.wsl_log_path).startswith(str(log_dir)), (
            f"Log path {handle.wsl_log_path} not under {log_dir}"
        )
        time.sleep(3)  # let child exit
    finally:
        _kill_win_pid(handle.win_pid)


# ────────────────────────────────────────────────────────────────────────
# Test 3 — PID file
# ────────────────────────────────────────────────────────────────────────

def test_pid_file_created_and_valid(tmp_path: Path) -> None:
    """Assert the PID file exists and its content is a digit-only string."""
    log_dir = tmp_path / "logs" / "gpu"
    log_dir.mkdir(parents=True, exist_ok=True)

    code = "import time; time.sleep(2)"
    handle: DetachedHandle = launch_detached(
        py_exe=sys.executable,
        code=code,
        job_name="test-pidfile",
        log_dir=log_dir,
    )

    try:
        assert handle.wsl_pid_path.exists(), (
            f"PID file {handle.wsl_pid_path} was not created"
        )
        pid_content = handle.wsl_pid_path.read_text(encoding="utf-8").strip()
        assert pid_content.isdigit(), (
            f"PID file content {pid_content!r} is not digit-only"
        )
        assert int(pid_content) == handle.win_pid, (
            f"PID file {pid_content} != handle.win_pid {handle.win_pid}"
        )
        time.sleep(3)  # let child exit
    finally:
        _kill_win_pid(handle.win_pid)


# ────────────────────────────────────────────────────────────────────────
# Test 4 — process alive check
# ────────────────────────────────────────────────────────────────────────

def test_process_still_alive_after_launch(tmp_path: Path) -> None:
    """Launch a child that sleeps 5 s, then verify it is still alive
    shortly after ``launch_detached`` returns, and finally wait for it
    to exit."""
    log_dir = tmp_path / "logs" / "gpu"
    log_dir.mkdir(parents=True, exist_ok=True)

    code = "import time; time.sleep(5)"
    handle: DetachedHandle = launch_detached(
        py_exe=sys.executable,
        code=code,
        job_name="test-alive",
        log_dir=log_dir,
    )

    try:
        # Child is still in its 5 s sleep — should be alive
        time.sleep(1)
        os.kill(handle.win_pid, 0)  # signal 0 = no-op; raises if dead
    except OSError:
        pytest.fail(
            f"Child PID {handle.win_pid} was already dead 1 s after launch"
        )
    finally:
        _kill_win_pid(handle.win_pid)
        time.sleep(4)  # let any straggler exit


# ────────────────────────────────────────────────────────────────────────
# Test 5 — unc path conversion
# ────────────────────────────────────────────────────────────────────────

def test_unc_path_conversion(tmp_path: Path, monkeypatch) -> None:
    """Mock ``wslpath -w`` to return a fake UNC path and verify that
    ``launch_detached`` does not crash when the conversion succeeds.

    The Linux child (``sys.executable``) cannot open UNC paths — only a
    real Windows child can.  To still exercise the conversion path we
    pre-write the PID file from inside the mock, so ``launch_detached``
    returns immediately once the polling loop sees it.  The real child
    launched by ``Popen`` will fail quietly in the background (it tries
    to open a UNC path that Linux cannot resolve), but that is expected
    and orthogonal to the conversion path being tested.
    """
    FAKE_PID = 99999

    def _mock_check_output(cmd, *a, **kw):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "wslpath" and cmd[1] == "-w":
            wsl_path = cmd[-1]
            unc = "\\\\wsl$\\Ubuntu" + wsl_path.replace("/", "\\")
            # Pre-write the PID file at the real WSL path so launch_detached
            # finds it on the first poll iteration (Linux child can't open
            # the UNC path to write it itself).
            if wsl_path.endswith(".pid"):
                Path(wsl_path).parent.mkdir(parents=True, exist_ok=True)
                Path(wsl_path).write_text(str(FAKE_PID))
            return unc.encode()
        raise RuntimeError(f"Unexpected subprocess call in test: {cmd}")

    monkeypatch.setattr(_sp, "check_output", _mock_check_output)

    log_dir = tmp_path / "logs" / "gpu"
    log_dir.mkdir(parents=True, exist_ok=True)

    code = "import time; time.sleep(1)"
    handle = launch_detached(
        py_exe=sys.executable,
        code=code,
        job_name="test-unc",
        log_dir=log_dir,
    )

    assert handle.win_pid == FAKE_PID
    assert str(handle.wsl_log_path).startswith(str(log_dir))
