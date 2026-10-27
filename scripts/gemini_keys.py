#!/usr/bin/env python3
"""Gemini key pool for the two X workflows (jephter, emmanuel).

Why this exists
---------------
Each workflow must be *independent*: exhausting one channel's quota must not slow the other.
That is only true if each workflow has its own disjoint set of keys, so this module owns the
split, the rotation inside a workflow, and the cooldown when a key is rate-limited.

How the split is stored
-----------------------
`config/gemini-pools.json` maps a workflow to a list of **key ids** — `sha256(key)[:12]` — and
never to a key. The file is therefore safe to commit, survives a reordering of the key file, and
lets the handoff log name which key did what without publishing it.

The key values themselves live only in the untracked key file (`GEMINI_KEYS_FILE`, default
`Gemini api key` in the project root, git-ignored by shape).

Usage
-----
    python scripts\\gemini_keys.py --list          # ids, shape, pool membership. No values.
    python scripts\\gemini_keys.py --validate      # one models.list per key, status only
    python scripts\\gemini_keys.py --split         # (re)write config/gemini-pools.json
    python scripts\\gemini_keys.py --generate jephter "write one line"

Never prints a key, a prefix or a suffix — only ids, lengths and HTTP status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEY_FILE = ROOT / "Gemini api key"
POOLS_FILE = ROOT / "config" / "gemini-pools.json"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"
# Measured 2026-10-27: `gemini-2.5-flash` and `gemini-2.0-flash` still appear in models.list but
# answer generateContent with HTTP 404 "no longer available to new users". Listing is not evidence
# that a model generates — only a generate call is.
#
# Measured the same day: the newest flash models answer HTTP 503 UNAVAILABLE ("high demand") often
# enough that a single model is not a usable dependency. The ladder below is walked in order; a
# model that is unavailable is left alone for a while and the next one is tried, on a fresh key.
MODEL_LADDER = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
)

# Ordered, and split in this order, so the split is reproducible rather than incidental.
WORKFLOWS = ("jephter", "emmanuel")

# Statuses that mean "this key is out of quota / not allowed" -> rotate and cool down.
ROTATE_STATUSES = {403, 429}
# Statuses that mean "the request was bad" -> do not burn the whole pool on it.
FATAL_STATUSES = {400, 404}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def key_id(key: str) -> str:
    """Stable, non-reversible label for a key. Safe to commit and to paste in a handoff."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def key_file_path() -> Path:
    return Path(os.environ.get("GEMINI_KEYS_FILE") or DEFAULT_KEY_FILE)


def load_keys(path: Path | None = None) -> list[str]:
    """Read the key file: one key per line, blanks and `#` comments ignored."""
    path = path or key_file_path()
    if not path.exists():
        raise SystemExit(f"key file not found: {path} (set GEMINI_KEYS_FILE)")
    keys: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            keys.append(line)
    return keys


def split_keys(keys: list[str]) -> dict[str, list[str]]:
    """Deal the key ids round-robin.

    Round-robin rather than "first half, second half" so that if the file was written in
    creation order, consecutive keys (often from the same Google project) are spread across
    workflows instead of piling into one. An odd key is left unassigned on purpose: a shared
    key between the two workflows would be a shared quota, which is exactly what independence
    forbids. It stays a hot spare, out of both pools.
    """
    ids = [key_id(k) for k in keys]
    pools: dict[str, list[str]] = {w: [] for w in WORKFLOWS}
    for i, kid in enumerate(ids):
        if i >= len(ids) - (len(ids) % len(WORKFLOWS)):
            break  # remainder -> spare
        pools[WORKFLOWS[i % len(WORKFLOWS)]].append(kid)
    return pools


def load_pools(keys: list[str]) -> dict[str, list[str]]:
    """Read config/gemini-pools.json, dropping ids that no longer exist in the key file."""
    live = {key_id(k) for k in keys}
    if POOLS_FILE.exists():
        data = json.loads(POOLS_FILE.read_text(encoding="utf-8"))
        pools = {w: [i for i in data.get(w, []) if i in live] for w in WORKFLOWS}
        assigned = {i for ids in pools.values() for i in ids}
        for i in data.get("spare", []):
            if i in live:
                pools.setdefault("spare", []).append(i)
        unassigned = live - assigned - set(pools.get("spare", []))
        for i in sorted(unassigned):  # new keys arrive here, never silently into a pool
            pools.setdefault("spare", []).append(i)
        return pools
    return split_keys(keys)


def write_pools(pools: dict[str, list[str]]) -> None:
    """Write the pools, preserving any `_`-prefixed annotation already in the file.

    `_status` records that these keys are due for rotation. Rewriting the file used to drop it,
    which would delete the only machine-readable warning that the values are compromised.
    """
    POOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ordered: dict = {}
    if POOLS_FILE.exists():
        try:
            existing = json.loads(POOLS_FILE.read_text(encoding="utf-8"))
            ordered.update({k: v for k, v in existing.items() if k.startswith("_")})
        except json.JSONDecodeError:
            pass
    for w in WORKFLOWS:
        ordered[w] = pools.get(w, [])
    ordered["spare"] = pools.get("spare", [])
    POOLS_FILE.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------

def _request(url: str, key: str, payload: dict | None = None, timeout: float = 120.0) -> tuple[int, dict | str]:
    """One call. Returns (status, parsed-json-or-text). The key never appears in a return value."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"x-goog-api-key": key, "content-type": "application/json", "accept": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body.replace(key, "<redacted>")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Status 0 means "never got an answer". Treated as transient by callers, and reported as
        # its own thing so a network problem is never mistaken for an exhausted quota.
        return 0, f"network: {type(exc).__name__}"


def validate_key(key: str, models: tuple[str, ...] = MODEL_LADDER) -> tuple[int, str]:
    """Cheapest honest check: does this key list models, and can it generate?

    `models.list` alone is not proof — a key can list models and still be out of quota, and a
    listed model can be retired. The ladder is walked until one model answers, so a 503 from the
    newest model does not get reported as a bad key.
    """
    status, _ = _request(f"{BASE_URL}/models?pageSize=1", key)
    if status != 200:
        return status, "no answer (network)" if status == 0 else "models.list refused"

    last = "no model answered"
    for model in models:
        status, body = _request(
            f"{BASE_URL}/models/{model}:generateContent",
            key,
            {
                "contents": [{"parts": [{"text": "ok"}]}],
                # Thinking tokens are billed against maxOutputTokens (measured: a 16-token ceiling
                # with thinking on returns an EMPTY candidate, finishReason STOP). Validation turns
                # thinking off so that a tiny ceiling still proves the key can generate.
                "generationConfig": {"maxOutputTokens": 16, "temperature": 0,
                                     "thinkingConfig": {"thinkingBudget": 0}},
            },
            timeout=45.0,
        )
        if status == 200:
            text, why = _text_of(body if isinstance(body, dict) else {})
            return (200, f"generates on {model}") if text else (200, f"{model} empty: {why}")
        if status == 429:
            return status, "quota exhausted"
        if status == 0:
            last = "no answer (network)"
            continue
        reason = ""
        if isinstance(body, dict):
            reason = str(body.get("error", {}).get("status", ""))[:40]
        last = f"{model}: HTTP {status} {reason}".strip()
        if status in FATAL_STATUSES and "NOT_FOUND" in reason:
            continue  # retired model: try the next rung rather than blaming the key
        if status >= 500:
            continue
        return status, last
    return status, last


@dataclass
class GeminiPool:
    """A workflow's private set of keys, with rotation and bounded retry.

    L0 in the fallback ladder (see the `build-fallbacks` skill): transient failures are retried
    a bounded number of times with backoff; a key that is rate-limited is put on cooldown and
    skipped. A pool that runs out raises `PoolExhausted` rather than returning an empty string —
    silence is the failure mode this project cannot afford.
    """

    workflow: str
    keys: list[str]
    models: tuple[str, ...] = MODEL_LADDER
    attempts_per_model: int = 2
    base_delay: float = 1.5
    cooldown_seconds: float = 60.0
    _cooldown: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.workflow not in WORKFLOWS:
            raise ValueError(f"unknown workflow {self.workflow!r}; expected one of {WORKFLOWS}")
        if not self.keys:
            raise PoolExhausted(f"{self.workflow}: no keys assigned (see config/gemini-pools.json)")

    @property
    def model(self) -> str:
        return self.models[0]

    def _ready_keys(self) -> list[str]:
        now = time.time()
        ready = [k for k in self.keys if self._cooldown.get(key_id(k), 0) <= now]
        # All cooling down: try them again rather than go quiet. A loud failure beats a silent one.
        return ready or list(self.keys)

    def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.9,
                 max_output_tokens: int = 4096, json_mode: bool = False,
                 thinking_budget: int | None = None) -> str:
        """One completion, rotated across the pool.

        `thinking_budget=0` disables thinking. It matters: thinking tokens are billed against
        `maxOutputTokens`, so a small ceiling with thinking on returns an empty candidate rather
        than a short answer. Script writing wants thinking and a large ceiling; classification or
        a yes/no wants neither.
        """
        cfg: dict = {"temperature": temperature, "maxOutputTokens": max_output_tokens}
        if json_mode:
            cfg["responseMimeType"] = "application/json"
        if thinking_budget is not None:
            cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": cfg,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        last = "no attempt made"
        n = 0
        for model in self.models:
            for attempt in range(self.attempts_per_model):
                keys = self._ready_keys()
                key = keys[n % len(keys)]
                n += 1
                kid = key_id(key)[:4]
                status, body = _request(f"{BASE_URL}/models/{model}:generateContent", key, payload)
                if status == 200 and isinstance(body, dict):
                    text, why = _text_of(body)
                    if text:
                        return text
                    # A 200 with no text is a configuration failure, not a transient one:
                    # retrying the same ceiling produces the same empty answer and burns the pool.
                    raise GeminiError(f"{self.workflow}: empty candidate ({why}); raise max_output_tokens")
                if status in ROTATE_STATUSES:
                    self._cooldown[key_id(key)] = time.time() + self.cooldown_seconds * (attempt + 1)
                    last = f"{model} kid={kid} HTTP {status} -> key on cooldown"
                elif status in FATAL_STATUSES:
                    raise GeminiError(f"{self.workflow}: {model} rejected (HTTP {status}); not a quota problem")
                else:
                    last = f"{model} kid={kid} HTTP {status}"

                if attempt < self.attempts_per_model - 1:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    log(f"[{self.workflow}] {last}; retrying in {delay:.1f}s")
                    time.sleep(delay)
            log(f"[{self.workflow}] {model} unavailable, trying next model in the ladder")

        raise PoolExhausted(f"{self.workflow}: every model in the ladder failed; last: {last}")


class GeminiError(RuntimeError):
    pass


def _text_of(body: dict) -> tuple[str, str]:
    """Return (text, why-it-was-empty). Never raises on a surprising response shape."""
    feedback = body.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return "", f"blocked: {feedback['blockReason']}"
    for cand in body.get("candidates") or []:
        parts = ((cand.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            return text, cand.get("finishReason", "STOP")
        reason = cand.get("finishReason", "STOP")
        used = (body.get("usageMetadata") or {}).get("thoughtsTokenCount")
        if reason == "MAX_TOKENS" or used:
            return "", f"finishReason={reason}, thinking used {used} tokens of maxOutputTokens"
        return "", f"finishReason={reason}"
    return "", "no candidates"


class PoolExhausted(GeminiError):
    """Raised when a workflow's pool cannot serve a request. Escalate (L3), never swallow."""


def pool_for(workflow: str, models: tuple[str, ...] = MODEL_LADDER) -> GeminiPool:
    """Build the workflow's pool from the key file + config/gemini-pools.json."""
    keys = load_keys()
    by_id = {key_id(k): k for k in keys}
    assigned = load_pools(keys).get(workflow, [])
    return GeminiPool(workflow=workflow, keys=[by_id[i] for i in assigned if i in by_id], models=models)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def audit(keys: list[str], pools: dict[str, list[str]]) -> list[tuple[bool, str]]:
    """Invariants the split has to hold. Run after any rotation or hand-edit.

    These are the properties the two workflows' independence actually rests on, so they are checked
    rather than assumed: one pool per workflow, disjoint, balanced, and no key belonging to nobody.
    """
    ids = [key_id(k) for k in keys]
    checks: list[tuple[bool, str]] = []

    checks.append((len(keys) >= 2, f"key file has {len(keys)} keys"))
    checks.append((len(set(keys)) == len(keys), "every key is unique"))

    short = [key_id(k) for k in keys if len(k) < 30]
    checks.append((not short, f"no truncated key ({len(short)} under 30 chars)"))

    a, b = (pools.get(w, []) for w in WORKFLOWS)
    checks.append((not (set(a) & set(b)), "the two pools are disjoint"))
    checks.append((abs(len(a) - len(b)) <= 1,
                   f"pools are balanced: {WORKFLOWS[0]}={len(a)} {WORKFLOWS[1]}={len(b)}"))

    live = set(ids)
    assigned = set(a) | set(b)
    checks.append((assigned <= live, f"every assigned id exists in the key file "
                                     f"({len(assigned - live)} stale)"))
    spare = pools.get("spare", [])
    unowned = live - assigned - set(spare)
    checks.append((not unowned, f"every key belongs to a pool or the spare ({len(unowned)} unowned)"))

    checks.append((len(a) + len(b) > 0, "at least one workflow has keys"))
    return checks


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="X Gemini key pool (never prints key values)")
    ap.add_argument("--list", action="store_true", help="show key ids and pool membership")
    ap.add_argument("--validate", action="store_true", help="check every key, status only")
    ap.add_argument("--audit", action="store_true", help="check split invariants, no network")
    ap.add_argument("--split", action="store_true", help="write config/gemini-pools.json")
    ap.add_argument("--models", action="store_true", help="list models one key can see")
    ap.add_argument("--generate", nargs=2, metavar=("WORKFLOW", "PROMPT"), help="one live generation")
    args = ap.parse_args(argv)

    keys = load_keys()

    if args.list:
        pools = load_pools(keys)
        owner = {i: w for w, ids in pools.items() for i in ids}
        print(f"{len(keys)} keys in {key_file_path().name}")
        for k in keys:
            i = key_id(k)
            print(f"  {i}  len={len(k)}  {owner.get(i, 'UNASSIGNED')}")
        for w, ids in pools.items():
            print(f"{w}: {len(ids)}")

    if args.split:
        pools = split_keys(keys)
        write_pools(pools)
        print(f"wrote {POOLS_FILE.relative_to(ROOT)}: " + ", ".join(f"{w}={len(ids)}" for w, ids in pools.items()))

    if args.audit:
        pools = load_pools(keys)
        checks = audit(keys, pools)
        failed = 0
        for ok, msg in checks:
            failed += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FAIL'} {msg}")
        status = ""
        if POOLS_FILE.exists():
            status = json.loads(POOLS_FILE.read_text(encoding="utf-8")).get("_status", "")
        if status:
            print(f"  note {status}")
        print(f"{len(checks) - failed}/{len(checks)} invariants hold")
        return 0 if failed == 0 else 1

    if args.validate:
        pools = load_pools(keys)
        owner = {i: w for w, ids in pools.items() for i in ids}
        # Serial validation is not usable: with 503s and timeouts one key can take minutes, so 18
        # keys ran for over half an hour. Eight workers keeps the whole sweep under a couple of
        # minutes, which is short enough that it gets re-run after every change.
        from concurrent.futures import ThreadPoolExecutor

        def check(k: str) -> tuple[str, str, int, str]:
            status, note = validate_key(k)
            return key_id(k), owner.get(key_id(k), "UNASSIGNED"), status, note

        bad = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for kid, wf, status, note in pool.map(check, keys):
                ok = status == 200 and note.startswith("generates")
                bad += 0 if ok else 1
                print(f"  {kid}  {wf:<9} HTTP {status}  {note}", flush=True)
        print(f"validated {len(keys)} keys, {bad} not usable")
        return 0 if bad == 0 else 1

    if args.models:
        status, body = _request(f"{BASE_URL}/models?pageSize=100", keys[0])
        if status != 200 or not isinstance(body, dict):
            print(f"HTTP {status}")
            return 1
        for m in body.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(f"  {m['name']:<44} in={m.get('inputTokenLimit')} out={m.get('outputTokenLimit')}")

    if args.generate:
        workflow, prompt = args.generate
        print(pool_for(workflow).generate(prompt, max_output_tokens=1024))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
