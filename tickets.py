"""
tickets.py — the ticket engine, with no window attached to it
================================================================================
A live signal and an issued ticket are different things, and everything that
sits between them used to live inside gui.py's Tk class: the confirmation
window, the gap between tickets, the daily brake, freezing the levels at entry,
watching the frozen levels for a hit, closing, logging, re-arming.

That was fine while there was exactly one screen. It stopped being fine when
the website needed the same behaviour, because the logic was tangled up with
Tk variables, message boxes and `self.root.bell()` — none of which mean anything
over HTTP. Re-implementing it for the web would have produced a second set of
rules that agreed with the first until the day they quietly didn't.

So it is here instead: the same decisions, the same order, the same wording in
the badges, and nothing that knows what a window is.

WHAT CHANGED IN THE MOVE, AND WHY
    * Tk variables became plain settings on the book. `reentry`, `limits`,
      `auto_rearm` and `lots` are attributes with config-backed defaults.
    * Pop-ups and the terminal bell became EVENTS. `update()` returns a list of
      things that just happened, and the caller decides whether that is a
      dialog, a toast, or nothing at all.
    * The trade history text widget became a list of closed tickets.
    * The log is per-owner. One shared trades.csv across several website users
      would interleave their tickets and let each of them read the others'
      through the daily counters.

WHAT DELIBERATELY DID NOT CHANGE
    The rules. A ticket still needs the direction to hold for
    SIGNAL_CONFIRM_SECONDS, still respects MIN_MINUTES_BETWEEN_TICKETS, still
    refuses to replace a running position on a change of mind, still answers to
    the daily brake, and re-arm still goes through every one of those gates
    rather than around them — that last one being the fix for thirteen tickets
    in twelve seconds.

*** NOTHING HERE PLACES AN ORDER. A ticket is a record of what the rules said,
*** tracked against frozen levels so it can be measured honestly afterwards.
"""

import re
import threading
import time

import config
import indicators
import real_entry
import explain
import signal_engine
import trade_log
from main import is_market_open, nse_holiday_name, now_ist

TARGET_KEYS = ("T1", "T2", "T3")


def _cfg(name, fallback):
    return getattr(config, name, fallback)


def cfd_pnl(trade, price):
    """Dollars on a CFD ticket (Exness BTCUSD / XAUUSD): the move in the trade's favour,
    less the spread it pays, times lot_size (1 BTC; 100 oz of gold) x lots.

    The spread is the whole cost on an Exness Standard account - buy at the ask, sell
    at the bid - and is charged once in full: the spread quoted when the ticket opened
    (half of it paid going in, half coming out at much the same spread). Levels are
    checked on the mid, as everywhere else here. Overnight swap is NOT included.
    None when a figure is missing."""
    entry = trade.get("entry_spot")
    if price is None or entry is None or not trade.get("lot_size"):
        return None
    sign = 1 if trade.get("option_type") == "CE" else -1
    units = float(trade["lot_size"]) * float(trade.get("lots", 1) or 1)
    return round((sign * (float(price) - float(entry)) - float(trade.get("entry_spread") or 0.0)) * units, 2)


def reward_risk_t3(name, rec):
    """(ratio, need) for the ticket gate _reward_hold() checks below - room to run (T3)
    over the stop distance, and the threshold this instrument's market actually
    requires (config.min_reward_risk_t3(), per-market since 30 Sep 2026). Both None if
    there is not enough of the recommendation to compute it (no targets yet, or the
    gate switched off for this market) - the same early-outs _reward_hold() itself
    takes. Pulled out to module level so the Signal card can show the exact number the
    gate uses, not a second, possibly-different one - the user, 30 Sep 2026, after
    raising crypto's own bar: "signal card [says 1x]" - that was reach_to_risk, a
    different, unchanged gate; this is the one that actually moved, and until this it
    had nowhere to be seen except inside a LOW REWARD hold message.

    `rr` is left unrounded - _reward_hold()'s own `rr < need` comparison needs the raw
    value, the same one this returns, so rounding here could move a borderline case
    across the boundary between the two callers."""
    need = config.min_reward_risk_t3(name)
    if not need:
        return None, None
    spot, risk = rec.get("spot"), rec.get("risk_points")
    # A CFD exit plan replaces the shown targets; the gate keeps the market-reach ladder it was
    # tested on (signal_engine's gate_targets).
    tg = rec.get("gate_targets") or rec.get("index_targets") or [None, None, None]
    t3 = tg[2] if len(tg) > 2 else None
    if spot is None or not risk or t3 is None:
        return None, None
    return abs(t3 - spot) / risk, need


def _safe_explain(rec):
    try:
        return explain.explain(rec)
    except Exception:
        return None


class IndexBook:
    """One index's ticket state for one owner."""

    def __init__(self, name):
        self.name = name
        self.trade = None              # the locked ticket, if any
        self.last_rec = None
        self.confirm_dir = None
        self.confirm_streak = 0
        self.confirm_since = None
        self.last_bias_signature = None
        self.last_ticket_at = None     # datetime, per index
        self.last_close_at = None      # datetime the last ticket here closed
        # How and in what trend strength the last ticket here closed - read by
        # the WAIVE_COOLDOWN_ON_TRENDING_TARGET check in _same_direction_hold().
        # "target" matches trade_log.is_target_close() exactly (a clean full
        # target reached - not a trailed stop, not a manual clear, not
        # square-off); anything else is "other". None until a ticket has
        # actually closed once.
        self.last_close_reason = None  # "target" | "other" | None
        self.last_close_adx = None     # float | None - ADX at that close
        self.wait_reason = None        # (code, short, long)
        # A cooldown you chose to skip, pinned to the ticket or exit it was
        # counting from. Pinned rather than a flag so it is one-shot by
        # construction: the next ticket or exit moves the reference, and the
        # waiver stops matching without anything having to remember to clear it.
        self.cooldown_waived = None    # {"ref": datetime|None, "gap": datetime|None}
        self.waiver_applied = False    # set while evaluating, when it let one through
        # Same idea, automatic: set while evaluating when the trend waiver
        # (not a hand-chosen skip) is what let a same-direction entry through.
        # Kept separate from waiver_applied so the two can be measured apart.
        self.trend_waiver_applied = False
        self.live = None               # newest streamed price for this index

    # -- the gate that only this index knows about -------------------------
    def last_ticket_epoch(self):
        """When THIS index last issued, as a time.time() stamp.

        `last_ticket_at` is a datetime; the gap rule wants a float. The
        conversion lives here rather than at each call site.
        """
        if self.last_ticket_at is None:
            return None
        return time.time() - (now_ist() - self.last_ticket_at).total_seconds()


class TicketBook:
    """Every index's tickets for one owner, plus the day's running totals.

    `owner` is a website account's email, or None for a single-user caller
    that wants the shared desktop log.
    """

    def __init__(self, owner=None, market=None, path=None):
        self.owner = owner
        # The executor of live orders, when this book's tickets can place any: the day's open P&L is worked from the
        # price it filled at (real_entry.py), like the ticket on the screen.
        self.fill_source = None
        # A callable (index, rec) -> a fresher price of the contract `rec` names, or None. Where the chain's price can be
        # seconds old (Delta's REST snapshot), the feed supplies the live one so a ticket is frozen - entry, stop and targets
        # together - from the price the order will be priced from. Not given to the AI desk's book: its levels are the model's.
        self.fresh_price = None
        self.market = market or config.DEFAULT_MARKET
        # `path` gives a second book its own log - the AI desk's paper tickets,
        # which must never be counted in the rule tickets' record or limits.
        self.path = path or (trade_log.user_log_path(owner, self.market) if owner else None)
        self.lock = threading.RLock()
        self.books = {name: IndexBook(name)
                      for name in config.instruments_in(self.market)}
        self.closed = []               # this session's closed tickets, newest first
        # Called as fn(kind, trade) when a ticket opens or closes - how live
        # orders follow a ticket. Called under self.lock, so a listener must
        # only queue work, never wait on anything.
        self.listeners = []
        self.session_net = 0.0
        self._day_cache = None
        self._contracts_cache = None

        # What used to be checkboxes. Defaults come from config so the website
        # and the desktop start from the same place.
        # The smallest size the market allows: one lot of an index option,
        # 0.1 of a BTC contract.
        self.lots = config.lot_choices(self.market)[0]
        self.reentry = bool(_cfg("ALLOW_SAME_DIRECTION_REENTRY", False))
        self.auto_rearm = True
        self.limits = bool(_cfg("DAILY_LIMITS_ON", False))
        # Account risk. Kept on disk beside the log, unlike the lots: the
        # account size is a fact about the person, and asking for it again
        # after every restart would mean the loss limit silently switched off
        # each morning until somebody noticed.
        self.capital = None
        self.risk_pct = float(_cfg("RISK_PER_TRADE_PCT", 1.0))
        self._booked_cache = None
        self._load_settings()

        self._reconcile()
        self._restore_memory()

    # =====================================================================
    def _settings_path(self):
        return (self.path + ".settings.json") if self.path else None

    def _load_settings(self):
        p = self._settings_path()
        if not p:
            return
        try:
            import json
            with open(p) as fh:
                s = json.load(fh)
            cap = s.get("capital")
            self.capital = float(cap) if cap else None
            if s.get("risk_pct") in _cfg("RISK_PCT_CHOICES", (1.0,)):
                self.risk_pct = float(s["risk_pct"])
            # The lot size the user picked. It used to live only in memory, so
            # every restart quietly put it back to the smallest size while an
            # open page kept showing the chosen number - on 23 Sep 2026 every
            # ticket and real order went out at 1 lot after a restart, whatever
            # the selector said. Snapped to what the market offers now.
            if s.get("lots") not in (None, ""):
                choices = config.lot_choices(self.market)
                saved = self._from_contracts(float(s["lots"]), choices)
                self.lots = min(choices, key=lambda c: abs(c - saved))
        except Exception:
            pass

    def _from_contracts(self, lots, choices):
        """A size in Delta contracts (10-500, one contract 0.001 BTC) read into an Exness
        market, where a lot is 1 BTC: 250 contracts is 0.25 - not the nearest Exness
        choice to 250, which would be a whole coin. A saved setting from before the
        switch, or a page still showing Delta's sizes, both come through here."""
        if lots > max(choices) and any(v.get("cfd") and v.get("market") == self.market
                                       for v in config.INSTRUMENTS.values()):
            return lots * 0.001
        return lots

    def _save_settings(self):
        p = self._settings_path()
        if not p:
            return
        try:
            import json, os
            tmp = p + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"capital": self.capital, "risk_pct": self.risk_pct, "lots": self.lots}, fh)
            os.replace(tmp, p)
        except Exception:
            pass

    def loss_limit(self):
        """Today's loss limit in money, or None when no capital is set."""
        pct = self.loss_limit_pct()
        if not self.capital or not pct:
            return None
        return round(self.capital * pct / 100.0, 2)

    def loss_limit_pct(self):
        """DAILY_LOSS_LIMIT_R full-risk losses, as a share of capital."""
        return round(float(_cfg("DAILY_LOSS_LIMIT_R", 0)) * self.risk_pct, 2)

    def booked_net_today(self, index=None):
        """Closed P&L for today, from the log, cached for a few seconds - the whole book's, or one
        instrument's own when `index` is given (DAILY_LOSS_LIMIT_PER_INSTRUMENT)."""
        now = time.time()
        c = self._booked_cache
        if c is None or now - c[0] > 3.0:
            try:
                per, _ = trade_log.booked_today(now_ist().strftime("%Y-%m-%d"),
                                                path=self.path)
                per = per or {}
            except Exception:
                per = None
            self._booked_cache = c = (now, per)
        per = c[1]
        if per is None:
            return None
        if index is not None:
            return round(per.get(index, 0.0), 2)
        return round(sum(per.values()), 2) if per else 0.0

    def _restore_memory(self):
        """Rebuild, from the log, what each index remembers about its last
        ticket: which way it went, when it opened, when it closed.

        That memory drives the same-direction rules - the cooldown after an
        exit and the room check - and it lived only in memory, so every
        restart forgot the morning and the next same-way signal skipped both.
        Only the recent past counts: today's session for an index, the last
        24 hours for a market that never closes.
        """
        if not self.path:
            return
        try:
            import datetime as _dt
            rows = trade_log._read_rows(self.path)
            now = now_ist()
            always = config.MARKETS.get(self.market, {}).get("always_open", False)
            for r in rows:
                name = r.get("index")
                book = self.books.get(name)
                if book is None:
                    continue
                try:
                    when = _dt.datetime.strptime(f"{r.get('date')} {r.get('time_ist')}",
                                                 "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
                except (TypeError, ValueError):
                    continue
                recent = ((now - when).total_seconds() < 86400) if always \
                    else when.date() == now.date()
                if not recent:
                    continue
                ev = (r.get("event") or "").upper()
                if ev == "OPEN":
                    opt = r.get("option_type")
                    bias = {"CE": "BULLISH", "PE": "BEARISH"}.get(opt)
                    if bias and (book.last_ticket_at is None or when >= book.last_ticket_at):
                        book.last_ticket_at = when
                        book.last_bias_signature = (bias, opt)
                elif ev == "CLOSE":
                    if book.last_close_at is None or when >= book.last_close_at:
                        book.last_close_at = when
        except Exception:
            pass

    # =====================================================================
    def _reconcile(self):
        """Close out tickets left OPEN by a process that is no longer running.

        The book lives in memory. A restart used to lose every open ticket
        while its OPEN row stayed in the CSV forever, so the log filled with
        opens that never closed — thirty-four of them against four closes —
        and the same signal was re-ticketed on every restart because the new
        book had no idea one was already running.

        There is no honest exit price for a trade nobody was watching, so
        these are closed with no P&L and a status that says exactly what
        happened. A missing number is better than an invented one, and a row
        that says "the process died" is better than a row that pretends the
        trade is still live.
        """
        if not self.path:
            return
        try:
            rows = trade_log._read_rows(self.path)
        except Exception:
            return
        seen = {}
        for r in rows:
            tid = r.get("trade_id") or ""
            if not tid:
                continue
            seen.setdefault(tid, {"open": None, "closed": False})
            if (r.get("event") or "").upper() == "OPEN":
                seen[tid]["open"] = r
            elif (r.get("event") or "").upper() == "CLOSE":
                seen[tid]["closed"] = True

        orphans = [v["open"] for v in seen.values() if v["open"] and not v["closed"]]
        for row in orphans:
            try:
                trade_log._append({
                    "trade_id": row.get("trade_id", ""),
                    "date": now_ist().strftime("%Y-%m-%d"),
                    "time_ist": now_ist().strftime("%H:%M:%S"),
                    "index": row.get("index"), "strike": row.get("strike"),
                    "option_type": row.get("option_type"),
                    "entry": row.get("entry"), "exit": None, "pnl": None,
                    "lot_size": row.get("lot_size"), "lots": row.get("lots"),
                    "tracked_on": row.get("tracked_on"),
                    "entry_spot": row.get("entry_spot"),
                    "event": "CLOSE", "t1_hit": False, "t2_hit": False,
                    "t3_hit": False, "sl_hit": False,
                    "status": "CLOSED — the tool stopped while this was open, "
                              "so there is no exit price for it",
                }, self.path)
            except Exception:
                pass
        if orphans:
            self._day_cache = None

    # =====================================================================
    # settings
    # =====================================================================
    def configure(self, lots=None, reentry=None, auto_rearm=None, limits=None,
                  capital=None, risk_pct=None):
        with self.lock:
            if capital is not None:
                # 0 or blank clears it, which also switches the loss limit off.
                self.capital = float(capital) if capital and capital > 0 else None
                self._save_settings()
            if risk_pct is not None and risk_pct in _cfg("RISK_PCT_CHOICES", (1.0,)):
                self.risk_pct = float(risk_pct)
                self._save_settings()
            if lots is not None:
                choices = config.lot_choices(self.market)
                # Snap to the nearest size the market offers, so a stale page
                # asking for "3" on crypto or "0.3" on Nifty still gets a real one.
                self.lots = min(choices, key=lambda c: abs(c - self._from_contracts(float(lots), choices)))
                self._save_settings()
            if reentry is not None:
                self.reentry = bool(reentry)
            if auto_rearm is not None:
                self.auto_rearm = bool(auto_rearm)
            if limits is not None:
                self.limits = bool(limits)

    # =====================================================================
    # the day's counters, read off the log rather than from memory
    # =====================================================================
    def day_stats(self):
        """(tickets, T3 wins, stop-outs, trailed-stop profits) for today, cached
        for a few seconds. A stop-out is a trade that LOST to its stop; one that
        closed in profit on a trailed stop is the fourth number (trade_log.is_stop_out).

        From disk, not memory: restarting must not hand you a fresh set of
        daily limits. Today explicitly — left to its own default the log
        answers about the newest date it holds, which on a fresh morning is
        yesterday, and yesterday's two winners would lock you out before the
        bell.
        """
        now = time.time()
        c = self._day_cache
        if c is None or now - c[0] > 3.0:
            try:
                vals = trade_log.day_outcomes(now_ist().strftime("%Y-%m-%d"),
                                              path=self.path)
            except Exception:
                vals = (None, None, None, None)
            self._day_cache = (now, vals)
            return vals
        return c[1]

    def contracts_today(self):
        """{(index, strike, "CE"/"PE")} this book has issued a ticket on today,
        open or closed, read off the trade log - so a restart forgets nothing,
        which is also why the AI desk's own in-memory list was never enough for
        the rules. Cached for a few seconds: it is asked once a second per index.

        By calendar day rather than by expiry - the log does not carry one. On an
        index option that is exact (the nearest expiry cannot change inside a
        day); on Bitcoin, which lists a new expiry every day, it can rule out a
        strike that is new again after 21:30 IST, which only ever costs a
        neighbouring strike."""
        now = time.time()
        c = self._contracts_cache
        if c is not None and now - c[0] <= 3.0:
            return c[1]
        out = set()
        try:
            today = now_ist().strftime("%Y-%m-%d")
            for r in trade_log._read_rows(self.path):
                if r.get("event") == "OPEN" and r.get("date") == today:
                    try:
                        out.add((r["index"], float(r["strike"]), r["option_type"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        except Exception:
            out = set()
        self._contracts_cache = (now, out)
        return out

    def _strike_taken(self, book, rec):
        """Why this reading cannot open a ticket on its strike, or None.

        Two ways: the reading itself says no strike near the money is free, or -
        a reading built a moment before another ticket opened, or before the last
        one closed - it still names a contract already traded today. The strike
        itself is chosen when the reading is built (signal_engine._next_free_strike);
        this is the invariant behind it, so no reading, stale or otherwise, and no
        auto re-arm can put a second ticket on a strike already used."""
        if config.is_cfd(rec.get("index")):
            return None                  # a CFD has no strikes: the instrument itself is the trade
        if rec.get("strike_taken"):
            return ("strike_taken", "NO FREE STRIKE",
                    "Every strike near the money has already been traded today, so a new "
                    "ticket here would buy one of them again - averaging into it in a real "
                    "account. It waits for the money to move to a strike not yet used.")
        try:
            key = (rec["index"], float(rec.get("suggested_strike")), rec.get("option_type"))
        except (KeyError, TypeError, ValueError):
            return None
        if key in self.contracts_today():
            return ("strike_taken", "STRIKE TAKEN",
                    f"{rec['index']} {key[1]:g} {key[2]} was already traded today. The next "
                    "reading names a strike that has not been - one moment.")
        return None

    def _ref_key(self):
        """Any instrument in this book's market — they share a session."""
        keys = config.instruments_in(self.market)
        return keys[0] if keys else None

    def entry_block(self, index=None):
        """Why no new ticket may be issued right now — or None if one may. `index` is the
        instrument asking: on a DAILY_LOSS_LIMIT_PER_INSTRUMENT market its own losses count.

        Deliberately separate from the signal itself: the analysis keeps
        running and the signal keeps being shown and explained. Only the act
        of taking it is held, because seeing the trade you did not take is how
        you learn whether the limit is helping or costing you.

        Returns (code, short, long) or None.
        """
        now = now_ist()

        # The session itself. This was missing, and the analysis loop keeps
        # running after the close — so tickets were being issued at 20:30 on a
        # market that shut at 15:40, against a premium that had not moved since
        # the bell. Every gate below assumed something upstream had already
        # established the market was open. Nothing had.
        # Ask about THIS book's market. Both of these took no instrument, so
        # both answered for an NSE index - and a crypto ticket was therefore
        # blocked with MARKET CLOSED all night, every night, on a market that
        # never closes. The confirm streak would run its 120 seconds and then
        # hit a gate belonging to a different exchange.
        ref = self._ref_key()
        if not is_market_open(now, ref):
            # Before the open is not "the session is over". That sentence, shown
            # at 08:52 with "09:15-15:40" in it, was read as the opening-range
            # wait that had been switched off the day before (17 Sep 2026).
            m = config.market_for(ref)
            o, c = m.get("open"), m.get("close")
            # A specific name, not just "shut" - the user, 2 Oct 2026, on Gandhi
            # Jayanti: "the tool should give a message... why the market closed
            # today". A weekend needs no explanation; a holiday on an otherwise
            # ordinary weekday does, and NSE's own name for it was sitting right
            # there in main.py, previously only as a comment nothing could read.
            holiday = nse_holiday_name(now.date()) if m.get("holidays") else None
            trading_day = not ((not m.get("weekends") and now.weekday() >= 5) or holiday)
            if trading_day and o and (now.hour, now.minute) < tuple(o):
                return ("closed", "BEFORE THE OPEN",
                        f"The market opens at {o[0]:02d}:{o[1]:02d}. The signal is already "
                        "being worked out from the latest candles, and a ticket can be "
                        "issued from the open.")
            hours = (f"{o[0]:02d}:{o[1]:02d} to {c[0]:02d}:{c[1]:02d}" if o and c
                     else "market hours")
            if holiday:
                return ("closed", "MARKET CLOSED",
                        f"Today is {holiday}, an NSE trading holiday - the exchange "
                        "is shut for the day. The analysis keeps running so you can "
                        f"see where things ended, but tickets are only issued from "
                        f"{hours} on a trading day.")
            return ("closed", "MARKET CLOSED",
                    "The session is over. The analysis keeps running so you can see "
                    f"where things ended, but tickets are only issued from {hours}.")

        # The closing auction. The market is open and the premium is still
        # moving, so every check below would pass - but from 15:15 the index
        # is a held value, and with it RSI, MACD, ADX, VWAP and the trend.
        # Issuing here means acting on a signal whose inputs stopped updating
        # twenty minutes ago while quoting a price that did not. Anything
        # already open is untouched: it is tracked on its own premium, which
        # is live, and it can still be closed until 15:40.
        if config.in_closing_auction(now, ref):
            return ("auction", "CLOSING AUCTION",
                    "From 15:15 every Nifty constituent is in NSE's closing "
                    "auction, so the index holds one value until the closing "
                    "prices publish around 15:35 - and every reading taken "
                    "from it is frozen with it. Options trade on until 15:40, "
                    "so anything already open is still tracked and can still "
                    "be closed. No new entry is issued on a stopped index.")

        # Waiting for the opening auction to settle only means something on a
        # market that has one. Applied to crypto it read "MARKET OPENING" every
        # night until 09:20 IST, which is neither an opening nor a reason.
        cut = _cfg("NO_NEW_TRADES_BEFORE", None)
        if config.market_for(ref)["always_open"]:
            cut = None
        if cut and (now.hour, now.minute) < (cut[0], cut[1]):
            return ("early", "MARKET OPENING",
                    f"No entries before {cut[0]:02d}:{cut[1]:02d} IST — the opening "
                    f"auction is still settling and the first candle barely "
                    f"exists. The signal is live; only the entry is held.")

        # The daily loss limit. Ahead of the limits switch on purpose: those
        # caps are about how much to trade, this is about how much a bad day
        # may cost, and it is only live once the account size is known.
        # Closed trades only - an open ticket's P&L is still moving, and a
        # limit that trips on a dip and un-trips on the bounce is no limit.
        limit = self.loss_limit()
        if limit:
            own = bool(index) and self.market in _cfg("DAILY_LOSS_LIMIT_PER_INSTRUMENT", ())
            booked = self.booked_net_today(index if own else None)
            if booked is not None and booked <= -limit:
                pct = self.loss_limit_pct()
                return ("loss_limit", "LOSS LIMIT",
                        f"Today's closed {index + ' ' if own else ''}trades are down {abs(booked):,.0f}, past "
                        f"the daily loss limit of {limit:,.0f} ({pct:g}% of the "
                        f"account). No new tickets today - the "
                        f"signal is still shown. A losing day ends here rather "
                        f"than being chased.")
        if not self.limits:
            return None
        issued, wins, stops, _locked = self.day_stats()
        if issued is None:
            return None
        cap = _cfg("DAILY_TARGET_WINS", 0)
        if cap and wins >= cap:
            return ("won", "DAY DONE",
                    f"{wins} trade{'s' if wins != 1 else ''} ran all the way to T3 "
                    f"today — that is the daily target. No new entries: the "
                    f"signal is still shown, but a good day is not worth "
                    f"handing back.")
        cap = _cfg("MAX_TRADES_PER_DAY", 0)
        if cap and issued >= cap:
            return ("maxed", "DAY LIMIT",
                    f"{issued} tickets issued today, which is the daily maximum. "
                    f"No new entries. The signal is still shown and logged.")
        return None

    # =====================================================================
    # the main entry point
    # =====================================================================
    def update(self, name, rec):
        """Feed one fresh recommendation in. Returns a list of events.

        Each event is {"kind": ..., ...} — "opened", "target", "stop",
        "closed". The caller turns them into whatever it has: a dialog, a
        toast, a line in a log, or nothing.
        """
        if not rec:
            return []
        with self.lock:
            book = self.books.get(name)
            if book is None:
                book = self.books[name] = IndexBook(name)
            book.last_rec = rec
            # How long the CURRENT reading's direction has held - shared by the entry
            # confirmation below (_consider) and, when EARLY_EXIT_ON_REVERSAL is on, an
            # early exit from an open ticket (_check_price): one clock, both ways. Updated
            # here, before either reads it, so both see this reading, not the last one.
            self._track_direction(book, rec)
            events = []
            # Order matters and matches the desktop: an already-open ticket is
            # checked against its frozen levels FIRST, so a target that was
            # reached is registered before anything decides whether a new
            # ticket may be issued in its place.
            events += self._track(book, rec)
            events += self._consider(book, rec)
            return events

    def _track_direction(self, book, rec):
        """Update book.confirm_dir/confirm_streak/confirm_since for THIS reading.

        Only ever called from update() - never from the public track(), which is how an
        AI-desk book (tickets tracked but never issued by these rules) is tracked instead.
        Such a book's confirm_dir simply stays None forever, which is exactly why the
        reversal-exit check below (gated on confirm_dir being set) never fires for it: the
        AI desk already re-asks itself on every candle close and decides in its own way.
        """
        direction = ((rec["bias"], rec["option_type"])
                     if rec.get("bias") not in (None, "NEUTRAL") else None)
        now = time.time()
        if direction == book.confirm_dir:
            book.confirm_streak += 1
        else:
            book.confirm_dir = direction
            book.confirm_streak = 1
            book.confirm_since = now
        return direction

    def track(self, name, rec):
        """Check an open ticket against a fresh reading, and nothing else - no
        new ticket is ever issued from here. For a book whose tickets are not
        the rules' own (the AI desk's): keep auto_rearm off on such a book."""
        if not rec:
            return []
        with self.lock:
            book = self.books.get(name)
            if book is None:
                return []
            book.last_rec = rec
            return self._track(book, rec)

    # ------------------------------------------------------------- tracking
    def _price_for(self, trade, rec):
        """Price of the LOCKED contract.

        Looked up by the trade's own frozen strike rather than taken from
        rec["live_ltp"], which reflects whatever strike is suggested *now* —
        possibly a different contract entirely.
        """
        if trade is None or rec is None:
            return None
        if trade["use_premium"]:
            oi = rec.get("option_chain")
            if not oi:
                return None
            # The chain on hand is for another expiry now - its price for
            # this strike belongs to a different contract. The tick stream,
            # subscribed to the ticket's own contract, carries it instead.
            if (trade.get("expiry") and oi.get("expiry")
                    and str(oi["expiry"])[:10] != str(trade["expiry"])[:10]):
                return self._streamed(trade)
            try:
                return signal_engine._find_strike_ltp(oi, trade["strike"],
                                                      trade["option_type"])
            except Exception:
                return None
        return rec.get("spot")

    def _streamed(self, trade):
        """The ticket's own contract as last streamed, or None. Used when the
        chain on hand is for another expiry, so a clear or the bell still logs
        a real exit price - they were logging none."""
        book = self.books.get(trade.get("index"))
        return book.live if book is not None else None

    def _track(self, book, rec):
        """Check an open ticket against its frozen levels, then re-arm if it
        closed naturally and the direction still holds."""
        trade = book.trade
        if trade is None or trade["status"] != "OPEN":
            return []
        events = self._check_price(book, self._price_for(trade, rec), rec)
        closed = book.trade is not None and book.trade["status"] != "OPEN"
        natural = closed and (trade_log.is_target_close(book.trade["status"])
                              or "stop-loss hit" in book.trade["status"])
        if natural and self.auto_rearm and rec.get("bias") != "NEUTRAL":
            if self._rearm(book, rec):
                events.append({"kind": "opened", "index": book.name,
                               "rearmed": True, "trade": self._public_trade(book.trade)})
        return events

    def _check_price(self, book, price, rec):
        """One price against the FROZEN targets and a stop that can only ever
        move in the trade's favour.

        Independent of whatever the live signal says now: an in-progress trade
        keeps being tracked correctly long after the signal itself has moved
        on, which is the entire reason the levels were frozen.

        THE TRAILING STOP — asked for by the user on 22 Sep 2026 ("when the
        ltp hit target one and cross the stop loss should change to target
        1 ... if the strike have more way to go we can do the same way"):
        each target strictly before the trade's own exit rung becomes the new
        stop the instant price crosses it, so a reversal after reaching T1 (or
        T2) costs back only to that rung, never all the way to the original
        stop. The exit rung itself still just closes the trade outright - it
        is the ceiling, not another waypoint. Rule tickets and AI tickets
        share this one method (see ai_desk.py's self.book), so both get it.
        """
        trade = book.trade
        if trade is None or trade["status"] != "OPEN":
            return []
        events = []
        opt = trade["option_type"]
        sl_field = "premium_sl" if trade["use_premium"] else "index_sl"

        if trade["use_premium"]:
            targets = trade["premium_targets"]
            # Premium targets are always "premium rising is good", CE or PE
            # alike — that is how build_recommendation computes them.
            hit_t = lambda v, t: v is not None and t is not None and v >= t
            hit_s = lambda v, s: v is not None and s is not None and v <= s
        else:
            targets = trade["index_targets"]
            if opt == "CE":
                hit_t = lambda v, t: v is not None and t is not None and v >= t
                hit_s = lambda v, s: v is not None and s is not None and v <= s
            else:                      # PE — the index falling is favourable
                hit_t = lambda v, t: v is not None and t is not None and v <= t
                hit_s = lambda v, s: v is not None and s is not None and v >= s

        # Which target ends the trade — frozen at entry for the same reason
        # the levels and the lots are. Moving the setting at lunchtime must not
        # retroactively change where the morning's trade was meant to exit.
        exit_key = trade.get("exit_at") or _cfg("EXIT_AT_TARGET", "T3")
        if exit_key not in TARGET_KEYS:
            exit_key = "T3"
        exit_i = TARGET_KEYS.index(exit_key)

        # Captured BEFORE this tick's targets can move it - otherwise the very
        # tick that first reaches a rung would also read as hitting the stop
        # it just moved to, closing a winning trade as a stop-out.
        sl_before = trade[sl_field]
        if not trade["sl_hit"] and hit_s(price, sl_before):
            trade["sl_hit"] = True
            trade["sl_hit_time"] = now_ist().strftime("%H:%M:%S")
            events.append({"kind": "stop", "index": book.name, "price": price, "level": sl_before})

        ratcheted = False
        for i, key in enumerate(TARGET_KEYS):
            tv = targets[i] if targets and len(targets) > i else None
            if not trade["hit"][key] and hit_t(price, tv):
                trade["hit"][key] = True
                trade["hit_time"][key] = now_ist().strftime("%H:%M:%S")
                events.append({"kind": "target", "index": book.name, "target": key, "price": price, "level": tv})
                if tv is not None and i < exit_i and not trade["sl_hit"] and not trade.get("plain_exit"):
                    trade[sl_field] = tv
                    ratcheted = True

        # TIME-BASED BREAKEVEN (config.TIME_BREAKEVEN_MINUTES, 0 disables) — tested via
        # time_breakeven_study.py, 29 Sep 2026: KEEP, and robustly so — every wait time from
        # 30 minutes to 10 hours beat the live exit both in-sample and held-out on identical
        # entries, roughly doubling profit and roughly halving the worst drawdown in both
        # periods. If T1 has not been touched within this many minutes of entry, the stop
        # tightens to breakeven and never loosens again — it does not close the trade by
        # itself; target, the real stop and square-off are all unaffected. A T1 touch already
        # ratchets the stop to T1 above, more favourable than breakeven, so this only ever
        # matters — and only ever tightens further — while T1 is still untouched.
        # Per market (config.time_breakeven_minutes): off for crypto since 3 Oct 2026, the
        # Indian indices unchanged.
        wait_min = config.time_breakeven_minutes(trade.get("index") or book.name)
        if (wait_min and not trade["sl_hit"] and not trade["hit"]["T1"]
                and not trade["time_breakeven_done"]):
            elapsed_min = (now_ist() - trade["entry_ts"]).total_seconds() / 60.0
            if elapsed_min >= wait_min:
                be = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
                if be is not None:
                    current = trade[sl_field]
                    tightened = max(current, be) if opt == "CE" else min(current, be)
                    if tightened != current:
                        trade[sl_field] = tightened
                        ratcheted = True
                trade["time_breakeven_done"] = True

        # SUPERTREND TRAIL AFTER T1 (config.TRAIL_AFTER_T1_SUPERTREND, True by
        # default) — tested via supertrend_trail_study.py, 2 Oct 2026: KEEP
        # against the REAL live exit (T1-ratchet included), both periods.
        # Once T1 has been touched, the stop keeps following the index's own
        # Supertrend line instead of sitting flat at T1 for the rest of the
        # trade — only ever tightening further, same as every ratchet above.
        # rec["supertrend"] is in INDEX points; on a premium-tracked ticket
        # it is converted the same way build_recommendation() converts
        # premium targets/stop at entry — a straight delta move off the
        # trade's own frozen entry anchor (entry_spot/entry_ltp), since
        # Supertrend is computed on the index, not the option. Skipped
        # outright on a missing/NaN reading (indicator still warming up)
        # rather than guessing a level.
        st = rec.get("supertrend") if rec else None
        if (_cfg("TRAIL_AFTER_T1_SUPERTREND", True) and trade["hit"]["T1"]
                and not trade["sl_hit"] and st is not None and not trade.get("plain_exit")):
            if trade["use_premium"]:
                sign = 1 if opt == "CE" else -1
                st_level = trade["entry_ltp"] + sign * config.APPROX_ATM_DELTA * (st - trade["entry_spot"])
            else:
                st_level = st
            current = trade[sl_field]
            tightened = max(current, st_level) if opt == "CE" else min(current, st_level)
            if tightened != current:
                trade[sl_field] = tightened
                ratcheted = True

        if trade["hit"][exit_key]:
            trade["status"] = f"CLOSED — {exit_key} hit (full target reached)"
        elif trade["sl_hit"]:
            trailed_to = next((k for k in reversed(TARGET_KEYS[:exit_i]) if trade["hit"][k]), None)
            trade["status"] = (f"CLOSED — stop-loss hit (trailed to {trailed_to})" if trailed_to
                               else "CLOSED — stop-loss hit")

        # A SUSTAINED REVERSAL (EARLY_EXIT_ON_REVERSAL, config.py - off by default, and only ever
        # for a book update() tracks: see _track_direction). Checked only once target and stop have
        # NOT already ended the trade this tick, so the two never race for the same close. The test
        # is exactly the one a fresh entry itself has to pass - the same clock, the same tick count,
        # just read against a position instead of nothing open - and firing it resets that clock, so
        # the next ticket, either direction, has to earn its own confirmation from scratch: without
        # that, this reversal's own just-satisfied clock would satisfy the entry gate again on the
        # very next reading, reopening the churn CLOSE_ON_SIGNAL_FLIP existed to describe.
        if trade["status"] == "OPEN" and _cfg("EARLY_EXIT_ON_REVERSAL", False) and not trade.get("rule_strategy"):
            rd = book.confirm_dir
            need_s = _cfg("SIGNAL_CONFIRM_SECONDS", 0)
            need_ticks = _cfg("SIGNAL_CONFIRM_TICKS", 1)
            if (rd is not None and rd[1] != opt
                    and need_s and (time.time() - (book.confirm_since or time.time())) >= need_s
                    and book.confirm_streak >= need_ticks):
                trade["status"] = "CLOSED — signal reversed and held"
                book.confirm_since = time.time()
                book.confirm_streak = 1

        # A CFD (Exness) is held 24 hours at most - the limit every Exness study walked with (96
        # fifteen-minute bars) - closed at the market after that. Options had their own expiry to
        # end them; a CFD has none, and every night held costs a buy its overnight swap.
        if trade["status"] == "OPEN" and trade.get("cfd"):
            limit = _cfg("CFD_MAX_HOLD_MINUTES", 0)
            if limit and (now_ist() - trade["entry_ts"]).total_seconds() / 60.0 >= limit:
                trade["status"] = f"CLOSED — time limit ({limit / 60:g} hours) reached"

        if trade["status"] != "OPEN":
            events.append(self._close(book, trade, price, rec))
        elif ratcheted:
            # A live order (Zerodha SL order, or Delta's own tool-held level)
            # follows this so the real stop actually moves too - see
            # live_orders.py / delta_orders.py's on_ticket_event("trailed", ...).
            # Never fired on a tick that also closes the trade: the close
            # itself cancels/exits the real position a moment later, so
            # repricing first would be a wasted broker call.
            self._emit("trailed", trade)
        return events

    # -------------------------------------------------------------- issuing
    def _consider_rule(self, book, rec):
        """An Exness entry rule's reading (cfd_rules.py, config.CFD_RULES) - issued exactly as it
        was tested (cfd_vote_search.py), with none of the engine's own entry gates in between:
        the session and the daily brake, a closed market, one position at a time, ONE decision
        per closed 15-minute candle taken in the first cfd_rules.RULE_ENTRY_WINDOW_S after it,
        and REENTRY_COOLDOWN_MIN after an exit."""
        def hold(code, short, why):
            book.wait_reason = (code, short, why)
            return []
        block = self.entry_block(book.name)
        if block is not None:
            return hold(*block)
        block = self._closed_hold(rec)
        if block is not None:
            return hold(*block)
        if book.trade is not None and book.trade["status"] == "OPEN":
            return hold("position_open", "POSITION OPEN",
                        "A ticket is already running on this instrument. It ends at its target, its stop or "
                        "24 hours.")
        info = rec.get("rule") or {}
        if not info.get("ready"):
            return hold("neutral", "NO SIGNAL", info.get("why") or "The rule has no reading yet.")
        if not info.get("side"):
            return hold("neutral", "NO SIGNAL", "The rule's votes and filters do not all agree on the last "
                        "15-minute close. It decides again at the next close.")
        # A ticket is a direction AND a stop AND a target, or it is not issued - whatever the reading's side
        # says (the 4 Oct 2026 ticket with none of them went to Exness as an unprotected SELL).
        want = "CE" if info.get("side", 0) > 0 else "PE"
        tg = rec.get("index_targets") or []
        if (rec.get("option_type") != want or rec.get("index_stop_loss") is None or len(tg) < 3
                or any(x is None for x in tg)):
            return hold("neutral", "NO SIGNAL", "The rule's reading has no complete set of levels (a direction, "
                        "a stop and a target), so no ticket is issued.")
        if getattr(book, "last_rule_bar", None) == info.get("bar_close"):
            return hold("neutral", "NEXT CLOSE", "This 15-minute close has already been decided. The next "
                        "decision is at the next close.")
        if not info.get("fresh"):
            return hold("neutral", "NEXT CLOSE", "The rule agreed on the last 15-minute close, but a decision is "
                        "only taken in the first two minutes after a close - as it was tested. The next one is at "
                        "the next close.")
        cd = _cfg("REENTRY_COOLDOWN_MIN", 0)
        if cd and book.last_close_at is not None and (now_ist() - book.last_close_at).total_seconds() < cd * 60:
            left = int(cd - (now_ist() - book.last_close_at).total_seconds() / 60) + 1
            return hold("reentry_cooldown", "COOLDOWN", f"{cd} minutes after the last exit before a new ticket - "
                        f"about {left} more minute{'s' if left != 1 else ''}.")
        book.last_rule_bar = info.get("bar_close")
        book.last_bias_signature = (rec["bias"], rec["option_type"])
        book.wait_reason = None
        self._open(book, rec)
        return [{"kind": "opened", "index": book.name, "rearmed": False, "trade": self._public_trade(book.trade)}]

    def _consider(self, book, rec):
        """Whether to issue a ticket. The five gates, in the order the desktop
        applies them, with the same badge words."""
        if rec.get("rule") is not None:
            return self._consider_rule(book, rec)
        direction = book.confirm_dir          # this reading's direction - set by update(), just before this ran
        now = time.time()
        book.waiver_applied = False
        book.trend_waiver_applied = False

        def hold(code, short, why):
            book.wait_reason = (code, short, why)
            return []

        # --- 0. the session and the daily brake ---------------------------
        # First of all - even before asking whether the indicators agree on
        # a direction. This used to sit after that question, and after the
        # confirmation gate below it, so a holiday/closed-session reason
        # could be masked by "the indicators do not agree on a direction
        # yet" whenever the signal happened to be sitting at NEUTRAL when
        # the session ended - which is exactly when the feed goes quiet and
        # nothing moves it off NEUTRAL again until trading resumes. Whether
        # the market is open at all is a more fundamental fact than what the
        # indicators say, and must never depend on which direction the
        # signal happened to be reading by chance. The user, 2 Oct 2026, on
        # an actual NSE holiday: "the tool should give a message... why the
        # market closed today."
        block = self.entry_block(book.name)
        if block is not None:
            book.confirm_since = now
            book.confirm_streak = 1
            return hold(*block)
        # 0b. a CFD (Exness) whose price has stopped is a closed market, whatever
        # the 24/7 crypto session says - gold from Friday night to Sunday night.
        block = self._closed_hold(rec)
        if block is not None:
            return hold(*block)

        if direction is None:
            return hold("neutral", "NO SIGNAL",
                        "The indicators do not agree on a direction yet.")

        # --- 1. the direction has to hold, measured in seconds -------------
        need_s = _cfg("SIGNAL_CONFIRM_SECONDS", 0)
        if need_s and (now - (book.confirm_since or now)) < need_s:
            left = int(need_s - (now - (book.confirm_since or now)))
            return hold("confirming", "CONFIRMING",
                        f"The direction has to hold for {need_s}s before a ticket "
                        f"is issued — about {max(1, left)}s to go.")
        if book.confirm_streak < _cfg("SIGNAL_CONFIRM_TICKS", 1):
            return hold("confirming", "CONFIRMING",
                        f"Waiting for {_cfg('SIGNAL_CONFIRM_TICKS', 1)} agreeing "
                        f"readings — {book.confirm_streak} so far.")

        # --- 2. same direction, already taken today ------------------------
        if direction == book.last_bias_signature:
            held = self._same_direction_hold(book, rec, direction)
            if held:
                return hold(*held)

        # --- 3. a floor under the gap between tickets ----------------------
        gap = _cfg("MIN_MINUTES_BETWEEN_TICKETS", 0)
        per_index = _cfg("TICKET_GAP_PER_INDEX", False)
        last_any = book.last_ticket_epoch() if per_index else self._last_any()
        waived = book.cooldown_waived or {}
        if (gap and last_any is not None and (now - last_any) < gap * 60
                and "gap" in waived and waived["gap"] == self._gap_ref(book)):
            book.waiver_applied = True
        elif gap and last_any is not None and (now - last_any) < gap * 60:
            left = int((gap * 60 - (now - last_any)) / 60) + 1
            scope = "on this index" if per_index else "across all indices"
            return hold("ticket_gap", "COOLDOWN",
                        f"A minimum of {gap} minutes between tickets {scope} — "
                        f"about {left} more minute{'s' if left != 1 else ''}.")

        # --- 4. a change of mind does not close a running trade ------------
        if (book.trade is not None and book.trade["status"] == "OPEN"
                and not _cfg("CLOSE_ON_SIGNAL_FLIP", False)):
            return hold("position_open", "POSITION OPEN",
                        "A ticket is already running on this index. It is left "
                        "alone to find its own target or stop.")

        # --- 5. the daily brake --------------------------------------------
        # Checked here rather than earlier so the confirm streak and the
        # direction signature still advance; otherwise the first trade after
        # the block lifts would fire on a stale streak.
        # (5. the daily brake now runs first, as step 0. It still does NOT
        # stamp last_bias_signature: doing so marked the direction "already
        # ticketed", and a signal at 09:17 locked the index out for the day.)

        # --- 6. the opening range has to be complete (and, if required, broken this way)
        # After the daily brake, so "market closed" and "closing auction" still
        # speak first. The confirm streak is left alone: once price does break,
        # the ticket goes at once rather than starting its 120 seconds again.
        held = self._regime_hold(rec)
        if held is not None:
            return hold(*held)

        # --- 6b. no entry into an RSI divergence (skills_study.py, variant D)
        held = self._divergence_hold(rec)
        if held is not None:
            return hold(*held)

        # --- 7. worth taking: the final target pays at least the risk ------
        held = self._reward_hold(book.name, rec)
        if held is not None:
            return hold(*held)

        # --- 8. and can be bought without handing the edge to the spread ---
        held = self._spread_hold(rec)
        if held is not None:
            return hold(*held)

        # --- 9. and is not a strike already traded today -------------------
        held = self._strike_taken(book, rec)
        if held is not None:
            return hold(*held)

        events = []
        if book.trade is not None and book.trade["status"] == "OPEN":
            px = self._price_for(book.trade, rec)
            book.trade["status"] = "CLOSED — signal changed before target/SL was hit"
            events.append(self._close(book, book.trade, px, rec))
        book.last_bias_signature = direction
        book.wait_reason = None
        self._open(book, rec)
        events.append({"kind": "opened", "index": book.name, "rearmed": False,
                       "trade": self._public_trade(book.trade)})
        return events

    def _last_any(self):
        """The newest ticket across every index, as a time.time() stamp."""
        stamps = [b.last_ticket_epoch() for b in self.books.values()]
        stamps = [s for s in stamps if s is not None]
        return max(stamps) if stamps else None

    def _regime_hold(self, rec):
        """Held until the opening range is complete and - when
        REGIME_OR_REQUIRE_BREAK is on - until price has broken it in the trade's
        direction. Only for markets that have an opening; crypto has none."""
        if not _cfg("REGIME_OR_BREAK", False):
            return None
        if config.market_for(rec.get("index"))["always_open"]:
            return None
        orng = rec.get("opening_range") or {}
        need_break = bool(_cfg("REGIME_OR_REQUIRE_BREAK", True))
        if not orng.get("ready"):
            return ("or_wait", "OPENING RANGE",
                    "The first half hour, 09:15 to 09:45, sets the opening range. "
                    + ("Entries wait until it is complete, then need a break of it "
                       "in the trade's direction." if need_break else
                       "Entries wait until it is complete."))
        if not need_break:
            return None
        spot, hi, lo = rec.get("spot"), orng.get("high"), orng.get("low")
        if spot is None or hi is None or lo is None:
            return None
        if rec.get("option_type") == "CE" and spot <= hi:
            return ("or_break", "WAITING FOR BREAK",
                    f"A CE here is taken only above the opening-range high of "
                    f"{hi:,.2f}. Price is {spot:,.2f}, {hi - spot:,.2f} below it. "
                    f"Over three years this rule made about the same money on "
                    f"nearly half the trades, with a smaller worst losing run.")
        if rec.get("option_type") == "PE" and spot >= lo:
            return ("or_break", "WAITING FOR BREAK",
                    f"A PE here is taken only below the opening-range low of "
                    f"{lo:,.2f}. Price is {spot:,.2f}, {spot - lo:,.2f} above it. "
                    f"Over three years this rule made about the same money on "
                    f"nearly half the trades, with a smaller worst losing run.")
        return None

    def _divergence_hold(self, rec):
        """Held while the entry runs into an RSI divergence against it - a new
        20-candle closing high for a CE (low for a PE) that RSI does not
        confirm. Indian indices only: that is where it was tested."""
        if not _cfg("SKIP_RSI_DIVERGENCE", False):
            return None
        if config.market_for(rec.get("index"))["always_open"]:
            return None
        df, side = rec.get("candles"), rec.get("option_type")
        if df is None or side not in ("CE", "PE") or len(df) < 30:
            return None
        try:
            closes = df["Close"]
            if not indicators.rsi_divergence(closes.to_numpy(),
                                             indicators.rsi(closes, 14).to_numpy(), side):
                return None
        except Exception:
            return None
        word = "high" if side == "CE" else "low"
        return ("rsi_divergence", "RSI DIVERGENCE",
                f"Price has just made a new 20-candle closing {word}, but RSI is more "
                f"than 2 points weaker than it was at the previous {word}: momentum is "
                f"not confirming the move. Over three years, skipping entries like this "
                f"made more in both the tested years and the held-out one, with a "
                f"shallower drawdown. Held until the divergence clears.")

    def _reward_hold(self, name, rec):
        """Watch-only indices, then reward:risk to T3. Last of the gates, so
        every rule about WHEN has spoken before this one says whether the
        trade itself is worth having."""
        if name in _cfg("WATCH_ONLY_INDICES", ()):
            return ("watch_only", "WATCH ONLY",
                    f"{name} is shown but not ticketed. Since its weekly expiry "
                    f"ended in November 2024 the same rules have lost money on "
                    f"it (-62k per lot over the held-out year, after costs), "
                    f"while Nifty and Sensex made money. Remove it from "
                    f"WATCH_ONLY_INDICES in config.py to trade it again.")
        rr, need = reward_risk_t3(name, rec)
        if rr is None:
            return None
        if rr < need:
            spot, risk = rec.get("spot"), rec.get("risk_points")
            t3 = (rec.get("index_targets") or [None, None, None])[2]
            return ("low_rr", "LOW REWARD",
                    f"The market has about {abs(t3 - spot):,.0f} points of room "
                    f"this way today and the stop is {risk:,.0f} away - "
                    f"{rr:.2f} to 1. There is not enough room for the trade to "
                    f"pay for the risk it takes. Held until the room is at "
                    f"least {need:g}x the distance to the stop.")
        return None

    def _closed_hold(self, rec):
        """Held when a CFD (Exness) has no live price, or its last real tick is older than
        config.CFD_STALE_QUOTE_S: that is a closed market - gold stops from Friday night to
        Sunday night and pauses daily - and its last price must not open a ticket, though
        the crypto session itself never closes. Not a CFD: never holds."""
        name = (rec or {}).get("index")
        if not config.is_cfd(name):
            return None
        age = rec.get("quote_age_s")
        if age is None:
            return ("closed", "NO LIVE PRICE",
                    f"No live {name} price from Exness yet, so no ticket can open. It waits "
                    "for the first quote.")
        limit = float(_cfg("CFD_STALE_QUOTE_S", 600))
        if age > limit:
            mins = int(age // 60)
            return ("closed", "MARKET CLOSED",
                    f"Exness has not quoted {name} for {mins} minute{'s' if mins != 1 else ''}, so "
                    "its market is closed right now (gold stops from Friday night to Sunday night "
                    "and pauses briefly each day). No ticket opens on the last price; it waits for "
                    "trading to resume.")
        return None

    def _spread_hold(self, rec):
        """Held when the contract's bid-ask spread is wider than
        MAX_SPREAD_PCT of its mid. Unknown spreads never block."""
        cap = config.max_spread_pct(rec.get("index"))     # per instrument: gold's spreads are 5-8%
        sp = rec.get("spread") or {}
        pct = sp.get("pct")
        if not cap or pct is None or pct <= cap:
            return None
        return ("wide_spread", "WIDE SPREAD",
                f"{rec.get('suggested_strike')} {rec.get('option_type')} is quoted "
                f"{sp['bid']:,.2f} bid / {sp['ask']:,.2f} ask - a spread of "
                f"{pct:.1f}% of the price. Buying at the offer and selling at "
                f"the bid gives that up before the market moves at all, and "
                f"no target here earns it back. Held until it is under "
                f"{cap:g}%. If you trade it anyway, use a limit order near the "
                f"middle, never a market order.")

    def _same_direction_hold(self, book, rec, direction):
        """Why a second ticket in a direction already taken today is held, or
        None if it may go.

        It may go when nothing is open, the cooldown has passed, and there is
        genuinely room left to reach the targets - measured as room ahead
        against risk to the stop, and held to a stricter bar than a first entry
        because the move has already had one leg.
        """
        side = direction[1]
        if not self.reentry:
            return ("same_direction", "ALREADY TAKEN",
                    f"This index was already ticketed {side} today, and re-entry "
                    f"in the same direction is off - the next ticket here waits "
                    f"for the direction to change.")
        if book.trade is not None and book.trade["status"] == "OPEN":
            return ("position_open", "POSITION OPEN",
                    "A ticket is already running on this index. It is left alone "
                    "to find its own target or stop.")
        mins = _cfg("REENTRY_COOLDOWN_MIN", 20)
        # From the later of the last entry and the last exit. Counted from the
        # entry alone, a trade stopped out 22 minutes in could be bought
        # straight back - exactly what this pause exists to prevent.
        ref = self._reentry_ref(book)
        if ref is not None:
            gap = (now_ist() - ref).total_seconds() / 60.0
            waived = book.cooldown_waived or {}
            manual_waiver = (gap < mins and "ref" in waived and waived["ref"] == ref)
            # WAIVE_COOLDOWN_ON_TRENDING_TARGET, backtested 2 Oct 2026
            # (conditional_cooldown_study.py) - a robust KEEP, every ADX
            # threshold swept. The LAST same-direction ticket here closed by
            # reaching its own target, and the market was still trending (ADX
            # at/above ADX_TREND_THRESHOLD) at that exact close - re-entering
            # now is riding a continuation, not chasing a loss, which is the
            # case this cooldown exists to prevent. A stop-out, a
            # time/square-off close, or a target hit in an already-fading
            # market (ADX already under the threshold) earns no waiver here
            # and falls straight through to the ordinary cooldown below.
            trend_waiver = (gap < mins and not manual_waiver
                             and _cfg("WAIVE_COOLDOWN_ON_TRENDING_TARGET", False)
                             and book.last_close_reason == "target"
                             and book.last_close_adx is not None
                             and book.last_close_adx >= _cfg("ADX_TREND_THRESHOLD", 20))
            if manual_waiver:
                # Skipped by hand. Only the clock is waived: the room check
                # below, and every gate after this one, still decide.
                book.waiver_applied = True
            elif trend_waiver:
                book.trend_waiver_applied = True
            elif gap < mins:
                return ("reentry_cooldown", "COOLDOWN",
                        f"Already ticketed {side} today. A second ticket the same "
                        f"way waits {mins} minutes after the last - about "
                        f"{max(1, int(round(mins - gap)))} to go - so a stop-out "
                        f"is not bought straight back.")
        need = float(_cfg("REENTRY_MIN_RR", 1.0))
        rr = rec.get("reach_to_risk")
        room, risk = rec.get("reach_points"), rec.get("risk_points")
        if rr is None or room is None:
            return ("reentry_no_room", "ROOM CHECK",
                    f"Already ticketed {side} today, and the room to run cannot be "
                    f"measured right now, so a second ticket is not issued on a "
                    f"guess.")
        if rr < need:
            return ("reentry_no_room", "NOT ENOUGH ROOM",
                    f"Same direction again, but only {room:,.0f} pts of room against "
                    f"{(risk or 0):,.0f} at risk ({rr:.2f}x). A second ticket this "
                    f"way needs at least {need:.1f}x - room at least as far as the "
                    f"stop - so the targets are really within reach.")
        return None

    def _reentry_allowed(self, book):
        """A SECOND ticket in a direction already ticketed today.

        Only with re-entry on, only with nothing open, and only after a
        cooldown — without which this fires once a second for as long as the
        trend lasts.
        """
        if not self.reentry:
            return False
        if book.trade is not None and book.trade["status"] == "OPEN":
            return False               # one position at a time
        if book.last_ticket_at is None:
            return True
        gap_min = (now_ist() - book.last_ticket_at).total_seconds() / 60.0
        return gap_min >= _cfg("REENTRY_COOLDOWN_MIN", 20)

    def _rearm(self, book, rec):
        """Re-arm after a natural close — through the SAME gates as any other
        entry.

        This used to call the open path directly, which meant it answered to
        nothing: not the daily cap, not the gap, not the opening window. A
        ticket whose stop was already breached would close and re-arm on the
        next evaluation, several times a second, until the chain refreshed.
        Thirteen tickets in twelve seconds against a cap of four, measured.
        """
        book.waiver_applied = False
        book.trend_waiver_applied = False
        if rec.get("rule") is not None:
            return False                 # an entry rule decides only at a 15-minute close (_consider_rule)
        if self.entry_block(book.name) is not None:
            return False
        # Re-arm is only ever a second ticket the SAME way, so it answers to
        # the same-direction rules: the cooldown after the last exit and the
        # room check. It skipped both, and re-bought a stop-out 60 seconds
        # later. A signal that has turned the other way is not a re-arm at
        # all - it goes through _consider and its full confirmation.
        direction = ((rec.get("bias"), rec.get("option_type"))
                     if rec.get("bias") not in (None, "NEUTRAL") else None)
        if direction is None or direction != book.last_bias_signature:
            return False
        if self._same_direction_hold(book, rec, direction) is not None:
            return False
        # The trade-quality gates too. Re-arm skipped them, so a Bank Nifty
        # ticket could re-open while the index was watch-only, and a 0.6:1
        # trade could re-open straight after a stop on a 1:1 one.
        if (self._closed_hold(rec) is not None
                or self._regime_hold(rec) is not None
                or self._reward_hold(book.name, rec) is not None
                or self._spread_hold(rec) is not None
                or self._strike_taken(book, rec) is not None):
            return False
        last = self._last_any()
        gap_s = _cfg("MIN_MINUTES_BETWEEN_TICKETS", 0) * 60
        # The churn brake if one is set, and ALWAYS the anti-runaway floor.
        gap_s = max(gap_s, _cfg("REARM_MIN_SECONDS", 60))
        if last is not None and (time.time() - last) < gap_s:
            return False
        self._open(book, rec)
        return True

    def _open(self, book, rec):
        """Freeze this signal's entry, targets and stop as fixed numbers.

        Everything from here on checks live price against THESE, not against
        whatever the next cycle recalculates.
        """
        if self.fresh_price is not None and rec.get("premium_source") == "live":
            try:
                rec = signal_engine.rebase_premium(rec, self.fresh_price(rec["index"], rec))
            except Exception:
                pass
        cfd = config.is_cfd(rec["index"])
        use_premium = (not cfd and rec.get("premium_source") == "live"
                       and rec.get("live_ltp") is not None)
        meta = config.INSTRUMENTS.get(rec["index"], {})
        stamp = now_ist()
        book.trade = {
            "index": rec["index"],
            "option_type": rec["option_type"],
            "strike": rec["suggested_strike"],
            # Frozen with the strike. On crypto the expiry is picked by spread
            # and can move while a ticket is open; without this the ticket
            # would silently start reading a different contract's price.
            "expiry": (rec.get("option_chain") or {}).get("expiry"),
            "entry_time": stamp.strftime("%H:%M:%S"),
            "entry_ts": stamp,
            # The reasoning frozen at this instant. Without it, reading the
            # ticket an hour later explains what the market looks like *then*,
            # which is not the question — the question is always "why did it
            # fire?", and that answer must not drift.
            #
            # Guarded: explain() reaches into the option chain, and a chain
            # that is present but missing a field raises. Losing the frozen
            # reasoning is a bad outcome; losing the ticket because the prose
            # about it could not be generated would be a much worse one.
            "why": _safe_explain(rec),
            "entry_spot": rec["spot"],
            "entry_ltp": rec.get("live_ltp"),
            "use_premium": use_premium,
            "lot_size": meta.get("lot_size"),
            "lots": self.lots,          # frozen on purpose
            # A CFD (Exness): the ticket is the instrument itself, and the spread
            # paid to get in and out is its cost - frozen with the entry.
            "cfd": cfd,
            "entry_spread": rec.get("cfd_spread") if cfd else None,
            # A CFD exit plan (config.cfd_exit_plan) or an entry rule (config.CFD_RULES): ONE target,
            # nothing moves the stop on the way; a rule's ticket has no reversal exit either.
            "plain_exit": rec.get("target_basis") in ("plain_r", "rule"),
            "rule_strategy": rec.get("target_basis") == "rule",
            # A rule's reward:risk is fixed with its levels (target over stop); frozen so the open
            # ticket's panel keeps showing it after the rule's reading goes quiet.
            "reward_risk": rec.get("reach_to_risk") if rec.get("target_basis") == "rule" else None,
            "exit_at":("T3" if rec.get("target_basis") in ("plain_r", "rule")
                        else _cfg("EXIT_AT_TARGET", "T3") if _cfg("EXIT_AT_TARGET", "T3") in TARGET_KEYS else "T3"),
            "index_targets": rec["index_targets"],
            "index_sl": rec["index_stop_loss"],
            "premium_targets": rec["premium_targets"],
            "premium_sl": rec["premium_stop_loss"],
            "hit": {k: False for k in TARGET_KEYS},
            "hit_time": {k: None for k in TARGET_KEYS},
            "sl_hit": False,
            "sl_hit_time": None,
            "time_breakeven_done": False,
            "status": "OPEN",
            "trade_id": f"{rec['index']}-{stamp.strftime('%Y%m%d-%H%M%S')}",
            # Issued only because you skipped a cooldown by hand. Kept with the
            # ticket and written to the log, so the record can show whether
            # those entries earn their keep separately from the ones the
            # rules let through on their own.
            "cooldown_skipped": bool(book.waiver_applied),
            # Issued only because WAIVE_COOLDOWN_ON_TRENDING_TARGET let it
            # through automatically - a same-direction continuation, not a
            # hand-chosen skip. Kept separate from cooldown_skipped above so
            # the two can be measured apart.
            "cooldown_waived_trend": bool(book.trend_waiver_applied),
            # Set when this strike is not the one at the money because that one
            # was already traded today - so the ticket can say why it is here.
            "strike_swap": rec.get("strike_swap"),
        }
        book.cooldown_waived = None
        book.waiver_applied = False
        book.trend_waiver_applied = False
        book.last_ticket_at = stamp
        # The previous ticket's streamed price is not this one's.
        book.live = None
        # Written to disk immediately rather than on close — if this dies
        # mid-trade there is still proof the signal happened.
        try:
            trade_log.log_open(book.trade, rec, stamp, path=self.path)
        except Exception:
            pass
        self._day_cache = None          # it counts against today from now
        self._contracts_cache = None    # and its strike is spoken for from now
        self._emit("opened", book.trade)

    def _emit(self, kind, trade):
        for fn in list(self.listeners):
            try:
                fn(kind, trade)
            except Exception:
                pass

    def _close(self, book, trade, price, rec):
        """Log a ticket that has stopped being OPEN, and bank its P&L."""
        entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
        realized = None
        if trade.get("cfd"):
            realized = cfd_pnl(trade, price)
            if realized is not None:
                self.session_net += realized
        elif (trade["use_premium"] and trade["lot_size"]
                and price is not None and entry is not None):
            realized = round((price - entry) * trade["lot_size"]
                             * trade.get("lots", 1), 2)
            self.session_net += realized
        try:
            trade_log.log_close(trade, rec, now_ist(), price, realized,
                                path=self.path)
        except Exception:
            pass
        self._day_cache = None
        self._booked_cache = None        # a close changes today's booked P&L - the loss limit must see it now
        book.last_close_at = now_ist()
        # For WAIVE_COOLDOWN_ON_TRENDING_TARGET: how this one closed, and the
        # trend strength at that moment - read back the NEXT time a
        # same-direction entry is considered here. rec is whatever the caller
        # had on hand at close (the live tick's rec for a natural close,
        # book.last_rec for a manual one) - never re-fetched, so this reflects
        # exactly what was known at the moment of closing, nothing fresher.
        book.last_close_reason = ("target" if trade_log.is_target_close(trade["status"])
                                   else "other")
        book.last_close_adx = (rec or {}).get("adx")
        row = {
            "index": trade["index"], "strike": trade["strike"],
            "expiry": trade.get("expiry"),
            "option_type": trade["option_type"], "entry": entry,
            "exit": price, "entry_time": trade["entry_time"],
            "exit_time": now_ist().strftime("%H:%M:%S"),
            "status": trade["status"], "pnl": realized,
            "lots": trade.get("lots", 1),
        }
        self.closed.insert(0, row)
        del self.closed[40:]            # a session strip, not an archive
        self._emit("closed", trade)
        return {"kind": "closed", "index": book.name, "trade": row}

    def tick_price(self, name, price):
        """Check an open ticket against ONE streamed price.

        The analysis cycle runs every thirty seconds; ticks arrive several
        times a second. Without this, a target reached at 11:44:09 would be
        registered at 11:44:30 with the price it happened to have by then —
        which is not when it was reached and not what it was worth. The
        desktop has always checked on the tick, so the website does too.
        """
        if price is None:
            return []
        with self.lock:
            book = self.books.get(name)
            if book is None or book.trade is None or book.trade["status"] != "OPEN":
                return []
            evs = self._check_price(book, price, book.last_rec)
            for ev in evs:
                ev["live"] = True
            return evs

    def live_price(self, name, price):
        """Remember the newest streamed price for an index, so the ticket
        panel can quote a premium that is current rather than one that is up
        to a poll old."""
        with self.lock:
            book = self.books.get(name)
            if book is not None:
                book.live = price

    # =====================================================================
    # manual actions
    # =====================================================================
    def clear(self, name):
        """Drop a ticket without waiting for a target or a stop — for when
        you have decided not to take a signal and do not want it sitting on
        screen as OPEN. It is still logged, because it still happened."""
        with self.lock:
            book = self.books.get(name)
            if not book or book.trade is None:
                return None
            if book.trade["status"] == "OPEN":
                px = self._price_for(book.trade, book.last_rec)
                book.trade["status"] = "CLOSED — cleared manually"
                ev = self._close(book, book.trade, px, book.last_rec)
                book.trade = None
                return ev
            book.trade = None
            return None

    def close_ticket(self, name, status, price=None, trade_id=None):
        """Close an open ticket for a stated reason - the live-order intraday
        close at 15:20, an AI exit - logged at the price
        that caused it when there is one, else the ticket's own streamed price,
        else the chain's (which can be a REST pass old). With trade_id, only that
        ticket: a broker closing an older position (Exness's own stop, after a
        restart) must never close a newer ticket on the same instrument."""
        with self.lock:
            book = self.books.get(name)
            if not book or book.trade is None or book.trade["status"] != "OPEN":
                return None
            if trade_id is not None and book.trade.get("trade_id") != trade_id:
                return None
            px = price if price is not None else book.live
            if px is None:
                px = self._price_for(book.trade, book.last_rec)
            book.trade["status"] = status
            ev = self._close(book, book.trade, px, book.last_rec)
            book.trade = None
            return ev

    def skip_cooldown(self, name):
        """Let the next ticket on this index through its cooldown, once.

        Only the clock is lifted. The ticket is still issued by the rules on
        the next reading - the direction confirmed, room to the targets, reward
        to risk, the spread, one position at a time, the daily brake - so this
        cannot open anything those would refuse. Returns (ok, message).
        """
        with self.lock:
            book = self.books.get(name)
            if book is None:
                return False, "That index is not on this market."
            if book.trade is not None and book.trade["status"] == "OPEN":
                return False, "A ticket is already open on this index."
            code = (book.wait_reason or (None,))[0]
            if code not in ("reentry_cooldown", "ticket_gap"):
                return False, "Nothing on this index is waiting on a cooldown right now."
            book.cooldown_waived = {"ref": self._reentry_ref(book),
                                    "gap": self._gap_ref(book)}
            book.wait_reason = ("cooldown_skipped", "COOLDOWN SKIPPED",
                                "Cooldown skipped. The ticket is issued on the next "
                                "reading if every other check still passes.")
            return True, (f"Cooldown skipped on {name}. The ticket comes on the next "
                          f"reading if the other checks still pass.")

    def _reentry_ref(self, book):
        """What the same-direction cooldown counts from: the later of the last
        entry and the last exit on this index."""
        return max([t for t in (book.last_ticket_at, book.last_close_at) if t is not None],
                   default=None)

    def _gap_ref(self, book):
        """What the between-tickets gap counts from, as a datetime - stable
        between readings, unlike the float stamps the gap itself compares."""
        if _cfg("TICKET_GAP_PER_INDEX", False):
            return book.last_ticket_at
        stamps = [b.last_ticket_at for b in self.books.values() if b.last_ticket_at]
        return max(stamps) if stamps else None

    def clear_all(self):
        """The bulk form of clear() - drop every open ticket across every index in
        this book at once, without waiting for a target or a stop. Each one is
        still logged, because it still happened.

        Defensive per ticket, like close_all_at_bell(): one index's price lookup
        failing must not stop the rest from clearing too - the entire point of a
        bulk "get me out of everything" action is that it cannot half-work."""
        out = []
        with self.lock:
            for book in self.books.values():
                t = book.trade
                if t is None or t["status"] != "OPEN":
                    continue
                try:
                    px = self._price_for(t, book.last_rec)
                except Exception:
                    px = None
                t["status"] = "CLOSED — cleared manually"
                out.append(self._close(book, t, px, book.last_rec))
                book.trade = None
        return out

    def close_all_at_bell(self):
        """Square up every open ticket at the close.

        An intraday position does not survive the session, so leaving one
        marked OPEN is not caution — it is a missing row.
        """
        out = []
        with self.lock:
            for book in self.books.values():
                t = book.trade
                if t is None or t["status"] != "OPEN":
                    continue
                try:
                    px = self._price_for(t, book.last_rec)
                except Exception:
                    px = None
                t["status"] = "CLOSED — market closed with the trade still open"
                out.append(self._close(book, t, px, book.last_rec))
        return out

    # =====================================================================
    # reading
    # =====================================================================
    def _public_trade(self, trade, rec=None, live=None):
        if trade is None:
            return None
        # A streamed price beats a polled one: it is the same number, seconds
        # fresher, and it is what the desktop quotes.
        price = live if live is not None else (
            self._price_for(trade, rec) if rec is not None else None)
        entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
        pnl = None
        if trade.get("cfd"):
            pnl = cfd_pnl(trade, price)
        elif (trade["use_premium"] and trade["lot_size"]
                and price is not None and entry is not None):
            pnl = round((price - entry) * trade["lot_size"] * trade.get("lots", 1), 2)
        return {
            "trade_id": trade.get("trade_id"),
            "index": trade["index"], "strike": trade["strike"],
            "expiry": trade.get("expiry"),
            "option_type": trade["option_type"],
            "status": trade["status"], "open": trade["status"] == "OPEN",
            "entry_time": trade["entry_time"],
            "entry": entry, "now": price, "pnl": pnl,
            "tracked_on": "premium" if trade["use_premium"] else "index",
            # A CFD ticket (Exness): BUY (CE) or SELL (PE) the instrument itself - no
            # strike, no expiry - and the spread it paid, frozen at entry.
            "cfd": bool(trade.get("cfd")), "entry_spread": trade.get("entry_spread"),
            "reward_risk": trade.get("reward_risk"),      # a rule's fixed one; None = the panel reads the live signal's
            "lots": trade.get("lots", 1), "lot_size": trade.get("lot_size"),
            "targets": (trade["premium_targets"] if trade["use_premium"]
                        else trade["index_targets"]),
            "stop": (trade["premium_sl"] if trade["use_premium"]
                     else trade["index_sl"]),
            "hit": trade["hit"], "hit_time": trade["hit_time"],
            "sl_hit": trade["sl_hit"], "sl_hit_time": trade["sl_hit_time"],
            "exit_at": trade.get("exit_at", "T3"),
            "cooldown_skipped": bool(trade.get("cooldown_skipped")),
            "strike_swap": trade.get("strike_swap"),
            # The frozen INDEX levels, whichever way the ticket is tracked, so
            # the chance of reaching each can be priced from the live index -
            # a premium ticket's rungs are these same levels in option terms.
            "index_targets": trade.get("index_targets"),
            "index_stop": trade.get("index_sl"),
            # Where the INDEX was at entry, whichever way the ticket is tracked:
            # the chart draws the entry line in index points.
            "entry_spot": trade.get("entry_spot"),
        }

    def public(self, name, rec=None):
        """This index's ticket state, for a page to render."""
        with self.lock:
            book = self.books.get(name)
            if book is None:
                return {"ticket": None, "wait": None}
            rec = rec if rec is not None else book.last_rec
            wait = book.wait_reason
            return {
                "ticket": self._public_trade(book.trade, rec, book.live),
                "wait": ({"code": wait[0], "badge": wait[1], "why": wait[2],
                          "base": self._wait_base(wait[0], wait[2]), "left_s": self._wait_left_s(book)}
                         if wait else None),
            }

    # The three holds that are a clock - the direction must hold for N seconds, a second ticket the same way waits N
    # minutes after the last, a minimum gap between tickets - say how long is left as a NUMBER, so the page can count
    # it down every second instead of showing "about 47s to go" that only moves when a poll arrives (25 Sep 2026: "i
    # dont see that 20 sec holding text or 20 minutes holding text with timers"). Worked out from the same state the
    # gates read, at the moment it is asked for; None for every other hold.
    def _wait_left_s(self, book):
        code = (book.wait_reason or (None,))[0]
        try:
            if code == "confirming":
                need = _cfg("SIGNAL_CONFIRM_SECONDS", 0)
                if need and book.confirm_since is not None:
                    return round(max(0.0, need - (time.time() - book.confirm_since)), 1)
            elif code == "reentry_cooldown":
                ref = self._reentry_ref(book)
                if ref is not None:
                    return round(max(0.0, _cfg("REENTRY_COOLDOWN_MIN", 20) * 60 - (now_ist() - ref).total_seconds()), 1)
            elif code == "ticket_gap":
                gap = _cfg("MIN_MINUTES_BETWEEN_TICKETS", 0)
                last = book.last_ticket_epoch() if _cfg("TICKET_GAP_PER_INDEX", False) else self._last_any()
                if gap and last is not None:
                    return round(max(0.0, gap * 60 - (time.time() - last)), 1)
        except Exception:
            return None
        return None

    @staticmethod
    def _wait_base(code, why):
        """The reason without its "about N to go", for a page that shows a live clock beside it."""
        if code == "confirming":
            return re.sub(r"\s*\u2014\s*about \d+s to go\.$", ".", why)
        if code == "reentry_cooldown":
            return re.sub(r" - about \d+ to go - ", ", ", why)
        if code == "ticket_gap":
            return re.sub(r"\s*\u2014\s*about \d+ more minutes?\.$", ".", why)
        return why

    def session(self):
        """The day's totals — booked from the log, open from live prices.

        Booked is read back off disk rather than kept in memory, because
        restarting at lunchtime must not reset the morning to zero: those
        trades happened whether or not this process was running when they
        closed.
        """
        with self.lock:
            try:
                per_index, n = trade_log.booked_today(
                    now_ist().strftime("%Y-%m-%d"), path=self.path)
            except Exception:
                per_index, n = {}, 0
            booked = round(sum(per_index.values()), 2) if per_index else 0.0
            open_pnl = 0.0
            per = dict(per_index)
            for name, book in self.books.items():
                t = book.trade
                if t is None or t["status"] != "OPEN":
                    continue
                pub = self._public_trade(t, book.last_rec, book.live)
                if pub:
                    real_entry.apply(self.fill_source, pub)
                if pub and pub["pnl"] is not None:
                    open_pnl += pub["pnl"]
                    per[name] = round(per.get(name, 0.0) + pub["pnl"], 2)
            issued, wins, stops, locked = self.day_stats()
            return {
                "per_index": per,
                "booked": round(booked, 2),
                "open": round(open_pnl, 2),
                "net": round(booked + open_pnl, 2),
                "closed_today": n,
                "issued": issued, "wins": wins, "stops": stops, "locked": locked,
                "max_trades": _cfg("MAX_TRADES_PER_DAY", 0),
                "limits": self.limits,
                "recent": self.closed[:8],
                "lots": self.lots,
                "lot_choices": config.lot_choices(self.market),
                "capital": self.capital,
                "risk_pct": self.risk_pct,
                "risk_choices": list(_cfg("RISK_PCT_CHOICES", (1.0,))),
                "loss_limit": self.loss_limit(),
                "loss_limit_pct": self.loss_limit_pct(),
                "auto_rearm": self.auto_rearm,
                "reentry": self.reentry,
            }
