"""The publish ledger must survive two runs writing at once, and never be half-written.

The failure this pins: each run loaded the ledger, mutated its own copy, and saved the whole
thing back. The later save erased the earlier run's claim — so the double-post guard forgot a
file it had already claimed, and the same bytes could upload twice.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
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
    """A lock left by a process that died must not block the next run for ever.

    "Abandoned" means the file is **older than the timeout**, which is what the owner-check uses to
    decide the holder can no longer be alive.
    """
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        lock.write_text("99999", encoding="utf-8")
        old = time.time() - 3600
        os.utime(lock, (old, old))                       # a lock an hour old, from a dead process
        with yt.ledger_lock(led, timeout=0.2):           # breaks it rather than waiting
            pass
        assert not lock.exists()


def test_a_fresh_lock_is_honoured_then_reported_by_name():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        lock.write_text("someone-else", encoding="utf-8")
        try:
            with yt.ledger_lock(led, timeout=0.2):
                raise AssertionError("a fresh lock was ignored")
        except TimeoutError as exc:
            assert str(lock) in str(exc), str(exc)       # the message names the file to delete


def test_the_lock_is_released_even_when_the_body_raises():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        try:
            with yt.ledger_lock(led):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert not led.with_name(led.name + ".lock").exists()


def test_the_lock_wait_always_ends_even_when_stat_keeps_failing():
    """A wait loop that can `continue` into itself forever is a hang, not a lock.

    The first version checked the deadline only after a *successful* `stat()` and skipped the sleep on
    the exception paths. With `stat()` failing on every pass — Windows reports EACCES while another
    handle is mid-delete — it spun with no timeout and no output, which in production looks exactly
    like a wedged pipeline.
    """
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        lock.write_text("held", encoding="utf-8")

        real_open, real_stat = yt.os.open, Path.stat
        started = time.monotonic()

        def refuse_open(*a, **k):
            raise PermissionError(13, "Permission denied")

        def refuse_stat(self, *a, **k):
            raise PermissionError(13, "Permission denied")

        try:
            yt.os.open = refuse_open          # patched inside the try, so the restore always runs
            Path.stat = refuse_stat
            try:
                with yt.ledger_lock(led, timeout=0.25):
                    raise AssertionError("the lock was reported as taken")
            except TimeoutError:
                pass
        finally:
            yt.os.open = real_open
            Path.stat = real_stat
            lock.unlink(missing_ok=True)
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"the wait did not end: {elapsed:.1f}s"


def test_the_lock_is_taken_despite_windows_reporting_eacces():
    """Windows answers EACCES, not EEXIST, when the lock exists and is being deleted.

    The 24-thread test caught this: `os.open(..., O_CREAT | O_EXCL)` raised PermissionError and the
    whole claim died instead of waiting its turn.
    """
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "publish_ledger.json"
        lock = led.with_name(led.name + ".lock")
        lock.write_text("held-by-someone-else", encoding="utf-8")

        def flaky_open(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        real_open = yt.os.open
        yt.os.open = flaky_open
        try:
            try:
                with yt.ledger_lock(led, timeout=0.2):
                    pass
            except TimeoutError:
                pass                      # the honest outcome: it waited, then said so
            else:
                raise AssertionError("a PermissionError was treated as a successful lock")
        finally:
            yt.os.open = real_open
            lock.unlink(missing_ok=True)


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
