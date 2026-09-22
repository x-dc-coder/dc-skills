#!/usr/bin/env python3
"""TDD tests for lexicon_loader.py — the frozen I1 word-list contract.

Covers (positive):
  1. The default v1 directory loads: 8 files, release "1.1", all bundle slots filled.
  2. Every entry is normalized (strip + lower + collapse whitespace), unique, sorted.
  3. Every Lexicon.sha256 equals sha256 of the raw file bytes (third-party verifiable).
  4. hedge and booster are disjoint.
  5. connectors has exactly contrastive/causal/result, pairwise disjoint.
  6. Entry counts meet the INTERFACES.md section 2 minimums; suffixes are the exact 11.
  7. fingerprint() is stable, directory-independent and independently recomputable.
  8. to_manifest() follows the frozen shape, sorted by name.
  9. entries survive an independent re-normalization path (V2 recomputation).

Covers (negative, each must raise LexiconError):
  N1. missing "source"                       -> LexiconError
  N2. hedge/booster intersection non-empty   -> LexiconError
  N3. connectors missing the "causal" group  -> LexiconError
  N4. missing "entries" field                -> LexiconError
  N5. entry normalizing to the empty string  -> LexiconError
  N6. connectors with an unexpected 4th group -> LexiconError
  N7. non-existent lexicon directory         -> LexiconError
  N8. connector groups that overlap          -> LexiconError

Run:
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_lexicon_loader.py -v
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

import lexicon_loader as ll  # noqa: E402

LEX_DIR = ll.DEFAULT_DIR

# Reimplemented on purpose: an independent normalization path for verification.
_WS = re.compile(r"\s+")


def _independent_normalize(entry: str) -> str:
    return " ".join(entry.strip().lower().split())


@pytest.fixture(scope="module")
def bundle() -> ll.LexiconBundle:
    return ll.load_lexicons()


def _copy_and_mutate(tmp_path: Path, filename: str, mutate) -> Path:
    """Copy the shipped v1 dir to tmp_path and mutate one JSON file."""
    target = tmp_path / "v1"
    shutil.copytree(LEX_DIR, target)
    path = target / filename
    obj = json.loads(path.read_text(encoding="utf-8"))
    mutate(obj)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# 1. Loading
# ---------------------------------------------------------------------------

def test_default_dir_has_the_eight_files() -> None:
    names = sorted(p.name for p in LEX_DIR.glob("*.json"))
    assert names == [
        "academic_words.json",
        "booster.json",
        "connectors.json",
        "hedge.json",
        "nominalization_denylist.json",
        "nominalization_suffixes.json",
        "nominalization_verb_bases.json",
        "stopwords.json",
    ]


def test_load_default_bundle(bundle: ll.LexiconBundle) -> None:
    assert bundle.version == ll.LEXICON_VERSION == "1.1"
    assert bundle.directory == LEX_DIR
    for lx in (bundle.hedge, bundle.booster, bundle.nominalization_suffixes,
               bundle.nominalization_verb_bases, bundle.nominalization_denylist,
               bundle.academic_words, bundle.stopwords):
        assert lx.source.strip(), lx.name
    assert sorted(bundle.connectors) == ["causal", "contrastive", "result"]
    assert bundle.hedge.name == "hedge"
    assert bundle.connectors["contrastive"].name == "connectors/contrastive"
    # Release discipline: ONE release = all eight files share one version and it
    # equals LEXICON_VERSION (lexicon_loader enforces both).
    assert {lx.version for lx in bundle.all_lexicons()} == {"1.1"}
    assert bundle.version == ll.LEXICON_VERSION == "1.1"


def test_load_accepts_path_and_string(bundle: ll.LexiconBundle) -> None:
    assert ll.load_lexicons(str(LEX_DIR)).fingerprint() == bundle.fingerprint()


# ---------------------------------------------------------------------------
# 2. Normalization / 3. sha256
# ---------------------------------------------------------------------------

def test_entries_normalized_unique_sorted(bundle: ll.LexiconBundle) -> None:
    for lx in bundle.all_lexicons():
        entries = list(lx.entries)
        assert entries, lx.name
        assert entries == sorted(entries), lx.name
        assert len(entries) == len(set(entries)), lx.name
        for entry in entries:
            assert entry == _independent_normalize(entry), (lx.name, entry)
            assert entry != ""


def test_entries_match_independent_recomputation() -> None:
    """V2 path: re-read the raw JSON without the loader and re-derive the lists."""
    for filename, key in (("hedge.json", "entries"), ("booster.json", "entries"),
                          ("academic_words.json", "entries"), ("stopwords.json", "entries"),
                          ("nominalization_verb_bases.json", "entries"),
                          ("nominalization_denylist.json", "entries")):
        obj = json.loads((LEX_DIR / filename).read_text(encoding="utf-8"))
        expected = sorted({_independent_normalize(e) for e in obj[key]})
        got = ll.load_lexicons(LEX_DIR)
        name = filename[:-5]
        lx = next(x for x in got.all_lexicons() if x.name == name)
        assert list(lx.entries) == expected


def test_sha256_covers_raw_file_bytes(bundle: ll.LexiconBundle) -> None:
    for filename in sorted(p.name for p in LEX_DIR.glob("*.json")):
        digest = hashlib.sha256((LEX_DIR / filename).read_bytes()).hexdigest()
        stem = filename[:-5]
        matching = [lx for lx in bundle.all_lexicons()
                    if lx.name == stem or lx.name.startswith(stem + "/")]
        assert matching, filename
        assert all(lx.sha256 == digest for lx in matching), filename


# ---------------------------------------------------------------------------
# 4. hedge / booster disjointness
# ---------------------------------------------------------------------------

def test_hedge_booster_disjoint(bundle: ll.LexiconBundle) -> None:
    assert set(bundle.hedge.entries) & set(bundle.booster.entries) == set()
    assert bundle.hedge.entry_count() >= 40
    assert bundle.booster.entry_count() >= 40


# ---------------------------------------------------------------------------
# 5. connector groups
# ---------------------------------------------------------------------------

def test_connector_groups_pairwise_disjoint(bundle: ll.LexiconBundle) -> None:
    keys = sorted(bundle.connectors)
    assert keys == ["causal", "contrastive", "result"]
    for i, left in enumerate(keys):
        for right in keys[i + 1:]:
            assert set(bundle.connectors[left].entries) & set(bundle.connectors[right].entries) == set()
    for key in keys:
        assert bundle.connectors[key].entry_count() >= 15, key


def test_connector_total_equals_group_sum(bundle: ll.LexiconBundle) -> None:
    obj = json.loads((LEX_DIR / "connectors.json").read_text(encoding="utf-8"))
    total = sum(len(v) for v in obj["groups"].values())
    assert total == sum(lx.entry_count() for lx in bundle.connectors.values())


# ---------------------------------------------------------------------------
# 6. Minimum counts + suffix set
# ---------------------------------------------------------------------------

def test_minimum_entry_counts(bundle: ll.LexiconBundle) -> None:
    assert bundle.hedge.entry_count() >= 40
    assert bundle.booster.entry_count() >= 40
    assert bundle.nominalization_verb_bases.entry_count() >= 150
    assert bundle.nominalization_denylist.entry_count() >= 60
    assert bundle.academic_words.entry_count() >= 300
    assert bundle.stopwords.entry_count() >= 120
    assert set(bundle.academic_words.entries) >= {"analyse", "hypothesis", "implement", "whereas"}
    assert {"section", "station", "mention", "action", "position", "condition", "mission",
            "version", "question", "function"} <= set(bundle.nominalization_denylist.entries)


def test_suffixes_are_the_exact_frozen_set(bundle: ll.LexiconBundle) -> None:
    assert set(bundle.nominalization_suffixes.entries) == {
        "tion", "sion", "ment", "ness", "ity", "ance", "ence", "ancy", "ency", "ism", "ist",
    }


HIGH_AMBIGUITY_BARE_WORDS = {
    "contrastive": {"still", "while", "whilst", "yet"},
    "causal": {"as", "since", "through"},
    "result": {"so", "as such"},
}
MUST_KEEP_PHRASES = {
    "as a result of", "as a consequence of", "as a result", "as a consequence",
    "so that", "because of", "due to", "owing to", "on account of", "thanks to",
    "by virtue of", "given that",
}


def test_connectors_exclude_high_ambiguity_bare_words(bundle: ll.LexiconBundle) -> None:
    """Regression for the glm-5.3 finding: bare 'as' made causal = as-frequency."""
    for group, removed in HIGH_AMBIGUITY_BARE_WORDS.items():
        entries = set(bundle.connectors[group].entries)
        assert entries & removed == set(), (group, entries & removed)
    all_entries = {e for lx in bundle.connectors.values() for e in lx.entries}
    assert "as" not in all_entries and "so" not in all_entries
    assert "still" not in all_entries and "while" not in all_entries


def test_connectors_keep_the_unambiguous_multiword_phrases(bundle: ll.LexiconBundle) -> None:
    all_entries = {e for lx in bundle.connectors.values() for e in lx.entries}
    assert MUST_KEEP_PHRASES <= all_entries
    # every retained entry is either multiword or a single word with one dominant
    # connectives reading; no retained entry contains 'such as'
    assert all("such as" not in e for e in all_entries)


def test_connectors_declare_revision_and_curation_notes(bundle: ll.LexiconBundle) -> None:
    obj = json.loads((LEX_DIR / "connectors.json").read_text(encoding="utf-8"))
    assert obj["version"] == "1.1"
    assert obj["version"] != "1.0", "connectors content changed -> version must be bumped"
    notes = obj.get("notes", "")
    assert "1.1" in notes and "as a result of" in notes
    for group, removed in HIGH_AMBIGUITY_BARE_WORDS.items():
        for word in removed:
            assert word in notes, word


def test_connectors_carry_a_machine_readable_change_ledger(bundle: ll.LexiconBundle) -> None:
    obj = json.loads((LEX_DIR / "connectors.json").read_text(encoding="utf-8"))
    change = obj["change"]
    assert change["from_version"] == "1.0" and change["to_version"] == "1.1"
    assert change["removed"] == {g: sorted(v) for g, v in HIGH_AMBIGUITY_BARE_WORDS.items()}
    assert change["entry_count_before"] == {"contrastive": 25, "causal": 21, "result": 20}
    assert change["entry_count_after"] == {
        g: bundle.connectors[g].entry_count() for g in ("contrastive", "causal", "result")}
    assert change["entry_count_after"] == {"contrastive": 21, "causal": 18, "result": 18}


def test_mixed_release_versions_raise(tmp_path: Path) -> None:
    """Release discipline: a half-bumped release must not load."""
    def mutate(obj: dict) -> None:
        obj["version"] = "1.0"

    with pytest.raises(ll.LexiconError, match="inconsistent versions"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "hedge.json", mutate))


def test_wrong_release_version_raises(tmp_path: Path) -> None:
    """A uniformly wrong release version (not LEXICON_VERSION) must not load."""
    target = tmp_path / "v1"
    shutil.copytree(LEX_DIR, target)
    for path in sorted(target.glob("*.json")):
        obj = json.loads(path.read_text(encoding="utf-8"))
        obj["version"] = "9.9"
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ll.LexiconError, match="LEXICON_VERSION"):
        ll.load_lexicons(target)


def test_sources_cite_the_required_literature(bundle: ll.LexiconBundle) -> None:
    assert "Hyland" in bundle.hedge.source and "Hyland" in bundle.booster.source
    assert "1998" in bundle.hedge.source and "2005" in bundle.booster.source
    assert "PDTB" in bundle.connectors["contrastive"].source
    assert "Coxhead" in bundle.academic_words.source and "2000" in bundle.academic_words.source


# ---------------------------------------------------------------------------
# 7. fingerprint / 8. manifest
# ---------------------------------------------------------------------------

def test_fingerprint_is_stable_and_recomputable(bundle: ll.LexiconBundle) -> None:
    again = ll.load_lexicons(LEX_DIR)
    assert bundle.fingerprint() == again.fingerprint()
    pairs = sorted((m["name"], m["sha256"]) for m in bundle.to_manifest())
    payload = "".join("%s\t%s\n" % (n, d) for n, d in pairs)
    assert bundle.fingerprint() == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert len(bundle.fingerprint()) == 64


def test_fingerprint_is_directory_independent(bundle: ll.LexiconBundle, tmp_path: Path) -> None:
    clone = tmp_path / "elsewhere"
    shutil.copytree(LEX_DIR, clone)
    other = ll.load_lexicons(clone)
    assert other.directory != bundle.directory
    assert other.fingerprint() == bundle.fingerprint()


def test_manifest_shape(bundle: ll.LexiconBundle) -> None:
    manifest = bundle.to_manifest()
    assert [m["name"] for m in manifest] == sorted(m["name"] for m in manifest)
    assert len(manifest) == 10  # 8 files -> 7 plain + 3 connector groups
    for row in manifest:
        assert set(row) == {"name", "version", "source", "sha256", "entry_count"}
        assert row["version"] == "1.1"
        assert len(row["sha256"]) == 64
        assert row["entry_count"] > 0
    by_name = {m["name"]: m for m in manifest}
    assert by_name["hedge"]["entry_count"] == bundle.hedge.entry_count()
    assert by_name["connectors/result"]["entry_count"] == bundle.connectors["result"].entry_count()


def test_json_round_trips_through_json_dumps(bundle: ll.LexiconBundle) -> None:
    assert json.loads(json.dumps(bundle.to_manifest(), sort_keys=True)) == \
        json.loads(json.dumps(bundle.to_manifest(), sort_keys=True))


# ---------------------------------------------------------------------------
# Negative cases (all must raise LexiconError)
# ---------------------------------------------------------------------------

def test_missing_source_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj.pop("source")

    with pytest.raises(ll.LexiconError, match="source"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "hedge.json", mutate))


def test_empty_source_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["source"] = "   "

    with pytest.raises(ll.LexiconError, match="source"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "booster.json", mutate))


def test_hedge_booster_overlap_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["entries"] = sorted(set(obj["entries"]) | {"perhaps"})

    with pytest.raises(ll.LexiconError, match="disjoint"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "booster.json", mutate))


def test_hedge_booster_overlap_is_case_insensitive(tmp_path: Path) -> None:
    """An overlap smuggled in as cased/whitespace noise must still be caught."""
    def mutate(obj: dict) -> None:
        obj["entries"] = sorted(set(obj["entries"]) | {"  PERHAPS  "})

    with pytest.raises(ll.LexiconError, match="disjoint"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "booster.json", mutate))


def test_connectors_missing_group_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["groups"].pop("causal")

    with pytest.raises(ll.LexiconError, match="groups"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "connectors.json", mutate))


def test_connectors_unexpected_group_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["groups"]["temporal"] = ["then", "meanwhile"]

    with pytest.raises(ll.LexiconError, match="groups"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "connectors.json", mutate))


def test_connector_groups_overlap_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["groups"]["result"] = sorted(set(obj["groups"]["result"]) | {"however"})

    with pytest.raises(ll.LexiconError, match="disjoint"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "connectors.json", mutate))


def test_missing_entries_field_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj.pop("entries")

    with pytest.raises(ll.LexiconError, match="entries"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "stopwords.json", mutate))


def test_empty_entry_raises(tmp_path: Path) -> None:
    def mutate(obj: dict) -> None:
        obj["entries"] = sorted(set(obj["entries"]) | {"   "})

    with pytest.raises(ll.LexiconError, match="empty"):
        ll.load_lexicons(_copy_and_mutate(tmp_path, "academic_words.json", mutate))


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ll.LexiconError, match="does not exist"):
        ll.load_lexicons(tmp_path / "nope")


def test_missing_file_raises(tmp_path: Path) -> None:
    clone = tmp_path / "v1"
    shutil.copytree(LEX_DIR, clone)
    (clone / "stopwords.json").unlink()
    with pytest.raises(ll.LexiconError, match="missing required files"):
        ll.load_lexicons(clone)


def test_duplicate_entries_are_reported_as_deduped(tmp_path: Path) -> None:
    """Duplicates are normalized away (the frozen entries contract), not an error."""
    def mutate(obj: dict) -> None:
        obj["entries"] = sorted(obj["entries"] + ["HOWEVER", "  however  "])

    loaded = ll.load_lexicons(_copy_and_mutate(tmp_path, "stopwords.json", mutate))
    names = [e for e in loaded.stopwords.entries if e == "however"]
    assert names == ["however"]


def test_normalize_entry_contract() -> None:
    assert ll.normalize_entry("  In   General \n") == "in general"
    assert ll.normalize_entry("MIGHT") == "might"
