#!/usr/bin/env python3
"""Tests for unified_search.py — the 7-source search aggregator.

Focus areas (the logic most likely to break silently):
  1. keenable YAML parsing — the source writes a *wrapper* dict
     ({query, mode, results:[...]}) and older releases appended a trailing
     "Update: ..." notice that breaks plain yaml.safe_load.  A mis-parse here
     makes the source return an empty placeholder instead of real results
     while still reporting success (this actually shipped broken for months).
  2. published-date normalization — YAML yields datetime objects, which are not
     JSON-serializable; the output contract uses `published_date` (ISO string).
  3. dedup_and_rank — URL-normalized dedup, cross-source agreement bonus,
     error filtering, top_k truncation.
  4. url_agreement — Jaccard over hosts; empty sets must yield 0.0, not crash.
  5. quota bookkeeping — monthly counters gate metered sources.

Run:
    cd ~/projects/dc-skills && uv run pytest unified-search/scripts/test_unified_search.py -v
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import unified_search as us  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg() -> dict:
    """Minimal config shaped like unified-search/config.json."""
    return {
        "sources": {
            "keenable": {"command": "keenable", "timeout_sec": 30, "weight": 1.0},
            "tavily": {"timeout_sec": 30, "weight": 1.0},
            "bocha": {"timeout_sec": 30, "weight": 1.0},
            "firecrawl": {"timeout_sec": 30, "weight": 1.0},
        },
        "api_keys": {},
        "quota": {"tavily": {"monthly_limit": 1000}, "firecrawl": {"monthly_limit": 1000}},
        "modes": {"general": {"arbitrator": "firecrawl", "agreement_threshold": 0.3}},
    }


@pytest.fixture
def isolated_quota(tmp_path, monkeypatch):
    """Point the quota file at a temp path so tests never touch real usage data."""
    path = tmp_path / "quota.json"
    monkeypatch.setattr(us, "QUOTA_PATH", path)
    return path


def _run_keenable(stdout: str, returncode: int = 0):
    """Patch subprocess.run for search_keenable only."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return patch.object(us.subprocess, "run", return_value=proc)


#: Canonical keenable stdout: a YAML *wrapper* mapping with a `results` list.
KEENABLE_WRAPPER = """query: quantum computing
mode: pro
results:
- title: First result
  url: https://example.com/a
  snippet: about quantum
- title: Second result
  url: https://example.com/b
  snippet: more
  published_at: 2026-05-15T00:00:00Z
"""


# ══ 1. keenable YAML parsing ════════════════════════════════════════════════

class TestKeenableYamlParsing:
    """The wrapper dict is the core contract; a regression here silently kills
    the source while still reporting success."""

    WRAPPER = KEENABLE_WRAPPER

    def test_wrapper_dict_is_unwrapped(self):
        """The parsed YAML is a dict; we must read its `results` list."""
        parsed = us._load_keenable_yaml(self.WRAPPER)
        assert isinstance(parsed, dict), "keenable emits a wrapper mapping, not a bare list"
        assert "results" in parsed

    def test_search_keenable_returns_real_results(self, cfg):
        with _run_keenable(self.WRAPPER):
            res = us.search_keenable("quantum computing", cfg)
        assert len(res) == 2
        assert res[0]["title"] == "First result"
        assert res[0]["url"] == "https://example.com/a"
        assert res[0]["source"] == "keenable"
        assert res[0]["error"] if "error" in res[0] else True

    def test_bare_list_still_accepted(self, cfg):
        """Forward compatibility: tolerate a bare list too."""
        bare = "- title: Only\n  url: https://example.com/only\n"
        with _run_keenable(bare):
            res = us.search_keenable("q", cfg)
        assert len(res) == 1
        assert res[0]["title"] == "Only"

    def test_trailing_update_notice_is_tolerated(self):
        """0.1.16 appended an 'Update: ...' notice that broke plain safe_load."""
        noisy = self.WRAPPER + (
            "\nUpdate: A newer version of keenable (0.2.3) is available. Run:\n"
            "  curl --proto '=https' -LsSf https://example.com/install.sh | sh\n"
        )
        parsed = us._load_keenable_yaml(noisy)
        assert isinstance(parsed, dict)
        assert len(parsed["results"]) == 2

    def test_empty_entries_are_filtered(self, cfg):
        """A result with neither title nor url must not become a phantom row."""
        payload = (
            "query: q\nmode: pro\nresults:\n"
            "- title: Good\n  url: https://example.com/good\n"
            "- title: ''\n  url: ''\n"
        )
        with _run_keenable(payload):
            res = us.search_keenable("q", cfg)
        assert len(res) == 1
        assert res[0]["url"] == "https://example.com/good"

    def test_empty_stdout_returns_no_results(self, cfg):
        with _run_keenable(""):
            assert us.search_keenable("q", cfg) == []

    def test_non_dict_entries_are_skipped(self, cfg):
        payload = "query: q\nmode: pro\nresults:\n- just-a-string\n- title: T\n  url: https://e.com/t\n"
        with _run_keenable(payload):
            res = us.search_keenable("q", cfg)
        assert len(res) == 1 and res[0]["title"] == "T"

    def test_subprocess_failure_returns_error_result(self, cfg):
        with patch.object(us.subprocess, "run", side_effect=FileNotFoundError("no keenable")):
            res = us.search_keenable("q", cfg)
        assert len(res) == 1
        assert res[0]["error"] == "no keenable"


# ══ 2. published-date normalization ═════════════════════════════════════════

class TestIsoDate:
    """YAML turns timestamps into datetime objects; the output contract needs
    ISO strings or json.dumps(result) blows up."""

    def test_datetime_to_iso(self):
        dt = datetime.datetime(2026, 9, 4, 13, 14, 28)
        assert us._iso_date(dt) == "2026-09-04T13:14:28"

    def test_date_to_iso(self):
        assert us._iso_date(datetime.date(2026, 9, 4)) == "2026-09-04"

    def test_none_passthrough(self):
        assert us._iso_date(None) is None

    def test_string_passthrough(self):
        assert us._iso_date("2026-09-04T13:14:28Z") == "2026-09-04T13:14:28Z"

    def test_keenable_result_is_json_serializable(self, cfg):
        """Regression: datetime leaked into the result → TypeError on output."""
        payload = (
            "query: q\nmode: pro\nresults:\n"
            "- title: Dated\n  url: https://example.com/d\n"
            "  published_at: 2026-05-15 00:00:00+00:00\n"
        )
        with _run_keenable(payload):
            res = us.search_keenable("q", cfg)
        assert res[0]["published_date"].startswith("2026-05-15")
        json.dumps(res)  # must not raise

    def test_missing_published_date_is_none(self, cfg):
        with _run_keenable(KEENABLE_WRAPPER):
            res = us.search_keenable("q", cfg)
        assert res[0]["published_date"] is None


# ══ 3. dedup_and_rank ═══════════════════════════════════════════════════════

class TestDedupAndRank:
    def test_dedup_by_normalized_url(self, cfg):
        """www. / trailing-slash / case differences collapse; the scheme is
        *kept* (http vs https stay distinct keys — documented behavior)."""
        per_source = {
            "keenable": [us.make_result("A", "https://www.Example.com/x/", "", "keenable", 0.6)],
            "bocha": [us.make_result("A dup", "https://example.com/x", "", "bocha", 0.7)],
        }
        out = us.dedup_and_rank(per_source, cfg)
        assert len(out) == 1

    def test_scheme_is_not_collapsed(self, cfg):
        """Guard the documented boundary: http and https are distinct resources."""
        per_source = {
            "keenable": [us.make_result("A", "https://example.com/x", "", "keenable", 0.6)],
            "bocha": [us.make_result("B", "http://example.com/x", "", "bocha", 0.6)],
        }
        assert len(us.dedup_and_rank(per_source, cfg)) == 2

    def test_cross_source_agreement_bonus(self, cfg):
        url = "https://example.com/shared"
        per_source = {
            "keenable": [us.make_result("A", url, "", "keenable", 0.6)],
            "bocha": [us.make_result("A", url, "", "bocha", 0.6)],
        }
        out = us.dedup_and_rank(per_source, cfg)
        assert len(out) == 1
        # 0.6*weight(1.0) + 0.15*weight(1.0) bonus
        assert out[0]["score"] == pytest.approx(0.75)
        assert "bocha" in out[0]["also_from"]

    def test_error_results_are_filtered(self, cfg):
        per_source = {
            "tavily": [us.make_result("", "", "[boom]", "tavily", 0.0, error="boom")],
            "bocha": [us.make_result("ok", "https://example.com/ok", "", "bocha", 0.7)],
        }
        out = us.dedup_and_rank(per_source, cfg)
        assert len(out) == 1 and out[0]["url"] == "https://example.com/ok"

    def test_sorted_desc_and_truncated(self, cfg):
        per_source = {
            "bocha": [us.make_result(f"t{i}", f"https://example.com/{i}", "", "bocha", 0.1 * i)
                      for i in range(1, 10)]
        }
        out = us.dedup_and_rank(per_source, cfg, top_k=3)
        assert len(out) == 3
        assert out[0]["score"] >= out[1]["score"] >= out[2]["score"]

    def test_empty_input(self, cfg):
        assert us.dedup_and_rank({}, cfg) == []

    def test_weight_scales_score(self, cfg):
        cfg["sources"]["keenable"]["weight"] = 2.0
        per_source = {"keenable": [us.make_result("A", "https://e.com/a", "", "keenable", 0.5)]}
        out = us.dedup_and_rank(per_source, cfg)
        assert out[0]["score"] == pytest.approx(1.0)


# ══ 4. url_agreement ════════════════════════════════════════════════════════

class TestUrlAgreement:
    def test_identical_hosts(self):
        a = [us.make_result("x", "https://a.com/1", "", "a", 0.5)]
        b = [us.make_result("y", "https://a.com/2", "", "b", 0.5)]
        assert us.url_agreement(a, b) == 1.0

    def test_disjoint_hosts(self):
        a = [us.make_result("x", "https://a.com/1", "", "a", 0.5)]
        b = [us.make_result("y", "https://b.com/1", "", "b", 0.5)]
        assert us.url_agreement(a, b) == 0.0

    def test_empty_both_is_zero_not_crash(self):
        """Regression: an all-empty source (the keenable phantom) made this 0.0
        which then always tripped arbitration."""
        assert us.url_agreement([], []) == 0.0

    def test_partial_overlap_is_jaccard(self):
        a = [us.make_result("x", "https://a.com/1", "", "a", 0.5),
             us.make_result("y", "https://b.com/1", "", "a", 0.5)]
        b = [us.make_result("x", "https://a.com/2", "", "b", 0.5)]
        assert us.url_agreement(a, b) == pytest.approx(1 / 2)


# ══ 5. normalize_url ════════════════════════════════════════════════════════

class TestNormalizeUrl:
    @pytest.mark.parametrize("raw,expected", [
        ("https://www.example.com/a/", "https://example.com/a"),
        ("HTTP://Example.COM/A", "http://example.com/A"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://example.com/", "https://example.com"),
        ("", ""),
    ])
    def test_normalization(self, raw, expected):
        assert us.normalize_url(raw) == expected

    def test_whitespace_trimmed(self):
        assert us.normalize_url("  https://example.com/a  ") == "https://example.com/a"


# ══ 6. Quota bookkeeping ════════════════════════════════════════════════════

class TestQuota:
    def test_unmetered_source_is_always_available(self, cfg, isolated_quota):
        assert us.quota_available(cfg, "keenable") is True

    def test_available_when_under_limit(self, cfg, isolated_quota):
        us.quota_consume("tavily", 10)
        assert us.quota_available(cfg, "tavily") is True
        assert us.quota_remaining(cfg, "tavily") == 990

    def test_exhausted_at_limit(self, cfg, isolated_quota):
        us.quota_consume("tavily", 1000)
        assert us.quota_available(cfg, "tavily") is False
        assert us.quota_remaining(cfg, "tavily") == 0

    def test_remaining_never_negative(self, cfg, isolated_quota):
        us.quota_consume("tavily", 1500)
        assert us.quota_remaining(cfg, "tavily") == 0

    def test_month_key_format(self):
        key = us.current_month_key()
        assert len(key) == 7 and key[4] == "-"

    def test_counter_is_per_source_and_month(self, cfg, isolated_quota):
        us.quota_consume("tavily", 3)
        us.quota_consume("firecrawl", 5)
        data = json.loads(isolated_quota.read_text(encoding="utf-8"))
        month = us.current_month_key()
        assert data["tavily"][month] == 3
        assert data["firecrawl"][month] == 5


# ══ 7. make_result schema ═══════════════════════════════════════════════════

class TestMakeResult:
    def test_required_fields(self):
        r = us.make_result("T", "https://e.com", "snip", "bocha", 0.7)
        assert set(r) >= {"title", "url", "snippet", "source", "score"}
        assert isinstance(r["score"], float)

    def test_extra_kwargs_merged(self):
        r = us.make_result("T", "u", "", "s", 0.5, published_date="2026-01-01", domain="e.com")
        assert r["published_date"] == "2026-01-01"
        assert r["domain"] == "e.com"

    def test_result_is_json_serializable(self):
        json.dumps(us.make_result("T", "u", "s", "src", 0.5))


# ══ 8. search_with_retry ════════════════════════════════════════════════════

class TestSearchWithRetry:
    def test_returns_first_success(self, cfg):
        fn = MagicMock(return_value=[us.make_result("ok", "https://e.com", "", "x", 0.5)])
        out = us.search_with_retry(fn, "q", cfg, retries=2, base_delay=0)
        assert len(out) == 1 and out[0]["title"] == "ok"
        assert fn.call_count == 1

    def test_error_results_retry_then_return_last_result(self, cfg):
        """All attempts return error rows → the last real result is returned
        as-is (the 'retry exhausted' marker is reserved for exceptions)."""
        fn = MagicMock(return_value=[us.make_result("", "", "[err]", "x", 0.0, error="err")])
        out = us.search_with_retry(fn, "q", cfg, retries=2, base_delay=0)
        assert fn.call_count == 3
        assert out[0]["error"] == "err"
        assert out[0]["snippet"] == "[err]"

    def test_exception_is_retried_then_reported(self, cfg):
        fn = MagicMock(side_effect=RuntimeError("kaboom"))
        out = us.search_with_retry(fn, "q", cfg, retries=1, base_delay=0)
        assert fn.call_count == 2
        assert out[0]["error"] == "kaboom"


# ══ 9. run_sources_parallel ═════════════════════════════════════════════════

class TestRunSourcesParallel:
    def test_collects_all_sources(self, cfg):
        fns = {
            "a": lambda q, c: [us.make_result("A", "https://a.com", "", "a", 0.5)],
            "b": lambda q, c: [us.make_result("B", "https://b.com", "", "b", 0.5)],
        }
        out = us.run_sources_parallel(fns, "q", cfg)
        assert set(out) == {"a", "b"}

    def test_source_exception_becomes_error_result(self, cfg):
        def boom(q, c):
            raise RuntimeError("explode")

        out = us.run_sources_parallel({"bad": boom}, "q", cfg)
        assert out["bad"][0]["error"] == "explode"


# ── Ai4Scholar（新增学术源）──────────────────────────────────────────────────

class TestAi4ScholarParsing:
    def test_parse_payload_maps_fields(self):
        payload = {"total": 1, "data": [{
            "paperId": "abc123", "title": "RAG", "abstract": "x" * 600,
            "year": 2020, "venue": "NeurIPS", "citationCount": 18104,
            "authors": [{"name": "P. Lewis"}, {"name": "E. Perez"}],
            "externalIds": {"DOI": "10.1/x", "ArXiv": "2005.11401"},
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2005.11401"},
            "url": "https://www.semanticscholar.org/paper/abc123",
        }]}
        res = us._parse_ai4scholar_payload(payload, credits_left="141")
        assert len(res) == 1
        r = res[0]
        assert r["source"] == "ai4scholar" and r["paper_id"] == "abc123"
        assert r["arxiv_id"] == "2005.11401" and r["doi"] == "10.1/x"
        assert r["pdf_url"].endswith("2005.11401") and r["citation_count"] == 18104
        assert len(r["snippet"]) == 500 and r["credits_remaining"] == "141"

    def test_missing_key_returns_error_entry(self, monkeypatch):
        monkeypatch.delenv("AI4SCHOLAR_API_KEY", raising=False)
        cfg = {"sources": {"ai4scholar": {}}, "api_keys": {"ai4scholar": {"env_var": "AI4SCHOLAR_API_KEY"}}}
        res = us.search_ai4scholar("x", cfg, max_results=1)
        assert res and res[0]["error"] == "no_key"


class TestCrossSourceDedup:
    def test_dedup_key_prefers_arxiv_id_over_url(self):
        a = us.make_result("t", "https://arxiv.org/abs/2005.11401", source="arxiv", arxiv_id="2005.11401")
        b = us.make_result("t", "https://www.semanticscholar.org/paper/abc", source="ai4scholar", arxiv_id="2005.11401")
        assert us._dedup_key(a) == us._dedup_key(b) == "arxiv:2005.11401"

    def test_dedup_key_prefers_doi(self):
        r = us.make_result("t", "https://x/y", source="openalex", doi="https://doi.org/10.1/A")
        assert us._dedup_key(r) == "doi:10.1/a"

    def test_cross_source_merge_bonus(self):
        per = {"arxiv": [us.make_result("t", "https://arxiv.org/abs/1", source="arxiv", arxiv_id="1", score=0.5)],
               "ai4scholar": [us.make_result("t", "https://s2/paper/x", source="ai4scholar", arxiv_id="1", score=0.5)]}
        cfg = {"sources": {"arxiv": {"weight": 1.0}, "ai4scholar": {"weight": 1.0}}}
        merged = us.dedup_and_rank(per, cfg, top_k=5)
        assert len(merged) == 1
        assert set(merged[0]["also_from"]) == {"arxiv", "ai4scholar"}
        assert merged[0]["score"] > 0.5
