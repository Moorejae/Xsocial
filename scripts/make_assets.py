#!/usr/bin/env python3
"""Generate the static overlay assets the renderer composites.

One asset, and the reason it exists: these are stock vertical clips. Many of them already carry
their own burned-in caption near the bottom of the frame, and plenty are bright (snow, sky, white
walls) where a white caption loses its edge. A stepped or hand-drawn shadow does not fix that.

So the bottom of the frame gets a smooth black gradient — strong enough at the very bottom to mute
a clip's own caption and to sit under the YouTube UI, and transparent by the middle of the frame so
the picture is not dulled. The result is committed (`assets/scrim.png`) so the renderer never needs
PIL and never re-derives it:

    python scripts\\make_assets.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# (width, height, start fraction, peak alpha at the very bottom)
SCRIMS = [
    (1080, 1920, 0.55, 205),  # 9:16 vertical shorts
]


def make_scrim(width: int, height: int, start: float, peak: int, out: Path) -> Path:
    """RGBA image: fully transparent above `start`, then a quadratic ramp to `peak` alpha."""
    alpha = np.zeros((height, width), dtype=np.uint8)
    y = np.arange(height, dtype=np.float32) / height
    ramp = np.clip((y - start) / max(1e-6, (1.0 - start)), 0.0, 1.0) ** 1.7
    alpha[:, :] = (ramp * peak)[:, None].astype(np.uint8)

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0:3] = 0          # pure black
    rgba[..., 3] = alpha
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba).save(out)
    return out


def main() -> int:
    for w, h, start, peak in SCRIMS:
        path = make_scrim(w, h, start, peak, ASSETS / f"scrim_{w}x{h}.png")
        print(f"  {path.relative_to(ROOT)}  {w}x{h}  ramp from {start:.0%}  peak alpha {peak}")
    print(f"{len(list(ASSETS.glob('scrim_*.png')))} scrim(s) in {ASSETS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
