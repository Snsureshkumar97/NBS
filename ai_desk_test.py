#!/usr/bin/env python3
"""The AI desk: Ask TradePicker's own paper tickets, beside the rules.

Fakes only - a scripted model, a fake feed and chain, a clock the test moves, a
temporary log folder. What matters: it only asks at a candle close and only
when an entry is allowed (so a blocked moment costs nothing), every limit bites
whatever the model says, a proposal is re-checked against live data and priced
at the live premium, its tickets live in their own log and never reach live
orders, and they are tracked and closed like any ticket.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_desk as ad
import config
import market_bot as mb
import tickets
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = {"now": dt.datetime(2026, 9, 18, 10, 0, 50, tzinfo=IST), "clock": 1_789_000_000.0}
tickets.now_ist = lambda: T["now"]


def at(h, m, s=50, day=18):
    T["now"] = dt.datetime(2026, 9, day, h, m, s, tzinfo=IST)


def chain(spot=25010.0, ce=130.0, pe=120.0, spread=0.5):
    strikes = []
    for k in range(24500, 25550, 50):
        c = ce + (25000 - k) * 0.4
        p = pe - (25000 - k) * 0.4
        strikes.append({"strike": float(k), "call_ltp": round(max(c, 1), 2), "put_ltp": round(max(p, 1), 2),
                        "call_bid": round(max(c, 1) - spread / 2, 2), "call_ask": round(max(c, 1) + spread / 2, 2),
                        "put_bid": round(max(p, 1) - spread / 2, 2), "put_ask": round(max(p, 1) + spread / 2, 2),
                        "call_oi": 1000, "put_oi": 900})
    return {"available": True, "expiry": "2026-09-22", "spot": spot, "strikes": strikes, "pcr": 0.9}


def rec(name="NIFTY", **kw):
    r = {"index": name, "spot": 25010.0, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
         "suggested_strike": 25000, "option_chain": chain(**kw), "technical": {"adx": 24.0}}
    return r


class Rules:
    lots, capital, risk_pct = 2, None, 1.0


class Streamer:
    def __init__(self):
        self.px = {}
    def price(self, tok):
        return self.px.get(tok)


class FakeFeed:
    def __init__(self, market="nse_index", email="me@example.invalid"):
        self.market, self.email, self.key = market, email, f"{email}#{market}"
        self.lock = threading.RLock()
        self.tickets = Rules()
        self.streamer = Streamer()
        self.dstream = None
        self.faults = []
        self.live_at = __import__("time").time()
        names = config.instruments_in(market)
        self.state = {"feed": "ok", "indices": {n: {"rec": rec(n)} for n in names}}
    def instruments(self):
        return config.instruments_in(self.market)
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


SCRIPT, ASKED = [], []


def fake_decide(kind, index, context, desk, tools_ctx=None, client=None):
    ASKED.append((kind, index, desk))
    d = SCRIPT.pop(0) if SCRIPT else ({"action": "wait", "reason": "nothing clear"} if kind == "entry"
                                        else {"action": "hold", "reason": "still valid"})
    if isinstance(d, Exception):
        raise d
    return d, {"input_tokens": 1000, "output_tokens": 100, "looked_at": ["Option chain"]}


mb.decide = fake_decide
mb.key_present = lambda: True


N = [0]


def desk(market="nse_index", email=None):
    N[0] += 1
    email = email or f"desk{N[0]}@example.invalid"
    f = FakeFeed(market, email)
    d = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
    return f, d


def enter(strike=25000, side="CE", target=160.0, stop=110.0):
    return {"action": "enter", "option_type": side, "strike": strike, "target": target, "stop": stop,
            "reason": "Trend and VWAP agree; chain shows puts building."}


print("1. IT ONLY ASKS WHEN IT SHOULD")
at(10, 0, 50)
f, d = desk()
ASKED.clear()
d.step()
check("off: nothing is asked", not ASKED)
check("off by default for a new account", d.on is False)
d.set_on(True)
at(10, 0, 10)
d.step()
check("on, but under DECISION_DELAY_S after the candle closed: waits for the finished candle", not ASKED)
at(10, 0, 50)
d.step()
check("then one entry question per index", [a[:2] for a in ASKED] == [("entry", "NIFTY"), ("entry", "BANKNIFTY"),
                                                                        ("entry", "SENSEX")], ASKED)
d.step()
at(10, 10, 0)
d.step()
check("the same candle is never asked twice", len(ASKED) == 3)
at(10, 15, 50)
d.step()
check("the next candle close asks again", len(ASKED) == 6)
at(16, 0, 50)
ASKED.clear()
d.step()
check("the Indian market closed: nothing asked", not ASKED)
at(10, 45, 50)
f.live_at -= ad.STALE_S + 5
d.step()
check("the feed's analysis has gone stale: nothing asked", not ASKED)
f.live_at = __import__("time").time()
f.state["feed"] = "expired"
at(11, 45, 50)
d.step()
check("the feed says its Zerodha token expired: nothing asked", not ASKED)
f.state["feed"] = "ok"
mb.key_present = lambda: False
at(10, 30, 50)
d.step()
check("no API key: nothing asked", not ASKED)
mb.key_present = lambda: True
check("waits are recorded with the reason and what it looked at",
      d.recent[0]["action"] == "wait" and d.recent[0]["reason"] == "nothing clear" and d.recent[0]["looked_at"])

print("2. AN ENTRY: CHECKED, PRICED LIVE, IN ITS OWN LOG")
at(11, 0, 50)
f, d = desk()
d.set_on(True)
ASKED.clear()
SCRIPT[:] = [enter(target=160.0, stop=110.0)]
d.step()
t = d._open_trade("NIFTY")
check("a valid proposal opens an AI ticket", t is not None, d.recent[:1])
check("entry is the chain's live premium (130), not a number the model named", t["entry_ltp"] == 130.0)
check("target and stop frozen as proposed; T1 is the exit", t["premium_targets"][0] == 160.0 and t["premium_sl"] == 110.0
      and t["exit_at"] == "T1")
check("lots are the user's own setting", t["lots"] == 2)
check("the reason rides with the ticket", "VWAP" in t["ai_reason"])
orow = trade_log._read_rows(d.book.path)[0]
check("the log's signal columns describe the AI trade, not the rule signal",
      orow["confidence"] == "AI" and orow["strictness"] == "ai" and float(orow["reward_risk"]) == 1.5
      and orow["risk_points"] == "", orow)
rows = trade_log._read_rows(d.book.path)
check("written to ai_trades.csv, not the rule tickets' log",
      d.book.path.endswith("ai_trades.csv") and rows and rows[0]["event"] == "OPEN"
      and not trade_log._read_rows(trade_log.user_log_path(f.email, "nse_index")))
check("paper by default: nothing is listening to this book here", d.book.listeners == [])
ent = next((r for r in d.recent if r["action"] == "enter"), {})
check("the entry is recorded with contract, entry, target and stop",
      ent.get("contract") == "NIFTY|25000|CE|2026-09-22" and ent.get("entry") == 130.0
      and ent.get("target") == 160.0 and ent.get("stop") == 110.0, ent)
check("the other indices were still asked about", [a[1] for a in ASKED] == ["NIFTY", "BANKNIFTY", "SENSEX"])

print("3. A PROPOSAL THAT FAILS A CHECK IS NOT TAKEN")
at(11, 0, 50)
f, d = desk()
r = rec()
cases = [
    ({"option_type": "XX", "strike": 25000, "target": 160, "stop": 110}, "CE or PE"),
    ({"option_type": "CE", "strike": "abc", "target": 160, "stop": 110}, "numbers"),
    ({"option_type": "CE", "strike": 25025, "target": 160, "stop": 110}, "not a strike"),
    ({"option_type": "CE", "strike": 24500, "target": 400, "stop": 250}, "strikes from the money"),
    ({"option_type": "CE", "strike": 25000, "target": 160, "stop": 129}, "below the live premium"),
    ({"option_type": "CE", "strike": 25000, "target": 160, "stop": 30}, "more than 70%"),
    ({"option_type": "CE", "strike": 25000, "target": 132, "stop": 110}, "above the live premium"),
    ({"option_type": "CE", "strike": 25000, "target": 145, "stop": 110}, "reward to risk"),
]
for prop, why in cases:
    ok, reason, _ = d.validate("NIFTY", r, prop)
    check(f"refused: {why}", not ok and why in reason, reason)
ok, reason, _ = d.validate("NIFTY", rec(spread=10.0), {"option_type": "CE", "strike": 25000, "target": 160, "stop": 110})
check("refused: a spread over MAX_SPREAD_PCT", not ok and "spread" in reason, reason)
nochain = dict(r, option_chain={"available": False, "strikes": []})
ok, reason, _ = d.validate("NIFTY", nochain, {"option_type": "CE", "strike": 25000, "target": 160, "stop": 110})
check("refused: no live chain to check against", not ok and "no live option chain" in reason)
ok, _, plan = d.validate("NIFTY", r, {"option_type": "PE", "strike": 25050, "target": 190, "stop": 100})
check("a put, a strike off the money: fine, priced off the put side", ok and plan["ltp"] == 140.0, plan)

d.set_on(True)
SCRIPT[:] = [{"action": "enter", "option_type": "CE", "strike": 25025, "target": 160, "stop": 110, "reason": "x"}]
d.step()
rj = next((r for r in d.recent if r["index"] == "NIFTY"), {})
check("a refused proposal opens nothing and is recorded with why",
      d._open_trade("NIFTY") is None and rj.get("action") == "rejected"
      and "not a strike" in rj.get("rejected_because", ""), rj)

print("4. NO OVERTRADING, NO SECOND BUY OF THE SAME CONTRACT")
at(9, 30, 50)
f, d = desk()
d.set_on(True)
SCRIPT[:] = [enter()]
d.step()
t = d._open_trade("NIFTY")
d.book.tick_price("NIFTY", 109.0)                          # stopped out
check("stopped out on the tick", d._open_trade("NIFTY") is None)
d._after("NIFTY", [{"kind": "closed"}])
ASKED.clear()
at(9, 45, 50)
T["clock"] += 15 * 60
d.step()
check("inside COOLDOWN_MIN after an exit, that index is not even asked",
      "NIFTY" not in [a[1] for a in ASKED] and "BANKNIFTY" in [a[1] for a in ASKED])
at(10, 15, 50)
T["clock"] += 30 * 60
ASKED.clear()
SCRIPT[:] = [enter()]
d.step()
check("after the cooldown it is asked again", "NIFTY" in [a[1] for a in ASKED])
nf = next((r for r in d.recent if r["index"] == "NIFTY"), {})
check("but the same contract a second time today is refused",
      d._open_trade("NIFTY") is None and "already traded today" in nf.get("rejected_because", ""), nf)
check("and the model is told which contracts it has already traded",
      "NIFTY|25000|CE|2026-09-22" in ASKED[0][2]["contracts_already_traded_today"])

at(10, 30, 50)
T["clock"] += 30 * 60
SCRIPT[:] = [enter(strike=25050, target=150, stop=100)]
d.step()
check("a different strike is allowed: second NIFTY entry", d._open_trade("NIFTY") is not None and d.entries["NIFTY"] == 2)
d.book.close_ticket("NIFTY", "CLOSED — AI exit: test")
d._after("NIFTY", [{"kind": "closed"}])
at(11, 30, 50)
T["clock"] += 60 * 60
ASKED.clear()
d.step()
check(f"MAX_ENTRIES_PER_INDEX ({ad.MAX_ENTRIES_PER_INDEX}) reached: NIFTY is not asked again today",
      "NIFTY" not in [a[1] for a in ASKED])
d.entries.update({"BANKNIFTY": 1, "SENSEX": 1})
at(11, 45, 50)
ASKED.clear()
d.step()
check(f"MAX_ENTRIES_PER_DAY ({ad.MAX_ENTRIES_PER_DAY}) reached: no index is asked", not ASKED)

f, d = desk()
d.set_on(True)
at(15, 15, 50)
ASKED.clear()
d.step()
check("no Indian-index entry question from 15:10", not [a for a in ASKED if a[0] == "entry"])
at(9, 15, 50)
ASKED.clear()
d.step()
check("nor before the opening wait ends (the rule book's own gate)", not ASKED)

f, d = desk()
d.set_on(True)
f.tickets.capital = 100000.0
at(10, 0, 50)
SCRIPT[:] = [enter(target=250, stop=40)]
d.step()
d.book.tick_price("NIFTY", 39.0)
d._after("NIFTY", [{"kind": "closed"}])
ASKED.clear()
at(12, 0, 50)
T["clock"] += 3 * 3600
d.step()
check("the daily loss limit, on the rule book's capital, stops AI entries too",
      not ASKED and "LOSS LIMIT" in str(d.entry_block("BANKNIFTY")), d.entry_block("BANKNIFTY"))

f, d = desk()
d.set_on(True)
d.day, d.decisions_today = "2026-09-18", ad.MAX_DECISIONS_PER_DAY["nse_index"]
at(12, 15, 50)
ASKED.clear()
d.step()
check("the daily decision cap: no model call, said once", not ASKED
      and sum(1 for r in d.recent if r["action"] == "none" and r["kind"] == "cap") == 1)
at(9, 30, 50, day=21)
d.step()
check("a new day resets the counters", d.decisions_today > 0 and d.decisions_today < 5 and d.contracts == [])

print("5. HOLD OR EXIT, AND THE TICK")
at(10, 0, 50)
f, d = desk()
d.set_on(True)
SCRIPT[:] = [enter(target=160, stop=110)]
d.step()
at(10, 15, 50)
ASKED.clear()
SCRIPT[:] = [{"action": "hold", "reason": "trend intact"}]
d.step()
check("with a ticket open, the index gets a review question, not an entry one",
      ASKED[0][:2] == ("review", "NIFTY") and d._open_trade("NIFTY") is not None)
at(10, 30, 50)
SCRIPT[:] = [{"action": "exit", "reason": "VWAP lost. Momentum gone."}]
d.step()
rows = [r for r in trade_log._read_rows(d.book.path) if r["event"] == "CLOSE"]
check("an exit decision closes the ticket, with the bot's reason in the status",
      d._open_trade("NIFTY") is None and rows and rows[-1]["status"] == "CLOSED — AI exit: VWAP lost", rows[-1:])
check("...and starts the cooldown", d.last_exit.get("NIFTY") == T["clock"])

f, d = desk()
d.set_on(True)
at(11, 0, 50)
SCRIPT[:] = [enter(target=160, stop=110)]
d.step()
tid = d._open_trade("NIFTY")["trade_id"]
d._tok[tid] = "tok1"
f.streamer.px["tok1"] = 145.0
d.price_tick()
check("the tick prices the AI ticket's own contract", d.book.public("NIFTY")["ticket"]["now"] == 145.0)
f.streamer.px["tok1"] = 161.0
d.price_tick()
rows = [r for r in trade_log._read_rows(d.book.path) if r["event"] == "CLOSE"]
check("its target hit on the tick: closed at the target", d._open_trade("NIFTY") is None and rows
      and "T1 hit" in rows[-1]["status"], rows[-1:])
check("P&L in rupees at the user's lots x the lot size", float(rows[-1]["pnl"]) == round((161 - 130) * 2 * 65, 2),
      rows[-1]["pnl"])

f, d = desk()
d.set_on(True)
at(12, 0, 50)
SCRIPT[:] = [enter(target=160, stop=110)]
d.step()
hot = rec()
for s in hot["option_chain"]["strikes"]:
    s["call_ltp"] = 100.0
d.track("NIFTY", hot)
check("the analysis pass checks it too: stop hit on the chain price", d._open_trade("NIFTY") is None)
strong = rec()
d.track("NIFTY", strong)
d.track("BANKNIFTY", rec("BANKNIFTY"))
check("the analysis pass never opens an AI ticket by itself", all(d._open_trade(k) is None for k in f.instruments()))
check("the rule engine's re-arm is off on the AI book - a closed AI ticket is never replaced by a rule one",
      d.book.auto_rearm is False)

f, d = desk()
d.set_on(True)
at(12, 0, 50)
SCRIPT[:] = [mb.BotError("Anthropic is rate-limiting this key right now - try again in a minute.", 429)]
d.step()
check("a failed decision is recorded and opens nothing", d._open_trade("NIFTY") is None
      and any(r["action"] == "error" and "rate-limiting" in r["reason"] for r in d.recent))

f, d = desk()
d.set_on(True)
at(13, 0, 50)


def switched_off_mid_decision(kind, index, context, desk_info, tools_ctx=None, client=None):
    d.enabled[index] = False
    return enter(), {"input_tokens": 1, "output_tokens": 1}


mb.decide = switched_off_mid_decision
try:
    d.step()
finally:
    mb.decide = fake_decide
check("switched off while the bot was deciding: its entry is not taken",
      d._open_trade("NIFTY") is None and "switched off" in (d.recent[0].get("rejected_because") or ""), d.recent[:1])

print("5b. EACH INDEX ON ITS OWN SWITCH")
at(14, 0, 50)
f, d = desk()
d.set_on(True, "SENSEX")
ASKED.clear()
d.step()
check("only the switched-on index is asked about", [a[1] for a in ASKED] == ["SENSEX"], ASKED)
check("switching one index on leaves the others off", d.enabled == {"SENSEX": True})
try:
    d.set_on(True, "BTC")
    check("an index from another market cannot be switched on", False)
except ValueError:
    check("an index from another market cannot be switched on", True)
d.set_on(True)
at(14, 15, 50)
ASKED.clear()
SCRIPT[:] = [enter(), {"action": "wait", "reason": "x"}, {"action": "wait", "reason": "y"}]
d.step()
d.set_on(False, "NIFTY")
at(14, 30, 50)
ASKED.clear()
d.step()
check("switching NIFTY off stops its reviews too; the others carry on",
      [a[1] for a in ASKED] == ["BANKNIFTY", "SENSEX"] and d._open_trade("NIFTY") is not None, ASKED)
pub = d.public()
check("public: a switch per index and a record per index",
      pub["enabled"] == {"NIFTY": False, "BANKNIFTY": True, "SENSEX": True}
      and set(pub["records"]) == {"NIFTY", "BANKNIFTY", "SENSEX"} and pub["limits"]["decisions_by_index"]["NIFTY"] == 1)
d.book.tick_price("NIFTY", 161.0)
pub = d.public()
check("a closed NIFTY trade counts in NIFTY's record only",
      pub["records"]["NIFTY"]["closed"] == 1 and pub["records"]["SENSEX"]["closed"] == 0 and pub["record"]["closed"] == 1)
old_state = json.load(open(d.state_path))
old_state.pop("enabled")
old_state["on"] = True
json.dump(old_state, open(d.state_path, "w"))
f3 = FakeFeed("nse_index", f.email)
d3 = ad.AIDesk(f3, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("a desk saved with the old market-wide switch comes back with every index on",
      d3.enabled == {"NIFTY": True, "BANKNIFTY": True, "SENSEX": True})

print("5c. A PROFIT THAT TURNS: THE GIVE-BACK RULE AND AN UNSCHEDULED REVIEW")
at(10, 0, 50)
f, d = desk()
d.set_on(True)
SCRIPT[:] = [enter(target=160.0, stop=110.0)]     # entry 130, so the target is 30 away
d.step()
tid = d._open_trade("NIFTY")["trade_id"]
d._tok[tid] = "tok1"
f.streamer.px["tok1"] = 148.0                      # 60% of the way
d.price_tick()
check("60% of the way to the target: still open", d._open_trade("NIFTY") is not None)
f.streamer.px["tok1"] = 142.0                      # gave back a third of the best gain
d.price_tick()
check("gave back a third of it: still open", d._open_trade("NIFTY") is not None)
f.streamer.px["tok1"] = 138.0                      # gave back half of the 18-point best gain
d.price_tick()
rows = [r for r in trade_log._read_rows(d.book.path) if r["event"] == "CLOSE"]
check("gave back half the best gain: closed by the rule, in profit, before the stop",
      d._open_trade("NIFTY") is None and "give-back rule" in rows[-1]["status"]
      and float(rows[-1]["pnl"]) > 0, rows[-1:])
check("...and it is in the decisions list as the tool's own exit, not the bot's",
      any(r["kind"] == "rule" and r["action"] == "exit" and "give-back" in r["reason"] for r in d.recent))
check("...and it starts the cooldown", d.last_exit.get("NIFTY") == T["clock"])

f, d = desk()
d.set_on(True)
at(11, 0, 50)
SCRIPT[:] = [enter(target=190.0, stop=110.0)]
d.step()
trade = d._open_trade("NIFTY")
tid = trade["trade_id"]
d._tok[tid] = "tok1"
f.streamer.px["tok1"] = 145.0
d.price_tick()
check("a modest gain, no give-back", d._open_trade("NIFTY") is not None)
ASKED.clear()
at(11, 5, 50)                                      # not a candle close: only an event can ask now
half = dict(rec(), option_chain=chain())
pub = d.book.public("NIFTY")["ticket"]
d.book.live_price("NIFTY", 120.0)                  # halfway from 130 to the stop at 110
d.track("NIFTY", half)
T["clock"] += ad.ticket_watch.HOLD_PRICE_S
d.book.live_price("NIFTY", 120.0)
d.track("NIFTY", half)
check("a turn is noticed between closes", d.pending.get("NIFTY"), d.pending)
SCRIPT[:] = [{"action": "exit", "reason": "Halfway to the stop with the trend gone."}]
d.step()
check("the bot is asked straight away, told what happened, and can exit in between closes",
      ASKED and ASKED[0][0] == "review" and ASKED[0][2].get("what_just_happened")
      and d._open_trade("NIFTY") is None, ASKED[:1])
check("the desk block also tells it the give-back rule exists", "give_back_rule" in ASKED[0][2])

f, d = desk()
d.set_on(True)
at(12, 0, 50)
SCRIPT[:] = [enter(target=190.0, stop=110.0)]
d.step()
trade = d._open_trade("NIFTY")
d.event_reviews[trade["trade_id"]] = ad.MAX_EVENT_REVIEWS
d.pending["NIFTY"] = ["Halfway to the stop"]
ASKED.clear()
at(12, 5, 50)
d.step()
check("event reviews are capped per ticket", not ASKED and d._open_trade("NIFTY") is not None)
d.event_reviews[trade["trade_id"]] = 0
d.last_event_review["NIFTY"] = T["clock"]
d.pending["NIFTY"] = ["ADX fell below the trend gate"]
d.step()
check("and spaced out - two turns in a minute is one review", not ASKED)

print("5d. ITS OWN TRACK RECORD, WITH EVERY DECISION")
f, d = desk()
tr = d.track_record("NIFTY")
check("no trades yet: says so, nothing to learn from", tr["closed_trades"] == 0 and "nothing to learn" in tr["note"])


def trade_on(day, h, name, side, strike, target, stop, reason):
    at(h, 0, 50, day=day)
    r = rec(name)
    ok, why, plan = d.validate(name, r, {"option_type": side, "strike": strike, "target": target, "stop": stop})
    assert ok, why
    d._open(name, r, plan, reason)
    d._record(name, "entry", "enter", reason, {"contract": plan["contract"], "entry": plan["ltp"]})
    return plan


trade_on(21, 10, "NIFTY", "CE", 25000, 160, 110, "Breakout above VWAP with ADX rising.")
d.book.tick_price("NIFTY", 161.0)                                     # target: +31 x 130
trade_on(22, 10, "NIFTY", "CE", 25050, 150, 100, "Second leg up after a pullback to VWAP.")
d.book.tick_price("NIFTY", 99.0)                                      # stop
trade_on(23, 12, "NIFTY", "PE", 25050, 180, 120, "Rejected at the day high, puts building.")
d.book.live_price("NIFTY", 150.0)
d.book.close_ticket("NIFTY", "CLOSED — AI exit: momentum gone")       # its own exit, at the streamed price
check("an AI exit is logged at the contract's streamed price, not the chain's older one",
      float([r for r in trade_log._read_rows(d.book.path) if r["event"] == "CLOSE"][-1]["exit"]) == 150.0)
trade_on(24, 14, "SENSEX", "CE", 25000, 160, 110, "Range break on Sensex.")
trade_on(24, 14, "NIFTY", "CE", 25100, 120, 70, "Late-day squeeze.")
d._giveback("NIFTY", d._open_trade("NIFTY"), 108.0)                   # 60% of the way (entry 90 -> 120)
d._giveback("NIFTY", d._open_trade("NIFTY"), 99.0)                    # then half of that gain back
check("the give-back rule's close is logged at the price that fired it",
      float([r for r in trade_log._read_rows(d.book.path) if r["event"] == "CLOSE"][-1]["exit"]) == 99.0)
d.book.tick_price("SENSEX", 109.0)
tr = d.track_record("NIFTY")
check("counts every closed AI trade in the market", tr["closed_trades"] == 5, tr["closed_trades"])
check("this index separately from the rest", tr["this_index"]["n"] == 4 and tr["all_indices"]["n"] == 5)
ex = tr["by_exit"]
check("how each one ended: target, stop, its own exit, the give-back rule",
      ex["target"]["n"] == 1 and ex["stop"]["n"] >= 1 and ex["your_exit"]["n"] == 1 and ex["give_back_rule"]["n"] == 1, ex)
check("win rate and net add up", tr["this_index"]["net"] == round(sum(float(r["pnl"]) for r in trade_log._read_rows(d.book.path)
      if r["event"] == "CLOSE" and r["index"] == "NIFTY"), 2))
check("by side and by time of entry on this index", set(tr["by_side_on_this_index"]) == {"CE", "PE"}
      and set(tr["by_entry_time_on_this_index"]) >= {"before 11:00", "11:00-13:00", "after 13:00"},
      tr["by_entry_time_on_this_index"])
last = tr["your_last_trades_on_this_index"]
check("its latest trades on this index, newest first, each with the reason it gave then",
      len(last) == 4 and last[0]["contract"] == "25100 CE" and last[0]["your_reason_then"] == "Late-day squeeze."
      and last[-1]["your_reason_then"] == "Breakout above VWAP with ADX rising.", [x["your_reason_then"] for x in last])
check("...and the readings at entry", last[0]["at_entry"]["adx"] == "24.0", last[0]["at_entry"])
check("under 30 trades it is told not to lean on patterns yet", "too few" in tr.get("caution", ""))
check("it rides with every decision", "your_track_record" in d._desk_info("NIFTY"))
check("and stays small enough to send every time", len(json.dumps(d._desk_info("NIFTY"))) < 9000,
      len(json.dumps(d._desk_info("NIFTY"))))
import market_bot as real_mb
check("the desk's instructions explain it and warn about small samples",
      "your_track_record" in real_mb.DESK_SYSTEM and "mostly noise" in real_mb.DESK_SYSTEM)

print("6. AFTER A RESTART")
at(13, 0, 50)
f, d = desk(email="restart@example.invalid")
d.set_on(True)
SCRIPT[:] = [enter()]
d.step()
f2 = FakeFeed("nse_index", "restart@example.invalid")
d2 = ad.AIDesk(f2, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("the switch, the counters and the decisions are read back", d2.on and d2.entries.get("NIFTY") == 1
      and d2.recent and d2.contracts == d.contracts)
closes = [r for r in trade_log._read_rows(d2.book.path) if r["event"] == "CLOSE"]
check("an AI ticket left open by the old process is closed as 'the tool stopped', like any ticket",
      closes and "the tool stopped" in closes[-1]["status"])

print("7. BITCOIN")
config.ENABLE_CRYPTO = True
f, d = desk(market="crypto")
d.set_on(True)
T["now"] = dt.datetime(2026, 9, 19, 2, 45, 50, tzinfo=IST)    # a Saturday night
ASKED.clear()
d.step()
check("Bitcoin is asked around the clock, weekends included", [a[1] for a in ASKED] == ["BTC"])
check("with its own decision cap", ad.MAX_DECISIONS_PER_DAY["crypto"] >= 96)

print("8. THE MODEL CALL")


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def resp(blocks, stop="tool_use"):
    r = type("R", (), {})()
    r.content, r.stop_reason, r.model, r._request_id = blocks, stop, mb.MODEL, "req"
    r.usage = type("U", (), {"input_tokens": 500, "output_tokens": 50, "cache_read_input_tokens": 0})()
    return r


class Scripted:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self
    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        return self.script.pop(0)


import importlib
real = importlib.reload(mb)
real.key_present = lambda: True
import bot_data
orig_call = bot_data.call
bot_data.call = lambda name, args, c: ('{"pcr":0.9}', False)
try:
    ctx = bot_data.Ctx("me@example.invalid", "nse_index", "NIFTY")
    cl = Scripted([resp([Block(type="tool_use", id="a", name="get_option_chain", input={})]),
                   resp([Block(type="tool_use", id="b", name="submit_decision",
                               input={"action": "enter", "option_type": "CE", "strike": 25000, "target": 160,
                                      "stop": 110, "reason": "x"})])])
    dec, meta = real.decide("entry", "NIFTY", "{}", {"lots": 2}, tools_ctx=ctx, client=cl)
    check("looks things up, then hands in a decision", dec["action"] == "enter" and meta["looked_at"] == ["Option chain"])
    check("uses the desk's own instructions, not the Q&A ones", cl.calls[0]["system"][0]["text"] == real.DESK_SYSTEM
          and "PAPER" in real.DESK_SYSTEM)
    check("the desk block goes with the snapshot", "<desk>" in cl.calls[0]["messages"][0]["content"])
    check("submit_decision is offered beside every lookup", any(t["name"] == "submit_decision" for t in cl.calls[0]["tools"])
          and len(cl.calls[0]["tools"]) == len(bot_data.SOURCES) + 1)
    cl = Scripted([resp([Block(type="text", text="I think it's bullish")], "end_turn")])
    dec, _ = real.decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    check("no decision handed in: WAIT - never an entry by default", dec["action"] == "wait")
    cl = Scripted([resp([Block(type="tool_use", id="c", name="submit_decision", input={"action": "buy_everything", "reason": "x"})])])
    dec, _ = real.decide("review", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    check("an action outside the allowed ones: HOLD for a review", dec["action"] == "hold")
    loop = [resp([Block(type="tool_use", id=f"t{i}", name="get_news", input={})]) for i in range(real.MAX_TOOL_ROUNDS)]
    loop.append(resp([Block(type="tool_use", id="z", name="submit_decision", input={"action": "exit", "reason": "done"})]))
    cl = Scripted(loop)
    dec, _ = real.decide("review", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    check("a model that keeps looking gets only submit_decision on the last turn",
          dec["action"] == "exit" and [t["name"] for t in cl.calls[-1]["tools"]] == ["submit_decision"])
finally:
    bot_data.call = orig_call

print("9. THE PAGE AND THE SWITCH")
import feeds
import web_server
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("an AI trades tab, in the tab list, with a label", 'data-tab="aidesk"' in SRC and '"aidesk"];' in SRC
      and 'aidesk:"AI trades"' in SRC)
check("the model's words reach the page through esc() in element text",
      "<div>${esc(r.reason)}</div>" in SRC and '<div class="whyhold">${esc(t.reason)}</div>' in SRC)
check("the tab has an index picker, a live spot line and a switch per index",
      'id="aipicker"' in SRC and 'id="aispot"' in SRC and "body: new URLSearchParams({index: k, on: on ? \"1\" : \"0\"})" in SRC)
check("the fast price poll moves the AI tab (spot and open AI tickets)", "  aiTick(t);" in SRC
      and 'payload["ai"] = live_ai' in SRC)
check("an open AI ticket is drawn as the Signal page's ticket card - badge, stats row, ladder",
      "function aiTicketCard(" in SRC and '<span class="badge open">OPEN</span>' in SRC
      and '`<div class="tstats">`' in SRC and '`<div class="ladder">${ladder}</div>`' in SRC)
check("the decision's css class comes from a fixed list", 'const cls = ["enter", "exit", "rejected", "error"].includes(r.action)' in SRC)
check("the page says paper only", "on paper - nothing is ever sent to Zerodha" in SRC)

at(10, 0, 50)
f, d = desk()
f.ai = d
orig = feeds.for_user
feeds.for_user = lambda email, market=None, start=True: f


def handler(user="me@example.invalid", same_origin=True):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: "nse_index"
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


try:
    h, out = handler(same_origin=False)
    h._do_ai({"index": "NIFTY", "on": "1"})
    check("a cross-site request cannot switch it on", out["code"] == 403 and not d.on)
    real.key_present = lambda: False
    h, out = handler()
    h._do_ai({"index": "NIFTY", "on": "1"})
    check("not without an API key", out["code"] == 503 and not d.on)
    real.key_present = lambda: True
    h, out = handler()
    h._do_ai({"on": "1"})
    check("an index must be named - there is no market-wide switch on the page", out["code"] == 400 and not d.on)
    h, out = handler()
    h._do_ai({"index": "BTC", "on": "1"})
    check("an index from another market is refused", out["code"] == 400 and not d.on)
    h, out = handler()
    h._do_ai({"index": "BANKNIFTY", "on": "1"})
    body = json.loads(out["body"])
    check("switching BANKNIFTY on turns on BANKNIFTY only", body["ok"] and d.enabled == {"BANKNIFTY": True}
          and body["ai"]["enabled"] == {"NIFTY": False, "BANKNIFTY": True, "SENSEX": False}, body.get("ai", {}).get("enabled"))
    check("the reply names the index", "BANKNIFTY" in body["message"])
    h, out = handler()
    h._api_ai("me@example.invalid")
    pub = json.loads(out["body"])
    check("GET /api/ai: switch, open tickets, decisions, record, limits",
          {"on", "open", "recent", "record", "limits", "paper_only"} <= set(pub))
finally:
    feeds.for_user = orig

FSRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
check("the feed prices AI tickets on both tick loops and checks them on every recompute",
      FSRC.count("self._ai_prices()") == 2 and "self.ai.track(name, rec)" in FSRC)
check("squared off at the bell with the rule tickets", "self.ai.book.close_all_at_bell()" in FSRC)
ASRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_desk.py")).read()
check("nothing in the AI desk itself touches live orders", "live_orders" not in ASRC and "place_order" not in ASRC)
check("the feed bridges AI tickets to the executor under their own source, so they need their own switch",
      'self.live.on_ticket_event(kind, trade, source="ai")' in FSRC)
check("the AI tab has the AI live-orders switch, and it names real money",
      'id="ailive"' in SRC and "Place REAL Zerodha orders for the AI desk" in SRC
      and 'source: "ai"' in SRC)

print("AI DESK TEST PASSED" if not fails else f"AI DESK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
