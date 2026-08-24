---
name: repo-lessons
description: This skill should be used when the user says "log a lesson", "remember that for this repo", "add that to lessons", "don't make that mistake again", "note that for next time", or corrects a wrong assumption about how a repository works. It maintains .repo-intel/LESSONS.md, the per-repo ledger of corrections that the repo-map, issue-hunt, and opt-scan skills read before every run. Also invoked by those three skills to read lessons at the start of a run and record a correction at the end.
version: 0.2.0
---

# Repo Lessons

Maintain `.repo-intel/LESSONS.md` — a per-repo ledger of corrections, read before work starts and appended to when something turns out to be other than assumed. This is the memory that makes the other three `repo-intel` skills improve at a repo over time rather than re-deriving the same wrong assumptions every session.

Read `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md` before writing or editing any entry. It carries the full entry schema, the dedupe procedure, and the promotion rules. This file covers only the operating loop.

## Two modes

This skill runs in one of two modes. Determine which from context before doing anything.

**Read mode** — invoked at the start of any repo-intel run, or whenever orienting in a repo that already has a ledger.

**Write mode** — invoked explicitly by the user, or at the end of a run in which something went wrong.

## Read mode

1. Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"` to locate the repo and confirm which artifacts exist.
2. If `.repo-intel/LESSONS.md` does not exist, proceed with the task normally. Do not create an empty ledger.
3. Read **the index section only** — the one-line-per-lesson list near the top. Loading every full entry on every run is exactly the cost this structure exists to avoid.
4. Scan the index for subjects that touch the current task.
5. Read the full entries for those subjects, and only those.
6. When a lesson's **Rule** applies to a step about to be taken, follow it and say so in one short sentence: *"LESSONS.md notes the root `test` script is a no-op here, so using `pnpm -w test:unit`."* Making the ledger visibly load-bearing is what earns it trust; a ledger read silently is indistinguishable from one never read.

## Write mode

Write an entry when — and only when — one of these has happened:

1. **The user corrected a wrong assumption.** Always a lesson.
2. **A command, build, or test failed because of a guess** — not any failure, but one caused by acting on an unverified belief.
3. **A run dead-ended** and the reason is now known.
4. **A finding contradicted `REPO-MAP.md`.** Record the correction, then fix the map.
5. **Something non-obvious was learned at real cost** — an implicit invariant, a load-bearing side effect, a config silently overriding another.

Do not write for: anything already in `REPO-MAP.md`, `CLAUDE.md`, `CONTRIBUTING.md`, or the README; a restatement of an error message (the lesson is the cause, never the traceback); transient one-offs; anything `rg` or `git log` would answer in seconds; or speculation.

### Procedure

1. **Draft the entry** against the schema:

```markdown
## <YYYY-MM-DD> — <short title>
**Confusion:** What was assumed, and what turned out to be true instead.
**Signal that would have caught it:** The observable fact that was available all along.
**Rule:** The generalized directive for next time.
**Refs:** `path/file.ext:line`, [[other-lesson]]
```

   Spend the most care on **Signal**. It answers *what could have been read, and where, that would have prevented this* — it is what converts a war story into something actionable. And write **Rule** in the imperative, general enough to fire in a similar-but-not-identical situation, specific enough to act on. "Be careful with tests" is not a rule; "read `turbo.json` before inferring test commands in a monorepo" is.

2. **Dedupe before appending.** Never skip this:

   ```bash
   rg -i '<subject keywords>' .repo-intel/LESSONS.md
   ```

   - No match → append the entry, add its index line at the top of the index.
   - Near-match → **sharpen the existing entry rather than adding a second.** Broaden its Rule to cover both cases, add the new `file:line` to Refs, update its date. Two entries on one subject is where this file starts to decay.
   - Contradicting match → the newer observation wins. Rewrite the entry and add `**Superseded:** <what the old entry claimed, and why it was wrong>`. Keeping the reversal on record prevents flip-flopping back later.

3. **Create the file if absent**, with the header and index structure from the protocol reference.

4. **Consider promotion.** When a rule has fired more than once, or is plainly a fixed property of the repo rather than a note about a mistake, add it to the **Gotchas** section of `REPO-MAP.md` and mark the entry `**Status:** promoted → REPO-MAP.md#gotchas`. Leave the entry in place — the record of how it was learned is what stops it being re-litigated.

5. **Report** what was written in one line, quoting the Rule. The user should be able to object immediately if the generalization is wrong.

## Reflection step for other skills

At the end of a `repo-map`, `issue-hunt`, or `opt-scan` run, make one pass asking: *did anything turn out other than expected?*

If yes and it meets a trigger above, record it. If nothing did, **write nothing.** A run producing no lesson is the normal case; manufacturing an entry to look productive degrades the file for every future run.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`** — full schema, dedupe, promotion, contradiction handling, worked file structure
- **`${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`** — `.repo-intel/` layout and provenance footers
