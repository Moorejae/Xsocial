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
import socketserver
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

# The narrowest scope that can publish: youtube.upload covers videos.insert. `openid email` is
# there so the operator can see which account consented; it grants no YouTube capability.
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "openid", "email")


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
    prefix = workflow.upper() + "_GOOGLE_"
    cid = client_id or read_env(prefix + "CLIENT_ID")
    secret = client_secret or read_env(prefix + "CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit(
            f"{workflow}: client id/secret missing. Set {prefix}CLIENT_ID and {prefix}CLIENT_SECRET "
            f"in .env (from that client's own Google console), or pass --client-id / --client-secret."
        )
    return cid, secret


def consent_url(workflow: str, client_id: str, port: int = DEFAULT_PORT) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(workflow, port),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",          # without this a re-run returns no refresh_token
        "include_granted_scopes": "true",
        "state": f"x-{workflow}-{int(time.time())}",
    }
    return AUTH_URI + "?" + urllib.parse.urlencode(params)


def post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        # Google echoes no secret here, but scrub defensively before it reaches a terminal.
        for value in fields.values():
            if isinstance(value, str) and len(value) > 20:
                body = body.replace(value, "<redacted>")
        raise SystemExit(f"token endpoint HTTP {exc.code}: {body[:300]}")


def whoami(access_token: str) -> tuple[str, str]:
    """Read back the authorised channel. This is the evidence that consent landed on the right one."""
    url = CHANNELS_URI + "?" + urllib.parse.urlencode({"part": "snippet", "mine": "true"})
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    items = body.get("items") or []
    if not items:
        return "(no channel on this account)", "(none)"
    return items[0]["snippet"]["title"], items[0]["id"]


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


def interactive_consent(workflow: str, client_id: str, client_secret: str, port: int) -> int:
    url = consent_url(workflow, client_id, port)
    _Callback.expected_path = f"/{workflow}/callback"

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", port), _Callback) as httpd:
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

    title, channel_id = whoami(tokens["access_token"])
    payload = {
        "workflow": workflow,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": redirect_uri(workflow, port),
        "scopes": tokens.get("scope", " ".join(SCOPES)).split(),
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
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube OAuth for one X workflow (never prints a secret)")
    ap.add_argument("--workflow", choices=WORKFLOWS)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--client-id", default="")
    ap.add_argument("--client-secret", default="")
    ap.add_argument("--selftest", action="store_true",
                    help="check the encryption at rest without touching Google")
    ap.add_argument("--print-url", action="store_true",
                    help="print the consent URL only. The client id appears in it by design — "
                         "Google puts it in every consent URL; the secret never does.")
    ap.add_argument("--access-token", action="store_true", help="refresh and report the channel")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not a.workflow:
        ap.error("--workflow is required unless --selftest is given")

    if a.print_url:
        # The secret is deliberately not required here: showing the consent URL is a read-only,
        # secret-free operation and it is the fastest way to check the redirect URI by eye.
        cid = a.client_id or read_env(a.workflow.upper() + "_GOOGLE_CLIENT_ID")
        if not cid:
            raise SystemExit(f"{a.workflow}: no client id. Pass --client-id or set "
                             f"{a.workflow.upper()}_GOOGLE_CLIENT_ID in .env")
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
