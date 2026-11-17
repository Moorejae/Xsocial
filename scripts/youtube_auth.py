#!/usr/bin/env python3
"""Get, and later refresh, a YouTube upload credential for one workflow.

Why loopback and not a web callback
-----------------------------------
The bot runs unattended on the VPS and never needs to receive a browser request. So the consent
step happens once, here, on this workstation, against a **Desktop app** OAuth client whose redirect
URI is a loopback address:

    http://localhost:8765/jephter/callback
    http://localhost:8765/emmanuel/callback

Google rejects a bare IP as a redirect URI, and a Web client needs a public domain over HTTPS —
neither of which exists yet, and neither of which an unattended uploader needs. The refresh token
that comes back is long-lived, so this runs once per workflow and never again unless access is
revoked.

What is stored, and where
-------------------------
`secrets/youtube/<workflow>.json` (git-ignored by shape, mode 0600) holds the client id, the client
secret and the refresh token. Nothing is printed: the output is the token length, the expiry, and
— the useful part — the **channel title and id that actually authorised**, read back from
`channels.list(mine=true)`, which is the only proof that consent went to the right channel.

Usage
-----
    python scripts\\youtube_auth.py --workflow jephter            # opens a browser
    python scripts\\youtube_auth.py --workflow jephter --print-url # just show the consent URL
    python scripts\\youtube_auth.py --workflow jephter --access-token   # refresh + show what it can do

Client id and secret come from `.env` (`JEPHTER_GOOGLE_CLIENT_ID` / `_CLIENT_SECRET`,
`EMMANUEL_GOOGLE_CLIENT_ID` / `_CLIENT_SECRET`) or from `--client-id` / `--client-secret`.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SECRETS = ROOT / "secrets" / "youtube"
WORKFLOWS = ("jephter", "emmanuel")
DEFAULT_PORT = 8765

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
CHANNELS_URI = "https://www.googleapis.com/youtube/v3/channels"

# The scopes to request. **These must match the app's approved Data Access list, character for
# character** — a production app refuses a sensitive scope that is not in it, and Google's number-one
# verification rejection is exactly this mismatch.
#
# One app serves three channels (ForChi + jephter + emmanuel) and its approved list is
# `youtube.upload` + `youtube.force-ssl`. `youtube.readonly` used to be requested here to make the
# channel read-back possible; `youtube.force-ssl` grants that read **and** write, and is the one
# Google approved (measured 2026-11-14: still Testing then; verified to production 2026-11-17).
# `youtube.upload` alone covers videos.insert but NOT channels.list — asking for only that made every
# verification call answer 403 and the token was discarded unsaved (2026-11-01, both channels).
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "openid",
    "email",
)


def scopes() -> list[str]:
    """The scopes to request: the minimum above, plus anything named in `X_EXTRA_SCOPES`.

    Why the seam still exists: it is how a scope is added without editing code — set
    `X_EXTRA_SCOPES=…` and the next consent carries it. Note that `youtube.force-ssl` is now in the
    default set, so `videos.update` — the only way to correct a title, description, tags or privacy
    after upload — works with the ordinary token instead of needing a special consent.
    """
    extra = [s.strip() for s in read_env("X_EXTRA_SCOPES").replace(",", " ").split() if s.strip()]
    out = list(SCOPES)
    for s in extra:
        if s not in out:
            out.append(s)
    return out


def read_env(name: str) -> str:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith(name + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def redirect_uri(workflow: str, port: int = DEFAULT_PORT) -> str:
    """Must match the console character for character, trailing path included."""
    return f"http://localhost:{port}/{workflow}/callback"


def credentials(workflow: str, client_id: str = "", client_secret: str = "") -> tuple[str, str]:
    """Client id + secret for a workflow, falling back to the shared client.

    Since 2026-11-01 both X channels' Google accounts were added to the audience of the OAuth client
    ForChi already uses, so there is **one client and three channels**. That means the console work is
    done once and each channel only has to sign in. The chain is:

    1. `--client-id` / `--client-secret`, or `<WORKFLOW>_GOOGLE_CLIENT_ID` / `_SECRET` in `.env`;
    2. `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in this project's `.env` (the shared client);
    3. ForChi's `.env` — `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` — read in place, never copied.
    """
    prefix = workflow.upper() + "_GOOGLE_"
    cid = client_id or read_env(prefix + "CLIENT_ID")
    secret = client_secret or read_env(prefix + "CLIENT_SECRET")
    if not (cid and secret):
        cid = cid or read_env("GOOGLE_CLIENT_ID")
        secret = secret or read_env("GOOGLE_CLIENT_SECRET")
    if not (cid and secret):
        forchi = Path(r"C:\Users\hp\forchi\.env")
        cid = cid or read_env_in(forchi, "YOUTUBE_CLIENT_ID")
        secret = secret or read_env_in(forchi, "YOUTUBE_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit(
            f"{workflow}: no OAuth client. Set {prefix}CLIENT_ID/_SECRET, or the shared "
            f"GOOGLE_CLIENT_ID/_SECRET, or let it read ForChi's YOUTUBE_CLIENT_ID/_SECRET."
        )
    return cid, secret


def read_env_in(path: Path, name: str) -> str:
    """Read one setting from an arbitrary env file. Never logged."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith(name + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def consent_url(workflow: str, client_id: str, port: int = DEFAULT_PORT) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(workflow, port),
        "response_type": "code",
        "scope": " ".join(scopes()),
        "access_type": "offline",
        "prompt": "consent",          # without this a re-run returns no refresh_token
        "include_granted_scopes": "true",
        "state": f"x-{workflow}-{int(time.time())}",
    }
    return AUTH_URI + "?" + urllib.parse.urlencode(params)


def post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={
        "content-type": "application/x-www-form-urlencoded",
        # Some Google endpoints answer 403 to a request with no User-Agent at all.
        "user-agent": "x-youtube-auth/1.0",
        "accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        # Google explains the reason in the body; without it a 403 is unactionable. Scrub the
        # request's own secrets before the text can reach a page or a terminal.
        for value in fields.values():
            if isinstance(value, str) and len(value) > 16:
                body = body.replace(value, "<redacted>")
        raise SystemExit(f"token endpoint HTTP {exc.code}: {body[:400]}")


NO_CHANNEL_TITLE = "(no channel on this account)"


def whoami(access_token: str) -> tuple[str, str]:
    """Read back the authorised channel. This is the evidence that consent landed on the right one.

    `channels.list(mine=true)` needs `youtube.force-ssl` (or `youtube.readonly`). Measured 2026-11-17:
    the app's approved scope list is `youtube.upload` + `youtube.force-ssl`, and force-ssl is a read
    scope as well as a write one — so this call works with the pair Google actually verified.
    """
    url = CHANNELS_URI + "?" + urllib.parse.urlencode({"part": "snippet", "mine": "true"})
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    items = body.get("items") or []
    if not items:
        return NO_CHANNEL_TITLE, "(none)"
    return items[0]["snippet"]["title"], items[0]["id"]


def whoami_safe(access_token: str) -> tuple[str, str]:
    """`whoami` that never raises.

    A refresh token is the thing consent exists to produce. Reading the channel back is evidence, not
    the deliverable, so a failure here must not discard the token — on 2026-11-01 a 403 from
    channels.list made both channels look unauthorised while the exchange had actually succeeded.

    The one answer that is **not** a failure is "this account owns no channel". A successful read that
    returns nothing means the wrong Google account was picked, and that token can never upload. ForChi
    recorded the cost of saving one anyway (2026-11-17, `identifyChannel` now refuses it): a working
    channel's token was overwritten by an account that owned no channel, and the channel stopped.
    """
    try:
        return whoami(access_token)
    except Exception as exc:                             # noqa: BLE001 - report, never fatal
        print(f"  could not read the channel back ({type(exc).__name__}: {exc}); token kept", flush=True)
        return "(unread)", f"({type(exc).__name__})"


def owns_no_channel(title: str, channel_id: str) -> bool:
    """True only when the read *succeeded* and found no channel — i.e. the wrong Google account.

    Distinct from `(unread)`, which means the check itself failed and the token must be kept.
    """
    return channel_id == "(none)" or title == NO_CHANNEL_TITLE


def save(workflow: str, payload: dict) -> Path:
    """Write the credential with the two secret fields sealed under AES-GCM.

    The privacy policy served to Google says the refresh token is *encrypted at rest*, so it is.
    Metadata (channel title, scopes, timestamps) stays readable so the file can be inspected without
    a key; the client secret and refresh token are sealed, bound to the workflow name as associated
    data so a file cannot be swapped between the two channels.
    """
    key = master_key()
    sealed = dict(payload)
    sealed["enc"] = "aesgcm-v1"
    for field in ("client_secret", "refresh_token"):
        if payload.get(field):
            sealed[field + "_enc"] = _seal(key, payload[field], workflow)
            sealed.pop(field, None)
    return _write(sealed, SECRETS / f"{workflow}.json")


def _write(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load(workflow: str) -> dict:
    path = SECRETS / f"{workflow}.json"
    if not path.exists():
        raise SystemExit(f"{workflow}: no stored credential. Run the consent flow first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("enc") == "aesgcm-v1":
        key = master_key()
        for field in ("client_secret", "refresh_token"):
            if payload.get(field + "_enc"):
                payload[field] = _unseal(key, payload[field + "_enc"], workflow)
    return payload


# --------------------------------------------------------------------------------------
# Encryption at rest
# --------------------------------------------------------------------------------------

def master_key() -> bytes:
    """32 bytes from `X_SECRET_KEY` in .env, else from secrets/master.key (created once, 0600).

    Keeping the key in `.env` means the ciphertext and the key are not in the same file. The
    file fallback exists so the tool works unattended; it is weaker, and the note printed on
    creation says so.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401  (fail early if absent)

    env = read_env("X_SECRET_KEY")
    if env:
        raw = base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
        if len(raw) != 32:
            raise SystemExit("X_SECRET_KEY in .env must be base64 of exactly 32 bytes")
        return raw

    path = ROOT / "secrets" / "master.key"
    if path.exists():
        raw = base64.urlsafe_b64decode(path.read_text(encoding="utf-8").strip())
        if len(raw) != 32:
            raise SystemExit(f"{path} does not hold 32 bytes")
        return raw

    raw = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.urlsafe_b64encode(raw).decode(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"created {path.relative_to(ROOT)} - preferably copy its contents into .env as X_SECRET_KEY "
          f"and delete the file, so the key does not sit beside the data it protects.", file=sys.stderr)
    return raw


def _seal(key: bytes, plaintext: str, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), aad.encode())).decode()


def _unseal(key: bytes, blob: str, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(blob)
    return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode()).decode()


class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    expected_path = "/"

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != self.expected_path.rstrip("/"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _Callback.result = {k: v[0] for k, v in params.items()}
        ok = "code" in _Callback.result
        body = (
            "<h2>Authorised.</h2><p>You can close this tab and return to the terminal.</p>"
            if ok else
            f"<h2>Not authorised.</h2><p>{_Callback.result.get('error', 'no code returned')}</p>"
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep the terminal clean
        pass


class _LoopbackServer(socketserver.ThreadingTCPServer):
    """A loopback listener that answers on **both** `127.0.0.1` and `[::1]`.

    This is not a nicety. Browsers resolve `localhost` to `::1` (IPv6) first and only fall back to
    IPv4; a listener bound to `127.0.0.1` alone therefore gives the owner "this site can't be
    reached" *after* a successful approval, and the code is lost. Measured 2026-11-17: the old
    listener answered `http://127.0.0.1:8765/...` with 200 while `http://[::1]:8765/...` was actively
    refused — which is exactly the error the owner saw twice. `IPV6_V6ONLY=0` makes one IPv6 socket
    accept IPv4 connections too, so either resolution works.

    `allow_reuse_address` is left **off** on purpose: on Windows `SO_REUSEADDR` lets a second process
    bind a port that is already listening and steal its traffic, so two listeners would look healthy
    and answer unpredictably. A clash now fails loudly instead.
    """
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def loopback_server(handler, port: int) -> "_LoopbackServer":
    """Bind the dual-stack loopback listener, turning "port busy" into a sentence."""
    try:
        return _LoopbackServer(("::", port), handler)
    except OSError as exc:
        raise SystemExit(
            f"cannot listen on port {port} ({exc}). Another listener is probably already running — "
            f"close it (or use --port {port + 1} and register that redirect URI first)."
        ) from None


def free_port() -> int:
    """An unused loopback port. For tests, and for suggesting `--port` after a clash."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def interactive_consent(workflow: str, client_id: str, client_secret: str, port: int) -> int:
    url = consent_url(workflow, client_id, port)
    _Callback.expected_path = f"/{workflow}/callback"

    with loopback_server(_Callback, port) as httpd:
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        print("Open this URL and approve the upload permission for THIS channel:\n")
        print("  " + url + "\n")
        print(f"Waiting for the callback on {redirect_uri(workflow, port)} ...")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        thread.join(timeout=600)

    result = _Callback.result
    if "code" not in result:
        print(f"no authorisation code received ({result.get('error', 'timeout')})", file=sys.stderr)
        return 1

    tokens = post_form(TOKEN_URI, {
        "code": result["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri(workflow, port),
        "grant_type": "authorization_code",
    })
    if "refresh_token" not in tokens:
        print(f"no refresh_token returned (keys={sorted(tokens)}); re-run — 'prompt=consent' should force one",
              file=sys.stderr)
        return 1

    title, channel_id = whoami_safe(tokens["access_token"])
    if owns_no_channel(title, channel_id):
        print(
            "\n  REFUSING: that Google account owns no YouTube channel, so the token could never\n"
            "  upload — and saving it would replace a working one. Nothing was written.\n"
            "  Sign in as the account that owns the channel, or create a channel for it first.",
            file=sys.stderr, flush=True)
        return 1
    payload = {
        "workflow": workflow,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": redirect_uri(workflow, port),
        "scopes": tokens.get("scope", " ".join(scopes())).split(),
        "channel_title": title,
        "channel_id": channel_id,
        "obtained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = save(workflow, payload)

    print(f"\nstored {path.relative_to(ROOT)} (mode 0600)")
    print(f"  refresh_token length: {len(tokens['refresh_token'])}")
    print(f"  access_token expires in: {tokens.get('expires_in')} s")
    print(f"  authorised channel: {title} ({channel_id})")
    print("\nIf the channel above is not the one you meant, revoke at "
          "https://myaccount.google.com/permissions and run this again.")
    return 0


def refresh(workflow: str) -> str:
    """Mint a fresh access token from the stored refresh token."""
    cred = load(workflow)
    tokens = post_form(TOKEN_URI, {
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    })
    if "access_token" not in tokens:
        raise SystemExit(f"{workflow}: refresh failed ({sorted(tokens)}); re-consent if access was revoked")
    return tokens["access_token"]


def selftest() -> int:
    """Network-free proof that the sealing does what the privacy policy claims."""
    import tempfile

    key = master_key()
    secret = "synthetic-refresh-token-value-0123456789"
    blob = _seal(key, secret, "jephter")
    if _unseal(key, blob, "jephter") != secret:
        print("FAIL round-trip returned something else")
        return 1
    print("ok   seal/unseal round-trip")

    try:
        _unseal(key, blob, "emmanuel")
        print("FAIL ciphertext accepted under the wrong workflow name")
        return 1
    except Exception:
        print("ok   ciphertext rejected under the wrong workflow name")

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "probe.json"
        _write({"workflow": "jephter", "refresh_token_enc": blob, "channel_title": "probe"}, path)
        text = path.read_text(encoding="utf-8")
        if secret in text:
            print("FAIL plaintext is present in the written file")
            return 1
        mode = oct(path.stat().st_mode)[-3:]
        platform_note = "posix mode, enforced on Linux" if os.name == "posix" else "posix mode is not enforced on Windows"
    print(f"ok   no plaintext in the written file ({mode}: {platform_note})")

    # The scope request: the approved list plus anything named on purpose. Checked without touching
    # the real .env, so a stray value can never make this test pass by accident.
    global ENV_FILE
    original = ENV_FILE
    extra_probe = "https://www.googleapis.com/auth/youtubepartner"
    try:
        if scopes() != list(SCOPES):
            print("FAIL the default scope request is not the plain SCOPES list")
            return 1
        print("ok   the scope request is SCOPES unless X_EXTRA_SCOPES says otherwise")
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / ".env"
            probe.write_text(f"X_EXTRA_SCOPES={extra_probe}\n", encoding="utf-8")
            ENV_FILE = probe
            widened = scopes()
            if widened[:len(SCOPES)] != list(SCOPES) or widened[-1] != extra_probe:
                print(f"FAIL X_EXTRA_SCOPES did not append cleanly: {widened}")
                return 1
            probe.write_text("X_EXTRA_SCOPES=\n", encoding="utf-8")
            if scopes() != list(SCOPES):
                print("FAIL an empty X_EXTRA_SCOPES changed the request")
                return 1
        print("ok   X_EXTRA_SCOPES appends, and an empty value leaves the request alone")
    finally:
        ENV_FILE = original
    return 0


class _MultiCallback(http.server.BaseHTTPRequestHandler):
    """Handles `<workflow>/callback` for any workflow while serving.

    The single-shot listener was fine for one channel and actively misleading for two: the owner
    authorised, the browser hit a port nobody was listening on, and the token was lost with no error
    anywhere. This one stays up, dispatches on the path, exchanges each code and saves each token as
    it arrives, so several channels can be authorised from several links in one session.
    """
    saved: dict = {}
    expected: dict = {}          # workflow -> (client_id, client_secret, port)
    stop_after: int = 4

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 2 or parts[1] != "callback" or parts[0] not in self.expected:
            self.send_response(404)
            self.end_headers()
            return
        workflow = parts[0]
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        ok, message = False, params.get("error", "no code returned")
        if "code" in params:
            cid, secret, port = self.expected[workflow]
            try:
                tokens = post_form(TOKEN_URI, {
                    "code": params["code"], "client_id": cid, "client_secret": secret,
                    "redirect_uri": redirect_uri(workflow, port), "grant_type": "authorization_code",
                })
                if "refresh_token" not in tokens:
                    message = "no refresh_token returned; re-run (prompt=consent should force one)"
                else:
                    # A token is the thing we came for. Saving it must not depend on the read-back
                    # succeeding: on 2026-11-01 a 403 from channels.list (missing read scope) threw
                    # away two perfectly good refresh tokens.
                    title, channel_id = whoami_safe(tokens["access_token"])
                    granted_scopes = tokens.get("scope", " ".join(scopes())).split()
                    short = " ".join(s.rsplit("/", 1)[-1] for s in granted_scopes)
                    if owns_no_channel(title, channel_id):
                        # The wrong Google account was picked. Its token can never upload, and saving
                        # it would replace a working one — ForChi lost a channel to exactly this on
                        # 2026-11-17, so the file is left alone and the page says what to do.
                        ok = False
                        message = (f"{workflow}: NOT saved — that Google account owns no YouTube "
                                   f"channel. Open the link again and choose the account that owns "
                                   f"the {workflow} channel.")
                        print(f"\n  REFUSED {message}", flush=True)
                        self._respond(ok, message)
                        return
                    save(workflow, {
                        "workflow": workflow, "client_id": cid, "client_secret": secret,
                        "refresh_token": tokens["refresh_token"],
                        "redirect_uri": redirect_uri(workflow, port),
                        "scopes": granted_scopes,
                        "channel_title": title, "channel_id": channel_id,
                        "obtained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    })
                    _MultiCallback.saved[workflow] = {"channel_title": title, "channel_id": channel_id}
                    # A consent that omits youtube.upload cannot post. Say so on the page, while the
                    # owner is still looking at it, instead of letting the scheduler discover it.
                    if not any(s.endswith("/youtube.upload") for s in granted_scopes):
                        ok = False
                        message = (f"{workflow}: saved, but the YouTube permission was NOT granted "
                                   f"(granted: {short}). Open the {workflow} link again and tick it.")
                        print(f"\n  MISSING SCOPE {message}", flush=True)
                    else:
                        ok = True
                        message = f"{workflow}: authorised {title} ({channel_id}) - granted: {short}"
                        print(f"\n  SAVED {message}", flush=True)
            except SystemExit as exc:
                message = str(exc)[:200]
            except Exception as exc:                     # noqa: BLE001 - report, never crash the server
                message = f"{type(exc).__name__}: {exc}"[:200]

        self._respond(ok, message)

    def _respond(self, ok: bool, message: str) -> None:
        """The page the browser lands on. One place, so an early return cannot send nothing."""
        body = (f"<h2>{'Authorised' if ok else 'Not authorised'}</h2><p>{message}</p>"
                + ("<p>You can close this tab.</p>" if ok else "")).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def serve(workflows: list[str], port: int, timeout: float = 900.0) -> int:
    """Listen for several channels' callbacks at once and save each token as it arrives."""
    for wf in workflows:
        cid, secret = credentials(wf)
        _MultiCallback.expected[wf] = (cid, secret, port)
        print(f"  {wf}: {consent_url(wf, cid, port)}", flush=True)

    print(f"\n  listening on http://localhost:{port}/<channel>/callback for {timeout:.0f}s", flush=True)
    print("  open each link above, signed in as that channel's Google account, and approve.\n", flush=True)

    server = loopback_server(_MultiCallback, port)
    server.timeout = 2.0
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and len(_MultiCallback.saved) < len(workflows):
            server.handle_request()
            pending = [w for w in workflows if w not in _MultiCallback.saved]
            if pending and time.time() < deadline:
                continue
        else:
            pass
    finally:
        server.server_close()

    for wf in workflows:
        got = _MultiCallback.saved.get(wf)
        print(f"  {wf:<9} {'OK  ' + got['channel_title'] if got else 'NOT authorised'}", flush=True)
    missing = [w for w in workflows if w not in _MultiCallback.saved]
    return 0 if not missing else 1


def exchange_pasted(workflow: str, pasted: str, port: int,
                    client_id: str = "", client_secret: str = "") -> int:
    """Exchange a callback URL (or a bare code) that was produced on *someone else's* machine.

    The loopback redirect is why this exists. A consent link emailed to a channel owner opens on
    their computer, so when Google redirects to `http://localhost:8765/<wf>/callback` there is no
    listener there and the browser shows a connection error — but the **code is in the address bar**.
    That URL, pasted back here, is exchanged with the same redirect_uri the request used, which is
    what Google requires. Nothing secret needs to travel: only the short-lived code does.
    """
    text = (pasted or "").strip().strip('"').strip("'")
    if not text:
        raise SystemExit("nothing pasted")
    params = {}
    if "code=" in text or "error=" in text:
        query = text.split("?", 1)[1] if "?" in text else text
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    elif "://" in text:
        raise SystemExit("that URL carries no ?code= — copy the whole address bar after approving")
    else:
        params["code"] = text                      # a bare code
    if "error" in params:
        raise SystemExit(f"Google refused the consent: {params['error']} "
                         f"({params.get('error_description', 'no description')})")
    code = params.get("code")
    if not code:
        raise SystemExit("no ?code= in what was pasted")

    cid, secret = credentials(workflow, client_id, client_secret)
    tokens = post_form(TOKEN_URI, {
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri(workflow, port), "grant_type": "authorization_code",
    })
    if "refresh_token" not in tokens:
        print(f"no refresh_token returned (keys={sorted(tokens)}) — the account may have approved "
              f"before; revoke at myaccount.google.com/permissions and use a fresh link", file=sys.stderr)
        return 1
    title, channel_id = whoami_safe(tokens["access_token"])
    granted = tokens.get("scope", " ".join(scopes())).split()
    if owns_no_channel(title, channel_id):
        print(f"REFUSING: that Google account owns no YouTube channel, so this token could never "
              f"upload — and saving it would replace a working one. Nothing was written. Sign in as "
              f"the account that owns the {workflow} channel.", file=sys.stderr)
        return 1
    save(workflow, {
        "workflow": workflow, "client_id": cid, "client_secret": secret,
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": redirect_uri(workflow, port),
        "scopes": granted,
        "channel_title": title, "channel_id": channel_id,
        "obtained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    short = " ".join(s.rsplit("/", 1)[-1] for s in granted)
    print(f"stored {workflow}: {title} ({channel_id})")
    print(f"  granted scopes: {short}")
    if not any(s.endswith("/youtube.upload") for s in granted):
        print("  MISSING youtube.upload — this token cannot post; re-issue the link and tick it",
              file=sys.stderr)
        return 1
    return 0


def push(workflow: str) -> int:
    """Send this channel's sealed token to the host that publishes, and prove it there.

    Authorising on the laptop is the quickest path — `--serve` prints a link that works end to end —
    but the pipeline runs on the VPS, and a token saved only on the laptop is half a procedure: the
    host keeps refreshing the old one and the channel goes quiet a week later with nothing to show for
    it. That half-step is exactly what this closes.
    """
    local = SECRETS / f"{workflow}.json"
    if not local.exists():
        print(f"  no token at {local.relative_to(ROOT)} — authorise {workflow} first", file=sys.stderr)
        return 1
    vps = ROOT / "scripts" / "vps.py"
    remote_root = "/root/X"
    remote = f"{remote_root}/secrets/youtube/{workflow}.json"

    print(f"  pushing {local.relative_to(ROOT)} -> 169.58.212.7:{remote}")
    rc = subprocess.call([sys.executable, str(vps), "--put", str(local), remote])
    if rc != 0:
        print(f"  copy failed (rc={rc}); nothing was changed on the host", file=sys.stderr)
        return rc

    # chmod, then let the host's own uploader name the channel. If the host still refreshes the old
    # token, this prints the wrong channel or fails outright, on the command the operator just ran.
    check = (f"cd {remote_root} && chmod 600 secrets/youtube/*.json && "
             f".venv/bin/python scripts/youtube_upload.py --workflow {workflow} --check")
    rc = subprocess.call([sys.executable, str(vps), check])
    if rc != 0:
        print(f"  the host could not use the token it was given (rc={rc})", file=sys.stderr)
        return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube OAuth for one X workflow (never prints a secret)")
    ap.add_argument("--workflow", choices=WORKFLOWS)
    ap.add_argument("--serve", action="store_true",
                    help="listen for several channels at once (use with --workflow repeated or no --workflow "
                         "for both)")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--client-id", default="")
    ap.add_argument("--client-secret", default="")
    ap.add_argument("--from-url", default="",
                    help="exchange a callback URL (or bare code) produced on another machine — the "
                         "flow for a consent link emailed to a channel owner")
    ap.add_argument("--selftest", action="store_true",
                    help="check the encryption at rest without touching Google")
    ap.add_argument("--print-url", action="store_true",
                    help="print the consent URL only. The client id appears in it by design — "
                         "Google puts it in every consent URL; the secret never does.")
    ap.add_argument("--access-token", action="store_true", help="refresh and report the channel")
    ap.add_argument("--push", action="store_true",
                    help="copy this channel's sealed token to the publishing host and prove it there")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.serve:
        targets = [a.workflow] if a.workflow else list(WORKFLOWS)
        return serve(targets, a.port, a.timeout)

    if not a.workflow:
        ap.error("--workflow is required unless --selftest or --serve is given")

    if a.from_url:
        return exchange_pasted(a.workflow, a.from_url, a.port, a.client_id, a.client_secret)

    if a.push:
        return push(a.workflow)

    if a.print_url:
        # Use the same client chain as the real flow, so this check cannot disagree with it.
        try:
            cid, _ = credentials(a.workflow, a.client_id, a.client_secret)
        except SystemExit:
            cid = a.client_id or read_env(a.workflow.upper() + "_GOOGLE_CLIENT_ID")
            if not cid:
                raise
        print(consent_url(a.workflow, cid, a.port))
        print(f"\nredirect_uri registered in the console must be exactly: {redirect_uri(a.workflow, a.port)}",
              file=sys.stderr)
        return 0

    if a.access_token:
        token = refresh(a.workflow)
        title, channel_id = whoami(token)
        cred = load(a.workflow)
        print(f"{a.workflow}: access token ok ({len(token)} chars) for {title} ({channel_id})")
        print(f"  stored cred was obtained for: {cred.get('channel_title')} ({cred.get('channel_id')})")
        return 0

    cid, secret = credentials(a.workflow, a.client_id, a.client_secret)
    return interactive_consent(a.workflow, cid, secret, a.port)


if __name__ == "__main__":
    raise SystemExit(main())
