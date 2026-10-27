---
name: x-gemini-key-pool
description: 'The 18-key Gemini pool behind the X video workflows: where the keys physically live, why they are split 9/9 between jephter and emmanuel, and how scripts/gemini_keys.py rotates them. Load this when a script-writing call fails, when a key must be added, replaced or rebalanced, when a model answers 404 "no longer available" or 503 "high demand", when a generation returns empty text, or when someone asks which keys belong to which workflow. Also the place to look before printing anything key-shaped.'
---

# The Gemini key pool

Eighteen keys, one ignored file, two disjoint pools. The point of the split is **independence**: one channel
running out of quota must not slow the other down, so no key is ever shared between workflows.

> **Resilience is part of done.** This pool is the L0 rung (bounded retry with backoff) for script writing. See
> `build-fallbacks` in the shared skill library — `GeminiPool.generate()` raises `PoolExhausted` instead of
> returning `""`, so an exhausted pool becomes an alert (L3) rather than a silently empty video.

## When to use

- A generation failed: `HTTP 404 NOT_FOUND`, `HTTP 503 UNAVAILABLE`, `HTTP 429`, an empty candidate, or a timeout.
- A key has to be added, revoked or rebalanced between the two workflows.
- Anyone asks "which key does Emmanuel use?" — answer by **id**, never by value.
- A script is about to print a config value.

## Where the keys live

| | |
| :--- | :--- |
| Key file | `X\Gemini api key` — one key per line, 18 keys, all 53 chars, shape `U.Ul9…`. They are **not** the classic `AIza…` AI Studio shape, and they do work against `generativelanguage.googleapis.com/v1beta` |
| Ignored by | `.gitignore` rules `*api*key*` and `Gemini*`, added 2026-10-27 after the file was found untracked-but-not-ignored in a **public** repository |
| Split | `config/gemini-pools.json` — **key ids only** (`sha256(key)[:12]`), never values, so it is committed |
| Code | `scripts/gemini_keys.py` |
| Override | `GEMINI_KEYS_FILE` env var, else the default path above |

A key id is the only safe way to name a key: it survives a reordering of the file, it is not reversible, and it is
what the handoff log and this skill quote.

```powershell
python scripts\gemini_keys.py --list        # id, length, pool — no values
python scripts\gemini_keys.py --split       # (re)write config/gemini-pools.json
python scripts\gemini_keys.py --validate    # every key, every model rung, status only
python scripts\gemini_keys.py --models      # what these keys can reach
python scripts\gemini_keys.py --generate jephter "write one line"
```

## The split, and why round-robin

18 keys, `WORKFLOWS = ("jephter", "emmanuel")`, dealt alternately: **9 and 9**, no spare. A remainder would stay
unassigned on purpose — a key in both pools is a shared quota, which is the one thing the split exists to avoid.

Round-robin rather than "first half, second half" because the file is in creation order and consecutive keys often
belong to the same Google project; dealing alternately spreads any single project's ceiling across both workflows
instead of stacking it in one.

`load_pools()` **drops ids that are no longer in the key file and parks new ids in `spare`**. A key added to the
file therefore does not silently join a live pool — it waits until someone runs `--split` or edits the JSON. That
is deliberate: an accidental 10th key in one pool would look like a quota increase and hide a real problem.

## What was measured on 2026-10-27

| Observation | Evidence | Consequence |
| :--- | :--- | :--- |
| All 18 keys generate | `--validate` → `validated 18 keys, 0 not usable` | the pool is real, not a wish |
| `gemini-2.5-flash` and `gemini-2.0-flash` **still appear in `models.list`** but answer `generateContent` with **HTTP 404** *"no longer available to new users"* | raw 404 body quoted in the handoff log | listing a model is not evidence it generates; only a generate call is |
| `gemini-3.8-flash` answers **HTTP 503 UNAVAILABLE** *"currently experiencing high demand"* often enough to fail a whole pool cycle | `--generate jephter` failed 4/4 on 3.8, then `--validate` succeeded on 3.6 | a single model is not a usable dependency → `MODEL_LADDER` |
| Thinking tokens are billed against `maxOutputTokens` | `maxOutputTokens=16` with thinking on → **empty candidate**, `finishReason STOP`, `thoughtsTokenCount 58` | an empty answer is a *ceiling* problem, not a key problem. `generate()` raises `GeminiError` for it instead of retrying |
| Latency is wildly variable | same prompt: 7.3 s, 21 s, 138 s | timeouts are 120 s per request, and `generate()` holds a **300 s wall-clock budget** so a hung request cannot stall a caller for 16 minutes |
| Two workflows starting on the same model is slower, not faster | emmanuel on `3.8-flash` first: 102.9 s; starting on `3.6-flash`: **5.8 s** | `ladder_for(workflow)` rotates the ladder per workflow |
| Serial validation is unusable | 18 keys serial ran **> 30 min** without finishing | `--validate` uses 8 threads; the sweep now finishes in about a minute |

`MODEL_LADDER` is walked in order, on a **fresh key** per attempt: `gemini-3.8-flash` → `3.6-flash` → `3.5-flash` →
`3.1-flash-lite`. Two attempts per rung, exponential backoff with jitter between attempts, all inside a 300 s
wall-clock budget. Each workflow **starts at a different rung** (`ladder_for`) so the two pipelines do not queue
behind the same contended model.

Other models these keys can reach, worth knowing before the pipeline is built: `gemini-3.8-flash-tts` and
`gemini-3.1-flash-tts-preview` (narration — see the voice work), `lyria-3.5` (music), `gemini-3.5-transcribe`.

## Rules

- **Never print a key, a prefix, a suffix, or a length-plus-prefix.** Print the id.
- **"Truncated" is not redacted.** On 2026-10-27 a guard script reported matched lines with `cut -c1-60`; the keys
  are 53 characters, so every one of the 18 was printed in full into a terminal transcript. A guard must print the
  **line number** and nothing else. Those keys are compromised and are due for rotation — see the top of
  [HANDOFF_LOG.md](../../HANDOFF_LOG.md).
- **Never put a key in a Markdown file, a commit, a URL in a document, or a screenshot.** `.githooks/pre-commit`
  blocks the common shapes; it is a backstop, not permission.
- **`429`/`403` = this key is out of quota**, so it goes on cooldown and the next key is used. **`400`/`404` = the
  request is wrong**, so the pool is not burned retrying it — `GeminiError` is raised immediately.
- **`0` is a network failure**, distinct from an exhausted quota. One means "try again", the other means "add keys",
  and an alert that confuses them sends someone to the wrong problem.
- **A key belongs to one Google project, and quota is per project, not per key.** Two workflows must never share a
  project even with different keys. This is why each channel gets its own Google console.
- Rebalancing is `--split` plus a commit of `config/gemini-pools.json`. Never hand-edit key values.

## Verification

```powershell
cd C:\Users\hp\Downloads\X
python scripts\gemini_keys.py --list        # expect jephter: 9, emmanuel: 9
python scripts\gemini_keys.py --generate jephter  "Reply with the single word: ready"
python scripts\gemini_keys.py --generate emmanuel "Reply with the single word: ready"
```

Both must print `ready`. A `PoolExhausted` traceback is the useful failure: it names the last model tried and the
last HTTP status, and never a key.
