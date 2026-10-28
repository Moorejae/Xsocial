# Fonts

Derived from the typefaces REZMO itself ships (`REZMO-FRONTEND-DESIGN-SPEC/public/fonts`), so the
captions use the same family the brand does rather than a lookalike.

| File | Source | Weight |
| :--- | :--- | :--- |
| `Jost-Bold.ttf` | `jost.woff2` (variable, wght 100–900) | 700 — **video captions** |
| `Jost-Medium.ttf` | `jost.woff2` | 500 |
| `CormorantGaramond-SemiBold.ttf` | `cormorant-garamond.woff2` (variable, 300–700) | 600 — **watermark** |
| `CormorantGaramond-SemiBoldItalic.ttf` | `cormorant-garamond-italic.woff2` (static) | as shipped |

Both families are **SIL Open Font License 1.1**, which permits embedding and redistribution. The
licence text travels with the fonts and must not be removed.

Regenerate with:

```powershell
python scripts\make_fonts.py
```

Why static weights: ffmpeg's `subtitles` and `drawtext` filters take a font *file*, and a woff2 is
not one. Worse, handing libass a variable font and asking for bold gives the default instance — body
weight with a synthesised smear, not a real bold. Instantiating `wght=700` produces a genuine bold
outline, which is what makes a caption readable over moving footage.
