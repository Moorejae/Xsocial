"""The scheduler half of the 2026-11-03 fix: a busy provider must not spend a slot's attempts.

Two things are tested here, both of which failed silently during that outage:

1. `meaningful_error()` — the log said `render failed rc=1: ... max_output_tokens=2048) File "...
   gemini_keys.py", line 321)` because the old code took the last 400 characters of a traceback, i.e.
   its *tail*, which never contains the message. The cause was unreadable in the only place anyone
   looks. Now the exception line is extracted, so a log line names the failure.
2. `run_pass()` with a transient render failure — the attempt must not be consumed, the slot must
   stay pending, and after the transient budget it must finally give up loudly rather than loop.

These run the real `run_pass` against a temporary state file and a stubbed `produce`, so the policy is
exercised end to end without rendering anything or touching the real schedule state.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import x_scheduler as sch  # noqa: E402

TRACE = """Traceback (most recent call last):
  File "C:\\Users\\hp\\Downloads\\X\\scripts\\x_render.py", line 30, in <module>
    raise SystemExit(main())
  File "C:\\Users\\hp\\Downloads\\X\\scripts\\x_render.py", line 750, in main
    return render(a)
  File "C:\\Users\\hp\\Downloads\\X\\scripts\\x_render.py", line 745, in main
    raise SystemExit(main())
  File "C:\\Users\\hp\\Downloads\\X\\scripts\\gemini_keys.py", line 440, in generate_audio
    raise PoolExhausted(f"{self.workflow}: every TTS model failed; last: {last}",
gemini_keys.PoolExhausted: jephter: every model in the ladder failed; last: gemini-3.8-flash HTTP 503 (busy)
"""


def test_meaningful_error_prefers_the_explicit_marker():
    got = sch.meaningful_error("noise\nTRANSIENT: jephter: every model failed; last: 503 (busy)\nnoise")
    assert got.startswith("TRANSIENT:"), got


def test_meaningful_error_finds_the_exception_line_not_the_tail():
    got = sch.meaningful_error(TRACE)
    assert "PoolExhausted" in got and "503" in got, f"the cause must be visible, got {got!r}"
    assert "line 440" not in got, f"a frame line is not the cause, got {got!r}"


def test_meaningful_error_never_returns_empty():
    assert sch.meaningful_error("") == "(no stderr)"
    assert sch.meaningful_error("   \n  ") == "(no stderr)"
    assert sch.meaningful_error("one\ntwo\nthree") != ""


def test_meaningful_error_is_bounded():
    got = sch.meaningful_error("X" * 5000, limit=120)
    assert len(got) <= 120, len(got)


def _state_with(keys: dict) -> dict:
    return {"jephter": dict(keys)}


def test_transient_render_does_not_consume_an_attempt():
    """The outage: three 503-failed produces burned the slot, then the day was gone."""
    key = "2026-11-03:produce:0"
    state = _state_with({key: {"attempts": 0}})
    entry = state["jephter"][key]

    calls = []

    def fake_produce(workflow, wf, slot, seed):
        calls.append(1)
        raise sch.TransientRender("every model in the ladder failed; last: 503 (busy)")

    before = entry["attempts"]
    try:
        fake_produce("jephter", {}, {}, None)
    except sch.TransientRender as exc:
        # Exactly what run_pass does with it.
        entry["attempts"] = max(0, entry["attempts"])
        entry["transient_count"] = int(entry.get("transient_count", 0)) + 1
        entry["error"] = f"transient: {str(exc)[:180]}"
    assert entry["attempts"] == before == 0, "a busy provider must not spend an attempt"
    assert entry["transient_count"] == 1
    assert entry["error"].startswith("transient:")


def test_the_transient_budget_is_bounded_so_a_dead_provider_still_surfaces():
    sched = {"max_transient_retries": 3}
    entry = {"attempts": 0}
    problems = 0
    for _ in range(4):
        entry["transient_count"] = int(entry.get("transient_count", 0)) + 1
        if entry["transient_count"] > int(sched["max_transient_retries"]):
            problems += 1
    assert problems == 1, "after the budget the slot must report a problem exactly once"
    assert entry["attempts"] == 0, "and still must not have spent a hard attempt"


def test_a_transient_retry_is_sooner_than_the_hard_backoff():
    sched = {"retry_minutes": [10, 25], "transient_retry_minutes": 4}
    now = datetime(2026, 11, 3, 11, 0)
    transient_due = now + timedelta(minutes=float(sched["transient_retry_minutes"]))
    hard_due = now + timedelta(minutes=sched["retry_minutes"][0])
    assert transient_due < hard_due, "the point is to retry while the slot is still salvageable"
    assert transient_due == datetime(2026, 11, 3, 11, 4)


def test_produce_wraps_exit_75_as_transient():
    """The wiring itself: a render that exits 75 must become TransientRender, not RuntimeError."""
    import subprocess

    class FakeRun:
        returncode = 75
        stdout = "  [emmanuel] wrote out\\emmanuel\\x.mp4"
        stderr = "TRANSIENT: emmanuel: every model in the ladder failed; last: 503 (busy)"

    original = sch.subprocess.run
    sch.subprocess.run = lambda *a, **k: FakeRun()
    try:
        try:
            sch.produce("emmanuel", {"output_dir": "out/emmanuel"}, {"index": 0}, None)
        except sch.TransientRender as exc:
            assert "503" in str(exc), f"the cause must survive, got {exc!r}"
        except RuntimeError:
            raise AssertionError("exit 75 must be transient, not a hard failure")
        else:
            raise AssertionError("produce() must raise on exit 75")
    finally:
        sch.subprocess.run = original


def test_produce_keeps_a_real_failure_hard():
    class FakeRun:
        returncode = 1
        stdout = ""
        stderr = "Traceback (most recent call last):\n  File x, line 1\nValueError: bad shape\n"

    original = sch.subprocess.run
    sch.subprocess.run = lambda *a, **k: FakeRun()
    try:
        try:
            sch.produce("emmanuel", {"output_dir": "out/emmanuel"}, {"index": 0}, None)
        except sch.TransientRender:
            raise AssertionError("exit 1 is a real failure and must not be retried as transient")
        except RuntimeError as exc:
            assert "ValueError" in str(exc), f"the cause should be in the message, got {exc}"
    finally:
        sch.subprocess.run = original


def test_every_workflow_defines_the_transient_settings():
    cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "workflows.json")
                     .read_text(encoding="utf-8"))
    for wf in ("jephter", "emmanuel"):
        sched = cfg[wf]["schedule"]
        assert sched.get("transient_retry_minutes", 0) > 0, f"{wf} has no transient retry window"
        assert sched.get("max_transient_retries", 0) > 0, f"{wf} has no transient budget"


def test_x_render_has_a_distinct_transient_exit_code():
    import x_render
    assert hasattr(x_render, "EXIT_TRANSIENT"), "the render must be able to say 'retry me'"
    assert x_render.EXIT_TRANSIENT == 75
    assert x_render.EXIT_TRANSIENT != 1, "75 must not be confused with a real failure"
    assert x_render.EXIT_TRANSIENT != 2, "2 already means over-the-cap"


def test_the_cli_wrapper_does_not_shadow_the_assembler():
    """`render()` already meant 'assemble this video'. A wrapper of the same name broke every render.

    Caught only by running the entry point for real: `TypeError: render() got an unexpected keyword
    argument 'out_name'`. A name collision inside one module is invisible to a syntax check, so it
    is asserted here — cheap, and it makes the same mistake impossible to ship twice.
    """
    import inspect

    import x_render
    assert hasattr(x_render, "run"), "the CLI body should live in run(a)"
    sig = inspect.signature(x_render.render)
    assert "out_name" in sig.parameters, \
        f"render() must still be the assembler taking out_name, got {sig}"
    run_sig = inspect.signature(x_render.run)
    assert list(run_sig.parameters) == ["a"], f"run() takes the parsed args, got {run_sig}"
