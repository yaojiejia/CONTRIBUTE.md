---
name: bug-scan
description: This skill should be used when the user asks "find low hanging fruit", "find more bugs like these issues", "these issues share a pattern, are there others", "scan for similar bugs", "what else has this shape", "find easy bugs to fix", "pattern hunt", or points at one or more issues and asks what else in the codebase is broken the same way. It extracts the shape of a known defect, enumerates every place that shape could recur, verifies each candidate against real code and, where cheap, against a real run, and produces a ranked list plus issue drafts in the repo's own template. It reports only and never modifies source.
version: 0.1.0
---

# Bug Scan

Find bugs that share a shape with bugs already known, and prove each one before reporting it.

The starting point is one or more filed issues, a merged fix, or a finding from a review. Those are seeds, not the target. The target is the *shape* underneath them: a producer hands something to a consumer across a boundary, and the two disagree about a unit, a limit, or who is responsible for checking. Once the shape is named, every other crossing of the same boundary is a candidate. Most of them are fine. The ones that are not are the low hanging fruit: small, mechanical, and easy to verify.

The method is three-pass: **abstract the seeds into a shape, enumerate every crossing, then verify each one against the code and, when cheap, against a real execution.** The first pass is what separates this from grepping for the seed's literal text. The third pass is what separates it from a list of pattern matches.

## Hard contract

**This skill reads and reports. It does not modify source code.** No fixes, no refactors, no "while I was there". A probe test written to exercise a real function is allowed only if it is deleted in the same step and `git status` is clean afterwards. Say so when presenting results.

**Every finding cites `file:line` and names the failure mode.** "Silently returns a wrong answer", "crashes with an unreadable error", and "emits a garbage character in a log line" are three different severities. A finding that does not say which one it is has not been verified.

**Numbers are measured or labeled as estimates.** A claim like "about 2x over budget" derived from a rule of thumb is a guess about a guess. Run the tokenizer, run the function, count the bytes. If that is not possible in the session, write `(estimated)` next to the number.

## Procedure

### 1. Inventory

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"
```

Read `.repo-intel/LESSONS.md` (index first). If `.repo-intel/REPO-MAP.md` exists, read **Module map** and **Primary flows**; the second pass needs to know which paths are production and which are tests or tooling.

Check `.repo-intel/bugs/` for a previous scan and read its **Already filed / Checked and fine** ledger. Never re-report something a prior run already ruled out or already filed.

### 2. Read the seeds and extract the shape

Fetch every seed issue in full, including linked and referenced issues:

```bash
gh issue view <n> --json title,body,labels,state
```

Read the referenced ones too. A seed that says "same root as #N" or "related: #M" is handing over more of the shape for free, and the related issues often already list the surfaces they cover, which tells the scan which ones are left.

Then write the shape down in one paragraph before searching for anything. Use this frame:

- **Boundary.** What is handed across: text into a model, bytes into a buffer, a value into a hash, a count into a threshold.
- **Producer assumption.** What the caller believes about the input: that it fits, that it is ASCII, that it is short, that someone else clamps it.
- **Consumer behaviour.** What actually happens when the assumption is false: silent truncation, wrong answer, crash, garbage.
- **Unit or limit in dispute.** Bytes versus characters versus tokens. A 512 hard-coded somewhere. A heuristic constant calibrated for one language.
- **Who is already handled.** Which call sites the seeds say are fixed, chunked, sampled, or guarded. These are the precedents the fix will copy.

Name the shape in a phrase. "Unbounded text reaches a 512-token model without chunking" is a shape. "Issue 3364" is not.

### 3. Divergent pass: enumerate every crossing

Walk the codebase through **every lens** in `${CLAUDE_PLUGIN_ROOT}/references/bug-lenses.md`, starting from the lens the seeds belong to and then covering the rest:

1. Limit mismatch across a boundary · 2. Unit mismatch (bytes, runes, tokens, seconds) · 3. Sibling-path asymmetry · 4. Surface disagreement · 5. Silent discard · 6. Heuristic constants · 7. Guards that do not guard · 8. Boundary slicing · 9. Default fall-through · 10. Inconsistent failure modes

The most productive single move is to **list every caller of the boundary.** Find the function, FFI export, client method, or type that sits on the consumer side of the shape, then enumerate every call into it:

```bash
rg -n 'boundary_fn\(' --type go | rg -v _test | sed -E 's/.*(boundary_fn)\(.*/\1/' | sort | uniq -c
rg -n 'boundary_fn\(' --type go | rg -v _test
```

For each call site record: the file and line, what text or value it passes, and whether it is already guarded (chunked, sampled, clamped, errors early) or not. This table is the scan. Everything after it is verification.

Record every plausible candidate without judgment. A call site that "probably passes short text" is a candidate until the code says what it passes.

### 4. Convergent pass: verify

For every candidate establish, in order:

1. **It exists.** Open the file. Read the code around the call. Confirm what is passed and that no guard sits between the caller and the boundary. A grep hit is not evidence; a wrapper two frames up may already clamp.
2. **It is reachable in production.** Trace from the call site to a config option, request handler, or filter chain. Note whether the path is on by default, behind a flag, or test-only. A defect in a function with no non-test callers is dead code, not a bug; report it under cleanup, not under findings.
3. **The consequence is concrete.** Say what a user sees. "Cache returns the answer to a different question", "API returns zero entities past 2800 characters", "prompt starts with two replacement characters". If the consequence cannot be stated, the finding is not ready.
4. **The failure mode is classified.** Silent wrong result, loud but unreadable error, loud and clear error, cosmetic. Silent wrong results rank above everything else because nobody will find them from logs.

Then, when it is cheap, **measure instead of reasoning:**

- Write a throwaway `_test.go` (or equivalent) inside the package that calls the real function with a crafted input and logs what comes back. Run it. Delete it. Confirm `git status` is clean.
- Run a real tokenizer on the real slice the code produces. The `tokenizers` crate or library is usually already a dependency; a twenty-line program with the model's `tokenizer.json` settles a density argument that estimation cannot.
- Count call sites, byte lengths, and rune counts with the tools at hand rather than by inspection.

A measurement that contradicts the reasoning is the most valuable output of the session. Report the correction plainly. In one scan the estimate went from "6x over budget" to "2x over" to, after running the tokenizer, "40% under". The first two numbers would have gone into an issue.

Sort the results:

- **Fails (1)** → drop silently.
- **Fails (2) or (3)** → **Plausible, needs a live check**, with a note on what could not be established.
- **Passes all four, consequence is silent-wrong or loud-unreadable** → **Same shape, verified**.
- **Passes all four, consequence is cosmetic** → **Nits**.
- **Inspected and found guarded** → **Checked and fine**. This section matters: it is what stops the next scan from re-deriving the same conclusions, and it is what tells the reader the scan was systematic.

Finally, check overlap with existing issues before writing anything:

```bash
gh issue list --search "<key terms from the finding>" --state all --json number,title,state
```

A finding that is a missing surface of an existing issue belongs as a comment on that issue, not as a new one. Say which.

### 5. Rank and write

Write `.repo-intel/bugs/BUG-<YYYY-MM-DD>.md`. Rank by consequence first (silent wrong > unreadable error > cosmetic), then by fix size ascending. The top of the list is what someone should file first.

Each entry:

```markdown
## BUG-04  src/cache/polarity.go:56  [SILENT-WRONG / small fix]
**Shape:** premise+hypothesis truncated right at 512; hypothesis lost when premise is long
**Seeds:** #3366 (same trigger), #3365 (same truncation)
**Evidence:** `ClassifyNLI(cachedQuery, incomingQuery)` → `format!("{} [SEP] {}")` at ffi/classify.rs:1939, TruncationDirection::Right
**Reachable via:** semantic_cache.polarity_guard.mode: nli (off by default)
**Consequence:** contradiction score describes only the cached query; guard passes exactly when the embedding collides
**Measured:** (not run) reasoning from code only
**Precedent:** looper/grounding.go:210 chunks the hypothesis for the same model
**Overlaps:** none filed; related to #3366
```

Close the file with **Plausible, needs a live check**, **Nits**, **Checked and fine**, **Already filed**, then the provenance footer from `${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`. Carry forward the previous scan's ledger.

### 6. Draft the issues

When the user asks to file, read the repo's own template first:

```bash
ls .github/ISSUE_TEMPLATE/
cat .github/ISSUE_TEMPLATE/*bug*
```

Fill every required field. Follow these rules for the body, because the reader is a maintainer skimming a queue:

- **Mechanism in one plain sentence before any code.** "The code cuts the string at a byte offset. A Chinese character is three bytes, so two out of three cuts land inside a character." Then the snippet. A reader who does not know the codebase must be able to understand the bug from the prose alone.
- **Observed, not asserted.** Put measurements in a small table with the inputs named. Say which tokenizer, which model, which commit.
- **Expected behaviour as a rule, not a fix.** "Compacted text is valid UTF-8" rather than "use utf8.RuneStart".
- **Impact says who and whether there is a workaround.** Name the config that turns the path on.
- **Additional context carries the fix sketch and the precedent.** Point at the sibling path that already does it right.
- **Short phrases.** No paragraphs where a bullet will do.

Offer the draft as a fenced Markdown block the user can paste, with the title and any dropdown choices stated above it.

### 7. Report and reflect

Summarize: how many crossings were enumerated, how many candidates survived, how many were measured, and the top three findings in one line each. Restate that nothing was modified and that any probe tests were removed.

Then apply the reflection step from `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`. Two things from a bug scan often belong in `LESSONS.md`: a boundary that turned out to be guarded by a wrapper the grep did not show, and a heuristic constant whose real value was measured.

## Calibration

- **The shape is the deliverable, the list is the evidence.** A scan that finds nothing new but writes down the shape and the twelve call sites that are fine has still done its job. The next contributor adding a thirteenth call site now has a rule to follow.
- **One wrong number sinks the list.** A maintainer who catches one estimate presented as a measurement will discount every other line. Label estimates, and prefer running the code.
- **Sibling paths are the richest seam.** When one of two implementations of the same operation has a guard, the other one almost never does. Always compare single and batch, sync and async, API and runtime, request and response.
- **Cosmetic is still worth a line, not an issue.** Byte slicing in a log preview is real and one line to fix. Group these into one cleanup entry rather than filing each.
- **Respect deliberate discards.** Sampling the head, middle, and tail of a prompt for an intent signal is a design choice with a comment explaining it. Do not report it as a truncation bug. Do report it when a security or correctness surface copies that choice without the reasoning.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/bug-lenses.md`**: the ten lenses with search patterns, false positives, and the measurement recipes
- **`${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`**: output location and provenance footers
- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`**: the reflection step
