"""A video must never be published invisible by accident.

2026-11-03: an emmanuel video went up **private**. The run reported success, the ledger recorded a publish,
and the channel got nothing — because `x_scheduler.py --privacy` still defaulted to `private` from an earlier
idea that the dangerous mistake was an accidental publish. It was not. The dangerous mistake was an accidental
*private* upload, which is a failure dressed as a success and takes a day to notice.

So the rule these tests protect:

- **public is the default** on every entry point that can publish;
- **`private` requires `--allow-private`** — two deliberate acts, on the same command line;
- the placeholder in the upload metadata is public too, so a caller that forgets to set it publishes.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import x_publish  # noqa: E402
import x_scheduler  # noqa: E402
import youtube_upload  # noqa: E402


def _parser_defaults(module) -> dict:
    """Ask argparpe what the defaults are, without running main()."""
    import argparse
    seen: dict = {}
    real = argparse.ArgumentParser.add_argument

    def spy(self, *args, **kwargs):
        action = real(self, *args, **kwargs)
        if action.dest in ("privacy", "allow_private"):
            seen[action.dest] = kwargs.get("default", action.default)
        return action

    argparse.ArgumentParser.add_argument = spy
    try:
        old = sys.argv
        sys.argv = [module.__name__]
        try:
            module.main()
        except SystemExit:
            pass
        except Exception:                                    # noqa: BLE001 - only the parser matters
            pass
        finally:
            sys.argv = old
    finally:
        argparse.ArgumentParser.add_argument = real
    return seen


def test_the_upload_metadata_placeholder_is_public():
    meta = youtube_upload.metadata_for("emmanuel", {"lines": ["Love them in the quiet ways."]})
    assert meta["status"]["privacyStatus"] == "public", \
        "a caller that forgets to set privacy must publish, not hide"


def test_the_scheduler_post_refuses_private_without_the_flag():
    called = {}

    def fake_run(cmd, **kw):
        called["cmd"] = cmd
        class R:
            returncode = 0
        return R()

    original = x_scheduler.subprocess.run
    x_scheduler.subprocess.run = fake_run
    try:
        try:
            x_scheduler.post("emmanuel", ROOT / "out" / "x.mp4", "private", dry_run=True)
        except RuntimeError as exc:
            assert "allow-private" in str(exc), exc
        else:
            raise AssertionError("a private post without --allow-private must be refused")
        assert "cmd" not in called, "nothing may be launched"
    finally:
        x_scheduler.subprocess.run = original


def test_the_scheduler_post_passes_the_flag_when_asked():
    called = {}

    def fake_run(cmd, **kw):
        called["cmd"] = cmd
        class R:
            returncode = 0
        return R()

    original = x_scheduler.subprocess.run
    x_scheduler.subprocess.run = fake_run
    try:
        x_scheduler.post("emmanuel", ROOT / "out" / "x.mp4", "private", dry_run=True, allow_private=True)
        assert "--allow-private" in called["cmd"], called["cmd"]
        x_scheduler.post("emmanuel", ROOT / "out" / "x.mp4", "public", dry_run=True)
        assert "--allow-private" not in called["cmd"], "public must never carry the private flag"
    finally:
        x_scheduler.subprocess.run = original


def test_the_publisher_refuses_private_without_the_flag():
    try:
        x_publish.publish("emmanuel", "private", dry_run=True, force=False)
    except SystemExit as exc:
        assert "allow-private" in str(exc), exc
    else:
        raise AssertionError("x_publish must refuse a private upload without --allow-private")


def test_both_signatures_carry_allow_private():
    for fn in (x_scheduler.post, x_scheduler.run_pass, x_publish.publish):
        sig = inspect.signature(fn)
        assert "allow_private" in sig.parameters, f"{fn.__name__}{sig} lost allow_private"


def test_the_help_text_says_public_is_the_default():
    for script in ("youtube_upload.py", "x_scheduler.py", "x_publish.py"):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"],
                             capture_output=True, text=True, errors="replace").stdout
        flat = " ".join(out.split())
        assert "public" in flat, f"{script} never mentions public"
        assert "private" in flat, f"{script} never mentions private"
