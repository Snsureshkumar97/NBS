#!/usr/bin/env python3
"""The Market Bot's automatic updates on an open ticket.

Fakes only - no real Anthropic call, a temporary log folder. What matters: it
is off until switched on, each of the six events fires when it should and not
on a flicker, the caps and the gap actually bound how often the bot is called,
nothing is sent about a ticket that has closed, and the feed and the endpoint
are wired to it.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anthropic

import config
import market_bot as mb
import ticket_watch as tw
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


T0 = 1_789_000_000.0          # a fixed clock
GATE = 20.0


def ticket(now=130.0, entry=130.0, targets=(156.0, 182.0, 227.0), stop=97.5, hit=None,
           option_type="CE", tracked_on="premium", exit_at="T2", open_=True):
    return {"index": "NIFTY", "open": open_, "entry": entry, "now": now, "targets": list(targets),
            "stop": stop, "option_type": option_type, "tracked_on": tracked_on, "exit_at": exit_at,
            "hit": dict({"T1": False, "T2": False, "T3": False}, **(hit or {})),
            "hit_time": {"T1": "11:02:03" if (hit or {}).get("T1") else None}}


def idx(adx=25.0, macd=1.5):
    return {"adx": adx, "macd_hist": macd}


def fresh(on=True):
    w = tw.Watch(os.path.join(tempfile.mkdtemp(), "trades.csv.botwatch.json"))
    if on:
        w.set_on(True)
    return w


def codes(evs):
    return [e["code"] for e in evs]


print("1. OFF UNTIL SWITCHED ON, AND IT STAYS THE WAY IT WAS SET")
path = os.path.join(tempfile.mkdtemp(), "trades.csv.botwatch.json")
w = tw.Watch(path)
check("a new account starts with it off", w.on is False)
check("nothing is watched while off - not even a T1 hit",
      w.observe("NIFTY", "N-1", ticket(now=160, hit={"T1": True}), idx(), GATE, T0) == [] and not w.tickets)
w.set_on(True)
check("switched on survives a restart (read back from disk)", tw.Watch(path).on is True)
w.observe("NIFTY", "N-1", ticket(), idx(), GATE, T0)
w.set_on(False)
check("switched off survives a restart too, and forgets what it was watching",
      tw.Watch(path).on is False and not w.tickets)
check("a feed with no account (free mode) has no settings file and stays off",
      tw.Watch(None).on is False)

print("2. T1")
w = fresh()
evs = w.observe("NIFTY", "N-1", ticket(now=157, hit={"T1": True}), idx(), GATE, T0)
check("T1 hit fires at once - it is a fact, not a reading that flickers", codes(evs) == ["t1"], evs)
check("...and only once", w.observe("NIFTY", "N-1", ticket(now=158, hit={"T1": True}), idx(), GATE, T0 + 5) == [])
w = fresh()
check("not when T1 IS the exit target (that hit closes the ticket instead)",
      w.observe("NIFTY", "N-1", ticket(now=157, hit={"T1": True}, exit_at="T1"), idx(), GATE, T0) == [])

print("3. HALFWAY TO THE STOP - HELD, NOT A BLIP")
w = fresh()
half = 130 - 0.5 * (130 - 97.5)                    # 113.75
check("first touch does not fire", w.observe("NIFTY", "N-1", ticket(now=113), idx(), GATE, T0) == [])
check("a bounce back above resets it", w.observe("NIFTY", "N-1", ticket(now=120), idx(), GATE, T0 + 10) == [])
w.observe("NIFTY", "N-1", ticket(now=113), idx(), GATE, T0 + 11)
check("back below but not for long enough yet",
      w.observe("NIFTY", "N-1", ticket(now=112), idx(), GATE, T0 + 11 + tw.HOLD_PRICE_S - 1) == [])
evs = w.observe("NIFTY", "N-1", ticket(now=112), idx(), GATE, T0 + 11 + tw.HOLD_PRICE_S)
check("held for HOLD_PRICE_S: fires", codes(evs) == ["half_stop"], evs)

print("4. DIRECTION IS RIGHT FOR EVERY KIND OF TICKET")
w = fresh()
t = ticket(entry=25000, now=25030, targets=(24950, 24900, 24850), stop=25060, option_type="PE",
           tracked_on="index")
w.observe("NIFTY", "P-1", t, idx(macd=-1.0), GATE, T0)
evs = w.observe("NIFTY", "P-1", t, idx(macd=-1.0), GATE, T0 + tw.HOLD_PRICE_S)
check("an index-tracked PE with the index RISING toward its stop is halfway to the stop",
      codes(evs) == ["half_stop"], evs)
w = fresh()
t = ticket(entry=25000, now=24970, targets=(24950, 24900, 24850), stop=25060, option_type="PE",
           tracked_on="index")
w.observe("NIFTY", "P-2", t, idx(macd=-1.0), GATE, T0)
check("...and the index FALLING is not", w.observe("NIFTY", "P-2", t, idx(macd=-1.0), GATE, T0 + 60) == [])
w = fresh()
t = ticket(now=112, option_type="PE", tracked_on="premium")
w.observe("NIFTY", "P-3", t, idx(macd=-1.0), GATE, T0)
check("a premium-tracked PE: the premium falling is what hurts, same as a CE",
      codes(w.observe("NIFTY", "P-3", t, idx(macd=-1.0), GATE, T0 + tw.HOLD_PRICE_S)) == ["half_stop"])

print("5. GAVE BACK AFTER T1")
w = fresh()
w.observe("NIFTY", "N-1", ticket(now=157, hit={"T1": True}), idx(), GATE, T0)
check("still well above the halfway line: nothing more",
      w.observe("NIFTY", "N-1", ticket(now=150, hit={"T1": True}), idx(), GATE, T0 + 30) == [])
w.observe("NIFTY", "N-1", ticket(now=142, hit={"T1": True}), idx(), GATE, T0 + 40)   # 130 + 0.5*26 = 143
evs = w.observe("NIFTY", "N-1", ticket(now=141, hit={"T1": True}), idx(), GATE, T0 + 40 + tw.HOLD_PRICE_S)
check("back under half the entry->T1 move, held: fires", codes(evs) == ["gave_back"], evs)

print("6. ADX BELOW THE TREND GATE")
w = fresh()
w.observe("NIFTY", "N-1", ticket(), idx(adx=24), GATE, T0)
w.observe("NIFTY", "N-1", ticket(), idx(adx=18), GATE, T0 + 1)
check("under the gate for less than a minute: nothing",
      w.observe("NIFTY", "N-1", ticket(), idx(adx=18), GATE, T0 + tw.HOLD_INDICATOR_S) == [])
evs = w.observe("NIFTY", "N-1", ticket(), idx(adx=18.5), GATE, T0 + 1 + tw.HOLD_INDICATOR_S)
check("under it for HOLD_INDICATOR_S: fires", codes(evs) == ["adx_weak"], evs)
w = fresh()
w.observe("NIFTY", "N-2", ticket(), idx(adx=17), GATE, T0)
check("never above the gate on this ticket (watch switched on late): not a change, so no event",
      w.observe("NIFTY", "N-2", ticket(), idx(adx=17), GATE, T0 + 600) == [])

print("7. MACD TURNING AGAINST THE TRADE")
w = fresh()
w.observe("NIFTY", "N-1", ticket(), idx(macd=1.2), GATE, T0)
w.observe("NIFTY", "N-1", ticket(), idx(macd=-0.4), GATE, T0 + 1)
evs = w.observe("NIFTY", "N-1", ticket(), idx(macd=-0.6), GATE, T0 + 1 + tw.HOLD_INDICATOR_S)
check("a CE: histogram from positive to negative, held a minute", codes(evs) == ["macd_against"], evs)
w = fresh()
t = ticket(option_type="PE")
w.observe("NIFTY", "P-1", t, idx(macd=-1.2), GATE, T0)
w.observe("NIFTY", "P-1", t, idx(macd=0.3), GATE, T0 + 1)
check("a PE: the other way round", codes(w.observe("NIFTY", "P-1", t, idx(macd=0.3), GATE,
                                                   T0 + 1 + tw.HOLD_INDICATOR_S)) == ["macd_against"])
w = fresh()
w.observe("NIFTY", "N-3", ticket(), idx(macd=-0.5), GATE, T0)
check("already against when watching began: no event",
      w.observe("NIFTY", "N-3", ticket(), idx(macd=-0.5), GATE, T0 + 600) == [])

print("8. NO PROGRESS FOR 30 MINUTES")
w = fresh()
w.observe("NIFTY", "N-1", ticket(now=131), idx(), GATE, T0)
w.observe("NIFTY", "N-1", ticket(now=132), idx(), GATE, T0 + 600)      # +2 is under 10% of 26
check("creeping up by a tick or two is not progress",
      codes(w.observe("NIFTY", "N-1", ticket(now=131), idx(), GATE, T0 + tw.STALL_S)) == ["stalled"])
w = fresh()
w.observe("NIFTY", "N-2", ticket(now=131), idx(), GATE, T0)
w.observe("NIFTY", "N-2", ticket(now=140), idx(), GATE, T0 + 900)      # +10 is real progress
check("a real new best restarts the clock",
      w.observe("NIFTY", "N-2", ticket(now=139), idx(), GATE, T0 + tw.STALL_S) == [])
check("...which then runs its own 30 minutes",
      codes(w.observe("NIFTY", "N-2", ticket(now=139), idx(), GATE, T0 + 900 + tw.STALL_S)) == ["stalled"])

print("9. WHAT ACTUALLY REACHES THE BOT: GAP, MERGING, CAPS")
w = fresh()
check("nothing pending: nothing to send", w.take_due(T0) is None)
w.observe("NIFTY", "N-1", ticket(now=157, hit={"T1": True}), idx(), GATE, T0)
due = w.take_due(T0)
check("an event makes an update due at once", due and codes(due["events"]) == ["t1"], due)
check("the watch block says what happened, when, and which update this is",
      due["watch"]["what_just_happened"][0]["label"] == "T1 hit"
      and due["watch"]["update_number_for_this_ticket"] == 1)
check("only one call in flight at a time", w.take_due(T0 + 1000) is None)
w.finish(due, text="T1 is in. Rules say hold to T2 or the stop.", now=T0 + 3)
check("finished: recorded, newest first, with its events", w.updates[0]["text"].startswith("T1 is in")
      and w.updates[0]["events"] == ["T1 hit"] and w.seq == 1)

w.observe("NIFTY", "N-1", ticket(now=141, hit={"T1": True}), idx(adx=18), GATE, T0 + 60)
w.observe("NIFTY", "N-1", ticket(now=141, hit={"T1": True}), idx(adx=18), GATE, T0 + 60 + tw.HOLD_INDICATOR_S)
check("a second event inside the 5-minute gap waits", w.take_due(T0 + 200) is None and w.has_pending())
due2 = w.take_due(T0 + tw.MIN_GAP_S)
check("...and is sent when the gap ends, merged with any others in one call",
      due2 and sorted(codes(due2["events"])) == ["adx_weak", "gave_back"], due2 and codes(due2["events"]))
check("the second update is told what the first one said, so it does not repeat it",
      due2["watch"]["previous_updates_for_this_ticket"] == ["T1 is in. Rules say hold to T2 or the stop."])
w.finish(due2, error="Anthropic is rate-limiting this key right now - try again in a minute.",
         now=T0 + tw.MIN_GAP_S)
check("a failed call is recorded as an error, not retried", w.updates[0]["error"] and not w.has_pending())

w = fresh()
w.tickets["X"] = {"index": "NIFTY", "trade_id": "X", "fired": set(), "since": {}, "count": tw.MAX_PER_TICKET,
                  "pending": [{"code": "stalled", "label": "x", "at": "", "detail": ""}], "last_sent": 0.0,
                  "watching_since": T0, "best": 0, "best_at": T0, "best_price": 1, "adx_seen_ok": False,
                  "macd_seen_with": False, "previous": []}
check("a ticket that has had MAX_PER_TICKET updates gets no more", w.take_due(T0) is None)

w = fresh()
for i in range(tw.DAILY_CAP + 2):
    tid = f"D-{i}"
    w.observe("NIFTY", tid, ticket(now=157, hit={"T1": True}), idx(), GATE, T0 + i)
    d = w.take_due(T0 + i)
    if d:
        w.finish(d, text="ok", now=T0 + i)
check("the daily cap bites across tickets", len(w.updates) == min(tw.DAILY_CAP, tw.KEPT) and w.day_count == tw.DAILY_CAP,
      (len(w.updates), w.day_count))
nextday = T0 + 86400
w.observe("NIFTY", "NEXT", ticket(now=157, hit={"T1": True}), idx(), GATE, nextday)
check("and resets the next day (IST)", w.take_due(nextday) is not None)

w = fresh()
w.observe("NIFTY", "N-1", ticket(now=157, hit={"T1": True}), idx(), GATE, T0)
w.forget_except(set())
check("a ticket that closed before its update was sent: dropped, nothing sent about it",
      not w.has_pending() and w.take_due(T0) is None)

pub = fresh().public(full=True)
check("what the page gets: on, a counter, the updates and the limits",
      set(pub) == {"on", "seq", "updates", "limits"} and pub["limits"]["per_ticket"] == tw.MAX_PER_TICKET
      and pub["limits"]["gap_minutes"] == 5 and pub["limits"]["per_day"] == tw.DAILY_CAP)
check("the light version for the state poll is just on + counter", set(fresh().public()) == {"on", "seq"})


print("10. THE BOT CALL")
class FakeMessages:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []
    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.resp


class FakeClient:
    def __init__(self, **kw):
        self.messages = FakeMessages(**kw)


def resp(text):
    b = type("B", (), {})(); b.type, b.text = "text", text
    r = type("R", (), {})()
    r.content, r.stop_reason, r.model, r._request_id = [b], "end_turn", mb.MODEL, "req_x"
    r.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0})()
    return r


fc = FakeClient(resp=resp("T1 hit at 11:02. Price 157 against entry 130; rules say hold to T2 (182) or the stop."))
watch = {"what_just_happened": [{"label": "T1 hit", "at": "11:02:03", "detail": "mail me@example.invalid"}]}
text, meta = mb.auto_update("NIFTY", '{"selected_index":"NIFTY"}', watch, client=fc)
call = fc.messages.calls[0]
content = call["messages"][0]["content"]
check("returns the model's text", text.startswith("T1 hit at 11:02"))
check("sends the snapshot, the watch block and the instruction naming the index",
      "<market_snapshot>" in content and "<watch>" in content and "open ticket on NIFTY" in content)
check("low effort and a small token budget - a status note, not an essay",
      call["output_config"] == {"effort": "low"} and call["max_tokens"] == mb.AUTO_MAX_TOKENS)
check("the same system prompt as questions, cached", call["system"][0]["text"] == mb.SYSTEM
      and call["system"][0]["cache_control"] == {"type": "ephemeral"})
check("the watch block is scrubbed like everything else", "me@example.invalid" not in content)
bad = FakeClient(exc=anthropic.AuthenticationError(
    "bad key", response=type("R", (), {"status_code": 401, "request": object(), "headers": {}})(), body=None))
try:
    mb.auto_update("NIFTY", "{}", {}, client=bad)
    check("an SDK error becomes a BotError", False)
except mb.BotError as exc:
    check("an SDK error becomes a BotError a user can read", "API key" in str(exc))

print("11. THE OPEN TICKET'S OWN ENTRY READING, FOR QUESTIONS AND UPDATES ALIKE")
ME = "me@example.invalid"
log = trade_log.user_log_path(ME, "nse_index")
now = dt.datetime(2026, 9, 18, 10, 5, tzinfo=mb.IST)
tr = {"trade_id": "NIFTY-20260918-100500", "index": "NIFTY", "strike": 25000, "option_type": "CE",
      "use_premium": True, "entry_ltp": 130.0, "entry_spot": 24990.0, "premium_targets": [156, 182, 227],
      "premium_sl": 97.5, "index_targets": [], "index_sl": None, "lot_size": 75, "lots": 1.0,
      "hit": {"T1": False, "T2": False, "T3": False}, "sl_hit": False, "status": "OPEN"}
rec = {"confidence": "Medium", "score": 3, "reach_to_risk": 1.4, "risk_points": 60,
       "technical": {"adx": 26.1, "last_rsi": 61.27, "macd_hist": 1.9, "vwap_gap": 12.5}}
trade_log.log_open(tr, rec, now, path=log)
got = mb._open_entry_reading(ME, "nse_index", "NIFTY")
check("read from the OPEN row of the ticket still open", got and got["adx"] == 26.1 and got["rsi"] == 61.3
      and got["macd_hist"] == 1.9 and got["vwap_gap"] == 12.5 and got["confidence"] == "Medium", got)
snap = {"indices": {"NIFTY": {"adx": 19.0, "macd_hist": -0.4}},
        "tickets": {"NIFTY": {"ticket": {"index": "NIFTY", "open": True, "entry": 130.0, "now": 140.0}}}}
ctx = json.loads(mb.build_context(snap, "nse_index", "NIFTY", now=now, user=ME))
check("build_context carries it beside the live reading", ctx["open_ticket_entry_reading"]["adx"] == 26.1
      and ctx["selected"]["adx"] == 19.0)
trade_log.log_close(tr, rec, now + dt.timedelta(minutes=30), 182.0, 3900.0, path=log)
check("once that ticket has closed, there is no open entry reading",
      mb._open_entry_reading(ME, "nse_index", "NIFTY") is None)
snap_closed = {"tickets": {"NIFTY": {"ticket": {"index": "NIFTY", "open": False}}}}
check("and build_context says null rather than borrowing a closed trade's",
      json.loads(mb.build_context(snap_closed, "nse_index", "NIFTY", now=now, user=ME))["open_ticket_entry_reading"] is None)
check("the system prompt tells the model what the field is", "open_ticket_entry_reading" in mb.SYSTEM)

print("12. THE FEED'S WIRING")
import threading
import feeds


class StubTickets:
    def __init__(self, trade, pub):
        self.lock = threading.RLock()
        self.books = {"NIFTY": type("B", (), {"trade": trade})(), "SENSEX": type("B", (), {"trade": None})()}
        self._pub = pub
    def public(self, name):
        return {"ticket": self._pub if name == "NIFTY" else None}


class StubFeed:
    key, market, email = "me", "nse_index", ME
    def __init__(self, watch, trade, pub):
        self.watch, self.tickets = watch, StubTickets(trade, pub)
        self.lock = threading.RLock()
        self.state = {"indices": {"NIFTY": {"public": idx()}, "SENSEX": {"public": idx()}}}
        self.faults, self.written = [], []
    def instruments(self):
        return ["NIFTY", "SENSEX"]
    def _note_fault(self, where, text):
        self.faults.append((where, text))
    def _bot_write(self, due):
        self.written.append(due)
    def snapshot(self):
        return {"tickets": {"NIFTY": {"ticket": self.tickets._pub}}, "indices": {"NIFTY": idx()}}


_real_key = mb.key_present
try:
    trade = {"trade_id": "NIFTY-1", "status": "OPEN"}
    pub = ticket(now=157, hit={"T1": True})
    off = StubFeed(fresh(on=False), trade, pub)
    feeds.Feed._bot_watch(off)
    check("switched off: the feed does nothing at all", not off.watch.tickets and not off.written)

    mb.key_present = lambda: False
    f = StubFeed(fresh(), trade, pub)
    feeds.Feed._bot_watch(f)
    time.sleep(0.05)
    check("on, but no API key: the event is noted and nothing is sent or claimed",
          f.watch.has_pending() and not f.written and not f.watch.busy)

    mb.key_present = lambda: True
    feeds.Feed._bot_watch(f)
    time.sleep(0.1)
    check("on, with a key: the update is claimed and written off the feed's own thread",
          len(f.written) == 1 and codes(f.written[0]["events"]) == ["t1"] and not f.faults, (f.written, f.faults))
    f.tickets.books["NIFTY"].trade = None
    feeds.Feed._bot_watch(f)
    check("the ticket closed: the watch forgets it", "NIFTY-1" not in f.watch.tickets)

    # _bot_write end to end, with the real method and a fake bot call.
    g = StubFeed(fresh(), trade, pub)
    g.watch.observe("NIFTY", "NIFTY-1", pub, idx(), GATE)
    due = g.watch.take_due()
    _real_auto = mb.auto_update
    mb.auto_update = lambda index, ctx, watch, client=None: ("T1 hit; hold to T2 or the stop.", {})
    try:
        feeds.Feed._bot_write(g, due)
    finally:
        mb.auto_update = _real_auto
    check("_bot_write records the bot's text and frees the slot",
          g.watch.updates[0]["text"].startswith("T1 hit") and not g.watch.busy)
    def boom(index, ctx, watch, client=None):
        raise mb.BotError("Could not reach Anthropic - check this machine's internet connection.")
    g.watch.observe("NIFTY", "NIFTY-2", pub, idx(), GATE)
    due = g.watch.take_due()
    mb.auto_update = boom
    try:
        feeds.Feed._bot_write(g, due)
    finally:
        mb.auto_update = _real_auto
    check("...and a failure as a readable error, slot freed",
          "Could not reach Anthropic" in (g.watch.updates[0]["error"] or "") and not g.watch.busy)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
    check("both analysis passes call the watch", src.count("self._bot_watch()") == 2)
finally:
    mb.key_present = _real_key

print("13. THE SWITCH ON THE PAGE")
import web_server


class WatchFeed:
    def __init__(self, w):
        self.watch = w


def handler(w, user=ME, same_origin=True):
    feeds.for_user = lambda email, mkt=None, start=True: WatchFeed(w)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: "nse_index"
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


_real_for_user = feeds.for_user
try:
    w = fresh(on=False)
    mb.key_present = lambda: False
    h, out = handler(w)
    h._do_marketbot({"action": "watch", "on": "1"})
    check("cannot be switched on without a key (503)", out["code"] == 503 and w.on is False)
    mb.key_present = lambda: True
    h, out = handler(w, same_origin=False)
    h._do_marketbot({"action": "watch", "on": "1"})
    check("a cross-site request cannot switch it on (403)", out["code"] == 403 and w.on is False)
    h, out = handler(w)
    h._do_marketbot({"action": "watch", "on": "1"})
    d = json.loads(out["body"])
    check("switched on: ok, and the page gets the new state back",
          d["ok"] and w.on is True and d["watch"]["on"] is True, d)
    check("does not use up a question from the daily cap", mb._usage.get(ME) is None)
    h, out = handler(w)
    h._do_marketbot({"action": "watch", "on": "0"})
    check("switched off again", json.loads(out["body"])["ok"] and w.on is False)
    mb.key_present = lambda: False
    h, out = handler(w)
    w.set_on(True)
    h._do_marketbot({"action": "watch", "on": "0"})
    check("can always be switched OFF, key or no key", w.on is False)
    h, out = handler(w)
    h._api_marketbot(ME)
    d = json.loads(out["body"])
    check("the status call returns the watch with its updates and limits",
          d["watch"]["on"] is False and "updates" in d["watch"] and "limits" in d["watch"])
finally:
    mb.key_present = _real_key
    feeds.for_user = _real_for_user

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("the page has the switch, the tab dot and the state-poll hook",
      'id="botwatch"' in SRC and 'id="botdot"' in SRC and "botWatchTick(s.bot_watch)" in SRC)
check("an update is put on the page as text, never as HTML",
      "createTextNode(u.error" in SRC and "innerHTML = u." not in SRC)

print("TICKET WATCH TEST PASSED" if not fails else f"TICKET WATCH TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
