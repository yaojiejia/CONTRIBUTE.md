---
name: opt-scan
description: This skill should be used when the user asks "what can be optimized", "find optimization opportunities", "which modules could be improved", "brainstorm optimizations", "where are the performance problems", "what's slow in this repo", or "some modules could be optimized but aren't". It walks every module through a fixed set of optimization lenses, verifies each candidate against real code, and produces a ranked list with evidence, impact, effort, and risk. It reports only and never modifies source.
version: 0.1.0
---

# Opt Scan

Find what could be optimized in a repository and has not been — systematically, with evidence, and ranked.

The method is deliberately two-pass: **brainstorm broadly, then work through the ideas against real code.** Generating and filtering at the same time suppresses candidates before they are examined and lets unverified ones through; separating the passes is what makes the output trustworthy.

## Hard contract

**This skill reads and reports. It does not modify source code.** No edits, no refactors, no "while I was there" fixes. Implementation is a separate, explicit request from the user. State this when presenting results.

## Procedure

### 1. Inventory

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"
```

Read `.repo-intel/LESSONS.md` (index first) and the **Module map**, **Hot spots**, and **Primary flows** sections of `.repo-intel/REPO-MAP.md`.

If no repo map exists, offer to run `repo-map` first. It is worth it: without a module inventory the scan has no systematic frame, and without **Primary flows** there is no way to tell a hot path from a cold one — which is the difference between a ranked list and a pile of pattern matches.

If the user declines, run `repo_survey.py` directly for the module list and churn data:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_survey.py" > /tmp/survey.json
```

Then check for a previous scan in `.repo-intel/optimizations/` and read its **Done / Rejected** ledger — never re-propose something already fixed or deliberately declined.

### 2. Divergent pass — brainstorm

Walk **every module through every lens** in `${CLAUDE_PLUGIN_ROOT}/references/optimization-lenses.md`:

1. Algorithmic complexity · 2. I/O and N+1 · 3. Caching and recomputation · 4. Redundant per-call construction · 5. Data structure fit · 6. Concurrency (missing *and* unbounded) · 7. Memory and allocation · 8. Startup, build, and CI time · 9. Bundle and artifact size · 10. Duplication and dead code · 11. Database queries and indexes · 12. Configuration-level wins

For each module × lens pair ask: *is there a plausible instance of this pattern here?* Record every plausible one, cheaply and without judgment. The reference file carries `rg` patterns to start from for each lens.

Judgment belongs in the next pass. A fixed checklist earns its keep by surfacing categories that would not otherwise be considered — lenses 8, 11, and 12 in particular are consistently where the largest untaken wins sit, and consistently the least examined.

### 3. Convergent pass — verify

This pass determines whether the output is useful. For every candidate, establish three things:

1. **It exists.** Open the file and read the actual code. Get the exact `file:line`. A regex hit is not evidence.
2. **It is on a path that matters.** Trace it to an entrypoint, request handler, or hot loop, using the repo map's **Primary flows**. An inefficiency in a startup path that runs once in 4ms is not a finding, however textbook the pattern.
3. **The fix is concrete.** Name the specific change and what it costs. "Add caching" is not a proposal; "hoist the `re.compile` to module scope" is.

Then:

- **Fails (1)** → drop it silently. It was not real.
- **Fails (2) or (3)** → move it to **Unverified hypotheses** with a note on what could not be established.
- **Passes all three** → it goes in the ranked list.

Cheap measurement beats estimation whenever it is available: counting call sites with `rg`, reading CI durations from `gh run list`, checking a file size, timing a single existing test. Use the number when you can get one in seconds; estimate honestly when you cannot.

**The failure mode this pass exists to prevent** is a plausible-sounding optimization for code that does not exist, or that runs once at import. One such entry makes a reader distrust the entire list — which is why unverified items are separated rather than softened with hedging language.

### 4. Rank and write

Write `.repo-intel/optimizations/OPT-<YYYY-MM-DD>.md`. Rank by impact first, then by effort ascending — the top of the list should be what a person would actually do first.

Each entry:

```markdown
## OPT-03  api/serializer.py:88  [HIGH impact / LOW effort / LOW risk]
**Pattern:** N+1 query
**Evidence:** `for u in users: db.get_profile(u.id)` — reached from GET /users (api/routes.py:41)
**Proposed change:** single batched query + dict join
**Why it matters:** 412 calls in the test fixture
**Risk:** none — pure read path
**Verify with:** tests/test_api.py::test_user_list, plus timing
```

Rate the three dimensions honestly:
- **Impact** — HIGH only with a reason: a hot path, a measured cost, a user-visible delay.
- **Effort** — LOW means under an hour for someone who knows the code.
- **Risk** — what could break. A pure read path is LOW; anything touching concurrency, caching, or public API is not.

Close the file with sections **Unverified hypotheses** and **Done / Rejected**, then the provenance footer. Carry forward the previous scan's Done/Rejected entries.

### 5. Report and reflect

Summarize: how many candidates were generated, how many survived verification, and the top three with one line each. Restate that nothing was modified.

Then apply the reflection step from `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md` — a discovery about how the repo is structured belongs in `LESSONS.md`, not only in the scan output.

## Calibration

- **Ten verified findings beat forty plausible ones.** The list is read by someone deciding where to spend an afternoon.
- **Report an empty result when that is the answer.** A well-optimized repo exists. Manufacturing findings to look thorough is the fastest way to make the tool worthless.
- **Do not report style as optimization.** Naming, formatting, and idiom preferences are a different concern; keep them out unless they carry a real cost.
- **Respect deliberate choices.** Code that looks naive is sometimes load-bearing — a comment explaining the tradeoff, a linked issue, a benchmark in the repo. Look before proposing, and note the justification when one exists.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/optimization-lenses.md`** — all twelve lenses with search patterns, false positives, and the verification bar
- **`${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`** — output location and provenance footers
- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`** — the reflection step
