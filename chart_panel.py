"""
chart_panel.py — a live candlestick chart, and the reasoning drawn beside it
================================================================================
Two things live here:

  ChartPanel  a candlestick chart on a plain Tk canvas — the same candles the
              indicators are computed from, with the EMAs, VWAP and the
              locked trade's entry / T1 / T2 / T3 / stop drawn on top, so
              every number on the ticket has a visible position on the chart.

  WhyPanel    the sentences from explain.py, laid out under the chart. When a
              ticket is open it shows the reasoning FROZEN at the moment the
              signal fired — which is the thing you actually want to check
              later — with a toggle to see the current live read instead.

No plotting library. matplotlib would pull in a large dependency that is
genuinely painful to build on Termux, and a candlestick chart is a few
hundred rectangles — Tk's own canvas draws it fine and starts instantly.
"""

import tkinter as tk
from tkinter import font as tkfont

import config
import indicators as ind
import explain

# Palette mirrored from gui.py — deliberately duplicated rather than imported,
# because gui.py imports this module and the cycle would break the import.
BG_APP = "#0b0d12"
BG_PANEL = "#141924"
BG_INPUT = "#1c2330"
BORDER = "#2a3141"
FG_PRIMARY = "#eef1f6"
FG_SECOND = "#9aa4b8"
FG_MUTED = "#6b7280"
ACCENT = "#9085e9"       # UI marker only — deliberately not the EMA blue
AMBER = "#f59e0b"
GRID = "#1b2130"

# ---------------------------------------------------------------------------
# CHART COLOURS — chosen with a validator, not by eye
# ---------------------------------------------------------------------------
# These were run through the palette checker for colour-blind separation on this
# dark surface, and the results were not what picking by eye would have given:
#
#   * The old EMA20 blue (#60a5fa) and EMA50 purple (#a78bfa) measured ΔE 0.3
#     under deuteranopia — to a red-green colour-blind reader (about 1 man in
#     12) the two moving averages were literally the same line. Even in full
#     colour they scored 10.2, under the 15 "can a normal reader tell these
#     apart" floor.
#   * Candle green mattered just as much. #22a55e and #1faa63 look identical to
#     #199e70 on screen, but only #199e70 separates from the red under deuteranopia
#     (ΔE 8.3 vs 4.9 and 4.8). It is a touch more teal than the usual trading
#     green; that is the price of it being readable by everyone.
#
# Blue vs yellow — the pair that MUST separate, since both EMAs are solid lines
# in the same style — now measures ΔE 27.4 under CVD. VWAP carries a dashed
# stroke as secondary encoding, so it never depends on hue alone.
GREEN = "#199e70"        # bullish candle / target
RED = "#ef4444"          # bearish candle / stop
EMA_FAST_COL = "#3987e5"
EMA_SLOW_COL = "#c98500"
VWAP_COL = "#d55181"

MAX_BARS = 70          # how many candles fit comfortably without turning to mush


# ===========================================================================
# CHART
# ===========================================================================
class ChartPanel:
    def __init__(self, parent, height=380):
        self.frame = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER,
                              highlightthickness=1, bd=0)
        head = tk.Frame(self.frame, bg=BG_PANEL)
        head.pack(fill="x", padx=14, pady=(10, 0))
        self.title_label = tk.Label(head, text="—", font=tkfont.Font(size=11, weight="bold"),
                                    bg=BG_PANEL, fg=FG_PRIMARY)
        self.title_label.pack(side="left")
        self.sub_label = tk.Label(head, text="", font=tkfont.Font(size=9),
                                  bg=BG_PANEL, fg=FG_MUTED)
        self.sub_label.pack(side="left", padx=(9, 0))

        # Legend, so the coloured lines aren't a guessing game.
        leg = tk.Frame(head, bg=BG_PANEL)
        leg.pack(side="right")
        for text, colour in (
            (f"EMA{config.EMA_FAST}", EMA_FAST_COL),
            (f"EMA{config.EMA_SLOW}", EMA_SLOW_COL),
            ("VWAP", VWAP_COL),
        ):
            tk.Label(leg, text="—", font=tkfont.Font(size=11, weight="bold"),
                     bg=BG_PANEL, fg=colour).pack(side="left", padx=(8, 2))
            tk.Label(leg, text=text, font=tkfont.Font(size=9),
                     bg=BG_PANEL, fg=FG_MUTED).pack(side="left")

        self.canvas = tk.Canvas(self.frame, height=height, bg=BG_PANEL,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self._state = None

    # -------------------------------------------------------------- public
    def pack(self, **kw):
        self.frame.pack(**kw)

    def pack_forget(self):
        self.frame.pack_forget()

    def show(self, df, rec, trade, title, subtitle):
        self._state = {"df": df, "rec": rec, "trade": trade}
        self.title_label.config(text=title)
        self.sub_label.config(text=subtitle)
        self._redraw()

    def clear(self, message="Press Start to load candles."):
        self._state = None
        self.canvas.delete("all")
        try:
            w = max(self.canvas.winfo_width(), 100)
            h = max(self.canvas.winfo_height(), 60)
            self.canvas.create_text(w / 2, h / 2, text=message,
                                    fill=FG_MUTED, font=("TkDefaultFont", 10))
        except Exception:
            pass

    # ------------------------------------------------------------- drawing
    def _redraw(self):
        """Hands the real Tk canvas to the shared drawing function — the same
        one the web server hands an SvgCanvas to."""
        st = self._state
        c = self.canvas
        c.delete("all")
        if not st or st["df"] is None or len(st["df"]) < 2:
            return
        try:
            W, H = int(c.winfo_width()), int(c.winfo_height())
        except Exception:
            return
        draw_chart(c, W, H, st["df"], st["rec"], st["trade"])


# ===========================================================================
# THE DRAWING ITSELF — deliberately not a method
# ===========================================================================
def draw_chart(c, W, H, df, rec, trade):
    """Draw the whole chart onto `c`.

    `c` only has to speak the Tk canvas dialect — create_line, create_rectangle,
    create_text, create_polygon. The desktop app passes a real tk.Canvas; the
    web server passes an SvgCanvas that writes SVG instead of pixels.

    One function, two front ends. The alternative — reimplementing candles,
    EMAs and level lines in JavaScript — guarantees the website and the window
    eventually disagree about what your chart looks like, and you'd have no way
    to tell which one was lying.
    """
    rec = rec or {}
    if df is None or len(df) < 2 or W < 120 or H < 80:
        return

    pad_l, pad_r, pad_t, pad_b = 6, 82, 8, 22
    x0, x1 = pad_l, W - pad_r
    y0, y1 = pad_t, H - pad_b
    if x1 - x0 < 40:
        return

    n = min(MAX_BARS, len(df))
    view = df.iloc[-n:]
    opens = [float(v) for v in view["Open"]]
    highs = [float(v) for v in view["High"]]
    lows = [float(v) for v in view["Low"]]
    closes = [float(v) for v in view["Close"]]

    lo, hi = min(lows), max(highs)
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo

    overlays = [
        (_series(df, "vwap", n), VWAP_COL, (3, 3)),
        (_series(df, "ema_slow", n), EMA_SLOW_COL, None),
        (_series(df, "ema_fast", n), EMA_FAST_COL, None),
    ]

    # Anything off the chart — a trade level, an EMA, VWAP — is included
    # in the price scale only if it's close enough that showing it doesn't
    # squash the candles into a ribbon. A target four screens away tells
    # you nothing, and a slow EMA far below in a hard trend is the same.
    # Whatever still falls outside gets clamped to the edge when drawn,
    # so a line can never escape the plot and scribble over the axis.
    levels = _levels(rec, trade)

    # Tested against the ORIGINAL candle bounds, not the growing ones —
    # otherwise each accepted value widens the window for the next, and a
    # run of values could walk the scale out arbitrarily far.
    base_lo, base_hi = lo, hi

    def widen(value, window):
        nonlocal lo, hi
        if value == value and base_lo - span * window <= value <= base_hi + span * window:
            lo, hi = min(lo, value), max(hi, value)

    # Trade levels get the wider allowance — seeing where your stop sits
    # relative to price is worth some squashing. The overlays get a much
    # tighter one: a slow EMA or a session VWAP a long way from price
    # would otherwise eat half the chart to show a line you don't need
    # positioned precisely, and the candles are the point.
    for _, price, _, _ in levels:
        widen(price, 0.8)
    for values, _, _ in overlays:
        for v in values:
            widen(v, 0.35)

    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    rng = hi - lo

    def y_of(p):
        return y1 - (p - lo) / rng * (y1 - y0)

    def y_clamped(p):
        return min(y1, max(y0, y_of(p)))

    cw = (x1 - x0) / n

    def x_of(i):
        return x0 + (i + 0.5) * cw

    # ---- grid + price axis -------------------------------------------
    for k in range(5):
        p = lo + rng * k / 4
        yy = y_of(p)
        c.create_line(x0, yy, x1, yy, fill=GRID)
        c.create_text(x1 + 6, yy, text=f"{p:,.0f}", anchor="w",
                      fill=FG_MUTED, font=("TkDefaultFont", 8))

    # ---- time axis ----------------------------------------------------
    step = max(1, n // 6)
    for i in range(0, n, step):
        try:
            stamp = view.index[i].strftime("%H:%M")
        except Exception:
            continue
        c.create_text(x_of(i), y1 + 11, text=stamp, fill=FG_MUTED,
                      font=("TkDefaultFont", 8))

    # ---- overlays -----------------------------------------------------
    # Computed on the FULL series and then sliced, so the EMA at the left
    # edge of the view is the real EMA, not one restarted from scratch.
    for values, colour, dash in overlays:
        _line(c, values, x_of, y_clamped, colour, dash=dash)

    # ---- candles ------------------------------------------------------
    body_w = max(1.0, cw * 0.62)
    for i in range(n):
        o, h, l, cl = opens[i], highs[i], lows[i], closes[i]
        up = cl >= o
        colour = GREEN if up else RED
        xc = x_of(i)
        c.create_line(xc, y_of(h), xc, y_of(l), fill=colour)
        top, bot = y_of(max(o, cl)), y_of(min(o, cl))
        if bot - top < 1:
            bot = top + 1
        last = (i == n - 1)
        c.create_rectangle(xc - body_w / 2, top, xc + body_w / 2, bot,
                           fill=colour if not up else BG_PANEL,
                           outline=AMBER if last else colour,
                           width=2 if last else 1)

    # The newest candle is still forming — say so on the chart, because a
    # half-built candle looks like a real one and its close will change.
    # Right-anchored to the LEFT of the last candle, so it can't collide
    # with the price tags sitting on the right-hand axis.
    c.create_text(x_of(n - 1) - body_w, max(y0 + 4, y_of(highs[-1]) - 9),
                  text="forming", anchor="e", fill=AMBER, font=("TkDefaultFont", 7))

    # ---- trade levels -------------------------------------------------
    for label, price, colour, style in levels:
        if not (lo <= price <= hi):
            continue
        yy = y_of(price)
        c.create_line(x0, yy, x1, yy, fill=colour,
                      dash=(5, 4) if style == "dash" else (1, 3))
        c.create_rectangle(x1 + 1, yy - 7, W - 1, yy + 7, fill=colour, outline=colour)
        c.create_text(x1 + 4, yy, text=f"{label} {price:,.0f}", anchor="w",
                      fill="#0b0d12", font=("TkDefaultFont", 8, "bold"))

    # ---- where the signal fired ---------------------------------------
    idx = _entry_bar(view, trade)
    if idx is not None:
        xc = x_of(idx)
        c.create_line(xc, y0, xc, y1, fill=ACCENT, dash=(2, 4))
        up = trade.get("option_type") == "CE"
        # Kept clear of the plot edges so the arrow and its caption can't
        # spill over the time axis or off the top of the canvas.
        raw = y_of(lows[idx]) + 14 if up else y_of(highs[idx]) - 14
        ay = min(y1 - 20, max(y0 + 20, raw))
        c.create_polygon(
            xc, ay - 7 if up else ay + 7, xc - 6, ay + 4 if up else ay - 4,
            xc + 6, ay + 4 if up else ay - 4,
            fill=GREEN if up else RED, outline="")
        c.create_text(xc, ay + 15 if up else ay - 15,
                      text=f"{trade.get('option_type','')} signal",
                      fill=ACCENT, font=("TkDefaultFont", 8, "bold"))

    # ---- last price ---------------------------------------------------
    last_px = closes[-1]
    yy = y_of(last_px)
    c.create_line(x0, yy, x1, yy, fill=FG_SECOND, dash=(2, 2))
    c.create_rectangle(x1 + 1, yy - 8, W - 1, yy + 8, fill=FG_PRIMARY, outline=FG_PRIMARY)
    c.create_text(x1 + 4, yy, text=f"{last_px:,.0f}", anchor="w",
                  fill="#0b0d12", font=("TkDefaultFont", 9, "bold"))



# ===========================================================================
# SVG BACKEND — the same drawing, as a picture the browser can show
# ===========================================================================
class SvgCanvas:
    """Records Tk canvas calls and renders them as SVG."""

    def __init__(self, width, height, bg=BG_PANEL):
        self.w, self.h, self.bg = width, height, bg
        self.parts = []

    def delete(self, *a):
        self.parts = []

    @staticmethod
    def _dash(kw):
        d = kw.get("dash")
        return f' stroke-dasharray="{d[0]},{d[1]}"' if d else ""

    @staticmethod
    def _esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def create_line(self, *c, **kw):
        pts = " ".join(f"{c[i]:.1f},{c[i+1]:.1f}" for i in range(0, len(c) - 1, 2))
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{kw.get("fill", "none")}" '
            f'stroke-width="{kw.get("width", 1)}"{self._dash(kw)}/>')

    def create_rectangle(self, x0, y0, x1, y1, **kw):
        fill = kw.get("fill") or "none"
        out = kw.get("outline") or "none"
        self.parts.append(
            f'<rect x="{min(x0,x1):.1f}" y="{min(y0,y1):.1f}" width="{abs(x1-x0):.1f}" '
            f'height="{abs(y1-y0):.1f}" fill="{fill}" stroke="{out}" '
            f'stroke-width="{kw.get("width", 1)}"/>')

    def create_polygon(self, *c, **kw):
        pts = " ".join(f"{c[i]:.1f},{c[i+1]:.1f}" for i in range(0, len(c) - 1, 2))
        self.parts.append(f'<polygon points="{pts}" fill="{kw.get("fill", "none")}"/>')

    def create_text(self, x, y, **kw):
        f = kw.get("font", ("", 9))
        size = f[1] if isinstance(f, (tuple, list)) and len(f) > 1 else 9
        bold = ' font-weight="700"' if isinstance(f, (tuple, list)) and "bold" in f else ""
        anchor = {"w": "start", "e": "end"}.get(kw.get("anchor", ""), "middle")
        self.parts.append(
            f'<text x="{x:.1f}" y="{y + size/3:.1f}" fill="{kw.get("fill", "#fff")}" '
            f'font-size="{size + 2}" font-family="-apple-system,Segoe UI,Roboto,sans-serif" '
            f'text-anchor="{anchor}"{bold}>{self._esc(kw.get("text", ""))}</text>')

    def to_svg(self):
        # NO preserveAspectRatio="none". Stretching the viewBox to fit the
        # container squashes the text along with the candles, which on a phone
        # made the price axis unreadable. The chart is drawn at the size it
        # will actually be shown at instead — the caller passes the client's
        # real width — so it needs no distortion to fit.
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" style="width:100%;height:auto">'
                f'<rect width="{self.w}" height="{self.h}" fill="{self.bg}"/>'
                + "".join(self.parts) + "</svg>")


def chart_svg(df, rec, trade, width=900, height=None):
    """Height defaults to a sensible ratio of the width, floored so a narrow
    phone still gets a chart tall enough to read rather than a letterbox."""
    if height is None:
        height = int(min(440, max(260, width * 0.42)))
    c = SvgCanvas(width, height)
    draw_chart(c, width, height, df, rec, trade)
    return c.to_svg()


# ---------------------------------------------------------------- helpers
def _series(df, which, n):
    try:
        if which == "vwap":
            s = ind.vwap(df)
        elif which == "ema_fast":
            s = ind.ema(df["Close"], config.EMA_FAST)
        else:
            s = ind.ema(df["Close"], config.EMA_SLOW)
        return [float(v) for v in s.iloc[-n:]]
    except Exception:
        return []

def _line(c, values, x_of, y_of, colour, dash=None):
    pts = []
    for i, v in enumerate(values):
        if v != v:      # NaN
            continue
        pts.extend([x_of(i), y_of(v)])
    if len(pts) >= 4:
        if dash:
            c.create_line(*pts, fill=colour, width=1, dash=dash)
        else:
            c.create_line(*pts, fill=colour, width=2)

def _levels(rec, trade):
    """(label, price, colour, style) for every line worth drawing.

    A locked ticket wins over the live read: those are the numbers being
    traded, and they never move once issued.
    """
    out = []
    if trade is not None:
        entry = trade.get("entry_spot")
        if entry:
            out.append(("ENTRY", float(entry), FG_PRIMARY, "dash"))
        for i, t in enumerate(trade.get("index_targets") or []):
            if t:
                out.append((f"T{i+1}", float(t), GREEN, "dash"))
        if trade.get("index_sl"):
            out.append(("STOP", float(trade["index_sl"]), RED, "dash"))
        return out

    # No ticket — preview the levels the live signal would use, drawn as
    # dotted so it's obvious nothing is locked in.
    for i, t in enumerate(rec.get("index_targets") or []):
        if t:
            out.append((f"t{i+1}", float(t), "#1f7a4d", "dot"))
    if rec.get("index_stop_loss"):
        out.append(("sl", float(rec["index_stop_loss"]), "#8a3030", "dot"))
    return out

def _entry_bar(view, trade):
    """Which visible candle the ticket was issued on."""
    if not trade or not trade.get("entry_ts"):
        return None
    try:
        ts = trade["entry_ts"]
        best, best_gap = None, None
        for i, stamp in enumerate(view.index):
            a = stamp.to_pydatetime()
            b = ts
            # Candle stamps may be tz-aware or naive depending on the data
            # source; subtracting one of each raises. Match them up first.
            if (a.tzinfo is None) != (b.tzinfo is None):
                a = a.replace(tzinfo=None)
                b = b.replace(tzinfo=None)
            gap = abs((a - b).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = i, gap
        # Only mark it if the entry is genuinely inside the window shown.
        if best_gap is not None and best_gap < 3600:
            return best
    except Exception:
        return None
    return None


# ===========================================================================
# WHY PANEL
# ===========================================================================
class WhyPanel:
    """The reasoning, in sentences. Rows are rebuilt on each render rather
    than kept as fixed widgets, because the number of level lines varies."""

    def __init__(self, parent):
        self.frame = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER,
                              highlightthickness=1, bd=0)
        self.mode = tk.StringVar(value="ticket")   # "ticket" | "live"
        self._has_ticket = False

        head = tk.Frame(self.frame, bg=BG_PANEL)
        head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(head, text="WHY", font=tkfont.Font(size=9, weight="bold"),
                 bg=BG_PANEL, fg=FG_MUTED).pack(side="left")

        self.toggle_wrap = tk.Frame(head, bg=BG_PANEL)
        self.toggle_wrap.pack(side="right")
        self.toggle_btns = {}
        for key, text in (("ticket", "This ticket"), ("live", "Right now")):
            b = tk.Label(self.toggle_wrap, text=text, font=tkfont.Font(size=9),
                         bg=BG_INPUT, fg=FG_SECOND, padx=9, pady=3, cursor="hand2")
            b.pack(side="left", padx=(4, 0))
            b.bind("<Button-1>", lambda e, k=key: self._set_mode(k))
            self.toggle_btns[key] = b

        self.headline = tk.Label(self.frame, text="—", font=tkfont.Font(size=15, weight="bold"),
                                 bg=BG_PANEL, fg=FG_SECOND, anchor="w")
        self.headline.pack(anchor="w", padx=16, pady=(4, 0))
        self.stamp = tk.Label(self.frame, text="", font=tkfont.Font(size=9),
                              bg=BG_PANEL, fg=FG_MUTED, anchor="w")
        self.stamp.pack(anchor="w", padx=16)

        self.rows = tk.Frame(self.frame, bg=BG_PANEL)
        self.rows.pack(fill="x", padx=16, pady=(10, 14))
        self.rows.grid_columnconfigure(2, weight=1)

        self._wrap = 620
        self._texts = []
        self.frame.bind("<Configure>", self._on_resize)
        self._on_change = None
        self._last = None
        self._sig = None

    # -------------------------------------------------------------- public
    def pack(self, **kw):
        self.frame.pack(**kw)

    def pack_forget(self):
        self.frame.pack_forget()

    def set_on_change(self, fn):
        self._on_change = fn

    def render(self, live_rec, trade):
        """live_rec = the current read. trade = the locked ticket, whose
        frozen explanation lives in trade['why']."""
        self._has_ticket = bool(trade and trade.get("why"))
        if not self._has_ticket:
            self.mode.set("live")
        mode = self.mode.get()

        if mode == "ticket" and self._has_ticket:
            data = trade["why"]
            stamp = (f"Frozen at {trade.get('entry_time','?')} IST, when the ticket was "
                     f"issued — this is why it fired, not what the market looks like now.")
        else:
            data = explain.explain(live_rec)
            stamp = "The current live read, recomputed every second."

        self._paint_toggle()
        self.headline.config(text=data.get("headline", "—"),
                             fg=self._headline_colour(data.get("headline", "")))
        self.stamp.config(text=stamp)

        # Rebuilding ~12 labels every second would churn widgets pointlessly
        # and flicker on screen — the sentences only change when a reading
        # crosses a threshold, which is rare. Rebuild only when they differ.
        sig = (mode, data.get("headline"), data.get("verdict"),
               tuple((v["name"], v["vote"], v["text"]) for v in data.get("votes", [])),
               (data.get("gate") or {}).get("text"), tuple(data.get("levels") or ()))
        if sig != self._sig:
            self._sig = sig
            self._build_rows(data)
        self._last = (live_rec, trade)

    # ------------------------------------------------------------- internal
    def _set_mode(self, key):
        if key == "ticket" and not self._has_ticket:
            return
        self.mode.set(key)
        if self._last:
            self.render(*self._last)
        if self._on_change:
            self._on_change()

    def _paint_toggle(self):
        for key, b in self.toggle_btns.items():
            active = (key == self.mode.get())
            enabled = self._has_ticket or key == "live"
            b.config(bg=ACCENT if active else BG_INPUT,
                     fg=FG_PRIMARY if active else (FG_SECOND if enabled else FG_MUTED))

    @staticmethod
    def _headline_colour(text):
        if text.startswith("BUY CE"):
            return GREEN
        if text.startswith("BUY PE"):
            return RED
        return FG_SECOND

    def _on_resize(self, event):
        wrap = max(280, int(event.width) - 150)
        if abs(wrap - self._wrap) < 24:
            return
        self._wrap = wrap
        for lbl in self._texts:
            try:
                lbl.config(wraplength=wrap)
            except Exception:
                pass

    def _build_rows(self, data):
        for w in list(self.rows.winfo_children()) if hasattr(self.rows, "winfo_children") else []:
            w.destroy()
        self._texts = []
        r = 0

        def sep():
            nonlocal r
            tk.Frame(self.rows, bg=BORDER, height=1).grid(
                row=r, column=0, columnspan=3, sticky="we", pady=8)
            r += 1

        def row(mark, mark_col, name, text, name_col=FG_SECOND):
            nonlocal r
            tk.Label(self.rows, text=mark, font=tkfont.Font(size=11, weight="bold"),
                     bg=BG_PANEL, fg=mark_col, width=2, anchor="w").grid(
                row=r, column=0, sticky="nw", pady=2)
            tk.Label(self.rows, text=name, font=tkfont.Font(size=10, weight="bold"),
                     bg=BG_PANEL, fg=name_col, width=8, anchor="w").grid(
                row=r, column=1, sticky="nw", pady=2)
            lbl = tk.Label(self.rows, text=text, font=tkfont.Font(size=10),
                           bg=BG_PANEL, fg=FG_SECOND, anchor="w", justify="left",
                           wraplength=self._wrap)
            lbl.grid(row=r, column=2, sticky="w", pady=2)
            self._texts.append(lbl)
            r += 1

        for v in data.get("votes", []):
            vote = v.get("vote")
            if vote is None:
                mark, col = "n/a", FG_MUTED
            elif vote > 0:
                mark, col = "▲", GREEN
            elif vote < 0:
                mark, col = "▼", RED
            else:
                mark, col = "–", FG_MUTED
            row(mark, col, v["name"], v["text"])

        gate = data.get("gate") or {}
        if gate.get("text"):
            sep()
            ok = gate.get("ok")
            mark, col = ("✓", GREEN) if ok else (("✕", RED) if ok is False else ("·", FG_MUTED))
            row(mark, col, "Gate", gate["text"])

        sep()
        row("=", ACCENT, "Verdict", data.get("verdict", ""), name_col=FG_PRIMARY)

        levels = data.get("levels") or []
        if levels:
            sep()
            for i, text in enumerate(levels):
                row("·" if i else "▸", AMBER, "Levels" if i == 0 else "", text)
