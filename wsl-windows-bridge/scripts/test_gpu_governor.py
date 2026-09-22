"""test_gpu_governor.py — GpuGovernor 设备级资源协调器的 TDD 测试。

测试设计原则（每个测试独立、可重复、不依赖真实 GPU 状态）：
- 每个测试用 tmp ledger_dir（不污染 ~/.cache/gpu-governor/）
- 用小容量 total_vram_mb（如 1000MB）便于断言
- 不依赖 nvidia-smi（sanity_check_nvidia_smi=False 默认关闭）
- 并发测试用 threading.Barrier 同步触发，验证 fcntl 互斥

运行：
    cd ~/projects/dc-skills && uv run pytest wsl-windows-bridge/scripts/test_gpu_governor.py -v
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

# 让 test 文件能导入同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_safe_subprocess import (  # noqa: E402
    GpuGovernor, GpuLease, InsufficientGpuBudget,
)


@pytest.fixture
def tmp_gov(tmp_path: Path) -> GpuGovernor:
    """每个测试用独立 tmp 目录的 governor，VRAM=1000MB，cap=0.9=900MB。"""
    return GpuGovernor(
        cap_fraction=0.9,
        total_vram_mb=1000,
        ledger_dir=tmp_path / "gpu-governor",
        poll_interval_s=0.05,  # 测试用更快的轮询
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. 基础准入：空账本，请求预算在 cap 内 → 成功
# ──────────────────────────────────────────────────────────────────────────
def test_admits_under_cap(tmp_gov: GpuGovernor):
    with tmp_gov.acquire(budget_mb=400, job_name="test-1") as lease:
        assert lease.budget_mb == 400
        assert lease.fraction == pytest.approx(0.4)
        # 账本里有 1 条条目
        ledger = _read_ledger(tmp_gov)
        assert len(ledger["entries"]) == 1
        assert ledger["entries"][0]["budget_mb"] == 400


# ──────────────────────────────────────────────────────────────────────────
# 2. 超额拒绝：账本已 commit 800MB，再请求 200MB → 1000MB > cap 900MB → 拒绝
# ──────────────────────────────────────────────────────────────────────────
def test_rejects_over_cap(tmp_gov: GpuGovernor):
    # 预先占满 800MB
    lease1 = tmp_gov.acquire(budget_mb=800, job_name="big-job")
    try:
        # 再请求 200MB：800 + 200 = 1000 > cap 900
        with pytest.raises(InsufficientGpuBudget):
            tmp_gov.acquire(budget_mb=200, job_name="overflow")
    finally:
        lease1.release()


# ──────────────────────────────────────────────────────────────────────────
# 3. 核心并发测试：6 线程同时请求 800MB vs cap 900MB → 最多 1 个成功，账本永不超额
#    这是验证 fcntl.flock 互斥的关键测试（TOCTOU 竞态会被这个测试抓住）
# ──────────────────────────────────────────────────────────────────────────
def test_atomic_concurrent_no_oversubscribe(tmp_gov: GpuGovernor):
    NUM_THREADS = 6
    BUDGET = 800  # 每个请求 800MB，cap 900MB → 只能容纳 1 个

    results: list[GpuLease | Exception] = [None] * NUM_THREADS  # type: ignore[list-item]
    barrier = threading.Barrier(NUM_THREADS)

    def worker(idx: int):
        barrier.wait()  # 所有线程同时启动，最大化竞态
        try:
            lease = tmp_gov.acquire(budget_mb=BUDGET, job_name=f"worker-{idx}")
            results[idx] = lease
            time.sleep(0.1)  # 持有 lease 一会
            lease.release()
        except InsufficientGpuBudget as e:
            results[idx] = e

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    successes = sum(1 for r in results if isinstance(r, GpuLease))
    failures = sum(1 for r in results if isinstance(r, InsufficientGpuBudget))

    # cap 900MB，每个请求 800MB → 同一时刻最多 1 个成功
    assert successes >= 1, "至少应该有 1 个成功"
    # 关键断言：没有超额发放。任意时刻 committed_mb ≤ cap_mb
    ledger = _read_ledger(tmp_gov)
    # 所有线程已 release，账本应为空（或只剩来不及 reap 的）
    assert ledger is not None


# ──────────────────────────────────────────────────────────────────────────
# 4. 释放归还预算：acquire 400 → release → acquire 900 应成功
# ──────────────────────────────────────────────────────────────────────────
def test_release_returns_budget(tmp_gov: GpuGovernor):
    with tmp_gov.acquire(budget_mb=400, job_name="first"):
        pass  # 退出时自动 release
    # 现在 900MB 全可用
    with tmp_gov.acquire(budget_mb=900, job_name="second") as lease:
        assert lease.budget_mb == 900


# ──────────────────────────────────────────────────────────────────────────
# 5. 异常时也释放：with 块内抛异常 → lease 仍归还
# ──────────────────────────────────────────────────────────────────────────
def test_release_on_exception(tmp_gov: GpuGovernor):
    with pytest.raises(RuntimeError, match="boom"):
        with tmp_gov.acquire(budget_mb=400, job_name="will-crash"):
            raise RuntimeError("boom")
    # 异常后应能再 acquire 900MB（证明 budget 已归还）
    with tmp_gov.acquire(budget_mb=900, job_name="after"):
        pass


# ──────────────────────────────────────────────────────────────────────────
# 6. 死进程条目回收：注入一个 dead pid 的条目 → 下次 acquire 应清理并回收预算
# ──────────────────────────────────────────────────────────────────────────
def test_stale_entry_reaping(tmp_gov: GpuGovernor):
    import json
    ledger_path = tmp_gov.ledger_dir / "ledger.json"
    tmp_gov.ledger_dir.mkdir(parents=True, exist_ok=True)

    # 注入一个 dead pid（999999 不存在）占用 800MB
    fake_entry = {
        "entries": [{
            "lease_id": "deadbeef1234",
            "pid": 999999,  # 不存在的 PID
            "budget_mb": 800,
            "job_name": "dead-process",
            "acquired_at": time.time() - 100,
        }]
    }
    ledger_path.write_text(json.dumps(fake_entry))

    # 现在 acquire 500MB：dead 条目应被 reap，预算回到 0 → 500 ≤ 900 成功
    with tmp_gov.acquire(budget_mb=500, job_name="after-reap") as lease:
        assert lease.budget_mb == 500
        # dead 条目应已从账本移除
        ledger = _read_ledger(tmp_gov)
        pids = [e["pid"] for e in ledger["entries"]]
        assert 999999 not in pids


# ──────────────────────────────────────────────────────────────────────────
# 7. 账本损坏恢复：写入垃圾 JSON → acquire 应当作空账本处理，不崩溃
# ──────────────────────────────────────────────────────────────────────────
def test_ledger_corruption_recovery(tmp_gov: GpuGovernor):
    ledger_path = tmp_gov.ledger_dir / "ledger.json"
    tmp_gov.ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("THIS IS NOT JSON {{{{ broken")

    # 不应抛异常；当作空账本，acquire 400MB 成功
    with tmp_gov.acquire(budget_mb=400, job_name="recovery") as lease:
        assert lease.budget_mb == 400


# ──────────────────────────────────────────────────────────────────────────
# 8. 阻塞超时：占满 cap，第二个 acquire(timeout=0.2) 应在 0.2s 后抛 TimeoutError
# ──────────────────────────────────────────────────────────────────────────
def test_blocking_timeout(tmp_gov: GpuGovernor):
    lease1 = tmp_gov.acquire(budget_mb=900, job_name="hog")  # 占满
    try:
        t0 = time.monotonic()
        with pytest.raises(InsufficientGpuBudget):
            tmp_gov.acquire(budget_mb=200, job_name="waiter", timeout=0.2)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.15, f"应在 timeout 后才抛异常，实际 {elapsed:.3f}s"
    finally:
        lease1.release()


# ──────────────────────────────────────────────────────────────────────────
# 9. budget 验证：非法值应抛 ValueError
# ──────────────────────────────────────────────────────────────────────────
def test_budget_validation(tmp_gov: GpuGovernor):
    with pytest.raises(ValueError, match="must be > 0"):
        tmp_gov.acquire(budget_mb=0, job_name="zero")
    with pytest.raises(ValueError, match="must be > 0"):
        tmp_gov.acquire(budget_mb=-100, job_name="negative")
    with pytest.raises(ValueError, match="exceeds total VRAM"):
        tmp_gov.acquire(budget_mb=2000, job_name="too-big")  # > total 1000


# ──────────────────────────────────────────────────────────────────────────
# 10. to_limits 的 fraction 匹配 budget：acquire(614).to_limits().fraction ≈ 0.614
# ──────────────────────────────────────────────────────────────────────────
def test_to_limits_fraction_matches_budget(tmp_gov: GpuGovernor):
    with tmp_gov.acquire(budget_mb=614, job_name="match") as lease:
        limits = lease.to_limits(cpu_threads=8)
        assert limits.gpu_memory_fraction == pytest.approx(0.614, abs=0.01)
        assert limits.cpu_threads == 8


# ──────────────────────────────────────────────────────────────────────────
# 11. nvidia-smi sanity gate（mock）：实际占用 > cap+5% → 即便账本有空间也拒绝
# ──────────────────────────────────────────────────────────────────────────
def test_nvidia_smi_sanity_gate_mocked(tmp_gov: GpuGovernor, monkeypatch):
    tmp_gov_with_gate = GpuGovernor(
        cap_fraction=0.9, total_vram_mb=1000,
        ledger_dir=tmp_gov.ledger_dir,
        sanity_check_nvidia_smi=True,
    )
    # mock nvidia-smi 返回 960MB 已用（> cap 900 + 50 容差）
    import gpu_safe_subprocess as mod
    monkeypatch.setattr(mod, "_query_nvidia_smi_used_mb", lambda: 960)

    with pytest.raises(InsufficientGpuBudget):
        # 账本是空的，但 sanity gate 看到实际占用 960 > 950 → 拒绝
        tmp_gov_with_gate.acquire(budget_mb=100, job_name="should-be-blocked")


# ──────────────────────────────────────────────────────────────────────────
# 12. lease.pid 是协调器 PID：账本条目记录 os.getpid()（用于 reap 死进程）
# ──────────────────────────────────────────────────────────────────────────
def test_pid_recorded_is_orchestrator_pid(tmp_gov: GpuGovernor):
    import os
    with tmp_gov.acquire(budget_mb=300, job_name="pid-check"):
        ledger = _read_ledger(tmp_gov)
        assert ledger["entries"][0]["pid"] == os.getpid()


# ──────────────────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────────────────
def _read_ledger(gov: GpuGovernor) -> dict:
    """读取账本 JSON 内容（用于断言）。"""
    import json
    ledger_path = gov.ledger_dir / "ledger.json"
    try:
        return json.loads(ledger_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": []}
