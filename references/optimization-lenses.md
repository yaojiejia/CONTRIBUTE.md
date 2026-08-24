# Optimization lenses

Twelve lenses for the divergent pass of `opt-scan`. Walk every module through every lens so coverage is systematic rather than whatever happens to come to mind — the point of a fixed checklist is that it surfaces the categories a reader would not have thought to look for.

Each lens gives search patterns to start from, and the false positives that make that lens produce junk. **Every pattern here finds candidates, never conclusions.** A regex hit is the beginning of the convergent pass, not evidence.

---

## 1. Algorithmic complexity

Quadratic work hiding in readable code.

- Nested iteration over the same or a related collection
- Membership tests against a list or array inside a loop
- Repeated `.index()` / `.find()` / `indexOf` lookups
- Sorting inside a loop, or re-sorting already-sorted data

```bash
rg -n --pcre2 'for .+:\n(?s).{0,400}?for ' -t py
rg -n 'in \[|\.includes\(|\.indexOf\(|\.index\(' -g '!test*'
```

**False positives:** nested loops over small fixed-size collections (a 3×3 matrix, a list of enum variants) are fine. Complexity only matters when the input can grow — establish that it can before reporting.

## 2. I/O and N+1

The highest-value lens in most application code, because the cost is measured in network round-trips rather than CPU.

- Database queries, HTTP calls, or file reads inside a loop
- ORM lazy-loading in a render or serialization path
- Missing `select_related` / `include` / `JOIN` / `DataLoader` batching
- A "get one" helper called from a "get many" caller

```bash
rg -n -B2 -A6 '\b(for|while|forEach|\.map\()' -g '*.{py,js,ts,go,rb,java}' | rg -n 'query|fetch|get\(|find\(|await|execute|request'
```

**False positives:** loops over a bounded collection (config entries at startup, a handful of shards). Confirm the collection is user-scaled, and confirm the path is actually hot before ranking it high.

## 3. Caching and recomputation

- Pure functions with expensive bodies called repeatedly with the same arguments
- Values recomputed per request that change per deploy
- Cache lookups immediately followed by unconditional recompute
- Missing memoization on a recursive function

**False positives:** caching adds invalidation as a new failure mode. If correctness depends on freshness, a cache is a bug, not an optimization. Say so rather than proposing it.

## 4. Redundant per-call construction

Objects that are expensive to build and cheap to reuse, rebuilt on every call.

- `re.compile` / `new RegExp` inside a function body rather than at module scope
- HTTP clients, DB connections, or SDK clients constructed per request
- Schema/validator/parser objects rebuilt per invocation
- Template compilation inside a render loop

```bash
rg -n '(re\.compile|new RegExp|regexp\.MustCompile)' -g '!test*'
rg -n '(requests\.Session|new .*Client|createClient|http\.Client\{)' -g '!test*'
```

**False positives:** many languages cache compiled regexes internally, so the win can be near zero — check before claiming it. A client built once per *process* that merely looks per-call is also fine.

## 5. Data structure fit

- A list used where a set or dict is needed (membership, dedupe, lookup by key)
- Linear scans over data that is already keyed
- String concatenation in a loop instead of a join or builder
- Repeated dict/array copying where a view or reference would do

```bash
rg -n '\+= *["\x27]|\.append\(.*\).*join' -g '!test*'
```

**False positives:** for small collections a list is often faster than a set and always simpler. This lens matters at scale or in a hot loop; elsewhere it is noise dressed up as rigor.

## 6. Concurrency — missing and excessive

Both directions are defects.

- Independent `await`s issued serially that could run together
- Sequential independent network calls
- Unbounded `Promise.all` / goroutine spawning over a user-controlled list — a load amplifier, not a speedup
- A lock held across an I/O call
- Thread pools sized without regard to the workload

```bash
rg -n -A4 'await ' -g '*.{ts,js}' | rg -n 'await .*\n.*await'
rg -n 'Promise\.all|errgroup|WaitGroup|asyncio\.gather'
```

**False positives:** sequential awaits are correct when the second depends on the first. Verify independence before proposing parallelism — this is the most common way this lens produces wrong findings.

## 7. Memory and allocation

- Reading an entire file or response into memory where streaming works
- `SELECT *` then discarding most columns; fetching all rows to count them
- Defensive copies of large structures on a hot path
- Accumulating an unbounded list in a long-running process (also a leak)

**False positives:** streaming complicates error handling and retries. For inputs that are known-small, materializing is the better engineering choice.

## 8. Startup, build, and CI time

Often the largest wins in developer-facing repos, and consistently under-examined.

- Heavy imports at module scope used by one rarely-taken branch
- CI steps without dependency caching
- Test suites running serially that could shard
- Full rebuilds where incremental would work
- Docker layers ordered so that a source change invalidates dependency installation

```bash
rg -n 'uses: actions/(setup|cache)' .github/workflows/
rg -n '^(COPY|RUN)' Dockerfile
```

**Verify with:** CI run durations from `gh run list`, which turn a guess into a measurement.

## 9. Bundle and artifact size

- A whole library imported for one function
- Both a library and its lighter equivalent present
- Dev-only dependencies reaching production
- Unminified or uncompressed assets; unoptimized images
- Missing tree-shaking or code splitting on large routes

```bash
rg -n "^import .* from ['\"](lodash|moment)['\"]"
```

## 10. Duplication and dead code

- The same logic implemented in several modules, each with its own bugs
- Copy-pasted blocks differing only in a constant
- Exported symbols with no callers; feature flags permanently on or off
- Commented-out blocks retained "for reference"

```bash
rg -n --stats '\bTODO\b|\bFIXME\b|\bXXX\b|\bDEPRECATED\b'
```

**Verify before deleting:** an export with no in-repo caller may be public API. Check whether the package is published and whether the symbol is documented. Removing public API is a breaking change, not a cleanup.

## 11. Database queries and indexes

- Filters and joins on unindexed columns
- Indexes that no query uses (write cost with no read benefit)
- `COUNT(*)` on large tables in a request path
- `OFFSET`-based pagination deep into a table
- Transactions held open across application logic or network calls

Cross-reference migration files against the `WHERE` and `ORDER BY` clauses actually present in the code — this is where evidence for this lens comes from.

## 12. Configuration-level wins

Changes that touch no application logic and are therefore the cheapest of all.

- Compiler and bundler optimization flags left at defaults
- Connection pool sizes, timeouts, and batch sizes never tuned from their defaults
- Debug logging, source maps, or profiling enabled in production paths
- Missing HTTP compression or caching headers
- Log level set to `DEBUG` in a hot loop

```bash
rg -n 'DEBUG|verbose|log_level|LOG_LEVEL' -g '*.{env,yaml,yml,toml,ini,json}'
```

---

## Applying the lenses

For each module × lens pair, ask: *is there a plausible instance of this pattern here?* Record every plausible one. Judgment about whether it is real belongs in the convergent pass — mixing the two suppresses candidates before they have been examined.

Then, for every candidate, the convergent pass must establish three things:

1. **It exists.** Open the file. Read the actual code. Cite `file:line`.
2. **It is on a path that matters.** Trace to an entrypoint, request handler, or hot loop. An inefficiency in a one-time startup path that runs in 4ms is not a finding.
3. **The fix is real.** Name the concrete change and what it costs. "Add caching" is not a proposal; "hoist the `re.compile` to module scope" is.

Anything failing (1) is dropped. Anything failing (2) or (3) goes to **Unverified hypotheses** with a note on what could not be established — visible, but never mixed into the ranked list.
