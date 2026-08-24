#!/usr/bin/env bash
# intel_init.sh — idempotent bootstrap for a repo's .repo-intel/ directory.
#
# Creates <repo-root>/.repo-intel/{issues,optimizations}, hides it via
# .git/info/exclude (never touching the tracked .gitignore), and prints a JSON
# summary of the repo for the calling skill to use.
#
# Usage: bash intel_init.sh [path-to-repo]     (defaults to cwd)

set -euo pipefail

target="${1:-$PWD}"
cd "$target" 2>/dev/null || { printf '{"ok":false,"error":"no such directory: %s"}\n' "$target"; exit 1; }

if ! root=$(git rev-parse --show-toplevel 2>/dev/null); then
  printf '{"ok":false,"error":"not a git repository: %s"}\n' "$target"
  exit 1
fi
cd "$root"

intel="$root/.repo-intel"
mkdir -p "$intel/issues" "$intel/optimizations"

# Hide .repo-intel/ from git without dirtying the tracked .gitignore.
# Use --git-common-dir so this works correctly inside linked worktrees.
common_dir=$(git rev-parse --git-common-dir 2>/dev/null || echo ".git")
case "$common_dir" in /*) ;; *) common_dir="$root/$common_dir" ;; esac
exclude_file="$common_dir/info/exclude"
excluded=false
mkdir -p "$(dirname "$exclude_file")"
touch "$exclude_file"
if grep -qxF '.repo-intel/' "$exclude_file" 2>/dev/null; then
  excluded=true
else
  printf '\n# repo-intel plugin artifacts (local only)\n.repo-intel/\n' >> "$exclude_file"
  excluded=true
fi

head_sha=$(git rev-parse HEAD 2>/dev/null || echo "")
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# Default branch: prefer origin's HEAD, fall back to the current branch.
default_branch=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)
[ -z "$default_branch" ] && default_branch="$branch"

# Repo slug (owner/name) from the origin remote, if there is one.
slug=""
if origin_url=$(git config --get remote.origin.url 2>/dev/null); then
  slug=$(printf '%s' "$origin_url" \
    | sed -E 's#^git@[^:]+:##; s#^ssh://[^/]+/##; s#^https?://[^/]+/##; s#\.git$##')
fi

exists() { [ -f "$1" ] && echo true || echo false; }

cat <<JSON
{
  "ok": true,
  "repo_root": "$root",
  "intel_dir": "$intel",
  "slug": "$slug",
  "head_sha": "$head_sha",
  "branch": "$branch",
  "default_branch": "$default_branch",
  "git_excluded": $excluded,
  "artifacts": {
    "repo_map": $(exists "$intel/REPO-MAP.md"),
    "lessons": $(exists "$intel/LESSONS.md"),
    "candidates": $(exists "$intel/issues/CANDIDATES.md")
  }
}
JSON
