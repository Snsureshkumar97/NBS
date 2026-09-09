#!/usr/bin/env python3
"""
theme.py — every colour and font in one place
================================================================================
The colours here are not chosen by eye. The ones that carry meaning were run
through the OKLab colour-vision validator, and the notes below record what it
said, so a future "let's brighten that green" can be checked instead of argued.

Surfaces are near-black with a violet cast; the accent is a magenta-to-violet
gradient used for CHROME ONLY — buttons, active tabs, the logo, the headline.
No data is ever encoded in the accent gradient.
"""

# ---------------------------------------------------------------------------
# surfaces

BG_APP = "#08070e"        # the window
BG_RAIL = "#0a0912"       # left icon rail
BG_CARD = "#141222"       # every card
BG_CARD_HI = "#191630"    # top of a card's subtle gradient
BG_INPUT = "#1b1730"      # dropdowns, secondary buttons, entry fields
BG_INPUT_HI = "#241f3f"   # hover / pressed
BORDER = "#241f3a"        # 1px card edge
BORDER_HI = "#3a3160"     # focused / active edge

# ---------------------------------------------------------------------------
# ink

FG = "#eef1f6"
FG_2 = "#9aa4b8"
FG_MUTED = "#6b7280"
FG_ON_ACCENT = "#ffffff"

# ---------------------------------------------------------------------------
# brand gradient — chrome only, never data

ACC_A = "#e0459a"         # magenta end
ACC_B = "#8b5cf6"         # violet end
ACC_C = "#3987e5"         # blue end, used for the third stop on the ring

# ---------------------------------------------------------------------------
# meaning
#
# Direction is green/red EVERYWHERE — the trend card, the vote list, the chart.
# The mockup showed "BULLISH" in magenta on the trend card and "Bullish" in
# green two inches away in the vote list; same fact, two colours, which teaches
# the eye nothing. One colour per meaning.
#
#   node validate_palette.js "#199e70,#ef4444,#9aa4b8" --mode dark
#        --surface "#141222" --pairs all
#   -> CVD separation  green<->red  dE 8.3 deutan / 12.5 tritan   PASS (>=8)
#   -> contrast        all three >= 3:1 on the card                PASS
#   The gray is flagged for "no chroma", which is the point of it: gray is the
#   absence of a reading, not a category.

UP = "#199e70"            # bullish, pass, above, yes
DOWN = "#ef4444"          # bearish, fail, below, no, and the stop level
NEUTRAL = "#9aa4b8"       # undecided — deliberately colourless
WARN = "#c98500"

# ---------------------------------------------------------------------------
# the three targets
#
# The mockup gave T1/T2/T3/STOP four unrelated hues. They are not four
# categories — they are one thing at three increasing distances, plus a stop.
# So the targets are an ORDINAL ramp of a single hue, and the validator agrees:
#
#   node validate_palette.js "#a79ef0,#8a7ce4,#6d5fd0" --mode dark
#        --surface "#141222" --ordinal
#   -> Lightness monotone PASS · Adjacent dL PASS · Light-end contrast 3.68:1
#      PASS · Single hue (spread 3 deg) PASS
#
# Brightest is T1 because T1 is the one that actually gets hit; the fade to T3
# is the fading likelihood, which is a true thing to encode.
#
# The four-hue version was tried first and failed outright:
#   "#9085e9,#3987e5,#d55181,#c98500" --pairs all
#   -> blue<->violet dE 1.9 protan, and 9.8 even with normal colour vision.

T1_COL = "#a79ef0"
T2_COL = "#8a7ce4"
T3_COL = "#6d5fd0"
STOP_COL = DOWN

# ---------------------------------------------------------------------------
# LIGHT MODE
#
# Same structure, same meanings, different surfaces. The colours were put
# through the same validator, and it is included in the folder now
# (validate_palette.py) so these claims can be re-run rather than believed:
#
#   python3 validate_palette.py --surface "#ffffff" \
#           --pairs "#16a34a,#dc2626,#7d8494"
#   -> contrast    3.30 / 4.83 / 3.75 on white     PASS (floor 3:1)
#   -> CVD  green<->red  dE 32.7 worst (tritan)    PASS (floor 8)
#
#   python3 validate_palette.py --surface "#ffffff" \
#           --ordinal "#173f9e,#2f62c6,#4a80da"
#   -> lightness monotone PASS · adjacent dL 0.114 / 0.088 PASS
#   -> single hue (spread 3 deg) PASS · worst contrast 3.90:1 PASS
#
# The targets are a BLUE ramp here, not the green the reference design used:
# colouring T1, T2 and T3 the same green throws away the ordinal encoding —
# they stop being one thing at three distances and become three of a kind.
#
# The direction of the ramp flips with the mode, and for the same reason. On
# black, prominence is brightness, so T1 is the lightest. On white, prominence
# is darkness, so T1 is the darkest. T1 is the target that actually gets hit;
# it stays the loudest one either way.
#
# Known and accepted, exactly as in dark mode: the gray NEUTRAL sits close to
# the target ramp for a deuteranope. Gray is the ABSENCE of a reading, every
# level is labelled in words beside its colour, and no meaning anywhere is
# carried by hue alone.

PALETTES = {
    "dark": {
        "BG_APP": "#08070e", "BG_RAIL": "#0a0912", "BG_CARD": "#141222",
        "BG_CARD_HI": "#191630", "BG_INPUT": "#1b1730", "BG_INPUT_HI": "#241f3f",
        "BORDER": "#241f3a", "BORDER_HI": "#3a3160",
        "FG": "#eef1f6", "FG_2": "#9aa4b8", "FG_MUTED": "#6b7280",
        "FG_ON_ACCENT": "#ffffff",
        "ACC_A": "#e0459a", "ACC_B": "#8b5cf6", "ACC_C": "#3987e5",
        "UP": "#199e70", "DOWN": "#ef4444", "NEUTRAL": "#9aa4b8",
        "WARN": "#c98500",
        "T1_COL": "#a79ef0", "T2_COL": "#8a7ce4", "T3_COL": "#6d5fd0",
    },
    "light": {
        "BG_APP": "#e7e9ef", "BG_RAIL": "#eceef3", "BG_CARD": "#ffffff",
        "BG_CARD_HI": "#ffffff", "BG_INPUT": "#ffffff", "BG_INPUT_HI": "#f0f2f6",
        "BORDER": "#dde0e8", "BORDER_HI": "#bcc3d2",
        "FG": "#1e2430", "FG_2": "#7d8494", "FG_MUTED": "#9aa0ad",
        "FG_ON_ACCENT": "#ffffff",
        "ACC_A": "#3b7cf6", "ACC_B": "#0ea5a4", "ACC_C": "#5a95ff",
        "UP": "#16a34a", "DOWN": "#dc2626", "NEUTRAL": "#7d8494",
        "WARN": "#b8860b",
        "T1_COL": "#173f9e", "T2_COL": "#2f62c6", "T3_COL": "#4a80da",
    },
}

MODE = "dark"


def use(name):
    """Switch palette. Every drawing call reads these names at paint time, so
    rebinding them here and repainting is the whole of it."""
    global MODE, STOP_COL
    pal = PALETTES.get(name)
    if pal is None:
        return MODE
    g = globals()
    g.update(pal)
    STOP_COL = pal["DOWN"]
    MODE = name
    return MODE


def other():
    return "light" if MODE == "dark" else "dark"


def emphasis(colour, amount=0.5):
    """A louder version of `colour` against the current surface.

    On black that means mixing toward white; on white it means mixing toward
    black. Hardcoding "toward white" is right in exactly one of the two modes,
    and in the other it prints pale green text on a pale green card."""
    import ui_kit
    return ui_kit.mix(colour, "#ffffff" if MODE == "dark" else "#0b1020", amount)


# ---------------------------------------------------------------------------
# fonts

_STACKS = {
    "Darwin": ["SF Pro Display", "SF Pro Text", "Helvetica Neue", "Helvetica"],
    "Windows": ["Segoe UI Variable Display", "Segoe UI", "Tahoma"],
}
_FALLBACK = ["DejaVu Sans", "Liberation Sans", "TkDefaultFont"]

_family = None


def family(root=None):
    """Pick the nicest grotesque this machine actually has, once."""
    global _family
    if _family:
        return _family
    import platform
    names = _STACKS.get(platform.system(), []) + _FALLBACK
    have = set()
    try:
        from tkinter import font as tkfont
        have = set(tkfont.families(root))
    except Exception:
        pass
    for n in names:
        if n in have:
            _family = n
            return n
    _family = "TkDefaultFont"
    return _family


def font(size, weight="normal", root=None):
    return (family(root), size, weight) if weight != "normal" else (family(root), size)
