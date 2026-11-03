"""Push the clip and song libraries to the VPS, and its labels, without re-sending everything.

Why this exists
---------------
The media lives in two places on purpose: **here**, where clips are added and `label_emotions.py` is run,
and **on `169.58.212.7`**, which is what actually renders. Two copies drift the moment someone drops a new
clip in `jephtah/` and forgets to send it — and the symptom is a video that keeps using the same old
footage, which is easy to misread as "the picker is broken".

So this compares file name + size on both sides and transfers only what differs, including the manifests in
`config/scenes/`. It is safe to run repeatedly, and `--dry-run` shows the plan first.

Usage
-----
    python scripts\\sync_media.py --dry-run      # what would be sent
    python scripts\\sync_media.py                # send it
    python scripts\\sync_media.py --workflow jephter
    python scripts\\sync_media.py --verify       # compare only; exit 1 if the two sides differ

After a sync, nothing needs restarting: the next timer pass reads the files from disk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vps  # noqa: E402  — reuses the project's own credential chain and connection code

REMOTE_ROOT = "/root/X"
MANIFESTS = ("jephter", "emmanuel")


def local_tree(folder: Path) -> dict[str, int]:
    """{relative posix path: size} for every file under `folder`."""
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).as_posix(): p.stat().st_size
            for p in folder.rglob("*") if p.is_file()}


def remote_tree(client, folder: str) -> dict[str, int]:
    """{relative posix path: size} under /root/X/<folder>, from the far side."""
    cmd = (f"cd {REMOTE_ROOT}/{folder} 2>/dev/null && "
           f"find . -type f -printf '%P\\t%s\\n' || true")
    _stdin, stdout, _stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", "replace")
    tree = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        rel, _, size = line.partition("\t")
        rel = rel.strip()
        if rel:
            tree[rel] = int(size or 0)
    return tree


def _norm_digest(data: bytes) -> str:
    """Hash of the text with line endings normalised.

    Windows checks files out with CRLF and Linux with LF, so the same committed manifest has a
    different byte size on the two machines (measured: 26,710 vs 25,654). Comparing sizes would make
    every sync push four files that did not change — and pushing CRLF into the VPS worktree leaves
    git there reporting them as modified, for ever.
    """
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def manifest_by_name(client, name: str) -> str | None:
    sftp = client.open_sftp()
    try:
        with sftp.open(f"{REMOTE_ROOT}/config/scenes/{name}", "rb") as fh:
            return _norm_digest(fh.read())
    except OSError:
        return None
    finally:
        sftp.close()


def plan(local: dict[str, int], remote: dict[str, int]) -> tuple[list[str], list[str], list[str]]:
    """(to send, to delete remotely, already matched). Deletions are reported, never performed."""
    send = sorted(k for k, v in local.items() if remote.get(k) != v)
    gone = sorted(k for k in remote if k not in local)
    same = sorted(k for k, v in local.items() if remote.get(k) == v)
    return send, gone, same


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync the media libraries and their labels to the VPS")
    ap.add_argument("--workflow", choices=("jephter", "emmanuel", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, send nothing")
    ap.add_argument("--verify", action="store_true",
                    help="compare only; exit 1 if the two sides differ")
    a = ap.parse_args()

    names = MANIFESTS if a.workflow == "both" else (a.workflow,)
    cfg = {}
    try:
        import json
        cfg = json.loads((ROOT / "config" / "workflows.json").read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001 - defaults are fine
        cfg = {}
    client = vps.connect()
    try:
        total_send = 0
        drift = False
        for name in names:
            folder = Path(cfg.get(name, {}).get("media_dir", "jephtah" if name == "jephter" else name)) \
                if cfg else Path("jephtah" if name == "jephter" else name)
            local = local_tree(ROOT / folder)
            remote = remote_tree(client, folder)
            send, gone, same = plan(local, remote)
            total_send += len(send)
            label = "identical" if not send and not gone else f"{len(send)} to send, {len(gone)} only on the VPS"
            print(f"  {str(folder):9} {len(local):3} local / {len(remote):3} remote — {label}")
            if gone:
                drift = True
                for g in gone[:5]:
                    print(f"      only on the VPS: {g}")
                print("      (not deleted automatically; remove by hand if it was a rename)")
            if send:
                drift = True
                if a.verify:
                    for s in send[:8]:
                        print(f"      would send: {s}")
                    continue
                if a.dry_run:
                    for s in send[:8]:
                        print(f"      would send: {s}")
                    if len(send) > 8:
                        print(f"      ... and {len(send) - 8} more")
                    continue
                sftp = client.open_sftp()
                try:
                    for rel in send:
                        dest_dir = f"{REMOTE_ROOT}/{folder}"
                        if "/" in rel:
                            dest_dir = f"{dest_dir}/{rel.rsplit('/', 1)[0]}"
                        client.exec_command(f"mkdir -p '{dest_dir}'")[1].channel.recv_exit_status()
                        src = ROOT / folder / rel
                        print(f"      sending {rel} ({src.stat().st_size // 1024} KB)")
                        sftp.put(str(src), f"{REMOTE_ROOT}/{folder}/{rel}")
                finally:
                    sftp.close()
            # The manifests carry the labels; a clip without its label drives the wrong cut.
            for man in (f"{name}.json", f"{name}-labels.md"):
                src = ROOT / "config" / "scenes" / man
                if not src.is_file():
                    continue
                if manifest_by_name(client, man) == _norm_digest(src.read_bytes()):
                    continue
                drift = True
                if a.dry_run or a.verify:
                    print(f"      would send manifest: {man}")
                    continue
                sftp = client.open_sftp()
                try:
                    sftp.put(str(src), f"{REMOTE_ROOT}/config/scenes/{man}")
                    print(f"      sent manifest: {man}")
                finally:
                    sftp.close()
        print()
        if a.verify:
            print("identical" if not drift else "DRIFT: the two sides differ")
            return 0 if not drift else 1
        if a.dry_run:
            print(f"dry run: {total_send} file(s) would be sent")
        else:
            print("sync complete" if total_send or not drift else "already in sync")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
