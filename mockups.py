#!/usr/bin/env python3
"""
mockups.py — alternative layouts, drawn in the real theme
================================================================================
These are PROPOSALS, not the running app. They use the same theme.py colours,
the same ui_kit primitives and the same rasteriser as the screenshots of the
live tool, so what you see is what it would actually look like — no mockup
flattery, no fonts or spacing the real window could not produce.

Run:  python3 mockups.py        -> writes mockup_*.png

Nothing here is imported by gui.py. Deleting this file changes nothing.
"""
import sys

import theme as T
import ui_kit as K
import tkraster

W, H = 1570, 1002
RAIL = 98
PAD = 30
L = RAIL + PAD
R = W - 20

try:
    from PIL import ImageFont
    _fc = {}

    def _tw(t, px, bold=False):
        f = _fc.get((px, bool(bold)))
        if f is None:
            f = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
                % ("-Bold" if bold else ""), int(px))
            _fc[(px, bool(bold))] = f
        return f.getbbox(t)[2]
except Exception:                                    # pragma: no cover
    def _tw(t, px, bold=False):
        return int(len(t) * px * 0.58)


def txt(c, x, y, s, px=13, col=None, bold=False, anchor="w"):
    c.create_text(x, y, text=s, anchor=anchor, fill=col or T.FG,
                  font=(T.family(), -px, "bold" if bold else "normal"))


# ---------------------------------------------------------------------------
# shared chrome, so every proposal is judged on its LAYOUT and not on whether
# it happens to have a nicer header


def rail(c, active=0):
    c.create_rectangle(0, 0, RAIL, H, fill=T.BG_RAIL, outline=T.BG_RAIL)
    c.create_line(RAIL, 0, RAIL, H, fill=T.BORDER)
    cx, cy, r = 49, 51, 24
    for i in range(26):
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=i * 360 / 26,
                     extent=-14.5, style="arc", width=3,
                     outline=K.ramp([T.ACC_A, T.ACC_B, T.ACC_C, T.ACC_A], i / 25))
    txt(c, cx, cy + 1, "R", 20, T.FG, True, "center")
    y = 168
    for n, name in enumerate(("pulse", "dashboard", "bars", "bell", "gear")):
        if n == active:
            K.card(c, 25, y - 26, 73, y + 26, K.mix(T.BG_RAIL, T.ACC_B, 0.22),
                   K.mix(T.BG_RAIL, T.ACC_B, 0.45), 14)
        K.icon(c, name, 49, y, 23, T.FG if n == active else T.FG_MUTED)
        y += 88
    K.icon(c, "logout", 49, H - 78, 23, T.FG_MUTED)


def topbar(c, note="MARKET OPEN · 11:42:05 IST"):
    def sel(x, y, w, val):
        K.card(c, x, y, x + w, y + 34, T.BG_INPUT, T.BORDER, 10)
        txt(c, x + 14, y + 18, val, 13, T.FG)
        K.icon(c, "chevron", x + w - 18, y + 17, 14, T.FG_2)
        return x + w
    txt(c, L + 5, 45, "Mode:", 13, T.FG_2)
    x = sel(L + 60, 28, 118, "kite")
    K.pill(c, x + 18, 27, x + 126, 63, None, grad=(T.ACC_A, T.ACC_B))
    txt(c, x + 72, 46, "Start", 13, T.FG_ON_ACCENT, True, "center")
    K.card(c, x + 136, 27, x + 216, 63, T.BG_INPUT, T.BORDER, 10)
    txt(c, x + 176, 46, "Stop", 13, T.FG, False, "center")
    txt(c, x + 236, 45, "Lots:", 13, T.FG_2)
    sel(x + 276, 28, 62, "2")
    K.dot(c, R - _tw(note, 13) - 14, 45, 4, T.UP)
    txt(c, R, 46, note, 13, T.FG_2, False, "e")


def portfolio(c, y0, rows, booked, open_, chips=True):
    y1 = y0 + 108
    K.card(c, L, y0, R, y1, T.BG_CARD_HI, T.BORDER, 16, fill2=T.BG_CARD)
    K.card(c, L + 20, y0 + 18, L + 56, y0 + 54, T.BG_INPUT, T.BORDER, 10)
    K.icon(c, "calendar", L + 38, y0 + 36, 17, T.ACC_A)
    txt(c, L + 72, y0 + 36, "SESSION", 12, T.FG_2, True)
    net = booked + open_
    money = f"{'+' if net >= 0 else '-'}Rs.{abs(net):,.0f}"
    col = T.UP if net > 0 else (T.DOWN if net < 0 else T.FG_2)
    mid = (y0 + y1) / 2
    txt(c, R - 76, mid - 16, "TODAY", 11, T.FG_2, True, "e")
    txt(c, R - 76, mid + 8, money, 26, col, True, "e")
    txt(c, R - 76, mid + 30,
        f"booked {booked:+,.0f}  ·  open {open_:+,.0f}", 11, T.FG_MUTED, False, "e")
    K.card(c, R - 62, y0 + 18, R - 20, y0 + 60, T.BG_INPUT, T.BORDER, 10)
    K.icon(c, "list", R - 41, y0 + 39, 17, T.FG_2)
    if not chips:
        return
    cx = R - 76 - 300
    for name, v, live in reversed(rows):
        t = f"{name} {v:+,.0f}"
        cw = _tw(t, 12, True) + 26
        cx -= cw + 8
        K.pill(c, cx, mid - 15, cx + cw, mid + 15, T.BG_INPUT, T.BORDER)
        txt(c, cx + cw / 2, mid + 1, t,
            12, T.UP if v > 0 else T.DOWN if v < 0 else T.FG_2, True, "center")
        if live:
            K.dot(c, cx + 7, mid - 9, 2.5, T.ACC_A)


# ===========================================================================
# 1. DESK — all three indices at once, no tab switching
# ===========================================================================

def desk(c):
    """Today you can only see ONE index, though all three are analysed every
    cycle. This shows all three side by side: the tab bar disappears and with
    it the question "what is BANKNIFTY doing while I watch NIFTY?"."""
    rail(c, 0)
    topbar(c)

    cols = [
        {"name": "NIFTY", "spot": "24,483.65", "day": "+41.00", "dp": "+0.17%",
         "trend": "MODERATE UPTREND", "dir": "up", "adx": "ADX 24.1",
         "state": "OPEN", "act": "BUY CE", "strike": "24500 CE",
         "entry": "168.20", "now": "196.40", "pnl": "+2,820",
         "levels": [("T1", "214.0", 1.0, "10:41:02"), ("T2", "246.0", 0.62, None),
                    ("T3", "285.0", 0.34, None), ("STOP", "152.0", 0.0, None)]},
        {"name": "BANKNIFTY", "spot": "51,980.10", "day": "-118.40", "dp": "-0.23%",
         "trend": "RANGE-BOUND", "dir": "flat", "adx": "ADX 12.6",
         "state": "WAITING", "act": "NO SIGNAL", "strike": "—",
         "entry": "—", "now": "—", "pnl": "—",
         "levels": [("T1", "—", 0, None), ("T2", "—", 0, None),
                    ("T3", "—", 0, None), ("STOP", "—", 0, None)]},
        {"name": "SENSEX", "spot": "77,264.51", "day": "-340.46", "dp": "-0.44%",
         "trend": "STRONG DOWNTREND", "dir": "down", "adx": "ADX 47.8",
         "state": "NO ROOM", "act": "NOT WORTH IT", "strike": "77100 PE",
         "entry": "—", "now": "—", "pnl": "—",
         "levels": [("T1", "—", 0, None), ("T2", "—", 0, None),
                    ("T3", "—", 0, None), ("STOP", "—", 0, None)]},
    ]

    gap = 18
    cw = (R - L - gap * 2) / 3
    y0, y1 = 96, 852
    for i, d in enumerate(cols):
        x0 = L + i * (cw + gap)
        x1 = x0 + cw
        tint = {"up": T.UP, "down": T.DOWN}.get(d["dir"], T.ACC_B)
        K.card(c, x0, y0, x1, y1, T.BG_CARD_HI, T.BORDER, 16, fill2=T.BG_CARD)

        # header: name, spot, day move — the three things you scan for
        K.card(c, x0, y0, x1, y0 + 96, K.mix(T.BG_CARD, tint, 0.16),
               T.BORDER, 16)
        txt(c, x0 + 22, y0 + 30, d["name"], 15, T.FG, True)
        col = T.UP if d["day"].startswith("+") else T.DOWN
        txt(c, x1 - 22, y0 + 30, f"{d['day']}  {d['dp']}", 13, col, True, "e")
        txt(c, x0 + 22, y0 + 62, d["spot"], 24, T.FG, True)
        K.icon(c, {"up": "arrow_up", "down": "arrow_down"}.get(d["dir"], "arrow_flat"),
               x1 - 34, y0 + 62, 15, col, width=3)

        # trend line
        txt(c, x0 + 22, y0 + 126, d["trend"], 14, col if d["dir"] != "flat" else T.FG_2, True)
        txt(c, x0 + 22, y0 + 148, d["adx"] + " · momentum building", 11, T.FG_MUTED)

        # state badge
        bcol = {"OPEN": T.UP, "NO ROOM": T.DOWN}.get(d["state"], T.FG_MUTED)
        bw = _tw(d["state"], 11, True) + 26
        K.card(c, x1 - 22 - bw, y0 + 114, x1 - 22, y0 + 140,
               K.mix(T.BG_CARD, bcol, 0.22), K.mix(T.BG_CARD, bcol, 0.5), 8)
        txt(c, x1 - 22 - bw / 2, y0 + 127, d["state"], 11,
            K.mix(bcol, "#ffffff", 0.35), True, "center")

        c.create_line(x0 + 22, y0 + 172, x1 - 22, y0 + 172, fill=T.BORDER)

        # the ticket
        acol = T.UP if "CE" in d["act"] else T.DOWN if "PE" in d["act"] else T.FG_2
        txt(c, x0 + 22, y0 + 208, d["act"], 26, acol, True)
        txt(c, x0 + 22, y0 + 234, d["strike"], 12, T.FG_MUTED)

        sy = y0 + 274
        for n, (lab, val) in enumerate((("ENTRY", d["entry"]), ("NOW", d["now"]),
                                        ("2 LOTS", d["pnl"]))):
            sx = x0 + 22 + n * ((cw - 44) / 3)
            txt(c, sx, sy, lab, 10, T.FG_2, True)
            vc = T.UP if val.startswith("+") else T.FG if val != "—" else T.FG_MUTED
            txt(c, sx, sy + 24, val, 16, vc, True)

        # levels as rows, not cards — three columns cannot afford four boxes
        ly = y0 + 336
        for lab, val, prog, when in d["levels"]:
            lc = {"T1": T.T1_COL, "T2": T.T2_COL, "T3": T.T3_COL}.get(lab, T.STOP_COL)
            done = prog >= 1.0
            if done:
                lc = T.UP if lab != "STOP" else T.DOWN
            K.card(c, x0 + 22, ly, x1 - 22, ly + 58,
                   K.mix(T.BG_CARD, lc, 0.22 if done else 0.10),
                   lc if done else T.BORDER, 10)
            c.create_line(x0 + 22, ly + 1, x0 + 40, ly + 1, fill=lc, width=3)
            txt(c, x0 + 36, ly + 20, lab + ("  ✓" if done else ""), 13,
                lc if done else T.FG, True)
            txt(c, x1 - 36, ly + 20, val, 13, T.FG_2, False, "e")
            if when:
                txt(c, x1 - 36, ly + 40, when, 10, lc, False, "e")
            elif prog > 0:
                bx0, bx1 = x0 + 36, x1 - 36
                K.pill(c, bx0, ly + 38, bx1, ly + 44, T.BG_INPUT)
                K.pill(c, bx0, ly + 38, bx0 + (bx1 - bx0) * prog, ly + 44, lc)
                txt(c, bx0, ly + 40, "", 10, T.FG_MUTED)
            ly += 66

    portfolio(c, 870, [("NIFTY", 2820, True), ("BANKNIFTY", 0, False),
                       ("SENSEX", 3170, False)], 3170, 2820)


# ===========================================================================
# 2. LADDER — the levels drawn at their TRUE distance
# ===========================================================================

def ladder(c):
    """Four equal-width cards tell you T1, T2, T3 and the stop exist. They do
    not tell you T1 is a third of the way to T3, or that the stop is nearer
    than any of them. Here the levels are plotted on a real price axis, so the
    shape of the trade is visible before a single number is read."""
    rail(c, 0)
    topbar(c)

    # tabs
    K.pill(c, L, 96, L + 420, 140, T.BG_CARD, T.BORDER)
    for i, n in enumerate(("NIFTY", "BANKNIFTY", "SENSEX")):
        w = 136 if i else 132
        x = L + 4 + i * 138
        if i == 0:
            K.pill(c, x, 100, x + w, 136, None, grad=(T.ACC_A, T.ACC_B))
        txt(c, x + w / 2, 119, n, 13, T.FG_ON_ACCENT if i == 0 else T.FG_2, True, "center")

    y0, y1 = 158, 852
    K.card(c, L, y0, R, y1, T.BG_CARD_HI, T.BORDER, 16, fill2=T.BG_CARD)

    # ---- left: the headline and the money ----------------------------
    txt(c, L + 30, y0 + 34, "SIGNAL TICKET  ·  #0007", 12, T.FG_2, True)
    txt(c, L + 30, y0 + 84, "BUY CE", 34, T.UP, True)
    txt(c, L + 30, y0 + 116, "NIFTY 24500 CE  ·  live premium  ·  2 lots",
        13, T.FG_2)
    txt(c, L + 30, y0 + 138, "Issued 10:12:55 IST · levels frozen at entry",
        12, T.FG_MUTED)
    bw = _tw("OPEN", 12, True) + 34
    K.card(c, L + 30, y0 + 168, L + 30 + bw, y0 + 202,
           K.mix(T.BG_CARD, T.UP, 0.22), K.mix(T.BG_CARD, T.UP, 0.5), 10)
    txt(c, L + 30 + bw / 2, y0 + 185, "OPEN", 12,
        K.mix(T.UP, "#ffffff", 0.35), True, "center")

    sy = y0 + 246
    for lab, val, col in (("ENTRY", "168.20", T.FG), ("NOW", "196.40", T.FG),
                          ("SPOT", "24,483.65", T.FG), ("2 LOTS", "+2,820", T.UP)):
        txt(c, L + 30, sy, lab, 11, T.FG_2, True)
        txt(c, L + 30, sy + 28, val, 20, col, True)
        sy += 74

    # risk read-out — the number that decides whether a trade is worth it
    K.card(c, L + 30, y1 - 148, L + 330, y1 - 34, T.BG_APP, T.BORDER, 12)
    txt(c, L + 50, y1 - 120, "REWARD : RISK", 11, T.FG_2, True)
    txt(c, L + 50, y1 - 86, "2.4 : 1", 26, T.UP, True)
    txt(c, L + 50, y1 - 58, "116 pts to T3  ·  48 pts to stop", 11, T.FG_MUTED)

    # ---- centre: the price ladder -------------------------------------
    lx = L + 400
    lw = 470
    top, bot = y0 + 56, y1 - 56
    levels = [
        ("T3", 285.0, T.T3_COL, None),
        ("T2", 246.0, T.T2_COL, None),
        ("T1", 214.0, T.T1_COL, "10:41:02"),
        ("ENTRY", 168.20, T.FG_2, None),
        ("STOP", 152.0, T.STOP_COL, None),
    ]
    price_now = 196.40
    hi, lo = 285.0, 152.0
    span = hi - lo

    def ypx(p):
        return bot - (p - lo) / span * (bot - top)

    # the axis
    c.create_line(lx, top - 14, lx, bot + 14, fill=T.BORDER)
    # the travelled band, entry -> now
    ye, yn = ypx(168.20), ypx(price_now)
    K.gradient(c, lx + 1, min(ye, yn), lx + lw, max(ye, yn),
               K.mix(T.BG_CARD, T.UP, 0.30), K.mix(T.BG_CARD, T.UP, 0.06))

    for name, price, col, when in levels:
        y = ypx(price)
        dash = (2, 4) if name == "ENTRY" else None
        if dash:
            for xx in range(int(lx), int(lx + lw), 6):
                c.create_line(xx, y, xx + 3, y, fill=col)
        else:
            c.create_line(lx, y, lx + lw, y, fill=col, width=2)
        hit = when is not None
        tag = f"{name}  {price:,.2f}"
        tw = _tw(tag, 13, True) + 28
        K.card(c, lx + lw - tw, y - 15, lx + lw, y + 15,
               K.mix(T.BG_CARD, col, 0.30 if hit else 0.18),
               col, 8)
        txt(c, lx + lw - tw + 14, y + 1, tag, 13,
            K.mix(col, "#ffffff", 0.5) if hit else T.FG, True)
        if hit:
            txt(c, lx + lw + 12, y + 1, "✓ " + when, 11, T.UP, True)
        # distance from the current price, in points — the thing you actually
        # want to know and currently have to work out in your head
        d = price - price_now
        if abs(d) > 0.01:
            txt(c, lx + 14, y - 12, f"{d:+.2f}", 11, T.FG_MUTED)

    # the live price marker
    K.pill(c, lx - 96, yn - 15, lx - 8, yn + 15, None, grad=(T.ACC_A, T.ACC_B))
    txt(c, lx - 52, yn + 1, f"{price_now:,.2f}", 13, T.FG_ON_ACCENT, True, "center")
    c.create_polygon(lx - 8, yn - 8, lx, yn, lx - 8, yn + 8,
                     fill=T.ACC_B, outline=T.ACC_B)

    # ---- right: confidence ---------------------------------------------
    rx = R - 250
    K.ring(c, rx + 100, y0 + 130, 62, 13, 0.75,
           [T.ACC_A, T.ACC_B, T.ACC_C], T.BG_INPUT)
    txt(c, rx + 100, y0 + 128, "75%", 30, T.FG, True, "center")
    txt(c, rx + 100, y0 + 218, "CONFIDENCE · Medium", 12, T.FG_2, False, "center")
    yy = y0 + 268
    for n, v, col in (("Trend", "Bullish", T.UP), ("MACD", "Bullish +30.1", T.UP),
                      ("RSI", "Bullish 60", T.UP), ("VWAP", "+0", T.FG_2),
                      ("PCR", "1.02", T.FG_2), ("ADX Gate", "27.1 PASS", T.UP),
                      ("Room", "↑380 ↓380", T.FG_2)):
        c.create_line(rx, yy - 20, R - 30, yy - 20, fill=T.BORDER)
        txt(c, rx, yy, n, 12, T.FG_2)
        txt(c, R - 30, yy, v, 12, col, True, "e")
        yy += 46

    portfolio(c, 870, [("NIFTY", 2820, True), ("SENSEX", 3170, False)], 3170, 2820)


# ===========================================================================
# 3. SPLIT — chart and ticket on screen together
# ===========================================================================

def split(c):
    """Signal and Chart are two tabs today, so you can read the numbers or see
    where they sit on the candles, never both. Here the chart carries the level
    lines and the ticket runs beneath it as a strip."""
    import math
    rail(c, 0)
    topbar(c)

    K.pill(c, L, 96, L + 420, 140, T.BG_CARD, T.BORDER)
    for i, n in enumerate(("NIFTY", "BANKNIFTY", "SENSEX")):
        w = 136 if i else 132
        x = L + 4 + i * 138
        if i == 0:
            K.pill(c, x, 100, x + w, 136, None, grad=(T.ACC_A, T.ACC_B))
        txt(c, x + w / 2, 119, n, 13, T.FG_ON_ACCENT if i == 0 else T.FG_2, True, "center")

    # ---- chart ---------------------------------------------------------
    cy0, cy1 = 158, 596
    K.card(c, L, cy0, R, cy1, T.BG_CARD_HI, T.BORDER, 16, fill2=T.BG_CARD)
    px0, px1 = L + 26, R - 150
    py0, py1 = cy0 + 26, cy1 - 44
    lo, hi = 150.0, 292.0

    def yp(v):
        return py1 - (v - lo) / (hi - lo) * (py1 - py0)

    # A series that actually behaves: drifts up from the entry to the current
    # price and stays inside the axis. The first attempt accumulated a linear
    # term, pinned to the top of the range and clamped — which drew forty
    # identical candles in a row and looked like a comb, not a chart.
    import random
    rnd = random.Random(7)
    n = 96
    start, end = 168.2, 196.4
    vals, v = [], start
    for i in range(n):
        pull = (start + (end - start) * (i / (n - 1)) - v) * 0.22
        v += pull + math.sin(i / 6.0) * 2.1 + rnd.uniform(-3.4, 3.4)
        vals.append(max(lo + 10, min(hi - 14, v)))
    vals[-1] = end
    bw = (px1 - px0) / len(vals)
    for i, v in enumerate(vals):
        o = vals[i - 1] if i else v
        up = v >= o
        col = T.UP if up else T.DOWN
        x = px0 + i * bw + bw / 2
        c.create_line(x, yp(max(o, v) + 3.4), x, yp(min(o, v) - 3.4), fill=col)
        c.create_rectangle(x - bw * 0.3, yp(max(o, v)), x + bw * 0.3, yp(min(o, v)),
                           fill=col if up else T.BG_CARD, outline=col)

    for name, price, col in (("T3", 285.0, T.T3_COL), ("T2", 246.0, T.T2_COL),
                             ("T1", 214.0, T.T1_COL), ("STOP", 152.0, T.STOP_COL)):
        y = yp(price)
        for xx in range(int(px0), int(px1), 9):
            c.create_line(xx, y, xx + 4, y, fill=K.mix(T.BG_CARD, col, 0.75))
        tag = f"{name} {price:,.0f}"
        tw = _tw(tag, 11, True) + 20
        K.card(c, px1 + 8, y - 13, px1 + 8 + tw, y + 13,
               K.mix(T.BG_CARD, col, 0.28), col, 7)
        txt(c, px1 + 18, y + 1, tag, 11, T.FG, True)
    ye = yp(168.20)
    c.create_line(px0, ye, px1, ye, fill=T.FG_2, width=1)
    txt(c, px0 + 6, ye - 12, "entry 168.20", 10, T.FG_MUTED)
    yn = yp(196.40)
    K.card(c, px1 + 8, yn - 14, px1 + 8 + 88, yn + 14, T.FG, T.FG, 7)
    txt(c, px1 + 20, yn + 1, "196.40", 12, T.BG_APP, True)

    # ---- the why, right on the chart -----------------------------------
    txt(c, px0, cy1 - 22, "50-EMA below 20-EMA · MACD +30.1 · RSI 60 · ADX 27.1 PASS",
        11, T.FG_MUTED)

    # ---- ticket strip ---------------------------------------------------
    ty0, ty1 = 614, 852
    K.card(c, L, ty0, R, ty1, T.BG_CARD_HI, T.BORDER, 16, fill2=T.BG_CARD)
    txt(c, L + 26, ty0 + 30, "SIGNAL TICKET  ·  #0007", 12, T.FG_2, True)
    txt(c, L + 26, ty0 + 74, "BUY CE", 30, T.UP, True)
    txt(c, L + 26 + _tw("BUY CE", 30, True) + 20, ty0 + 78,
        "NIFTY 24500 CE · 2 lots", 13, T.FG_2)
    bw2 = _tw("OPEN", 12, True) + 34
    K.card(c, L + 26, ty0 + 100, L + 26 + bw2, ty0 + 134,
           K.mix(T.BG_CARD, T.UP, 0.22), K.mix(T.BG_CARD, T.UP, 0.5), 10)
    txt(c, L + 26 + bw2 / 2, ty0 + 117, "OPEN", 12,
        K.mix(T.UP, "#ffffff", 0.35), True, "center")

    sx = L + 220
    for lab, val, col in (("ENTRY", "168.20", T.FG), ("NOW", "196.40", T.FG),
                          ("2 LOTS", "+2,820", T.UP), ("R:R", "2.4 : 1", T.UP)):
        txt(c, sx, ty0 + 34, lab, 11, T.FG_2, True)
        txt(c, sx, ty0 + 62, val, 19, col, True)
        sx += 150

    # levels as a single horizontal progress track — one glance, not four
    gx0, gx1 = L + 220, R - 30
    gy = ty0 + 128
    txt(c, gx0, gy - 14, "PROGRESS", 11, T.FG_2, True)
    K.pill(c, gx0, gy + 10, gx1, gy + 30, T.BG_INPUT)
    marks = [("STOP", 0.0, T.STOP_COL), ("ENTRY", 0.12, T.FG_2),
             ("T1", 0.46, T.T1_COL), ("T2", 0.70, T.T2_COL), ("T3", 1.0, T.T3_COL)]
    now_f = 0.335
    K.pill(c, gx0, gy + 10, gx0 + (gx1 - gx0) * now_f, gy + 30, None,
           grad=(T.ACC_A, T.ACC_B))
    for name, f, col in marks:
        x = gx0 + (gx1 - gx0) * f
        c.create_line(x, gy + 4, x, gy + 36, fill=col, width=2)
        hit = f <= now_f and name in ("T1", "T2", "T3")
        txt(c, x, gy + 54, name + (" ✓" if hit else ""), 11,
            T.UP if hit else col, True, "center")
    txt(c, gx0 + (gx1 - gx0) * now_f, gy - 14, "196.40", 11, T.FG, True, "center")

    portfolio(c, 870, [("NIFTY", 2820, True), ("SENSEX", 3170, False)], 3170, 2820)


# ===========================================================================

DESIGNS = {"desk": desk, "ladder": ladder, "split": split}


def main():
    want = sys.argv[1:] or list(DESIGNS)
    for name in want:
        fn = DESIGNS.get(name)
        if fn is None:
            print("no such design:", name)
            continue
        rec = tkraster.Recorder(W, H)
        fn(rec)
        out = f"mockup_{name}.png"
        tkraster.render(rec, path=out)
        print("wrote", out)


if __name__ == "__main__":
    main()
