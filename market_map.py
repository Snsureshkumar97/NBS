"""
market_map.py — live sector heat map of the index constituents
================================================================================
A treemap of the stocks that make up the index you're trading, coloured by
each one's move today: deep green up, deep red down. It answers the question
the signal engine can't — is the whole market moving, or is one heavyweight
dragging the index while everything else goes the other way?

Prices come from the same Kite WebSocket the rest of the tool already uses,
subscribed in QUOTE mode so each tick carries the day's % change directly.

*** THE CONSTITUENT LISTS BELOW NEED OCCASIONAL MANUAL UPDATING. ***
Index membership and weights change when the exchange rebalances (roughly
twice a year for Nifty). Weights especially drift continuously with prices —
the ones here are approximate and only control how big each tile is drawn,
never any number the tool calculates or trades on. A stale weight makes a
box slightly the wrong size; it cannot make a price wrong.
"""

# Tk is needed only by MarketMapWindow, which is the desktop app's own
# window. Everything else here — the constituent lists, squarify(), breadth(),
# heat_colour() — is arithmetic, and the website needs exactly those. A hosted
# Python image often has no tkinter at all, so importing it unconditionally
# would make this module unimportable on the very box that only wants the
# arithmetic.
try:
    import tkinter as tk
    from tkinter import font as tkfont
    HAVE_TK = True
except Exception:            # ImportError, or a Tk that cannot find a display
    tk = None
    tkfont = None
    HAVE_TK = False

# --- palette, matched to the main window -----------------------------------
BG_APP = "#0b0d12"
BG_PANEL = "#141924"
BORDER = "#2a3141"
FG_PRIMARY = "#eef1f6"
FG_SECOND = "#9aa4b8"
FG_MUTED = "#6b7280"


# Colour ramp: flat grey at 0%, saturating to full green/red by +/-2%.
def heat_colour(pct):
    if pct is None:
        return "#232936"
    p = max(-2.0, min(2.0, float(pct))) / 2.0
    if p >= 0:
        # #1f2a2f -> #16a34a -> #052e16 style ramp (up)
        r = int(0x1c + (0x16 - 0x1c) * p)
        g = int(0x23 + (0xC8 - 0x23) * p)
        b = int(0x30 + (0x60 - 0x30) * p)
    else:
        q = -p
        r = int(0x1c + (0xE0 - 0x1c) * q)
        g = int(0x23 + (0x2B - 0x23) * q)
        b = int(0x30 + (0x36 - 0x30) * q)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Constituents: (tradingsymbol, sector, approximate index weight %)
# ---------------------------------------------------------------------------
NIFTY50 = [
    ("HDFCBANK", "Financial Services", 13.3), ("ICICIBANK", "Financial Services", 8.9),
    ("RELIANCE", "Oil & Gas", 8.2), ("INFY", "Information Technology", 5.1),
    ("BHARTIARTL", "Telecom", 4.5), ("ITC", "FMCG", 3.9),
    ("TCS", "Information Technology", 3.7), ("LT", "Construction", 3.6),
    ("AXISBANK", "Financial Services", 3.1), ("KOTAKBANK", "Financial Services", 2.8),
    ("SBIN", "Financial Services", 2.7), ("M&M", "Automobiles", 2.4),
    ("BAJFINANCE", "Financial Services", 2.2), ("HINDUNILVR", "FMCG", 2.1),
    ("MARUTI", "Automobiles", 1.9), ("SUNPHARMA", "Healthcare", 1.8),
    ("NTPC", "Power", 1.6), ("HCLTECH", "Information Technology", 1.6),
    ("TATAMOTORS", "Automobiles", 1.5), ("ULTRACEMCO", "Construction", 1.3),
    ("TITAN", "Consumer Durables", 1.3), ("POWERGRID", "Power", 1.2),
    ("ASIANPAINT", "Consumer Durables", 1.1), ("BAJAJFINSV", "Financial Services", 1.1),
    ("ADANIENT", "Metals & Mining", 1.0), ("ONGC", "Oil & Gas", 1.0),
    ("TATASTEEL", "Metals & Mining", 1.0), ("COALINDIA", "Oil & Gas", 1.0),
    ("BAJAJ-AUTO", "Automobiles", 0.9), ("NESTLEIND", "FMCG", 0.9),
    ("JSWSTEEL", "Metals & Mining", 0.9), ("WIPRO", "Information Technology", 0.8),
    ("GRASIM", "Construction", 0.8), ("ADANIPORTS", "Services", 0.8),
    ("HDFCLIFE", "Financial Services", 0.7), ("TECHM", "Information Technology", 0.7),
    ("SBILIFE", "Financial Services", 0.7), ("CIPLA", "Healthcare", 0.7),
    ("DRREDDY", "Healthcare", 0.6), ("SHRIRAMFIN", "Financial Services", 0.6),
    ("HINDALCO", "Metals & Mining", 0.6), ("EICHERMOT", "Automobiles", 0.6),
    ("TATACONSUM", "FMCG", 0.5), ("BRITANNIA", "FMCG", 0.5),
    ("APOLLOHOSP", "Healthcare", 0.5), ("HEROMOTOCO", "Automobiles", 0.4),
    ("INDUSINDBK", "Financial Services", 0.4), ("BPCL", "Oil & Gas", 0.4),
    ("BEL", "Capital Goods", 0.4), ("TRENT", "Consumer Services", 0.4),
]

BANKNIFTY = [
    ("HDFCBANK", "Private Banks", 28.0), ("ICICIBANK", "Private Banks", 24.0),
    ("SBIN", "Public Banks", 9.5), ("AXISBANK", "Private Banks", 9.0),
    ("KOTAKBANK", "Private Banks", 8.5), ("INDUSINDBK", "Private Banks", 5.0),
    ("BANKBARODA", "Public Banks", 3.0), ("PNB", "Public Banks", 2.5),
    ("AUBANK", "Private Banks", 2.5), ("FEDERALBNK", "Private Banks", 2.5),
    ("IDFCFIRSTB", "Private Banks", 2.0), ("CANBK", "Public Banks", 2.0),
]

SENSEX = [
    ("HDFCBANK", "Financial Services", 15.0), ("ICICIBANK", "Financial Services", 10.0),
    ("RELIANCE", "Oil & Gas", 9.5), ("INFY", "Information Technology", 5.8),
    ("BHARTIARTL", "Telecom", 5.0), ("ITC", "FMCG", 4.4),
    ("TCS", "Information Technology", 4.2), ("LT", "Construction", 4.0),
    ("AXISBANK", "Financial Services", 3.5), ("KOTAKBANK", "Financial Services", 3.2),
    ("SBIN", "Financial Services", 3.0), ("M&M", "Automobiles", 2.7),
    ("HINDUNILVR", "FMCG", 2.4), ("BAJFINANCE", "Financial Services", 2.4),
    ("MARUTI", "Automobiles", 2.2), ("SUNPHARMA", "Healthcare", 2.0),
    ("NTPC", "Power", 1.8), ("HCLTECH", "Information Technology", 1.8),
    ("TATAMOTORS", "Automobiles", 1.7), ("ULTRACEMCO", "Construction", 1.5),
    ("TITAN", "Consumer Durables", 1.5), ("POWERGRID", "Power", 1.3),
    ("ASIANPAINT", "Consumer Durables", 1.2), ("ADANIPORTS", "Services", 0.9),
    ("NESTLEIND", "FMCG", 1.0), ("TECHM", "Information Technology", 0.8),
    ("TATASTEEL", "Metals & Mining", 1.1), ("INDUSINDBK", "Financial Services", 0.5),
    ("ZOMATO", "Consumer Services", 1.0), ("BAJAJFINSV", "Financial Services", 1.2),
]

CONSTITUENTS = {"NIFTY": NIFTY50, "BANKNIFTY": BANKNIFTY, "SENSEX": SENSEX}


# ---------------------------------------------------------------------------
# Squarified treemap — Bruls, Huizing & van Wijk (2000)
# ---------------------------------------------------------------------------
def _layout_row(row, x, y, dx, dy, out):
    """Place one strip of rectangles and return the rectangle left over.

    The strip always runs along the SHORTER side of the remaining space —
    that's the whole point of the algorithm, and getting it the wrong way
    round produces full-width slivers with 40:1 aspect ratios instead of
    near-squares."""
    total = sum(row)
    if total <= 0:
        return x, y, dx, dy
    if dx >= dy:
        # Wide space left: cut a vertical column off the left edge.
        w = total / dy if dy else 0
        cy = y
        for v in row:
            h = v / total * dy
            out.append((x, cy, w, h))
            cy += h
        return x + w, y, dx - w, dy
    else:
        # Tall space left: cut a horizontal band off the top.
        h = total / dx if dx else 0
        cx = x
        for v in row:
            w = v / total * dx
            out.append((cx, y, w, h))
            cx += w
        return x, y + h, dx, dy - h


def _worst(row, side):
    if not row or side <= 0:
        return float("inf")
    total = sum(row)
    if total <= 0:
        return float("inf")
    mx, mn = max(row), min(row)
    s2 = total * total
    side2 = side * side
    return max(side2 * mx / s2, s2 / (side2 * mn))


def squarify(values, x, y, dx, dy):
    """Lay `values` out as rectangles filling (x, y, dx, dy), keeping each
    one as close to square as possible — far more readable than naive
    slicing when sizes vary a lot."""
    values = [v for v in values if v > 0]
    if not values or dx <= 0 or dy <= 0:
        return []
    total = sum(values)
    scaled = [v / total * dx * dy for v in values]

    out, row = [], []
    while scaled:
        side = min(dx, dy)
        if not row or _worst(row + [scaled[0]], side) <= _worst(row, side):
            row.append(scaled.pop(0))
        else:
            x, y, dx, dy = _layout_row(row, x, y, dx, dy, out)
            row = []
    if row:
        _layout_row(row, x, y, dx, dy, out)
    return out


# Subclassing happens at import time, so the base has to exist even when Tk
# does not. Without Tk the class is still defined and still importable; it
# simply cannot be instantiated, which is the honest outcome on a machine with
# no display.
_WindowBase = tk.Toplevel if HAVE_TK else object


class MarketMapWindow(_WindowBase):
    """A separate window so the heat map gets real estate without squeezing
    the signal ticket. Reads prices straight from the shared streamer."""

    def __init__(self, master, streamer, provider, index_key, refresh_ms=1000):
        super().__init__(master)
        self.title(f"Market Map — {index_key}")
        self.geometry("980x680")
        self.minsize(560, 420)
        self.configure(bg=BG_APP)

        self.streamer = streamer
        self.provider = provider
        self.index_key = index_key
        self.refresh_ms = refresh_ms
        self.tokens = {}          # tradingsymbol -> instrument_token
        self._alive = True
        self._subscribe_note = "resolving instruments..."

        head = tk.Frame(self, bg=BG_APP)
        head.pack(fill="x", padx=12, pady=(10, 0))
        self.title_label = tk.Label(head, text=f"{index_key} — constituents",
                                     font=tkfont.Font(size=13, weight="bold"),
                                     bg=BG_APP, fg=FG_PRIMARY)
        self.title_label.pack(side="left")
        self.breadth_label = tk.Label(head, text="", font=tkfont.Font(size=11),
                                       bg=BG_APP, fg=FG_SECOND)
        self.breadth_label.pack(side="right")

        self.canvas = tk.Canvas(self, bg=BG_APP, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=10)
        self.canvas.bind("<Configure>", lambda e: self._draw())

        self.status = tk.Label(self, text="resolving instruments...",
                                font=tkfont.Font(size=9), bg=BG_APP, fg=FG_MUTED)
        self.status.pack(anchor="w", padx=14, pady=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(50, self._resolve_tokens)
        self.after(self.refresh_ms, self._tick)

    # ------------------------------------------------------------------
    def _resolve_tokens(self):
        """Map each tradingsymbol to its instrument token, then subscribe the
        whole basket in quote mode so every tick carries % change."""
        syms = [s for s, _, _ in CONSTITUENTS.get(self.index_key, [])]
        try:
            self.tokens = self.provider.equity_tokens(syms)
        except Exception as e:
            self.status.config(text=f"could not resolve instruments: {e}")
            return
        missing = [s for s in syms if s not in self.tokens]
        if self.streamer is not None and self.tokens:
            self.streamer.subscribe(list(self.tokens.values()), quote=True)
        note = f"{len(self.tokens)} of {len(syms)} instruments streaming"
        if missing:
            note += f"  ·  not found: {', '.join(missing[:6])}"
            if len(missing) > 6:
                note += f" +{len(missing)-6}"
        self._subscribe_note = note
        self.status.config(text=note, fg=FG_MUTED)

    def _rows(self):
        out = []
        for sym, sector, weight in CONSTITUENTS.get(self.index_key, []):
            tok = self.tokens.get(sym)
            pct = self.streamer.change_pct(tok) if (self.streamer and tok) else None
            out.append({"sym": sym, "sector": sector, "weight": weight, "pct": pct})
        return out

    def _draw(self):
        c = self.canvas
        c.delete("all")
        W, H = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        warn, _ = self._feed_state()
        draw_map(c, W, H, self._rows(), dead=bool(warn and "FEED" in warn))
        text, colour = breadth(self._rows())
        self.breadth_label.config(text=text, fg=colour or FG_SECOND)

    def _feed_state(self):
        """(label, colour) describing whether these numbers are actually
        live. A frozen map that still says 'streaming' is worse than no map
        at all — you'd read stale prices as current ones."""
        s = self.streamer
        if s is None:
            return "FEED STOPPED — these numbers are frozen", "#ef4444"
        if not getattr(s, "connected", False):
            return "FEED DISCONNECTED — frozen at the last tick received", "#ef4444"
        try:
            age = s.age_seconds()
        except Exception:
            age = None
        if age is None:
            return "connected, waiting for first tick", FG_MUTED
        if age > 20:
            return f"STALE — no tick for {age:.0f}s", "#f59e0b"
        return None, None

    def _tick(self):
        if not self._alive:
            return
        try:
            self._draw()
            warn, colour = self._feed_state()
            if warn:
                self.status.config(text=warn, fg=colour)
                self.title_label.config(text=f"{self.index_key} — constituents  (not live)")
            else:
                self.status.config(text=self._subscribe_note, fg=FG_MUTED)
                self.title_label.config(text=f"{self.index_key} — constituents")
        except Exception:
            pass
        self.after(self.refresh_ms, self._tick)

    def close(self):
        self._alive = False
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Drawing, split out of the window so the same treemap can be painted into the
# main screen's panel as well. Same arrangement as chart_panel.draw_chart: `c`
# only has to speak the Tk canvas dialect, and every font is a plain tuple
# rather than a tkfont.Font, so no live Tk is required to draw one.


def draw_map(c, W, H, rows, dead=False, tags=()):
    if not rows:
        c.create_text(W / 2, H / 2, text="no constituent data", fill=FG_MUTED,
                      font=("TkDefaultFont", -12), tags=tags)
        return

    sectors = {}
    for r in rows:
        sectors.setdefault(r["sector"], []).append(r)
    order = sorted(sectors.items(), key=lambda kv: -sum(x["weight"] for x in kv[1]))

    boxes = squarify([sum(x["weight"] for x in v) for _, v in order], 0, 0, W, H)
    for (name, members), (bx, by, bw, bh) in zip(order, boxes):
        pad = 2
        c.create_rectangle(bx + pad, by + pad, bx + bw - pad, by + bh - pad,
                           outline=BORDER, fill=BG_PANEL, width=1, tags=tags)
        header = 14 if bh > 46 else 0
        if header:
            c.create_text(bx + 7, by + 8, text=name.upper(), anchor="w",
                          fill=FG_MUTED, font=("TkDefaultFont", -10), tags=tags)

        members = sorted(members, key=lambda x: -x["weight"])
        inner = squarify([m["weight"] for m in members],
                         bx + pad + 1, by + pad + header,
                         bw - 2 * pad - 2, bh - 2 * pad - header - 1)
        for m, (ix, iy, iw, ih) in zip(members, inner):
            if iw < 3 or ih < 3:
                continue
            # A dead feed is drawn flat grey, so a frozen map is obvious at a
            # glance and can't be mistaken for a calm market.
            fill = "#232936" if dead else heat_colour(m["pct"])
            c.create_rectangle(ix, iy, ix + iw, iy + ih, fill=fill,
                               outline=BG_APP, width=1, tags=tags)
            if iw > 46 and ih > 26:
                fs = 13 if (iw > 92 and ih > 44) else 10
                c.create_text(ix + iw / 2, iy + ih / 2 - 7, text=m["sym"],
                              fill="#ffffff", font=("TkDefaultFont", -fs, "bold"),
                              tags=tags)
                txt = "\u2014" if m["pct"] is None else f"{m['pct']:+.2f}%"
                c.create_text(ix + iw / 2, iy + ih / 2 + 9, text=txt, fill="#ffffff",
                              font=("TkDefaultFont", -(fs - 2)), tags=tags)


def breadth(rows):
    """How many constituents are up vs down, and the weighted average — a
    cross-check on the index price the main screen is showing."""
    known = [r for r in rows if r.get("pct") is not None]
    if not known:
        return "", None
    up = sum(1 for r in known if r["pct"] > 0)
    down = sum(1 for r in known if r["pct"] < 0)
    wsum = sum(r["weight"] for r in known) or 1
    avg = sum(r["pct"] * r["weight"] for r in known) / wsum
    return (f"{up} up \u00b7 {down} down   |   weighted {avg:+.2f}%",
            "#199e70" if avg > 0 else "#ef4444" if avg < 0 else FG_SECOND)


def rows_for(index_key, tokens, streamer):
    """The data draw_map wants, without needing the window object."""
    out = []
    for sym, sector, weight in CONSTITUENTS.get(index_key, []):
        tok = tokens.get(sym)
        pct = streamer.change_pct(tok) if (streamer and tok) else None
        out.append({"sym": sym, "sector": sector, "weight": weight, "pct": pct})
    return out
