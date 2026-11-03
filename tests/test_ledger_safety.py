"""The publish ledger must survive two runs writing at once, and never be half-written.

The failure this pins: each run loaded the ledger, mutated its own copy, and saved the whole
thing back. The later save erased the earlier run's claim — so the double-post guard forgot a
file it had already claimed, and the same bytes could upload twice.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import youtube_upload as yt  # noqa: E402


def _memory(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stale_copy_cannot_erase_another_runs_claim():
    """A caller holding an old ledger still writes only its own key."""
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        ancient = yt.load_ledger(led)                     # loaded when the file was empty

        yt.claim(None, "emmanuel", "aaaa", led)           # another run claims first
        yt.claim(ancient, "jephter", "bbbb", led)         # this one still thinks it began empty

        claims = _memory(led)["claims"]
        assert "emmanuel:aaaa" in claims, claims
        assert "jephter:bbbb" in claims, claims


def test_concurrent_claims_all_survive():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        keys = [(f"wf{i}", f"{i:04x}") for i in range(24)]
        errors: list[BaseException] = []

        def worker(wf: str, h: str) -> None:
            try:
                yt.claim(None, wf, h, led)                # each is a separate read-modify-write
            except BaseException:                         # noqa: BLE001 - reported below
                errors.append(traceback.format_exc())

        threads = [threading.Thread(target=worker, args=k) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        claims = _memory(led)["claims"]
        missing = [f"{wf}:{h}" for wf, h in keys if f"{wf}:{h}" not in claims]
        assert not missing, f"lost claims: {missing}"


def test_save_is_atomic_and_leaves_no_temp_file():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        led.write_text(json.dumps({"claims": {"old": {}}, "published": []}), encoding="utf-8")
        yt.save_ledger({"claims": {"new": {}}, "published": []}, led)

        assert _memory(led)["claims"] == {"new": {}}
        leftovers = [p.name for p in Path(d).iterdir() if p.name != "publish_ledger.json"]
        assert not leftovers, leftovers


def test_a_corrupt_ledger_reads_as_empty_instead_of_crashing():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        led.write_text('{"claims": {"half": ', encoding="utf-8")
        assert yt.load_ledger(led) == {"claims": {}, "published": []}
        yt.claim(None, "jephter", "cccc", led)
        assert "jephter:cccc" in _memory(led)["claims"]


def test_an_abandoned_lock_is_broken_instead_of_blocking_forever():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        lock.write_text("99999", encoding="utf-8")
        assert yt.ledger_lock(led, timeout=0.01)          # a fresh lock is honoured
        with yt.ledger_lock(led, timeout=0.05):
            pass
        assert not lock.exists()


def test_the_lock_is_released_even_when_the_body_raises():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        try:
            with yt.ledger_lock(led):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert not led.with_name(led.name + ".lock").exists()


def test_a_lock_is_only_removed_by_the_run_that_took_it():
    """The phantom-unlink race: the previous holder must not delete the next holder's lock."""
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        with yt.ledger_lock(led):
            assert lock.exists()
            foreign = lock.read_text(encoding="utf-8")
            lock.write_text("someone-else", encoding="utf-8")   # simulate a new owner
        assert lock.exists(), "the lock was removed by a run that no longer owned it"
        assert lock.read_text(encoding="utf-8") == "someone-else", foreign
        lock.unlink()


def test_concurrent_claims_stay_consistent_over_many_rounds():
    """Timing-dependent, so repeated: a lock that loses a writer shows up as a lost claim."""
    for _ in range(5):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "publish_ledger.json"
            keys = [(f"wf{i}", f"{i:04x}") for i in range(16)]
            errors: list[str] = []

            def worker(wf: str, h: str) -> None:
                try:
                    yt.claim(None, wf, h, led)
                except BaseException:                     # noqa: BLE001 - reported below
                    errors.append(traceback.format_exc())

            threads = [threading.Thread(target=worker, args=k) for k in keys]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, errors[0]
            claims = _memory(led)["claims"]
            assert len(claims) == len(keys), sorted(claims)


def test_the_selftest_does_not_touch_the_real_ledger():
    """`--selftest` used to claim a fake hash in out/publish_ledger.json."""
    before = yt.LEDGER.stat().st_mtime if yt.LEDGER.exists() else None
    assert yt.selftest() == 0
    after = yt.LEDGER.stat().st_mtime if yt.LEDGER.exists() else None
    assert before == after, "selftest wrote to the live ledger"


if __name__ == "__main__":
    raise SystemExit(__import__("run_tests").main())
