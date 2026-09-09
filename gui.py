#!/usr/bin/env python3
"""
gui.py — Nifty / BankNifty / Sensex CE-PE Signal Tool (desktop window)
==========================================================================
A simple native window instead of the terminal — pick an index, hit Start,
and watch it auto-refresh with live data while you trade.

Run it with:
    python3 gui.py

Uses Tkinter, which ships with standard Python installs (no extra install
needed on most systems — if you get "No module named tkinter" on Mac,
reinstall Python from python.org, which bundles it).

*** THIS TOOL DOES NOT PLACE ANY ORDERS. It only displays a suggestion. ***
*** NOT SEBI-registered investment advice. See README.md for full disclaimer. ***
"""

import queue
import sys
import threading
import time
import datetime as dt

import pandas as pd

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox

import config
import signal_engine
import explain
import kite_auth
import skin_bridge
from chart_panel import ChartPanel, WhyPanel
from data_providers import KiteStreamer
from market_map import MarketMapWindow
import trade_log
from main import (
    fetch_recommendation,
    format_report,
    format_market_closed_banner,
    is_market_open,
    get_provider,
    MissingKiteCredentials,
    now_ist,
    MARKET_OPEN_TIME,
    MARKET_CLOSE_TIME,
    SESSION_REFRESH_EVERY,
    DISCLAIMER,
)

# =============================================================================
# COLOR PALETTE — one explicit dark theme for the whole app, applied to every
# widget. Deliberately NOT relying on the OS's own light/dark appearance:
# plain tk.Label/tk.Frame widgets don't reliably pick up the system theme on
# every platform, which is what caused the earlier "dark text on a black
# background, invisible" bug on macOS Dark Mode. Setting every background and
# foreground explicitly here means it looks the same, and stays readable, no
# matter what theme your OS is in.
# =============================================================================
BG_APP = "#0b0d12"       # window background
BG_PANEL = "#141924"     # card/panel background (spot price, bias, tracker)
BG_INPUT = "#1c2330"     # dropdowns / entry fields / buttons
BORDER = "#2a3141"       # card borders / dividers
FG_PRIMARY = "#eef1f6"   # main text
FG_SECOND = "#9aa4b8"    # secondary/sub text
FG_MUTED = "#6b7280"     # disabled/faint text
ACCENT = "#3b82f6"       # blue accent (buttons, header rule)
GREEN = "#2ecc71"        # bullish / target hit
RED = "#ef4444"          # bearish / stop-loss hit
AMBER = "#f59e0b"        # market closed / warnings

BIAS_COLORS = {
    "BULLISH": GREEN,
    "BEARISH": RED,
    "NEUTRAL": FG_SECOND,
}

# Everything below is tracked SEPARATELY for each index, so all three are
# monitored at once and switching tabs is instant — no stop/start, and a
# trade on one index keeps running while you watch another.
_PER_INDEX_DEFAULTS = {
    "last_rec": None,            # newest recommendation
    "base_df": None,             # completed candles from REST
    "base_oi": None,             # last option-chain snapshot
    "active_trade": None,        # the locked-in ticket, if any
    "index_token": None,         # streaming token for the index
    "trade_token": None,         # streaming token for the locked contract
    "live_spot": None,           # newest streamed spot
    "last_signature": None,      # (bias, type, strike) — for report highlighting
    "last_bias_signature": None, # (bias, type) — for "is this a NEW signal"
    "_confirm_streak": 0,        # consecutive live evals agreeing
    "_confirm_dir": None,
    "_confirm_since": None,      # when that direction first showed up

    "_last_ticket_at": None,     # when a ticket was last issued, for re-entry cooldown
    # WHY the last evaluation did not issue a ticket, as (code, short, long).
    # The screen used to assert "a ticket is issued when the direction changes"
    # no matter which rule actually held it back, which is wrong more often
    # than it is right — the direction signature is only one of five gates.
    "_wait_reason": None,
    "report_text": None,         # last full text report
    "notes": (),
}


class SignalApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nifty / BankNifty / Sensex — Live Signal")
        # The ticket needs enough width for both stubs side by side (the main
        # stub plus the ~228px reasoning stub), so the minimum is wider than
        # the old stacked layout's was.
        # Scrollable, so the height only decides how much you see at once.
        self.root.geometry("1500x960")
        self.root.minsize(1180, 720)
        self.root.configure(bg=BG_APP)

        self.provider = None
        self.worker_thread = None
        self.current_stop_event = None   # fresh Event per run — see _start()
        self.msg_queue = queue.Queue()
        self.cycle_count = 0
        self.was_open_last_check = None
        # One snapshot fetch per closed period, so the screen shows the market
        # as it stood at the bell instead of going blank overnight.
        self._closed_snapshot_done = False
        self.streamer = None       # live WebSocket price feed (kite mode)
        self.map_window = None     # the market heat map, when open
        self._summary_done = False # session report written for this run
        self._login_running = False
        self._token_state = None   # "ok" | "missing" | "expired" | "unknown"
        self._token_detail = ""

        # ------------------------------------------------------------------
        # ALL THREE INDICES ARE TRACKED AT ONCE.
        # Everything that used to be a single attribute is now per-index, so
        # a Nifty trade keeps being tracked while you're looking at BankNifty
        # and a signal on any of them lights up its tab immediately. The
        # properties defined below this class proxy the old attribute names
        # to whichever index is currently in focus, so the rest of the logic
        # reads exactly as it did before.
        # ------------------------------------------------------------------
        self.indices = list(config.INSTRUMENTS.keys())
        self.states = {k: dict(_PER_INDEX_DEFAULTS) for k in self.indices}
        self.current = self.indices[0]   # the index being DISPLAYED
        self._focus = self.current       # the index the properties resolve to

        self.confidence_pct = None   # 0-100 for the ring; set in _render_live_side
        self.pnl_caption = "1 LOT"   # heading over the rupee column
        self._entry_block = None     # (code, short, long) when entries are held
        self._last_ticket_any = None # last ticket in ANY index/direction
        self.spark_values = []       # today's closes, for the DAY MOVE sparkline
        self.expiry_options = []     # filled by _load_expiries, shown in its menu
        skin_bridge.attach(self)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll_queue)
        self.root.after(config.LIVE_UI_MS, self._live_tick)
        self.root.after(config.LIVE_ANALYSIS_MS, self._live_analysis)
        # Checked in the background at launch, so the badge already says
        # whether today's token is alive before you reach for Start.
        self.root.after(300, self._check_token)

    # ------------------------------------------------------------------ UI
    # The window is painted by skin.py now — one canvas, drawn by hand, so it
    # looks the same on every OS. skin_bridge.attach() above hands this class
    # stand-ins for the labels it used to own, which is why every render method
    # below still reads the way it did.

    def _card(self, parent, **pack_opts):
        return parent

    def _build_scroll_container(self):
        pass

    def _bind_wheel(self, on):
        pass

    def _on_wheel(self, event):
        pass

    def _draw_perforation(self, event=None):
        """The ticket's torn edge. The painted layout draws its own cards, so
        this is kept only because _build_widgets used to bind it."""

    def _draw_range_bar(self):
        """The day's range bar lived under the trend card. The new layout shows
        the range as text instead; this stays so _render_trend can keep calling
        it unchanged."""

    def _set_badge(self, text: str, color: str):
        if self._focus != self.current:
            return
        self.badge_label.config(text=f"  {text}  ", fg=color)

    def _render_trend(self, trend: dict):
        """Fills the market-trend strip from compute_market_trend()."""
        if self._focus != self.current:
            return
        if not trend:
            self.trend_arrow.config(text="•", fg=FG_MUTED)
            self.trend_label.config(text="—", fg=FG_SECOND)
            self.trend_sub.config(text="trend read unavailable")
            return

        direction = trend["direction"]
        if trend["strength"] == "UNKNOWN":
            arrow, color = "•", FG_MUTED       # too few candles to judge yet
        elif trend["strength"] == "WEAK":
            arrow, color = "~", AMBER          # chop — neither up nor down
        elif direction == "UP":
            arrow, color = "▲", GREEN
        elif direction == "DOWN":
            arrow, color = "▼", RED
        else:
            arrow, color = "•", FG_SECOND

        self.trend_arrow.config(text=arrow, fg=color)
        self.trend_label.config(text=trend["label"], fg=color)
        # Displacement is shown alongside ADX so the headline can be checked
        # rather than trusted — a high ADX with near-zero movement is exactly
        # the case that used to read as "STRONG DOWNTREND" on a flat day.
        bits = [f"ADX {trend['adx']}", f"momentum {trend['momentum']}"]
        if trend.get("displacement_atr") is not None:
            bits.append(f"moved {trend['displacement_atr']} ATR in 14 bars")
        self.trend_sub.config(
            text=" · ".join(bits),
            fg=FG_MUTED)

        chg, pct = trend.get("day_change"), trend.get("day_change_pct")
        if chg is None:
            self.day_change_label.config(text="—", fg=FG_SECOND)
            self.day_range_text.config(text="")
        else:
            sign = "+" if chg >= 0 else ""
            self.day_change_label.config(
                text=f"{sign}{chg:,.2f}   ({sign}{pct}%)",
                fg=GREEN if chg > 0 else (RED if chg < 0 else FG_SECOND))
            lo, hi = trend.get("day_low"), trend.get("day_high")
            if lo is not None and hi is not None:
                self.day_range_text.config(text=f"{lo:,.2f}  —  {hi:,.2f}")

        self._range_pos = trend.get("range_pos_pct")
        self._draw_range_bar()

        def arrow_for(d):
            return {"UP": ("▲", GREEN), "DOWN": ("▼", RED)}.get(d, ("–", FG_MUTED))

        a, col = arrow_for(direction)
        self.tf_labels["15m"].config(text=a, fg=col)
        a, col = arrow_for(trend["htf_direction"])
        self.tf_labels["1 hour"].config(text=a, fg=col)
        vs = trend["vs_vwap"]
        self.tf_labels["VWAP"].config(
            text={"ABOVE": "▲", "BELOW": "▼"}.get(vs, "–"),
            fg={"ABOVE": GREEN, "BELOW": RED}.get(vs, FG_MUTED))

    def _reward_risk(self, targets, sl, entry):
        """Distance to T3 against distance to the stop, from THESE numbers.

        Not read off the recommendation: rec's reward:risk describes the setup
        the engine is looking at right now, which after a ticket is frozen is a
        different trade. This one describes the trade you are actually in.
        """
        try:
            t3, e = targets[2], entry
            if t3 is None or sl is None or e is None:
                return None
            reward, risk = abs(t3 - e), abs(e - sl)
            if risk <= 0:
                return None
            return {"ratio": round(reward / risk, 2),
                    "reward": round(reward, 2), "risk": round(risk, 2)}
        except (TypeError, IndexError):
            return None

    def _lots(self, trade=None):
        """Lots the rupee figures are quoted for.

        A locked ticket carries the number that was selected when it was
        issued; only a preview follows the live selector. Otherwise changing
        the dropdown at lunchtime would rewrite what the morning's trade made,
        and the session log would disagree with itself."""
        if trade is not None and trade.get("lots"):
            return int(trade["lots"])
        try:
            n = int(self.lots_var.get())
        except (TypeError, ValueError, AttributeError):
            return config.DEFAULT_LOTS
        return max(1, min(config.MAX_LOTS, n))

    def _lot_caption(self, trade=None):
        n = self._lots(trade)
        return "1 LOT" if n == 1 else f"{n} LOTS"

    def _set_stats(self, entry=None, now=None, spot=None, pnl=None, pnl_color=None):
        def fmt(v, money=False):
            if v is None:
                return "—"
            if money:
                return f"{'+' if v >= 0 else ''}{v:,.0f}"
            try:
                # Always comma-grouped and 2dp — a streamed tick can arrive as
                # an int and would otherwise render as a bare "57450".
                return f"{float(v):,.2f}"
            except (TypeError, ValueError):
                return str(v)

        self.stat_labels["entry"].config(text=fmt(entry), fg=FG_PRIMARY if entry is not None else FG_MUTED)
        self.stat_labels["now"].config(text=fmt(now), fg=FG_PRIMARY if now is not None else FG_MUTED)
        self.stat_labels["spot"].config(text=fmt(spot), fg=FG_PRIMARY if spot is not None else FG_MUTED)
        self.stat_labels["pnl"].config(
            text=fmt(pnl, money=True),
            fg=(pnl_color or FG_PRIMARY) if pnl is not None else FG_MUTED,
        )

    def _fill_levels(self, targets, sl, trade=None, entry_ltp=None, lot_size=None,
                     preview=False, lots=1):
        """Renders the four level boxes. `trade` (when given) supplies the
        hit/timestamp state; `preview` greys everything out to signal these
        numbers aren't locked in yet."""
        vals = {"T1": targets[0], "T2": targets[1], "T3": targets[2], "SL": sl}
        # The ladder plots these at their true distance, so it needs the
        # numbers themselves — the label text is already formatted and rounded.
        self.level_values = dict(vals)
        self.level_money = {}
        for key in ["T1", "T2", "T3", "SL"]:
            box, title_lbl, val_lbl, pnl_lbl = self.trade_rows[key]
            v = vals[key]

            hit, when = False, None
            if trade is not None:
                if key == "SL":
                    hit, when = trade["sl_hit"], trade["sl_hit_time"]
                else:
                    hit, when = trade["hit"][key], trade["hit_time"][key]

            base_title = "STOP" if key == "SL" else key
            if hit:
                col = RED if key == "SL" else GREEN
                title_lbl.config(text=f"{base_title} ✓ {when}", fg=col)
                val_lbl.config(fg=col)
                box.config(highlightbackground=col)
            else:
                title_lbl.config(text=base_title, fg=RED if (key == "SL" and not preview) else FG_MUTED)
                val_lbl.config(fg=FG_MUTED if (preview or v is None) else FG_PRIMARY)
                box.config(highlightbackground=RED if (key == "SL" and not preview and v is not None) else BORDER)

            val_lbl.config(text="—" if v is None else f"{v}")

            # Estimated rupee P&L at this level — only meaningful when we're
            # tracking a real live premium with a known lot size.
            if v is None or entry_ltp is None or lot_size is None:
                self.level_money[key] = ""
                pnl_lbl.config(text="", fg=FG_MUTED)
            else:
                pnl = round((v - entry_ltp) * lot_size * lots, 2)
                self.level_money[key] = (f"{'+' if pnl >= 0 else '-'}Rs."
                                         f"{abs(pnl):,.0f}")
                # sign goes BEFORE the currency, not before the digits —
                # "-Rs.2,672", never "Rs.-2,672".
                pnl_lbl.config(text=f"{'+' if pnl >= 0 else '-'}Rs.{abs(pnl):,.0f}",
                                fg=GREEN if pnl > 0 else (RED if pnl < 0 else FG_MUTED))

    def _render_live_side(self, rec):
        """Right-hand stub — the reasoning: which indicators voted which way,
        the confidence, and whether the ADX trend-strength gate passed. This
        always reflects the LATEST read, even while an older ticket is still
        being tracked on the left."""
        if self._focus != self.current:
            return
        tech = rec.get("technical", {})
        oi = rec.get("option_chain", {})

        votes = {
            "Trend": tech.get("trend_score"),
            "MACD": tech.get("macd_score"),
            "RSI": tech.get("rsi_score"),
            "VWAP": tech.get("vwap_score"),
            "PCR": oi.get("oi_score") if oi.get("available") else None,
        }
        # The actual reading behind each vote, so "–" is self-explaining:
        # you can see PCR is 1.04 (inside the neutral 0.80–1.20 band) rather
        # than having to wonder whether the data is even arriving.
        pcr_val = oi.get("pcr") if oi.get("available") else None
        readings = {
            "Trend": "",
            "MACD": f"{tech['macd_hist']:+g}" if tech.get("macd_hist") is not None else "",
            "RSI": f"{tech['last_rsi']:.0f}" if tech.get("last_rsi") is not None else "",
            "VWAP": f"{tech['vwap_gap']:+g}" if tech.get("vwap_gap") is not None else "",
            "PCR": f"{pcr_val:.2f}" if pcr_val is not None else "",
        }

        for nm, score in votes.items():
            lbl = self.ind_labels[nm]
            self.ind_values[nm].config(text=readings.get(nm, ""))
            if score is None:
                lbl.config(text="n/a", fg=FG_MUTED)
            elif score > 0:
                lbl.config(text="▲", fg=GREEN)
            elif score < 0:
                lbl.config(text="▼", fg=RED)
            else:
                lbl.config(text="–", fg=FG_MUTED)

        conf = rec.get("confidence", "N/A")
        conf_color = {"High": GREEN, "Medium": AMBER}.get(conf, FG_SECOND)
        self.conf_label.config(text=str(conf).upper(), fg=conf_color)
        self.conf_sub_label.config(text=f"score {rec.get('score')} of {rec.get('max_score')}")
        # The ring wants a number, and deriving it here — where the score and
        # its maximum are both in hand — beats parsing it back out of the label.
        try:
            score, mx = float(rec.get("score") or 0), float(rec.get("max_score") or 0)
            # MAGNITUDE, not the signed score. A bearish read scores negative,
            # and feeding that straight in produced "-100%" over an empty ring
            # — which reads as negative confidence, a thing that does not
            # exist. How SURE the engine is, is a number from 0 to 100; WHICH
            # WAY it leans is already said by the trend card, the headline and
            # every row of the vote list underneath.
            self.confidence_pct = round(100.0 * abs(score) / mx, 0) if mx else None
        except Exception:
            self.confidence_pct = None

        adx = tech.get("adx")
        if adx is None:
            self.adx_label.config(text="—", fg=FG_MUTED)
        elif rec.get("adx_blocked"):
            self.adx_label.config(text=f"{adx}  BLOCKED", fg=RED)
        elif tech.get("adx_ok"):
            self.adx_label.config(text=f"{adx}  PASS", fg=GREEN)
        else:
            self.adx_label.config(text=f"{adx}  WEAK", fg=AMBER)

        # How far the market can realistically go, and whether that's worth
        # the risk. This is what the targets on the left are built from.
        pts = rec.get("reach_points")
        rr = rec.get("reach_to_risk")
        rch = rec.get("reach") or {}
        if pts is None:
            up, down = rch.get("reach_up"), rch.get("reach_down")
            if up is not None or down is not None:
                self.reach_label.config(text=f"↑{up or '—'} ↓{down or '—'}", fg=FG_SECOND)
                self.reach_sub.config(text="pts either way, rest of day")
            else:
                self.reach_label.config(text="—", fg=FG_MUTED)
                self.reach_sub.config(text="")
        else:
            if rec.get("not_worth_it"):
                col = RED
            elif rr is not None and rr >= 2:
                col = GREEN
            else:
                col = AMBER
            self.reach_label.config(text=f"{pts:g} pts", fg=col)
            bits = []
            if rr is not None:
                bits.append(f"reward:risk {rr}:1")
            if rec.get("reach_reason"):
                bits.append(f"capped by {rec['reach_reason']}")
            self.reach_sub.config(text=" · ".join(bits), fg=FG_MUTED)

    def _last_candle_stamp(self, rec):
        """When the last candle in this snapshot printed — the honest answer to
        "how old is what I'm looking at?" Falls back to a plain date if the
        index isn't a timestamp."""
        try:
            ts = rec["candles"].index[-1]
            return ts.strftime("%d %b %H:%M")
        except Exception:
            return "previous session"

    def _set_text(self, content: str, changed: bool = False):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        if changed:
            self.text.tag_add("changed", "1.0", "3.0")
        self.text.config(state="disabled")

    def _switch_index(self, key):
        """Show a different index. Instant — every index is already being
        analysed in the background, so this is purely a view change with no
        stop/start and nothing to re-fetch."""
        if key not in self.states:
            return
        self.current = key
        self._focus = key
        self.index_var.set(key)
        self._paint_tabs()
        self._render_current()

    # -------------------------------------------------------- Zerodha login
    def _check_token(self, quiet=True):
        """Ask Zerodha whether the stored token is actually alive.

        Deliberately asks rather than checking the clock: a token can be
        revoked early, and one generated before the morning flush dies at the
        flush no matter how recently you made it.
        """
        def worker():
            try:
                state, detail = kite_auth.token_status()
            except Exception as exc:
                state, detail = "unknown", str(exc)
            self.msg_queue.put(("token_state", state, detail, quiet))

        threading.Thread(target=worker, daemon=True).start()

    def _paint_token(self, state, detail):
        text, colour = {
            "ok": ("● logged in", GREEN),
            "missing": ("● not logged in", FG_MUTED),
            "expired": ("● token expired", AMBER),
        }.get(state, ("● unknown", FG_MUTED))
        self.token_label.config(text=text, fg=colour)
        self._token_state = state
        self._token_detail = detail

    def _kite_login(self):
        """One click: listen locally, open Zerodha, catch the redirect, save.

        Runs on a worker thread — the login involves a browser round-trip that
        can take a minute, and doing it on the UI thread would freeze the
        window (and stop any open trade from being tracked) the whole time.
        """
        if getattr(self, "_login_running", False):
            return
        if not config.KITE_API_KEY or not config.KITE_API_SECRET:
            if not self._ask_api_credentials():
                return

        self._login_running = True
        self.login_btn.config(state="disabled", text="Logging in…")
        self.token_label.config(text="● waiting for Zerodha", fg=ACCENT)
        self.status_label.config(
            text=f"A browser tab is opening — log in to Zerodha there. "
                 f"Waiting for the redirect to {kite_auth.redirect_url()}")

        def worker():
            token, error = kite_auth.one_click_login(
                on_status=lambda m: self.msg_queue.put(("status", m)))
            self.msg_queue.put(("login_done", token, error))

        threading.Thread(target=worker, daemon=True).start()

    def _ask_api_credentials(self):
        """API key and secret, asked once — unlike the access token these do
        NOT expire daily, so this should happen exactly once per machine."""
        from tkinter import simpledialog
        messagebox.showinfo(
            "One-time setup",
            "Two things are needed once (they don't expire daily like the token does):\n\n"
            "1. Your API key and secret, from https://developers.kite.trade/apps\n\n"
            f"2. That app's Redirect URL set to exactly:\n     {kite_auth.redirect_url()}\n\n"
            "Without step 2 the login can't be caught automatically and will time out.")
        key = simpledialog.askstring("Kite API key", "API key:", parent=self.root)
        if not key:
            return False
        secret = simpledialog.askstring("Kite API secret", "API secret:",
                                        parent=self.root, show="*")
        if not secret:
            return False
        config.apply_credentials(key.strip(), secret.strip(), None)
        try:
            kite_auth.save_env({"KITE_API_KEY": key.strip(),
                                "KITE_API_SECRET": secret.strip()})
        except RuntimeError as exc:
            messagebox.showerror("Couldn't save", str(exc))
            return False
        return True

    def _finish_login(self, token, error):
        self._login_running = False
        self.login_btn.config(state="normal", text="Login to Zerodha")
        if error:
            self._paint_token("missing", error)
            self.status_label.config(text="Login failed — see the dialog.")
            messagebox.showerror("Zerodha login failed", error)
            return
        self._paint_token("ok", "Logged in.")
        self.status_label.config(text="Logged in. Today's token is saved — press Start.")
        self.mode_var.set("kite")

    def _switch_view(self, key):
        """Signal <-> Chart. Purely a view change; the engine behind the Signal
        view keeps running whichever tab is showing."""
        if key not in ("signal", "chart") or key == self.view:
            return
        self.view = key
        if key == "chart":
            self.signal_view.pack_forget()
            self.chart_view.pack(fill="both", expand=True, before=self.footer)
            self._render_chart()
        else:
            self.chart_view.pack_forget()
            self.signal_view.pack(fill="x", before=self.footer)
        self._paint_view_tabs()

    def _paint_view_tabs(self):
        for key, b in self.view_widgets.items():
            active = (key == self.view)
            b.config(bg=ACCENT if active else BG_INPUT,
                     fg=FG_PRIMARY if active else FG_SECOND)

    def _render_chart(self):
        """Redraw the candles and the reasoning for the index on screen.

        Cheap enough to run on every analysis pass (~1/sec): 70 candles is a
        few hundred canvas items. Skipped entirely while the Signal view is
        showing, so it costs nothing when you're not looking at it."""
        if self.view != "chart":
            return
        try:
            df = self._df_with_live_bar()
            rec = self.last_rec
            trade = self.active_trade
            if df is None or len(df) < 2:
                self.chart.clear("Press Start — candles load on the first fetch.")
                self.why.render(rec, trade)
                return
            # Read the timeframe off the candles themselves rather than
            # trusting a constant — the label can then never disagree with
            # what is actually drawn.
            tf = "?"
            try:
                gap = pd.Series(df.index).diff().dropna().median()
                mins = int(round(gap.total_seconds() / 60))
                tf = f"{mins}m" if mins < 1440 else "1d"
            except Exception:
                pass
            live = getattr(self, "streamer", None) is not None
            spot = self.live_spot or (rec.get("spot") if rec else None)
            bits = [str(tf)]
            if spot:
                bits.append(f"{spot:,.2f}")
            bits.append("live" if live else "not live")
            self.chart.show(df, rec, trade, self.current, "  ·  ".join(bits))
            self.why.render(rec, trade)
        except Exception as exc:
            # Deliberately NOT swallowed silently. A chart that quietly stops
            # updating looks identical to a flat market, which is exactly the
            # kind of failure that wastes a trading session before you notice.
            try:
                self.chart.clear(f"Chart error: {exc}")
            except Exception:
                pass

    def _paint_tabs(self):
        """Colour each tab by that index's live state, so you can see where
        the action is without switching to it."""
        for key, w in self.tab_widgets.items():
            st = self.states[key]
            rec = st["last_rec"]
            trade = st["active_trade"]
            selected = (key == self.current)

            if trade is not None and trade["status"] == "OPEN":
                colour = GREEN if trade["option_type"] == "CE" else RED
                sub = f"{trade['option_type']} open"
            elif rec is None:
                colour, sub = FG_MUTED, "—"
            elif rec.get("bias") == "BULLISH":
                colour, sub = GREEN, "BUY CE"
            elif rec.get("bias") == "BEARISH":
                colour, sub = RED, "BUY PE"
            elif rec.get("not_worth_it"):
                colour, sub = AMBER, "no room"
            else:
                colour = FG_MUTED
                spot = st["live_spot"] or rec.get("spot")
                sub = f"{spot:,.0f}" if spot else "wait"

            w["dot"].config(fg=colour)
            w["sub"].config(text=sub, fg=colour if colour != FG_MUTED else FG_MUTED)
            w["name"].config(fg=FG_PRIMARY if selected else FG_SECOND)
            w["frame"].config(highlightbackground=ACCENT if selected else BORDER,
                              highlightthickness=2 if selected else 1)

    def _render_current(self):
        """Repaint the whole window from the focused index's stored state."""
        # The DAY MOVE sparkline draws today's closes. Taken here rather than in
        # the drawing code so the line and the numbers beside it always come
        # from the same frame — a sparkline half a bar ahead of the price above
        # it is the kind of small lie that erodes trust in the whole screen.
        try:
            df = self._df_with_live_bar()
            if df is not None and len(df) > 2:
                closes = df["Close"].dropna()
                self.spark_values = [float(v) for v in closes.tail(120)]
        except Exception:
            pass
        rec = self.last_rec
        if rec is not None:
            self._render_trend(rec.get("trend"))
            self._render_live_side(rec)
            if self.report_text:
                self._set_text(self.report_text)
        trade = self.active_trade
        px = None
        if trade is not None:
            px = self._current_price_for(trade, rec)
        self._render_trade_tracker(current_price=px)
        self._render_chart()

    def _lock_new_trade(self, rec):
        """Called once, the moment a fresh CE/PE signal fires. Freezes that
        signal's entry + T1/T2/T3/SL as fixed numbers in self.active_trade —
        everything from here on checks live price against THESE frozen
        numbers, not whatever the report recalculates next cycle."""
        use_premium = rec.get("premium_source") == "live" and rec.get("live_ltp") is not None
        lot_size = config.INSTRUMENTS.get(rec["index"], {}).get("lot_size")
        self.active_trade = {
            "index": rec["index"],
            "option_type": rec["option_type"],
            "strike": rec["suggested_strike"],
            "entry_time": now_ist().strftime("%H:%M:%S"),
            # Kept as a real timestamp too, so the chart can mark the exact
            # candle the ticket was issued on.
            "entry_ts": now_ist(),
            # The reasoning FROZEN at this instant. Without this, opening the
            # Chart tab an hour later would explain what the market looks like
            # *then* — which is not the question. The question is always "why
            # did it fire?", and that answer must not drift.
            "why": explain.explain(rec),
            "entry_spot": rec["spot"],
            "entry_ltp": rec.get("live_ltp"),
            "use_premium": use_premium,          # track against real premium, or index-level fallback
            "lot_size": lot_size,                 # for the estimated rupee P&L column
            "lots": self._lots(),                  # frozen here on purpose — see _lots()
            # Which target ends this trade. Frozen for the same reason the
            # lots are: changing the setting mid-session must not rewrite where
            # a trade already running was supposed to get out.
            "exit_at": (getattr(config, "EXIT_AT_TARGET", "T3")
                        if getattr(config, "EXIT_AT_TARGET", "T3")
                        in ("T1", "T2", "T3") else "T3"),
            "index_targets": rec["index_targets"],
            "index_sl": rec["index_stop_loss"],
            "premium_targets": rec["premium_targets"],
            "premium_sl": rec["premium_stop_loss"],
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False,
            "sl_hit_time": None,
            "status": "OPEN",
            # Stamped so the OPEN and CLOSE rows in trades.csv pair up.
            "trade_id": f"{rec['index']}-{now_ist().strftime('%Y%m%d-%H%M%S')}",
        }
        # Written to disk immediately, not on close — if the tool dies
        # mid-trade there is still proof the signal happened.
        self._last_ticket_at = now_ist()
        # Stamped HERE, not at the call site: auto re-arm creates tickets too,
        # and when only _consider_signal stamped it, a re-armed ticket did not
        # extend the gap that is supposed to sit between tickets.
        self._last_ticket_any = time.time()
        trade_log.log_open(self.active_trade, rec, now_ist())
        # This ticket counts against the day's budget from right now, not from
        # whenever it happens to close.
        self._day_cache = None
        # A fresh ticket always shows ITS reasoning first, not the live read.
        try:
            self.why.mode.set("ticket")
        except Exception:
            pass
        self._render_trade_tracker(current_price=rec.get("live_ltp") if use_premium else rec.get("spot"))
        self._render_chart()
        self._subscribe_trade_token()

    def _df_with_live_bar(self):
        """The completed candles from REST, with the in-progress candle —
        assembled live from the tick stream — appended on the end.

        This is what makes the indicators live. Without it, EMA/MACD/RSI/ADX
        can only move when a whole 15-minute candle closes and gets fetched.
        With it they update continuously, the same way a charting platform
        redraws an indicator before the bar has finished."""
        df = self.base_df
        if df is None or df.empty:
            return None
        s = getattr(self, "streamer", None)
        if s is None or not self.index_token:
            return df
        bar = s.forming_bar(self.index_token)
        if not bar:
            return df
        try:
            ts = pd.Timestamp(bar["start"], unit="s", tz="UTC").tz_convert(df.index.tz or "Asia/Kolkata")
            # REST usually hands back the current (incomplete) candle too —
            # drop it so the streamed one replaces it rather than duplicating.
            if len(df) and df.index[-1] >= ts:
                df = df.iloc[:-1]
            if df.empty:
                return None
            # Indices carry no real volume, and VWAP treats 0 as "unknown"
            # and forward-fills. Carrying the previous bar's figure keeps the
            # forming bar behaving like every other bar rather than creating
            # a hole. It is an estimate, and only affects VWAP.
            vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0.0
            row = pd.DataFrame(
                {"Open": [bar["o"]], "High": [bar["h"]], "Low": [bar["l"]],
                 "Close": [bar["c"]], "Volume": [vol]}, index=[ts])
            return pd.concat([df, row])
        except Exception:
            return df

    def _live_analysis(self):
        """Recomputes the full read for EVERY index on its live candle, about
        once a second. ~15ms each, so all three cost ~45ms — still nothing.

        Only the index on screen gets rendered; the others update silently in
        the background so their tabs light up the moment they fire."""
        if getattr(self, "_in_analysis", False):
            return                       # nested inside a modal — see _poll_queue
        self._in_analysis = True
        try:
            if getattr(self, "streamer", None) is not None:
                prev_focus = self._focus
                try:
                    for key in self.indices:
                        self._focus = key
                        self._analyse_one(key)
                finally:
                    self._focus = prev_focus
                self._paint_tabs()
        except Exception:
            pass
        finally:
            self._in_analysis = False
            self.root.after(config.LIVE_ANALYSIS_MS, self._live_analysis)

    def _analyse_one(self, key):
        """One index's live recompute. Assumes focus is already on `key`."""
        if self.base_df is None:
            return
        df = self._df_with_live_bar()
        if df is None or len(df) < 60:
            return

        oi = self.base_oi
        if not isinstance(oi, dict) or "available" not in oi:
            oi = signal_engine.compute_option_chain_signal(None)

        tech = signal_engine.compute_technical_signal(df)
        spot = float(df["Close"].iloc[-1])
        step = config.INSTRUMENTS[key]["strike_step"]
        try:
            # ADX is already computed in tech — pass it rather than letting
            # reachability recompute it three times a second, once per index.
            reach = signal_engine.compute_reachability(spot, oi, df, now_ist(),
                                                        adx=tech.get("adx"))
        except Exception:
            reach = None
        rec = signal_engine.build_recommendation(key, tech, oi, step, reach=reach)
        try:
            rec["trend"] = signal_engine.compute_market_trend(df)
        except Exception:
            rec["trend"] = None

        self.last_rec = rec
        self._consider_signal(rec)

        if key == self.current:
            self._render_trend(rec.get("trend"))
            self._render_live_side(rec)
            if self.active_trade is None:
                self._render_trade_tracker(current_price=None)
            self._render_chart()

    def _consider_signal(self, rec, confirm_now=False):
        """Decide whether to issue a ticket for the index currently in focus.

        Shared by the live tick path and the REST path so both obey the same
        debounce rule. confirm_now short-circuits it for free mode, where a
        60-second REST cycle is the only opportunity there will be."""
        direction = (rec["bias"], rec["option_type"]) if rec["bias"] != "NEUTRAL" else None
        now = time.time()
        if direction == self._confirm_dir:
            self._confirm_streak += 1
        else:
            self._confirm_dir = direction
            self._confirm_streak = 1
            self._confirm_since = now

        def hold(code, short, why):
            """Decline, and record WHY so the screen can say it."""
            self._wait_reason = (code, short, why)
            return False

        if direction is None:
            return hold("neutral", "NO SIGNAL",
                        "The indicators do not agree on a direction yet.")

        # --- 1. the direction has to HOLD, measured in seconds -------------
        # Counting evaluations meant counting the tick rate: four of them at
        # 250ms is one second. Seconds are what "it held" actually means.
        if not confirm_now:
            need_s = getattr(config, "SIGNAL_CONFIRM_SECONDS", 0)
            if need_s and (now - (self._confirm_since or now)) < need_s:
                left = int(need_s - (now - (self._confirm_since or now)))
                return hold("confirming", "CONFIRMING",
                            f"The direction has to hold for {need_s}s before a "
                            f"ticket is issued — about {max(1, left)}s to go.")
            if self._confirm_streak < config.SIGNAL_CONFIRM_TICKS:
                return hold("confirming", "CONFIRMING",
                            f"Waiting for {config.SIGNAL_CONFIRM_TICKS} "
                            f"agreeing readings — {self._confirm_streak} so far.")

        if direction == self.last_bias_signature and not self._reentry_allowed():
            if self.reentry_var.get():
                mins = config.REENTRY_COOLDOWN_MIN
                return hold("reentry_cooldown", "COOLDOWN",
                            f"This index was already ticketed {direction[1]} today. "
                            f"Re-entry in the same direction needs {mins} minutes "
                            f"between tickets.")
            return hold("same_direction", "ALREADY TAKEN",
                        f"This index was already ticketed {direction[1]} today, and "
                        f"'Re-enter same direction' is off — so the next ticket "
                        f"here waits for the direction to change.")

        # --- 2. a floor under the gap between tickets, whichever way --------
        gap = getattr(config, "MIN_MINUTES_BETWEEN_TICKETS", 0)
        per_index = getattr(config, "TICKET_GAP_PER_INDEX", False)
        last_any = (self._last_ticket_epoch() if per_index
                    else getattr(self, "_last_ticket_any", None))
        if gap and last_any is not None and (now - last_any) < gap * 60:
            left = int((gap * 60 - (now - last_any)) / 60) + 1
            scope = "on this index" if per_index else "across all indices"
            return hold("ticket_gap", "COOLDOWN",
                        f"A minimum of {gap} minutes between tickets {scope} — "
                        f"about {left} more minute{'s' if left != 1 else ''}.")

        # --- 3. a change of mind does not close a running trade -------------
        # Two trades and two sets of costs for one change of opinion, dozens
        # of times a day. The levels were frozen at entry so this position
        # could be left alone to find its own target or its own stop.
        if (self.active_trade is not None
                and self.active_trade["status"] == "OPEN"
                and not getattr(config, "CLOSE_ON_SIGNAL_FLIP", False)):
            return hold("position_open", "POSITION OPEN",
                        "A ticket is already running on this index. It is left "
                        "alone to find its own target or stop.")

        # The daily brake. Checked HERE rather than earlier so the confirm
        # streak and the direction signature still advance — otherwise the
        # first trade after the block lifts would fire on a stale streak.
        block = self.entry_block()
        if block is not None:
            self._entry_block = block
            # Deliberately NOT stamping last_bias_signature. Doing so marked
            # this direction as "already ticketed", and because same-direction
            # re-entry is off by default, the signal could then never be taken
            # once the block lifted — a signal that appeared at 09:17 locked
            # the index out for the entire day. Restart the confirmation
            # instead, so the direction has to hold again after the block goes.
            self._confirm_since = now
            self._confirm_streak = 1
            # entry_block() already returns (code, short, long) — the same
            # shape hold() files — so the daily brake explains itself in the
            # same words the ticket prints for every other rule.
            return hold(*block)
        self._entry_block = None

        if self.active_trade is not None and self.active_trade["status"] == "OPEN":
            px = self._current_price_for(self.active_trade, rec)
            self.active_trade["status"] = "CLOSED — signal changed before target/SL was hit"
            self._log_trade_closed(self.active_trade, px)
        self.last_bias_signature = direction
        self._wait_reason = None
        self._lock_new_trade(rec)
        if self.popup_var.get():
            self._show_signal_popup(rec)
        return True

    def _day_stats(self):
        """(tickets, T3 wins, stop-outs) for today, cached.

        entry_block() is consulted on every repaint, and this reads a file;
        the cache is dropped the instant a ticket opens or closes, so it can
        never be stale about the thing it gates."""
        now = time.time()
        c = getattr(self, "_day_cache", None)
        if c is None or now - c[0] > 3.0:
            try:
                # TODAY explicitly. Left to its own default the log answers
                # about the newest date it contains, which on a fresh morning
                # is YESTERDAY — and yesterday's two winners would lock you
                # out before the bell.
                vals = trade_log.day_counts(now_ist().strftime("%Y-%m-%d"))
            except Exception:
                vals = (None, None, None)
            self._day_cache = (now, vals)
            return vals
        return c[1]

    def entry_block(self):
        """Why a new ticket may not be issued right now — or None if it may.

        Deliberately separate from the signal itself: the analysis keeps
        running, the signal keeps being shown and explained, and only the act
        of taking it is held. Seeing the trade you did not take is how you
        learn whether the limit is helping or costing you.

        Returns (code, short, long) or None.
        """
        now = now_ist()

        # --- the opening auction is still settling --------------------------
        cut = getattr(config, "NO_NEW_TRADES_BEFORE", None)
        if cut:
            if (now.hour, now.minute) < (cut[0], cut[1]):
                return ("early", "MARKET OPENING",
                        f"No entries before {cut[0]:02d}:{cut[1]:02d} IST — the opening "
                        f"auction is still settling and the first candle barely exists.\n"
                        f"The signal is live; only the entry is held.")

        # --- daily budgets, read off the log so a restart cannot reset them --
        # Off by default: the caps answer "have I traded enough today?", which
        # is a question about discipline, not about whether THIS trade is any
        # good. The gates that judge the trade itself — chain data, room to
        # run, the opening window — are above and are not switchable here.
        try:
            if not self.limits_var.get():
                return None
        except Exception:
            return None

        issued, wins, stops = self._day_stats()
        if issued is None:
            return None

        cap = getattr(config, "DAILY_TARGET_WINS", 0)
        if cap and wins >= cap:
            return ("won", "DAY DONE",
                    f"{wins} trade{'s' if wins != 1 else ''} ran all the way to T3 today "
                    f"— that is the daily target.\nNo new entries: the signal is still "
                    f"shown, but a good day is not worth handing back.")

        cap = getattr(config, "MAX_TRADES_PER_DAY", 0)
        if cap and issued >= cap:
            return ("maxed", "DAY LIMIT",
                    f"{issued} tickets issued today, which is the daily maximum.\n"
                    f"No new entries. The signal is still shown and logged.")
        return None

    def _last_ticket_epoch(self):
        """When THIS index last issued a ticket, as a time.time() stamp.

        `_last_ticket_at` is a datetime and per-index; `_last_ticket_any` is a
        float and global. The gap rule needs one or the other depending on the
        setting, so the conversion lives here rather than at the call site.
        """
        last = self._last_ticket_at
        if last is None:
            return None
        return time.time() - (now_ist() - last).total_seconds()

    def _reentry_allowed(self):
        """May we issue a SECOND ticket in the direction we already ticketed?

        Only with re-entry switched on, only when nothing is open, and only
        after a cooldown. Without the cooldown this would fire once a second
        for as long as the trend lasted — see the note in config.py.
        """
        if not self.reentry_var.get():
            return False
        trade = self.active_trade
        if trade is not None and trade["status"] == "OPEN":
            return False          # one position at a time
        last = self._last_ticket_at
        if last is None:
            return True
        gap_min = (now_ist() - last).total_seconds() / 60.0
        return gap_min >= config.REENTRY_COOLDOWN_MIN

    def _track_open_trade(self, rec):
        """Check the focused index's open ticket against this snapshot, and
        auto re-arm if it closed naturally and the direction still holds."""
        trade = self.active_trade
        if trade is None or trade["status"] != "OPEN":
            return
        self._check_trade_price(self._current_price_for(trade, rec))
        closed = self.active_trade is not None and self.active_trade["status"] != "OPEN"
        natural = closed and (trade_log.is_target_close(self.active_trade["status"])
                              or "stop-loss hit" in self.active_trade["status"])
        if natural and self.auto_rearm_var.get() and rec.get("bias") != "NEUTRAL":
            self._rearm(rec)

    def _rearm(self, rec):
        """Re-arm after a trade closed naturally — through the SAME gates as
        any other entry.

        This used to call _lock_new_trade directly, which meant it answered to
        nothing: not the daily cap, not the 20-minute gap, not the opening
        window. A ticket whose stop was already breached would close on the
        next evaluation and re-arm instantly, four times a second, until the
        option chain refreshed. Thirteen tickets in twelve seconds against a
        cap of four, measured.
        """
        if self.entry_block() is not None:
            return False
        last = getattr(self, "_last_ticket_any", None)
        gap_s = getattr(config, "MIN_MINUTES_BETWEEN_TICKETS", 0) * 60
        # The churn brake if one is set, and ALWAYS the anti-runaway floor.
        # With the brake at 0 and the daily limits switch off, the floor is the
        # only thing between a close and the next open, and re-arm skips the
        # confirmation window by design — so taking the larger of the two is
        # what stops the machine-gun, not a nicety.
        gap_s = max(gap_s, getattr(config, "REARM_MIN_SECONDS", 60))
        if last is not None and (time.time() - last) < gap_s:
            return False
        self._lock_new_trade(rec)
        return True

    def _subscribe_trade_token(self):
        """Once a ticket is issued, add THAT option contract to the live feed
        so its premium streams tick by tick like the index does."""
        self.trade_token = None
        s = getattr(self, "streamer", None)
        trade = self.active_trade
        if s is None or trade is None or not trade.get("use_premium"):
            return
        try:
            expiry = None
            if self.last_rec:
                oi = self.last_rec.get("option_chain") or {}
                expiry = oi.get("expiry")
            tok = self.provider.option_token(
                trade["index"], trade["strike"], trade["option_type"], expiry)
            if tok:
                self.trade_token = tok
                s.subscribe([tok])
        except Exception:
            pass

    def _save_summary(self, silent=False):
        """Write a readable session report next to the trade log. Also fires
        automatically once when the market closes, so an overnight run leaves
        something to read in the morning without you being awake for it."""
        try:
            path = trade_log.save_summary()
        except Exception as e:
            if not silent:
                self.status_label.config(text=f"Could not save summary: {e}")
            return None
        if path:
            self.status_label.config(text=f"Session summary saved: {path}")
            if not silent:
                try:
                    messagebox.showinfo("Session summary",
                                        trade_log.build_summary()[:3000])
                except Exception:
                    pass
        elif not silent:
            self.status_label.config(text="Nothing to summarise yet — no closed trades.")
        return path

    def _open_market_map(self):
        """Heat map of the index's constituent stocks. Opens in its own
        window so it has room to breathe without squeezing the ticket."""
        if self.map_window is not None:
            try:
                if self.map_window.winfo_exists():
                    self.map_window.lift()
                    return
            except Exception:
                pass
            self.map_window = None
        if getattr(self, "streamer", None) is None or self.provider is None:
            self.status_label.config(
                text="Market Map needs the live feed — press Start in kite mode first.")
            return
        try:
            self.map_window = MarketMapWindow(
                self.root, self.streamer, self.provider, self.index_var.get())
        except Exception as e:
            self.map_window = None
            self.status_label.config(text=f"Could not open Market Map: {e}")

    def _close_market_map(self):
        if self.map_window is not None:
            try:
                self.map_window.close()
            except Exception:
                pass
            self.map_window = None

    def _stop_streamer(self):
        if getattr(self, "streamer", None) is not None:
            try:
                self.streamer.stop()
            except Exception:
                pass
        self.streamer = None

    def _live_tick(self):
        if getattr(self, "_in_tick", False):
            return                       # nested inside a modal — see _poll_queue
        """~4x a second. Reads streamed prices out of memory for ALL indices
        — no network — so every open ticket is checked against live price,
        not just the one you happen to be looking at."""
        self._in_tick = True
        try:
            s = getattr(self, "streamer", None)
            if s is not None:
                prev_focus = self._focus
                try:
                    for key in self.indices:
                        self._focus = key
                        self._tick_one(s)
                finally:
                    self._focus = prev_focus
                self._update_live_badge(s)
                self._paint_tabs()
        except Exception:
            # The live loop must never be able to kill the window.
            pass
        finally:
            self._in_tick = False
        self.root.after(config.LIVE_UI_MS, self._live_tick)

    def _tick_one(self, s):
        """One index's live price update. Assumes focus is already set."""
        if not self.index_token:
            return
        spot = s.price(self.index_token)
        if spot is not None:
            self.live_spot = spot
            if self._focus == self.current:
                self.stat_labels["spot"].config(text=f"{spot:,.2f}", fg=FG_PRIMARY)

        trade = self.active_trade
        if trade is None or trade["status"] != "OPEN":
            return
        px = s.price(self.trade_token) if trade["use_premium"] else spot
        if px is None:
            return
        # _check_trade_price raises the alert, renders and logs by itself.
        self._check_trade_price(px)
        closed = self.active_trade is not None and self.active_trade["status"] != "OPEN"
        natural = closed and (trade_log.is_target_close(self.active_trade["status"])
                              or "stop-loss hit" in self.active_trade["status"])
        if natural and self.auto_rearm_var.get() \
                and self.last_rec and self.last_rec.get("bias") != "NEUTRAL":
            self._lock_new_trade(self.last_rec)
            self._subscribe_trade_token()

    def _update_live_badge(self, s):
        """Feed health — a socket that has quietly gone silent must not keep
        claiming to be LIVE."""
        age = s.age_seconds()
        if not s.connected:
            self.live_label.config(text="◌ reconnecting", fg=AMBER)
        elif age is None:
            self.live_label.config(text="◉ LIVE  waiting", fg=FG_SECOND)
        elif age > 20:
            self.live_label.config(text=f"◌ stale {age:.0f}s", fg=AMBER)
        else:
            self.live_label.config(text="◉ LIVE", fg=GREEN)

    def _display_spot(self):
        """Newest spot we have. The streamed tick is fresher than the last
        analysis snapshot, so it wins — without this the render would keep
        stamping the stale REST value back over the live one."""
        if self.live_spot is not None:
            return self.live_spot
        return (self.last_rec or {}).get("spot")

    def portfolio(self):
        """Today's money, all three indices in one place.

        Two halves, deliberately kept apart:
          BOOKED — closed trades, read back off trades.csv so a restart at
                   lunchtime does not zero the morning.
          OPEN   — live unrealised P&L on whatever is still running, priced
                   from the same feed the ticket is tracked on.

        Returns a dict the screen can render without doing any arithmetic of
        its own.
        """
        # The booked half comes off disk, and this runs on every repaint — up
        # to fourteen times a second. Cache it for a few seconds and drop the
        # cache the moment a trade closes, so the number is never stale in the
        # only way that matters.
        now = time.time()
        cache = getattr(self, "_booked_cache", None)
        if cache is None or now - cache[0] > 3.0:
            try:
                # Same reasoning as the daily counts: "TODAY" on the screen
                # has to mean today, not the last day that happened to trade.
                booked, n_closed = trade_log.booked_today(
                    now_ist().strftime("%Y-%m-%d"))
            except Exception:
                booked, n_closed = {}, 0
            self._booked_cache = (now, booked, n_closed)
        else:
            _, booked, n_closed = cache

        rows, open_total, n_open = [], 0.0, 0
        prev_focus = self._focus
        try:
            for key in self.indices:
                self._focus = key
                trade = self.active_trade
                live = None
                if trade is not None and trade["status"] == "OPEN":
                    n_open += 1
                    px = self._current_price_for(trade, self.last_rec)
                    entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
                    if (trade["use_premium"] and trade.get("lot_size")
                            and px is not None and entry is not None):
                        live = round((px - entry) * trade["lot_size"]
                                     * self._lots(trade), 2)
                        open_total += live
                rows.append({
                    "index": key,
                    "booked": booked.get(key),
                    "open": live,
                    # An index with a running ticket but no live premium can
                    # show a position without a number — saying "in a trade,
                    # value unknown" beats implying it is flat at zero.
                    "in_trade": trade is not None and trade["status"] == "OPEN",
                    "total": round((booked.get(key) or 0.0) + (live or 0.0), 2)
                             if (booked.get(key) is not None or live is not None) else None,
                })
        finally:
            self._focus = prev_focus

        booked_total = round(sum(v for v in booked.values()), 2) if booked else 0.0
        return {
            "rows": rows,
            "booked": booked_total,
            "open": round(open_total, 2),
            "net": round(booked_total + open_total, 2),
            "closed_count": n_closed,
            "open_count": n_open,
            "any": bool(booked) or n_open > 0,
        }

    def _current_price_for(self, trade, rec):
        """Current price of the LOCKED contract — deliberately looked up by
        the trade's own frozen strike, NOT rec['live_ltp'] (which reflects
        whatever strike happens to be suggested right now, possibly a
        different contract). Used both for live tracking and so a trade
        closing for ANY reason can still be logged with a real P&L."""
        if trade is None or rec is None:
            return None
        if trade["use_premium"]:
            oi = rec.get("option_chain")
            if not oi:
                return None
            return signal_engine._find_strike_ltp(oi, trade["strike"], trade["option_type"])
        return rec.get("spot")

    def _update_active_trade(self, rec):
        """Analysis-cycle check — prices from the latest REST snapshot."""
        trade = self.active_trade
        if trade is None or trade["status"] != "OPEN":
            return
        self._check_trade_price(self._current_price_for(trade, rec))

    def _check_trade_price(self, current_price):
        """Check one price against the trade's FROZEN targets/SL.

        Shared by the slow analysis cycle and the live tick feed, so a target
        registers the INSTANT the price touches it rather than whenever the
        next poll happens to land. Deliberately independent of whatever the
        live signal says now — an in-progress trade keeps being tracked
        correctly even after the signal itself has moved on."""
        trade = self.active_trade
        if trade is None or trade["status"] != "OPEN":
            return

        option_type = trade["option_type"]

        if trade["use_premium"]:
            targets = trade["premium_targets"]
            sl = trade["premium_sl"]
            # Premium targets are always "premium rising = good" regardless
            # of CE or PE (that's how build_recommendation computes them).
            hit_target = lambda v, t: v is not None and t is not None and v >= t
            hit_sl = lambda v: v is not None and sl is not None and v <= sl
        else:
            targets = trade["index_targets"]
            sl = trade["index_sl"]
            if option_type == "CE":
                hit_target = lambda v, t: v is not None and t is not None and v >= t
                hit_sl = lambda v: v is not None and sl is not None and v <= sl
            else:  # PE — index falling is the favourable direction
                hit_target = lambda v, t: v is not None and t is not None and v <= t
                hit_sl = lambda v: v is not None and sl is not None and v >= sl

        newly_hit = []
        for idx, key in enumerate(["T1", "T2", "T3"]):
            if not trade["hit"][key] and hit_target(current_price, targets[idx]):
                trade["hit"][key] = True
                trade["hit_time"][key] = now_ist().strftime("%H:%M:%S")
                newly_hit.append(key)

        sl_newly_hit = not trade["sl_hit"] and hit_sl(current_price)
        if sl_newly_hit:
            trade["sl_hit"] = True
            trade["sl_hit_time"] = now_ist().strftime("%H:%M:%S")

        # Which target ends the trade. The ticket freezes this at entry, the
        # same way it freezes the levels and the lots: moving the setting at
        # lunchtime must not retroactively change where the morning's trade was
        # supposed to exit.
        exit_key = trade.get("exit_at") or getattr(config, "EXIT_AT_TARGET", "T3")
        if exit_key not in ("T1", "T2", "T3"):
            exit_key = "T3"
        if trade["hit"][exit_key]:
            trade["status"] = f"CLOSED — {exit_key} hit (full target reached)"
        elif trade["sl_hit"]:
            trade["status"] = "CLOSED — stop-loss hit"

        self._render_trade_tracker(current_price=current_price)

        if newly_hit or sl_newly_hit:
            self.root.bell()  # audible cue so you don't have to be staring at the screen

        if self.popup_var.get():
            kind = "Premium" if trade["use_premium"] else "Index"
            for key in newly_hit:
                extra = "\n\nAll 3 targets reached — trade complete." if key == "T3" else \
                        "\n\nConsider booking partial profit / trailing your stop-loss."
                messagebox.showinfo(
                    f"{key} HIT — {trade['index']} {trade['strike']} {option_type}",
                    f"Target {key} reached!\n\n{kind} now at {current_price}."
                    f"{extra}\n\nEntered {trade['entry_time']} IST.",
                )
            if sl_newly_hit:
                messagebox.showwarning(
                    f"STOP-LOSS HIT — {trade['index']} {trade['strike']} {option_type}",
                    f"Stop-loss reached — {kind} now at {current_price}.\n\n"
                    f"This trade is now marked closed in the tracker.\n\n"
                    f"Entered {trade['entry_time']} IST.",
                )

        if trade["status"] != "OPEN":
            self._log_trade_closed(trade, current_price)

    def _close_open_at_bell(self):
        """Close every index's open ticket at the close, and log it.

        An intraday position does not survive the session, so leaving one
        marked OPEN is not caution — it is a missing row. Logged at the last
        price we have, with a status that says plainly why it closed."""
        prev_focus = self._focus
        closed = 0
        try:
            for key in self.indices:
                self._focus = key
                trade = self.active_trade
                if trade is None or trade["status"] != "OPEN":
                    continue
                try:
                    px = self._current_price_for(trade, self.last_rec)
                    trade["status"] = "CLOSED — market closed with the trade still open"
                    self._log_trade_closed(trade, px)
                    closed += 1
                except Exception:
                    # One unloggable ticket must not stop the others being
                    # squared up.
                    trade["status"] = "CLOSED — market closed with the trade still open"
        finally:
            self._focus = prev_focus
        if closed:
            self.status_label.config(
                text=f"Market closed — squared up {closed} open "
                     f"ticket{'s' if closed != 1 else ''} and logged {'them' if closed != 1 else 'it'}.")
        return closed

    def _clear_active_trade(self):
        """Manually reset the tracker without needing to Stop/Start the
        whole tool — e.g. if you've decided not to take a signal and don't
        want it lingering on screen as OPEN."""
        if self.active_trade is not None and self.active_trade["status"] == "OPEN":
            close_px = self._current_price_for(self.active_trade, self.last_rec)
            self.active_trade["status"] = "CLOSED — cleared manually"
            self._log_trade_closed(self.active_trade, close_px)
        self.active_trade = None
        self._render_trade_tracker(current_price=None)
        # The chart's level lines and the WHY panel both hang off the ticket,
        # so they have to drop back to the live read with it.
        self._render_chart()

    def _log_trade_closed(self, trade, current_price):
        """Appends one line to the session trade history panel — called
        whenever a locked trade stops being OPEN (T3 hit, SL hit, replaced
        by a genuine new-direction signal, or manually cleared)."""
        self.trade_history_count += 1
        entry_val = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]

        pnl_str = ""
        tag = "neutral"
        realized = None
        if trade["use_premium"] and trade["lot_size"] and current_price is not None and entry_val is not None:
            pnl = round((current_price - entry_val) * trade["lot_size"]
                        * self._lots(trade), 2)
            realized = pnl
            n = self._lots(trade)
            pnl_str = (f"  |  Est. P&L: {'+' if pnl >= 0 else '-'}Rs.{abs(pnl):,.0f}"
                       f" ({n} lot{'s' if n != 1 else ''})")
            tag = "win" if pnl > 0 else ("loss" if pnl < 0 else "neutral")
        elif "T3" in trade["status"]:
            tag = "win"
        elif "stop-loss" in trade["status"]:
            tag = "loss"

        line = (
            f"#{self.trade_history_count}  {trade['entry_time']} IST  "
            f"{trade['index']} {trade['strike']} {trade['option_type']}  @ {entry_val}  "
            f"-> {trade['status']}{pnl_str}\n"
        )
        self.history_text.config(state="normal")
        self.history_text.insert("1.0", line, tag)   # newest at top
        self.history_text.config(state="disabled")

        # The permanent record — this is what survives closing the window.
        trade_log.log_close(trade, self.last_rec, now_ist(), current_price, realized)
        self._booked_cache = None      # the day's total just changed
        self._day_cache = None         # and so did the day's counts

        # Running session total, shown on the right of the SESSION strip.
        if realized is not None:
            self.session_net += realized
            net = self.session_net
            self.net_label.config(
                text=f"Net {'+' if net >= 0 else '-'}Rs.{abs(net):,.0f}",
                fg=GREEN if net > 0 else (RED if net < 0 else FG_SECOND),
            )

    def _render_trade_tracker(self, current_price):
        """Renders the ticket's left stub. When a trade is locked in it shows
        those FROZEN numbers; otherwise it shows the current live signal as a
        greyed-out preview (or an empty ticket if there's nothing yet)."""
        if self._focus != self.current:
            return
        trade = self.active_trade
        rec = self.last_rec
        # How far price has travelled from entry towards each level, 0..1.
        # Nothing is locked in during a preview, so there is no journey to show.
        self.level_progress = {}
        # A preview quotes the selector; a locked ticket quotes its own frozen
        # count, set further down.
        self.pnl_caption = self._lot_caption()
        # What the ladder needs beyond the levels themselves.
        self.ladder_entry = None
        self.ladder_now = None
        self.ladder_rr = None

        # ---------------- no locked ticket: preview / empty state -----------
        if trade is None:
            spot = self._display_spot()
            if rec is not None and rec.get("bias") != "NEUTRAL":
                use_prem = rec.get("premium_source") == "live"
                targets = rec["premium_targets"] if use_prem else rec["index_targets"]
                sl = rec["premium_stop_loss"] if use_prem else rec["index_stop_loss"]
                self.bias_label.config(text=f"BUY {rec['option_type']}",
                                        fg=BIAS_COLORS.get(rec["bias"], FG_SECOND))
                block = self.entry_block()
                if block is not None:
                    # The signal is real and is shown in full — levels, targets,
                    # reasoning. Only the entry is held. Saying "PREVIEW" here
                    # would imply it is about to fire, which is the opposite of
                    # what is happening.
                    code, short, why = block
                    self.ticket_no_label.config(text="SIGNAL TICKET  ·  NOT TAKEN")
                    self._set_badge(short, AMBER)
                    self.trade_header_label.config(
                        text=f"{rec['index']} {rec['suggested_strike']} "
                             f"{rec['option_type']} — signal is live, entry is held.\n{why}",
                        fg=AMBER)
                else:
                    # Say WHICH rule is holding the entry, not a guess. The old
                    # line asserted "a ticket is issued when the direction
                    # changes" whatever the reason — so a signal waiting out a
                    # 20-minute cooldown, or one still confirming, read as
                    # though the tool disagreed with a screen full of agreement.
                    wait = getattr(self, "_wait_reason", None)
                    if wait is not None and wait[0] != "neutral":
                        _, short, why = wait
                        self.ticket_no_label.config(
                            text="SIGNAL TICKET  ·  WAITING")
                        self._set_badge(short, AMBER)
                        self.trade_header_label.config(
                            text=f"{rec['index']} {rec['suggested_strike']} "
                                 f"{rec['option_type']} — signal is live, entry is "
                                 f"waiting.\n{why}",
                            fg=AMBER)
                    else:
                        self.ticket_no_label.config(text="SIGNAL TICKET  ·  PREVIEW")
                        self._set_badge("PREVIEW", AMBER)
                        self.trade_header_label.config(
                            text=f"{rec['index']} {rec['suggested_strike']} "
                                 f"{rec['option_type']} — evaluating. A ticket is "
                                 f"issued the moment every rule passes.",
                            fg=FG_SECOND)
                self._fill_levels(targets, sl, preview=True)
                self._set_stats(spot=spot)
                self.ladder_entry = (rec.get("live_ltp") if use_prem
                                     else rec.get("spot"))
                self.ladder_rr = self._reward_risk(targets, sl, self.ladder_entry)
            elif rec is not None and rec.get("not_worth_it"):
                # The setup was real, but there isn't enough room left for the
                # move to be worth the risk — say so plainly rather than
                # showing an empty ticket that looks like nothing happened.
                self.ticket_no_label.config(text="SIGNAL TICKET  ·  REJECTED")
                if rec.get("blocked_reason") == "no_chain":
                    # A different refusal from "no room", and it must not
                    # borrow that wording — the old text would have read
                    # "can only travel ~None pts", which says nothing true.
                    self._set_badge("NO CHAIN", AMBER)
                    self.bias_label.config(text="ON HOLD", fg=AMBER)
                    self.trade_header_label.config(
                        text=f"{rec['raw_bias'].title()} setup is real, but the option chain "
                             f"isn't available right now, so the room-to-run check can't run.\n"
                             f"No ticket is issued on a target nobody has checked. "
                             f"This usually clears within a cycle or two.",
                        fg=AMBER)
                else:
                    self._set_badge("NO ROOM", RED)
                    self.bias_label.config(text="NOT WORTH IT", fg=RED)
                    self.trade_header_label.config(
                        text=f"{rec['raw_bias'].title()} setup is real, but the market can only travel "
                             f"~{rec.get('reach_points')} pts from here while the stop is "
                             f"{rec.get('risk_points')} pts away.\nCapped by {rec.get('reach_reason')}.",
                        fg=AMBER)
                self._fill_levels([None, None, None], None, preview=True)
                self._set_stats(spot=spot)
            else:
                self.ticket_no_label.config(text="SIGNAL TICKET")
                self._set_badge("WAITING", FG_MUTED)
                self.bias_label.config(text="NO SIGNAL YET", fg=FG_SECOND)
                # Say WHY there's no trade. A blank waiting state is
                # indistinguishable from a broken tool, and gives you no
                # sense of how close the market is to qualifying.
                if rec is not None and rec.get("blockers"):
                    why = "\n".join(f"• {b}" for b in rec["blockers"])
                    self.trade_header_label.config(
                        text=f"Waiting because:\n{why}", fg=FG_SECOND)
                else:
                    self.trade_header_label.config(
                        text="No trade locked in — a ticket is issued automatically on the next CE/PE signal.",
                        fg=FG_SECOND)
                self._fill_levels([None, None, None], None, preview=True)
                self._set_stats(spot=spot)
            self.issued_label.config(text="")
            return

        # ---------------------- a ticket is locked in ----------------------
        use_prem = trade["use_premium"]
        targets = trade["premium_targets"] if use_prem else trade["index_targets"]
        sl = trade["premium_sl"] if use_prem else trade["index_sl"]
        entry_val = trade["entry_ltp"] if use_prem else trade["entry_spot"]
        lot_size = trade.get("lot_size")

        status = trade["status"]
        if status == "OPEN":
            self._set_badge("OPEN", GREEN)
        elif trade_log.is_target_close(status):
            self._set_badge("TARGET HIT", GREEN)
        elif "stop-loss" in status:
            self._set_badge("STOPPED OUT", RED)
        else:
            self._set_badge("CLOSED", FG_MUTED)

        self.ticket_no_label.config(text=f"SIGNAL TICKET  ·  #{self.trade_history_count + 1:04d}")
        self.bias_label.config(text=f"BUY {trade['option_type']}",
                                fg=GREEN if trade["option_type"] == "CE" else RED)
        kind = "live premium" if use_prem else "index-level (no live premium at entry)"
        self.trade_header_label.config(
            text=f"{trade['index']} {trade['strike']} {trade['option_type']} · tracked on {kind}"
                 f"\n{status}",
            fg=FG_PRIMARY if status == "OPEN" else (GREEN if trade_log.is_target_close(status) else
                                                     RED if "stop-loss" in status else FG_SECOND))

        # Rupee P&L at the CURRENT price (only meaningful on live premium).
        pnl = pnl_color = None
        lots = self._lots(trade)
        if use_prem and lot_size and current_price is not None and entry_val is not None:
            pnl = round((current_price - entry_val) * lot_size * lots, 2)
            pnl_color = GREEN if pnl > 0 else (RED if pnl < 0 else FG_SECOND)

        self._set_stats(entry=entry_val, now=current_price,
                        spot=self._display_spot(), pnl=pnl, pnl_color=pnl_color)

        # Distance covered from entry to each level. Written as a ratio rather
        # than a subtraction so it reads the same for a CE (price rising to the
        # target) and a PE tracked on the index (price falling to it) — both
        # halves flip sign together, so the fraction stays positive.
        if current_price is not None and entry_val is not None:
            for key, level in (("T1", targets[0]), ("T2", targets[1]),
                               ("T3", targets[2]), ("SL", sl)):
                if level is None or level == entry_val:
                    continue
                p = (current_price - entry_val) / (level - entry_val)
                self.level_progress[key] = max(0.0, min(1.0, p))
        # A level that has actually been touched is finished, whatever the
        # price has done since — the bar must not creep backwards once hit.
        for key in ("T1", "T2", "T3"):
            if trade["hit"].get(key):
                self.level_progress[key] = 1.0
        if trade["sl_hit"]:
            self.level_progress["SL"] = 1.0
        self._fill_levels(targets, sl, trade=trade,
                           entry_ltp=(trade["entry_ltp"] if use_prem else None),
                           lot_size=(lot_size if use_prem else None), lots=lots)
        self.pnl_caption = self._lot_caption(trade)
        self.ladder_entry = entry_val
        self.ladder_now = current_price
        self.ladder_rr = self._reward_risk(targets, sl, entry_val)
        self.issued_label.config(
            text=f"Issued {trade['entry_time']} IST\nLevels frozen at entry")

    # -------------------------------------------------------------- control
    def _start(self):
        # The Start button is painted on a canvas and has no disabled state, so
        # nothing stopped a second click. A second run overwrote
        # current_stop_event, orphaning the first thread's event — nothing could
        # ever stop it, and two workers then fetched, queued and confirmed
        # signals in parallel at double the intended rate.
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_label.config(text="Already running — press Stop first.")
            return

        # Clear EVERY index before anything else can fail. These are per-index
        # properties resolving through self._focus, so the old code cleared
        # only the index on screen; the other two resumed carrying a ticket
        # frozen in the previous run — still tracked, and eventually logged
        # with a P&L for a position abandoned hours earlier.
        # Done up here rather than after the provider is built, so a start that
        # fails on credentials does not leave yesterday's tickets running.
        prev_focus = self._focus
        try:
            for key in self.indices:
                self._focus = key
                if self.active_trade is not None and self.active_trade["status"] == "OPEN":
                    # Logging must not be able to abort the cleanup. A single
                    # malformed ticket would otherwise leave the other indices
                    # still carrying stale positions — the exact bug this loop
                    # exists to prevent.
                    try:
                        px = self._current_price_for(self.active_trade, self.last_rec)
                        self.active_trade["status"] = "CLOSED — tool restarted"
                        self._log_trade_closed(self.active_trade, px)
                    except Exception:
                        pass
                self.active_trade = None
                self.last_rec = None
                self.last_signature = None
                self.last_bias_signature = None
                self._confirm_dir = None
                self._confirm_streak = 0
                self._confirm_since = None
        finally:
            self._focus = prev_focus

        mode = self.mode_var.get()
        refresh = (config.ANALYSIS_INTERVAL_SEC if mode == "kite"
                   else config.ANALYSIS_INTERVAL_FREE_SEC)

        # Catch the dead-token case BEFORE trying to start, and offer to fix
        # it right here. Otherwise the usual sequence is: press Start, get an
        # error, go find the login helper, come back — at 10pm, every night.
        if mode == "kite" and getattr(self, "_token_state", None) in ("missing", "expired"):
            if messagebox.askyesno(
                    "Zerodha login needed",
                    f"{getattr(self, '_token_detail', 'No valid token.')}\n\n"
                    f"Zerodha clears access tokens every morning, so this is normal and "
                    f"has to be redone each trading day.\n\nLog in now?"):
                self._kite_login()
                return

        try:
            self.provider = get_provider(mode)
        except MissingKiteCredentials as e:
            # Show the full explanation in a dialog — the old behaviour just
            # printed to the terminal, which is invisible if you launched the
            # app by double-clicking, and easy to miss otherwise.
            self.status_label.config(
                text="Kite credentials missing or expired — see the dialog. "
                     "Switch Mode to 'free' to run without Zerodha.")
            self._set_badge("NO TOKEN", RED)
            self.market_label.config(text="●  kite login needed", fg=RED)
            messagebox.showerror("Kite mode needs a login", str(e))
            return
        except SystemExit:
            self.status_label.config(text="ERROR: could not start — see terminal for details.")
            return
        except Exception as e:
            self.status_label.config(text=f"ERROR creating provider: {e}")
            return

        # A fresh Event per run — if an old thread is still winding down
        # when Start is clicked again, it keeps its OWN (already-set) event
        # and will exit on its own; it can never be "un-stopped" by a new run.
        stop_event = threading.Event()
        self.current_stop_event = stop_event
        self.cycle_count = 0
        self.was_open_last_check = None
        self._closed_snapshot_done = False

        self._render_trade_tracker(current_price=None)
        self.market_label.config(text="●  starting...", fg=FG_SECOND)

        # ---- live WebSocket price feed (kite mode only) -------------------
        self._stop_streamer()
        if mode == "kite":
            try:
                self.streamer = KiteStreamer(self.provider.api_key, self.provider.access_token)
                if self.streamer.start():
                    # Subscribe EVERY index, not just the visible one — that's
                    # what lets all three tabs stay live at once.
                    prev_focus = self._focus
                    toks = []
                    try:
                        for key in self.indices:
                            self._focus = key
                            self.index_token = self.provider.index_token(key)
                            if self.index_token:
                                toks.append(self.index_token)
                    finally:
                        self._focus = prev_focus
                    if toks:
                        self.streamer.subscribe(toks)
                    self.live_label.config(text="◉ LIVE", fg=GREEN)
                else:
                    self.live_label.config(text="◌ no stream", fg=AMBER)
                    self.status_label.config(
                        text=f"Live feed unavailable ({self.streamer.last_error}) — "
                             f"falling back to {refresh}s updates.")
                    self.streamer = None
            except Exception as e:
                self.streamer = None
                self.live_label.config(text="◌ no stream", fg=AMBER)
                self.status_label.config(text=f"Live feed unavailable: {e}")
        else:
            # Yahoo/NSE are request-response only — nothing to stream.
            self.live_label.config(text=f"◌ {refresh}s updates", fg=FG_MUTED)

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        expiry_choice = self.expiry_var.get()
        expiry_locked = None if expiry_choice in ("(nearest)", "") else expiry_choice

        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(list(self.indices), refresh, stop_event, expiry_locked), daemon=True
        )
        self.worker_thread.start()
        self._paint_tabs()

    def _stop(self):
        if self.current_stop_event is not None:
            self.current_stop_event.set()
        self._stop_streamer()
        self.live_label.config(text="", fg=FG_MUTED)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.msg_queue.put(("status", "Stopped."))

    def _on_close(self):
        if self.current_stop_event is not None:
            self.current_stop_event.set()
        self._close_market_map()
        self._stop_streamer()
        self.root.destroy()

    def _load_expiries(self):
        """Fetch available expiry dates for the selected index/mode in a
        background thread (network call) and populate the dropdown."""
        index_key = self.index_var.get()
        mode = self.mode_var.get()
        self.load_expiries_btn.config(state="disabled")
        self.status_label.config(text=f"Loading expiry dates for {index_key}...")

        def worker():
            try:
                provider = self.provider if (self.provider is not None) else get_provider(mode)
                expiries = provider.get_expiry_dates(index_key)
                self.msg_queue.put(("expiries", expiries))
            except MissingKiteCredentials:
                self.msg_queue.put((
                    "expiries_error",
                    "Kite login needed (token missing or expired) — run "
                    "python3 kite_login_helper.py, or switch Mode to 'free'.",
                ))
            except SystemExit:
                self.msg_queue.put(("expiries_error", "Could not start — see terminal for details."))
            except Exception as e:
                self.msg_queue.put(("expiries_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------- worker
    def _worker(self, index_keys, refresh, stop_event, expiry):
        """Fetches candles + option chain for EVERY index each cycle, so all
        three stay current and any of them can raise a signal. Runs in a
        background thread and never touches widgets — it only queues data for
        the main thread to store and render."""
        provider = self.provider
        while not stop_event.is_set():
            # Always real India time, whatever this machine's clock says.
            now = now_ist()

            if not is_market_open(now):
                if self.was_open_last_check is not False:
                    self.msg_queue.put(("closed", format_market_closed_banner(now)))
                self.was_open_last_check = False

                # Fetch ONCE per closed period. Historical candles are still
                # served after hours, so this paints the market exactly as it
                # stood at the closing bell instead of leaving a blank screen.
                # Marked stale=True: it is a photograph, not a live feed, so
                # nothing downstream is allowed to open a trade off it.
                if not self._closed_snapshot_done:
                    got = 0
                    for index_key in index_keys:
                        if stop_event.is_set():
                            break
                        try:
                            rec, notes = fetch_recommendation(
                                provider, index_key, "15m", None, quiet=True, expiry=expiry)
                            report = format_report(rec)
                            self.msg_queue.put(("report", index_key, rec, report, notes, True))
                            got += 1
                        except Exception as e:
                            self.msg_queue.put(("status", f"[{index_key}] last session's data "
                                                          f"is unavailable: {e}"))
                        self._sleep_interruptible(1, stop_event)
                    # Only call it done if it actually worked, so a network
                    # hiccup at 16:00 still gets another go a few minutes later
                    # instead of leaving the screen empty all evening.
                    self._closed_snapshot_done = got > 0
                    if not got:
                        # Nothing to show — say so rather than leaving the
                        # screen looking merely empty.
                        self.msg_queue.put(("status", "Market closed, and last session's data "
                                                      "could not be fetched. Retrying shortly."))
                    # Nothing changes while the market is shut, so the status
                    # line is left alone from here — it already carries the
                    # timestamp of the data on screen.

                self._sleep_interruptible(min(refresh, 300), stop_event)
                continue

            # Re-opened: allow a fresh snapshot the next time it closes.
            self._closed_snapshot_done = False
            self.was_open_last_check = True
            self.cycle_count += 1
            if self.cycle_count % SESSION_REFRESH_EVERY == 0 and hasattr(provider, "refresh_session"):
                provider.refresh_session()

            for index_key in index_keys:
                if stop_event.is_set():
                    break
                try:
                    rec, notes = fetch_recommendation(
                        provider, index_key, "15m", None, quiet=True, expiry=expiry)
                    report = format_report(rec)
                    self.msg_queue.put(("report", index_key, rec, report, notes))
                except Exception as e:
                    self.msg_queue.put(("error", f"[{index_key}] {e}"))
                # A small gap between indices keeps NSE's public endpoint
                # from seeing three rapid-fire requests and rate-limiting us.
                self._sleep_interruptible(1, stop_event)

            self._sleep_interruptible(refresh, stop_event)

    def _sleep_interruptible(self, seconds, stop_event):
        end = time.time() + seconds
        while time.time() < end and not stop_event.is_set():
            time.sleep(0.2)

    # ------------------------------------------------------------- UI pump
    def _poll_queue(self):
        # Re-entrancy guard. messagebox and tk_popup run a NESTED event loop,
        # and Tk's `after` timers keep firing inside it. A nested run used to
        # reach the reschedule at the bottom, and then the outer one did too —
        # so every dismissed dialog permanently DOUBLED the tick rate. Two
        # popups in a morning and the window was doing four times the work.
        if getattr(self, "_in_poll", False):
            return
        self._in_poll = True
        try:
            self._pump_queue()
        except Exception as e:
            # The pump must never die. It used to catch queue.Empty only, so
            # any error anywhere in the body killed it for good: the window
            # stayed responsive and kept repainting cached state, while no
            # report, status or login result ever arrived again, and the
            # worker filled an unread queue forever.
            try:
                self.status_label.config(text=f"Recovered from an internal error: {e}")
            except Exception:
                pass
        finally:
            self._in_poll = False
            self.root.after(150, self._poll_queue)

    def _pump_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status_label.config(text=item[1])
                elif kind == "closed":
                    self.market_label.config(text="●  MARKET CLOSED", fg=AMBER)
                    self._set_badge("MKT CLOSED", AMBER)
                    # Only take over the panel when there is genuinely nothing
                    # to show. If the session already produced a ticket, that
                    # ticket stays up — the banner goes in the status line
                    # instead, so closing time doesn't erase the day.
                    if not self.report_text:
                        self._set_text(item[1])
                    self.status_label.config(text=item[1].strip().replace("\n", "  "))
                    # Write the day's report once, the moment the session
                    # ends — the whole point is that you don't have to be
                    # awake at 15:30 IST to capture it.
                    # Square up anything still open. Nothing used to close a
                    # position at 15:30, so it stayed OPEN forever: trades.csv
                    # kept an OPEN row with no CLOSE, the day's last trade —
                    # including a full stop-out — was invisible to the P&L and
                    # missing from the summary written moments later.
                    self._close_open_at_bell()
                    if not self._summary_done:
                        self._summary_done = True
                        self._save_summary(silent=True)
                elif kind == "error":
                    self.status_label.config(text=f"Error: {item[1]}")
                elif kind == "token_state":
                    state, detail, quiet = item[1], item[2], item[3]
                    self._paint_token(state, detail)
                    if not quiet and state != "ok":
                        self.status_label.config(text=detail)
                elif kind == "login_done":
                    self._finish_login(item[1], item[2])
                elif kind == "expiries":
                    expiries = item[1]
                    self.load_expiries_btn.config(state="normal")
                    if expiries:
                        self.expiry_options = ["(nearest)"] + list(expiries)
                        self.status_label.config(text=f"Loaded {len(expiries)} expiry date(s). Pick one, then Start.")
                    else:
                        self.expiry_options = ["(nearest)"]
                        self.status_label.config(text="No expiry list available for this index/mode — will use nearest automatically.")
                elif kind == "expiries_error":
                    self.load_expiries_btn.config(state="normal")
                    self.status_label.config(text=f"Could not load expiries: {item[1]}")
                elif kind == "report":
                    index_key, rec, report, notes = item[1], item[2], item[3], item[4]
                    # A closed-market snapshot carries stale=True: show it,
                    # but never trade off it.
                    stale = len(item) > 5 and item[5]
                    if index_key not in self.states:
                        continue
                    prev_focus = self._focus
                    self._focus = index_key          # everything below now
                    try:                             # targets THIS index
                        self.last_rec = rec
                        self.report_text = report
                        self.notes = tuple(notes or ())
                        self.base_df = rec.get("candles")
                        self.base_oi = rec.get("option_chain")

                        # In free mode there is no tick stream, so this REST
                        # cycle is the only chance to act on a signal —
                        # confirm immediately rather than waiting for live
                        # passes that will never come.
                        if not stale:
                            self._consider_signal(rec, confirm_now=(self.streamer is None))
                            self._track_open_trade(rec)

                        if index_key == self.current:
                            now_str = now_ist().strftime("%H:%M:%S")
                            if stale:
                                last = self._last_candle_stamp(rec)
                                self.market_label.config(
                                    text=f"●  MARKET CLOSED · {index_key} · last data {last}",
                                    fg=AMBER)
                                status = f"Market closed — this is the last data of the session ({last})."
                            else:
                                self.market_label.config(
                                    text=f"●  MARKET OPEN · {index_key} · {now_str} IST", fg=GREEN)
                                status = f"Last updated {now_str} IST"
                            if notes:
                                status += "  |  " + " ".join(notes)
                            self.status_label.config(text=status)
                            self._render_current()
                            # Last word on the badge: _render_current repaints
                            # the trade tracker, which would otherwise put
                            # WAITING back over the closed-market notice.
                            if stale:
                                self._set_badge("MKT CLOSED", AMBER)
                    finally:
                        self._focus = prev_focus
                    self._paint_tabs()
        except queue.Empty:
            pass

    def _show_signal_popup(self, rec: dict):
        t1, t2, t3 = rec["index_targets"]
        lines = [
            f"{rec['index']}  —  {rec['action']}",
            f"Suggested strike: {rec['suggested_strike']} {rec['option_type']}",
            "",
            f"Index entry: {rec['spot']}   T1: {t1}   T2: {t2}   T3: {t3}   SL: {rec['index_stop_loss']}",
        ]
        if rec.get("premium_source") == "live":
            pt1, pt2, pt3 = rec["premium_targets"]
            lines += [
                "",
                f"LIVE premium: {rec['live_ltp']}",
                f"Premium T1: {pt1}   T2: {pt2}   T3: {pt3}   SL: {rec['premium_stop_loss']}",
            ]
        why = explain.explain(rec)
        lines += ["", "WHY:", why["verdict"]]
        lines += ["", "Open the Chart tab for the full reasoning, drawn on the candles.",
                  "Not investment advice — confirm before trading."]
        self.root.bell()  # audible cue so a new signal doesn't rely on you watching the screen
        # Non-modal-ish: messagebox is technically modal, but the background
        # worker thread keeps running and queuing updates regardless, so
        # nothing is lost while this is open — just dismiss it when ready.
        messagebox.showinfo(f"New {rec['option_type']} signal — {rec['index']}", "\n".join(lines))


def _make_index_property(key):
    """Proxy an old single-value attribute onto the per-index state dict.

    self.last_rec, self.active_trade and friends now resolve to whichever
    index is in focus. Background evaluation of a non-displayed index simply
    moves the focus, runs the identical code, and moves it back — which is
    why none of the signal logic needed rewriting for multi-index support."""
    def getter(self):
        return self.states[self._focus][key]

    def setter(self, value):
        self.states[self._focus][key] = value

    return property(getter, setter)


def _font_exists(name: str) -> bool:
    try:
        return name in tkfont.families()
    except Exception:
        return False


def _apply_dark_theme(style: ttk.Style, root: tk.Tk) -> None:
    """Explicitly styles every ttk widget class used in this app so the
    whole window reads as one consistent dark theme, regardless of the
    OS's own light/dark appearance setting."""
    style.configure(".", background=BG_APP, foreground=FG_PRIMARY,
                     fieldbackground=BG_INPUT, bordercolor=BORDER)
    style.configure("TFrame", background=BG_APP)
    style.configure("TLabel", background=BG_APP, foreground=FG_PRIMARY)
    style.configure("TButton", background=BG_INPUT, foreground=FG_PRIMARY,
                     bordercolor=BORDER, focusthickness=0, focuscolor=BG_APP, padding=6)
    style.map(
        "TButton",
        background=[("active", ACCENT), ("disabled", BG_PANEL)],
        foreground=[("disabled", FG_MUTED)],
    )
    style.configure("TCheckbutton", background=BG_APP, foreground=FG_PRIMARY)
    style.map(
        "TCheckbutton",
        background=[("active", BG_APP)],
        foreground=[("disabled", FG_MUTED)],
    )
    style.configure("TEntry", fieldbackground=BG_INPUT, foreground=FG_PRIMARY,
                     bordercolor=BORDER, insertcolor=FG_PRIMARY)
    style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                     foreground=FG_PRIMARY, arrowcolor=FG_PRIMARY, bordercolor=BORDER)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", BG_INPUT)],
        foreground=[("readonly", FG_PRIMARY)],
    )
    style.configure("TScrollbar", background=BG_INPUT, troughcolor=BG_APP,
                     bordercolor=BG_APP, arrowcolor=FG_PRIMARY)
    # The combobox dropdown list is a plain Tk Listbox internally, not
    # themed by ttk.Style — this is the only way to color it.
    root.option_add("*TCombobox*Listbox.background", BG_INPUT)
    root.option_add("*TCombobox*Listbox.foreground", FG_PRIMARY)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", FG_PRIMARY)


for _k in _PER_INDEX_DEFAULTS:
    setattr(SignalApp, _k, _make_index_property(_k))


def main():
    root = tk.Tk()
    root.configure(bg=BG_APP)
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    try:
        _apply_dark_theme(style, root)
    except Exception:
        pass  # theming is cosmetic — never block the app from starting over it
    app = SignalApp(root)

    # `python3 gui.py --demo` puts a worked example on screen — a live ticket
    # with T1 already taken out — so the layout can be looked at without
    # waiting for the market to open and produce a signal. Nothing is logged
    # and nothing is fetched; it is a drawing, and it says so on the ticket.
    if "--demo" in sys.argv:
        _load_demo(app)

    root.mainloop()


def _load_demo(app):
    app.current = app.indices[0]
    app._focus = app.current
    app.index_var.set(app.current)
    app.lots_var.set("2")
    app.active_trade = {
        "index": app.current, "option_type": "CE", "strike": 24500,
        "entry_time": "10:12:55", "entry_ts": now_ist(), "why": None,
        "entry_spot": 24402.0, "entry_ltp": 168.20, "use_premium": True,
        "lot_size": 75, "lots": 2,
        "index_targets": [24548.0, 24612.0, 24690.0], "index_sl": 24402.0,
        "premium_targets": [214.0, 246.0, 285.0], "premium_sl": 152.0,
        "hit": {"T1": True, "T2": False, "T3": False},
        "hit_time": {"T1": "10:41:02", "T2": None, "T3": None},
        "sl_hit": False, "sl_hit_time": None, "status": "OPEN",
        "trade_id": "DEMO",
    }
    app.last_rec = {
        "index": app.current, "spot": 24483.65, "bias": "BULLISH",
        "option_type": "CE", "suggested_strike": 24500,
        "index_targets": [24548.0, 24612.0, 24690.0], "index_stop_loss": 24402.0,
        "premium_targets": [214.0, 246.0, 285.0], "premium_stop_loss": 152.0,
        "premium_source": "live", "live_ltp": 196.40, "candles": None,
        "option_chain": {"available": True, "strikes": [
            {"strike": 24500, "call_ltp": 196.40, "put_ltp": 5.0,
             "call_oi": 1, "put_oi": 1}]},
    }
    app.market_label.config(text="●  DEMO — not live data", fg=AMBER)
    app.status_label.config(
        text="Demo ticket. Run without --demo for the real thing.")
    app._render_trade_tracker(current_price=196.40)
    app._set_badge("DEMO", AMBER)


if __name__ == "__main__":
    main()
