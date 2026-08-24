# CONTRIBUTE.md

A Claude Code plugin (`repo-intel`) for working in repositories you did not write.

Dropping into an unfamiliar codebase costs the same work every time: re-deriving the architecture, re-guessing the build and test commands, re-discovering the same gotchas, and re-scanning the issue tracker for something actually workable. Nothing accumulates between sessions, so the same wrong assumptions get made again.

`repo-intel` turns that repeated work into durable artifacts that live in the repo.

## The four skills

| Skill | What it does | Output |
|---|---|---|
| **`repo-map`** | Surveys the repo and writes a map: architecture, module responsibilities, **verified** build/test commands, primary code paths, hot spots, gotchas | `.repo-intel/REPO-MAP.md` |
| **`repo-lessons`** | A ledger of corrections, read before every run and appended to when something turns out wrong — so confusion is resolved once, not every session | `.repo-intel/LESSONS.md` |
| **`issue-hunt`** | Triages open issues down to those with **no pull request attached**, ranks them by workability, then analyzes one deeply enough to make implementation mechanical | `.repo-intel/issues/` |
| **`opt-scan`** | Walks every module through twelve optimization lenses, verifies each candidate against real code, and ranks the survivors by impact, effort, and risk | `.repo-intel/optimizations/` |

All four read `LESSONS.md` before starting and record a lesson when something turns out other than expected. That loop is the point: the plugin gets better at a repo the more it is used there.

## Install

```bash
git clone https://github.com/yaojiejia/CONTRIBUTE.md.git
```

Then in Claude Code:

```
/plugin marketplace add /path/to/CONTRIBUTE.md
/plugin install repo-intel@contribute-md
```

The skills then trigger on natural requests — "map this repo", "find me an issue to work on", "what can be optimized here" — or explicitly as `/repo-intel:repo-map`, `/repo-intel:issue-hunt`, `/repo-intel:opt-scan`, `/repo-intel:repo-lessons`.

## Where the output goes

Everything lands in `.repo-intel/` at the target repo's root:

```
<target-repo>/.repo-intel/
├── REPO-MAP.md
├── LESSONS.md
├── issues/CANDIDATES.md, issues/<num>-analysis.md
└── optimizations/OPT-<date>.md
```

The directory is hidden via `.git/info/exclude`, not the tracked `.gitignore` — so the artifacts never appear in `git status`, a diff, or a pull request, and the target repo is never modified.

## Requirements

- `git` and `python3` (standard library only — no pip install)
- `gh`, authenticated, for `issue-hunt`
- `rg` recommended

## Design notes

**"No PR attached" is checked two ways.** GitHub exposes development-linked PRs (`closedByPullRequestsReferences`) and timeline cross-references separately, and either alone misses cases. Two traps that a naive implementation falls into, both found against live data and handled here:

- A cross-reference from another *Issue* returns a `source` with only `__typename: "Issue"`. Treating any non-empty source as a PR excludes every issue that another issue mentions.
- A cross-reference from a PR in an *unrelated repository*. Bots and template repos generate these constantly — one `cli/cli` issue carried eleven, including an open PR in `podman-desktop/e2e`. Only same-repo PRs count as attached.

**Commands are verified, not guessed.** A root `test` script that delegates to turbo, tox, or make is the single most common source of wrong assumptions about a repo. `repo-map` prefers what CI actually runs, flags no-op script bodies, and labels every command `verified` or `unverified`.

**Optimization findings must resolve to real code.** `opt-scan` separates brainstorming from verification, and anything that cannot be traced to a `file:line` on a path that matters goes to a visible "Unverified hypotheses" section rather than into the ranked list. One plausible-sounding finding about code that does not exist makes a reader distrust the whole report.

**The lessons ledger is deduplicated and promoted.** Append-only logs grow until nobody reads them. New entries sharpen existing ones rather than stacking beside them, and stable rules are promoted into the repo map's Gotchas section.

## License

MIT
