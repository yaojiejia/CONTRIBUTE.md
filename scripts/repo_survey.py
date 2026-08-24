#!/usr/bin/env python3
"""repo_survey.py — deterministic facts about a repository, emitted as JSON.

Gathers the things a script gets right and a language model guesses wrong:
language breakdown, directory shape, manifests and the commands they declare,
entrypoints, CI configuration, test layout, and git history signal.

Standard library only. Usage:
    python3 repo_survey.py [--repo PATH] [--since SHA] [--json-indent N]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

MAX_FILE_BYTES = 2_000_000
LINE_COUNT_FILE_CAP = 30_000

EXT_LANG = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".swift": "Swift",
    ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".m": "Objective-C", ".mm": "Objective-C++",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell",
    ".sql": "SQL", ".html": "HTML", ".htm": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".sass": "SCSS", ".less": "Less", ".vue": "Vue", ".svelte": "Svelte",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell",
    ".lua": "Lua", ".r": "R", ".jl": "Julia", ".dart": "Dart", ".zig": "Zig",
    ".pl": "Perl", ".ps1": "PowerShell", ".proto": "Protobuf", ".tf": "Terraform",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML",
}

# Languages that are content/config rather than source; reported but ranked separately.
NON_SOURCE = {"Markdown", "reStructuredText", "JSON", "YAML", "TOML", "XML"}

VENDOR_RE = re.compile(
    r"(^|/)(node_modules|vendor|third_party|dist|build|out|target|\.venv|venv|"
    r"__pycache__|\.next|\.nuxt|coverage|migrations/versions)(/|$)"
)

TEST_RE = re.compile(r"(^|/)(tests?|spec|__tests__|e2e|integration.?tests?)(/|$)|(^|/)(test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[a-z]+$")


def run(args, cwd, timeout=60):
    """Run a command, returning stdout as text ('' on any failure)."""
    try:
        p = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def repo_identity(root):
    slug = ""
    url = run(["git", "config", "--get", "remote.origin.url"], root).strip()
    if url:
        slug = re.sub(r"\.git$", "",
                      re.sub(r"^(git@[^:]+:|ssh://[^/]+/|https?://[^/]+/)", "", url))
    default_branch = run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root
    ).strip().removeprefix("origin/")
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    return {
        "root": root,
        "slug": slug,
        "head_sha": run(["git", "rev-parse", "HEAD"], root).strip(),
        "branch": branch,
        "default_branch": default_branch or branch,
    }


def tracked_files(root):
    out = run(["git", "ls-files", "-z"], root, timeout=120)
    return [f for f in out.split("\0") if f]


def count_lines(root, path):
    full = os.path.join(root, path)
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return 0
        with open(full, "rb") as fh:
            return fh.read().count(b"\n") + 1
    except OSError:
        return 0


def survey_languages(root, files, do_lines):
    lang_files, lang_lines = Counter(), Counter()
    for f in files:
        if VENDOR_RE.search(f):
            continue
        lang = EXT_LANG.get(os.path.splitext(f)[1].lower())
        if not lang:
            continue
        lang_files[lang] += 1
        if do_lines:
            lang_lines[lang] += count_lines(root, f)
    key = lang_lines if do_lines else lang_files
    total = sum(v for k, v in key.items() if k not in NON_SOURCE) or 1
    out = []
    for lang, _ in key.most_common():
        out.append({
            "language": lang,
            "files": lang_files[lang],
            "lines": lang_lines[lang] if do_lines else None,
            "pct_of_source": (round(100 * key[lang] / total, 1)
                              if lang not in NON_SOURCE else None),
            "source": lang not in NON_SOURCE,
        })
    return out


def survey_tree(root, files, do_lines, depth=2):
    """Directory shape to `depth` levels below the root, with dominant language."""
    dirs = defaultdict(lambda: {"files": 0, "lines": 0, "langs": Counter()})
    for f in files:
        if VENDOR_RE.search(f):
            continue
        parts = f.split("/")
        if len(parts) == 1:
            key = "."
        else:
            key = "/".join(parts[:min(depth, len(parts) - 1)])
        d = dirs[key]
        d["files"] += 1
        lang = EXT_LANG.get(os.path.splitext(f)[1].lower())
        if lang:
            d["langs"][lang] += 1
            if do_lines:
                d["lines"] += count_lines(root, f)
    out = []
    for path, d in sorted(dirs.items(), key=lambda kv: -kv[1]["files"]):
        out.append({
            "path": path,
            "files": d["files"],
            "lines": d["lines"] if do_lines else None,
            "dominant_language": d["langs"].most_common(1)[0][0] if d["langs"] else None,
        })
    return out[:40]


def read_text(root, path, limit=200_000):
    try:
        with open(os.path.join(root, path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def parse_toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        return None


MANIFEST_FILES = [
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Cargo.toml", "go.mod", "Makefile", "makefile", "justfile", "Taskfile.yml",
    "turbo.json", "nx.json", "lerna.json", "pnpm-workspace.yaml",
    "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    "pom.xml", "build.gradle", "build.gradle.kts", "CMakeLists.txt",
    "Gemfile", "composer.json", "mix.exs", "deno.json", "bun.lockb",
]


def survey_manifests(root, files):
    present = {}
    commands = defaultdict(list)
    fileset = set(files)

    def add(kind, cmd, source):
        cmd = cmd.strip()
        if cmd and not any(c["cmd"] == cmd for c in commands[kind]):
            commands[kind].append({"cmd": cmd, "source": source, "verified": False})

    def classify(name):
        n = name.lower()
        if n in ("test", "tests") or n.startswith("test:") or "test" in n:
            return "test"
        if n in ("build", "compile", "dist") or n.startswith("build"):
            return "build"
        if "lint" in n or "format" in n or n in ("fmt", "check", "typecheck"):
            return "lint"
        if n in ("start", "dev", "serve", "run", "watch"):
            return "run"
        return None

    for name in MANIFEST_FILES:
        if name not in fileset:
            continue
        text = read_text(root, name)
        if text is None:
            continue
        info = {"present": True}

        if name == "package.json":
            try:
                pkg = json.loads(text)
            except Exception:
                pkg = {}
            info["name"] = pkg.get("name")
            info["package_manager"] = pkg.get("packageManager")
            info["workspaces"] = pkg.get("workspaces")
            scripts = pkg.get("scripts", {}) or {}
            info["scripts"] = scripts
            deps = pkg.get("dependencies", {}) or {}
            dev_deps = pkg.get("devDependencies", {}) or {}
            info["dependency_count"] = len(deps)
            info["dev_dependency_count"] = len(dev_deps)
            info["all_dependency_names"] = sorted(set(deps) | set(dev_deps))
            info["notable_dependencies"] = sorted(
                d for d in set(deps) | set(dev_deps)
                if d in {"react", "next", "vue", "svelte", "express", "fastify", "nest",
                         "@nestjs/core", "typescript", "vite", "webpack", "esbuild", "rollup",
                         "prisma", "drizzle-orm", "typeorm", "sequelize", "mongoose",
                         "tailwindcss", "electron", "commander", "yargs", "zod"})
            runner = "npm run"
            if "pnpm-lock.yaml" in fileset:
                runner = "pnpm"
            elif "yarn.lock" in fileset:
                runner = "yarn"
            elif "bun.lockb" in fileset:
                runner = "bun run"
            info["inferred_runner"] = runner
            for script, body in scripts.items():
                kind = classify(script)
                if kind:
                    add(kind, f"{runner} {script}", f"package.json:scripts.{script}")
                    # A script that only delegates is a classic false lead.
                    if isinstance(body, str) and re.fullmatch(r"\s*(echo\s+.*|true|exit 0)\s*", body):
                        commands[kind][-1]["warning"] = f"script body is a no-op: {body!r}"

        elif name == "pyproject.toml":
            data = parse_toml(text) or {}
            proj = data.get("project", {})
            poetry = data.get("tool", {}).get("poetry", {})
            info["name"] = proj.get("name") or poetry.get("name")
            info["build_backend"] = data.get("build-system", {}).get("build-backend")
            tools = list(data.get("tool", {}).keys())
            info["tools"] = tools
            if "pytest" in tools or "pytest" in text:
                add("test", "pytest", "pyproject.toml")
            if "ruff" in tools:
                add("lint", "ruff check .", "pyproject.toml:tool.ruff")
            if "black" in tools:
                add("lint", "black --check .", "pyproject.toml:tool.black")
            if "mypy" in tools:
                add("lint", "mypy .", "pyproject.toml:tool.mypy")

        elif name == "Cargo.toml":
            data = parse_toml(text) or {}
            info["name"] = data.get("package", {}).get("name")
            info["workspace"] = "workspace" in data
            add("build", "cargo build", "Cargo.toml")
            add("test", "cargo test", "Cargo.toml")
            add("lint", "cargo clippy", "Cargo.toml")

        elif name == "go.mod":
            m = re.search(r"^module\s+(\S+)", text, re.M)
            info["module"] = m.group(1) if m else None
            gv = re.search(r"^go\s+(\S+)", text, re.M)
            info["go_version"] = gv.group(1) if gv else None
            add("build", "go build ./...", "go.mod")
            add("test", "go test ./...", "go.mod")

        elif name in ("Makefile", "makefile"):
            targets = re.findall(r"^([a-zA-Z0-9_.-]+):(?!=)", text, re.M)
            info["targets"] = targets[:40]
            for t in targets:
                kind = classify(t)
                if kind:
                    add(kind, f"make {t}", f"{name}:{t}")

        elif name in ("turbo.json", "nx.json"):
            try:
                data = json.loads(re.sub(r"//.*", "", text))
            except Exception:
                data = {}
            info["pipeline_tasks"] = list(
                (data.get("tasks") or data.get("pipeline") or {}).keys()
            )
            info["note"] = ("monorepo task runner present — root package.json scripts "
                            "likely delegate here; verify before trusting them")

        elif name == "pnpm-workspace.yaml":
            info["packages"] = re.findall(r"^\s*-\s*['\"]?([^'\"\n]+)", text, re.M)

        elif name in ("docker-compose.yml", "docker-compose.yaml"):
            info["services"] = re.findall(r"^  ([a-zA-Z0-9_-]+):", text, re.M)

        elif name == "Gemfile":
            add("test", "bundle exec rspec", "Gemfile")

        elif name == "mix.exs":
            add("test", "mix test", "mix.exs")
            add("build", "mix compile", "mix.exs")

        elif name in ("build.gradle", "build.gradle.kts"):
            add("build", "./gradlew build", name)
            add("test", "./gradlew test", name)

        elif name == "pom.xml":
            add("build", "mvn package", "pom.xml")
            add("test", "mvn test", "pom.xml")

        elif name == "CMakeLists.txt":
            add("build", "cmake -B build && cmake --build build", "CMakeLists.txt")

        present[name] = info

    return present, dict(commands)


ENTRYPOINT_PATTERNS = [
    (re.compile(r"^main\.(go|rs|py|c|cpp)$"), "conventional main file at repo root"),
    (re.compile(r"^src/main\.(rs|ts|js|py)$"), "conventional src/main"),
    (re.compile(r"^cmd/[^/]+/main\.go$"), "Go command entrypoint"),
    (re.compile(r"^src/(index|app)\.(ts|tsx|js|jsx)$"), "JS/TS application entry"),
    (re.compile(r"^(index|app|server|main)\.(ts|js|py)$"), "application entry at root"),
    (re.compile(r"^(src/)?[^/]+/__main__\.py$"), "Python module entrypoint"),
    (re.compile(r"^(src/)?[^/]+/cli\.py$"), "Python CLI entry"),
    (re.compile(r"^src/[^/]+/__init__\.py$"), "Python package root (src layout)"),
    (re.compile(r"^[a-z_][a-z0-9_]*/__init__\.py$"), "Python package root (flat layout)"),
    (re.compile(r"^(app|manage|wsgi|asgi)\.py$"), "Python web/app entry"),
    (re.compile(r"^bin/[^/]+$"), "executable in bin/"),
    (re.compile(r"^(pages|app)/(index|layout|page)\.(tsx|jsx|ts|js)$"), "web framework root route"),
]


NON_ENTRY_DIRS = re.compile(
    r"^(tests?|docs?|examples?|benchmarks?|scripts?|tools?|samples?|e2e|fixtures)/")


def survey_entrypoints(root, files):
    found = []
    for f in files:
        if VENDOR_RE.search(f) or NON_ENTRY_DIRS.match(f):
            continue
        for pat, why in ENTRYPOINT_PATTERNS:
            if pat.match(f):
                found.append({"path": f, "why": why})
                break
    return found[:25]


CI_COMMAND_PATTERNS = [
    ("test", re.compile(r"\b(pytest|python -m pytest|go test|cargo test|npm (run )?test|"
                        r"yarn test|pnpm (run )?test|tox|jest|vitest|make test|"
                        r"bundle exec rspec|mix test|gradlew test|mvn test)\b")),
    ("lint", re.compile(r"\b(ruff|mypy|flake8|black|isort|pylint|eslint|prettier|"
                        r"golangci-lint|clippy|pre-commit run|codespell|actionlint|"
                        r"make lint|npm run lint)\b")),
    ("build", re.compile(r"\b(python -m build|cmake --build|go build|cargo build|"
                         r"npm run build|yarn build|pnpm build|make build|gradlew build|"
                         r"mvn package|docker build)\b")),
]

# Shell scaffolding inside a `run:` block that is never the project's own command.
CI_NOISE = re.compile(
    r"^(shell:|set -|export |cd |echo |if |fi$|else$|then$|done$|for |while |"
    r"[A-Z_][A-Z0-9_]*=|#|sudo apt|apt-get|pip install|python -m pip|rm -rf|mkdir|cp |mv )")


def commands_from_ci(ci):
    """Extract real build/test/lint invocations from CI run steps."""
    out = defaultdict(list)
    for wf in ci:
        for line in wf["commands"]:
            stripped = line.strip()
            if (not stripped or CI_NOISE.match(stripped) or len(stripped) > 200
                    or stripped.startswith("-")):
                continue
            for kind, pat in CI_COMMAND_PATTERNS:
                if pat.search(stripped):
                    if not any(c["cmd"] == stripped for c in out[kind]):
                        out[kind].append({
                            "cmd": stripped,
                            "source": f"ci:{wf['workflow']}",
                            "verified": False,
                            "from_ci": True,
                        })
                    break
    return out


def survey_ci(root, files):
    """CI config is the most reliable statement of how a project really builds and tests."""
    out = []
    for f in files:
        if not re.match(r"^\.github/workflows/.*\.ya?ml$", f) and f not in (
            ".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile", ".travis.yml", "azure-pipelines.yml"
        ):
            continue
        text = read_text(root, f)
        if text is None:
            continue
        name = re.search(r"^name:\s*(.+)$", text, re.M)
        def join_continuations(raw_lines):
            """Fold `cmd \\` + following line into one command, within one block."""
            out_lines, buf = [], ""
            for raw in raw_lines:
                stripped = raw.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.endswith("\\"):
                    buf += stripped[:-1].rstrip() + " "
                    continue
                out_lines.append((buf + stripped).strip())
                buf = ""
            if buf.strip():
                out_lines.append(buf.strip())
            return out_lines

        cmds = []
        # Single-line `run: cmd` steps (the negative lookahead skips block headers).
        for m in re.finditer(r"^\s*-?\s*run:\s*(?!\|)(\S.*)$", text, re.M):
            line = m.group(1).strip()
            if line:
                cmds.append(line)
        # `run: |` blocks put the commands on following indented lines. Join
        # continuations per block so one step's trailing backslash cannot swallow
        # the first command of the next step.
        for m in re.finditer(r"^(\s*)-?\s*run:\s*\|\s*\n((?:\1\s+\S.*\n?)+)", text, re.M):
            cmds.extend(join_continuations(m.group(2).splitlines()))
        seen, uniq = set(), []
        for c in cmds:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        out.append({
            "workflow": f,
            "name": name.group(1).strip() if name else None,
            "commands": uniq[:30],
        })
    return out


TEST_FRAMEWORK_MARKERS = {
    "pytest": ["pytest.ini", "conftest.py"],
    "jest": ["jest.config.js", "jest.config.ts", "jest.config.mjs"],
    "vitest": ["vitest.config.ts", "vitest.config.js"],
    "mocha": [".mocharc.json", ".mocharc.yml"],
    "playwright": ["playwright.config.ts", "playwright.config.js"],
    "cypress": ["cypress.config.js", "cypress.config.ts"],
    "rspec": [".rspec"],
    "go test": [],
    "cargo test": [],
}


def survey_tests(root, files, manifests):
    fileset = set(files)
    dirs = Counter()
    count = 0
    for f in files:
        if VENDOR_RE.search(f):
            continue
        if TEST_RE.search(f):
            count += 1
            dirs["/".join(f.split("/")[:-1]) or "."] += 1
    frameworks = []
    for fw, markers in TEST_FRAMEWORK_MARKERS.items():
        if any(m in fileset or any(x.endswith("/" + m) for x in fileset) for m in markers):
            frameworks.append(fw)

    # Config-file markers miss frameworks configured inline, so also read declared
    # dependencies and language-native runners.
    pkg = manifests.get("package.json", {})
    dep_names = set(pkg.get("all_dependency_names", []))
    for fw in ("jest", "vitest", "mocha", "ava", "tape", "playwright", "cypress",
               "@playwright/test", "testing-library", "@testing-library/react", "supertest"):
        if any(fw in d for d in dep_names) and fw not in frameworks:
            frameworks.append(fw)
    if "node:test" in dep_names or any(
            "node --test" in c or "node:test" in c
            for c in (pkg.get("scripts") or {}).values() if isinstance(c, str)):
        frameworks.append("node:test")
    if "go.mod" in manifests:
        frameworks.append("go test")
    if "Cargo.toml" in manifests:
        frameworks.append("cargo test")
    if "pyproject.toml" in manifests and "pytest" not in frameworks:
        if "pytest" in str(manifests["pyproject.toml"].get("tools", [])):
            frameworks.append("pytest")
    frameworks = sorted(set(frameworks))
    return {
        "test_files": count,
        "top_dirs": [{"path": p, "files": n} for p, n in dirs.most_common(10)],
        "frameworks": frameworks,
    }


DOC_RE = re.compile(
    r"^(README|CONTRIBUTING|CONTRIBUTE|ARCHITECTURE|DESIGN|CHANGELOG|CODE_OF_CONDUCT|"
    r"SECURITY|LICENSE|CLAUDE|AGENTS|GOVERNANCE|MAINTAINERS)(\.[a-z]+)?$", re.I)


def survey_docs(root, files):
    text_ext = {".md", ".rst", ".txt", ".adoc", ".org", ""}
    def is_prose(f):
        return os.path.splitext(f)[1].lower() in text_ext
    docs = [f for f in files
            if DOC_RE.match(os.path.basename(f)) and f.count("/") <= 1
            and not VENDOR_RE.search(f)]
    docs += [f for f in files
             if f.lower().startswith("docs/") and f.count("/") <= 2 and is_prose(f)][:20]
    return sorted(set(d for d in docs if is_prose(d)))[:30]


def survey_git(root):
    info = {}
    info["commits"] = run(["git", "rev-list", "--count", "HEAD"], root).strip() or None
    # `git log --reverse --max-count=1` returns the NEWEST commit (max-count is
    # applied before the reversal), so ask for the root commit explicitly.
    info["first_commit"] = run(
        ["git", "log", "--max-parents=0", "--format=%ad", "--date=short"], root
    ).strip().splitlines()[-1:] or [""]
    info["first_commit"] = info["first_commit"][0]
    info["last_commit"] = run(
        ["git", "log", "-1", "--format=%ad", "--date=short"], root).strip()

    authors = Counter(a for a in run(
        ["git", "log", "--format=%an", "--max-count=4000"], root).splitlines() if a)
    info["top_contributors"] = [{"name": a, "commits": n} for a, n in authors.most_common(10)]

    recent_authors = Counter(a for a in run(
        ["git", "log", "--format=%an", "--since=180.days"], root).splitlines() if a)
    info["active_contributors_180d"] = [
        {"name": a, "commits": n} for a, n in recent_authors.most_common(10)]

    churn = Counter(f for f in run(
        ["git", "log", "--format=", "--name-only", "--max-count=3000"], root, timeout=120
    ).splitlines() if f and not VENDOR_RE.search(f))
    info["churn_top"] = [{"path": p, "commits": n} for p, n in churn.most_common(20)]

    recent = Counter(f for f in run(
        ["git", "log", "--format=", "--name-only", "--since=90.days"], root, timeout=120
    ).splitlines() if f and not VENDOR_RE.search(f))
    info["changed_90d"] = [{"path": p, "commits": n} for p, n in recent.most_common(20)]
    info["files_changed_90d"] = len(recent)
    return info


def survey_since(root, sha):
    """What moved since a previously recorded commit, for incremental updates."""
    check = run(["git", "cat-file", "-t", sha], root).strip()
    if check != "commit":
        return {"sha": sha, "reachable": False,
                "note": "commit not reachable (force-push or shallow clone) — regenerate fully"}
    stat = run(["git", "diff", "--numstat", f"{sha}..HEAD"], root, timeout=120)
    changed = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            changed.append({"added": parts[0], "deleted": parts[1], "path": parts[2]})
    return {
        "sha": sha,
        "reachable": True,
        "commits_since": run(["git", "rev-list", "--count", f"{sha}..HEAD"], root).strip(),
        "files_changed": len(changed),
        "changed": changed[:100],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.getcwd(), help="path to the repository (default: cwd)")
    ap.add_argument("--since", help="previously recorded commit SHA, for an incremental update")
    ap.add_argument("--json-indent", type=int, default=2)
    args = ap.parse_args()

    root = os.path.abspath(args.repo)
    toplevel = run(["git", "rev-parse", "--show-toplevel"], root).strip()
    if not toplevel:
        json.dump({"ok": False, "error": f"not a git repository: {root}"}, sys.stdout)
        print()
        return 1
    root = toplevel

    files = tracked_files(root)  # noqa: E501
    if not files:
        json.dump({"ok": False, "error": "no tracked files (empty repo or unborn HEAD)"}, sys.stdout)
        print()
        return 1

    do_lines = len(files) <= LINE_COUNT_FILE_CAP
    manifests, commands = survey_manifests(root, files)
    ci = survey_ci(root, files)

    # CI is the most reliable statement of how a project actually builds and tests;
    # a manifest script may be a stale alias or delegate elsewhere. List CI first.
    for kind, cmds in commands_from_ci(ci).items():
        existing = commands.get(kind, [])
        commands[kind] = cmds + [c for c in existing
                                 if not any(c["cmd"] == x["cmd"] for x in cmds)]

    result = {
        "ok": True,
        "repo": repo_identity(root),
        "scale": {
            "tracked_files": len(files),
            "line_counting": "full" if do_lines else
                             f"skipped (>{LINE_COUNT_FILE_CAP} files; counts are file-based)",
        },
        "languages": survey_languages(root, files, do_lines),
        "tree": survey_tree(root, files, do_lines),
        "manifests": manifests,
        "commands": commands,
        "entrypoints": survey_entrypoints(root, files),
        "ci": ci,
        "tests": survey_tests(root, files, manifests),
        "docs": survey_docs(root, files),
        "git": survey_git(root),
    }
    if do_lines:
        result["scale"]["total_source_lines"] = sum(
            l["lines"] or 0 for l in result["languages"] if l["source"])
    if args.since:
        result["since"] = survey_since(root, args.since)

    # Consumed by survey_tests above; too bulky to emit for dependency-heavy repos.
    if "package.json" in result["manifests"]:
        result["manifests"]["package.json"].pop("all_dependency_names", None)

    json.dump(result, sys.stdout, indent=args.json_indent)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
