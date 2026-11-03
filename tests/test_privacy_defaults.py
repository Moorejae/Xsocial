"""No entry point may ever default to a private upload again.

The 2026-11-03 failure was not a typo: `private` was the *deliberate default* in three files, on the
theory that an accidental publish was the dangerous mistake. It was backwards — a private upload
reports success and the channel gets nothing — so the rule is now written into the source and pinned
here. This test reads the source rather than calling the parsers, because the thing that has to hold
is the default, not one invocation of it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import x_publish  # noqa: E402
import x_scheduler  # noqa: E402
import youtube_upload  # noqa: E402

ENTRY_POINTS = ("youtube_upload.py", "x_publish.py", "x_scheduler.py")

_PRIVACY_ARG = re.compile(r'add_argument\(\s*"--privacy"\s*,\s*default\s*=\s*"([^"]+)"')


def test_every_privacy_default_is_public():
    found = {}
    for name in ENTRY_POINTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        m = _PRIVACY_ARG.search(text)
        assert m, f"{name} has no --privacy default any more"
        found[name] = m.group(1)
    assert set(found.values()) == {"public"}, found


def test_every_entry_point_can_refuse_private():
    for name in ENTRY_POINTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "--allow-private" in text, f"{name} cannot refuse a private upload"
        assert "allow_private" in text, f"{name} does not carry the flag through"


def test_the_upload_url_placeholder_metadata_is_public():
    meta = youtube_upload.metadata_for("jephter", {"lines": ["I told myself I was fine."]})
    assert meta["status"]["privacyStatus"] == "public"


def test_the_scheduler_and_publisher_refuse_private_without_the_flag():
    for mod, fn, args in (
        (x_scheduler, "run_pass", (("jephter",), "private", False, None, False, True, False)),
        (x_publish, "publish", ("jephter", "private", False, False, False)),
    ):
        try:
            getattr(mod, fn)(*args)
        except SystemExit as exc:
            assert "allow-private" in str(exc), str(exc)
        except TypeError as exc:                       # signature moved: the test must not go quiet
            raise AssertionError(f"{mod.__name__}.{fn} signature changed: {exc}") from exc
        else:
            raise AssertionError(f"{mod.__name__}.{fn} accepted a private upload without --allow-private")


def test_the_runner_passes_public_on_its_own_command_line():
    """The unit's command line is the one that actually posts, so it must say it out loud."""
    sh = (SCRIPTS / "x_daily.sh").read_text(encoding="utf-8")
    assert "--privacy public" in sh, sh


if __name__ == "__main__":
    raise SystemExit(__import__("run_tests").main())
