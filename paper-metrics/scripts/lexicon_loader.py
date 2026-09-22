#!/usr/bin/env python3
"""Frozen lexicon loader for the paper-metrics writing-feature metrics (I1).

Loads the versioned, citation-backed word lists in
paper-metrics/data/lexicons/v1/ and exposes them as immutable
Lexicon / LexiconBundle objects with per-file sha256 and a bundle-wide
fingerprint.

Contract (frozen, INTERFACES.md section 2)
------------------------------------------
    LEXICON_VERSION = "1.1"
    DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "v1"

    class LexiconError(Exception): ...
    @dataclass(frozen=True) class Lexicon:  name / version / source / sha256 / entries
    @dataclass(frozen=True) class LexiconBundle: version / directory / hedge / booster /
        connectors / nominalization_suffixes / nominalization_verb_bases /
        nominalization_denylist / academic_words / stopwords
        fingerprint() / to_manifest()
    load_lexicons(directory=None) -> LexiconBundle

Files (8)
---------
hedge.json, booster.json, connectors.json (three groups), nominalization_suffixes.json,
nominalization_verb_bases.json, nominalization_denylist.json, academic_words.json,
stopwords.json.

Guarantees
----------
- Entries are normalized (strip -> lower -> collapse internal whitespace),
  de-duplicated and sorted(); the loader never emits duplicate or empty entries.
- Lexicon.sha256 is the sha256 of the RAW FILE BYTES (so a third party can
  verify a shipped list with sha256sum hedge.json).
- Load-time validation raises LexiconError when a file lacks
  name/version/source/entries (or groups for connectors), when a source is
  empty, when an entry normalizes to the empty string, when hedge/booster
  intersect, or when the connector groups are not exactly
  {contrastive, causal, result} and pairwise disjoint.
- Versioning (release discipline): one lexicon release = all eight files carry
  the SAME version, which must equal LEXICON_VERSION. A mismatch raises
  LexiconError, so a half-bumped release cannot ship. LexiconBundle.version and
  every Lexicon.version come from the files (and equal LEXICON_VERSION); each
  file's version is also reported in to_manifest().
- fingerprint() is a sha256 over every (name, sha256) pair sorted by name,
  joined as "<name>\\t<sha256>" with "\\n" and a trailing newline. It is stable
  across runs, machines and directory locations: it depends only on the shipped
  bytes.
- Pure stdlib, no LLM calls (OBSERVED-layer red line).

Usage
-----
    cd ~/projects/dc-skills && uv run pytest paper-metrics/scripts/test_lexicon_loader.py -v
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LEXICON_VERSION",
    "DEFAULT_DIR",
    "LEXICON_DIRS",
    "LEXICON_VERSIONS",
    "SUPPORTED_LEXICON_LANGUAGES",
    "lexicon_dir_for",
    "CONNECTOR_GROUPS",
    "CONNECTOR_GROUPS_ZH",
    "LexiconError",
    "Lexicon",
    "LexiconBundle",
    "normalize_entry",
    "load_lexicons",
]

LEXICON_VERSION = "1.1"
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "v1"

#: Lexicon set per language. English keeps the frozen v1 release; Chinese ships
#: its own curated release (there is no public, redistributable Hyland
#: equivalent for Chinese - see issue #11 group B), so the release version is
#: per-language and validated against this table.
LEXICON_DIRS: dict[str, str] = {"en": "v1", "zh": "v2-zh"}
LEXICON_VERSIONS: dict[str, str] = {"en": "1.1", "zh": "2.2-zh"}
SUPPORTED_LEXICON_LANGUAGES: tuple[str, ...] = ("en", "zh")


def lexicon_dir_for(language: str) -> Path:
    """Directory holding the lexicon release of `language`."""
    if language not in LEXICON_DIRS:
        raise LexiconError(
            "no lexicon release for language %r (available: %s)"
            % (language, sorted(LEXICON_DIRS)))
    return Path(__file__).resolve().parent.parent / "data" / "lexicons" / LEXICON_DIRS[language]

#: The connector sub-lists required in connectors.json. The first three are the
#: frozen M-CONN-30 groups (M-CONN-30 = 30c + 30k + 30r) and are required for
#: every language. Chinese additionally ships temporal/condition (issue #23,
#: PDTB 2.0 sense counterparts) for the forthcoming M-CONN-30t/30q derivatives;
#: they are validated and loaded, but never enter M-CONN-30 (see _metric_conn30,
#: which names the three original groups explicitly). English stays at its
#: frozen three-group v1 release.
CONNECTOR_GROUPS: tuple[str, ...] = ("contrastive", "causal", "result")
CONNECTOR_GROUPS_ZH: tuple[str, ...] = (
    "contrastive", "causal", "result", "temporal", "condition",
)


def _connector_groups_for(language: str) -> tuple[str, ...]:
    """The connector groups the release of `language` must declare."""
    if language == "zh":
        return CONNECTOR_GROUPS_ZH
    return CONNECTOR_GROUPS

_PLAIN_FILES: tuple[str, ...] = (
    "hedge",
    "booster",
    "nominalization_suffixes",
    "nominalization_verb_bases",
    "nominalization_denylist",
    "academic_words",
    "stopwords",
)
_CONNECTOR_FILE = "connectors"
_ALL_FILES: tuple[str, ...] = _PLAIN_FILES + (_CONNECTOR_FILE,)

_WS_RE = re.compile(r"\s+")


class LexiconError(Exception):
    """Raised for any malformed, inconsistent or missing lexicon file."""


def normalize_entry(entry: str) -> str:
    """Normalize one lexicon entry: strip, lowercase, collapse inner whitespace."""
    return _WS_RE.sub(" ", entry.strip().lower())


@dataclass(frozen=True)
class Lexicon:
    """One frozen word list (or one group of connectors.json)."""

    name: str
    version: str
    source: str
    sha256: str
    entries: tuple[str, ...]

    def entry_count(self) -> int:
        """Number of (normalized, unique) entries."""
        return len(self.entries)


@dataclass(frozen=True)
class LexiconBundle:
    """All eight lexicon files of one version, loaded and cross-validated."""

    version: str
    directory: Path
    hedge: Lexicon
    booster: Lexicon
    connectors: dict[str, Lexicon]
    nominalization_suffixes: Lexicon
    nominalization_verb_bases: Lexicon
    nominalization_denylist: Lexicon
    academic_words: Lexicon
    stopwords: Lexicon
    #: Which lexicon release this bundle is ("en" | "zh"). Appended with a
    #: default so existing positional construction keeps working.
    language: str = "en"

    # -- helpers ----------------------------------------------------------
    def all_lexicons(self) -> tuple[Lexicon, ...]:
        """Every Lexicon in the bundle, sorted by name (deterministic)."""
        items = [
            self.hedge,
            self.booster,
            self.nominalization_suffixes,
            self.nominalization_verb_bases,
            self.nominalization_denylist,
            self.academic_words,
            self.stopwords,
        ]
        items.extend(self.connectors[key] for key in sorted(self.connectors))
        return tuple(sorted(items, key=lambda lx: lx.name))

    def fingerprint(self) -> str:
        """Stable sha256 over every (name, sha256) pair, sorted by name."""
        pairs = sorted((lx.name, lx.sha256) for lx in self.all_lexicons())
        payload = "".join("%s\t%s\n" % (name, digest) for name, digest in pairs)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_manifest(self) -> list[dict]:
        """[{name, version, source, sha256, entry_count}] sorted by name."""
        return [
            {
                "name": lx.name,
                "version": lx.version,
                "source": lx.source,
                "sha256": lx.sha256,
                "entry_count": lx.entry_count(),
            }
            for lx in self.all_lexicons()
        ]


# ---------------------------------------------------------------------------
# Loading internals
# ---------------------------------------------------------------------------

def _read_raw(path: Path) -> tuple[bytes, dict]:
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        raise LexiconError("cannot read lexicon file %s: %s" % (path, exc)) from exc
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LexiconError("invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(obj, dict):
        raise LexiconError("%s: top-level JSON value must be an object" % path)
    return raw, obj


def _require_field(path: Path, obj: dict, field: str, expected: type) -> object:
    if field not in obj:
        raise LexiconError("%s: missing required field '%s'" % (path.name, field))
    value = obj[field]
    if not isinstance(value, expected):
        raise LexiconError(
            "%s: field '%s' must be %s, got %s"
            % (path.name, field, expected.__name__, type(value).__name__)
        )
    return value


def _checked_source(path: Path, obj: dict) -> str:
    source = _require_field(path, obj, "source", str)
    if not source.strip():
        raise LexiconError("%s: 'source' must be a non-empty string" % path.name)
    return source  # type: ignore[return-value]


def _checked_version(path: Path, obj: dict) -> str:
    version = _require_field(path, obj, "version", str)
    if not version.strip():
        raise LexiconError("%s: 'version' must be a non-empty string" % path.name)
    return version  # type: ignore[return-value]


def _checked_name(path: Path, obj: dict) -> str:
    name = _require_field(path, obj, "name", str)
    if not name.strip():
        raise LexiconError("%s: 'name' must be a non-empty string" % path.name)
    return name  # type: ignore[return-value]


def _normalize_entries(path: Path, raw_entries: object, where: str) -> tuple[str, ...]:
    if not isinstance(raw_entries, list):
        raise LexiconError(
            "%s: %s must be a JSON array, got %s"
            % (path.name, where, type(raw_entries).__name__)
        )
    normalized: list[str] = []
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, str):
            raise LexiconError(
                "%s: %s[%d] must be a string, got %s"
                % (path.name, where, idx, type(entry).__name__)
            )
        norm = normalize_entry(entry)
        if not norm:
            raise LexiconError(
                "%s: %s[%d] normalizes to an empty string" % (path.name, where, idx)
            )
        normalized.append(norm)
    # De-duplicate + sort: the double guarantee required by the contract. The
    # disjointness checks in load_lexicons() run on these normalized sets, so a
    # file cannot smuggle in an overlap via case/whitespace differences.
    return tuple(sorted(set(normalized)))


def _make_lexicon(
    path: Path,
    name: str,
    version: str,
    source: str,
    raw: bytes,
    entries: tuple[str, ...],
) -> Lexicon:
    return Lexicon(
        name=name,
        version=version,
        source=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        entries=entries,
    )


def _load_plain(path: Path, stem: str) -> Lexicon:
    raw, obj = _read_raw(path)
    json_name = _checked_name(path, obj)
    version = _checked_version(path, obj)
    source = _checked_source(path, obj)
    if "entries" not in obj:
        raise LexiconError("%s: missing required field 'entries'" % path.name)
    entries = _normalize_entries(path, obj["entries"], "entries")
    return _make_lexicon(path, json_name or stem, version, source, raw, entries)


def _load_connectors(path: Path, language: str) -> dict[str, Lexicon]:
    raw, obj = _read_raw(path)
    json_name = _checked_name(path, obj)
    version = _checked_version(path, obj)
    source = _checked_source(path, obj)
    groups = obj.get("groups")
    if groups is None:
        raise LexiconError("%s: missing required field 'groups'" % path.name)
    if not isinstance(groups, dict):
        raise LexiconError("%s: 'groups' must be a JSON object" % path.name)
    given = set(groups)
    required = set(_connector_groups_for(language))
    missing = sorted(required - given)
    extra = sorted(given - required)
    if missing or extra:
        raise LexiconError(
            "%s: 'groups' must be exactly %s; missing=%s unexpected=%s"
            % (path.name, sorted(required), missing, extra)
        )
    out: dict[str, Lexicon] = {}
    for group in _connector_groups_for(language):
        entries = _normalize_entries(path, groups[group], "groups.%s" % group)
        # Frozen contract: the dict is keyed by the bare group name
        # ({contrastive, causal, result}); the Lexicon carries the qualified
        # name "connectors/<group>" so it is unique in to_manifest()/fingerprint().
        out[group] = _make_lexicon(
            path, "%s/%s" % (json_name, group), version, source, raw, entries
        )
    return out


def _check_disjoint(a: Lexicon, b: Lexicon) -> None:
    overlap = sorted(set(a.entries) & set(b.entries))
    if overlap:
        raise LexiconError(
            "lexicons '%s' and '%s' must be disjoint; %d overlapping entries, e.g. %s"
            % (a.name, b.name, len(overlap), overlap[:10])
        )


def load_lexicons(directory: Path | None = None,
                  language: str = "en") -> LexiconBundle:
    """Load and validate all eight lexicon files from directory.

    Parameters
    ----------
    directory:
        Directory holding the eight JSON files. Defaults to the release
        directory of `language` (en -> .../data/lexicons/v1,
        zh -> .../data/lexicons/v2-zh).
    language:
        Which release to load. Sets the expected release version too, so a
        half-bumped Chinese release cannot ship as if it were English.

    Raises
    ------
    LexiconError
        On a missing directory/file, a missing or empty required field, an
        empty normalized entry, a hedge/booster overlap, or connector groups
        that are not exactly contrastive/causal/result and pairwise disjoint.
    """
    if language not in LEXICON_VERSIONS:
        raise LexiconError(
            "unknown lexicon language %r (available: %s)"
            % (language, sorted(LEXICON_VERSIONS)))
    expected_version = LEXICON_VERSIONS[language]
    if directory is None:
        directory = lexicon_dir_for(language)
    directory = Path(directory)
    if not directory.is_dir():
        raise LexiconError("lexicon directory does not exist: %s" % directory)

    missing_files = [
        "%s.json" % stem for stem in _ALL_FILES if not (directory / ("%s.json" % stem)).is_file()
    ]
    if missing_files:
        raise LexiconError(
            "lexicon directory %s is missing required files: %s" % (directory, missing_files)
        )

    plain: dict[str, Lexicon] = {
        stem: _load_plain(directory / ("%s.json" % stem), stem) for stem in _PLAIN_FILES
    }
    connectors = _load_connectors(
        directory / ("%s.json" % _CONNECTOR_FILE), language)

    # Release discipline: all eight files must declare the same version (a partial
    # bump is a release bug, not a per-file revision), and it must match the frozen
    # LEXICON_VERSION constant. Consumers see both the constant and the files.
    versions = {lx.version for lx in plain.values()}
    versions.update(lx.version for lx in connectors.values())
    if len(versions) != 1:
        raise LexiconError(
            "lexicon files declare inconsistent versions: %s "
            "(one lexicon release must bump all eight files together)" % sorted(versions)
        )
    if versions != {expected_version}:
        raise LexiconError(
            "lexicon release version %s does not match the expected version %s for language %r "
            "(LEXICON_VERSION=%s; per-language releases: %s)"
            % (sorted(versions), expected_version, language,
               LEXICON_VERSION, sorted(LEXICON_VERSIONS.items()))
        )

    bundle = LexiconBundle(
        language=language,
        version=versions.pop(),
        directory=directory,
        hedge=plain["hedge"],
        booster=plain["booster"],
        connectors=connectors,
        nominalization_suffixes=plain["nominalization_suffixes"],
        nominalization_verb_bases=plain["nominalization_verb_bases"],
        nominalization_denylist=plain["nominalization_denylist"],
        academic_words=plain["academic_words"],
        stopwords=plain["stopwords"],
    )

    _check_disjoint(bundle.hedge, bundle.booster)
    group_keys = sorted(bundle.connectors)
    for i, left in enumerate(group_keys):
        for right in group_keys[i + 1:]:
            _check_disjoint(bundle.connectors[left], bundle.connectors[right])

    return bundle
