"""The fallback that keeps a busy Gemini provider from costing a whole day.

Why these exist
---------------
2026-11-03: both channels went quiet. The cause was not a bad key, a bad script or a bad render — it
was HTTP 503 ("model overloaded") while `ROUTATE_STATUSES` only knew about 403/429. A 503 fell through
to the plain else branch, so the ladder was walked once in about five seconds at a 1.5 s backoff, raised
`PoolExhausted`, failed the render, and the scheduler spent all three of the slot's attempts on a
condition that clears itself. `TRANSIENT_STATUSES` and the extra ladder passes are the fix.

These tests fake `_request`, so they make no network call and take milliseconds. They are run inside
the workflow by `scripts/x_daily.cmd` before each pass — a fallback that is not exercised is a hope.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gemini_keys as gk  # noqa: E402


def _pool(**kw) -> gk.GeminiPool:
    """A pool with every sleep set to zero, so the retry policy is exercised at full speed."""
    defaults = dict(workflow="jephter", keys=["key-one", "key-two"],
                    models=("model-a", "model-b"), attempts_per_model=2,
                    transient_delay=0.0, round_pause=0.0, base_delay=0.0)
    defaults.update(kw)
    return gk.GeminiPool(**defaults)


class _Fake:
    """A stand-in for `_request` that answers from a list, then repeats the last answer for ever.

    A 200 reply carries *both* a text part and an inline audio part, so the same fake serves the
    script ladder and the TTS ladder.
    """

    OK = {"candidates": [{"content": {"parts": [
        {"text": "spoken words"},
        {"inlineData": {"mimeType": "audio/wav", "data": "UklGRg=="}},
    ]}, "finishReason": "STOP"}]}

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls: list[str] = []

    def __call__(self, url, key, payload=None, timeout=120.0):
        self.calls.append(url)
        if len(self.statuses) > 1:
            status = self.statuses.pop(0)
        else:
            status = self.statuses[0] if self.statuses else 200
        return (200, self.OK) if status == 200 else (status, {})

    @property
    def n(self) -> int:
        return len(self.calls)


def _with_fake(fake):
    """Swap gk._request, and hand back the original so the module is left as it was found."""
    original = gk._request
    gk._request = fake
    return original


def test_a_busy_provider_is_retried_across_the_whole_ladder_not_just_once():
    """The outage: one pass over the ladder, then PoolExhausted. Now it must walk it `ladder_rounds` times."""
    fake = _Fake([503])                      # answers 503 for ever
    original = _with_fake(fake)
    try:
        pool = _pool()
        try:
            pool.generate("write something")
        except gk.PoolExhausted as exc:
            assert exc.transient is True, "a 503 burst is transient, not fatal"
        else:
            raise AssertionError("a permanently busy provider must raise, not return empty text")
        expected = pool.ladder_rounds * len(pool.models) * pool.attempts_per_model
        assert fake.n == expected, f"expected {expected} attempts (3 ladder passes), got {fake.n}"
        assert fake.n > len(pool.models) * pool.attempts_per_model, \
            "it must retry the ladder, not give up after a single pass — that was the outage"
    finally:
        gk._request = original


def test_a_busy_provider_that_recovers_is_used():
    fake = _Fake([503, 503, 503, 200])       # the spike passes on the fourth attempt
    original = _with_fake(fake)
    try:
        text = _pool().generate("write something")
        assert text == "spoken words", text
        assert fake.n == 4, f"should have recovered on the 4th call, took {fake.n}"
    finally:
        gk._request = original


def test_a_503_does_not_put_the_key_on_cooldown():
    """The key is fine; the model is busy. Cooling the key is how a pool runs itself dry."""
    fake = _Fake([503, 200])
    original = _with_fake(fake)
    try:
        pool = _pool()
        pool.generate("write something")
        assert pool._cooldown == {}, f"a 503 must not cool a key, got {pool._cooldown}"
    finally:
        gk._request = original


def test_a_429_still_rotates_and_cools_the_key():
    fake = _Fake([429, 200])
    original = _with_fake(fake)
    try:
        pool = _pool()
        pool.generate("write something")
        assert pool._cooldown, "a 429 is a quota answer, so the key must go on cooldown"
    finally:
        gk._request = original


def test_a_bad_request_still_fails_fast_and_is_not_transient():
    """400 is our fault. Retrying it burns the pool for nothing, and it must not be queued as transient."""
    fake = _Fake([400])
    original = _with_fake(fake)
    try:
        pool = _pool()
        try:
            pool.generate("write something")
        except gk.PoolExhausted:
            raise AssertionError("a 400 must raise GeminiError, not PoolExhausted")
        except gk.GeminiError as exc:
            assert not isinstance(exc, gk.PoolExhausted)
            assert fake.n == 1, f"a 400 must not be retried, took {fake.n} calls"
    finally:
        gk._request = original


def test_the_wall_clock_budget_still_bounds_a_busy_provider():
    """Retrying harder must not become hanging. A zero budget must raise as transient immediately."""
    fake = _Fake([503])
    original = _with_fake(fake)
    try:
        try:
            _pool(round_pause=600.0).generate("write something", total_timeout=30.0)
        except gk.PoolExhausted as exc:
            assert exc.transient is True
        else:
            raise AssertionError("an exhausted wall-clock budget must raise")
    finally:
        gk._request = original


def test_tts_gets_the_same_treatment():
    """Speech is on the same ladder and the same provider; a 503 there also killed a render."""
    fake = _Fake([503, 503, 503, 503, 503, 503, 503, 503, 200])
    original = _with_fake(fake)
    try:
        data, mime = _pool().generate_audio("say this", voice="Leda")
        assert data, "audio bytes expected"
        assert fake.n == 9, f"TTS should walk the ladder too, took {fake.n}"
    finally:
        gk._request = original


def test_is_transient_is_the_contract_callers_rely_on():
    assert gk.is_transient(gk.PoolExhausted("busy", transient=True)) is True
    assert gk.is_transient(gk.PoolExhausted("broken")) is False
    assert gk.is_transient(gk.GeminiError("bad request")) is False
    assert gk.is_transient(RuntimeError("something else")) is False
