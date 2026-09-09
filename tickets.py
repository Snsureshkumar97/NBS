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

import threading
import time

import config
import explain
import signal_engine
import trade_log
from main import now_ist

TARGET_KEYS = ("T1", "T2", "T3")


def _cfg(name, fallback):
    return getattr(config, name, fallback)


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
        self.wait_reason = None        # (code, short, long)

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

    def __init__(self, owner=None):
        self.owner = owner
        self.path = trade_log.user_log_path(owner) if owner else None
        self.lock = threading.RLock()
        self.books = {name: IndexBook(name) for name in config.INSTRUMENTS}
        self.closed = []               # this session's closed tickets, newest first
        self.session_net = 0.0
        self._day_cache = None

        # What used to be checkboxes. Defaults come from config so the website
        # and the desktop start from the same place.
        self.lots = 1
        self.reentry = False
        self.auto_rearm = True
        self.limits = bool(_cfg("DAILY_LIMITS_ON", False))

    # =====================================================================
    # settings
    # =====================================================================
    def configure(self, lots=None, reentry=None, auto_rearm=None, limits=None):
        with self.lock:
            if lots is not None:
                self.lots = max(1, min(int(lots), int(_cfg("MAX_LOTS", 5))))
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
        """(tickets, T3 wins, stop-outs) for today, cached for a few seconds.

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
                vals = trade_log.day_counts(now_ist().strftime("%Y-%m-%d"),
                                            path=self.path)
            except Exception:
                vals = (None, None, None)
            self._day_cache = (now, vals)
            return vals
        return c[1]

    def entry_block(self):
        """Why no new ticket may be issued right now — or None if one may.

        Deliberately separate from the signal itself: the analysis keeps
        running and the signal keeps being shown and explained. Only the act
        of taking it is held, because seeing the trade you did not take is how
        you learn whether the limit is helping or costing you.

        Returns (code, short, long) or None.
        """
        now = now_ist()
        cut = _cfg("NO_NEW_TRADES_BEFORE", None)
        if cut and (now.hour, now.minute) < (cut[0], cut[1]):
            return ("early", "MARKET OPENING",
                    f"No entries before {cut[0]:02d}:{cut[1]:02d} IST — the opening "
                    f"auction is still settling and the first candle barely "
                    f"exists. The signal is live; only the entry is held.")
        if not self.limits:
            return None
        issued, wins, stops = self.day_stats()
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
            events = []
            # Order matters and matches the desktop: an already-open ticket is
            # checked against its frozen levels FIRST, so a target that was
            # reached is registered before anything decides whether a new
            # ticket may be issued in its place.
            events += self._track(book, rec)
            events += self._consider(book, rec)
            return events

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
            try:
                return signal_engine._find_strike_ltp(oi, trade["strike"],
                                                      trade["option_type"])
            except Exception:
                return None
        return rec.get("spot")

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
        """One price against the FROZEN targets and stop.

        Independent of whatever the live signal says now: an in-progress trade
        keeps being tracked correctly long after the signal itself has moved
        on, which is the entire reason the levels were frozen.
        """
        trade = book.trade
        if trade is None or trade["status"] != "OPEN":
            return []
        events = []
        opt = trade["option_type"]

        if trade["use_premium"]:
            targets, sl = trade["premium_targets"], trade["premium_sl"]
            # Premium targets are always "premium rising is good", CE or PE
            # alike — that is how build_recommendation computes them.
            hit_t = lambda v, t: v is not None and t is not None and v >= t
            hit_s = lambda v: v is not None and sl is not None and v <= sl
        else:
            targets, sl = trade["index_targets"], trade["index_sl"]
            if opt == "CE":
                hit_t = lambda v, t: v is not None and t is not None and v >= t
                hit_s = lambda v: v is not None and sl is not None and v <= sl
            else:                      # PE — the index falling is favourable
                hit_t = lambda v, t: v is not None and t is not None and v <= t
                hit_s = lambda v: v is not None and sl is not None and v >= sl

        for i, key in enumerate(TARGET_KEYS):
            tv = targets[i] if targets and len(targets) > i else None
            if not trade["hit"][key] and hit_t(price, tv):
                trade["hit"][key] = True
                trade["hit_time"][key] = now_ist().strftime("%H:%M:%S")
                events.append({"kind": "target", "index": book.name, "target": key,
                               "price": price, "level": tv})

        if not trade["sl_hit"] and hit_s(price):
            trade["sl_hit"] = True
            trade["sl_hit_time"] = now_ist().strftime("%H:%M:%S")
            events.append({"kind": "stop", "index": book.name, "price": price,
                           "level": sl})

        # Which target ends the trade — frozen at entry for the same reason
        # the levels and the lots are. Moving the setting at lunchtime must not
        # retroactively change where the morning's trade was meant to exit.
        exit_key = trade.get("exit_at") or _cfg("EXIT_AT_TARGET", "T3")
        if exit_key not in TARGET_KEYS:
            exit_key = "T3"
        if trade["hit"][exit_key]:
            trade["status"] = f"CLOSED — {exit_key} hit (full target reached)"
        elif trade["sl_hit"]:
            trade["status"] = "CLOSED — stop-loss hit"

        if trade["status"] != "OPEN":
            events.append(self._close(book, trade, price, rec))
        return events

    # -------------------------------------------------------------- issuing
    def _consider(self, book, rec):
        """Whether to issue a ticket. The five gates, in the order the desktop
        applies them, with the same badge words."""
        direction = ((rec["bias"], rec["option_type"])
                     if rec.get("bias") not in (None, "NEUTRAL") else None)
        now = time.time()
        if direction == book.confirm_dir:
            book.confirm_streak += 1
        else:
            book.confirm_dir = direction
            book.confirm_streak = 1
            book.confirm_since = now

        def hold(code, short, why):
            book.wait_reason = (code, short, why)
            return []

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
        if direction == book.last_bias_signature and not self._reentry_allowed(book):
            if self.reentry:
                mins = _cfg("REENTRY_COOLDOWN_MIN", 20)
                return hold("reentry_cooldown", "COOLDOWN",
                            f"This index was already ticketed {direction[1]} today. "
                            f"Re-entry in the same direction needs {mins} minutes "
                            f"between tickets.")
            return hold("same_direction", "ALREADY TAKEN",
                        f"This index was already ticketed {direction[1]} today, and "
                        f"re-entry in the same direction is off — so the next "
                        f"ticket here waits for the direction to change.")

        # --- 3. a floor under the gap between tickets ----------------------
        gap = _cfg("MIN_MINUTES_BETWEEN_TICKETS", 0)
        per_index = _cfg("TICKET_GAP_PER_INDEX", False)
        last_any = book.last_ticket_epoch() if per_index else self._last_any()
        if gap and last_any is not None and (now - last_any) < gap * 60:
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
        block = self.entry_block()
        if block is not None:
            # Deliberately NOT stamping last_bias_signature: doing so marked
            # the direction "already ticketed", and with same-direction
            # re-entry off the signal could then never be taken once the block
            # lifted — a signal at 09:17 locked the index out for the day.
            book.confirm_since = now
            book.confirm_streak = 1
            return hold(*block)

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
        if self.entry_block() is not None:
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
        use_premium = (rec.get("premium_source") == "live"
                       and rec.get("live_ltp") is not None)
        meta = config.INSTRUMENTS.get(rec["index"], {})
        stamp = now_ist()
        book.trade = {
            "index": rec["index"],
            "option_type": rec["option_type"],
            "strike": rec["suggested_strike"],
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
            "exit_at": (_cfg("EXIT_AT_TARGET", "T3")
                        if _cfg("EXIT_AT_TARGET", "T3") in TARGET_KEYS else "T3"),
            "index_targets": rec["index_targets"],
            "index_sl": rec["index_stop_loss"],
            "premium_targets": rec["premium_targets"],
            "premium_sl": rec["premium_stop_loss"],
            "hit": {k: False for k in TARGET_KEYS},
            "hit_time": {k: None for k in TARGET_KEYS},
            "sl_hit": False,
            "sl_hit_time": None,
            "status": "OPEN",
            "trade_id": f"{rec['index']}-{stamp.strftime('%Y%m%d-%H%M%S')}",
        }
        book.last_ticket_at = stamp
        # Written to disk immediately rather than on close — if this dies
        # mid-trade there is still proof the signal happened.
        try:
            trade_log.log_open(book.trade, rec, stamp, path=self.path)
        except Exception:
            pass
        self._day_cache = None          # it counts against today from now

    def _close(self, book, trade, price, rec):
        """Log a ticket that has stopped being OPEN, and bank its P&L."""
        entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
        realized = None
        if (trade["use_premium"] and trade["lot_size"]
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
        row = {
            "index": trade["index"], "strike": trade["strike"],
            "option_type": trade["option_type"], "entry": entry,
            "exit": price, "entry_time": trade["entry_time"],
            "exit_time": now_ist().strftime("%H:%M:%S"),
            "status": trade["status"], "pnl": realized,
            "lots": trade.get("lots", 1),
        }
        self.closed.insert(0, row)
        del self.closed[40:]            # a session strip, not an archive
        return {"kind": "closed", "index": book.name, "trade": row}

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
    def _public_trade(self, trade, rec=None):
        if trade is None:
            return None
        price = self._price_for(trade, rec) if rec is not None else None
        entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
        pnl = None
        if (trade["use_premium"] and trade["lot_size"]
                and price is not None and entry is not None):
            pnl = round((price - entry) * trade["lot_size"] * trade.get("lots", 1), 2)
        return {
            "index": trade["index"], "strike": trade["strike"],
            "option_type": trade["option_type"],
            "status": trade["status"], "open": trade["status"] == "OPEN",
            "entry_time": trade["entry_time"],
            "entry": entry, "now": price, "pnl": pnl,
            "tracked_on": "premium" if trade["use_premium"] else "index",
            "lots": trade.get("lots", 1), "lot_size": trade.get("lot_size"),
            "targets": (trade["premium_targets"] if trade["use_premium"]
                        else trade["index_targets"]),
            "stop": (trade["premium_sl"] if trade["use_premium"]
                     else trade["index_sl"]),
            "hit": trade["hit"], "hit_time": trade["hit_time"],
            "sl_hit": trade["sl_hit"], "sl_hit_time": trade["sl_hit_time"],
            "exit_at": trade.get("exit_at", "T3"),
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
                "ticket": self._public_trade(book.trade, rec),
                "wait": ({"code": wait[0], "badge": wait[1], "why": wait[2]}
                         if wait else None),
            }

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
                pub = self._public_trade(t, book.last_rec)
                if pub and pub["pnl"] is not None:
                    open_pnl += pub["pnl"]
                    per[name] = round(per.get(name, 0.0) + pub["pnl"], 2)
            issued, wins, stops = self.day_stats()
            return {
                "per_index": per,
                "booked": round(booked, 2),
                "open": round(open_pnl, 2),
                "net": round(booked + open_pnl, 2),
                "closed_today": n,
                "issued": issued, "wins": wins, "stops": stops,
                "max_trades": _cfg("MAX_TRADES_PER_DAY", 0),
                "limits": self.limits,
                "recent": self.closed[:8],
                "lots": self.lots,
                "auto_rearm": self.auto_rearm,
                "reentry": self.reentry,
            }
