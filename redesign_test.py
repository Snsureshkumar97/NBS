"""The redesign (16 Oct 2026): the new look's fonts are served from vendor/fonts, and both of its schemes keep
every text colour readable - 4.5 : 1 against the page and against a panel, the WCAG AA level for body text.

The user, after the clickable preview: "yes i like this ... work the same theme design on website also"."""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "web_server.py"), encoding="utf-8").read()
FAILS = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def lum(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


R0 = SRC.index("THE REDESIGN (16 Oct 2026)")
BLOCK = SRC[R0:SRC.index("</style>", R0)]


def tokens(selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", BLOCK)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", m.group(1))) if m else {}


print("1. THE FONTS COME FROM THIS SERVER")
for f in ("geist.woff2", "geist-mono.woff2", "bricolage.woff2"):
    path = os.path.join(HERE, "vendor", "fonts", f)
    check(f"vendor/fonts/{f} is there and is a woff2 file",
          os.path.exists(path) and open(path, "rb").read(4) == b"wOF2", path)
    check(f"...and the page's @font-face asks for /vendor/fonts/{f}", f'url("/vendor/fonts/{f}")' in BLOCK)
check("the licences travel with them", all(os.path.exists(os.path.join(HERE, "vendor", "fonts", n))
                                         for n in ("OFL-geist.txt", "OFL-bricolage.txt")))
check("the route serves only those three files, as font/woff2",
      '_FONT_FILES = frozenset({"geist.woff2", "geist-mono.woff2", "bricolage.woff2"})' in SRC
      and 'return self._send(body, "font/woff2")' in SRC
      and 'if path.startswith("/vendor/fonts/"):\n                return self._font(path[len("/vendor/fonts/"):])' in SRC)
check("nothing is loaded from another site (the page's own policy stays default-src 'self')",
      "fonts.googleapis" not in SRC and "fonts.gstatic" not in SRC and "default-src 'self' 'unsafe-inline'" in SRC)

print("2. BOTH SCHEMES STAY READABLE")
light = tokens(':root[data-look="kite"]')
dark = dict(light, **tokens(':root[data-look="kite"][data-scheme="dark"]'))
check("the survey found both schemes' colours", len(light) >= 30 and len(dark) >= 30, (len(light), len(dark)))
for name, t in (("white", light), ("dark", dark)):
    for ink in ("ink", "ink-2", "ink-3", "up", "down", "warn", "accent", "accent-text", "note-ink"):
        grounds = ("note-bg",) if ink == "note-ink" else ("bg", "surface", "raised")
        worst = min(ratio(t[ink], t[g]) for g in grounds)
        check(f"{name}: --{ink} reads at {worst:.2f} : 1 or better on the page and its panels", worst >= 4.5, worst)
    check(f"{name}: white type on the action blue reads at 4.5 : 1", ratio("#ffffff", t["accent-strong"]) >= 4.5,
          ratio("#ffffff", t["accent-strong"]))

print("3. THE MARKETS NEVER SHARE A SCREEN, AND THE SWITCH IS IN THE BAR")
check("a switch in the menu bar, built from the markets this server runs (hidden with only one)",
      '<div class="mseg" id="mseg" role="group" aria-label="Market" hidden></div>' in SRC
      and "ms.hidden = opts.length < 2;" in SRC)
check("...named the way the user says it: Indian markets, Crypto & Gold",
      '({nse_index: "Indian markets", crypto: "Crypto & Gold"})' in SRC)
check("...and it switches through the same /market post as the dropdowns (the server keeps one market per session)",
      'if(b && b.getAttribute("aria-pressed") !== "true") switchMarketOn({value: b.dataset.mseg});' in SRC
      and 'const body = new URLSearchParams({market: sel.value});' in SRC)
check("on a wide screen the watchlist itself moves into the row of tiles under the menu - the same element, so its "
      "clicks and prices are the watchlist's own",
      'rail.appendChild(mk);' in SRC and ".mrail > #markets{display:grid;" in BLOCK)
check("...and the left column keeps its other boxes (open trade, today, funds)", '<div class="kside" id="kside"></div>' in SRC)

print("4. STILL NOTHING MOVES")
check("the redesign adds no animation and no transition", "animation" not in BLOCK.split("*/", 1)[1]
      and "transition" not in BLOCK.split("*/", 1)[1])

print()
if FAILS:
    print(f"REDESIGN TEST FAILED - {len(FAILS)}: " + "; ".join(FAILS))
    sys.exit(1)
print("REDESIGN TEST PASSED")
