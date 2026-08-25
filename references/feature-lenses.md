# Feature lenses

Ten lenses for the divergent pass of `feature-scan`. Walk the repo through every lens so coverage is systematic rather than whatever a language model happens to associate with the phrase "AI agent framework".

They are ordered by **evidence strength**, not by yield. Lens 1 is somebody saying "I want this." Lens 10 is a guess about a competitor. A proposal is worth exactly as much as the lens it came from, and the write-up must name that lens so the reader can weight it.

**Every pattern here finds candidates, never conclusions.** The convergent pass is what makes a candidate a finding — above all the absence check, because the single most common way this skill embarrasses itself is proposing something the repo already shipped.

---

## 1. Stated demand

Someone opened an issue and other people reacted to it. Nothing else in this file is as trustworthy.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/find_feature_demand.py" --repo owner/name --format md
gh search issues --repo owner/name --state open 'is:issue label:enhancement sort:reactions-desc'
```

Read the thread, not just the title. A request with 40 thumbs-up and a maintainer reply saying "we will never do this in core" is a lens-2 finding, not a lens-1 one.

**False positives:** reaction count measures reach, not value — an issue linked from a popular blog post accumulates votes from people who will never use the feature. A single well-argued request from a heavy user often matters more than twenty drive-by upvotes.

## 2. Declined already

The inverse lens, and the one that protects the whole report. Before proposing anything, know what has been refused and why.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/find_feature_demand.py" --repo owner/name --format md   # "Declined" section
gh search issues --repo owner/name --state closed 'is:issue label:wontfix'
rg -n -i 'out of scope|will not|by design|not planned|as a plugin instead' CONTRIBUTING.md AGENTS.md docs/
```

Refusals come in three kinds and they are not interchangeable:

- **"Not ever"** — a design position. Do not propose it. Propose nothing adjacent to it either.
- **"Not here"** — a placement rule: ship it as a plugin, an extension, a separate repo. The feature is welcome; the location is not. This is a *real* finding, reframed.
- **"Not yet"** — blocked on something. Name the blocker, and the feature becomes a sequenced proposal.

A request that keeps reappearing and keeps getting closed is a strong signal that the *underlying need* is real even though the proposed shape was wrong. That reframing is often the most valuable output of a whole scan.

**False positives:** a `duplicate` label frequently means "already built", not "refused" — chase the link before recording it as a refusal.

## 3. Unfinished in-tree work

The repo already started it. Finishing costs a fraction of starting.

```bash
rg -n 'TODO|FIXME|XXX|HACK|for now|temporary|placeholder' -g '!test*' -g '!*.lock'
rg -n 'NotImplementedError|not implemented|unimplemented|todo!\(\)|panic!\("TODO'
rg -n -i 'coming soon|planned|future|stub|no-?op' --type md docs/ README*
```

Also look for **dark feature flags**: a flag that exists, defaults off, and gates a half-built path. And for **abandoned branches** — `git branch -r --sort=-committerdate | head -30` sometimes shows a feature that got 80% done and stalled.

**False positives:** an old `TODO` is often a note that stopped being true years ago. Check `git log -L` or `git blame` on the line before treating it as a live intention — a 2019 TODO in code rewritten in 2024 is archaeology, not a roadmap.

## 4. Asymmetric coverage

The highest-yield **derivable** lens. The repo supports a family of things and is missing an obvious member.

Enumerate the family from the tree, not from memory:

```bash
ls plugins/platforms/ tools/environments/ providers/        # whatever the family dirs are
rg -n 'register\(|SUPPORTED_|_BACKENDS|ADAPTERS =' -g '!test*'
```

Then ask what a user would reasonably expect in that list and not find. Seven container backends but no Podman. Adapters for a dozen chat platforms but not the one your users keep asking about. Import for three formats, export for one.

This lens is powerful because the answer is *checkable*: the family is a directory listing, and the gap is either there or it is not.

**False positives:** the missing member is sometimes missing on purpose — a licence conflict, a dead upstream, a maintainer who refuses to carry it. Check lens 2 before proposing, and check whether the "gap" is served by a plugin outside the tree.

**Worked false positive.** In a repo whose `tools/environments/` held `local, docker, ssh, singularity, modal, daytona, vercel_sandbox` and no `podman.py`, this lens fires immediately: seven container backends, an obvious eighth missing. The directory listing is real and the gap looks real. It is not — `docker.py:337` already resolves `podman` from `PATH` as a drop-in replacement, with an `HERMES_DOCKER_BINARY` override documented above it, and the approval layer at `tools/approval.py:1084` carries podman-specific daemon-redirect rules. A single `rg -i podman` finds all of it in seconds.

The lesson generalises: **this lens keys on file names, and capabilities are not always files.** A backend can be a branch inside a sibling, a config value, or a runtime probe. Never let a directory listing be the whole absence check.

## 5. Extension points with one implementation

An ABC, interface, hook, or registry built for many and used by one. The architecture is already inviting the feature; nobody accepted the invitation.

```bash
rg -n 'class \w+\(ABC\)|@abstractmethod|Protocol\)|interface \w+|trait \w+'
rg -n 'register_\w+\(|add_hook\(|entry_points|plugin' -g '!test*'
```

For each extension point, count implementations. One means the abstraction was speculative, or it means the second one is a small, well-scoped, obviously-fitting feature. Read the ABC's docstring — it usually names the intended second case explicitly.

**False positives:** a single-implementation ABC is sometimes there purely for testing (a fake), or to keep a dependency invertible. Neither is a gap.

## 6. Config and docs that promise more than the code delivers

A setting that parses and does nothing. A documented capability with no implementation. Both are bugs *and* features depending on how you close them.

```bash
rg -n '^[a-z_]+:' cli-config*.example* config.example* --type yaml   # every documented key
# then, for each key, confirm something reads it:
rg -n 'config\.get\("KEY"|config\["KEY"\]|KEY'
```

Compare the documentation tree against the CLI's own `--help` output. Anything present in one and absent from the other is a candidate.

**False positives:** a key may be consumed dynamically (`getattr`, a config-to-kwargs splat, a schema walk), so a missing literal grep is not proof it is unused. Confirm by tracing the load path.

## 7. Workflow discontinuity

The user can do step 1 and step 3 inside the tool, and must leave it for step 2. These gaps are invisible in the code and obvious in the docs.

Read the getting-started guide and the top-level command list as if you were a new user with a real task. Where does the happy path tell you to go somewhere else, open another program, or copy something by hand?

```bash
rg -n -i 'manually|by hand|outside of|you will need to|copy the|paste' --type md docs/ README*
```

**False positives:** some hand-offs are correct — a tool should not reimplement a text editor. The gap is real only when staying inside would remove genuine friction, not merely add surface.

## 8. Format and protocol adjacency

The repo already speaks a protocol or handles a format, and stops one short step from a neighbouring one.

- Reads a format but cannot write it (or the reverse)
- Speaks HTTP but exposes no webhook; emits events but offers no subscription
- Has structured internal state and no export path
- Supports one auth scheme where the ecosystem standard is another

```bash
rg -n 'json\.dump|yaml\.safe_load|csv\.|to_dict|serialize|export'
rg -n 'oauth|bearer|api[_-]?key|webhook|sse|websocket' -g '!test*'
```

**False positives:** "we have JSON, so we should have XML" is category-filling, not a feature. There must be a user who needs the neighbour, which sends you back to lens 1.

## 9. Reach — platforms, environments, locales

Capability that exists but is gated off for part of the audience.

```bash
rg -n 'sys\.platform|platform\.system|process\.platform|darwin|win32|linux_only|macos_only'
rg -n 'skipif|skip_on|pytest\.mark\.(windows|macos|linux)' -t py
```

Skipped tests are the best index that exists of what does not work where. A capability with a `windows_only` skip on its whole test module is a reach gap with a ready-made acceptance test.

**False positives:** genuinely platform-bound primitives (`osascript`, `/proc`, the Windows registry) cannot be ported, only abstracted — and the abstraction may cost more than the reach is worth. Say so if that is the honest read.

## 10. Peer parity

What comparable projects ship that this one does not.

**This is the weakest lens and the only one that can fabricate.** Everything above is derived from the repository; this one is derived from memory, and memory about fast-moving projects goes stale silently.

Rules for using it at all:

- Name the specific peer and the specific feature. "Competitors have better observability" is not a finding.
- Cite something checkable — a docs URL, a changelog entry, a release tag.
- If it cannot be cited, it goes to **Unverified hypotheses** or it is dropped. Never into the ranked list.
- Never present parity as sufficient justification on its own. "X has it" is a reason to investigate, never a reason to build.

**False positives:** the whole lens, when used lazily. A parity item that no user of *this* repo has asked for is a guess about a market, not a feature proposal.

---

## Applying the lenses

For each lens, ask: *is there a plausible instance here?* Record every plausible one, cheaply and without judgment. Filtering during generation suppresses candidates before they are examined.

Then, for every candidate, the convergent pass must establish four things. The first is not negotiable:

1. **It is genuinely absent.** Search the code, the config schema, the CLI `--help`, the docs, and the changelog before claiming a gap. Issue trackers lag implementations badly — a two-year-old request is often long since shipped by a PR that never closed it. Cite the searches that came back empty. *This is where a feature scan loses its credibility, and it does so on the first wrong entry.*
2. **It is wanted, or it is structurally load-bearing.** Either external demand (lens 1: an issue, reactions, a discussion) or an internal structural argument (lenses 3–6: an extension point built for it, an asymmetry that costs users). A feature with neither is an opinion.
3. **It belongs in this repository.** Read the project's own placement rules — most mature repos have them, and they are usually in `CONTRIBUTING.md` under a heading about plugins or scope. A feature that must ship as a plugin is still a finding, but it is a *different* finding, and proposing it for core is a category error the maintainers will reject on sight.
4. **The first step is concrete.** Name the module it lands in and the seam it plugs into, with a `file:line`. "Add multi-tenancy" is not a proposal; "implement `BaseEnvironment` in a new `tools/environments/podman.py`, register it in the backend table at `tools/environments/__init__.py:22`" is.

Anything failing (1) is dropped and recorded in **Already exists**, so the next scan does not re-derive it. Anything failing (2), (3), or (4) goes to **Unverified hypotheses** with a note on what could not be established — visible, but never mixed into the ranked list.
