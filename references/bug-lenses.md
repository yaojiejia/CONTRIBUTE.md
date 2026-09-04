# Bug lenses

Ten lenses for the divergent pass of `bug-scan`. Each describes a *shape*: a producer and a consumer disagreeing across a boundary about a unit, a limit, or who checks. Seeds from filed issues usually fall under one or two lenses; walk the rest anyway, because the same boundary tends to be crossed badly in more than one way.

Each lens gives search patterns to start from and the false positives that make it produce junk. **Every pattern here finds candidates, never conclusions.** A regex hit is the beginning of the convergent pass.

---

## 1. Limit mismatch across a boundary

Input longer than the consumer's window, and nobody chunks, clamps, or errors.

- Text handed to a model with a fixed position table (512 for BERT-family, 2048 or 32768 for others)
- A buffer, column, or header with a size cap fed from an unbounded source
- A caller passing `0` or `None` for a limit parameter that the callee resolves to a default

```bash
rg -n 'unwrap_or\(512\)|max_length|max_position|MAX_.*_LEN|TruncationDirection' -g '!*test*'
rg -n 'GetEmbedding\(|Classify[A-Za-z]*\(|encode\(' -g '!*_test.go' | rg -v 'func '
```

**The key move:** list every caller of the boundary function and classify each as chunked, sampled, clamped, or unguarded. The unguarded ones are the candidates.

**False positives:** a wrapper one frame up may already window the text. Read up the call chain before reporting. A sampled path (head, middle, tail) is a design choice if a comment says so.

## 2. Unit mismatch

Two sides of a boundary counting different things.

- Bytes versus characters (`len(s)` on a Go string, `.len()` on a Rust `str`)
- Characters versus tokens (a "4 chars per token" constant applied to CJK, hex, or base64)
- Milliseconds versus seconds, or a timeout compared against the wrong clock
- A rune budget spent with a per-rune heuristic against a token cap

```bash
rg -n 'len\([a-zA-Z_.]+\)\s*/\s*4\b|\*\s*4\b|CharactersPerToken|chars.?per.?token|4 chars' -g '!*test*'
rg -n 'MaxRunes|maxRunes|MaxChars|maxChars|MaxBytes|maxBytes' -g '!*test*'
```

**Measure, do not estimate.** For text density, run the real tokenizer on the real slice the code produces. Tokenizers differ enough that a rule of thumb can have the wrong sign: a BERT WordPiece vocabulary spends one token per Chinese character, a modern BPE vocabulary spends about half a token. The direction of the bug flips with the tokenizer.

**False positives:** a constant named `MaxBytes` that slices bytes is correct. The bug is a constant named in one unit applied in another.

## 3. Sibling-path asymmetry

Two implementations of the same operation, one guarded and one not.

- Single-item versus batch paths
- Sync versus async, streaming versus non-streaming
- The routing path versus the public API that mirrors it
- Request-side versus response-side filters
- Native backend versus ONNX, OpenVINO, or remote backend

```bash
rg -n 'fn generate_.*_batch|fn .*_batch\(|func .*Batch\(' -g '!*test*'
rg -n '\.min\(|clamp|truncate' -g '!*test*'   # then check the sibling lacks the same line
```

**This is the richest seam.** When one sibling has a `.min(max_len)` and the other does not, the other one is a finding nine times out of ten. Diff the two bodies side by side.

## 4. Surface disagreement

Two surfaces that should answer the same question answer differently on the same input.

- An API endpoint and the runtime path it is meant to preview
- A threshold applied to the argmax class's confidence on one path and to a fixed positive class on another
- One path chunking and keeping the max, another taking a single call

```bash
rg -n 'threshold|Threshold' -g '!*test*' | rg -n 'argmax|Argmax|confidence >=|Confidence >='
```

**Verify with:** the same crafted input through both surfaces. If a stub backend exists in the tests that truncates the way the model does, reuse it.

**False positives:** two surfaces with deliberately different thresholds from different config keys. Report the disagreement only when the score itself differs, or note the config difference as a separate observation.

## 5. Silent discard

Input is dropped and nothing says so.

- Tokenizer truncation with no log, metric, or result flag
- A `min()` clamp with no counter
- A cap on the number of items processed (first N windows, first N sentences) inside a function whose comment says nothing is dropped

```bash
rg -n 'with_truncation|\.truncate\(|\[:max|\[:limit|break$' -g '!*test*'
rg -n 'max[A-Z][a-zA-Z]*Windows|max[A-Z][a-zA-Z]*Sentences|max[A-Z][a-zA-Z]*Items' -g '!*test*'
```

**Report as:** what is discarded, from which position, and whether any signal exists. The fix is often observability rather than behaviour.

**False positives:** deliberate sampling with a documented reason. Quote the comment and move on.

## 6. Heuristic constants

A number calibrated once, for one input distribution, applied everywhere.

- Rune weights standing in for token counts
- Overlap sizes that bound the longest detectable span
- Budget headroom (a 384 target against a 512 cap is 33% headroom; 128 against 512 is 300%)
- "Roughly 4 characters per token" anywhere near non-English text

```bash
rg -n '(Budget|budget|Overlap|overlap|Headroom)\s*=\s*[0-9]' -g '!*test*'
rg -n '//.*(rough|approx|heuristic|~)' -g '!*test*'
```

**Verify with:** the real tokenizer on dense inputs (hex, base64, URLs, emoji, CJK). Report the input class that breaks the constant and by how much.

## 7. Guards that do not guard

Something named like a protection that does not protect.

- A "hash" that is the first N bytes rendered as hex
- A dedupe that compares a prefix
- A bound that is checked on one branch and not the other
- A validation that runs after the value has already been used

```bash
rg -n 'func .*[Hh]ash.*string|fn .*hash.*&str' -g '!*test*'   # then read the body for sha/fnv
rg -n 'Sprintf\("%x", [a-zA-Z_.]+\[' -g '!*test*'
```

**Consequence to state:** what collides. A prefix hash used as a cache or affinity key collides for every input sharing that prefix, which is the same failure as lens 1 at a smaller scale.

## 8. Boundary slicing

Cutting text at a byte offset and calling the result text.

- `s[:n]`, `s[len(s)-n:]` on a Go string with user or model content
- `&s[..n]` on a Rust `str` (panics at a non-boundary)
- Truncate-with-ellipsis helpers for logs, previews, and error messages

```bash
rg -n '\[(:|[a-zA-Z0-9_().-]+:)[a-zA-Z0-9_().+-]*\]' -t go -g '!*_test.go' | rg -v '\[\]byte|bytes\.|hash\[|\[:0\]'
rg -n '\+ *"\.\.\."' -g '!*test*'
```

**Verify with:** a three-byte-per-character input (CJK) through the real function, then `utf8.ValidString`. Two out of three offsets will land mid-character. Also check whether the same repo already has a rune-safe helper (`utf8.RuneStart` loop, `[]rune` slice); the finding is then a consolidation, not a new fix.

**Severity:** cosmetic when the result feeds a log. Real when it feeds a prompt, a stored record, or an API response.

## 9. Default fall-through

A caller passes a sentinel meaning "use the default" and the default is wrong for that caller.

- `GetEmbedding(text, 0)` where `0` resolves to a 512 window
- `unwrap_or(512)` behind a parameter the caller believes is unbounded
- A config field left empty resolving to a legacy value

```bash
rg -n '\(.*, 0\)$|\(.*, 0\) *//|unwrap_or\(' -g '!*test*'
```

**Report as:** what the sentinel resolves to and whether the caller could have asked for something else. "There is no configuration workaround" is a stronger finding than "the default is small".

## 10. Inconsistent failure modes

The same bad input produces three different outcomes depending on which sibling receives it.

- One model errors with the limit named, another crashes with an index-select error, a third silently clamps
- One path returns an empty result, another returns a stale one

```bash
rg -n 'bail!|Err\(|return nil, fmt\.Errorf' -g '!*test*' | rg -n 'exceed|too long|limit|max'
```

**Report as:** a table of sibling versus outcome. The fix is to pick one and make the others match; the finding is that they differ.

---

## Measurement recipes

These turn a reasoning step into a number in under five minutes.

**Probe a real function in-package.** Write `zz_probe_test.go` in the package, call the function with a crafted input, `t.Logf` the result, run with `go test -run TestProbe -v`, then delete the file and confirm `git status` is clean. If the package needs a shared library, put its build directory on `LD_LIBRARY_PATH` rather than skipping the probe.

**Count tokens with the real tokenizer.** A Cargo project with `tokenizers = { version = "<same as the repo>", default-features = false, features = ["onig"] }`, a `main.rs` that loads `tokenizer.json`, calls `with_truncation(None)`, and prints `encode(text, false).get_ids().len()`. Disable truncation explicitly: cached tokenizer files often carry a 128 or 512 cap that will make every count identical. If the model's tokenizer is not cached locally, fetch `tokenizer.json` from the model hub; it is a few megabytes.

**Check UTF-8 validity.** `utf8.ValidString(out)` in Go, `std::str::from_utf8` in Rust, `json.Marshal` to see what a downstream reader receives.

**Count callers.** `rg -n 'fn_name\(' | rg -v _test | wc -l`, then list them. The count goes in the report; the list is the scan.

## Applying the lenses

For each boundary × lens pair ask: *is there a plausible instance here?* Record it. Judgment belongs in the convergent pass.

Then for every candidate establish: it exists (read the code, cite `file:line`), it is reachable in production (trace to a config or handler), the consequence is concrete (say what the user sees), and the failure mode is classified (silent wrong, unreadable error, clear error, cosmetic). Anything that fails the first is dropped. Anything that fails the second or third goes to **Plausible, needs a live check**. Anything inspected and found guarded goes to **Checked and fine**, which is as much a part of the output as the findings.
