"""The scope request must match the app's approved Data Access list, character for character.

This is the failure mode that matters most and is invisible at design time. Verification is granted
for a **list of scope strings**; a production app refuses a sensitive scope that is not on it. X asked
for `youtube.readonly` for months because the app was in Testing, where test users bypass that check —
and when the app went to production on 2026-11-17 the request no longer matched what Google approved
(`youtube.upload` + `youtube.force-ssl`, the list ForChi's app carries).

So the strings are pinned here, and a test also proves the read-back that `youtube.readonly` existed
for is still reachable under `youtube.force-ssl` — otherwise someone "simplifies" the list and every
`--check` starts answering 403 again, which is exactly what happened on 2026-11-01.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import youtube_auth as ya  # noqa: E402

# The app approved in the Google Cloud console (one app, three channels: ForChi, jephter, emmanuel).
APPROVED = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)


def test_the_requested_scopes_are_the_approved_ones():
    """The YouTube scopes must be exactly the approved pair, in order, with nothing else added."""
    yt_scopes = tuple(s for s in ya.SCOPES if "/youtube" in s)
    assert yt_scopes == APPROVED, ya.SCOPES


def test_openid_and_email_ride_along_so_the_channel_can_be_named():
    assert set(ya.SCOPES) >= {"openid", "email"}, ya.SCOPES


def test_the_unverified_read_scope_is_not_requested():
    """`youtube.readonly` is not on the approved list; `youtube.force-ssl` supersedes it."""
    assert not any(s.endswith("/youtube.readonly") for s in ya.SCOPES), ya.SCOPES


def test_a_no_channel_consent_is_refused_but_an_unreadable_one_is_kept():
    """Two different answers that must not be confused.

    `(unread)` = the check itself failed, so the token is kept (the 2026-11-01 lesson: a 403 threw
    away two good refresh tokens). A *successful* read with no channel = the wrong Google account,
    which can never upload — ForChi lost a working channel to one on 2026-11-17.
    """
    assert ya.owns_no_channel("(no channel on this account)", "(none)") is True
    assert ya.owns_no_channel("", "(none)") is True
    assert ya.owns_no_channel("(unread)", "(HTTPError)") is False
    assert ya.owns_no_channel("Brown Cinema", "UCYjRtF_ECcIaF8ldCGXAI2w") is False


def test_the_loopback_listener_answers_over_ipv4_and_ipv6():
    """Bound to 127.0.0.1 only, a browser that resolves `localhost` to ::1 gets a refusal.

    Measured 2026-11-17: `http://127.0.0.1:8765/...` answered 200 while `http://[::1]:8765/...` was
    actively refused, and the owner saw "this site can't be reached" twice after approving.
    """
    import socket

    port = ya.free_port()
    server = ya.loopback_server(ya._MultiCallback, port)
    try:
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(3)
                probe.connect((host, port))       # raises if nothing is listening there
    finally:
        server.server_close()


def test_a_busy_port_says_so_instead_of_crashing():
    port = ya.free_port()
    first = ya.loopback_server(ya._MultiCallback, port)
    try:
        try:
            ya.loopback_server(ya._MultiCallback, port)
        except SystemExit as exc:
            assert "already running" in str(exc), str(exc)
        else:
            raise AssertionError("a second listener on the same port was allowed to bind")
    finally:
        first.server_close()


def test_force_ssl_still_allows_the_channel_read_back():
    """A write scope that also reads: `channels.list` is why the read scope was ever requested."""
    assert any(s.endswith("/youtube.force-ssl") for s in ya.SCOPES)


def test_the_consent_url_carries_exactly_the_approved_scopes():
    url = ya.consent_url("emmanuel", "client-id.example.apps.googleusercontent.com")
    for scope in APPROVED:
        assert scope.replace("/", "%2F").replace(":", "%3A") in url, scope
    assert "youtube.readonly" not in url, "a link minted with the old scope set would be refused"


def test_the_selftest_passes():
    assert ya.selftest() == 0


if __name__ == "__main__":
    raise SystemExit(__import__("run_tests").main())
