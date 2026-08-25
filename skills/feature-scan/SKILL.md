---
name: feature-scan
description: This skill should be used when the user asks "what features could we add", "what should we build next", "find feature opportunities", "what's missing from this repo", "brainstorm features", "what would make this better", "give me a roadmap", or "run the feature finder". It walks the repository through a fixed set of feature lenses, verifies each candidate is genuinely absent and in scope, and produces a ranked list with evidence, demand, effort, and risk. It reports only and never modifies source.
version: 0.4.0
---

# Feature Scan

Find what a repository could gain and has not — grounded in evidence from the repo and its users, ranked, and safe to hand to someone deciding what to build.

The method mirrors `opt-scan`: **brainstorm broadly, then work through the ideas against real code.** The difference is where the danger sits. An optimization scan fails by proposing a fix for code that does not exist. A feature scan fails by proposing a feature that *already* exists, or that the maintainers have already refused — and either one, once, makes the reader discard the whole list.

## Hard contract

**This skill reads and reports. It does not modify source code.** No edits, no scaffolding, no "I went ahead and started it". Implementation is a separate, explicit request. State this when presenting results.

**A feature proposal must cite the lens it came from.** Proposals derived from what a category of software "usually has" are guesses. They are allowed only under lens 10, only with a citation, and never in the ranked list without one.

## Procedure

### 1. Inventory

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"
```

Read `.repo-intel/LESSONS.md` (index first) and the **Module map**, **Key abstractions & invariants**, and **Open questions** sections of `.repo-intel/REPO-MAP.md`.

If no repo map exists, offer to run `repo-map` first. It matters more here than for `opt-scan`: without a module inventory there is no way to run lens 4 (asymmetric coverage) at all, because that lens *is* an inventory diff. If the user declines, run `repo_survey.py` for the module list:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_survey.py" > /tmp/survey.json
```

Then read the project's own scope rules — `CONTRIBUTING.md`, `AGENTS.md`, `docs/` — looking for headings about plugins, extensions, or what does not belong in the tree. Mature repos state these explicitly, and a proposal that violates one is rejected on sight regardless of merit.

Finally, check `.repo-intel/features/` for a previous scan and read its **Already exists / Declined** ledger. Never re-propose something a prior run already ruled out.

### 2. Gather stated demand

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/find_feature_demand.py" --repo owner/name --format md
```

Ranks open feature requests by reaction-weighted demand and lists closed ones that carry a declining label or `not planned`. Write it to `.repo-intel/features/DEMAND-<YYYY-MM-DD>.md`.

Read the **Declined** section before the **Wanted** section. Knowing what has been refused reshapes every later proposal, and a refusal reading "not here — ship it as a plugin" converts a dead request into a live, correctly-placed one.

Skip with `--no-closed` only when the repo has no meaningful closed history. On a repo whose `origin` is a fork, pass `--repo` explicitly — forks usually have issues disabled.

### 3. Divergent pass — brainstorm

Walk the repo through **every lens** in `${CLAUDE_PLUGIN_ROOT}/references/feature-lenses.md`:

1. Stated demand · 2. Declined already · 3. Unfinished in-tree work · 4. Asymmetric coverage · 5. Extension points with one implementation · 6. Config and docs promising more than the code delivers · 7. Workflow discontinuity · 8. Format and protocol adjacency · 9. Reach — platforms, environments, locales · 10. Peer parity

Record every plausible candidate with the lens that produced it. Judgment belongs in the next pass.

The lenses are ordered by evidence strength deliberately. Lenses 3–6 are the ones that consistently produce findings a maintainer had not considered but immediately recognises, because they are derived from the repo's own structure rather than from anyone's taste. Lens 10 is the one that fabricates.

### 4. Convergent pass — verify

For every candidate, establish four things, in this order:

1. **It is genuinely absent.** Search code, config schema, CLI `--help`, docs, and recent changelog. Cite the searches that came back empty. Issue trackers lag implementations badly — an old request is often long since shipped by a PR that never closed it.
2. **It is wanted, or structurally load-bearing.** External demand from step 2, or an internal structural argument from lenses 3–6.
3. **It belongs in this repo.** Check against the scope rules read in step 1.
4. **The first step is concrete.** Name the module and the seam, with a `file:line`.

Then:

- **Fails (1)** → move to **Already exists**, with the evidence that it exists. This section is not filler; it is what stops the next scan re-deriving the same wrong idea.
- **Fails (3)** → keep it, but reframe it as the correctly-placed version (a plugin, an extension) and say so explicitly.
- **Fails (2) or (4)** → **Unverified hypotheses**, with a note on what could not be established.
- **Passes all four** → the ranked list.

### 5. Rank and write

Write `.repo-intel/features/FEAT-<YYYY-MM-DD>.md`. Rank by demand first, then effort ascending.

```markdown
## FEAT-03  S3 backend for the artifact store  [HIGH demand / MED effort / LOW risk]
**Lens:** 4 — asymmetric coverage
**Evidence:** `storage/backends/` ships local, gcs, azure — three backends, no s3.
#1245 asks for it (11 reactions, 5 participants, open 8mo).
**Absence verified:** `rg -in 's3|boto' -g '!*.lock'` → 0 hits outside
`docs/roadmap.md:44`; no `s3` key in `config.schema.json`; `--storage-backend`
in `cli.py:210` accepts only the three above
**Placement:** allowed in-tree — CONTRIBUTING.md closes only auth providers
**Where it lands:** new `storage/backends/s3.py` implementing `StorageBackend`
(`storage/backends/base.py:18`), registered in `BACKENDS` at
`storage/backends/__init__.py:22`
**First step:** copy `gcs.py`, swap the client and the URI parser
**Risk:** LOW — additive, no existing path changes
```

The **Absence verified** line is the one that earns the entry. Three independent
searches came back empty: the code, the config schema, and the user-facing flag.
One `rg` is not enough — see the worked false positive in lens 4.

Rate honestly:
- **Demand** — HIGH needs named evidence: an issue with real reactions, several independent requests, or a structural gap that provably costs users. Never HIGH from lens 10 alone.
- **Effort** — LOW means under a day for someone who knows the code. Say what dominates the cost.
- **Risk** — additive features are usually LOW. Anything touching a public interface, a data format, a security boundary, or a shared abstraction is not.

Close with **Unverified hypotheses**, **Already exists**, **Declined (do not re-propose)**, then the provenance footer. Carry forward the previous scan's ledgers.

### 6. Report and reflect

Summarize: how many candidates were generated, how many survived, how many were dropped as already-existing, and the top three with one line each. Restate that nothing was modified.

Then apply the reflection step from `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`. A discovery about how the repo is structured — an extension point, a scope rule, a family of adapters — belongs in `LESSONS.md`, not only in this scan.

## Calibration

- **Eight verified proposals beat thirty plausible ones.** This list is read by someone deciding what to spend a month on.
- **The "Already exists" section is a success metric, not an embarrassment.** A scan that dropped nine candidates as already-shipped did more work than one that dropped none.
- **Report an empty result when that is the answer.** A mature, tightly-scoped repo with an active maintainer has often already taken the obvious ideas. Manufacturing proposals to look useful is the fastest way to make the tool worthless.
- **Do not propose rewrites.** "Migrate to a different framework" is not a feature; it is a project. Keep the unit of proposal to something one person could land.
- **Respect a stated scope.** A repo that says "we do not accept new memory providers" means it. The right move is to propose the plugin, not to argue the policy.
- **Never fabricate demand.** If nobody asked, say nobody asked and justify it structurally instead. Inventing a user is the one unrecoverable error here.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/feature-lenses.md`** — all ten lenses with search patterns, false positives, and the four-point verification bar
- **`${CLAUDE_PLUGIN_ROOT}/scripts/find_feature_demand.py`** — `--repo`, `--limit`, `--closed-limit`, `--top`, `--label`, `--no-closed`, `--format {json,md}`
- **`${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`** — output location and provenance footers
- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`** — the reflection step
