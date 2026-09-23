---
name: remove-ai-slops
description: "Remove AI-generated code smells (slop) from branch changes or an explicit file list. Locks behavior with regression tests FIRST, then runs categorized cleanup via parallel subagents in batches of 5, then verifies with quality gates. Covers 10 slop categories: performance equivalences, excessive complexity (object annotations, if/elif chains), oversized modules (250+ pure LOC). MUST USE when asked to remove slop / clean AI code / deslop / strip slop from recent changes."
metadata:
  family: coding
  role: member
  load-mode: manual
disable-model-invocation: true
---
# Remove AI Slops Skill

## Inputs

- **Default scope**: branch diff vs `merge-base main` (no arguments needed)
- **Optional scope**: explicit file list passed by the caller (e.g., a Ralph workflow's changed-files set)

## What this skill does

Cleans AI-generated slop from a bounded set of changed files while strictly preserving behavior. Locks behavior with regression tests first, then runs a categorized multi-pass cleanup, then verifies with quality gates and a critical review. Reverts and direct-edits when verification fails.

The core safety invariant: **behavior is locked by green tests before a single line is removed**. A checklist alone is not safety; a passing regression test is.

---

## Categories (what counts as slop)

The agent looks for these nine categories. The first three are stylistic, the next three are structural, the next two are about hidden cost, and the last is about behavior coverage.

### Stylistic
1. **Obvious comments** — comments restating code, trivial docstrings, section dividers, commented-out code, vague TODOs/Notes.
   - KEEP: comments explaining WHY (business logic, edge cases, workarounds), ticket links, regex/algorithm explanations.
   - KEEP: BDD markers (`# given`, `# when`, `# then`, `# when/then`).

2. **Over-defensive code** — null checks for guaranteed values, try/except around code that cannot raise, isinstance checks for statically typed params, default values for required params, backward-compat shims, redundant validation duplicated at multiple layers, **broad exception catching** (`except Exception`/`except BaseException` in Python, empty `catch {}` or `catch (e) { console.error(e) }` without narrowing in TypeScript/JavaScript).
   - KEEP: validation at system boundaries (user input, external APIs), I/O error handling, nullable DB fields. Top-level boundary catch-all (CLI `main()`, HTTP handler) with explicit logging + re-raise is acceptable.
   - REFACTOR: `except Exception` → catch the specific exception you expect. Empty `catch {}` → add `instanceof` narrowing or re-throw. `catch (e) { log(e) }` → narrow with `instanceof`, handle known cases, re-throw unknown.
   - PROOF REQUIRED: before deleting any validation or error handling at a trust boundary, Phase 2 must include an **adversarial** regression (malformed or hostile input) that fails if the guard is removed. No adversarial test → the guard stays. Redundant defense to remove is a duplicate of a check that already runs *inside* the boundary; a guard with no proof of redundancy is load-bearing.

3. **Excessive complexity** — deep nesting (>3 levels), nested ternaries, complex boolean expressions (combine 4+ predicates), long parameter lists (>5 args without a struct/dataclass/object), god functions (>50 lines doing many things), overly clever one-liners that sacrifice readability, `if/elif/else` chains for type/enum/literal discrimination (must be `match/case` + `assert_never`), `object` used as a type annotation (must be `Protocol`, `TypeVar`, or explicit union).
   - KEEP: established complexity patterns in this codebase, performance-critical hot paths that intentionally use a complex idiom. `if/else` for boolean conditions and range checks (not variant discrimination).
   - REFACTOR: nested if-chains → guard clauses / early returns. Complex ternaries → explicit if/else. isinstance/enum if/elif chains → `match/case` with `assert_never` on the wildcard. `object` annotations → `Protocol` (structural), `TypeVar` (generic), or union (known variants).

### Structural
4. **Needless abstraction** — pass-through wrappers, single-use helpers, speculative indirection ("we might need this later"), interfaces with one implementer where the interface adds no testability win, factory functions that just call a constructor.
   - KEEP: abstractions that provide a real seam (testability, multiple implementers, framework-required boundaries).

5. **Boundary violations** — wrong-layer imports (UI importing DB driver), leaky responsibilities (handler doing business logic that belongs in a service), hidden coupling (module A reads module B's private state), side effects in pure-named functions.
   - KEEP: pragmatic short-circuits already established as a pattern in this codebase. Flag for human judgment if unsure.

6. **Dead code** — unused imports, unused private functions/methods, unreachable branches, stale feature flags, debug leftovers (`console.log`, `print(...)`, `dbg!`), removed-but-still-referenced code.
   - KEEP: code referenced via reflection, dynamic dispatch, or string lookup. Code intentionally kept as a feature flag rollback path (verify with the user).

### Hidden cost
7. **Duplication** — copy-pasted branches with trivial differences, redundant helpers that do the same thing in two places, repeated literal/magic-number sequences.
   - KEEP: incidental duplication (two pieces of code that look similar but serve different intents that could diverge). Prefer leaving them separate over forcing a premature shared abstraction.

8. **Performance equivalences (behavior-preserving optimizations)** — changes that are provably equivalent in semantics but cheaper in time/space:
   - O(n²) → O(n) when correctness preserved (e.g., set lookup vs list scan)
   - Repeated computation inside a loop → hoist outside
   - Unnecessary intermediate collections (eager `list(...)` when only iterated once → generator)
   - String concatenation in loop → `join`
   - Redundant DB/API calls in a loop → batch
   - Redundant deep copies / clones
   - `.length` / `len()` recomputed inside loop → cache

   **Hard rule**: only apply when behavior equivalence is obvious. Do NOT change algorithms with subtle correctness implications. Do NOT micro-optimize hot paths without a benchmark. If in doubt, SKIP.

### Behavior coverage
9. **Missing tests** — behavior present in changed files that is not locked by any regression test. The fix is not to remove code but to ADD the narrowest test that pins the behavior. EXCEPTION: a PROSE file (prompt, `SKILL.md`, rule, markdown) has no behavioral seam — do NOT add a text/word-count/phrase pin for it; that guards a diff, not behavior. Cover only a machine-consumed value (parsed field, sentinel a runtime greps, a doc JSON sample through its real validator) or leave it to review.

### Structural
10. **Oversized modules** — any source file exceeding **250 pure LOC** (non-blank, non-comment lines). This is an architectural defect, not a style preference. Measure: `awk '!/^[[:space:]]*$/ && !/^[[:space:]]*(#|\/\/)/' <file> | wc -l`.

   **When found, do NOT just flag it. Execute a full modular refactoring:**
   1. Run `check-no-excuse-rules.py` recursively on scope to list all violations.
   2. For each oversized file, identify distinct responsibilities (single-responsibility principle).
   3. Plan the split: name each new file after the concept it owns (never `utils.py`, `helpers.py`, `common.py`, `part_1.py`).
   4. Present the split plan to the user before executing.
   5. Extract into clean modules with explicit `__init__.py` re-exports (re-exports ONLY, no logic in `__init__.py`).
   6. Verify: run `check-no-excuse-rules.py` again — every file must be ≤250 pure LOC. Run tests, typecheck, lint.

   **Forbidden escapes**:
   - Counting blanks/comments toward budget.
   - Splitting by token count (`foo_1.py`, `foo_2.py`) — split by what each file DOES.
   - Catch-all dump files (`utils.py`, `helpers.py`, `service.py`).
   - "It's generated" — only valid if the file lives in a build output directory.
   - "230 LOC, close enough" — a 230-LOC file about to grow is already over. Split now.

   KEEP: genuinely self-contained single-responsibility scripts (e.g., a standalone CLI checker). Opt out with `# noqa: SIZE_OK` in first 5 lines and a comment explaining why.

---

## Quality Gates

A pass is complete only when all applicable gates are green. Skip gates that are genuinely N/A for the project (e.g., no security scanner configured), and report `N/A` explicitly — do not silently skip.

| Gate | Tool | Pass condition |
|---|---|---|
| Regression tests | project's test runner | all green |
| Lint | project's linter | zero errors (warnings OK if pre-existing) |
| Typecheck | project's own type-checker (tsc / basedpyright / cargo check / go vet …) on changed files; use project's own typecheck (`tsc --noEmit` / `ruff` / `mypy` / `cargo check` / `go vet`) only if that MCP is mounted | zero new errors |
| Unit/integration tests | project's test runner | all green (pre-existing failures noted, not introduced) |
| Static/security scan | project's scanner | zero new findings, or `N/A` if not configured |

---

## Process

### Phase 0: Plan with TodoWrite

Create todos for all phases below. Mark `in_progress` one at a time.

### Phase 1: Determine scope

If file paths were passed as arguments, that is the scope. Otherwise:

```bash
git diff $(git merge-base main HEAD)..HEAD --name-only
```

Filter out: deleted files, binary files, generated/vendored files (`node_modules/`, `dist/`, `target/`, lockfiles). List the final scope.

### Phase 2: Lock behavior with regression tests (NEW — non-negotiable)

For each in-scope source file:

1. Identify the public/observable behavior the file exposes (exported functions, HTTP handlers, CLI commands, classes used elsewhere).
2. Check whether existing tests cover that behavior. Use `git grep` / project test conventions to find related test files.
3. **If behavior is uncovered or weakly covered, write the narrowest regression test that pins current behavior BEFORE editing the file.** Tests should pin observable outputs, not implementation details. A PROSE file (prompt/`SKILL.md`/rule/markdown) is exempt — its wording is not behavior; skip the test and rely on review, or assert only a machine-consumed value.
4. Run the test suite (or at minimum the relevant tests). They must be **green** before any cleanup begins.

If you cannot establish a green baseline (e.g., test runner is broken), STOP and report. Do not proceed with cleanup on unverified ground.

### Phase 3: Cleanup plan — existence first, then smells

The largest, safest deletion is code that should not have existed. **Before categorizing smells, run the deletion ladder on each changed unit:**

- **Delete entirely** — the behavior is not needed (YAGNI, speculative, dead on arrival).
- **Reuse** — an existing helper or pattern in this repo already does it; replace the reimplementation with a call to it.
- **Platform / stdlib / native / dependency** — the language stdlib, the runtime, or an already-installed dependency already does it (a hand-rolled date picker → `<input type="date">`, a custom query parser → `URLSearchParams`, a bespoke debounce → the util already imported).