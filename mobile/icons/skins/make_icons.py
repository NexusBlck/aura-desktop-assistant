# Makes the home-screen / tab icons for each UI skin + theme (mobile/icons/skins/<skin>-<theme>-{180,192,512}.png) and a
# web manifest per skin so an install picks up the matching icon. Run again after changing a palette:  python make_icons.py
import json
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
F = r"C:\Windows\Fonts"
# skin, theme, background, panel, ink (text), accent
P = [("ledger", "paper", "#f3eee4", "#fbf8f1", "#211d18", "#8a2e1d"), ("ledger", "linen", "#f4f5f2", "#ffffff", "#141c26", "#1d3a5f"),
     ("ledger", "night", "#1a1916", "#22211d", "#ece6d8", "#c9a35b"),
     ("hmi", "daylight", "#cfd0cc", "#e4e5e1", "#151617", "#275c9b"), ("hmi", "nightshift", "#1c1f22", "#262a2e", "#d9dde0", "#4a8fd8"),
     ("hmi", "hazard", "#1a1a1a", "#242424", "#efefef", "#f2c200"),
     ("swiss", "signal", "#ffffff", "#ffffff", "#111111", "#e30613"), ("swiss", "klein", "#f6f6f3", "#ffffff", "#111111", "#002fa7"),
     ("swiss", "noir", "#0d0d0d", "#161616", "#f2f2f2", "#ff3b30"),
     ("console", "amber", "#120d04", "#1a1307", "#ffb000", "#ffc94d"), ("console", "phosphor", "#04100a", "#08180f", "#3cff6e", "#8bffaa"),
     ("console", "daylight", "#f4f4ef", "#ffffff", "#1d1d1b", "#005fcc"),
     ("haven", "sand", "#f3ece3", "#fffaf4", "#3a2e26", "#b85a36"), ("haven", "sage", "#ecf0ea", "#fbfcfa", "#25302a", "#46755a"),
     ("haven", "dusk", "#211d1a", "#2e2925", "#f1e7dc", "#e3a468")]


def font(name, size, variation=None):
    f = ImageFont.truetype(os.path.join(F, name), size)
    if variation:
        try:
            f.set_variation_by_name(variation)
        except Exception:
            pass
    return f


def centred(d, text, f, cx, cy, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=f, fill=fill)


def draw(skin, bg, panel, ink, acc, S=1024):
    im = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(im)
    if skin == "ledger":                       # a serif italic A over an ink rule - like a masthead
        centred(d, "A", font("georgiai.ttf", int(S * .62)), S * .5, S * .45, ink)
        d.rectangle([S * .28, S * .76, S * .72, S * .785], fill=acc)
    elif skin == "hmi":                        # a framed panel tile with a status square and a condensed A
        m = S * .14
        d.rectangle([m, m, S - m, S - m], fill=panel, outline=ink, width=int(S * .028))
        d.rectangle([m, m, S - m, m + S * .16], fill=ink)
        d.rectangle([m + S * .05, m + S * .045, m + S * .12, m + S * .115], fill=acc)
        centred(d, "A", font("bahnschrift.ttf", int(S * .5), "Bold Condensed"), S * .5, S * .58, ink)
    elif skin == "swiss":                      # a. - heavy grotesk, the full stop in the accent colour
        f = font("ariblk.ttf", int(S * .6))
        l, t, r, b = d.textbbox((0, 0), "a", font=f)
        w = r - l
        x = S * .5 - (w + S * .14) / 2 - l
        y = S * .5 - (t + b) / 2 - S * .02
        d.text((x, y), "a", font=f, fill=ink)
        dot = S * .12
        d.rectangle([x + l + w + S * .02, y + b - dot, x + l + w + S * .02 + dot, y + b], fill=acc)
    elif skin == "console":                    # >_ prompt with a block cursor
        f = font("consolab.ttf", int(S * .42))
        centred(d, ">", f, S * .36, S * .5, ink)
        d.rectangle([S * .52, S * .6, S * .74, S * .66], fill=acc)
    elif skin == "haven":                      # a soft status circle with a quiet ring
        r1, r2 = S * .32, S * .22
        d.ellipse([S / 2 - r1, S / 2 - r1, S / 2 + r1, S / 2 + r1], fill=acc)
        d.ellipse([S / 2 - r2, S / 2 - r2, S / 2 + r2, S / 2 + r2], fill=panel)
        centred(d, "a", font("segoeuib.ttf", int(S * .26)), S * .5, S * .49, acc)
    return im


manifest = json.load(open(os.path.join(HERE, "..", "..", "manifest.webmanifest"), encoding="utf-8"))
for skin, theme, bg, panel, ink, acc in P:
    big = draw(skin, bg, panel, ink, acc)
    for size in (180, 192, 512):
        big.resize((size, size), Image.LANCZOS).save(os.path.join(HERE, f"{skin}-{theme}-{size}.png"), optimize=True)
    m = dict(manifest)
    m["background_color"] = m["theme_color"] = bg
    m["icons"] = [{"src": f"/icons/skins/{skin}-{theme}-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": f"/icons/skins/{skin}-{theme}-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]
    json.dump(m, open(os.path.join(HERE, f"{skin}-{theme}.webmanifest"), "w", encoding="utf-8"), indent=2)
print("made", len(P), "icon sets")
