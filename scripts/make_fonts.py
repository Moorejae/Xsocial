#!/usr/bin/env python3
"""Build the caption and watermark fonts from REZMO's own variable fonts.

The client asked for "the same font style as used in rezmo", and REZMO ships two SIL-OFL variable
faces (`public/fonts/jost.woff2`, `cormorant-garamond.woff2`). ffmpeg's `subtitles`/`drawtext`
filters need a real TrueType file, not a woff2, and they need a **static** weight: declaring a
bold style against a variable font gives you whatever the default instance is, i.e. synthetic bold
at best and body weight at worst.

So this instantiates the exact weight from the exact file, once, and commits the result:

    Jost-Bold.ttf                     wght 700   captions (bold, as asked)
    Jost-Medium.ttf                   wght 500   spare
    CormorantGaramond-SemiBold.ttf    wght 600   watermark / brand moments
    CormorantGaramond-SemiBoldItalic.ttf         (already static in REZMO)

    python scripts\\make_fonts.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts"

# REZMO's own copies. If the project moves, this is the one path to change.
SOURCES = [
    Path(r"C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\public\fonts"),
    Path(r"C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\dist\fonts"),
]

JOBS = [
    ("jost.woff2", 700, "Jost-Bold.ttf"),
    ("jost.woff2", 500, "Jost-Medium.ttf"),
    ("cormorant-garamond.woff2", 600, "CormorantGaramond-SemiBold.ttf"),
    ("cormorant-garamond-italic.woff2", None, "CormorantGaramond-SemiBoldItalic.ttf"),
]


def source_dir() -> Path:
    for d in SOURCES:
        if d.is_dir() and any(d.glob("*.woff2")):
            return d
    raise SystemExit("REZMO font folder not found; edit SOURCES in scripts/make_fonts.py")


def main() -> int:
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    src = source_dir()
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"  source: {src}")

    for name, weight, dst_name in JOBS:
        path = src / name
        if not path.exists():
            print(f"  SKIP {name} (not in {src})")
            continue
        font = TTFont(str(path))
        variable = "fvar" in font
        if weight is not None and variable:
            font = instantiateVariableFont(font, {"wght": weight}, inplace=False, updateFontNames=True)
        font.flavor = None  # write plain TTF, not woff2
        dst = OUT / dst_name
        font.save(str(dst))
        axes = [(a.axisTag, a.minValue, a.maxValue) for a in font["fvar"].axes] if "fvar" in font else []
        print(f"  {dst_name:<38} {dst.stat().st_size // 1024:>4} KB  "
              f"weight={weight if weight else 'as-shipped'}  {'variable' if variable else 'static'}"
              + (f"  axes={axes}" if axes else ""))

    print(f"\n{len(list(OUT.glob('*.ttf')))} fonts in {OUT.relative_to(ROOT)}")
    print("Licence: SIL Open Font License 1.1 — see fonts/OFL.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
