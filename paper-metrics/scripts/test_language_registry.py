#!/usr/bin/env python3
"""Tests for language_registry.py (issue #10, Phase 1).

Locks three properties of the data-driven language dispatch:
  1. For every case in paper-reader/scripts/lang_spec_cases.json (the frozen
     shared spec), detect_language() and adapter_for() agree: the detected code
     routes to the adapter whose language matches (zh -> ChineseAdapter,
     en / unknown -> EnglishAdapter fallback).
  2. A FakeAdapter injected under a fake language "xx" is the one
     compute_text_metrics() actually runs - proving the dispatch reads the
     registry entry, not a hardcoded if branch.
  3. The compute_text_metrics() dispatch source no longer contains a language
     literal comparison (`== "zh"` or `== 'zh'`, likewise en / unknown).

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_language_registry.py -q
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import text_metrics as tm  # noqa: E402
import language_registry as lr  # noqa: E402

#: Frozen shared language spec both paper-reader and paper-metrics assert against.
LANG_SPEC = (
    SCRIPTS_DIR.parent.parent / "paper-reader" / "scripts" / "lang_spec_cases.json"
)


# ---------------------------------------------------------------------------
# (1) registry dispatch == detect_language() on the frozen shared spec
# ---------------------------------------------------------------------------

def test_registry_dispatch_matches_detect_language_on_shared_spec():
    spec = json.loads(LANG_SPEC.read_text(encoding="utf-8"))
    cases = spec["cases"]
    assert cases, "the shared language spec must not be empty"
    for case in cases:
        detected = tm.detect_language(case["text"])
        expected = case["expected"]
        # detect_language stays pinned to the shared spec ...
        assert detected["language"] == expected["language"], case["id"]
        assert detected["cjk_chars"] == expected["cjk_chars"], case["id"]
        assert detected["ascii_alpha_tokens"] == expected["ascii_alpha_tokens"], case["id"]
        assert detected["cjk_ratio"] == pytest.approx(expected["cjk_ratio"], abs=1e-9), case["id"]
        # ... and the registry routes that code to the matching adapter:
        # "zh" -> ChineseAdapter, every other code ("en", "unknown") -> the
        # English fallback, exactly the pre-registry routing.
        adapter = lr.adapter_for(detected["language"])
        expected_code = "zh" if detected["language"] == "zh" else "en"
        assert adapter.code == expected_code, case["id"]


# ---------------------------------------------------------------------------
# (2) data-driven dispatch: an injected FakeAdapter under "xx" is what runs
# ---------------------------------------------------------------------------

class _CountingFakeAdapter:
    """Fake adapter for a fake language "xx" with a counting tokenizer."""

    code = "xx"

    def __init__(self) -> None:
        self.tokenize_calls = 0

    def tokenize(self, text: str) -> list[str]:
        self.tokenize_calls += 1
        return text.split()

    def compute(self, text: str, language: dict, bundle) -> dict:
        self.tokenize(text)
        return {"M-FAKE": {"language": language["language"], "adapter": "xx"}}


def test_compute_dispatches_to_injected_fake_adapter(monkeypatch):
    fake = _CountingFakeAdapter()
    lr.ADAPTERS["xx"] = fake
    try:
        # Force the detected code to "xx": the sentinel result below can only
        # appear if compute_text_metrics routes through the registry entry - no
        # `if language == "zh"` (or any other literal) branch can produce it.
        monkeypatch.setattr(
            tm,
            "detect_language",
            lambda text: {
                "language": "xx", "supported": True, "cjk_ratio": 0.0,
                "cjk_chars": 0, "ascii_alpha_tokens": 0, "reason": None,
            },
        )
        result = tm.compute_text_metrics("any text", None)
        assert result == {"M-FAKE": {"language": "xx", "adapter": "xx"}}
        assert fake.tokenize_calls >= 1
    finally:
        lr.ADAPTERS.pop("xx", None)


# ---------------------------------------------------------------------------
# (3) structural lock: no language-literal comparison in the dispatch path
# ---------------------------------------------------------------------------

def test_compute_dispatch_path_has_no_language_literal_comparison():
    src = inspect.getsource(tm.compute_text_metrics)
    # The dispatch must go through the registry lookup ...
    assert "adapter_for" in src
    # ... and must not re-introduce the issue #10 anti-pattern of a hardcoded
    # language-literal comparison (the `if language["language"] == "zh"` chain
    # this slice removes).  Every language code lives in
    # language_registry.ADAPTERS, never in a string comparison in this function;
    # the test targets exactly the comparison form so a comment mentioning a
    # language name would not (and should not) trip it.
    for literal in ("zh", "en", "unknown"):
        assert f'== "{literal}"' not in src, literal
        assert f"== '{literal}'" not in src, literal
