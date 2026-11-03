"""The mail path, and the watchdog's own selftest, must be checked by the workflow.

Why this file exists: the watchdog's `--selftest` already asserted that SMTP settings were present,
but nothing ran it. When the pipeline moved to the VPS the clone had no `SMTP_*` at all — the laptop
had been borrowing ForChi's `.env` — so every alert and both day-4 reminders would have failed on a
machine whose only way to report that was the mail it could not send. The selftest said so, and
nobody heard it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import x_watchdog as w  # noqa: E402


def test_mail_is_ready_when_the_three_settings_and_a_recipient_exist():
    ok, detail = w.mail_ready_from(
        {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
         "SMTP_USER": "u", "SMTP_PASS": "p"}, "owner@example.com")
    assert ok, detail
    assert "smtp.example.com:587" in detail


def test_a_missing_setting_is_named_and_never_echoed():
    """The detail names the setting; a value must never appear in it."""
    ok, detail = w.mail_ready_from(
        {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "user@example.com"}, "owner@example.com")
    assert ok is False
    assert "SMTP_PASS" in detail, detail
    assert "user@example.com" not in detail, "the detail echoed a setting's value"


def test_no_recipient_is_a_problem_too():
    ok, detail = w.mail_ready_from(
        {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"}, "")
    assert ok is False
    assert "recipient" in detail


def test_this_host_can_send_because_a_silent_watchdog_is_the_worst_failure():
    """Machine-dependent on purpose: the publisher must be able to tell someone it stopped."""
    ok, detail = w.mail_ready()
    assert ok, f"this host cannot send mail: {detail}"


def test_an_unconfigured_mail_path_is_reported_as_a_problem(monkeypatch=None):
    real = w.mail_ready
    w.mail_ready = lambda: (False, "missing SMTP_HOST")
    try:
        problems = w.check(datetime(2026, 11, 3, 12, 0), 90, 19)
    finally:
        w.mail_ready = real
    assert any(p.startswith("mail:") for p in problems), problems


def test_the_summary_states_the_mail_path():
    text = w.summarise(datetime(2026, 11, 3, 20, 0))
    assert "mail:" in text, text
    assert "NOT CONFIGURED" not in text, text


def test_the_watchdog_selftest_passes():
    """It covers the day-4/day-5 rules, the recipient order and the escalation wording."""
    assert w.selftest() == 0


if __name__ == "__main__":
    raise SystemExit(__import__("run_tests").main())
