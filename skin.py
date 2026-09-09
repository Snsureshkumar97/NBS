#!/usr/bin/env python3
"""
skin.py — the whole window, painted on one canvas
================================================================================
Every pixel of the interface is drawn here. There are no ttk widgets in the main
view, which is the point: ttk buttons, tabs and checkbuttons look different on
each OS and cannot do gradients or rounded corners at all.

Two layers, repainted independently:

  CHROME  backgrounds, cards, borders, buttons, tabs, static labels. Redrawn on
          resize, on hover, and when a toggle changes — all human-speed events.
  LIVE    every number that moves: prices, the ring, the vote list, the chart.
          Redrawn on each tick, which is why it is kept separate; repainting the
          gradients ten times a minute would be visible.

Clicks are hit-tested against rectangles collected while painting, so adding a
control means adding it in one place rather than three.
"""

import tkinter as tk

import theme as T
import ui_kit as K

RAIL_W = 98
PAD = 30
ROW1_Y, ROW2_Y = 28, 84
# Right-hand slice of the SESSION strip reserved for the day's total. Chrome
# stops the log box here so the money never lands on top of a border.
PF_W = 300
CTRL_H = 34
GAP = 16


class OffsetCanvas:
    """Lets chart_panel.draw_chart — which draws from (0,0) — live inside a
    panel. Every coordinate pair is shifted; nothing else changes, so the chart
    code stays canvas-agnostic and untouched."""

    def __init__(self, canvas, dx, dy, w, h, tags):
        self._c, self._dx, self._dy = canvas, dx, dy
        self._w, self._h, self._tags = w, h, tags

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def _shift(self, coords):
        out = list(coords)
        if len(out) == 1 and isinstance(out[0], (list, tuple)):
            out = list(out[0])
        return [v + (self._dx if i % 2 == 0 else self._dy) for i, v in enumerate(out)]

    def _mk(self, name, coords, kw):
        kw = dict(kw)
        kw["tags"] = self._tags
        return getattr(self._c, name)(*self._shift(coords), **kw)

    def create_line(self, *c, **k):
        return self._mk("create_line", c, k)

    def create_rectangle(self, *c, **k):
        return self._mk("create_rectangle", c, k)

    def create_polygon(self, *c, **k):
        return self._mk("create_polygon", c, k)

    def create_text(self, *c, **k):
        return self._mk("create_text", c, k)

    def create_oval(self, *c, **k):
        return self._mk("create_oval", c, k)

    def delete(self, *a):
        pass


def blank_state():
    return {
        "mode": "kite", "expiry": "(nearest)", "running": False,
        "status": "not started", "status_col": T.FG_MUTED,
        "toggles": {"popup": True, "rearm": True, "reentry": False,
                    "limits": False},
        "token": "not connected", "token_col": T.FG_MUTED,
        "indices": ["NIFTY", "BANKNIFTY", "SENSEX"], "index": "NIFTY",
        "view": "Signal", "span": "15m", "rail": "board", "lots": "1",
        "theme": "dark",
        "map": None,            # (rows, dead, breadth_text, breadth_colour)
        "page": None,           # {"title":…, "sub":…, "lines":[(text,colour,px,bold)]}
        "trend": {"label": "—", "sub": "waiting for data", "dir": "flat"},
        "spark": [], "day": (None, None, ""), "tab_marks": {},
        "timeframes": [("15m", "–", "#6b7280"), ("1 hour", "–", "#6b7280"),
                       ("VWAP", "–", "#6b7280")],
        "confidence": {"pct": None, "label": "—", "rows": []},
        "ticket": {
            "badge": "WAITING", "badge_col": T.ACC_B,
            "headline": "NO SIGNAL YET", "accent": True,
            "sub": "No trade locked in — a ticket is issued automatically on the next CE/PE signal.",
            "stats": [("ENTRY", "—", T.FG), ("NOW", "—", T.FG),
                      ("SPOT", "—", T.FG), ("1 LOT", "—", T.FG)],
            "levels": [("T1", "—", T.T1_COL), ("T2", "—", T.T2_COL),
                       ("T3", "—", T.T3_COL), ("STOP", "—", T.STOP_COL)],
        },
        "session": [],
        "portfolio": None,      # today's money across every index, live
        "chart": None,          # (df, rec, trade) when the Chart tab is showing
    }


class Skin:
    def __init__(self, root, callbacks):
        self.root = root
        self.cb = callbacks
        self.state = blank_state()
        self.canvas = tk.Canvas(root, bg=T.BG_APP, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._hits = []
        self._hover = None
        self._zones = {}        # hover key -> (canvas tag, redraw thunk)
        self._zone_n = 0
        self._tg = ("chrome",)  # tags for the widget currently being drawn
        self._size = (0, 0)
        self._resize_job = None
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))

    # -- plumbing ---------------------------------------------------------
    def _on_configure(self, event):
        if (event.width, event.height) == self._size:
            return
        self._size = (event.width, event.height)
        # Coalesce the burst of Configure events a drag produces; repainting on
        # every one of them makes a resize crawl.
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(60, self.repaint)

    def _set_hover(self, key):
        """Hover used to trigger a full chrome repaint. Sweeping the cursor
        across the window then meant thousands of canvas items deleted and
        recreated many times a second, which is what the flicker was — the
        canvas showing its bare background between the delete and the redraw.
        Now only the widget being left and the widget being entered are
        redrawn: two small patches, no visible flash."""
        if key == self._hover:
            return
        was, self._hover = self._hover, key
        for k in (was, key):
            if k is None:
                continue
            if k not in self._zones:
                # A hover target that isn't a registered zone (shouldn't
                # happen) — fall back to the old behaviour rather than
                # leaving the highlight stuck on.
                self.paint_chrome()
                return
        for k in (was, key):
            if k is not None:
                self._redraw_zone(k)

    def _zone(self, key, fn):
        """Draw one hover-sensitive widget under its own canvas tag, and
        remember how to draw it again. Everything `fn` creates is tagged both
        "chrome" (so a full repaint still clears it in one go) and the zone's
        private tag (so a hover change can clear just this widget)."""
        self._zone_n += 1
        tag = "z%d" % self._zone_n
        self._zones[key] = (tag, fn)
        return self._draw_zone(tag, fn)

    def _draw_zone(self, tag, fn):
        prev, self._tg = self._tg, ("chrome", tag)
        try:
            return fn()
        finally:
            self._tg = prev

    def _redraw_zone(self, key):
        entry = self._zones.get(key)
        if not entry:
            return
        tag, fn = entry
        c = self.canvas
        c.delete(tag)
        # The widget re-registers its own click box; drop the stale one first
        # so _hits doesn't grow by one entry per mouse move.
        self._hits = [h for h in self._hits if h[4] != key]
        self._draw_zone(tag, fn)
        # Redrawn items land on top of everything, including the live layer.
        # Put them back under it so a ticket can never end up behind a button.
        if c.find_withtag("live"):
            try:
                c.tag_lower(tag, "live")
            except tk.TclError:
                pass

    def _on_motion(self, event):
        self._set_hover(self._at(event.x, event.y, want_key=True))

    def _at(self, x, y, want_key=False):
        for x0, y0, x1, y1, key, fn in reversed(self._hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key if want_key else fn
        return None

    def _on_click(self, event):
        fn = self._at(event.x, event.y)
        if fn:
            fn()

    def hit(self, x0, y0, x1, y1, key, fn):
        self._hits.append((x0, y0, x1, y1, key, fn))

    def set(self, **kw):
        """Update state. Chrome-affecting keys force a full repaint."""
        chrome_keys = {"running", "view", "index", "indices", "toggles",
                       "mode", "expiry", "span"}
        heavy = any(k in chrome_keys for k in kw)
        self.state.update(kw)
        if heavy:
            self.repaint()
        else:
            self.paint_live()

    def repaint(self):
        self._resize_job = None
        self.paint_chrome()
        self.paint_live()

    # -- geometry ---------------------------------------------------------
    def geom(self):
        c = self.canvas
        W = c.winfo_width() or 1570
        H = c.winfo_height() or 1002
        g = {"W": W, "H": H}
        g["left"] = RAIL_W + PAD
        g["right"] = W - 20
        g["rail_w"] = RAIL_W
        rail_gap = 16
        g["conf_x0"] = max(g["left"] + 620, W - 341)
        g["conf_x1"] = g["right"]
        g["main_x1"] = g["conf_x0"] - rail_gap

        # Vertical rhythm scales with the window. The design height is 960; at
        # the 720 minimum every fixed gap has to give a little or the ticket
        # ends up drawn on top of the session bar.
        roomy = H >= 900
        tight = H < 800
        g["tabs_y"] = 152
        g["cards_y"] = 208
        g["cards_y1"] = g["cards_y"] + (138 if roomy else 118)
        g["trend_x1"] = g["left"] + max(300, min(392, int((g["main_x1"] - g["left"]) * 0.42)))
        g["day_x0"] = g["trend_x1"] + rail_gap

        g["view_y"] = g["cards_y1"] + (27 if roomy else 16)
        g["view_y1"] = g["view_y"] + 35
        g["panel_y"] = g["view_y1"] + 10

        session_h = 108 if roomy else (88 if not tight else 72)
        g["session_y1"] = H - (42 if roomy else 20)
        g["session_y"] = g["session_y1"] - session_h
        g["panel_y1"] = g["session_y"] - (24 if roomy else 14)
        g["conf_y1"] = g["panel_y1"]
        g["full"] = self.state.get("rail", "board") != "board"
        if g["full"]:
            # The map and the reports get the whole area the cards and the
            # confidence rail were using. A treemap squeezed into a third of the
            # width is unreadable, which is why it used to need its own window.
            g["panel_y"] = g["cards_y"]
            g["main_x1"] = g["right"]
        return g

    # ===================================================================
    # CHROME
    # ===================================================================
    def paint_chrome(self):
        c = self.canvas
        c.delete("chrome")
        self._hits = []
        self._zones = {}
        g = self.geom()
        s = self.state
        c.configure(bg=T.BG_APP)

        self._rail(g)
        self._top_rows(g)
        self._index_tabs(g)
        if not g["full"]:
            self._trend_card(g)
            self._day_card(g)
            self._conf_card(g)
            self._view_tabs(g)
        self._panel_card(g)
        self._session(g)

    # -- side rail --------------------------------------------------------
    def _rail(self, g):
        c = self.canvas
        H = g["H"]
        c.create_rectangle(0, 0, RAIL_W, H, fill=T.BG_RAIL, outline=T.BG_RAIL,
                           tags="chrome")
        c.create_line(RAIL_W, 0, RAIL_W, H, fill=T.BORDER, tags="chrome")

        # logo: a gradient ring with the initial inside
        cx, cy, r = 49, 51, 24
        for i in range(26):
            a = i * 360 / 26
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=a, extent=-14.5,
                         style="arc", width=3,
                         outline=K.ramp([T.ACC_A, T.ACC_B, T.ACC_C, T.ACC_A], i / 25),
                         tags="chrome")
        c.create_text(cx, cy + 1, text="R", fill=T.FG,
                      font=(T.family(), -20, "bold"), tags="chrome")

        items = [("pulse", "board"), ("dashboard", "map"), ("bars", "review"),
                 ("bell", "alerts"), ("gear", "settings")]
        # The pitch is SOLVED rather than fixed: fit the icons between the
        # logo and the theme toggle at the bottom, then cap the result so a 4K
        # window does not spread them apart. A fixed 88px was fine for five
        # icons at 900px tall and collided the moment either number moved.
        ICON_H = 26                       # half-height of one icon's hit box
        y = 168 if H >= 800 else 146      # centre of the first icon
        floor_y = H - 180 - 10            # the theme toggle's top, plus air
        span = max(0.0, floor_y - ICON_H - y)
        pitch = span / (len(items) - 1) if len(items) > 1 else 88
        pitch = max(56, min(88, pitch))
        for name, key in items:
            self._zone(("rail", key),
                       lambda n=name, k=key, yy=y: self._rail_item(n, k, yy))
            y += pitch

        self._zone(("rail", "theme"), lambda: self._rail_theme(H))
        self._zone(("rail", "quit"), lambda: self._rail_quit(H))

    def _rail_item(self, name, key, y):
        c = self.canvas
        # The bell is a switch, not a destination — it lights up when the
        # popup alerts are on rather than selecting a page.
        SWITCHES = {"alerts": "popup"}
        active = (self.state["toggles"].get(SWITCHES[key], False)
                  if key in SWITCHES
                  else key == self.state.get("rail", "board"))
        # A selected PAGE gets a filled tile; a switch that happens to be ON
        # gets a tinted glyph and a dot. Using one highlight for both would
        # say "you are here" about a checkbox.
        toggle = key in SWITCHES
        hovered = self._hover == ("rail", key)
        if active and not toggle:
            K.card(c, 25, y - 26, 73, y + 26, K.mix(T.BG_RAIL, T.ACC_B, 0.22),
                   K.mix(T.BG_RAIL, T.ACC_B, 0.45), 14, tags=self._tg)
        col = (T.ACC_A if (toggle and active)
               else T.FG if (active or hovered) else T.FG_MUTED)
        K.icon(c, name, 49, y, 23, col, tags=self._tg)
        if toggle and active:
            K.dot(c, 66, y - 13, 3, T.ACC_A, tags=self._tg)
        self.hit(25, y - 26, 73, y + 26, ("rail", key),
                 lambda k=key: (self.cb.get("on_toggle", lambda *_: None)(SWITCHES[k])
                                if k in SWITCHES
                                else self.cb.get("on_rail", lambda *_: None)(k)))

    def _rail_theme(self, H):
        c = self.canvas
        K.icon(c, "theme", 49, H - 154, 21,
               T.FG if self._hover == ("rail", "theme") else T.FG_MUTED,
               tags=self._tg)
        self.hit(25, H - 178, 73, H - 130, ("rail", "theme"),
                 lambda: self.cb.get("on_theme", lambda: None)())

    def _rail_quit(self, H):
        c = self.canvas
        K.icon(c, "logout", 49, H - 78, 23,
               T.FG if self._hover == ("rail", "quit") else T.FG_MUTED, tags=self._tg)
        self.hit(25, H - 104, 73, H - 52, ("rail", "quit"),
                 lambda: self.cb.get("on_quit", lambda: None)())

    # -- buttons ----------------------------------------------------------
    def _button(self, x, y, w, h, label, key, fn, kind="ghost", icon=None):
        return self._zone(key, lambda: self._button_draw(x, y, w, h, label, key, fn, kind, icon))

    def _button_draw(self, x, y, w, h, label, key, fn, kind="ghost", icon=None):
        c = self.canvas
        hov = self._hover == key
        x1, y1 = x + w, y + h
        if kind == "accent":
            a, b = (K.mix(T.ACC_A, "#ffffff", 0.12), K.mix(T.ACC_B, "#ffffff", 0.12)) \
                if hov else (T.ACC_A, T.ACC_B)
            K.pill(c, x, y, x1, y1, None, grad=(a, b), tags=self._tg)
            fg = T.FG_ON_ACCENT
        else:
            fill = T.BG_INPUT_HI if hov else T.BG_INPUT
            K.card(c, x, y, x1, y1, fill, T.BORDER_HI if hov else T.BORDER,
                   10, tags=self._tg)
            fg = T.FG
        tx = (x + x1) / 2
        if icon:
            K.icon(c, icon, x + 18, (y + y1) / 2, 15, fg, tags=self._tg)
            tx += 8
        c.create_text(tx, (y + y1) / 2 + 1, text=label, fill=fg,
                      font=(T.family(), -13, "bold" if kind == "accent" else "normal"),
                      tags=self._tg)
        self.hit(x, y, x1, y1, key, fn)
        return x1

    def _select(self, x, y, w, h, value, key, fn):
        return self._zone(key, lambda: self._select_draw(x, y, w, h, value, key, fn))

    def _select_draw(self, x, y, w, h, value, key, fn):
        c = self.canvas
        hov = self._hover == key
        K.card(c, x, y, x + w, y + h, T.BG_INPUT_HI if hov else T.BG_INPUT,
               T.BORDER_HI if hov else T.BORDER, 10, tags=self._tg)
        c.create_text(x + 14, y + h / 2 + 1, text=value, fill=T.FG, anchor="w",
                      font=(T.family(), -13), tags=self._tg)
        K.icon(c, "chevron", x + w - 18, y + h / 2, 14, T.FG_2, tags=self._tg)
        self.hit(x, y, x + w, y + h, key, fn)
        return x + w

    def _check(self, x, y, label, name):
        return self._zone(("chk", name),
                          lambda: self._check_draw(x, y, label, name))

    def _check_draw(self, x, y, label, name):
        c = self.canvas
        on = self.state["toggles"].get(name, False)
        key = ("chk", name)
        hov = self._hover == key
        box = 18
        cy = y + CTRL_H / 2
        if on:
            K.card(c, x, cy - box / 2, x + box, cy + box / 2, T.ACC_B,
                   K.mix(T.ACC_B, "#ffffff", 0.2), 5, tags=self._tg)
            K.icon(c, "check", x + box / 2, cy, 12, "#ffffff", tags=self._tg, width=2)
        else:
            K.card(c, x, cy - box / 2, x + box, cy + box / 2, T.BG_INPUT,
                   T.BORDER_HI if hov else T.BORDER, 5, tags=self._tg)
        c.create_text(x + box + 9, cy + 1, text=label, anchor="w",
                      fill=T.FG if hov else T.FG_2, font=(T.family(), -12),
                      tags=self._tg)
        w = box + 9 + _tw(label, 12)
        self.hit(x, y, x + w, y + CTRL_H, key,
                 lambda n=name: self.cb.get("on_toggle", lambda *_: None)(n))
        return x + w

    # -- top two rows -----------------------------------------------------
    def _top_rows(self, g):
        c = self.canvas
        s = self.state
        L = g["left"]

        c.create_text(L + 5, ROW1_Y + CTRL_H / 2 + 1, text="Mode:", anchor="w",
                      fill=T.FG_2, font=(T.family(), -13), tags="chrome")
        x = self._select(L + 60, ROW1_Y, 118, CTRL_H, s["mode"], ("sel", "mode"),
                         lambda: self.cb.get("on_mode", lambda: None)())
        x = self._button(x + 18, ROW1_Y - 1, 108, CTRL_H + 2, "Start", ("btn", "start"),
                         lambda: self.cb.get("on_start", lambda: None)(), "accent")
        ctrl_end = self._button(x + 10, ROW1_Y - 1, 80, CTRL_H + 2, "Stop",
                                ("btn", "stop"),
                                lambda: self.cb.get("on_stop", lambda: None)())

        # Lots lives on row 1, which has the spare width; row 2 is already
        # measuring its labels to avoid an overlap at 1180px.
        lots_l = "Lots:"
        c.create_text(ctrl_end + 26, ROW1_Y + CTRL_H / 2 + 1, text=lots_l,
                      anchor="w", fill=T.FG_2, font=(T.family(), -13), tags="chrome")
        ctrl_end = self._select(ctrl_end + 30 + _tw(lots_l, 13), ROW1_Y, 62, CTRL_H,
                                str(s.get("lots", "1")), ("sel", "lots"),
                                lambda: self.cb.get("on_lots", lambda: None)())

        # The daily brake, switchable without editing a file — the decision it
        # governs is one you may want to change at 14:00, not at 09:00.
        # The status line to its right carries the market-closed timestamp and
        # must keep its room, so the label shrinks before that gets eaten.
        STATUS_ROOM = 360
        for lim_l in ("Daily limits", "Limits"):
            if ctrl_end + 30 + 27 + _tw(lim_l, 12) + 40 < g["right"] - STATUS_ROOM:
                ctrl_end = self._check(ctrl_end + 30, ROW1_Y, lim_l, "limits")
                break

        # Pinned to the RIGHT edge and grown leftward. It used to be anchored
        # 96px in and drawn rightward, which meant anything longer than that
        # ran off the side of the window — "MARKET CLOSED · NIFTY · last data
        # 18 Aug 15:15" showed up as "MARKET CLOSED". If it still won't fit
        # beside the buttons, trailing "·" segments are dropped one at a time,
        # so the part that matters most survives.
        txt = s["status"]
        room = g["right"] - ctrl_end - 40
        while " · " in txt and _tw(txt, 13) > room:
            txt = txt.rsplit(" · ", 1)[0]
        tw = _tw(txt, 13)
        c.create_text(g["right"], ROW1_Y + CTRL_H / 2 + 1, text=txt,
                      anchor="e", fill=T.FG_2, font=(T.family(), -13), tags="chrome")
        K.dot(c, g["right"] - tw - 14, ROW1_Y + CTRL_H / 2, 4, s["status_col"],
              tags="chrome")

        c.create_text(L + 5, ROW2_Y + CTRL_H / 2 + 1, text="Expiry:", anchor="w",
                      fill=T.FG_2, font=(T.family(), -13), tags="chrome")
        # Pinned right, so a narrow window eats the gap in the middle rather
        # than pushing this off the edge.
        sw = _tw("Save summary", 13) + 30
        mm = g["right"] - sw
        self._button(mm, ROW2_Y, sw, CTRL_H, "Save summary", ("btn", "save"),
                     lambda: self.cb.get("on_save", lambda: None)())

        # Everything else is flowed left-to-right, and the labels shrink to fit
        # rather than sliding under the button above. Three sets, longest first;
        # the first one that measures short enough wins. Without this the row
        # looked right on a 1500px window and overlapped on a 1180px one, which
        # is inside the minimum size the app allows.
        SETS = [
            ("Load expiries", "Pop up on new signal / target / SL",
             "Auto re-arm after T3/SL", "Login to Zerodha",
             "Re-enter same direction (after 20m)"),
            ("Load expiries", "Pop up on signals", "Auto re-arm",
             "Login to Zerodha", "Re-enter same direction"),
            ("Expiries", "Pop ups", "Re-arm", "Login", "Re-enter same side"),
        ]
        start = L + 60 + 118
        limit = mm - 14

        def row_width(labels):
            load, popup, rearm, login, reentry = labels
            return (10 + _tw(load, 13) + 32
                    + 22 + 27 + _tw(popup, 12)
                    + 22 + 27 + _tw(rearm, 12)
                    + 22 + _tw(login, 13) + 34
                    + 32 + _tw(s["token"], 12)
                    + 14 + 27 + _tw(reentry, 12))

        labels = SETS[-1]
        for candidate in SETS:
            if start + row_width(candidate) <= limit:
                labels = candidate
                break
        load_l, popup_l, rearm_l, login_l, reentry_l = labels

        x = self._select(L + 60, ROW2_Y, 118, CTRL_H, s["expiry"], ("sel", "expiry"),
                         lambda: self.cb.get("on_expiry", lambda: None)())
        x = self._button(x + 10, ROW2_Y, _tw(load_l, 13) + 32, CTRL_H,
                         load_l, ("btn", "load"),
                         lambda: self.cb.get("on_load", lambda: None)())
        x = self._check(x + 22, ROW2_Y, popup_l, "popup")
        x = self._check(x + 22, ROW2_Y, rearm_l, "rearm")
        x = self._button(x + 22, ROW2_Y, _tw(login_l, 13) + 34, CTRL_H,
                         login_l, ("btn", "login"),
                         lambda: self.cb.get("on_login", lambda: None)())
        K.dot(c, x + 18, ROW2_Y + CTRL_H / 2, 4, s["token_col"], tags="chrome")
        c.create_text(x + 28, ROW2_Y + CTRL_H / 2 + 1, text=s["token"], anchor="w",
                      fill=s["token_col"], font=(T.family(), -12), tags="chrome")
        x = x + 32 + _tw(s["token"], 12)
        if x + 14 + 27 + _tw(reentry_l, 12) <= limit:
            self._check(x + 14, ROW2_Y, reentry_l, "reentry")

    # -- pill tab bars ----------------------------------------------------
    def _tabbar(self, x, y, h, items, active, key_prefix, fn, widths=None):
        c = self.canvas
        widths = widths or [max(96, _tw(t, 13, True) + 46) for t in items]
        total = sum(widths) + 8
        K.pill(c, x, y, x + total, y + h, T.BG_CARD, T.BORDER, tags="chrome")
        cx = x + 4
        for label, w in zip(items, widths):
            key = (key_prefix, label)
            self._zone(key, lambda l=label, k=key, X=cx, W=w:
                       self._tab(X, y, W, h, l, k, active, fn))
            cx += w
        return x + total

    def _tab(self, cx, y, w, h, label, key, active, fn):
        c = self.canvas
        if label == active:
            K.pill(c, cx, y + 4, cx + w, y + h - 4, None,
                   grad=(T.ACC_A, T.ACC_B), tags=self._tg)
            col = T.FG_ON_ACCENT
        else:
            col = T.FG if self._hover == key else T.FG_2
        mark = (self.state.get("tab_marks") or {}).get(label)
        shift = 8 if mark else 0
        c.create_text(cx + w / 2 + shift, y + h / 2 + 1, text=label, fill=col,
                      font=(T.family(), -13, "bold"), tags=self._tg)
        if mark:
            # Left of the label, never over it. The dot repeats what the
            # card already says in words when that index is selected, so it
            # is a shortcut rather than the only place the state is stated.
            K.dot(c, cx + 17, y + h / 2, 4, mark[0], tags=self._tg)
        self.hit(cx, y, cx + w, y + h, key, lambda l=label: fn(l))

    def _index_tabs(self, g):
        s = self.state
        self._tabbar(g["left"], g["tabs_y"], 40, s["indices"], s["index"], "idx",
                     lambda l: self.cb.get("on_index", lambda *_: None)(l),
                     widths=[max(126, _tw(t, 13, True) + 74) for t in s["indices"]])

    def _view_tabs(self, g):
        s = self.state
        names = ["Signal", "Chart"]
        self._tabbar(g["left"], g["view_y"], g["view_y1"] - g["view_y"],
                     names, s["view"], "view",
                     lambda l: self.cb.get("on_view", lambda *_: None)(l),
                     widths=[max(92, _tw(t, 13, True) + 54) for t in names])

    # -- the three top cards ----------------------------------------------
    def _trend_card(self, g):
        c = self.canvas
        s = self.state["trend"]
        x0, y0, x1, y1 = g["left"], g["cards_y"], g["trend_x1"], g["cards_y1"]
        tint = {"up": T.UP, "down": T.DOWN}.get(s["dir"], T.ACC_B)
        K.card(c, x0, y0, x1, y1, K.mix(T.BG_CARD, tint, 0.14), T.BORDER, 16,
               tags="chrome", fill2=T.BG_CARD)
        K.icon(c, {"up": "arrow_up", "down": "arrow_down"}.get(s["dir"], "arrow_flat"),
               x0 + 27, y0 + 37, 13, T.FG_2, tags="chrome", width=2)
        c.create_text(x0 + 45, y0 + 37, text="MARKET TREND", anchor="w", fill=T.FG_2,
                      font=(T.family(), -12, "bold"), tags="chrome")

    def _day_card(self, g):
        c = self.canvas
        x0, y0, x1, y1 = g["day_x0"], g["cards_y"], g["main_x1"], g["cards_y1"]
        K.card(c, x0, y0, x1, y1, T.BG_CARD_HI, T.BORDER, 16, tags="chrome",
               fill2=T.BG_CARD)
        # Work out where the chips start BEFORE laying out the text, so the
        # range can be dropped when it would collide rather than drawn under
        # them. Two overlapping numbers are worse than one missing one.
        chips = self.state.get("timeframes") or []
        n = max(1, len(chips))
        # Leave room for the title, then give the chips whatever is left, down
        # to a floor. Below that they would be narrower than their own labels.
        title_room = 150
        chip_w = int(max(62, min(96, ((x1 - x0) - 24 - title_room) / n - 8)))
        self._chips_x0 = x1 - 24 - n * (chip_w + 8) + 8

        c.create_text(x0 + 33, y0 + 37, text="DAY MOVE", anchor="w", fill=T.FG_2,
                      font=(T.family(), -12, "bold"), tags="chrome")
        text, col, rng = (list(self.state.get("day") or (None, None, "")) + [None] * 3)[:3]
        tx = x0 + 33 + _tw("DAY MOVE", 12, True) + 18
        ty = y0 + 37
        if text:
            if tx + _tw(str(text), 14, True) > self._chips_x0 - 16:
                # Not enough room beside the title — drop BELOW the chips (they
                # end at y0+68), not merely to the next text line, which was the
                # first attempt and simply overlapped them lower down.
                tx, ty = x0 + 33, y0 + 86
            c.create_text(tx, ty, text=text, anchor="w", fill=col or T.FG,
                          font=(T.family(), -14, "bold"), tags="chrome")
            tx += _tw(str(text), 14, True) + 18
        if rng and tx + _tw(str(rng), 11) < self._chips_x0 - 16:
            c.create_text(tx, ty + 1, text=rng, anchor="w", fill=T.FG_MUTED,
                          font=(T.family(), -11), tags="chrome")
        # The three chips report what each timeframe says — they are readings,
        # not a range picker. Colouring the chip by its own verdict means the
        # card answers "do the timeframes agree?" at a glance.
        w = chip_w
        sx = self._chips_x0
        short = {"1 hour": "1h", "VWAP": "VW", "15m": "15m"}
        for label, reading, col in chips:
            if chip_w < 84:
                label = short.get(label, label)
            decided = (reading or "").strip() not in ("", "–", "-", "—")
            K.card(c, sx, y0 + 34, sx + w, y0 + 68,
                   K.mix(T.BG_INPUT, col, 0.22) if decided else T.BG_INPUT,
                   K.mix(T.BORDER, col, 0.5) if decided else T.BORDER, 12,
                   tags="chrome")
            c.create_text(sx + 10, y0 + 51, text=label, anchor="w", fill=T.FG_2,
                          font=(T.family(), -12), tags="chrome")
            c.create_text(sx + w - 10, y0 + 51, text=(reading or "–"), anchor="e",
                          fill=col if decided else T.FG_MUTED,
                          font=(T.family(), -13, "bold"), tags="chrome")
            sx += w + 8

    def _conf_card(self, g):
        c = self.canvas
        x0, y0, x1, y1 = g["conf_x0"], g["cards_y"], g["conf_x1"], g["conf_y1"]
        K.card(c, x0, y0, x1, y1, T.BG_CARD_HI, T.BORDER, 16, tags="chrome",
               fill2=T.BG_CARD)
        c.create_text(x0 + 30, y0 + 35, text="CONFIDENCE", anchor="w", fill=T.FG_2,
                      font=(T.family(), -12, "bold"), tags="chrome")

    # -- main panel and session -------------------------------------------
    def _panel_card(self, g):
        c = self.canvas
        K.card(c, g["left"], g["panel_y"], g["main_x1"], g["panel_y1"],
               T.BG_CARD_HI, T.BORDER, 16, tags="chrome", fill2=T.BG_CARD)
        if g["full"]:
            titles = {"map": "MARKET MAP", "review": "YOUR TRADES",
                      "settings": "SETTINGS"}
            c.create_text(g["left"] + 26, g["panel_y"] + 31,
                          text=titles.get(self.state["rail"], ""), anchor="w",
                          fill=T.FG_2, font=(T.family(), -12, "bold"), tags="chrome")
            return
        if self.state["view"] == "Signal":
            c.create_text(g["left"] + 26, g["panel_y"] + 31, text="SIGNAL TICKET",
                          anchor="w", fill=T.FG_2, font=(T.family(), -12, "bold"),
                          tags="chrome")
            self._button(g["main_x1"] - 108, g["panel_y"] + 16, 82, 34, "Clear",
                         ("btn", "clear"),
                         lambda: self.cb.get("on_clear", lambda: None)())

    def _session(self, g):
        c = self.canvas
        x0, y0, x1, y1 = g["left"], g["session_y"], g["right"], g["session_y1"]
        K.card(c, x0, y0, x1, y1, T.BG_CARD_HI, T.BORDER, 16, tags="chrome",
               fill2=T.BG_CARD)
        K.card(c, x0 + 20, y0 + 18, x0 + 56, y0 + 54, T.BG_INPUT, T.BORDER, 10,
               tags="chrome")
        K.icon(c, "calendar", x0 + 38, y0 + 36, 17, T.ACC_A, tags="chrome")
        c.create_text(x0 + 72, y0 + 36, text="SESSION", anchor="w", fill=T.FG_2,
                      font=(T.family(), -12, "bold"), tags="chrome")
        K.card(c, x0 + 180, y0 + 16, max(x0 + 420, x1 - 76 - PF_W), y1 - 16,
               T.BG_APP, T.BORDER, 12, tags="chrome")
        self._zone(("btn", "log"), lambda: self._log_btn(x1, y0))

    def _log_btn(self, x1, y0):
        c = self.canvas
        K.card(c, x1 - 62, y0 + 18, x1 - 20, y0 + 60, T.BG_INPUT,
               T.BORDER_HI if self._hover == ("btn", "log") else T.BORDER, 10,
               tags=self._tg)
        K.icon(c, "list", x1 - 41, y0 + 39, 17, T.FG_2, tags=self._tg)
        self.hit(x1 - 62, y0 + 18, x1 - 20, y0 + 60, ("btn", "log"),
                 lambda: self.cb.get("on_log", lambda: None)())

    # ===================================================================
    # LIVE
    # ===================================================================
    def paint_live(self):
        c = self.canvas
        c.delete("live")
        g = self.geom()
        rail = self.state.get("rail", "board")
        if rail == "map":
            self._live_map(g)
        elif rail in ("review", "settings"):
            self._live_page(g)
        else:
            self._live_trend(g)
            self._live_spark(g)
            self._live_conf(g)
            if self.state["view"] == "Signal":
                self._live_ticket(g)
            else:
                self._live_chart(g)
        self._live_session(g)

    def _live_trend(self, g):
        c = self.canvas
        s = self.state["trend"]
        x0, y0 = g["left"], g["cards_y"]
        col = {"up": T.UP, "down": T.DOWN}.get(s["dir"], T.FG_2)
        r = 27
        cx, cy = x0 + 47, y0 + 92
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=K.mix(T.BG_CARD, col, 0.20),
                      outline=K.mix(T.BG_CARD, col, 0.40), tags="live")
        K.icon(c, {"up": "arrow_up", "down": "arrow_down"}.get(s["dir"], "arrow_flat"),
               cx, cy, 24, col, tags="live", width=3)
        room = g["trend_x1"] - (x0 + 86) - 16
        size = 25
        while size > 15 and _tw(s["label"], size, True) > room:
            size -= 1
        c.create_text(x0 + 86, y0 + 76, text=s["label"], anchor="w", fill=col,
                      font=(T.family(), -size, "bold"), tags="live")
        c.create_text(x0 + 86, y0 + 104, text=s["sub"], anchor="w", fill=T.FG_2,
                      font=(T.family(), -12), width=int(room), justify="left",
                      tags="live")

    def _live_spark(self, g):
        c = self.canvas
        vals = self.state["spark"]
        x0, y0 = g["day_x0"] + 33, g["cards_y"] + 62
        x1 = getattr(self, "_chips_x0", g["main_x1"] - 330) - 22
        y1 = g["cards_y1"] - 20
        if x1 - x0 < 120 or y1 - y0 < 26:
            # Not enough room to be a chart rather than a smudge. The number and
            # the three chips above still say what the sparkline would have.
            return
        if len(vals) < 2:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="no data yet",
                          fill=T.FG_MUTED, font=(T.family(), -12), tags="live")
            return
        up = vals[-1] >= vals[0]
        col = T.UP if up else T.DOWN
        K.sparkline(c, x0, y0, x1, y1, vals, col, tags="live",
                    fill_to=(K.mix(T.BG_CARD, col, 0.28), T.BG_CARD))
        c.create_line(x0, y1 + 6, x1, y1 + 6, fill=T.BORDER, tags="live", dash=(2, 4))

    def _live_conf(self, g):
        c = self.canvas
        s = self.state["confidence"]
        x0, x1 = g["conf_x0"], g["conf_x1"]
        cx = (x0 + x1) / 2
        span = g["conf_y1"] - g["cards_y"]
        rad = 66 if span > 560 else (54 if span > 470 else 44)
        cy = g["cards_y"] + rad + 56
        pct = s.get("pct")
        K.ring(c, cx, cy, rad, max(9, int(rad * 0.2)), (pct or 0) / 100.0,
               [T.ACC_A, T.ACC_B, T.ACC_C], T.BG_INPUT, tags="live")
        c.create_text(cx, cy - 2, text="—" if pct is None else f"{pct:.0f}%",
                      fill=T.FG, font=(T.family(), -int(rad * 0.5), "bold"), tags="live")
        c.create_text(cx, cy + rad + 28, text=s.get("label", ""), fill=T.FG_2,
                      font=(T.family(), -13), tags="live")

        y = cy + rad + 76
        rows = s.get("rows", [])
        avail = g["conf_y1"] - 44 - y
        step = min(48, max(20, avail / max(1, len(rows))))
        # Each vote gets a bar as well as a word. The list used to be text
        # only, so "Bullish +30.05" and "Bullish 60" looked equally emphatic
        # when one is a strong reading and the other is barely off neutral.
        # The bar carries the STRENGTH; the word and the number carry the
        # direction, so nothing is encoded in length alone either.
        bars = step >= 34 and (x1 - x0) > 250
        for row in rows:
            name, value, col = row[0], row[1], row[2]
            frac = row[3] if len(row) > 3 else None
            c.create_line(x0 + 28, y - step / 2 + 2, x1 - 28, y - step / 2 + 2,
                          fill=T.BORDER, tags="live")
            vw = _tw(str(value), 13, True)
            c.create_text(x0 + 30, y - (9 if bars else 0), text=name, anchor="w",
                          fill=T.FG_2, font=(T.family(), -13), tags="live")
            c.create_text(x1 - 30, y - (9 if bars else 0), text=value, anchor="e",
                          fill=col, font=(T.family(), -13, "bold"), tags="live")
            if bars and frac is not None:
                bx0, bx1 = x0 + 30, x1 - 30
                by = y + 9
                K.pill(c, bx0, by, bx1, by + 5, T.BG_INPUT_HI, tags="live")
                p = max(0.04, min(1.0, abs(frac)))
                K.pill(c, bx0, by, bx0 + (bx1 - bx0) * p, by + 5,
                       col if col != T.FG_MUTED else T.NEUTRAL, tags="live")
            y += step
        c.create_text(cx, g["conf_y1"] - 20, text="— = undecided (ignored)",
                      fill=T.FG_MUTED, font=(T.family(), -11), tags="live")

    def _live_ticket(self, g):
        c = self.canvas
        t = self.state["ticket"]
        x0, x1 = g["left"], g["main_x1"]
        y = g["panel_y"]

        hsize = 34
        avail0 = g["panel_y1"] - y
        if avail0 < 330:
            hsize = 26
        # The headline is the longest string on the screen; shrink it until it
        # clears the badge rather than letting it slide under it.
        while hsize > 16 and _tw(t["headline"], hsize, True) > (x1 - 240) - (x0 + 26):
            hsize -= 1
        bw = _tw(t["badge"], 12, True) + 34
        K.card(c, x1 - 200 - bw, y + 16, x1 - 200, y + 50,
               K.mix(T.BG_CARD, t["badge_col"], 0.22),
               K.mix(T.BG_CARD, t["badge_col"], 0.5), 10, tags="live")
        c.create_text(x1 - 200 - bw / 2, y + 33, text=t["badge"],
                      fill=T.emphasis(t["badge_col"], 0.35),
                      font=(T.family(), -12, "bold"), tags="live")

        avail = g["panel_y1"] - y
        tight = avail < 330
        ly1 = g["panel_y1"] - (24 if not tight else 14)

        # ---- vertical plan, made BEFORE anything is drawn -----------------
        # The cards are the reason this panel exists, so they are measured
        # out first and the text block above adapts to what is left. Laying
        # it out the other way round — text first, cards in the remainder —
        # is what let the band collapse to zero and then to a NEGATIVE
        # height on a short window, which Tk draws as an inside-out rectangle
        # you simply cannot see. The targets did not move; they stopped
        # existing.
        band = (172 if avail >= 430 else 130 if avail >= 350
                else 96 if avail >= 288 else 62)
        ly = ly1 - band

        # Text block, laid out downward, compressed until it clears the cards.
        # Order of sacrifice: the gaps first, then the sub-line. Never the
        # cards, and never the four numbers.
        plans = [(78 if not tight else 62, 42 if not tight else 32,
                  82 if not tight else 62, True),
                 (54, 30, 58, True),
                 (46, 26, 44, False)]
        for gap_head, gap_sub, gap_stat, show_sub in plans:
            head_y = y + gap_head
            sub_y = head_y + gap_sub
            sy = head_y + gap_stat
            if sy + 30 <= ly - 8:
                break
        # Now that the text block is placed, sit the cards directly beneath it
        # rather than leaving them pinned to the floor. On a full-screen window
        # the old version left a 100px hole between the entry price and the
        # targets that price is heading for — two things that belong together.
        top = sy + (40 if tight else 62)
        if ly1 - top >= 62:
            # There is room to put them under the stats without squashing them.
            if t.get("ladder") or ly1 - top <= 300:
                # A ladder takes everything: its whole point is the vertical
                # distance between levels, and capping the height compresses
                # exactly the information it exists to show. Cards get the cap,
                # because a 400px-deep card is mostly empty card.
                ly = top
            else:
                ly, ly1 = top, top + 300  # very tall: pad below, not between
        # Otherwise keep the band reserved above — on a short window the cards
        # keep their floor and the text block is what gives way.
        if sy + 30 > ly - 8:
            # Shorter than this panel can honestly serve. Give the cards a
            # 44px strip and let them be cramped rather than invisible.
            ly = min(ly1 - 44, max(sy + 34, ly))
        # With a ladder, the headline and the numbers share one band instead of
        # stacking. The ladder is the content here and every row of text above
        # it is a row of height taken off the thing that needs it — the vertical
        # distance between levels IS the information.
        side_stats = bool(t.get("ladder")) and (x1 - x0) > 860 and avail >= 260
        col_w = 0
        if side_stats:
            # A fixed column for the headline and its sub-line. Sizing it to
            # the headline's own width made the column 156px wide and wrapped
            # "tracked on live premium" over five lines.
            col_w = int(max(260, min(340, (x1 - x0) * 0.30)))
            head_y = y + 62
            sub_y = head_y + hsize // 2 + 20
            show_sub = True
            sy = y + 46
            # However many lines the sub-line actually wraps to.
            sub_w = col_w - 24
            lines = max(1, min(4, -(-_tw(t["sub"], 13) // max(60, sub_w))))
            ly = max(head_y + 58, sub_y + lines * 18 + 14)
            ly1 = g["panel_y1"] - (24 if not tight else 14)

        if sy + 30 > ly - 8:
            # Still colliding — below the 720px minimum the window is supposed
            # to enforce. Pull the text block UP into the headline's space
            # instead of letting the numbers sit on top of the cards; the
            # headline shrinks and the sub-line goes, both of which repeat
            # information that is elsewhere on the screen.
            sy = ly - 40
            sub_y = sy - 24
            head_y = max(y + 40, sub_y - 26)
            show_sub = sub_y > head_y + 14
        self._levels_box = (ly, ly1)

        # ---- now draw it --------------------------------------------------
        # Needed by the sub-line below, which must not run under the numbers.
        sx = x0 + 26
        if side_stats:
            sx = x0 + 26 + col_w
        if t.get("accent"):
            # A gradient headline: the same text stamped per-letter along the
            # ramp. Tk gives one colour per item, so this is the only way.
            _grad_text(c, x0 + 26, head_y, t["headline"], (T.family(), -hsize, "bold"),
                       [T.ACC_A, T.ACC_B], "live")
        else:
            c.create_text(x0 + 26, head_y, text=t["headline"], anchor="w",
                          fill=t.get("headline_col", T.FG),
                          font=(T.family(), -hsize, "bold"), tags="live")
        if show_sub:
            # When the numbers sit beside the headline, the sub-line has to
            # wrap inside the headline's column instead of running the full
            # width of the panel — otherwise it is drawn straight through them.
            sub_w = int(col_w - 24) if side_stats else int(x1 - x0 - 52)
            # anchor "nw", not "w": a multi-line block anchored west is
            # centred on that point, so every extra line pushed the first one
            # UP — a three-line "Waiting because…" climbed into the headline.
            # Anchored north-west it grows downward, which is the only
            # direction there is room in.
            c.create_text(x0 + 26, sub_y - 8, text=t["sub"],
                          anchor="nw", fill=T.FG_2, font=(T.family(), -13),
                          width=sub_w, tags="live")

        # Reward:risk goes FIRST, not last. It is the number that decides
        # whether a setup is worth taking at all, and when the row ran out of
        # width it was the one that got dropped — the most useful number on
        # the ticket, sacrificed for the spot price, which is also printed in
        # the DAY MOVE card two inches above.
        cells = list(t["stats"])
        if side_stats:
            # Priority order, because the row will run out of width and
            # something has to go: what the trade is worth beats what the index
            # is at. SPOT is the one that drops — it is already printed in the
            # DAY MOVE card two inches above this one.
            rank = {"REWARD : RISK": 0, "ENTRY": 1, "NOW": 2, "SPOT": 9}
            cells.sort(key=lambda c: rank.get(c[0], 3))
        rr = ((t.get("ladder") or {}).get("rr")) or None
        if rr:
            ratio = rr["ratio"]
            rcol = T.UP if ratio >= 1.5 else (T.WARN if ratio >= 1.0 else T.DOWN)
            cells.insert(0, ("REWARD : RISK", f"{ratio:g} : 1", rcol))

        # The badge sits at the right end of this band; stop before it.
        limit = (x1 - 200 - (_tw(t["badge"], 12, True) + 34) - 24) if side_stats \
            else (x1 - 26)
        for label, value, col in cells:
            # A fixed pitch was fine while every column read "—" and wrong the
            # moment SPOT held 24,963.29.
            pad = 28 if side_stats else 34
            wcell = max(88 if side_stats else 96,
                        _tw(str(value), 17, True) + pad,
                        _tw(str(label), 11, True) + pad)
            if side_stats and sx + wcell > limit:
                break
            c.create_text(sx, sy, text=label, anchor="w", fill=T.FG_2,
                          font=(T.family(), -11, "bold"), tags="live")
            c.create_text(sx, sy + 28, text=value, anchor="w", fill=col,
                          font=(T.family(), -17, "bold"), tags="live")
            sx += wcell
        if rr and side_stats and sx + _tw("000 pts to T3  ·  000 pts to stop", 11) < limit:
            c.create_text(sx, sy + 29,
                          text=f"{rr['reward']:,.0f} pts to T3  ·  "
                               f"{rr['risk']:,.0f} pts to stop",
                          anchor="w", fill=T.FG_MUTED,
                          font=(T.family(), -11), tags="live")
        # Four tags need ~32px each plus air. Below that the ladder cannot show
        # the thing it exists to show — the vertical distance between levels —
        # and its tags climb out of the band and into the numbers above. The
        # cards degrade gracefully at that size; the ladder does not, so on a
        # short window the cards are simply the better answer.
        need = 96 if (t.get("ladder") or {}).get("empty") else 138
        if t.get("ladder") and ly1 - ly >= need:
            self._ladder(g, x0 + 26, x1 - 26, ly, ly1, t["ladder"])
            return

        levels = t["levels"]
        n = len(levels)
        gapx = 12
        cw = ((x1 - 26) - (x0 + 26) - gapx * (n - 1)) / n
        lx = x0 + 26
        for item in levels:
            name, value, col = item[0], item[1], item[2]
            progress = item[3] if len(item) > 3 else None
            mark = item[4] if len(item) > 4 else None
            # A level that has been reached is tinted harder and outlined in
            # its own colour, so "this one is done" is visible from across the
            # room, before you read a single number.
            K.card(c, lx, ly, lx + cw, ly1,
                   K.mix(T.BG_CARD, col, 0.26 if mark else 0.13),
                   col if mark else T.BORDER, 14, tags="live", fill2=T.BG_CARD)
            # The 3px cap is the level's identity; the big label beside it is the
            # second encoding, so the colour is never doing the job alone.
            c.create_line(lx + 14, ly + 1, lx + cw - 14, ly + 1, fill=col, width=3,
                          tags="live")
            ch = ly1 - ly
            if ch < 74:
                # Short window: name and value share a line, inside the card.
                # Only the tick survives here — there is no room for a
                # timestamp, and the tick is the part that answers "did it
                # hit?". The exact minute is still in the session log.
                short = name + "  ✓" if mark else name
                c.create_text(lx + 18, (ly + ly1) / 2 + 1, text=short, anchor="w",
                              fill=col if mark else T.FG,
                              font=(T.family(), -15, "bold"), tags="live")
                # Name and value share this line, so the value has to be told
                # how much room is actually left. Without this the level price
                # was drawn straight through the "T2" beside it.
                room = cw - 36 - _tw(short, 15, True) - 10
                val, vsize = value, 14
                if _tw(val, vsize) > room and " · " in val:
                    # Drop the rupee figure before the price: on a card this
                    # short the level itself is the part you cannot infer.
                    val = val.split(" · ")[0]
                while vsize > 10 and _tw(val, vsize) > room:
                    vsize -= 1
                if room > 24:
                    c.create_text(lx + cw - 18, (ly + ly1) / 2 + 1, text=val,
                                  anchor="e", fill=T.FG_2,
                                  font=(T.family(), -vsize), tags="live")
            else:
                c.create_text(lx + 22, ly + 34, text=name, anchor="w",
                              fill=col if mark else T.FG,
                              font=(T.family(), -17, "bold"), tags="live")
                if mark:
                    mx = lx + 22 + _tw(name, 17, True) + 10
                    # If the time will not fit beside the name, keep the tick
                    # and drop the clock rather than letting it run off the card.
                    txt = mark if mx + _tw(mark, 12, True) < lx + cw - 14 else "✓"
                    c.create_text(mx, ly + 35, text=txt, anchor="w", fill=col,
                                  font=(T.family(), -12, "bold"), tags="live")
                # Same trimming the short card already does: the value is
                # "214.0 · +Rs.6,870" and the card is not always wide enough
                # for both halves. The level price is the half you cannot
                # work out from anything else on screen, so it is the half
                # that stays.
                room = cw - 44
                val, vsize = value, 15
                if _tw(val, vsize) > room and " · " in val:
                    val = val.split(" · ")[0]
                while vsize > 11 and _tw(val, vsize) > room:
                    vsize -= 1
                c.create_text(lx + 22, ly + 64, text=val, anchor="w", fill=T.FG_2,
                              font=(T.family(), -vsize), tags="live")
            if progress is not None and ch > 104:
                # How far price has travelled from entry towards this level. The
                # bar is the same colour as the cap above it, so the eye reads
                # card and bar as one thing.
                bx0, bx1 = lx + 22, lx + cw - 22
                by = ly1 - 34
                K.pill(c, bx0, by, bx1, by + 7, T.BG_INPUT, tags="live")
                p = max(0.0, min(1.0, progress))
                if p > 0.02:
                    K.pill(c, bx0, by, bx0 + (bx1 - bx0) * p, by + 7, col, tags="live")
                c.create_text(bx0, by + 24, anchor="w",
                              text="reached" if p >= 1 else f"{p * 100:.0f}% of the way",
                              fill=col if p >= 1 else T.FG_MUTED,
                              font=(T.family(), -11, "bold" if p >= 1 else "normal"),
                              tags="live")
            lx += cw + gapx

    def _ladder(self, g, x0, x1, y0, y1, d):
        """The levels on a real price axis.

        Four equal-width cards say T1, T2, T3 and the stop exist. They cannot
        say that T1 is a third of the way to T3, or that the stop is nearer
        than any target — the two facts that decide whether a trade is worth
        sitting in. Here every level is at its true distance, so the shape of
        the trade is readable before a single number is.
        """
        c = self.canvas
        rows = d["levels"]
        entry, now = d.get("entry"), d.get("now")
        if d.get("empty"):
            self._ladder_empty(x0, x1, y0, y1, rows)
            return

        # The axis has to contain every level AND the live price, or the
        # marker slides off the end exactly when the trade gets interesting.
        pts = [r["price"] for r in rows]
        for v in (entry, now):
            if v is not None:
                pts.append(float(v))
        lo, hi = min(pts), max(pts)
        if hi - lo < 1e-9:
            return
        pad = (hi - lo) * 0.10
        lo, hi = lo - pad, hi + pad

        h = y1 - y0
        top, bot = y0 + 20, y1 - 20
        if bot - top < 40:
            top, bot = y0 + 6, y1 - 6

        def yp(p):
            return bot - (float(p) - lo) / (hi - lo) * (bot - top)

        # room: marker gutter on the left, tags on the right
        tagw = max(_tw(f"{r['name']}  {r['price']:,.2f}", 13, True) + 28
                   for r in rows)
        moneyw = max([_tw(r["money"], 11) for r in rows if r["money"]] or [0])
        gutter = 104 if now is not None else 22
        ax = x0 + gutter
        right = x1 - (moneyw + 14 if moneyw else 0)
        tag_x = right - tagw
        if tag_x - ax < 120:                       # narrow window: drop the money
            right, moneyw = x1, 0
            tag_x = right - tagw

        c.create_line(ax, top - 12, ax, bot + 12, fill=T.BORDER)

        # the ground covered so far, entry -> now
        if entry is not None and now is not None:
            ye, yn = yp(entry), yp(now)
            won = (now - entry) >= 0
            band = T.UP if won else T.DOWN
            if abs(ye - yn) > 1:
                K.gradient(c, ax + 1, min(ye, yn), tag_x - 8, max(ye, yn),
                           K.mix(T.BG_CARD, band, 0.30),
                           K.mix(T.BG_CARD, band, 0.05), tags="live")

        # entry, drawn first so a level line can sit on top of it
        if entry is not None:
            ye = yp(entry)
            for xx in range(int(ax), int(tag_x - 8), 7):
                c.create_line(xx, ye, xx + 3, ye, fill=T.FG_MUTED, tags="live")
            c.create_text(ax + 8, ye - 11, text=f"entry {entry:,.2f}", anchor="w",
                          fill=T.FG_MUTED, font=(T.family(), -10), tags="live")

        # Two levels can sit a rupee apart, and their tags are 30px tall. The
        # LINE stays at the true price — that is the whole point — but the tags
        # are pushed apart so both stay readable, with a leader joining each
        # tag back to its line when it has been moved.
        ys = [yp(r["price"]) for r in rows]
        ty = list(ys)
        for i in range(1, len(ty)):
            if ty[i] - ty[i - 1] < 32:
                ty[i] = ty[i - 1] + 32
        overflow = ty[-1] - (bot + 14) if ty else 0
        if overflow > 0:
            ty = [v - overflow for v in ty]
        # ...but never so far up that they leave the band. The routing above
        # guarantees the room; this is the belt to that pair of braces.
        lift = (top - 14) - ty[0] if ty else 0
        if lift > 0:
            ty = [v + lift for v in ty]

        entry_y = yp(entry) if entry is not None else None
        for r, y, yt in zip(rows, ys, ty):
            col = r["colour"]
            c.create_line(ax, y, tag_x - 8, y, fill=col,
                          width=2 if r["hit"] else 1, tags="live")
            if abs(yt - y) > 2:
                c.create_line(tag_x - 8, y, tag_x - 2, yt, fill=col, tags="live")
            tag = f"{r['name']}  {r['price']:,.2f}"
            K.card(c, tag_x, yt - 15, tag_x + tagw, yt + 15,
                   K.mix(T.BG_CARD, col, 0.30 if r["hit"] else 0.16), col, 8,
                   tags="live")
            c.create_text(tag_x + 14, yt + 1,
                          text=tag + ("  ✓" if r["hit"] else ""), anchor="w",
                          fill=T.emphasis(col) if r["hit"] else T.FG,
                          font=(T.family(), -13, "bold"), tags="live")
            if moneyw and r["money"]:
                c.create_text(x1, yt + 1, text=r["money"], anchor="e",
                              fill=T.FG_MUTED, font=(T.family(), -11), tags="live")
            # How far away it is from here — the sum you should not have to do.
            # Skipped when the entry line is within a few pixels, which is the
            # one place two of these labels used to be drawn on top of another.
            if entry_y is not None and abs(y - entry_y) < 14:
                continue
            if r["when"]:
                # Already reached: WHEN it happened is the useful fact. How far
                # away it is now is a distance to somewhere price has been.
                c.create_text(ax + 8, y - 11, text="✓ " + r["when"], anchor="w",
                              fill=col, font=(T.family(), -10, "bold"), tags="live")
            elif now is not None:
                c.create_text(ax + 8, y - 11, text=f"{r['price'] - now:+,.2f}",
                              anchor="w", fill=T.FG_MUTED,
                              font=(T.family(), -10), tags="live")

        # where price is right now
        if now is not None:
            yn = yp(now)
            lab = f"{now:,.2f}"
            w = _tw(lab, 13, True) + 30
            K.pill(c, ax - w - 10, yn - 15, ax - 10, yn + 15, None,
                   grad=(T.ACC_A, T.ACC_B), tags="live")
            c.create_text(ax - 10 - w / 2, yn + 1, text=lab, fill=T.FG_ON_ACCENT,
                          font=(T.family(), -13, "bold"), tags="live")
            c.create_polygon(ax - 10, yn - 7, ax, yn, ax - 10, yn + 7,
                             fill=T.ACC_B, outline=T.ACC_B, tags="live")

    def _ladder_empty(self, x0, x1, y0, y1, rows):
        """The ladder with nothing on it yet.

        Deliberately the same shape as the real thing: the panel keeps its
        layout whether or not the market is offering a trade this minute.
        Falling back to a different design every time a signal came and went
        made the window look like it was flickering between two apps.
        """
        c = self.canvas
        top, bot = y0 + 20, y1 - 20
        if bot - top < 40:
            top, bot = y0 + 6, y1 - 6
        tagw = max(_tw(f"{r['name']}  ——", 13, True) + 28 for r in rows)
        ax = x0 + 22
        tag_x = x1 - tagw
        c.create_line(ax, top - 12, ax, bot + 12, fill=T.BORDER, tags="live")
        n = len(rows)
        for i, r in enumerate(rows):
            y = top + (bot - top) * (i / max(1, n - 1))
            col = K.mix(T.BG_CARD, r["colour"], 0.55)
            for xx in range(int(ax), int(tag_x - 8), 9):
                c.create_line(xx, y, xx + 4, y, fill=col, tags="live")
            K.card(c, tag_x, y - 15, tag_x + tagw, y + 15, T.BG_CARD,
                   K.mix(T.BORDER, r["colour"], 0.5), 8, tags="live")
            c.create_text(tag_x + 14, y + 1, text=r["name"], anchor="w",
                          fill=T.FG_2, font=(T.family(), -13, "bold"), tags="live")
            c.create_text(tag_x + tagw - 14, y + 1, text="—", anchor="e",
                          fill=T.FG_MUTED, font=(T.family(), -13), tags="live")
        c.create_text(ax + 12, (top + bot) / 2,
                      text="Levels appear here the moment a ticket is issued.",
                      anchor="w", fill=T.FG_MUTED, font=(T.family(), -12),
                      tags="live")

    def _live_chart(self, g):
        c = self.canvas
        payload = self.state.get("chart")
        x0, y0 = g["left"] + 14, g["panel_y"] + 14
        x1, y1 = g["main_x1"] - 14, g["panel_y1"] - 14
        if not payload:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                          text="No chart yet — press Start.", fill=T.FG_MUTED,
                          font=(T.family(), -14), tags="live")
            return
        df, rec, trade = payload
        import chart_panel
        why = (trade or {}).get("why") or self.state.get("why")
        strip = 0
        if why and why.get("votes"):
            strip = 84
        ch = int(y1 - y0 - strip)
        proxy = OffsetCanvas(c, x0, y0, int(x1 - x0), ch, "live")
        chart_panel.draw_chart(proxy, int(x1 - x0), ch, df, rec, trade)
        if not strip:
            return
        # Why this signal, in words, right under the picture it refers to.
        wy = y0 + ch + 12
        c.create_line(x0, wy - 6, x1, wy - 6, fill=T.BORDER, tags="live")
        c.create_text(x0 + 2, wy + 8, anchor="w", text=why.get("headline", ""),
                      fill=T.FG, font=(T.family(), -13, "bold"), tags="live")
        vy = wy + 30
        for v in why["votes"][:3]:
            # explain() returns a dict per indicator; str() on it dumps the repr,
            # which is what a first draft of this did and it looked like a crash.
            if isinstance(v, dict):
                txt = v.get("text") or " ".join(
                    str(v.get(k, "")) for k in ("name", "reading")).strip()
            else:
                txt = str(v)
            c.create_text(x0 + 2, vy, anchor="w",
                          text="\u2022  " + _ellipsis(txt, 12, x1 - x0 - 24),
                          fill=T.FG_2, font=(T.family(), -12), tags="live")
            vy += 19

    def _live_map(self, g):
        c = self.canvas
        payload = self.state.get("map")
        x0, y0 = g["left"] + 20, g["panel_y"] + 52
        x1, y1 = g["main_x1"] - 20, g["panel_y1"] - 20
        if not payload:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, font=(T.family(), -14),
                          text="The map needs the live feed — press Start in kite mode.",
                          fill=T.FG_MUTED, tags="live")
            return
        rows, dead, btext, bcol = payload
        c.create_text(x1, g["panel_y"] + 31, text=btext or "", anchor="e",
                      fill=bcol or T.FG_2, font=(T.family(), -13, "bold"), tags="live")
        if dead:
            c.create_text(x0, g["panel_y"] + 31, anchor="w",
                          text="FEED NOT LIVE — these tiles are frozen",
                          fill=T.DOWN, font=(T.family(), -12, "bold"), tags="live")
        import market_map
        proxy = OffsetCanvas(c, x0, y0, int(x1 - x0), int(y1 - y0), "live")
        market_map.draw_map(proxy, int(x1 - x0), int(y1 - y0), rows, dead=dead)

    def _live_page(self, g):
        c = self.canvas
        page = self.state.get("page") or {}
        x0 = g["left"] + 28
        y = g["panel_y"] + 66
        if page.get("sub"):
            c.create_text(x0, y, text=page["sub"], anchor="w", fill=T.FG_2,
                          font=(T.family(), -13), tags="live")
            y += 32
        for row in page.get("lines", []):
            text, colour, px, bold = (list(row) + [T.FG, 13, False])[:4]
            if not text:
                y += 10
                continue
            c.create_text(x0, y, text=text, anchor="w", fill=colour or T.FG,
                          font=(T.family(), -(px or 13), "bold" if bold else "normal"),
                          tags="live")
            y += (px or 13) + 11
            if y > g["panel_y1"] - 20:
                break

    def _live_session(self, g):
        c = self.canvas
        # The strip is split: the day's trades on the left, the money on the
        # right. The money is the thing you look at twenty times an hour, so it
        # gets the fixed position and the large type; the log scrolls under it.
        pf = self.state.get("portfolio")
        x0 = g["left"] + 196
        x1 = g["right"] - 76
        pw = self._portfolio(g, x1, pf) if pf else 0
        lines = self.state["session"][-4:]
        y = g["session_y"] + 30
        room = (x1 - pw - 18) - x0
        if not lines:
            c.create_text(x0, y + 18, text="Nothing yet this session.", anchor="w",
                          fill=T.FG_MUTED, font=(T.family(), -12), tags="live")
            return
        for ln in lines:
            # Clip rather than let a long row run under the totals.
            txt = ln
            while txt and _tw(txt, 12) > room:
                txt = txt[:-2]
            if txt != ln:
                txt = txt.rstrip(" |") + "…"
            c.create_text(x0, y, text=txt, anchor="w", fill=T.FG_2,
                          font=(T.family(), -12), tags="live")
            y += 18

    def _portfolio(self, g, x1, pf):
        """Today's P&L for every index at once, drawn right-to-left from the
        edge of the session strip. Returns the width it used so the trade log
        beside it knows where to stop."""
        c = self.canvas
        y0, y1 = g["session_y"], g["session_y1"]
        mid = (y0 + y1) / 2
        h = y1 - y0

        net = pf["net"]
        col = T.UP if net > 0 else (T.DOWN if net < 0 else T.FG_2)
        money = f"{'+' if net >= 0 else '-'}Rs.{abs(net):,.0f}"
        big = 26 if h >= 100 else (22 if h >= 84 else 18)

        # The headline number, hard right.
        c.create_text(x1, mid + (8 if h >= 84 else 2), text=money, anchor="e",
                      fill=col, font=(T.family(), -big, "bold"), tags="live")
        w = _tw(money, big, True)
        c.create_text(x1, mid - (16 if h >= 84 else 12), text="TODAY", anchor="e",
                      fill=T.FG_2, font=(T.family(), -11, "bold"), tags="live")

        # The split underneath it, so a good day held up by one open position
        # cannot be mistaken for a good day already banked.
        if h >= 84:
            split = f"booked {pf['booked']:+,.0f}  ·  open {pf['open']:+,.0f}"
            c.create_text(x1, mid + (30 if h >= 100 else 26), text=split, anchor="e",
                          fill=T.FG_MUTED, font=(T.family(), -11), tags="live")
        # The total owns a fixed slice; the chips start at its edge so they
        # stay inside the log box rather than straddling its border.
        used = PF_W

        # Per-index chips to the left of the total — the "all markets in one
        # place" part. Dropped first when the window is narrow, because the
        # combined number is what the strip is for.
        chips = []
        for r in pf["rows"]:
            if r["total"] is None and not r["in_trade"]:
                continue
            v = r["total"]
            txt = (f"{r['index']} {v:+,.0f}" if v is not None
                   else f"{r['index']} in trade")
            cc = (T.UP if (v or 0) > 0 else T.DOWN if (v or 0) < 0 else T.FG_2)
            chips.append((txt, cc, r["open"] is not None))
        cx = x1 - used
        for txt, cc, live in reversed(chips):
            cw = _tw(txt, 12, True) + 26
            if cx - cw < g["left"] + 320:
                break
            cx -= cw + 8
            K.pill(c, cx, mid - 15, cx + cw, mid + 15, T.BG_INPUT, T.BORDER,
                   tags="live")
            c.create_text(cx + cw / 2, mid + 1, text=txt, fill=cc,
                          font=(T.family(), -12, "bold"), tags="live")
            if live:
                # A dot means this one is still moving.
                K.dot(c, cx + 7, mid - 9, 2.5, T.ACC_A, tags="live")
            used = x1 - cx
        return used


# ---------------------------------------------------------------------------


_MEASURE = None
_MCACHE = {}


def install_measurer(fn):
    """Let a headless renderer supply its own text metrics, so the preview and
    the real window agree about where things end up."""
    global _MEASURE
    _MEASURE = fn
    _MCACHE.clear()


def _tk_measure(text, px, bold):
    from tkinter import font as tkfont
    key = (px, bold)
    f = _MCACHE.get(key)
    if f is None:
        f = tkfont.Font(family=T.family(), size=-px,
                        weight="bold" if bold else "normal")
        _MCACHE[key] = f
    return f.measure(text)


def _tw(text, px, bold=False):
    """Real width if a font engine is available, estimate otherwise.

    This is not cosmetic. The top control row is laid out by flowing one widget
    after the next, so an under-estimate here does not make a label slightly
    narrow — it makes the next button sit on top of it."""
    fn = _MEASURE or _tk_measure
    try:
        return int(fn(text, px, bold))
    except Exception:
        return int(len(text) * px * 0.58)


def _ellipsis(text, px, max_w):
    """Trim to fit, with a real ellipsis. Wrapping would be nicer but the strip
    under the chart is a fixed three lines — silently overflowing it draws over
    the panel edge and into the card next door."""
    text = str(text)
    if _tw(text, px) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _tw(text[:mid] + "\u2026", px) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "\u2026"


def _grad_text(c, x, y, text, font, stops, tags):
    """Per-character gradient. Approximates each glyph's advance rather than
    measuring it, so it is only used on the one big headline where the text is
    short and the drift stays under a pixel."""
    n = max(1, len(text))
    px = abs(font[1])
    bold = len(font) > 2 and font[2] == "bold"
    cx = x
    for i, ch in enumerate(text):
        c.create_text(cx, y, text=ch, anchor="w", fill=K.ramp(stops, i / n),
                      font=font, tags=tags)
        cx += _tw(ch, px, bold)
