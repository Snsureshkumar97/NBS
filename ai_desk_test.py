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


def desk(market="nse_index", email=None):
    email = email or f"u{len(ASKED)}{T['clock']}@example.invalid"
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
check("paper only: the AI book has no listeners, so live orders can never follow it", d.book.listeners == [])
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
    d.on = False
    return enter(), {"input_tokens": 1, "output_tokens": 1}


mb.decide = switched_off_mid_decision
try:
    d.step()
finally:
    mb.decide = fake_decide
check("switched off while the bot was deciding: its entry is not taken",
      d._open_trade("NIFTY") is None and "switched off" in (d.recent[0].get("rejected_because") or ""), d.recent[:1])

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
    h._do_ai({"on": "1"})
    check("a cross-site request cannot switch it on", out["code"] == 403 and not d.on)
    real.key_present = lambda: False
    h, out = handler()
    h._do_ai({"on": "1"})
    check("not without an API key", out["code"] == 503 and not d.on)
    real.key_present = lambda: True
    h, out = handler()
    h._do_ai({"on": "1"})
    check("switched on, and the page gets the desk back", json.loads(out["body"])["ok"] and d.on
          and json.loads(out["body"])["ai"]["paper_only"] is True)
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
check("nothing in the AI desk touches live orders", "live_orders" not in ASRC and "place_order" not in ASRC)

print("AI DESK TEST PASSED" if not fails else f"AI DESK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
