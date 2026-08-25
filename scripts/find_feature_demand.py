#!/usr/bin/env python3
"""Rank stated feature demand in a repository, and list what was already declined.

Feeds the `stated demand` and `declined already` lenses of `feature-scan`. Two
questions, one pass:

  * What have users actually asked for, weighted by how many of them asked?
  * What has been asked for and refused, so it is never proposed again?

The second half matters more than it looks. Proposing a feature the maintainers
have already closed as out of scope is worse than proposing nothing: it burns the
reader's trust and, in repos with a written placement policy, it is a category
error rather than a judgment call.

Standard library only. Requires `gh` authenticated.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

QUERY = """
query($owner:String!, $name:String!, $cursor:String, $page:Int!, $states:[IssueState!]) {
  rateLimit { cost remaining }
  repository(owner:$owner, name:$name) {
    issues(first:$page, states:$states, after:$cursor,
           orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      totalCount
      nodes {
        number title url createdAt updatedAt state stateReason
        labels(first:20) { nodes { name } }
        reactionGroups { content reactors { totalCount } }
        comments { totalCount }
        participants(first:1) { totalCount }
        assignees(first:1) { totalCount }
        author { login }
      }
    }
  }
}
"""

# A feature request rarely says "feature" in a label the same way twice.
FEATURE_LABELS = {
    "enhancement", "feature", "feature request", "feature-request", "featurerequest",
    "type/feature", "type/enhancement", "kind/feature", "kind/enhancement",
    "new feature", "feat", "proposal", "idea", "rfc", "innovation",
}

# `opt-scan` and `issue-hunt` treat rfc/proposal as blocking, because they are not
# actionable work. Here they are the target: a proposal is stated demand.
# An explicit marker in the title is strong on its own.
TITLE_MARKER = re.compile(
    r"^\s*(\[\s*(feature|feat|enhancement|rfc|idea|proposal)\s*\]|"
    r"(feature|feat|rfc|proposal)\s*[:\-])", re.I)

# A bare verb opening is much weaker — "Support X" is as often a bug report about
# X being broken as a request for X. Trust it only when nothing calls this a bug.
TITLE_VERB = re.compile(r"^\s*(add|support|allow|enable)\s+", re.I)

BUG_LABELS = {"bug", "type/bug", "kind/bug", "defect", "regression", "type/perf"}

DECLINED_LABELS = {
    "wontfix", "won't fix", "wont-fix", "declined", "out of scope", "out-of-scope",
    "not planned", "invalid", "duplicate", "stale",
}

# Reaction weight. A thumbs-up is a vote; an eyes emoji is a bookmark.
REACTION_WEIGHT = {
    "THUMBS_UP": 3, "HOORAY": 2, "HEART": 2, "ROCKET": 2,
    "LAUGH": 1, "CONFUSED": 0, "EYES": 1, "THUMBS_DOWN": -3,
}


def die(msg, code=2):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


def run(args, timeout=180, stdin=None):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, input=stdin)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        die("`gh` not found on PATH. Install the GitHub CLI and run `gh auth login`.")
    except subprocess.TimeoutExpired:
        return 1, "", "timed out"


def detect_repo():
    rc, out, _ = run(["git", "config", "--get", "remote.origin.url"], timeout=15)
    if rc != 0 or not out.strip():
        return None
    slug = re.sub(r"\.git$", "",
                  re.sub(r"^(git@[^:]+:|ssh://[^/]+/|https?://[^/]+/)", "", out.strip()))
    return slug if "/" in slug else None


def days_since(iso):
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


def labels_of(issue):
    return [l["name"].lower() for l in (issue.get("labels") or {}).get("nodes") or []]


def is_feature_request(issue):
    """Label first, title second. Title matching alone is far too loose."""
    labs = set(labels_of(issue))
    if FEATURE_LABELS & labs:
        return True
    title = issue.get("title") or ""
    if TITLE_MARKER.match(title):
        return True
    # "Support Bailian endpoints that don't expose /v1/models" is labelled type/bug
    # and is a bug report, not a request. Without this guard the verb form drags in
    # every such issue and the demand ranking stops being about demand.
    return bool(TITLE_VERB.match(title)) and not (BUG_LABELS & labs)


def reaction_score(issue):
    total, weighted = 0, 0
    for g in issue.get("reactionGroups") or []:
        n = ((g.get("reactors") or {}).get("totalCount")) or 0
        total += n
        weighted += n * REACTION_WEIGHT.get(g.get("content", ""), 1)
    return total, weighted


def demand(issue):
    """Score stated demand. Reactions dominate: they are the cheapest honest vote."""
    reasons = []
    total_r, weighted = reaction_score(issue)
    score = weighted
    if total_r:
        reasons.append(f"+{weighted} from {total_r} reactions")

    participants = (issue.get("participants") or {}).get("totalCount", 0)
    if participants > 1:
        bump = min((participants - 1) * 2, 20)
        score += bump
        reasons.append(f"+{bump} {participants} participants — more than one person wants it")

    n_comments = (issue.get("comments") or {}).get("totalCount", 0)
    if n_comments:
        bump = min(n_comments, 10)
        score += bump
        reasons.append(f"+{bump} {n_comments} comments")

    age_update = days_since(issue.get("updatedAt"))
    if age_update <= 90:
        score += 8
        reasons.append("+8 still active in the last 90 days")
    elif age_update > 730:
        score -= 10
        reasons.append("-10 untouched for over two years — demand may be stale")

    age_open = days_since(issue.get("createdAt"))
    if age_open > 365 and age_update <= 90:
        score += 5
        reasons.append("+5 open over a year and still discussed — durable demand")

    if (issue.get("assignees") or {}).get("totalCount", 0):
        score -= 15
        reasons.append("-15 assigned — someone may already be building it")

    return score, reasons, {
        "reactions": total_r, "participants": participants, "comments": n_comments,
        "days_since_update": age_update, "days_open": age_open,
        "labels": labels_of(issue),
    }


def declined_reason(issue):
    """Why a closed feature request did not happen, when that is recorded."""
    labs = set(labels_of(issue))
    hit = labs & DECLINED_LABELS
    if hit:
        return f"closed with label: {', '.join(sorted(hit))}"
    if (issue.get("stateReason") or "").upper() == "NOT_PLANNED":
        return "closed as not planned"
    return None


def fetch(repo, limit, page_size, states):
    owner, name = repo.split("/", 1)
    issues, cursor, cost, total = [], None, 0, 0
    while len(issues) < limit:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}",
                "-F", f"owner={owner}", "-F", f"name={name}",
                "-F", f"page={min(page_size, limit - len(issues))}",
                "-f", f"states={states}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        rc, out, err = run(args)
        if rc != 0 and not out.strip():
            die(f"gh api graphql failed: {err.strip()[:400]}")
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            die(f"unparseable response from gh: {out[:300]}")
        if data.get("errors") and not (data.get("data") or {}).get("repository"):
            die(f"GraphQL error: {json.dumps(data['errors'])[:400]}")
        repo_node = (data.get("data") or {}).get("repository")
        if not repo_node:
            die(f"repository not found or not accessible: {repo}")
        cost += ((data.get("data") or {}).get("rateLimit") or {}).get("cost", 0)
        conn = repo_node["issues"]
        total = conn["totalCount"]
        issues.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return issues[:limit], total, cost


def to_markdown(r):
    lines = [f"# Stated feature demand — {r['repo']}", ""]
    lines.append(f"Open issues scanned: **{r['scanned_open']}**, of which "
                 f"**{len(r['wanted'])}** read as feature requests. "
                 f"Closed scanned: **{r['scanned_closed']}**, "
                 f"**{len(r['declined'])}** declined.")
    lines.append("")
    lines.append("Demand is scored from reactions (a thumbs-up is a vote, weighted 3), "
                 "distinct participants, and comment volume, adjusted for staleness. "
                 "It measures **what people asked for**, not what is worth building — "
                 "that judgment belongs to the `feature-scan` convergent pass, which "
                 "must still verify the capability is genuinely absent before proposing it.")
    lines.append("")

    if r["wanted"]:
        lines.append("## Wanted — open, ranked by demand")
        lines.append("")
        lines.append("| # | Demand | Request | 👍 all | People | Age | Labels |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in r["wanted"]:
            s = c["signals"]
            labs = ", ".join(f"`{l}`" for l in s["labels"][:3]) or "—"
            lines.append(f"| [{c['number']}]({c['url']}) | {c['score']} | {c['title'][:62]} "
                         f"| {s['reactions']} | {s['participants']} "
                         f"| {s['days_open']}d | {labs} |")
        lines.append("")
        lines.append("### Why each ranked as it did")
        lines.append("")
        for c in r["wanted"][:12]:
            lines.append(f"**#{c['number']} — {c['title']}**  ")
            lines.append(f"{c['url']}")
            for reason in c["reasons"]:
                lines.append(f"- {reason}")
            lines.append("")
    else:
        lines.append("**No open feature requests matched.** Either the repo labels them "
                     "differently — check `gh label list` and pass `--label` — or feature "
                     "discussion happens somewhere other than the issue tracker.")
        lines.append("")

    lines.append("## Declined — do not propose these again")
    lines.append("")
    if r["declined"]:
        lines.append("Closed feature requests carrying a declining label or `not planned`. "
                     "Read the closing comment before assuming a refusal was permanent — "
                     "some are 'not now', some are 'not here, ship it as a plugin', and "
                     "the distinction changes what to propose.")
        lines.append("")
        lines.append("| # | Request | Why it closed | Closed |")
        lines.append("|---|---|---|---|")
        for c in r["declined"]:
            lines.append(f"| [{c['number']}]({c['url']}) | {c['title'][:64]} "
                         f"| {c['why']} | {c['signals']['days_since_update']}d ago |")
    else:
        lines.append("None found in the scanned window. This is weak evidence, not proof "
                     "that nothing was declined — widen `--closed-limit` before relying on it.")
    lines.append("")
    lines.append("---")
    lines.append("<!-- repo-intel provenance -->")
    lines.append(f"- **Generated by:** repo-intel/find_feature_demand v{r['version']}")
    lines.append(f"- **Repo:** {r['repo']}")
    lines.append(f"- **Generated:** {r['generated']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="owner/name (default: origin of the current repo)")
    ap.add_argument("--limit", type=int, default=300,
                    help="open issues to scan (default 300)")
    ap.add_argument("--closed-limit", type=int, default=200,
                    help="closed issues to scan for declined requests (default 200)")
    ap.add_argument("--page-size", type=int, default=50)
    ap.add_argument("--top", type=int, default=30, help="wanted entries to keep (default 30)")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    ap.add_argument("--label", action="append", default=[],
                    help="treat this label as marking a feature request (repeatable, "
                         "adds to the built-in set)")
    ap.add_argument("--no-closed", action="store_true",
                    help="skip the declined pass (faster, loses the do-not-propose list)")
    args = ap.parse_args()

    repo = args.repo or detect_repo()
    if not repo or "/" not in repo:
        die("could not determine the repository. Pass --repo owner/name.")
    FEATURE_LABELS.update(l.lower() for l in args.label)

    open_issues, total_open, cost = fetch(repo, args.limit, args.page_size, "OPEN")
    wanted = []
    for iss in open_issues:
        if not is_feature_request(iss):
            continue
        score, reasons, signals = demand(iss)
        wanted.append({"number": iss["number"], "title": iss["title"], "url": iss["url"],
                       "score": score, "reasons": reasons, "signals": signals})
    wanted.sort(key=lambda c: -c["score"])

    declined, scanned_closed = [], 0
    if not args.no_closed:
        closed_issues, _, ccost = fetch(repo, args.closed_limit, args.page_size, "CLOSED")
        cost += ccost
        scanned_closed = len(closed_issues)
        for iss in closed_issues:
            if not is_feature_request(iss):
                continue
            why = declined_reason(iss)
            if not why:
                continue
            _, _, signals = demand(iss)
            declined.append({"number": iss["number"], "title": iss["title"],
                             "url": iss["url"], "why": why, "signals": signals})

    result = {
        "ok": True,
        "version": "0.4.0",
        "repo": repo,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_open": total_open,
        "scanned_open": len(open_issues),
        "scanned_closed": scanned_closed,
        "graphql_cost": cost,
        "wanted": wanted[:args.top],
        "declined": declined,
    }
    if args.format == "md":
        print(to_markdown(result))
    else:
        json.dump(result, sys.stdout, indent=2)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
