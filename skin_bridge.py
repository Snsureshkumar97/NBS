#!/usr/bin/env python3
"""
skin_bridge.py — connect the existing app logic to the new painted screen
================================================================================
gui.py's SignalApp knows how to fetch candles, score them, lock a ticket and
track it to a target. It expresses the result by calling `.config(text=…)` on
about forty labels.

Rather than rewrite every one of those call sites — which is where bugs would
come from, since each one carries a rule about colour or wording that took a
while to get right — this module hands SignalApp objects that ACCEPT
`.config(text=…, fg=…)` and quietly file the value under a name. After each
batch of updates, `flush()` assembles those values into the shape skin.py wants
and repaints.

So the trading logic is untouched, and the whole presentation change lives here.
"""

import os
import tkinter as tk

import market_map
import skin as skin_mod
import theme as T

# gui.py speaks in its own colour constants; map them onto the validated ones so
# a "green" decided by the engine ends up the green the palette approved.
# gui.py's colour constants, mapped onto the palette by NAME rather than by
# value. Mapping to values froze whatever palette happened to be loaded when
# this module was imported — so after a switch to light mode, every label that
# said "primary text" was still being handed the dark theme's near-white.
_COLOUR_MAP = {
    "#2ecc71": "UP", "#22c55e": "UP", "#16a34a": "UP", "#199e70": "UP",
    "#ef4444": "DOWN", "#e34948": "DOWN",
    "#f59e0b": "WARN", "#c98500": "WARN",
    "#eef1f6": "FG", "#9aa4b8": "FG_2", "#6b7280": "FG_MUTED",
    "#3b82f6": "ACC_B",
}


def _col(v, default=None):
    if not v:
        return default
    name = _COLOUR_MAP.get(str(v).lower())
    return getattr(T, name) if name else v


class Slot:
    """Looks like a tk.Label to code that only ever sets text and colour."""

    def __init__(self, bridge, name):
        self._b, self._n = bridge, name
        self.text = ""
        self._fg_raw = None
        # Buttons are told things like state="disabled" too. Keeping every
        # option means code that later reads it back gets the truth instead of
        # None — the login flow does exactly that.
        self.opts = {}

    def config(self, **kw):
        self.opts.update(kw)
        if "text" in kw:
            self.text = kw["text"]
        for k in ("fg", "foreground"):
            if k in kw:
                self._fg_raw = kw[k]
        self._b.touch()

    @property
    def fg(self):
        """Resolved against the CURRENT palette, not the one that was loaded
        when the label was set. Resolving in config() meant every value written
        before a theme switch kept the old theme's ink — near-white text on a
        white card."""
        return _col(self._fg_raw)

    configure = config

    def cget(self, key):
        if key == "text":
            return self.text
        if key in ("fg", "foreground"):
            return self.fg
        return self.opts.get(key)

    def __getitem__(self, key):
        return self.cget(key)

    # Anything a layout would have called on a real widget is a no-op here.
    def pack(self, *a, **k):
        pass

    pack_forget = grid = grid_forget = place = destroy = pack


class TextShim:
    """The full analysis text still gets written; it now lives behind the
    SESSION bar's list button instead of filling the middle of the window."""

    def __init__(self, bridge):
        self._b = bridge
        self.value = ""

    def insert(self, index, text, *a):
        self.value = text if str(index) in ("1.0", "0.0", "end") and not self.value \
            else self.value + text
        self._b.touch()

    def delete(self, *a):
        self.value = ""

    def get(self, *a):
        return self.value

    def config(self, **kw):
        pass

    configure = config

    def see(self, *a):
        pass

    def yview(self, *a):
        pass

    def pack(self, *a, **k):
        pass

    pack_forget = grid = place = destroy = pack


class DummyCanvas:
    """Absorbs the old range bar and ticket perforation drawing. Those two
    decorations have no home in the new layout, but the code that draws them is
    interleaved with code that does matter, so it is cheaper to let it run
    against a canvas that throws the ink away."""

    def winfo_width(self):
        return 200

    def winfo_height(self):
        return 10

    def delete(self, *a):
        pass

    def bind(self, *a, **k):
        pass

    def _mk(self, *a, **k):
        return 0

    create_line = create_rectangle = create_oval = create_text = _mk
    create_polygon = create_arc = create_image = _mk

    def config(self, **kw):
        pass

    configure = config

    def pack(self, *a, **k):
        pass

    pack_forget = grid = place = destroy = pack


class Pane:
    """A view container. Only pack/pack_forget are ever called on these."""

    def pack(self, *a, **k):
        pass

    pack_forget = grid = grid_forget = place = destroy = pack

    def config(self, **kw):
        pass

    configure = config


class ChartHolder:
    """gui.py hands the chart its data through .show(); we just keep it and let
    the skin draw it in place."""

    def __init__(self, bridge):
        self._b = bridge
        self.payload = None
        self.message = None

    def show(self, df, rec, trade, index_key, subtitle):
        self.payload = (df, rec, trade)
        self.message = None
        self._b.touch()

    def clear(self, message=""):
        self.payload = None
        self.message = message
        self._b.touch()

    def pack(self, *a, **k):
        pass

    pack_forget = grid = place = destroy = pack


class WhyHolder:
    def __init__(self, bridge):
        self._b = bridge
        self.data = None

    def render(self, rec, trade):
        why = (trade or {}).get("why")
        if why is None and rec is not None:
            try:
                import explain
                why = explain.explain(rec)
            except Exception:
                why = None
        self.data = why
        self._b.touch()

    def pack(self, *a, **k):
        pass

    pack_forget = grid = place = destroy = pack


# ---------------------------------------------------------------------------


class Bridge:
    def __init__(self, app):
        self.app = app
        self.skin = None
        self._pending = False
        self._painted = False   # first frame always draws the chrome
        self._map_tokens = None
        self._map_note = ""

    # -- lifecycle --------------------------------------------------------
    # Everything the chrome layer reads. If none of these changed, the frame
    # only needs the live layer redrawn — which is most of the screen's updates
    # and the difference between a calm window and a blinking one.
    CHROME_KEYS = ("running", "view", "index", "indices", "toggles", "mode",
                   "expiry", "lots", "theme", "span", "rail", "tab_marks",
                   "status", "status_col",
                   "token", "token_col", "timeframes", "trend", "day")

    def touch(self):
        """Coalesce a burst of .config() calls into one repaint.

        Deliberately a timer, not after_idle: a live tick stream can make the
        loop idle dozens of times a second, and repainting on every one of
        those is both wasted work and something you can see happening. 70ms is
        below the threshold where a click feels delayed and well above the
        rate at which the eye notices redraws."""
        if self._pending or self.skin is None:
            return
        self._pending = True
        try:
            self.app.root.after(70, self.flush)
        except Exception:
            self._pending = False
            self.flush()

    def _chrome_sig(self):
        st = self.skin.state
        return repr([st.get(k) for k in self.CHROME_KEYS])

    def install(self):
        app = self.app
        app.root.configure(bg=T.BG_APP)

        # These used to be created by the widget tree that has been replaced.
        # They are the app's actual settings — several methods read them on the
        # worker thread — so they are built first, before anything can look.
        import config as _config
        T.use(getattr(_config, "THEME", "dark"))
        app.index_var = tk.StringVar(value=app.current)
        app.mode_var = tk.StringVar(value="free")
        app.expiry_var = tk.StringVar(value="(nearest)")
        app.popup_var = tk.BooleanVar(value=True)
        app.auto_rearm_var = tk.BooleanVar(value=True)
        app.reentry_var = tk.BooleanVar(
            value=_config.ALLOW_SAME_DIRECTION_REENTRY)
        # How many lots the rupee figures are quoted for. A display setting,
        # not a trading one — the tool never places an order, it only tells you
        # what a move is worth. Frozen into the ticket at entry so changing it
        # later cannot rewrite what a finished trade made.
        app.lots_var = tk.StringVar(value=str(_config.DEFAULT_LOTS))
        app.limits_var = tk.BooleanVar(
            value=getattr(_config, "DAILY_LIMITS_ON", False))

        self.skin = skin_mod.Skin(app.root, self._callbacks())

        S = lambda n: Slot(self, n)
        for name in ("status_label", "market_label", "live_label", "token_label",
                     "badge_label", "trend_arrow", "trend_label", "trend_sub",
                     "day_change_label", "day_range_text", "conf_label",
                     "conf_sub_label", "adx_label", "reach_label", "reach_sub",
                     "ticket_no_label", "bias_label", "trade_header_label",
                     "issued_label", "net_label",
                     "start_btn", "stop_btn", "login_btn", "load_expiries_btn",
                     "summary_btn", "map_btn"):
            setattr(app, name, S(name))

        app.tf_labels = {k: S("tf_" + k) for k in ("15m", "1 hour", "VWAP")}
        app.stat_labels = {k: S("stat_" + k) for k in ("entry", "now", "spot", "pnl")}
        app.ind_labels = {k: S("ind_" + k) for k in ("Trend", "MACD", "RSI", "VWAP", "PCR")}
        app.ind_values = {k: S("indv_" + k) for k in ("Trend", "MACD", "RSI", "VWAP", "PCR")}
        app.trade_rows = {k: (Pane(), S("lt_" + k), S("lv_" + k), S("lp_" + k))
                          for k in ("T1", "T2", "T3", "SL")}
        app.tab_widgets = {k: {"frame": Pane(), "inner": Pane(), "dot": S("dot_" + k),
                               "sub": S("sub_" + k), "name": S("nm_" + k)}
                           for k in app.states}
        app.view_widgets = {k: S("view_" + k) for k in ("signal", "chart")}

        app.text = TextShim(self)
        app.history_text = TextShim(self)
        app.range_canvas = DummyCanvas()
        app._perf_canvas = DummyCanvas()
        app._canvas = DummyCanvas()
        app.signal_view = Pane()
        app.chart_view = Pane()
        app.footer = Pane()
        app.chart = ChartHolder(self)
        app.why = WhyHolder(self)
        app._range_pos = None
        app.view = "signal"
        app.trade_history_count = 0
        app.session_net = 0.0
        app.spark_values = []
        app._rail = "board"
        self.flush()

    # -- what the controls do --------------------------------------------
    def _callbacks(self):
        app = self.app
        return {
            "on_start": lambda: app._start(),
            "on_stop": lambda: app._stop(),
            "on_login": lambda: app._kite_login(),
            "on_load": lambda: app._load_expiries(),
            "on_save": lambda: app._save_summary(),
            "on_index": lambda k: app._switch_index(k),
            "on_view": lambda k: app._switch_view(k.lower()),
            "on_toggle": self._toggle,
            "on_rail": self._rail,
            "on_mode": lambda: self._menu("mode"),
            "on_expiry": lambda: self._menu("expiry"),
            "on_lots": lambda: self._menu("lots"),
            "on_theme": self._toggle_theme,
            "on_clear": self._clear,
            "on_quit": lambda: app._on_close(),
            "on_log": self._open_log,
            "on_span": lambda k: None,
        }

    def _toggle_theme(self):
        """Light <-> dark. Every draw call reads theme.py at paint time, so
        swapping the palette and repainting is genuinely all it takes."""
        T.use(T.other())
        try:
            self.app.root.configure(bg=T.BG_APP)
        except Exception:
            pass
        self.skin.state["theme"] = T.MODE
        self._painted = False
        self.flush()

    def _toggle(self, name):
        var = {"popup": "popup_var", "rearm": "auto_rearm_var",
               "reentry": "reentry_var", "limits": "limits_var"}.get(name)
        v = getattr(self.app, var, None)
        if v is not None:
            try:
                v.set(not bool(v.get()))
            except Exception:
                pass
        self.flush()

    def _rail(self, key):
        self.app._rail = key
        if key == "review":
            self.skin.state["page"] = self._review_page()
        elif key == "settings":
            self.skin.state["page"] = self._settings_page()
        self.flush()

    def _clear(self):
        app = self.app
        for name in ("_clear_active_trade",):
            fn = getattr(app, name, None)
            if fn:
                try:
                    fn()
                except Exception:
                    pass
        self.flush()

    def _menu(self, which):
        """A real popup menu — the one place a native widget still earns its
        keep, because reimplementing keyboard navigation on a canvas would be
        worse than what Tk already gives."""
        import tkinter as tk
        app = self.app
        if which == "mode":
            var, options = app.mode_var, ["free", "kite"]
        elif which == "lots":
            import config as _config
            var = app.lots_var
            options = [str(n) for n in range(1, _config.MAX_LOTS + 1)]
        else:
            var = app.expiry_var
            options = list(getattr(app, "expiry_options", None) or [var.get()])
        m = tk.Menu(app.root, tearoff=0, bg=T.BG_INPUT, fg=T.FG,
                    activebackground=T.ACC_B, activeforeground="#ffffff",
                    bd=0, relief="flat")
        for opt in options:
            m.add_command(label=str(opt),
                          command=lambda o=opt, v=var: (v.set(o), self.flush()))
        try:
            x = app.root.winfo_pointerx()
            y = app.root.winfo_pointery()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _open_log(self):
        """The full written analysis, which used to fill the middle of the
        window. It is still generated; it just lives one click away now."""
        import tkinter as tk
        app = self.app
        win = tk.Toplevel(app.root)
        win.title("Session log")
        win.geometry("880x620")
        win.configure(bg=T.BG_APP)
        box = tk.Text(win, bg=T.BG_CARD, fg=T.FG, insertbackground=T.FG,
                      relief="flat", wrap="word", padx=16, pady=14,
                      font=(T.family(), -12))
        box.pack(fill="both", expand=True, padx=12, pady=12)
        body = (app.text.value or "").rstrip()
        hist = (app.history_text.value or "").rstrip()
        if hist:
            body += "\n\n" + "-" * 60 + "\nTRADE HISTORY\n" + "-" * 60 + "\n" + hist
        box.insert("1.0", body or "Nothing yet this session.")
        box.config(state="disabled")

    # -- pages ------------------------------------------------------------
    def _review_page(self):
        try:
            import trade_log
            import review
            # review.load() already filters to CLOSE rows; reusing it means this
            # page and `python3 review.py` can never disagree about which trades
            # count, which they would the moment one of them was edited alone.
            closes = review.load()
        except Exception as exc:
            return {"sub": f"Could not read the trade log: {exc}", "lines": []}
        path = trade_log._log_path()
        if not closes:
            return {"sub": path, "lines": [
                ("No closed trades yet.", T.FG, 16, True), ("", None, 0, False),
                ("This file is written when a ticket is ISSUED and completed —",
                 T.FG_2, 13, False),
                ("not when the tool starts. An empty log means no signal has fired.",
                 T.FG_2, 13, False)]}
        rs = [x for x in (review.r_multiple(r) for r in closes) if x is not None]
        n = len(closes)

        lines = []
        for label, field in (("Reached T1", "t1_hit"), ("Reached T2", "t2_hit"),
                             ("Reached T3", "t3_hit"), ("Stopped out", "sl_hit")):
            v = review.hit(closes, field)
            lines.append((f"{label:<16}{v:>3} of {n}      {v / n * 100:4.1f}%",
                          T.DOWN if field == "sl_hit" else T.FG, 16, True))
        lines.append(("", None, 0, False))
        if rs:
            avg = sum(rs) / len(rs)
            net = avg - 0.15
            lines.append((f"Average          {avg:+.3f} R per trade before costs",
                          T.FG_2, 14, False))
            lines.append((f"After costs      {net:+.3f} R per trade",
                          T.UP if net > 0 else T.DOWN, 14, True))
        lines.append(("", None, 0, False))
        if n < 100:
            lines.append((f"{n} trades is not enough to tell an edge from luck. "
                          "Come back at a hundred.", T.WARN, 13, True))
        lines.append(("", None, 0, False))
        lines.append(("For the full breakdown by index, side, hour and ADX, run "
                      "python3 review.py in this folder.", T.FG_MUTED, 12, False))
        return {"sub": f"{path}  ·  {n} closed trade(s)", "lines": lines}

    def _settings_page(self):
        import config
        # Read the LAST KNOWN token state instead of asking Zerodha here.
        # token_status() is a network round-trip with no timeout, and this runs
        # on the drawing thread from a rail click — the window froze until
        # Zerodha answered, or indefinitely if it never did. gui.py already
        # refreshes this in the background via _check_token().
        app = self.app
        state = getattr(app, "_token_state", None) or "unknown"
        detail = getattr(app, "_token_detail", "") or "not checked yet"

        def mask(v):
            return "not set" if not v else f"{v[:4]}…{v[-2:]}  ({len(v)} chars)"

        home = config.home_config_dir()
        rows = [
            ("Today's Kite token", T.FG_2, 12, False),
            (f"{state.upper()} — {detail}",
             T.UP if state == "ok" else T.WARN, 15, True),
            ("", None, 0, False),
            ("Saved credentials", T.FG_2, 12, False),
            (f"API key       {mask(config.KITE_API_KEY)}", T.FG, 13, False),
            (f"API secret    {mask(config.KITE_API_SECRET)}", T.FG, 13, False),
            (f"stored in     {os.path.join(home, '.env')}", T.FG_MUTED, 12, False),
            ("", None, 0, False),
            ("Logs and history", T.FG_2, 12, False),
        ]
        try:
            import trade_log
            rows.append((f"trades        {trade_log._log_path()}", T.FG, 13, False))
        except Exception:
            pass
        rows += [
            ("", None, 0, False),
            ("Nothing here is ever sent anywhere. Every file above is on this "
             "computer only.", T.FG_MUTED, 12, False),
            ("", None, 0, False),
            ("This tool never places an order. Not SEBI-registered investment "
             "advice.", T.WARN, 12, True),
        ]
        return {"sub": "", "lines": rows}

    # -- assemble the screen ----------------------------------------------
    def flush(self):
        self._pending = False
        app, sk = self.app, self.skin
        if sk is None:
            return
        st = sk.state
        was = self._chrome_sig()

        st["rail"] = getattr(app, "_rail", "board")
        _v = getattr(app, "view", "signal")
        st["view"] = "Chart" if _v == "chart" else "Signal"
        st["mode"] = app.mode_var.get()
        st["expiry"] = app.expiry_var.get()
        st["lots"] = app.lots_var.get()
        st["index"] = app.index_var.get()
        st["toggles"] = {"popup": bool(app.popup_var.get()),
                         "rearm": bool(app.auto_rearm_var.get()),
                         "reentry": bool(app.reentry_var.get()),
                         "limits": bool(app.limits_var.get())}
        st["indices"] = list(app.states.keys())

        # top-right status: the market line, with the live badge appended
        market = app.market_label.text or app.status_label.text or "not started"
        live = (app.live_label.text or "").strip()
        st["status"] = (market.lstrip("● ").strip() + ("  ·  " + live if live else ""))
        st["status_col"] = app.market_label.fg or T.FG_MUTED
        st["token"] = (app.token_label.text or "not connected").lstrip("● ").strip()
        st["token_col"] = app.token_label.fg or T.FG_MUTED

        # trend card
        arrow = (app.trend_arrow.text or "").strip()
        st["trend"] = {
            "label": app.trend_label.text or "—",
            "sub": app.trend_sub.text or "",
            "dir": "up" if arrow in ("↑", "▲") else "down" if arrow in ("↓", "▼") else "flat",
        }
        st["spark"] = list(getattr(app, "spark_values", []) or [])
        st["day"] = (app.day_change_label.text, app.day_change_label.fg,
                     app.day_range_text.text)
        st["timeframes"] = [(k, app.tf_labels[k].text, app.tf_labels[k].fg or T.FG_MUTED)
                            for k in ("15m", "1 hour", "VWAP")]
        st["tab_marks"] = {k: (app.tab_widgets[k]["dot"].fg or T.FG_MUTED,
                               app.tab_widgets[k]["sub"].text or "")
                           for k in app.states}

        # confidence rail
        rows = []
        for nm in ("Trend", "MACD", "RSI", "VWAP", "PCR"):
            a = (app.ind_labels[nm].text or "–").strip()
            raw = (app.ind_values[nm].text or "").strip()
            col = app.ind_labels[nm].fg or T.FG_MUTED
            word = {"↑": "Bullish", "▲": "Bullish", "↓": "Bearish",
                    "▼": "Bearish"}.get(a, raw or "—")
            rows.append((nm, f"{word}  {raw}".strip() if word != raw else word,
                         col, self._strength(nm, raw, a)))
        rows.append(("ADX Gate", app.adx_label.text or "—",
                     app.adx_label.fg or T.FG_MUTED,
                     self._strength("ADX", app.adx_label.text or "", "")))
        rows.append(("Room to Run", app.reach_label.text or "—",
                     app.reach_label.fg or T.FG_MUTED))
        st["confidence"] = {
            "pct": getattr(app, "confidence_pct", None),
            "label": (app.conf_label.text or "—").title(),
            "rows": rows,
        }

        # the ticket
        head = app.bias_label.text or "NO SIGNAL YET"
        sub = app.trade_header_label.text or ""
        if app.issued_label.text:
            sub = (sub + "   ·   " + app.issued_label.text).strip(" ·")
        badge = (app.badge_label.text or "WAITING").strip()
        st["ticket"] = {
            "badge": badge,
            "badge_col": app.badge_label.fg or T.ACC_B,
            "headline": head,
            "accent": head.strip().upper() == "NO SIGNAL YET",
            "headline_col": app.bias_label.fg or T.FG,
            "sub": sub or "No trade locked in — a ticket is issued automatically "
                          "on the next CE/PE signal.",
            "stats": [(t, app.stat_labels[k].text or "—",
                       app.stat_labels[k].fg or T.FG)
                      for k, t in (("entry", "ENTRY"), ("now", "NOW"),
                                   ("spot", "SPOT"),
                                   ("pnl", getattr(app, "pnl_caption", "1 LOT")))],
            "levels": self._levels(),
            "ladder": self._ladder(),
        }

        if getattr(app, "view", "signal") == "chart":
            st["chart"] = app.chart.payload
            st["why"] = app.why.data

        if st["rail"] == "map":
            st["map"] = self._map_payload()

        st["session"] = [ln for ln in (app.history_text.value or "").splitlines() if ln.strip()]
        try:
            st["portfolio"] = app.portfolio()
        except Exception:
            st["portfolio"] = None
        if not self._painted or self._chrome_sig() != was:
            self._painted = True
            sk.repaint()
        else:
            sk.paint_live()

    def _levels(self):
        """The four level cards.

        This used to hand the skin a hardcoded "T1" and the level's own
        colour, which meant a target could be HIT and the card would look
        exactly as it did a second earlier — the tick and the timestamp the
        tracker computes were being thrown away here. Now the hit state, the
        time it happened and how far price has travelled all come through."""
        app = self.app
        trade = getattr(app, "active_trade", None)
        prog = getattr(app, "level_progress", None) or {}
        out = []
        for k, colour in (("T1", T.T1_COL), ("T2", T.T2_COL),
                          ("T3", T.T3_COL), ("SL", T.STOP_COL)):
            hit, when = False, None
            if trade is not None:
                if k == "SL":
                    hit, when = trade.get("sl_hit"), trade.get("sl_hit_time")
                else:
                    hit = trade.get("hit", {}).get(k)
                    when = trade.get("hit_time", {}).get(k)
            name = "STOP" if k == "SL" else k
            mark = None
            if hit:
                # Green for a target reached, red for a stop taken out — the
                # tick alone would say "done" without saying which kind.
                colour = T.DOWN if k == "SL" else T.UP
                mark = ("✓ " + when) if when else "✓"
            value = " · ".join(x for x in (app.trade_rows[k][2].text,
                                           app.trade_rows[k][3].text) if x) or "—"
            out.append((name, value, colour, prog.get(k), mark))
        return out

    @staticmethod
    def _strength(name, raw, arrow):
        """0..1 for the bar beside a vote — how FAR from neutral it reads.

        Each indicator has its own scale, so each gets its own mapping rather
        than one shared guess. Anything unparseable returns None and simply
        gets no bar: an undecided vote should look undecided, not like a
        confident zero.
        """
        txt = "".join(ch for ch in str(raw) if ch in "0123456789.-+")
        try:
            v = float(txt)
        except ValueError:
            return 0.0 if arrow in ("–", "-", "") else None
        if name == "RSI":                       # 50 is neutral, 0/100 extreme
            return min(1.0, abs(v - 50) / 30.0)
        if name == "PCR":                       # 1.0 is neutral
            return min(1.0, abs(v - 1.0) / 0.6)
        if name == "ADX":                       # the gate sits at ~20
            return min(1.0, v / 40.0)
        if name == "MACD":                      # unbounded; 40 is emphatic
            return min(1.0, abs(v) / 40.0)
        return min(1.0, abs(v) / 100.0)

    def _ladder(self):
        """The levels as NUMBERS on a price axis, plus where price is now.

        The card view only ever needed formatted strings. Plotting them at
        their true distance needs the values, so this is a second, numeric
        view of the same four levels — built here rather than in the skin so
        the drawing code never has to parse a label back into a price.
        """
        app = self.app
        vals = getattr(app, "level_values", None) or {}
        money = getattr(app, "level_money", None) or {}
        trade = getattr(app, "active_trade", None)

        rows = []
        for k, colour in (("T3", T.T3_COL), ("T2", T.T2_COL),
                          ("T1", T.T1_COL), ("SL", T.STOP_COL)):
            v = vals.get(k)
            if v is None:
                continue
            hit, when = False, None
            if trade is not None:
                if k == "SL":
                    hit, when = trade.get("sl_hit"), trade.get("sl_hit_time")
                else:
                    hit = trade.get("hit", {}).get(k)
                    when = trade.get("hit_time", {}).get(k)
            rows.append({
                "name": "STOP" if k == "SL" else k,
                "price": float(v),
                "colour": (T.DOWN if k == "SL" else T.UP) if hit else colour,
                "base": colour,
                "hit": bool(hit),
                "when": when,
                "money": money.get(k, ""),
            })
        if len(rows) < 2:
            # Nothing to plot yet. Hand back an EMPTY ladder rather than None:
            # returning None dropped the panel back to the four boxes, so the
            # screen appeared to change design every time a signal came and
            # went. The shape of the panel should not depend on whether the
            # market happens to be offering a trade this minute.
            return {
                "empty": True,
                "levels": [{"name": "STOP" if k == "SL" else k, "price": None,
                            "colour": colour, "base": colour, "hit": False,
                            "when": None, "money": ""}
                           for k, colour in (("T3", T.T3_COL), ("T2", T.T2_COL),
                                             ("T1", T.T1_COL), ("SL", T.STOP_COL))],
                "entry": None, "now": None, "rr": None, "live": False,
            }
        return {
            "levels": rows,
            "entry": getattr(app, "ladder_entry", None),
            "now": getattr(app, "ladder_now", None),
            "rr": getattr(app, "ladder_rr", None),
            "live": trade is not None and trade.get("status") == "OPEN",
        }

    def _map_payload(self):
        app = self.app
        streamer = getattr(app, "streamer", None)
        provider = getattr(app, "provider", None)
        if streamer is None or provider is None:
            return None
        key = app.index_var.get()
        if self._map_tokens is None or self._map_tokens[0] != key:
            syms = [s for s, _, _ in market_map.CONSTITUENTS.get(key, [])]
            try:
                tokens = provider.equity_tokens(syms)
                streamer.subscribe(list(tokens.values()), quote=True)
            except Exception:
                tokens = {}
            self._map_tokens = (key, tokens)
        rows = market_map.rows_for(key, self._map_tokens[1], streamer)
        dead = not getattr(streamer, "connected", False)
        text, colour = market_map.breadth(rows)
        return (rows, dead, text, colour)


def attach(app):
    b = Bridge(app)
    b.install()
    app.bridge = b
    return b
