"""test_streaming.py — TDD tests for stream_gpu_windows().

Tests cover: line-by-line streaming, \r cleaning, UTF-8 error handling,
timeout enforcement, returncode propagation, and environment variable injection.

Run:
    cd ~/projects/dc-skills && uv run pytest wsl-windows-bridge/scripts/test_streaming.py -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# 让 test 文件能导入同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_safe_subprocess import (  # noqa: E402
    StreamingLine,
    stream_gpu_windows,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Line-by-line streaming: 5 lines printed → 5 StreamingLine yields
# ═══════════════════════════════════════════════════════════════════════════════
def test_streams_lines_one_by_one():
    code = "for i in range(5): print(f'line {i}')"
    lines: list[StreamingLine] = list(stream_gpu_windows(
        py_exe=sys.executable,
        code=code,
        timeout=5,
    ))
    assert len(lines) == 5, f"expected 5 lines, got {len(lines)}"
    for i, line in enumerate(lines):
        assert isinstance(line, StreamingLine)
        assert line.stream == "stdout"
        assert f"line {i}" in line.text


# ═══════════════════════════════════════════════════════════════════════════════
# 2. \r content cleaned when clean_carriage_return=True
# ═══════════════════════════════════════════════════════════════════════════════
def test_carriage_return_cleaned():
    # print() appends \n, so child stdout is "progress\r50%\n".
    # Popen(text=True) uses universal newlines: \r→\n translation at I/O layer,
    # so readline() already splits on \r → two reads: "progress\n" and "50%\n".
    # clean_carriage_return then applies replace(\r\n, \n) and replace(\r, \n)
    # (redundant here since text mode already translated \r).
    code = "print('progress\\r50%')"
    lines: list[StreamingLine] = list(stream_gpu_windows(
        py_exe=sys.executable,
        code=code,
        timeout=5,
        clean_carriage_return=True,
    ))
    assert len(lines) == 2, f"text mode splits on \\r → 2 lines, got {len(lines)}"
    assert "50%" in lines[1].text, f"second line should contain 50%, got {lines[1].text!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. clean_carriage_return=False does NOT skip empty-after-trim lines
# ═══════════════════════════════════════════════════════════════════════════════
def test_carriage_return_preserved_when_disabled():
    # Child outputs "\r\n" (print("\\r") → "\r\n").
    # text=True translates \r\n → \n, so readline returns "\n".
    # clean_carriage_return=True would skip this (rstrip("\n") → "" → continue).
    # clean_carriage_return=False yields it as-is.
    code = "print('\\r')"
    lines: list[StreamingLine] = list(stream_gpu_windows(
        py_exe=sys.executable,
        code=code,
        timeout=5,
        clean_carriage_return=False,
    ))
    assert len(lines) >= 1, (
        f"clean=False should yield the empty line, got {len(lines)} lines"
    )
    # The yielded line is "\n" (universal newline translation of \r\n)
    assert lines[0].text == "\n", f"expected '\\n', got {lines[0].text!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Invalid UTF-8 bytes → U+FFFD replacement char, no crash
# ═══════════════════════════════════════════════════════════════════════════════
def test_utf8_decode_errors_replaced():
    # \xff and \xfe are never valid UTF-8 start/continuation bytes.
    # The parent's Popen(encoding="utf-8", errors="replace") replaces each
    # invalid byte with U+FFFD.
    code = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe\\n'); sys.stdout.buffer.flush()"
    lines: list[StreamingLine] = list(stream_gpu_windows(
        py_exe=sys.executable,
        code=code,
        timeout=5,
    ))
    assert len(lines) >= 1, "should yield at least one line"
    assert "\ufffd" in lines[0].text, (
        f"expected U+FFFD replacement char in output, got: {lines[0].text!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. timeout kills the subprocess
# ═══════════════════════════════════════════════════════════════════════════════
def test_timeout_kills_process():
    with pytest.raises(subprocess.TimeoutExpired):
        for _ in stream_gpu_windows(
            py_exe=sys.executable,
            code="import time; time.sleep(5)",
            timeout=1,
        ):
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 6. returncode propagated via StopIteration.value
# ═══════════════════════════════════════════════════════════════════════════════
def test_returncode_propagated():
    gen = stream_gpu_windows(
        py_exe=sys.executable,
        code="import sys; sys.exit(42)",
        timeout=5,
    )
    try:
        while True:
            next(gen)
    except StopIteration as e:
        assert e.value == 42, f"expected exit code 42, got {e.value}"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. env dict passed to Popen includes all streaming-related vars
# ═══════════════════════════════════════════════════════════════════════════════
def test_env_includes_streaming_vars(monkeypatch):
    captured_env: dict[str, str] = {}
    original_popen = subprocess.Popen

    def mock_popen(cmd, *args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        # Forward to real Popen with a trivial command that exits immediately,
        # preserving the captured env and pipe setup so select.select works.
        return original_popen(
            [sys.executable, "-c", ""],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            bufsize=kwargs.get("bufsize", -1),
            text=kwargs.get("text", False),
            encoding=kwargs.get("encoding"),
            errors=kwargs.get("errors"),
            cwd=kwargs.get("cwd"),
            env=kwargs.get("env"),
        )

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    gen = stream_gpu_windows(
        py_exe=sys.executable,
        code="irrelevant",
        timeout=5,
    )
    # Exhaust generator to let the mock subprocess run
    for _ in gen:
        pass

    assert captured_env.get("PYTHONUNBUFFERED") == "1", (
        f"PYTHONUNBUFFERED missing or wrong: {captured_env.get('PYTHONUNBUFFERED')!r}"
    )
    assert captured_env.get("TQDM_DISABLE") is None, (
        f"TQDM_DISABLE should NOT be set (was: {captured_env.get('TQDM_DISABLE')!r})"
        " — tqdm's envwrap uses bool('False')==True, so we rely on tqdm's default disable=False"
    )
    assert captured_env.get("TTY_COMPATIBLE") == "1", (
        f"TTY_COMPATIBLE missing or wrong: {captured_env.get('TTY_COMPATIBLE')!r}"
    )
    assert captured_env.get("TTY_INTERACTIVE") == "0", (
        f"TTY_INTERACTIVE missing or wrong: {captured_env.get('TTY_INTERACTIVE')!r}"
    )
    assert captured_env.get("HF_HUB_DISABLE_PROGRESS_BARS") == "1", (
        f"HF_HUB_DISABLE_PROGRESS_BARS missing or wrong: {captured_env.get('HF_HUB_DISABLE_PROGRESS_BARS')!r}"
    )
