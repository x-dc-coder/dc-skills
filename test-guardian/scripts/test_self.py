"""test_self.py — test-guardian 自身质检与规则验证自测用例。

确保防假绿规则检查器自身功能健全、无误报、无漏报。
"""
import sys
import tempfile
from pathlib import Path

# Add scripts directory to sys.path
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from check_test_hygiene import check_file, Finding

def _scan(code: str, filename: str = "test_sample_domain_feature.py", own="app") -> list[Finding]:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / filename
        p.write_text(code, encoding="utf-8")
        return check_file(p, own_packages={own}, rules={"R1", "R2", "R3", "R4", "R5", "R6", "R7", "naming"})

def test_valid_test_passes_cleanly():
    code = """
def test_user_login_with_correct_password_returns_token():
    resp = client.post("/login", json={"user": "a", "pwd": "b"})
    assert resp.status_code == 200
    assert resp.json()["token"] == "jwt-token"
"""
    findings = _scan(code)
    assert len(findings) == 0, f"Expected 0 findings, got: {[str(f) for f in findings]}"

def test_r1_blocks_patching_internal_sut():
    code = """
from unittest.mock import patch
@patch("app.service._internal_calc_tax")
def test_calc(mock_tax):
    assert 1 == 1
"""
    findings = _scan(code)
    assert any(f.rule == "R1" for f in findings), "R1 should catch patch on SUT"

def test_r2_catches_bare_status_code():
    code = """
def test_get_todos():
    r = client.get("/todos")
    assert r.status_code == 200
"""
    findings = _scan(code)
    assert any(f.rule == "R2" for f in findings), "R2 should catch bare status_code"

def test_r4_catches_broad_exception_raises():
    code = """
import pytest
def test_bad_raises():
    with pytest.raises(Exception):
        raise ValueError("oops")
"""
    findings = _scan(code)
    assert any(f.rule == "R4" for f in findings), "R4 should catch bare pytest.raises(Exception)"

def test_r6_catches_naked_sleep():
    code = """
import time
def test_async_wait():
    time.sleep(2)
    assert 1 == 1
"""
    findings = _scan(code)
    assert any(f.rule == "R6" for f in findings), "R6 should catch time.sleep"

def test_r7_catches_zero_assert_and_assert_true():
    code1 = """
def test_no_asserts():
    x = 1 + 1
"""
    code2 = """
def test_assert_true():
    assert True
"""
    f1 = _scan(code1)
    f2 = _scan(code2)
    assert any(f.rule == "R7" for f in f1), "R7 should catch zero assertion"
    assert any(f.rule == "R7" for f in f2), "R7 should catch assert True"

def test_naming_catches_milestone_filename():
    code = "def test_ok(): assert 1 == 1"
    findings = _scan(code, filename="test_m1_stage.py")
    assert any(f.rule == "naming" for f in findings), "naming should catch test_m1_stage.py"