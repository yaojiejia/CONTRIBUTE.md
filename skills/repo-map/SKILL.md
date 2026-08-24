---
name: repo-map
description: This skill should be used when the user asks to "map this repo", "help me understand this codebase", "what does this repo do", "onboard me to this project", "explain the architecture", "give me a tour of this code", or drops into an unfamiliar repository and needs to get oriented quickly. It generates .repo-intel/REPO-MAP.md — a comprehensive but scannable map covering architecture, module responsibilities, verified build and test commands, primary code paths, hot spots, and gotchas.
version: 0.1.0
---

# Repo Map

Generate `.repo-intel/REPO-MAP.md`: a map of a repository that makes it legible in about ninety seconds, and that later `repo-intel` runs use as their orientation layer.

The goal is not a description of every file. It is the answer to *what is this, how is it put together, how do I run it, and where do I start* — with every claim anchored to a real file and line.

## Procedure

### 1. Bootstrap and read prior knowledge

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/intel_init.sh"
```

Returns JSON with `repo_root`, `head_sha`, `slug`, and an `artifacts` map. If `ok` is false, the directory is not a git repository — say so and stop.

Then read `.repo-intel/LESSONS.md` if it exists — **index section only** — and read in full any entry whose subject bears on mapping this repo. Prior corrections outrank fresh inference.

### 2. Decide: fresh generation or incremental update

If `artifacts.repo_map` is true, read the provenance footer of the existing `REPO-MAP.md` for its recorded commit, then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_survey.py" --since <recorded-sha>
```

The `since` block reports commits and files changed. If nothing relevant moved, say the map is current and stop — do not rewrite a file only to change its timestamp. Otherwise update the affected sections and preserve everything else, including any section that looks hand-edited.

### 3. Run the survey

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_survey.py" > /tmp/survey.json
```

It emits deterministic facts — language breakdown, directory shape, parsed manifests, candidate build/test/lint commands with their sources, entrypoints, CI workflows and the commands they actually run, test layout, docs, and git history signal (commit count, date range, top contributors, churn, files touched in the last 90 days).

On a large repo the JSON runs tens of kilobytes. Read it selectively rather than whole:

```bash
python3 -c "import json;d=json.load(open('/tmp/survey.json'));print(json.dumps(d['commands'],indent=1))"
```

**Do not re-derive by hand what the survey already reports.** It is faster and it is right about line counts, churn, and manifest contents.

### 4. Verify the commands — do not guess them

The single highest-value and most-often-wrong section of the map. The survey reports *candidate* commands with `"verified": false`. Resolve them:

1. **Trust CI over manifests.** A `.github/workflows/*.yml` `run:` step is what the project actually executes; a `package.json` script may be a stale alias. When they disagree, CI wins and the map should say so.
2. **Watch for delegation.** A root `test` script that shells out to turbo, nx, tox, or make means the real command lives elsewhere. The survey flags no-op script bodies with a `warning` field — take it seriously.
3. **Run what is cheap and safe.** Lint and unit tests are usually fine. Do not run anything that installs globally, mutates a database, hits a paid API, or takes many minutes. Never run a `publish`, `deploy`, or `release` target.
4. **Label honestly.** Every command in the map is marked *verified* (it ran and succeeded) or *unverified* (it was not run, and why). An unverified command presented as fact is exactly the error that later becomes a `LESSONS.md` entry.

### 5. Read the code

The survey says where the weight is; reading is what turns it into a map. Prioritize:

- entrypoints from the survey, followed outward one or two hops
- the top-churn files — where change concentrates is where the design lives
- module `__init__.py` / `index.ts` / `mod.rs` / `doc.go` files, which state intent
- config and schema definitions, which encode the domain model
- one representative test per major module — tests state the contract more honestly than docs

Read enough to describe how the pieces connect. Stop when new files stop changing the picture.

**Scale rule:** past roughly 2,000 files, map at module granularity and expand only the modules the survey ranks as central by churn and fan-in. Note explicitly which areas were not examined.

### 6. Write REPO-MAP.md

Follow `${CLAUDE_PLUGIN_ROOT}/references/repo-map-template.md` exactly — the section order is fixed, because `repo-lessons` promotes entries into `## Gotchas` by heading name and later runs navigate by heading.

Sections: **What this is** · **Fast facts** · **60-second orientation** · **Architecture** (prose plus a mermaid diagram) · **Module map** · **Primary flows** · **Key abstractions & invariants** · **Hot spots** · **Gotchas** · **Open questions** · provenance footer.

Two sections carry most of the value:

- **60-second orientation** — a table of "to change X, start at `file:line`" for the five to eight most likely tasks. Write it last, once the code is actually understood. A map that describes structure without saying where to start is a table of contents.
- **Primary flows** — one to three end-to-end paths traced with real line numbers. If the line numbers cannot be produced, omit the section; an approximate trace is worse than none because it cannot be checked.

Never leave **Open questions** empty on a first pass. Anything fully understood on a first read was a trivial repo.

### 7. Reflect

Apply the reflection step from `${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`: if something turned out other than expected — a command that failed, a structure that contradicted its own naming, a config silently overridden — record it via the `repo-lessons` protocol. If nothing did, write nothing.

Then report to the user in a few lines: where the map was written, the verified build/test commands, and the two or three things most worth knowing about the repo.

## Accuracy rules

These are what separate a useful map from a plausible one:

- **Cite `file:line` for every structural claim.** An uncited claim is a guess and must be marked as one.
- **Distinguish observed from inferred.** Use `(inferred)` liberally. The next session inherits this file and cannot tell the difference otherwise.
- **Describe the repo that exists**, including its inconsistencies and dead corners. A tidy diagram of an untidy system misleads more than no diagram.
- **Do not invent structure that would be reasonable.** Only what is there.

## Additional resources

- **`${CLAUDE_PLUGIN_ROOT}/references/repo-map-template.md`** — full section-by-section template with a worked example
- **`${CLAUDE_PLUGIN_ROOT}/references/artifact-conventions.md`** — `.repo-intel/` layout, provenance footers, incremental updates
- **`${CLAUDE_PLUGIN_ROOT}/references/lessons-protocol.md`** — the reflection step and entry schema
- **`${CLAUDE_PLUGIN_ROOT}/scripts/repo_survey.py`** — `--repo PATH`, `--since SHA`; standard library only
