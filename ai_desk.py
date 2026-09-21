"""
ai_desk.py — Ask TradePicker trading on paper, alongside the rules
================================================================================
Asked for by the user on 17 Sep 2026: with every section of the tool readable,
let the bot pick its own entries, targets and stops - the other rules the same -
and see how it does.

WHAT THE USER CHOSE
    Paper only: AI tickets are never sent to Zerodha. One decision per index at
    each 15-minute candle close. Alongside the rule tickets, with a separate
    record. Indian indices and Bitcoin. No overtrading, and no second entry in
    a contract already traded that day (a real account averages the two buys
    into one price, which hides what each trade did).

HOW A DECISION HAPPENS
    Each index, once per closed 15-minute candle, and only while the switch is
    on and the market is open:
      * no AI ticket open  -> if every limit below allows an entry, the bot is
        asked "enter or wait"; otherwise it is not asked at all (no cost).
      * AI ticket open     -> the bot is asked "hold or exit". Its target and
        stop are checked on every tick regardless, like any ticket's.
    The bot may look anything up (bot_data.py) before it answers.

WHAT THE TOOL ENFORCES, WHATEVER THE BOT SAYS
    The same session gates as rule tickets (market hours, the opening wait, the
    closing auction, the daily loss limit on the rule book's capital), plus:
    MAX_ENTRIES_PER_DAY per market and MAX_ENTRIES_PER_INDEX per index, a
    COOLDOWN_MIN wait after an AI exit on that index, no Indian-index entry
    after NO_ENTRY_AFTER, one AI ticket per index, MAX_DECISIONS_PER_DAY model
    calls, and no contract twice in a day. An entry must name a strike in the
    live chain within STRIKE_STEPS of the money, with a live premium, a spread
    under MAX_SPREAD_PCT, a stop below and a target above that premium, and at
    least MIN_REWARD_RISK. The entry is the live premium, never the bot's number.
    Lots are the user's own lots setting.

*** PAPER ONLY. Nothing here places an order: this book has no listeners.
"""
import datetime as dt
import json
import os
import threading
import time

import config
import signal_engine
import ticket_watch
import tickets
import trade_log
from main import is_market_open, now_ist

CANDLE_MIN = 15
DECISION_DELAY_S = 45          # one REST pass (every 30s) has fetched the finished candle
STALE_S = 180                  # analysis older than this is not decided on
MAX_ENTRIES_PER_DAY = 4
MAX_ENTRIES_PER_INDEX = 2
COOLDOWN_MIN = 30
NO_ENTRY_AFTER = (15, 10)
MAX_DECISIONS_PER_DAY = {"nse_index": 90, "crypto": 100}
STRIKE_STEPS = 6
MIN_REWARD_RISK = 1.0
# Giving back a profit: once the trade has covered GIVEBACK_ARM of the way from
# entry to its target, the tool closes it if it hands back GIVEBACK_GIVE of that
# best gain. Checked on every tick, with no model call - a reversal inside one
# candle would otherwise run to the stop before the bot's next review.
GIVEBACK_ARM = 0.6
GIVEBACK_GIVE = 0.5
# A turn also wakes the bot for an unscheduled review, at most this many times
# per ticket and no closer together than this.
MAX_EVENT_REVIEWS = 3
EVENT_REVIEW_GAP_S = 300
RECENT_KEPT = 60
# Its own record, handed to the bot with every decision. Asked for by the user
# on 18 Sep 2026 as the cheap way for it to learn from its results - nothing is
# trained; it reads what happened and may adjust.
RECORD_LAST = 8               # its latest closed trades on the index, with the reason it gave
RECORD_MIN_SAMPLE = 30        # under this many trades, patterns are flagged as too few to lean on
LOOP_S = 15


class AIDesk:
    def __init__(self, feed, now=None, clock=None, start=True):
        self.feed = feed
        self.market = feed.market
        self.now = now or now_ist
        self.clock = clock or time.time
        base = os.path.dirname(trade_log.user_log_path(feed.email, feed.market))
        self.state_path = os.path.join(base, "ai_desk.json")
        self.decisions_path = os.path.join(base, "ai_decisions.jsonl")
        self.book = tickets.TicketBook(feed.email, feed.market, path=os.path.join(base, "ai_trades.csv"))
        self.book.auto_rearm = False
        self.lock = threading.RLock()
        self.enabled = {}            # index -> switched on (each index has its own switch)
        self.day = None
        self.decisions_today = 0
        self.decisions_by_index = {}
        self.tokens_today = {"input": 0, "output": 0}
        self.entries = {}            # index -> entries today
        self.contracts = []          # "NIFTY|25000|CE|2026-09-22" traded today
        self.last_candle = {}        # index -> iso of the candle close last decided on
        self.last_exit = {}          # index -> epoch of the last AI exit
        self.recent = []             # newest first
        self.decision_no = {}        # {"day": iso, index: the day's count so far}
        self.busy = None
        # The same six triggers the automatic updates watch for. Used here to
        # wake the bot between candle closes when a trade turns.
        self.watch = ticket_watch.Watch(None)
        self.watch.on = True
        self.pending = {}            # index -> [what just happened]
        self.event_reviews = {}      # trade_id -> reviews spent
        self.last_event_review = {}  # index -> epoch
        self.peak = {}               # trade_id -> best progress from entry, in premium
        self._tok = {}               # trade_id -> streamed contract handle
        # The desk's own lots, per market - asked for on 20 Sep 2026. None
        # means "the same as the rule tickets' lots setting".
        self._lots = None
        self._tok_tried = {}
        self._load()
        self.thread = None
        self._thread_lock = threading.Lock()
        if start:
            self.ensure_running()

    def ensure_running(self):
        """Start the decision loop if it is not running. Called from the feed's
        touch(), once the feed is registered - a loop started earlier would
        find no feed of its own and stop at once."""
        with self._thread_lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True, name=f"ai:{self.feed.key}")
                self.thread.start()

    # ------------------------------------------------------------ disk
    def _load(self):
        try:
            with open(self.state_path) as fh:
                s = json.load(fh)
        except Exception:
            return
        names = self.feed.instruments()
        self.enabled = {k: bool(v) for k, v in (s.get("enabled") or {}).items() if k in names}
        if s.get("on") and "enabled" not in s:            # one switch for the whole market, before 18 Sep 2026
            self.enabled = {k: True for k in names}
        self.day = s.get("day")
        self.decisions_today = int(s.get("decisions_today") or 0)
        self.decisions_by_index = s.get("decisions_by_index") or {}
        self.tokens_today = s.get("tokens_today") or self.tokens_today
        self.entries = s.get("entries") or {}
        self.contracts = s.get("contracts") or []
        self.last_candle = s.get("last_candle") or {}
        self.last_exit = s.get("last_exit") or {}
        self.recent = s.get("recent") or []
        self.decision_no = s.get("decision_no") or {}
        self._lots = float(s["lots"]) if s.get("lots") not in (None, "") else None

    def _save(self):
        with self.lock:
            data = {"enabled": self.enabled, "day": self.day, "decisions_today": self.decisions_today,
                    "decisions_by_index": self.decisions_by_index,
                    "tokens_today": self.tokens_today, "entries": self.entries, "contracts": self.contracts,
                    "last_candle": self.last_candle, "last_exit": self.last_exit,
                    "recent": self.recent[:RECENT_KEPT], "lots": self._lots,
                    "decision_no": self.decision_no}
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh, default=str)
            os.replace(tmp, self.state_path)

    def _roll_day(self):
        today = self.now().strftime("%Y-%m-%d")
        if self.day != today:
            self.day, self.decisions_today, self.decisions_by_index = today, 0, {}
            self.tokens_today = {"input": 0, "output": 0}
            self.entries, self.contracts = {}, []
            self.decision_no = {}                    # the day's numbering starts again

    @property
    def on(self):
        return any(self.enabled.values())

    def set_on(self, on, index=None):
        """Switch one index on or off - or every index in the market when none is named."""
        names = self.feed.instruments()
        if index is not None and index not in names:
            raise ValueError(f"{index} is not in this market.")
        with self.lock:
            for k in ([index] if index else names):
                self.enabled[k] = bool(on)
            self._save()
        return self.enabled.get(index) if index else self.on

    def _record(self, index, kind, action, reason, extra=None):
        at = self.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            # Each index numbers its own decisions within the day, so the page can
            # say "#4 today on NIFTY" and the number still means that after a
            # restart or once older entries fall off the list.
            if self.decision_no.get("day") != at[:10]:
                self.decision_no = {"day": at[:10]}
            n = int(self.decision_no.get(index) or 0) + 1
            self.decision_no[index] = n
        entry = {"at": at, "index": index, "kind": kind, "action": action, "reason": reason, "n": n}
        entry.update(extra or {})
        with self.lock:
            self.recent.insert(0, entry)
            del self.recent[RECENT_KEPT:]
        try:
            with open(self.decisions_path, "a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass
        return entry

    # ------------------------------------------------------------ the loop
    def _alive(self):
        import feeds
        with feeds._lock:
            return feeds._feeds.get(self.feed.key) is self.feed

    def _loop(self):
        import feeds
        while not feeds._stopping.is_set() and self._alive():
            try:
                self.step()
            except Exception as exc:
                self.feed._note_fault("ai desk", f"{type(exc).__name__}: {exc}")
            time.sleep(LOOP_S)

    def _candle_close(self, now):
        return now.replace(minute=now.minute - now.minute % CANDLE_MIN, second=0, microsecond=0)

    def step(self, client=None):
        """Decide for every index whose latest 15-minute candle has not been decided on."""
        import market_bot
        if not self.on:
            return
        self._roll_day()
        now = self.now()
        self._event_reviews(client)
        for name in self.feed.instruments():
            if not self.enabled.get(name):
                continue
            if not is_market_open(now, name):
                continue
            close = self._candle_close(now)
            if self.last_candle.get(name) == close.isoformat():
                continue
            if (now - close).total_seconds() < DECISION_DELAY_S:
                continue
            with self.feed.lock:
                rec = ((self.feed.state.get("indices") or {}).get(name) or {}).get("rec")
            if not rec or not self._fresh():
                continue
            if not market_bot.key_present():
                continue
            self.last_candle[name] = close.isoformat()
            if self.decisions_today >= MAX_DECISIONS_PER_DAY.get(self.market, 90):
                if not any(r.get("kind") == "cap" and r["at"][:10] == self.day for r in self.recent):
                    self._record(name, "cap", "none", "Today's decision cap is reached - no more model calls today.")
                continue
            trade = self._open_trade(name)
            if trade is not None:
                self._review(name, trade, rec, client)
            else:
                why = self.entry_block(name)
                if why:
                    continue                     # not asked at all: nothing to decide, nothing billed
                self._entry(name, rec, client)
            self._save()
        self._save()

    def _event_reviews(self, client=None):
        """A trade that turned, reviewed before the next candle close."""
        import market_bot
        for name in list(self.pending):
            with self.lock:
                why = self.pending.pop(name, [])
            trade = self._open_trade(name)
            if not why or trade is None or not self.enabled.get(name) or not self._fresh():
                continue
            if not market_bot.key_present() or self.decisions_today >= MAX_DECISIONS_PER_DAY.get(self.market, 90):
                continue
            tid = trade.get("trade_id")
            if self.event_reviews.get(tid, 0) >= MAX_EVENT_REVIEWS:
                continue
            last = self.last_event_review.get(name)
            if last and self.clock() - last < EVENT_REVIEW_GAP_S:
                continue
            with self.feed.lock:
                rec = ((self.feed.state.get("indices") or {}).get(name) or {}).get("rec")
            if not rec:
                continue
            self.event_reviews[tid] = self.event_reviews.get(tid, 0) + 1
            self.last_event_review[name] = self.clock()
            self._review(name, trade, rec, client, why=sorted(set(why)))
            self._save()

    def _fresh(self):
        """Whether the feed's analysis is current. A feed whose Zerodha token
        lapsed keeps its last reading on screen; deciding on it would be
        deciding on the past."""
        if self.feed.state.get("feed") not in (None, "ok"):
            return False
        live_at = getattr(self.feed, "live_at", None)
        return bool(live_at) and time.time() - live_at <= STALE_S

    # ------------------------------------------------------------ limits
    def _open_trade(self, name):
        with self.book.lock:
            b = self.book.books.get(name)
            t = b.trade if b else None
            return t if t is not None and t["status"] == "OPEN" else None

    def _sync_rules(self):
        rules = self.feed.tickets
        self.book.lots = self.lots
        self.book.capital = rules.capital
        self.book.risk_pct = rules.risk_pct

    @property
    def lots(self):
        """How many lots each AI ticket is for: the desk's own setting, or the
        rule tickets' lots until one is chosen."""
        return self._lots if self._lots is not None else self.feed.tickets.lots

    def set_lots(self, lots):
        """Choose the desk's lots from this market's choices (the nearest one),
        or None to follow the rule tickets' setting again. Open tickets keep
        the lots they were opened with."""
        choices = config.lot_choices(self.market)
        if lots in (None, ""):
            self._lots = None
        else:
            self._lots = float(min(choices, key=lambda c: abs(c - float(lots))))
            if self._lots.is_integer():
                self._lots = int(self._lots)
        self._save()
        return self.lots

    @staticmethod
    def _cost(entry, lot_size, lots):
        """What the premium cost to buy: entry x lot size x lots, or None."""
        try:
            return round(float(entry) * float(lot_size) * float(lots or 1), 2)
        except (TypeError, ValueError):
            return None

    def entry_block(self, name):
        """Why no AI entry may be considered on this index right now, or None."""
        self._sync_rules()
        gate = self.book.entry_block()
        if gate:
            return gate[1]
        now = self.now()
        if not config.market_for(name).get("always_open") and (now.hour, now.minute) >= NO_ENTRY_AFTER:
            return f"No AI entries after {NO_ENTRY_AFTER[0]}:{NO_ENTRY_AFTER[1]:02d}."
        if sum(self.entries.values()) >= MAX_ENTRIES_PER_DAY:
            return f"{MAX_ENTRIES_PER_DAY} AI entries today in this market - the daily maximum."
        if self.entries.get(name, 0) >= MAX_ENTRIES_PER_INDEX:
            return f"{MAX_ENTRIES_PER_INDEX} AI entries on {name} today - the maximum per index."
        last = self.last_exit.get(name)
        if last and self.clock() - last < COOLDOWN_MIN * 60:
            return f"Cooling down after the last AI exit on {name}."
        return None

    def _live_info(self, name):
        """Whether this index's AI tickets are also placed as real Zerodha orders
        and, only when they are, whether the account's cash covers one ticket at
        about the suggested premium - yes or no and any shortfall, never the
        balance itself."""
        live = getattr(self.feed, "live", None)
        if live is None or not live.enabled_ai.get(name):
            return {"on": False}
        with self.feed.lock:
            pub = (self.feed.state["indices"].get(name) or {}).get("public") or {}
        try:
            need = float(pub.get("ltp")) * int(pub.get("lot_size")) * float(self.lots or 1)
        except (TypeError, ValueError):
            need = None
        return {"on": True, "funds_for_one_ticket_at_the_suggested_premium":
                live.funds_check(need) if need else {"enough": None, "note": "no suggested premium right now"}}

    def _ticket_flow(self, name):
        """Order flow on an open AI ticket's own contract, off the tick socket."""
        import feeds
        import kite_flow
        trade = self._open_trade(name)
        st = self.feed.streamer
        tok = self._tok.get(trade.get("trade_id")) if trade else None
        if tok is None or st is None or st is feeds._NO_STREAM or not hasattr(st, "book"):
            return None
        return kite_flow.order_flow(st.book(tok, max_age=kite_flow.MAX_AGE_S))

    def _desk_info(self, name):
        tickets = {}
        for k in self.feed.instruments():
            if self._open_trade(k):
                t = self.book.public(k).get("ticket")
                if isinstance(t, dict):
                    flow = self._ticket_flow(k)
                    if flow:
                        t = dict(t, order_flow=flow)
                tickets[k] = t
        live = self._live_info(name)
        return {"paper_only": not live.get("on"), "real_orders": live, "your_open_tickets": tickets,
                "entries_today": dict(self.entries), "max_entries_per_day": MAX_ENTRIES_PER_DAY,
                "max_entries_per_index": MAX_ENTRIES_PER_INDEX, "contracts_already_traded_today": list(self.contracts),
                "cooldown_minutes_after_an_exit": COOLDOWN_MIN, "min_reward_to_risk": MIN_REWARD_RISK,
                "strike_within_steps_of_atm": STRIKE_STEPS, "lots": self.lots,
                "max_spread_pct_on_this_index": config.max_spread_pct(name),
                "contracts_per_lot": (config.INSTRUMENTS.get(name) or {}).get("contracts_per_lot", 1),
                "give_back_rule": (f"the tool closes a trade on its own once it has covered "
                                   f"{GIVEBACK_ARM * 100:.0f}% of the way to its target and then hands back "
                                   f"{GIVEBACK_GIVE * 100:.0f}% of that best gain"),
                "your_recent_decisions_on_this_index": [r for r in self.recent if r.get("index") == name][:4],
                "your_track_record": self.track_record(name)}

    # ------------------------------------------------------------ its own record
    @staticmethod
    def _exit_kind(status):
        low = (status or "").lower()
        if "give-back rule" in low:
            return "give_back_rule"
        if "ai exit" in low:
            return "your_exit"
        if trade_log.is_target_close(status):
            return "target"
        if "stop-loss hit" in low:
            return "stop"
        if "market closed" in low or "intraday close" in low:
            return "session_close"
        if "cleared manually" in low:
            return "cleared_by_user"
        return "other"

    def _entry_reasons(self):
        """{(date, index, strike, CE/PE): the reason the bot gave for that entry}."""
        out = {}
        try:
            with open(self.decisions_path) as fh:
                for line in fh:
                    if '"action": "enter"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    c = str(r.get("contract") or "").split("|")
                    if len(c) >= 3:
                        out[(str(r.get("at", ""))[:10], c[0], c[1], c[2])] = r.get("reason") or ""
        except OSError:
            pass
        return out

    def _fills(self):
        """{trade_id: real fill} of the AI tickets that ran as live Zerodha orders."""
        live = getattr(self.feed, "live", None)
        if live is None:
            return {}
        try:
            return {f.get("trade_id"): f for f in live.fills("ai")}
        except Exception:
            return {}

    @staticmethod
    def _real_fills(pairs):
        """Paper against real, over the AI trades that were live: Zerodha's own
        average prices, both gross of charges like the paper figures."""
        def mean(xs):
            return round(sum(xs) / len(xs), 2) if xs else None
        ent = [float(f["entry_avg"]) - float(f["paper_entry"]) for _r, f in pairs
               if f.get("entry_avg") is not None and f.get("paper_entry") not in (None, "")]
        ext = [float(f["exit_avg"]) - float(r["exit"]) for r, f in pairs
               if f.get("exit_avg") is not None and r.get("exit") not in (None, "")]
        gross = [float(f["gross_pnl"]) for _r, f in pairs if f.get("gross_pnl") is not None]
        return {"live_trades": len(pairs), "real_gross_pnl": round(sum(gross), 2) if gross else None,
                "paper_pnl_of_the_same_trades": round(sum(float(r["pnl"]) for r, _f in pairs), 2),
                "avg_entry_slippage_points": mean(ent), "avg_exit_slippage_points": mean(ext),
                "how_to_read": ("real = Zerodha's average buy and sell prices; paper = the ticket's. Entry "
                                "slippage above 0 means the real buy cost more than paper; exit slippage below 0 "
                                "means the real sell got less. Both P&L figures are before charges.")}

    def track_record(self, name):
        """What its own closed trades in this market did - overall, on this index,
        by how they ended, by side and by time of day - and its latest trades on
        this index with the reason it gave and the readings at entry."""
        try:
            rows = trade_log._read_rows(self.book.path)
        except Exception:
            rows = []
        opens = {r.get("trade_id"): r for r in rows if r.get("event") == "OPEN"}
        closes = [r for r in rows if r.get("event") == "CLOSE"]
        done = [r for r in closes if r.get("pnl") not in (None, "")]
        abandoned = len(closes) - len(done)
        if not done:
            return {"closed_trades": 0, "abandoned": abandoned,
                    "note": "No closed AI trades yet in this market - nothing to learn from so far."}

        def stats(sel):
            p = [float(r["pnl"]) for r in sel]
            if not p:
                return {"n": 0}
            wins, losses = [x for x in p if x > 0], [x for x in p if x <= 0]
            return {"n": len(p), "win_rate_pct": round(100.0 * len(wins) / len(p)), "net": round(sum(p), 2),
                    "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
                    "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
                    "per_trade": round(sum(p) / len(p), 2)}

        def bucket(r):
            o = opens.get(r.get("trade_id")) or {}
            try:
                h = int(str(o.get("time_ist") or "")[:2])
            except ValueError:
                return "unknown"
            if config.market_for(r.get("index")).get("always_open"):
                return f"{h - h % 6:02d}-{h - h % 6 + 6:02d} IST"
            return "before 11:00" if h < 11 else "11:00-13:00" if h < 13 else "after 13:00"

        mine = [r for r in done if r.get("index") == name]
        groups = {}
        for key, fn in (("by_exit", lambda r: self._exit_kind(r.get("status"))),
                        ("by_side_on_this_index", lambda r: r.get("option_type") or "?"),
                        ("by_entry_time_on_this_index", bucket)):
            sel = done if key == "by_exit" else mine
            g = {}
            for r in sel:
                g.setdefault(fn(r), []).append(r)
            groups[key] = {k: stats(v) for k, v in g.items()}
        reasons = self._entry_reasons()
        fills = self._fills()
        last = []
        for r in mine[-RECORD_LAST:][::-1]:
            o = opens.get(r.get("trade_id")) or {}
            last.append({
                "date": r.get("date"), "entered": (o.get("time_ist") or "")[:5], "closed": (r.get("time_ist") or "")[:5],
                "contract": f"{r.get('strike')} {r.get('option_type')}", "entry": r.get("entry"), "exit": r.get("exit"),
                "pnl": float(r["pnl"]), "how_it_ended": self._exit_kind(r.get("status")),
                "at_entry": {k: o.get(k) or None for k in ("adx", "rsi", "macd_hist", "vwap_gap")},
                "your_reason_then": (reasons.get((o.get("date") or r.get("date"), name, str(r.get("strike")),
                                                  r.get("option_type"))) or "")[:220] or None})
            f = fills.get(r.get("trade_id"))
            if f:
                last[-1]["real_fill"] = {"bought_at": f.get("entry_avg"), "sold_at": f.get("exit_avg"),
                                         "qty": f.get("qty"), "gross_pnl": f.get("gross_pnl")}
        out = {"closed_trades": len(done), "abandoned_no_exit_price": abandoned,
               "all_indices": stats(done), "this_index": stats(mine), **groups,
               "your_last_trades_on_this_index": last}
        pairs = [(r, fills[r.get("trade_id")]) for r in done if r.get("trade_id") in fills]
        if pairs:
            out["real_fills"] = self._real_fills(pairs)
        if len(done) < RECORD_MIN_SAMPLE:
            out["caution"] = (f"Only {len(done)} closed trades so far - too few to trust any pattern here. Note it; "
                              f"do not change how you trade because of it until there are at least "
                              f"{RECORD_MIN_SAMPLE}.")
        return out

    # ------------------------------------------------------------ deciding
    def _ask(self, kind, name, client, why=None):
        import bot_data
        import market_bot
        snap = self.feed.snapshot()
        context = market_bot.build_context(snap, self.market, name, user=self.feed.email)
        desk = self._desk_info(name)
        if why:
            desk["what_just_happened"] = why
        self.decisions_today += 1
        self.decisions_by_index[name] = self.decisions_by_index.get(name, 0) + 1
        self.busy = name
        try:
            decision, meta = market_bot.decide(kind, name, context, desk,
                                               tools_ctx=bot_data.Ctx(self.feed.email, self.market, name),
                                               client=client)
        finally:
            self.busy = None
        self.tokens_today["input"] += meta.get("input_tokens") or 0
        self.tokens_today["output"] += meta.get("output_tokens") or 0
        return decision, meta

    def _entry(self, name, rec, client):
        import market_bot
        try:
            d, meta = self._ask("entry", name, client)
        except market_bot.BotError as exc:
            return self._record(name, "entry", "error", str(exc))
        if d.get("action") != "enter":
            return self._record(name, "entry", "wait", d.get("reason") or "", {"looked_at": meta.get("looked_at")})
        with self.feed.lock:
            rec = ((self.feed.state.get("indices") or {}).get(name) or {}).get("rec") or rec
        ok, why, plan = self.validate(name, rec, d)
        if not ok:
            return self._record(name, "entry", "rejected", d.get("reason") or "",
                                {"proposal": {k: d.get(k) for k in ("option_type", "strike", "target", "stop")},
                                 "rejected_because": why, "looked_at": meta.get("looked_at")})
        if self.thread is not None and not self._alive():
            return None                  # this desk's feed was replaced while it decided
        if not self.enabled.get(name):
            return self._record(name, "entry", "rejected", d.get("reason") or "",
                                {"rejected_because": f"the AI desk for {name} was switched off while it decided"})
        self._open(name, rec, plan, d.get("reason") or "")
        return self._record(name, "entry", "enter", d.get("reason") or "",
                            {"contract": plan["contract"], "entry": plan["ltp"], "target": plan["target"],
                             "stop": plan["stop"], "looked_at": meta.get("looked_at")})

    def _review(self, name, trade, rec, client, why=None):
        import market_bot
        try:
            d, meta = self._ask("review", name, client, why=why)
        except market_bot.BotError as exc:
            return self._record(name, "review", "error", str(exc))
        if d.get("action") == "exit":
            reason = (d.get("reason") or "").strip()
            short = reason.split(". ")[0][:90] or "the bot's call"
            self.book.close_ticket(name, f"CLOSED — AI exit: {short}")
            self.last_exit[name] = self.clock()
            return self._record(name, "review", "exit", reason, {"looked_at": meta.get("looked_at")})
        return self._record(name, "review", "hold", d.get("reason") or "", {"looked_at": meta.get("looked_at")})

    # ------------------------------------------------------------ checking a proposal
    def validate(self, name, rec, d):
        """(ok, why, plan). Every number that matters is re-derived from live data."""
        side = str(d.get("option_type") or "").upper()
        if side not in ("CE", "PE"):
            return False, "option_type must be CE or PE", None
        try:
            strike, target, stop = float(d.get("strike")), float(d.get("target")), float(d.get("stop"))
        except (TypeError, ValueError):
            return False, "strike, target and stop must all be numbers", None
        chain = rec.get("option_chain") or {}
        strikes = sorted(s["strike"] for s in chain.get("strikes") or [])
        if not chain.get("available", True) or not strikes:
            return False, "no live option chain to check the strike against", None
        if strike not in strikes:
            return False, f"{strike:g} is not a strike in the live chain", None
        spot = rec.get("spot") or chain.get("spot")
        if spot is None:
            return False, "no spot price", None
        atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        if abs(strikes.index(strike) - atm_i) > STRIKE_STEPS:
            return False, f"{strike:g} is more than {STRIKE_STEPS} strikes from the money ({strikes[atm_i]:g})", None
        ltp = next((s.get("call_ltp" if side == "CE" else "put_ltp") for s in chain["strikes"]
                    if s["strike"] == strike), None)
        if not ltp or ltp <= 0:
            return False, "no live premium for that contract", None
        expiry = str(chain.get("expiry") or "")[:10]
        contract = f"{name}|{strike:g}|{side}|{expiry}"
        if contract in self.contracts:
            return False, ("that contract was already traded today - a second buy would average into the "
                           "first in a real account"), None
        cap = config.max_spread_pct(name)
        q = signal_engine._find_strike_quote(chain, strike, side) or {}
        if cap and q.get("pct") is not None and q["pct"] > cap:
            return False, f"the spread is {q['pct']:.1f}%, over the {cap:g}% limit", None
        if not stop < ltp * 0.97:
            return False, f"the stop {stop:g} must be below the live premium {ltp:g}", None
        if stop < ltp * 0.3:
            return False, f"the stop {stop:g} risks more than 70% of the premium {ltp:g}", None
        if not target > ltp * 1.03:
            return False, f"the target {target:g} must be above the live premium {ltp:g}", None
        rr = (target - ltp) / (ltp - stop)
        if rr < MIN_REWARD_RISK:
            return False, f"reward to risk is {rr:.2f}, under {MIN_REWARD_RISK:g}", None
        return True, "", {"side": side, "strike": strike, "target": round(target, 2), "stop": round(stop, 2),
                          "ltp": ltp, "expiry": expiry, "contract": contract, "rr": round(rr, 2)}

    def _open(self, name, rec, plan, reason):
        self._sync_rules()
        r = dict(rec)
        r.update(option_type=plan["side"], bias="BULLISH" if plan["side"] == "CE" else "BEARISH",
                 suggested_strike=int(plan["strike"]) if float(plan["strike"]).is_integer() else plan["strike"],
                 premium_source="live", live_ltp=plan["ltp"],
                 premium_targets=[plan["target"]] * 3, premium_stop_loss=plan["stop"],
                 index_targets=[], index_stop_loss=None, strictness="ai",
                 # The log's signal columns describe THIS trade, not the rule
                 # signal the reading came with.
                 confidence="AI", score=None, risk_points=None, reach_points=None,
                 reach_to_risk=plan["rr"])
        with self.book.lock:
            b = self.book.books[name]
            self.book._open(b, r)
            b.trade["exit_at"] = "T1"
            b.trade["ai_reason"] = reason
        self.entries[name] = self.entries.get(name, 0) + 1
        self.contracts.append(plan["contract"])

    # ------------------------------------------------------------ prices
    def track(self, name, rec):
        """A fresh reading from the analysis: check the open AI ticket's levels."""
        evs = self.book.track(name, rec)
        self._after(name, evs)
        trade = self._open_trade(name)
        if trade is None:
            return
        self._giveback(name, trade, self.book._price_for(trade, rec))
        with self.feed.lock:
            idx = ((self.feed.state.get("indices") or {}).get(name) or {}).get("public")
        self._events(name, trade, idx)

    def _events(self, name, trade, idx):
        """Note a turn in the trade - halfway to the stop, ADX below the gate, MACD
        against it, no progress - so the bot can be asked before the next close."""
        pub = self.book.public(name).get("ticket")
        if not pub or not pub.get("open"):
            return
        fired = self.watch.observe(name, trade.get("trade_id"), pub, idx,
                                   config.strictness().get("adx"), self.clock())
        self.watch.forget_except({t.get("trade_id") for t in
                                  (self._open_trade(k) for k in self.feed.instruments()) if t})
        if fired:
            with self.lock:
                self.pending.setdefault(name, [])
                self.pending[name] += [e["label"] for e in fired]

    def _giveback(self, name, trade, price):
        """Hand back too much of a gain and the trade is closed, on the tick."""
        if price is None:
            return
        entry, target = trade.get("entry_ltp"), (trade.get("premium_targets") or [None])[0]
        if not entry or not target or target <= entry:
            return
        tid = trade.get("trade_id")
        gain, room = price - entry, target - entry
        best = max(self.peak.get(tid, 0.0), gain)
        self.peak[tid] = best
        if best < GIVEBACK_ARM * room or gain > best * GIVEBACK_GIVE:
            return
        pct, back = best / room * 100.0, (best - gain) / best * 100.0
        self.book.close_ticket(name, f"CLOSED — AI give-back rule: {pct:.0f}% of the way to the target, "
                                     f"then gave back {back:.0f}% of that", price=price)
        self.last_exit[name] = self.clock()
        self.peak.pop(tid, None)
        self._record(name, "rule", "exit", f"Reached {pct:.0f}% of the way to the target ({target:g}) at "
                                           f"{entry + best:.2f}, then gave back {back:.0f}% of that gain. Closed by "
                                           f"the give-back rule, not by the bot.",
                     {"contract": f"{name}|{trade.get('strike')}|{trade.get('option_type')}"})
        self._save()

    def price_tick(self):
        """The AI ticket's own contract, off the stream, on every tick."""
        for name in self.feed.instruments():
            trade = self._open_trade(name)
            if trade is None:
                continue
            px = self._stream_price(name, trade)
            if px is None:
                continue
            self.book.live_price(name, px)
            self._after(name, self.book.tick_price(name, px))
            if self._open_trade(name) is not None:
                self._giveback(name, trade, px)

    def _after(self, name, evs):
        if any(ev.get("kind") == "closed" for ev in evs or []):
            self.last_exit[name] = self.clock()
            self._save()

    def _stream_price(self, name, trade):
        import feeds
        tid = trade.get("trade_id")
        handle = self._tok.get(tid)
        if handle is None:
            if self.clock() - self._tok_tried.get(tid, 0) < 30:
                return None
            self._tok_tried[tid] = self.clock()
            handle = self._tok[tid] = self._subscribe(name, trade)
            if handle is None:
                del self._tok[tid]
                return None
        if self.feed.streamer is feeds._NO_STREAM:
            ds = self.feed.dstream
            return ds.mark_usd(handle, config.crypto_index(name)) if ds else None
        st = self.feed.streamer
        return st.price(handle) if st is not None else None

    def _subscribe(self, name, trade):
        import feeds
        try:
            if self.feed.streamer is feeds._NO_STREAM:
                ds = self.feed.dstream
                inst = self.feed._provider_for(name, None).option_instrument(
                    name, trade["strike"], trade["option_type"], trade.get("expiry"))
                if ds is None or not inst:
                    return None
                ds.subscribe_ticker(inst)
                return inst
            import user_kite
            from data_providers import KiteDataProvider
            st, token = self.feed.streamer, user_kite.token_for(self.feed.email)
            if st is None or not token:
                return None
            tok = KiteDataProvider(config.KITE_API_KEY, token).option_token(
                name, trade["strike"], trade["option_type"], str(trade.get("expiry") or "")[:10] or None)
            if not tok:
                return None
            st.subscribe([tok], full=True)       # FULL carries its order flow, for the bot
            return tok
        except Exception:
            return None

    # ------------------------------------------------------------ reading
    def public(self):
        with self.lock:
            rows = []
            try:
                rows = [r for r in trade_log._read_rows(self.book.path) if r.get("event") == "CLOSE"]
            except Exception:
                pass
            today = self.now().strftime("%Y-%m-%d")
            names = self.feed.instruments()

            def summary(sel):
                pnl = [float(r["pnl"]) for r in sel if r.get("pnl") not in (None, "")]
                tp = [float(r["pnl"]) for r in sel if r.get("date") == today and r.get("pnl") not in (None, "")]
                return {"closed": len(pnl), "wins": sum(1 for p in pnl if p > 0),
                        "losses": sum(1 for p in pnl if p <= 0), "net": round(sum(pnl), 2),
                        "today": {"closed": len(tp), "net": round(sum(tp), 2)},
                        "last": [dict({k: r.get(k) for k in ("date", "time_ist", "index", "strike", "option_type",
                                                             "entry", "exit", "pnl", "status", "lots", "lot_size")},
                                      cost=self._cost(r.get("entry"), r.get("lot_size"), r.get("lots")))
                                 for r in sel[-8:][::-1]]}

            opened = {}
            for k in names:
                if self._open_trade(k):
                    t = self.book.public(k).get("ticket")
                    if t is not None:
                        t["reason"] = self.book.books[k].trade.get("ai_reason")
                    opened[k] = t
            fresh_day = self.day == today
            return {"on": self.on, "enabled": {k: bool(self.enabled.get(k)) for k in names},
                    "indices": names, "paper_only": True, "market": self.market, "busy": self.busy,
                    "lots": self.lots, "lots_follow_rules": self._lots is None,
                    "lot_choices": config.lot_choices(self.market),
                    "open": opened, "recent": self.recent[:40], "record": summary(rows),
                    "records": {k: summary([r for r in rows if r.get("index") == k]) for k in names},
                    "limits": {"entries_today": dict(self.entries) if fresh_day else {},
                               "max_entries_per_day": MAX_ENTRIES_PER_DAY,
                               "max_entries_per_index": MAX_ENTRIES_PER_INDEX, "cooldown_min": COOLDOWN_MIN,
                               "decisions_today": self.decisions_today if fresh_day else 0,
                               "decisions_by_index": dict(self.decisions_by_index) if fresh_day else {},
                               "max_decisions_per_day": MAX_DECISIONS_PER_DAY.get(self.market, 90),
                               "tokens_today": self.tokens_today if fresh_day else {"input": 0, "output": 0}}}
