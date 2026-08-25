---
name: issue-hunt
description: This skill should be used when the user asks to "find me an issue to work on", "what issues are workable", "find issues without PRs", "find an unclaimed issue", "what should I contribute to", "find a good first issue", or "analyze issue 1234" / "do a deep dive on this issue". It triages a repository's open issues down to those with no pull request attached, ranks them by workability, and then produces a deep implementation-ready analysis of a chosen issue.
version: 0.2.0
---

# Issue Hunt

Find open issues nobody has claimed, then analyze one deeply enough that implementing it becomes mechanical.

Runs in two phases. Phase A ranks candidates; phase B analyzes one. When the user names a specific issue number, skip straight to phase B.

## Phase A — find workable issues

### 1. Bootstrap and orient

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"
```

Read `.repo-intel/LESSONS.md` (index first) and, if present, the **Module map** section of `.repo-intel/REPO-MAP.md` — it is what makes it possible to say which issues land in code that is already understood.

### 2. Triage

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/find_workable_issues.py" --repo owner/name --limit 200 --format md
```

Defaults to the current repo's `origin` when `--repo` is omitted — **check that `origin` is not a fork with issues disabled**, and pass `--repo` explicitly when it is. Useful flags: `--top N`, `--label "good first issue"` (repeatable), `--allow-assigned`, `--allow-merged`, `--allow-contested`, `--no-outbound`, `--comment-page N`.

Write the Markdown output to `.repo-intel/issues/CANDIDATES.md`, then add a short editorial note on top: which candidates touch modules the repo map already covers, and which look worth the user's time given what they have worked on.

### What "no PR attached" actually means

Attachment is checked in **both directions**. Direction is the whole game here: GitHub records a cross-reference on the **target's** timeline, never on the source's. An issue whose thread says "related to #92030" therefore leaves *no trace on itself* — and inbound-only checking reports it as pristine.

**Inbound** — PRs that point at the issue:

- **`closedByPullRequestsReferences`** — PRs linked through the Development sidebar or a closing keyword (`fixes #123`)
- **timeline cross-references** — PRs that merely mention the issue

**Outbound** — numbers the issue itself points at, read from its own body and its most recent comments (`--comment-page`, default 20), then batch-resolved to find which are PRs and what state they are in.

An issue is excluded when a PR in **this repository** is `OPEN` (someone is working on it) or `MERGED` (effectively done), in either direction. Also excluded: assigned issues, and blocking labels (`wontfix`, `duplicate`, `question`, `needs-info`, `blocked`, `discussion`, `epic`).

Why outbound matters, measured on `NousResearch/hermes-agent` (12.1k open issues, 400 scanned): **6 of 30 issues that inbound-only checking called workable were not.** Four had competing open PRs named only in their own comment threads — including one where a collaborator's triage note listed three (`#92030`, `#88025`, `#18834`) attacking the exact code path, while `closedByPullRequestsReferences` was empty and the timeline held a single entry that was an *Issue*, not a PR. Two more were already fixed by merged PRs that nothing pointed inward from.

Four traps the script handles, all verified against live data — do not "fix" them by loosening the checks:

1. **A cross-reference from another *Issue*** returns a `source` carrying only `__typename: "Issue"`. Treating any non-empty `source` as a PR excludes every issue that another issue mentions.
2. **A cross-reference from a PR in an unrelated repository.** Bots and template repos generate these constantly — one `cli/cli` issue carried eleven, including an open PR in `podman-desktop/e2e`. Only same-repo PRs count as attached; foreign ones are recorded under `foreign_references` and excluded from the decision.
3. **`#123` inside pasted logs, diffs, and stack traces.** Bug reports are full of them. Fenced blocks and inline code spans are stripped before scanning, or a report quoting `#999999` from a log would exclude itself.
4. **One unresolvable number must not sink its batch.** References are resolved in aliased batches; a number that resolves to nothing (`#49` in prose, a deleted item) returns a per-alias `NOT_FOUND` *alongside every alias that did resolve*, while `gh` exits non-zero. Parse the partial payload and ignore `NOT_FOUND`. Discarding the batch on exit code alone cost 30% of all resolutions in testing (496 vs 714).

**Kept and flagged:** an issue whose only linked PR was *closed without merging*. These score higher, not lower — but see the warning in phase B.

**Kept and penalised (`-25`, flagged `CONTESTED`):** reachable only via `--allow-contested`, which keeps issues whose thread names an open PR. Use it when surveying a crowded area on purpose; the default excludes them.

### 3. Present

Show the top candidates as a short table: number, title, score, why it scored that way, and any flags. Say plainly which one is recommended and why. Then offer phase B.

## Phase B — deep analysis

Produce `.repo-intel/issues/<number>-analysis.md` following `${CLAUDE_PLUGIN_ROOT}/references/issue-analysis-template.md`.

### 1. Read the issue completely

```bash
gh issue view <number> --repo owner/name --comments
```

Read every comment. **Maintainer comments define what will actually be merged** — the reporter states what hurts, the maintainer states the acceptable fix. When the body and a maintainer comment disagree, the maintainer wins.

### 2. Reproduce

Run the reported commands. Mark the result honestly: confirmed, partially confirmed, or not reproduced with the reason. Never present an unrun reproduction as confirmed — "not reproduced, because this needs an Enterprise host" is a legitimate and useful finding.

### 3. Trace the root cause

Work from symptom to cause with real line references, using the repo map for orientation and `rg` for call sites. State the defect as a specific claim about specific code.

If the cause cannot be found, say so and list what was ruled out. A confidently wrong root cause costs more than an absent one, because it sends the implementer down a dead end.

### 4. Check prior art — do not skip this

```bash
gh pr view <closed-pr-number> --repo owner/name --comments
git log -S "<relevant symbol>" --oneline -- <path>
```

For any issue flagged with a closed-unmerged PR, find out **why it did not land**. An issue with three abandoned PRs is not unclaimed easy work — it is work that keeps failing review, and the reason is almost always recorded in the review comments. Recommending an approach without reading them repeats the failure.

### 5. Blast radius

Sweep for call sites with `rg` rather than reasoning from memory, and list what else depends on the code being changed.

### 6. Options, tests, constraints

Give two or three real fix options with honest trade-offs and a recommendation. Identify the existing tests that cover the area (and the gap that let the bug ship), plus the new tests needed. Read `CONTRIBUTING.md` for sign-off/CLA requirements, commit conventions, and lint gates — read it, do not assume it.

### 7. Reflect

Apply the reflection step from `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`, then summarize for the user in a few lines: the root cause, the recommended option, and the first concrete step.

## Scope

This skill analyzes; it does not implement or open pull requests. Implementation is a separate, explicit request.

Works on any repository the `gh` token can read. Requires the GitHub CLI authenticated (`gh auth status`). GraphQL cost is modest — scanning 200 issues costs roughly 10 points of the 5,000/hour budget.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/issue-analysis-template.md`** — full analysis structure with a worked example
- **`${CLAUDE_PLUGIN_ROOT}/scripts/find_workable_issues.py`** — `--repo`, `--limit`, `--top`, `--label`, `--format {json,md}`, `--allow-assigned`, `--allow-merged`
- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`** — the reflection step
