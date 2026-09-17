"""
ticket_watch.py — the Market Bot's automatic updates on an open ticket
================================================================================
Asked for by the user on 17 Sep 2026: while a ticket is open, the bot should say
how it is going without being asked - whether it is fading, and what the rules
say from here.

EVENT-BASED, NOT ON A TIMER (the user's choice)
    Every open ticket is checked about once a second against the tool's own
    numbers, which costs nothing. The bot is only called when one of these
    happens, each at most once per ticket:
        * T1 is hit (and T1 is not the exit target, which would close it)
        * after T1, price gives back half or more of the move from entry to T1
        * price covers half the distance from entry to the stop
        * ADX falls below the trend gate, having been above it on this ticket
        * the MACD histogram turns against the trade, having been with it
        * no meaningful progress for 30 minutes before T1
    A reading has to hold for a few seconds (price) or a minute (ADX, MACD) -
    both are recomputed on the forming candle and flicker across a line.

WHAT BOUNDS THE BILL
    Off until switched on, per account and per market. At most MAX_PER_TICKET
    updates per ticket, MIN_GAP_S apart - events inside the gap are merged into
    the next update rather than dropped - and DAILY_CAP a day. A failed call
    counts too, so a bad key cannot retry its way through the day.

*** Reads the ticket; never places, changes or cancels anything.
"""
import datetime as dt
import json
import os
import threading
import time

MAX_PER_TICKET = 6
MIN_GAP_S = 300
DAILY_CAP = 30
HOLD_PRICE_S = 15
HOLD_INDICATOR_S = 60
STALL_S = 30 * 60
STALL_STEP = 0.10          # a new best must add this share of entry->T1 to count as progress
KEPT = 30
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

LABELS = {
    "t1": "T1 hit",
    "gave_back": "Gave back half the move after T1",
    "half_stop": "Halfway to the stop",
    "adx_weak": "ADX fell below the trend gate",
    "macd_against": "MACD turned against the trade",
    "stalled": "No progress for 30 minutes",
}


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _hhmmss(ts):
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M:%S")


class Watch:
    """One account's watch on one market's open tickets."""

    def __init__(self, settings_path=None):
        self.path = settings_path
        self.lock = threading.RLock()
        self.on = False
        self.tickets = {}          # trade_id -> per-ticket state
        self.updates = []          # newest first
        self.seq = 0
        self.day = None
        self.day_count = 0
        self.busy = False
        self._load()

    # ------------------------------------------------------------ the switch
    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path) as fh:
                self.on = bool(json.load(fh).get("on"))
        except Exception:
            self.on = False

    def set_on(self, on):
        with self.lock:
            self.on = bool(on)
            if not self.on:
                self.tickets.clear()
            if self.path:
                tmp = self.path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump({"on": self.on}, fh)
                os.replace(tmp, self.path)
        return self.on

    # ------------------------------------------------------------ watching
    def observe(self, index, trade_id, ticket, idx, adx_gate, now=None):
        """Check one open ticket. Returns the events that fired just now."""
        now = time.time() if now is None else now
        if not (self.on and trade_id and ticket and ticket.get("open")):
            return []
        entry, price = _f(ticket.get("entry")), _f(ticket.get("now"))
        if entry is None or price is None:
            return []
        with self.lock:
            st = self.tickets.get(trade_id)
            if st is None:
                st = self.tickets[trade_id] = {
                    "index": index, "trade_id": trade_id, "fired": set(), "since": {},
                    "pending": [], "count": 0, "last_sent": 0.0, "watching_since": now,
                    "best": 0.0, "best_at": now, "best_price": entry,
                    "adx_seen_ok": False, "macd_seen_with": False, "previous": [],
                }
            idx = idx or {}
            ce = ticket.get("option_type") == "CE"
            # A premium rising is good for a CE or a PE alike; an index-tracked PE wants it falling.
            sign = 1.0 if (ticket.get("tracked_on") == "premium" or ce) else -1.0
            hit = ticket.get("hit") or {}
            targets = list(ticket.get("targets") or []) + [None] * 3
            t1, stop = _f(targets[0]), _f(ticket.get("stop"))
            exit_at = ticket.get("exit_at") or "T2"
            progress = (price - entry) * sign
            t1_room = (t1 - entry) * sign if t1 is not None else None
            stop_room = (entry - stop) * sign if stop is not None else None

            if progress > st["best"] + (STALL_STEP * t1_room if t1_room and t1_room > 0 else 0):
                st["best"], st["best_at"], st["best_price"] = progress, now, price

            adx, macd = _f(idx.get("adx")), _f(idx.get("macd_hist"))
            gate = _f(adx_gate)
            if adx is not None and gate is not None and adx >= gate:
                st["adx_seen_ok"] = True
            direction = 1.0 if ce else -1.0
            if macd is not None and macd * direction > 0:
                st["macd_seen_with"] = True

            conditions = {
                "t1": (bool(hit.get("T1")) and exit_at != "T1", 0,
                       f"T1 at {t1} reached at {(ticket.get('hit_time') or {}).get('T1') or 'now'}"),
                "gave_back": (bool(hit.get("T1")) and t1_room is not None and t1_room > 0
                              and progress <= 0.5 * t1_room, HOLD_PRICE_S,
                              f"price {price} against entry {entry} and T1 {t1}"),
                "half_stop": (stop_room is not None and stop_room > 0
                              and -progress >= 0.5 * stop_room, HOLD_PRICE_S,
                              f"price {price}, entry {entry}, stop {stop}"),
                "adx_weak": (st["adx_seen_ok"] and adx is not None and gate is not None
                             and adx < gate, HOLD_INDICATOR_S, f"ADX {adx} against a gate of {gate}"),
                "macd_against": (st["macd_seen_with"] and macd is not None
                                 and macd * direction < 0, HOLD_INDICATOR_S,
                                 f"MACD histogram {macd} on a {'CE' if ce else 'PE'}"),
                "stalled": (not hit.get("T1") and now - st["best_at"] >= STALL_S, 0,
                            f"best price {st['best_price']} last reached at {_hhmmss(st['best_at'])}"),
            }
            fired = []
            for code, (cond, hold, detail) in conditions.items():
                if code in st["fired"]:
                    continue
                if not cond:
                    st["since"].pop(code, None)
                    continue
                first = st["since"].setdefault(code, now)
                if now - first >= hold:
                    st["fired"].add(code)
                    ev = {"code": code, "label": LABELS[code], "at": _hhmmss(now), "detail": detail}
                    st["pending"].append(ev)
                    fired.append(ev)
            return fired

    def forget_except(self, open_ids):
        """Drop tickets that are no longer open, with anything still pending on them."""
        with self.lock:
            for tid in [t for t in self.tickets if t not in open_ids]:
                del self.tickets[tid]

    def has_pending(self):
        with self.lock:
            return any(st["pending"] for st in self.tickets.values())

    def take_due(self, now=None):
        """Claim the next update to write, or None. Only one is ever in flight."""
        now = time.time() if now is None else now
        day = dt.datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
        with self.lock:
            if self.day != day:
                self.day, self.day_count = day, 0
            if not self.on or self.busy or self.day_count >= DAILY_CAP:
                return None
            for st in self.tickets.values():
                if not st["pending"] or st["count"] >= MAX_PER_TICKET:
                    continue
                if st["last_sent"] and now - st["last_sent"] < MIN_GAP_S:
                    continue
                events, st["pending"] = st["pending"], []
                st["count"] += 1
                st["last_sent"] = now
                self.day_count += 1
                self.busy = True
                return {
                    "index": st["index"], "trade_id": st["trade_id"], "events": events,
                    "watch": {
                        "what_just_happened": [{k: e[k] for k in ("label", "at", "detail")} for e in events],
                        "update_number_for_this_ticket": st["count"],
                        "max_updates_for_this_ticket": MAX_PER_TICKET,
                        "watching_since_ist": _hhmmss(st["watching_since"]),
                        "best_price_since_watching": st["best_price"],
                        "best_price_at_ist": _hhmmss(st["best_at"]),
                        "previous_updates_for_this_ticket": list(st["previous"][-2:]),
                    },
                }
            return None

    def finish(self, due, text=None, error=None, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.busy = False
            st = self.tickets.get(due["trade_id"])
            if st is not None and text:
                st["previous"].append(text[:600])
            self.seq += 1
            self.updates.insert(0, {
                "id": self.seq, "index": due["index"], "at": _hhmmss(now),
                "events": [e["label"] for e in due["events"]],
                "text": text, "error": error,
            })
            del self.updates[KEPT:]

    # ------------------------------------------------------------ reading
    def public(self, full=False):
        with self.lock:
            out = {"on": self.on, "seq": self.seq}
            if full:
                out.update(updates=list(self.updates),
                           limits={"per_ticket": MAX_PER_TICKET, "gap_minutes": MIN_GAP_S // 60,
                                   "per_day": DAILY_CAP,
                                   "left_today": max(0, DAILY_CAP - (self.day_count if self.day ==
                                                     dt.datetime.now(IST).strftime("%Y-%m-%d") else 0))})
            return out
