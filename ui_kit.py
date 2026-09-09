#!/usr/bin/env python3
"""
ui_kit.py — the widgets Tk doesn't have
================================================================================
Gradients, rounded cards, pill tabs, a ring gauge, a sparkline, and the little
line icons for the side rail. All of it is drawn with create_rectangle,
create_polygon, create_line, create_arc and create_text — nothing else — so:

  * it looks the same on macOS, Windows and Linux, instead of inheriting three
    different native button styles;
  * the same code can be pointed at a recording canvas and rasterised to a PNG,
    which is how the layout gets checked without a screen.

Tk 8.6 has no antialiasing and no alpha channel. Two consequences drive the
design here: every "transparent" colour is pre-blended against its background by
`mix()` rather than composited, and corner radii are kept modest because a big
radius on a hard-edged renderer reads as a staircase.
"""

import math

# ---------------------------------------------------------------------------
# colour


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(t):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in t)


def mix(a, b, t):
    """Blend two hex colours. Stands in for opacity, which Tk canvas lacks."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def ramp(stops, t):
    """Sample a multi-stop gradient. `stops` is a list of hex colours."""
    if t <= 0:
        return stops[0]
    if t >= 1:
        return stops[-1]
    seg = 1.0 / (len(stops) - 1)
    i = min(int(t / seg), len(stops) - 2)
    return mix(stops[i], stops[i + 1], (t - i * seg) / seg)


# ---------------------------------------------------------------------------
# geometry


def _inset(y, y0, y1, r):
    """How far in the rounded edge is at this scanline. Lets a gradient be
    clipped to a rounded rectangle without any clipping support."""
    if r <= 0:
        return 0.0
    if y < y0 + r:
        d = (y0 + r) - y
    elif y > y1 - r:
        d = y - (y1 - r)
    else:
        return 0.0
    d = min(d, r)
    return r - math.sqrt(max(0.0, r * r - d * d))


def round_pts(x0, y0, x1, y1, r):
    """Corner points for a rounded rectangle, dense enough that Tk's polygon
    looks curved rather than chamfered."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    steps = max(3, int(r / 1.6))
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                       (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


# ---------------------------------------------------------------------------
# fills


def gradient(c, x0, y0, x1, y1, top, bottom, radius=0, tags=(), horizontal=False):
    """A linear gradient as a stack of 1px lines, clipped to a rounded rect.

    One line per pixel is not as wasteful as it sounds — a card is 90px tall, so
    this is 90 items, and it only redraws on resize."""
    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    if horizontal:
        n = max(1, int(x1 - x0))
        for i in range(n):
            x = x0 + i
            c.create_line(x, y0, x, y1, fill=mix(top, bottom, i / max(1, n - 1)),
                          width=1, tags=tags)
        return
    n = max(1, int(y1 - y0))
    for i in range(n):
        y = y0 + i
        d = _inset(y + 0.5, y0, y1, radius)
        if x1 - d <= x0 + d:
            continue
        c.create_line(x0 + d, y, x1 - d, y, fill=mix(top, bottom, i / max(1, n - 1)),
                      width=1, tags=tags)


def card(c, x0, y0, x1, y1, fill, border=None, radius=14, tags=(), fill2=None):
    """A panel. `fill2` makes it a vertical gradient instead of flat."""
    if fill2:
        gradient(c, x0, y0, x1, y1, fill, fill2, radius=radius, tags=tags)
        if border:
            c.create_polygon(*round_pts(x0, y0, x1, y1, radius), fill="",
                             outline=border, width=1, tags=tags)
    else:
        c.create_polygon(*round_pts(x0, y0, x1, y1, radius), fill=fill,
                         outline=border or fill, width=1, tags=tags)


def pill(c, x0, y0, x1, y1, fill, border=None, tags=(), grad=None):
    """A fully-rounded capsule. `grad` is (left_colour, right_colour)."""
    r = (y1 - y0) / 2.0
    if grad:
        # Horizontal gradient inside a capsule: walk columns and shorten each
        # to the capsule's vertical extent at that x.
        n = max(1, int(x1 - x0))
        for i in range(n):
            x = x0 + i + 0.5
            if x < x0 + r:
                d = (x0 + r) - x
            elif x > x1 - r:
                d = x - (x1 - r)
            else:
                d = 0.0
            d = min(d, r)
            dy = r - math.sqrt(max(0.0, r * r - d * d))
            c.create_line(x, y0 + dy, x, y1 - dy,
                          fill=mix(grad[0], grad[1], i / max(1, n - 1)),
                          width=1, tags=tags)
        return
    c.create_polygon(*round_pts(x0, y0, x1, y1, r), fill=fill,
                     outline=border or fill, width=1, tags=tags)


# ---------------------------------------------------------------------------
# marks


def ring(c, cx, cy, radius, thickness, frac, stops, track, tags=()):
    """The confidence meter.

    The VALUE is the arc length and the numeral in the middle — the gradient is
    chrome, exactly like the buttons. That matters: a colour-coded gauge would
    be encoding one number twice, once in a way a colourblind reader can't
    read."""
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    c.create_arc(*box, start=0, extent=359.9, style="arc", outline=track,
                 width=thickness, tags=tags)
    frac = max(0.0, min(1.0, frac))
    if frac <= 0:
        return
    # Tk gives one colour per arc, so the gradient is many short arcs. 2-degree
    # segments are below the eye's ability to see the seam at this radius.
    total = 360.0 * frac
    seg = 2.0
    n = max(1, int(math.ceil(total / seg)))
    for i in range(n):
        a0 = 90 - (i * total / n)
        ext = -(total / n) - 0.35        # slight overlap hides the seams
        c.create_arc(*box, start=a0, extent=ext, style="arc",
                     outline=ramp(stops, i / max(1, n - 1)),
                     width=thickness, tags=tags)


def sparkline(c, x0, y0, x1, y1, values, colour, fill_to=None, tags=(), width=2):
    """One series, so no legend — the card title names it."""
    vals = [v for v in values if v == v]
    if len(vals) < 2:
        return
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        pts.append(x0 + (x1 - x0) * i / (n - 1))
        pts.append(y1 - (y1 - y0) * (v - lo) / (hi - lo))

    if fill_to:
        # Fill under the curve. Tk gives one colour per item, so a vertical fade
        # has to be horizontal scanlines — and the scanline has to be clipped to
        # where the curve actually is, or the "fill" becomes a stack of stripes
        # floating in mid-air. So: sample the curve per column, then per row emit
        # the runs of columns that sit below it.
        cols = int(max(2, x1 - x0))
        cy = []
        for i in range(cols):
            t = i / (cols - 1)
            fpos = t * (n - 1)
            a = min(int(fpos), n - 2)
            f = fpos - a
            va = vals[a] + (vals[a + 1] - vals[a]) * f
            cy.append(y1 - (y1 - y0) * (va - lo) / (hi - lo))
        rows = int(max(2, y1 - y0))
        for r in range(rows):
            yy = y0 + r + 0.5
            col = mix(fill_to[0], fill_to[1], r / (rows - 1))
            run = None
            for i in range(cols):
                inside = cy[i] <= yy
                if inside and run is None:
                    run = i
                elif not inside and run is not None:
                    c.create_line(x0 + run, yy, x0 + i, yy, fill=col, width=1,
                                  tags=tags)
                    run = None
            if run is not None:
                c.create_line(x0 + run, yy, x0 + cols, yy, fill=col, width=1,
                              tags=tags)
    c.create_line(*pts, fill=colour, width=width, smooth=1, splinesteps=8,
                  capstyle="round", joinstyle="round", tags=tags)


# ---------------------------------------------------------------------------
# icons — simple line glyphs, drawn in a 24x24 box then scaled


def icon(c, name, cx, cy, size, colour, tags=(), width=2):
    """Everything is expressed in a -1..1 box so one number scales the glyph."""
    s = size / 2.0

    def P(*pairs):
        out = []
        for i in range(0, len(pairs), 2):
            out += [cx + pairs[i] * s, cy + pairs[i + 1] * s]
        return out

    def line(*pairs, **kw):
        c.create_line(*P(*pairs), fill=kw.get("fill", colour),
                      width=kw.get("w", width), capstyle="round",
                      joinstyle="round", smooth=kw.get("smooth", 0), tags=tags)

    if name == "pulse":
        line(-0.9, 0, -0.45, 0, -0.2, -0.7, 0.15, 0.7, 0.42, 0, 0.9, 0)
    elif name == "dashboard":
        for x0, y0, x1, y1 in ((-0.75, -0.75, -0.1, -0.1), (0.1, -0.75, 0.75, -0.3),
                               (-0.75, 0.1, -0.1, 0.75), (0.1, -0.1, 0.75, 0.75)):
            c.create_polygon(*round_pts(cx + x0 * s, cy + y0 * s, cx + x1 * s,
                                        cy + y1 * s, 0.14 * s),
                             fill="", outline=colour, width=width, tags=tags)
    elif name == "bars":
        for x, h in ((-0.55, 0.35), (0.0, 0.8), (0.55, 0.55)):
            c.create_line(cx + x * s, cy + 0.75 * s, cx + x * s, cy + (0.75 - 2 * h) * s,
                          fill=colour, width=width + 1, capstyle="round", tags=tags)
    elif name == "bell":
        # dome + flared skirt + clapper, drawn as one open path
        line(-0.72, 0.42, -0.52, 0.18, -0.52, -0.12,
             -0.36, -0.62, 0.36, -0.62, 0.52, -0.12, 0.52, 0.18, 0.72, 0.42,
             smooth=1)
        line(-0.72, 0.42, 0.72, 0.42)
        line(-0.15, 0.62, 0.15, 0.62)
    elif name == "gear":
        c.create_oval(cx - 0.3 * s, cy - 0.3 * s, cx + 0.3 * s, cy + 0.3 * s,
                      outline=colour, width=width, tags=tags)
        for k in range(8):
            a = math.radians(k * 45)
            c.create_line(cx + 0.46 * s * math.cos(a), cy + 0.46 * s * math.sin(a),
                          cx + 0.86 * s * math.cos(a), cy + 0.86 * s * math.sin(a),
                          fill=colour, width=width + 1, capstyle="butt", tags=tags)
    elif name == "logout":
        line(-0.2, -0.8, -0.8, -0.8, -0.8, 0.8, -0.2, 0.8)
        line(0.15, -0.45, 0.75, 0, 0.15, 0.45)
        line(-0.35, 0, 0.7, 0)
    elif name == "calendar":
        c.create_polygon(*round_pts(cx - 0.75 * s, cy - 0.6 * s, cx + 0.75 * s,
                                    cy + 0.75 * s, 0.16 * s),
                         fill="", outline=colour, width=width, tags=tags)
        line(-0.75, -0.2, 0.75, -0.2)
        line(-0.38, -0.85, -0.38, -0.42)
        line(0.38, -0.85, 0.38, -0.42)
    elif name == "list":
        for y in (-0.45, 0.0, 0.45):
            line(-0.7, y, 0.7, y)
    elif name == "arrow_up":
        line(-0.55, 0.55, 0.5, -0.5)
        line(0.0, -0.5, 0.5, -0.5)
        line(0.5, 0.0, 0.5, -0.5)
    elif name == "arrow_down":
        line(-0.55, -0.55, 0.5, 0.5)
        line(0.0, 0.5, 0.5, 0.5)
        line(0.5, 0.0, 0.5, 0.5)
    elif name == "arrow_flat":
        line(-0.6, 0, 0.5, 0)
        line(0.15, -0.35, 0.5, 0, 0.15, 0.35)
    elif name == "check":
        line(-0.55, 0.05, -0.15, 0.45, 0.6, -0.45, w=width + 1)
    elif name == "theme":
        # A disc with half of it filled: the same shape reads as "switch the
        # light" whichever mode you are already in.
        r = size * 0.42
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="", outline=colour,
                      width=width, tags=tags)
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=-180,
                     fill=colour, outline=colour, tags=tags)
        return
    elif name == "chevron":
        line(-0.4, -0.2, 0, 0.25, 0.4, -0.2)


def dot(c, cx, cy, r, colour, tags=()):
    c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline=colour,
                  tags=tags)
