#!/usr/bin/env python3
"""find_workable_issues.py — open issues that have no pull request attached.

Queries the GitHub GraphQL API through `gh`, filters out every issue that already
has someone working on it, scores what remains for workability, and emits a ranked
list as JSON or Markdown.

Two independent signals identify an attached PR, and both are needed:
  * closedByPullRequestsReferences — PRs linked through the "Development" sidebar
    or a closing keyword ("fixes #123")
  * timelineItems cross-references  — PRs that merely mention the issue

Usage:
    python3 find_workable_issues.py [--repo owner/name] [--limit N] [--format md]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

QUERY = """
query($owner:String!, $name:String!, $cursor:String, $page:Int!, $comments:Int!) {
  rateLimit { cost remaining }
  repository(owner:$owner, name:$name) {
    issues(first:$page, states:OPEN, after:$cursor,
           orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      totalCount
      nodes {
        number title url body createdAt updatedAt authorAssociation
        comments(last:$comments) {
          totalCount
          nodes { body authorAssociation author { login } }
        }
        reactions { totalCount }
        labels(first:20) { nodes { name } }
        assignees(first:5) { nodes { login } }
        author { login }
        milestone { title }
        closedByPullRequestsReferences(first:10, includeClosedPrs:true) {
          nodes { number state isDraft url createdAt repository { nameWithOwner } }
        }
        timelineItems(first:50, itemTypes:[CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
          nodes {
            __typename
            ... on CrossReferencedEvent {
              willCloseTarget
              source { __typename ... on PullRequest { number state isDraft url createdAt repository { nameWithOwner } } }
            }
            ... on ConnectedEvent {
              subject { __typename ... on PullRequest { number state isDraft url createdAt repository { nameWithOwner } } }
            }
          }
        }
      }
    }
  }
}
"""

# Labels that mean the issue is not actionable regardless of how it scores.
BLOCKING_LABELS = {
    "wontfix", "won't fix", "duplicate", "invalid", "question", "support",
    "needs-info", "needs more info", "needs-more-information", "awaiting-response",
    "needs author feedback", "blocked", "on hold", "discussion", "rfc", "proposal",
    "epic", "meta", "tracking", "spike",
}

POSITIVE_LABELS = {
    "good first issue": 30, "good-first-issue": 30, "beginner friendly": 30,
    "help wanted": 25, "help-wanted": 25, "help wanted candidate": 15,
    "bug": 12, "confirmed": 12, "accepted": 15, "ready": 15,
    "enhancement": 5, "feature": 5, "documentation": 8, "docs": 8,
    "p1": 10, "p2": 5, "priority: high": 10,
}

NEGATIVE_LABELS = {
    "stale": -15, "needs-triage": -8, "needs triage": -8, "triage": -8,
    "breaking change": -15, "needs design": -25, "design": -15,
    "upstream": -20, "external": -20, "wontfix-candidate": -20,
}

REPRO_MARKERS = [
    r"```", r"^\s*(steps to reproduce|to reproduce|reproduction|repro)\b",
    r"^\s*(expected|actual)\s+(behaviou?r|result)", r"\$ ", r"Traceback \(most recent",
    r"^\s*\d+\.\s", r"error:", r"panic:", r"exception",
]


def die(msg, code=1):
    json.dump({"ok": False, "error": msg}, sys.stdout)
    print()
    sys.exit(code)


def run(args, timeout=120, stdin=None):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, input=stdin)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        die("`gh` not found on PATH. Install the GitHub CLI and run `gh auth login`.")
    except subprocess.TimeoutExpired:
        return 1, "", "timed out"


def detect_repo():
    """Fall back to the origin remote of the repository in the working directory."""
    rc, out, _ = run(["git", "config", "--get", "remote.origin.url"], timeout=15)
    if rc != 0 or not out.strip():
        return None
    slug = re.sub(r"\.git$", "",
                  re.sub(r"^(git@[^:]+:|ssh://[^/]+/|https?://[^/]+/)", "", out.strip()))
    return slug if "/" in slug else None


def link_strength(pr, issue_created):
    """How strongly a linked PR indicates someone is working on this issue.

    strong  — an explicit link: the Development sidebar, or a closing keyword.
    weak    — the PR merely mentions the issue, but was opened after it existed.
    none    — the PR predates the issue, so it cannot be work on it. Issues that
              discuss an existing PR ("after #4253, X still breaks") land here.
    """
    if pr.get("via") == "development-link" or pr.get("will_close"):
        return "strong"
    pr_created, issue_created = pr.get("createdAt") or "", issue_created or ""
    if pr_created and issue_created and pr_created < issue_created:
        return "none"
    return "weak"


def linked_prs(issue):
    """Every PR attached to an issue, from both signals, de-duplicated by number.

    A cross-reference can come from another *Issue*, in which case `source` carries
    only `__typename: "Issue"`. Filtering on object non-emptiness instead of on the
    typename wrongly excludes every issue that another issue mentions.
    """
    found = {}

    def home(node):
        return ((node.get("repository") or {}).get("nameWithOwner") or "").lower()

    for pr in (issue.get("closedByPullRequestsReferences") or {}).get("nodes") or []:
        if pr and pr.get("number"):
            found[pr["number"]] = {**pr, "via": "development-link", "repo": home(pr)}

    for ev in (issue.get("timelineItems") or {}).get("nodes") or []:
        node = ev.get("source") if ev.get("__typename") == "CrossReferencedEvent" else ev.get("subject")
        if not node or node.get("__typename") != "PullRequest":
            continue  # cross-reference from an Issue, not a PR
        num = node.get("number")
        if num is None:
            continue
        if num not in found:
            found[num] = {**node, "via": "cross-reference", "repo": home(node),
                          "will_close": bool(ev.get("willCloseTarget"))}
    return list(found.values())


# A reference the issue makes *outward* — "related to #92030" in a comment — never
# appears in that issue's own timeline. GitHub records the cross-reference on the
# TARGET's timeline instead. An issue can therefore have three open PRs actively
# working its code path and still report closedByPullRequestsReferences: [] and an
# empty timeline. Inbound signals alone cannot see this; these two functions add
# the outbound direction.

FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LOG_TIMESTAMP = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}", re.M)

REF_HASH = re.compile(r"(?<![\w/])#(\d{1,7})\b")
REF_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/(?:issues|pull)/(\d+)")


def strip_code(text):
    """Drop fenced blocks and inline spans before scanning for `#123`.

    Bug reports paste logs and diffs constantly, and those carry `#` sequences
    that are not issue references. Scanning raw text produces false positives
    that would wrongly exclude a genuinely unclaimed issue.
    """
    return INLINE_CODE.sub(" ", FENCED_CODE.sub(" ", text or ""))


def outbound_references(issue, this_repo):
    """Issue numbers this issue points at, from its own body and comments.

    Returns {number: {"where": "body"|"comment", "author_assoc": str}}. Only
    same-repo references are collected; a full URL to another repository is
    ignored, matching how inbound foreign references are already treated.
    """
    found = {}

    def scan(text, where, assoc):
        text = strip_code(text)
        text = LOG_TIMESTAMP.sub(" ", text)
        nums = {int(m.group(1)) for m in REF_HASH.finditer(text)}
        for m in REF_URL.finditer(text):
            if m.group(1).lower() == this_repo:
                nums.add(int(m.group(2)))
        for n in nums:
            if n == issue.get("number"):
                continue
            prev = found.get(n)
            # A maintainer pointing at a PR outranks a drive-by mention.
            if prev is None or _assoc_rank(assoc) > _assoc_rank(prev["author_assoc"]):
                found[n] = {"where": where, "author_assoc": assoc}
    scan(issue.get("body"), "body", issue.get("authorAssociation") or "NONE")
    for c in (issue.get("comments") or {}).get("nodes") or []:
        scan(c.get("body"), "comment", c.get("authorAssociation") or "NONE")
    return found


_ASSOC_ORDER = ["NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "CONTRIBUTOR",
                "COLLABORATOR", "MEMBER", "OWNER"]


def _assoc_rank(assoc):
    try:
        return _ASSOC_ORDER.index((assoc or "NONE").upper())
    except ValueError:
        return 0


RESOLVE_QUERY_HEAD = "query($owner:String!, $name:String!) {\n  rateLimit { cost }\n  repository(owner:$owner, name:$name) {\n"
RESOLVE_FIELDS = ("""    r%d: issueOrPullRequest(number:%d) {
      __typename
      ... on PullRequest { number state isDraft url createdAt title }
    }
""")


def resolve_references(repo, numbers, chunk=80):
    """Batch-resolve referenced numbers to PR state. Issues resolve to None.

    One aliased query per `chunk` numbers, so a 400-issue scan costs a handful of
    extra requests rather than one per reference.
    """
    owner, name = repo.split("/", 1)
    out, cost = {}, 0
    nums = sorted(numbers)
    for i in range(0, len(nums), chunk):
        batch = nums[i:i + chunk]
        q = RESOLVE_QUERY_HEAD + "".join(RESOLVE_FIELDS % (n, n) for n in batch) + "  }\n}"
        rc, raw, err = run(["gh", "api", "graphql", "-f", f"query={q}",
                            "-F", f"owner={owner}", "-F", f"name={name}"], timeout=120)
        # A batch mixes real references with numbers that resolve to nothing —
        # "#49" in prose, a reference to a deleted item. GitHub answers those with
        # a per-alias NOT_FOUND *and still returns every alias that did resolve*,
        # while `gh` exits non-zero. Discarding the batch on rc alone throws away
        # up to `chunk` good resolutions for one bad number, so parse regardless
        # and only complain about errors that are not NOT_FOUND.
        data = None
        if raw.strip():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
        if data is None:
            sys.stderr.write(f"warning: reference resolution failed: {err.strip()[:200]}\n")
            continue
        other = [e for e in (data.get("errors") or [])
                 if (e.get("type") or "").upper() != "NOT_FOUND"]
        if other:
            sys.stderr.write(f"warning: reference resolution partial: {json.dumps(other)[:200]}\n")
        node = (data.get("data") or {}).get("repository") or {}
        cost += ((data.get("data") or {}).get("rateLimit") or {}).get("cost", 0)
        for n in batch:
            v = node.get(f"r{n}")
            if v and v.get("__typename") == "PullRequest":
                out[n] = v
    return out, cost


def has_repro(body):
    if not body:
        return False
    return any(re.search(p, body, re.M | re.I) for p in REPRO_MARKERS)


def days_since(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


def triage(issue, policy):
    """Return (verdict, reasons, score, signals). verdict in keep|exclude."""
    labels = [l["name"].lower() for l in (issue.get("labels") or {}).get("nodes") or []]
    assignees = [a["login"] for a in (issue.get("assignees") or {}).get("nodes") or []]
    # The same PR can arrive from both directions. Keep one entry per number,
    # preferring the inbound record: it carries the stronger provenance
    # (development-link / willCloseTarget) that link_strength reads.
    _by_number = {}
    for _pr in linked_prs(issue) + (issue.get("_outbound_prs") or []):
        _by_number.setdefault(_pr.get("number"), _pr)
    all_prs = list(_by_number.values())
    body = issue.get("body") or ""

    # A cross-reference can come from a PR in a completely unrelated repository
    # (bots and template repos do this constantly). Only same-repo PRs mean the
    # issue is being worked on; foreign ones are recorded but never exclude.
    this_repo = policy["repo"].lower()
    same_repo = [p for p in all_prs if p.get("repo") in ("", this_repo)]
    foreign = [p for p in all_prs if p.get("repo") not in ("", this_repo)]

    issue_created = issue.get("createdAt")
    for p in same_repo:
        p["strength"] = link_strength(p, issue_created)
    # A PR opened before the issue existed is not work on that issue.
    predates = [p for p in same_repo if p["strength"] == "none"]
    prs = [p for p in same_repo if p["strength"] != "none"]

    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    merged_prs = [p for p in prs if p.get("state") == "MERGED"]
    closed_prs = [p for p in prs if p.get("state") == "CLOSED"]

    # --- hard exclusions -------------------------------------------------
    if open_prs and not (policy.get("allow_contested")
                         and all(p.get("direction") == "outbound" for p in open_prs)):
        p = sorted(open_prs, key=lambda x: (x.get("direction") == "outbound",
                                            x["strength"] != "strong"))[0]
        draft = " (draft)" if p.get("isDraft") else ""
        if p.get("direction") == "outbound":
            others = [q for q in open_prs if q["number"] != p["number"]]
            more = f" (+{len(others)} more: " + ", ".join(f"#{q['number']}" for q in others) + ")" if others else ""
            how = (f"referenced from the issue's {p['where']} by a "
                   f"{p['author_assoc'].lower().replace('_', ' ')} — outbound link, "
                   f"absent from the timeline{more}")
        elif p["strength"] == "strong":
            how = "attached via " + p["via"]
        else:
            how = "mentions this issue (opened after it; no closing keyword)"
        return "exclude", [f"open PR #{p['number']}{draft} {how}"], 0, {}
    if merged_prs and not policy["allow_merged"]:
        return "exclude", [f"merged PR #{merged_prs[0]['number']} attached — likely already fixed"], 0, {}
    if assignees and not policy["allow_assigned"]:
        return "exclude", [f"assigned to {', '.join(assignees)}"], 0, {}
    blocked = [l for l in labels if l in BLOCKING_LABELS]
    if blocked:
        return "exclude", [f"blocking label: {', '.join(blocked)}"], 0, {}

    # --- scoring ---------------------------------------------------------
    score, reasons = 0, []

    for l in labels:
        if l in POSITIVE_LABELS:
            score += POSITIVE_LABELS[l]
            reasons.append(f"+{POSITIVE_LABELS[l]} label `{l}`")
        if l in NEGATIVE_LABELS:
            score += NEGATIVE_LABELS[l]
            reasons.append(f"{NEGATIVE_LABELS[l]} label `{l}`")

    repro = has_repro(body)
    if repro:
        score += 20
        reasons.append("+20 body contains reproduction detail (code block / steps / traceback)")
    elif len(body) < 200:
        score -= 12
        reasons.append("-12 body is very short — likely underspecified")

    n_comments = (issue.get("comments") or {}).get("totalCount", 0)
    if n_comments == 0:
        score -= 6
        reasons.append("-6 no comments — untriaged, maintainer intent unknown")
    elif n_comments <= 10:
        score += 10
        reasons.append(f"+10 {n_comments} comments — discussed")
    elif n_comments <= 30:
        reasons.append(f"+0 {n_comments} comments")
    else:
        score -= 20
        reasons.append(f"-20 {n_comments} comments — long thread suggests contested design")

    reactions = (issue.get("reactions") or {}).get("totalCount", 0)
    if reactions:
        bump = min(reactions, 10)
        score += bump
        reasons.append(f"+{bump} {reactions} reactions — demand signal")

    age = days_since(issue.get("updatedAt", ""))
    if age <= 30:
        score += 15
        reasons.append("+15 updated within 30 days")
    elif age <= 90:
        score += 8
        reasons.append("+8 updated within 90 days")
    elif age > 730:
        score -= 15
        reasons.append("-15 untouched for over two years — may no longer apply")

    if issue.get("milestone"):
        score += 8
        reasons.append(f"+8 milestone `{issue['milestone']['title']}` — maintainers plan to ship it")

    flags = []
    contested = [p for p in open_prs if p.get("direction") == "outbound"]
    if contested:
        nums = ", ".join(f"#{p['number']}" for p in contested)
        score -= 25
        reasons.append(f"-25 contested: open PR(s) {nums} referenced from the issue "
                       f"thread — competing work in flight")
        flags.append(f"CONTESTED — open PRs referenced in thread: {nums}")
    if closed_prs:
        score += 10
        nums = ", ".join(f"#{p['number']}" for p in closed_prs)
        reasons.append(f"+10 prior attempt abandoned ({nums}) — a path exists, read it first")
        flags.append(f"prior attempt closed unmerged: {nums}")

    signals = {
        "labels": labels,
        "comments": n_comments,
        "reactions": reactions,
        "days_since_update": age,
        "has_repro": repro,
        "linked_prs": prs,
        "foreign_references": [f"{p['repo']}#{p['number']}" for p in foreign],
        "predates_issue": [f"#{p['number']}" for p in predates],
        "outbound_prs": [f"#{p['number']} ({p.get('state','?').lower()})"
                         for p in prs if p.get("direction") == "outbound"],
        "flags": flags,
    }
    return "keep", reasons, score, signals


def fetch(repo, limit, page_size, comment_page=20):
    owner, name = repo.split("/", 1)
    issues, cursor, cost = [], None, 0
    while len(issues) < limit:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}",
                "-F", f"owner={owner}", "-F", f"name={name}",
                "-F", f"page={min(page_size, limit - len(issues))}",
                "-F", f"comments={comment_page}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        rc, out, err = run(args)
        if rc != 0:
            die(f"gh api graphql failed: {err.strip()[:400]}")
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            die(f"unparseable response from gh: {out[:300]}")
        if data.get("errors"):
            die(f"GraphQL error: {json.dumps(data['errors'])[:400]}")
        repo_node = (data.get("data") or {}).get("repository")
        if not repo_node:
            die(f"repository not found or not accessible: {repo}")
        cost += (data["data"].get("rateLimit") or {}).get("cost", 0)
        conn = repo_node["issues"]
        issues.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return issues[:limit], conn["totalCount"], cost


def to_markdown(result):
    r = result
    lines = [f"# Workable issues — {r['repo']}", ""]
    lines.append(f"Open issues scanned: **{r['scanned']}** of {r['total_open']} open. "
                 f"Candidates with no PR attached: **{len(r['candidates'])}**.")
    lines.append("")
    lines.append("An issue is excluded when a pull request is already attached to it "
                 "(open, or merged), when it is assigned to someone, or when it carries a "
                 "blocking label. Issues whose only linked PR was *closed without merging* "
                 "are kept and flagged — the prior attempt is usually worth reading.")
    lines.append("")
    lines.append("Attachment is checked in **both directions**. Inbound: the Development "
                 "sidebar and timeline cross-references, i.e. PRs that point at the issue. "
                 "Outbound: `#123` references in the issue's own body and most recent "
                 "comments, which GitHub records on the *target's* timeline and never on "
                 "this issue — so an issue with competing PRs in flight can otherwise show "
                 "no link at all. Code blocks are stripped before scanning so pasted logs "
                 "and diffs do not register as references.")
    lines.append("")
    if not r["candidates"]:
        if r["total_open"] == 0:
            lines.append("**This repository has no open issues.** (GitHub's REST "
                         "`open_issues_count` also counts pull requests, so a non-zero "
                         "number there does not contradict this.)")
        else:
            lines.append("**No workable issues found in the scanned window.** Every open "
                         "issue examined already has a pull request attached, is assigned, "
                         "or carries a blocking label. Try a larger `--limit`, or "
                         "`--allow-assigned` to include claimed issues.")
        lines.append("")
        if r["excluded"]:
            lines.append("## Excluded (audit trail)")
            lines.append("")
            lines.append("| # | Issue | Reason |")
            lines.append("|---|---|---|")
            for e in r["excluded"][:60]:
                lines.append(f"| [{e['number']}]({e['url']}) | {e['title'][:60]} "
                             f"| {'; '.join(e['reasons'])} |")
        return "\n".join(lines)
    lines.append("| # | Score | Issue | Labels | Comments | Updated | Flags |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in r["candidates"]:
        s = c["signals"]
        labels = ", ".join(f"`{l}`" for l in s["labels"][:4]) or "—"
        flags = "; ".join(s["flags"]) or ""
        lines.append(
            f"| [{c['number']}]({c['url']}) | {c['score']} | {c['title'][:70]} | {labels} "
            f"| {s['comments']} | {s['days_since_update']}d ago | {flags} |")
    lines.append("")
    lines.append("## Why each candidate scored as it did")
    lines.append("")
    for c in r["candidates"][:15]:
        lines.append(f"### #{c['number']} — {c['title']}")
        lines.append(f"{c['url']}")
        for reason in c["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")
    lines.append("## Excluded (audit trail)")
    lines.append("")
    lines.append("| # | Issue | Reason |")
    lines.append("|---|---|---|")
    for e in r["excluded"][:60]:
        lines.append(f"| [{e['number']}]({e['url']}) | {e['title'][:60]} | {'; '.join(e['reasons'])} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="owner/name (default: origin of the current repo)")
    ap.add_argument("--limit", type=int, default=200, help="open issues to scan (default 200)")
    ap.add_argument("--page-size", type=int, default=50)
    ap.add_argument("--top", type=int, default=25, help="candidates to keep (default 25)")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    ap.add_argument("--allow-assigned", action="store_true",
                    help="keep issues already assigned to someone")
    ap.add_argument("--allow-merged", action="store_true",
                    help="keep issues whose linked PR was merged")
    ap.add_argument("--allow-contested", action="store_true",
                    help="keep issues whose thread references an open PR (outbound link)")
    ap.add_argument("--no-outbound", action="store_true",
                    help="skip the outbound-reference pass (faster, misses competing PRs)")
    ap.add_argument("--comment-page", type=int, default=20,
                    help="most-recent comments to scan per issue for references (default 20)")
    ap.add_argument("--label", action="append", default=[],
                    help="only keep issues carrying this label (repeatable)")
    args = ap.parse_args()

    repo = args.repo or detect_repo()
    if not repo or "/" not in repo:
        die("could not determine the repository. Pass --repo owner/name.")

    issues, total_open, cost = fetch(repo, args.limit, args.page_size, args.comment_page)

    # Outbound pass. Inbound signals (Development sidebar, timeline) only see PRs
    # that point AT the issue. A maintainer writing "related to #92030" in the
    # issue's own thread creates no inbound trace at all, so an issue with three
    # open PRs on its code path can look pristine. Collect every number the issue
    # points at, then batch-resolve which of them are open PRs.
    this_repo = repo.lower()
    wanted = set()
    for iss in issues:
        refs = outbound_references(iss, this_repo)
        iss["_outbound_refs"] = refs
        wanted.update(refs)

    ref_index = {}
    if wanted and not args.no_outbound:
        ref_index, ref_cost = resolve_references(repo, wanted)
        cost += ref_cost

    for iss in issues:
        prs = []
        for num, meta in (iss.get("_outbound_refs") or {}).items():
            node = ref_index.get(num)
            if not node:
                continue  # resolved to an Issue, or not resolved at all
            prs.append({**node, "via": f"{meta['where']}-reference", "repo": this_repo,
                        "will_close": False, "direction": "outbound",
                        "where": meta["where"], "author_assoc": meta["author_assoc"]})
        iss["_outbound_prs"] = prs

    policy = {"allow_assigned": args.allow_assigned, "allow_merged": args.allow_merged,
              "allow_contested": args.allow_contested, "repo": repo}

    candidates, excluded = [], []
    want = {l.lower() for l in args.label}
    for iss in issues:
        verdict, reasons, score, signals = triage(iss, policy)
        row = {"number": iss["number"], "title": iss["title"], "url": iss["url"],
               "reasons": reasons}
        if verdict == "exclude":
            excluded.append(row)
            continue
        if want and not (want & set(signals["labels"])):
            continue
        body = iss.get("body") or ""
        row.update({
            "score": score,
            "signals": signals,
            "author": (iss.get("author") or {}).get("login"),
            "created_at": iss.get("createdAt"),
            "updated_at": iss.get("updatedAt"),
            "body_excerpt": body[:1200] + ("…" if len(body) > 1200 else ""),
        })
        candidates.append(row)

    candidates.sort(key=lambda c: -c["score"])
    result = {
        "ok": True,
        "repo": repo,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_open": total_open,
        "scanned": len(issues),
        "graphql_cost": cost,
        "candidates": candidates[:args.top],
        "excluded": excluded,
        "policy": {"exclude_open_pr": True,
                   "exclude_merged_pr": not args.allow_merged,
                   "exclude_assigned": not args.allow_assigned,
                   "keep_closed_unmerged_pr": True,
                   "exclude_contested": not args.allow_contested,
                   "outbound_scan": not args.no_outbound,
                   "outbound_refs_resolved": len(ref_index)},
    }
    if args.format == "md":
        print(to_markdown(result))
    else:
        json.dump(result, sys.stdout, indent=2)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
