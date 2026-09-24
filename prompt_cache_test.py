#!/usr/bin/env python3
"""Prompt caching for the AI desk, and the logging that says whether it pays.

Asked for by the user on 24 Sep 2026 after a walk through how Anthropic's prompt
cache works against market_bot.py. The desk decides once per 15-minute candle:

  * its system prompt (which carries the lookup tools with it) is now marked for
    an hour, so it is written once and read at every later candle instead of
    going cold after five minutes;
  * inside one decision - up to six model rounds, each re-sending the snapshot
    and every tool result so far - the growing conversation is cached too;
  * a single-shot request (a chat answer, an automatic update) is NOT given the
    tail: its snapshot is different every time, so it would only ever pay the
    1.25x write;
  * if the API ever rejects the cache options, the request is repeated without
    them and the desk decides regardless;
  * every decision records its tokens, the cache's read and write figures, its
    rounds and the hit rate, and the day's totals and hit rate reach the AI tab.

Nothing here can reach Anthropic or an account: every model call is a fake.
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anthropic

import ai_desk as ad
import bot_data
import config
import market_bot as mb

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def resp(blocks, stop="tool_use", inp=500, out=50, read=0, write=0):
    r = type("R", (), {})()
    r.content, r.stop_reason, r.model, r._request_id = blocks, stop, mb.MODEL, "req"
    r.usage = type("U", (), {"input_tokens": inp, "output_tokens": out,
                             "cache_read_input_tokens": read, "cache_creation_input_tokens": write})()
    return r


def submit(action="wait", **kw):
    return Block(type="tool_use", id="s", name="submit_decision", input=dict({"action": action, "reason": "x"}, **kw))


def lookup(i=0):
    return Block(type="tool_use", id=f"t{i}", name="get_news", input={})


class Scripted:
    """Records every request as plain JSON, answers from a script."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self
    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def http(status):
    return type("R", (), {"status_code": status, "request": object(), "headers": {}})()


mb.key_present = lambda: True
orig_call = bot_data.call
bot_data.call = lambda name, args, c: ('{"pcr":0.9}', False)
ctx = bot_data.Ctx("me@example.invalid", "nse_index", "NIFTY")
real_decide = mb.decide


print("1. THE DESK'S DECISION IS CACHED - THE SYSTEM PROMPT FOR AN HOUR, THE GROWING CONVERSATION AS IT GROWS")
cl = Scripted([resp([lookup(0)]), resp([lookup(1)]), resp([submit("wait")])])
dec, meta = real_decide("entry", "NIFTY", "SNAPSHOT-VALUE-9471", {"lots": 2}, tools_ctx=ctx, client=cl)
check("three rounds ran", len(cl.calls) == 3 and meta["rounds"] == 3, meta.get("rounds"))
for i, c in enumerate(cl.calls):
    check(f"round {i + 1}: the system prompt is marked for an hour, which caches the tools before it as well",
          c["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}, c["system"][0]["cache_control"])
    check(f"round {i + 1}: the conversation's tail is cached automatically, at the top level of the request",
          c["extra_body"].get("cache_control") == {"type": "ephemeral"}, c["extra_body"])
    check(f"round {i + 1}: refusal fallback still requested",
          c["extra_body"].get("fallbacks") == "default" and "server-side-fallback-2026-07-01" in c["extra_headers"]["anthropic-beta"])
check("the 1-hour marker comes before a 5-minute tail, the order the API allows (longer lifetime first)",
      all("ttl" in c["system"][0]["cache_control"] and "ttl" not in c["extra_body"]["cache_control"] for c in cl.calls))
check("the system prompt and the tool list are byte-identical on every round that can read the cache",
      cl.calls[0]["system"] == cl.calls[1]["system"] == cl.calls[2]["system"]
      and cl.calls[0]["tools"] == cl.calls[1]["tools"] == cl.calls[2]["tools"])
check("each round re-sends the whole conversation so far, so the earlier ones are what the cache reads",
      len(cl.calls[0]["messages"]) == 1 and len(cl.calls[1]["messages"]) == 3 and len(cl.calls[2]["messages"]) == 5
      and cl.calls[1]["messages"][0] == cl.calls[0]["messages"][0] == cl.calls[2]["messages"][0])
check("the snapshot is in the user message, behind everything stable - never in the system prompt",
      "SNAPSHOT-VALUE-9471" in cl.calls[0]["messages"][0]["content"]
      and "SNAPSHOT-VALUE-9471" not in json.dumps(cl.calls[0]["system"]) and "SNAPSHOT-VALUE-9471" not in json.dumps(cl.calls[0]["tools"]))
check("no dates, ids or per-request values in the system prompt (it would break the cache for every request)",
      cl.calls[0]["system"][0]["text"] == mb.DESK_SYSTEM)

loop = [resp([lookup(i)]) for i in range(mb.MAX_TOOL_ROUNDS)] + [resp([submit("hold")])]
cl = Scripted(loop)
real_decide("review", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
check("only the last, forced round changes the tool list (a change invalidates the cache, so it cannot read it)",
      [t["name"] for t in cl.calls[-1]["tools"]] == ["submit_decision"]
      and all(c["tools"] == cl.calls[0]["tools"] for c in cl.calls[:-1]))
cl_e = Scripted([resp([submit("wait")])])
real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl_e)
check("entry and review are different requests (their submit tool differs), each stable within itself",
      cl_e.calls[0]["tools"] != cl.calls[0]["tools"] and cl_e.calls[0]["system"] == cl.calls[0]["system"])


print("2. A SINGLE-SHOT REQUEST IS NOT GIVEN THE TAIL - ITS SNAPSHOT NEVER REPEATS, SO IT WOULD ONLY PAY THE WRITE")
def one_shot(fn):
    c = Scripted([resp([Block(type="text", text="Hold.")], "end_turn")])
    fn(c)
    return c.calls[0]

call = one_shot(lambda c: mb.ask("Should I hold?", [], "{}", client=c))
check("a chat answer: the system prompt keeps its plain 5-minute marker, no tail",
      call["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in call["extra_body"], call["extra_body"])
call = one_shot(lambda c: mb.auto_update("NIFTY", "{}", {"what": "target 1 hit"}, client=c))
check("an automatic update: the same", call["system"][0]["cache_control"] == {"type": "ephemeral"}
      and "cache_control" not in call["extra_body"])
cl = Scripted([resp([lookup(0)]), resp([Block(type="text", text="Done.")], "end_turn")])
mb.ask("What does the chain say?", [], "{}", client=cl, tools_ctx=ctx)
check("a chat answer that uses lookups keeps the plain marker too - only the desk's decision loop is changed",
      all(c["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in c["extra_body"] for c in cl.calls))


print("3. WHAT THE DECISION REPORTS: EVERY FIGURE, SUMMED OVER ITS ROUNDS")
cl = Scripted([resp([lookup(0)], inp=3000, out=200, read=0, write=5200),
               resp([lookup(1)], inp=400, out=120, read=8000, write=600),
               resp([submit("wait")], inp=300, out=80, read=8600, write=250)])
_, meta = real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
check("input, output, cache read AND cache write are each summed across the rounds",
      (meta["input_tokens"], meta["output_tokens"], meta["cache_read_tokens"], meta["cache_write_tokens"])
      == (3700, 400, 16600, 6050), meta)
check("a response with no cache fields at all (older SDK, or a model that reports none) counts as zero, not an error",
      real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx,
                  client=Scripted([type("R", (), {"content": [submit()], "stop_reason": "tool_use", "usage": None})()]))[1]["cache_write_tokens"] == 0)
c1 = Scripted([resp([Block(type="text", text="Hold.")], "end_turn", inp=10, out=5, read=90, write=40)])
_, m1 = mb.ask("q", [], "{}", client=c1)
check("a chat answer's meta carries the write figure beside the read one", m1["cache_read_tokens"] == 90 and m1["cache_write_tokens"] == 40, m1)


print("4. THE API REFUSING THE CACHE OPTIONS MUST NOT STOP THE DESK")
mb._cache_options_rejected = False
def refusing(message):
    class Picky(Scripted):
        def create(self, **kw):
            asked = "ttl" in kw["system"][0]["cache_control"] or "cache_control" in kw["extra_body"]
            if asked:
                self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
                raise anthropic.BadRequestError(message, response=http(400), body=None)
            return super().create(**kw)
    return Picky([resp([submit("wait")]), resp([submit("wait")])])

cl = refusing("cache_control: ttl must be one of ...")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    dec, meta = real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
check("the decision is still made", dec["action"] == "wait", dec)
check("it was asked twice: with the cache options, then again without them", len(cl.calls) == 2
      and "cache_control" in cl.calls[0]["extra_body"] and "ttl" in cl.calls[0]["system"][0]["cache_control"], len(cl.calls))
check("the repeat is the request as it was before the options existed - plain marker, no tail, fallbacks kept",
      cl.calls[1]["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in cl.calls[1]["extra_body"]
      and cl.calls[1]["extra_body"].get("fallbacks") == "default", cl.calls[1]["extra_body"])
check("the rejection is said out loud (the service log), not swallowed", "cache options" in err.getvalue(), err.getvalue()[:120])
check("and it is remembered, so the next decision does not pay a failed request first", mb._cache_options_rejected is True)
cl = refusing("cache_control: ttl must be one of ...")
real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
check("...it goes straight to the plain request", len(cl.calls) == 1
      and cl.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}, len(cl.calls))

mb._cache_options_rejected = False
cl = refusing("prompt is too long")
try:
    real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    raised = None
except mb.BotError as exc:
    raised = exc
check("any OTHER 400 is still an error, not retried and not blamed on caching",
      raised is not None and len(cl.calls) == 1 and mb._cache_options_rejected is False, (raised, len(cl.calls)))
cl = refusing("the request was throttled")
try:
    real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    raised = None
except mb.BotError as exc:
    raised = exc
check("a message that merely contains the letters 'ttl' (throttled) is not mistaken for a cache complaint",
      raised is not None and len(cl.calls) == 1 and mb._cache_options_rejected is False, (raised, len(cl.calls)))
plain = Scripted([anthropic.BadRequestError("cache_control is odd", response=http(400), body=None)])
try:
    mb.ask("q", [], "{}", client=plain)
    raised = None
except mb.BotError as exc:
    raised = exc
check("a request that asked for no cache options is never retried on a cache complaint", raised is not None and len(plain.calls) == 1)
cl = Scripted([anthropic.BadRequestError("cache_control: no", response=http(400), body=None),
               anthropic.BadRequestError("something else is wrong", response=http(400), body=None)])
try:
    with contextlib.redirect_stderr(io.StringIO()):
        real_decide("entry", "NIFTY", "{}", {}, tools_ctx=ctx, client=cl)
    raised = None
except mb.BotError as exc:
    raised = exc
check("if the repeat fails too, that is the error reported - and it is a BotError like any other",
      raised is not None and "something else is wrong" in str(raised), raised)
mb._cache_options_rejected = False


print("5. EVERY DECISION RECORDS WHAT IT COST AND WHAT THE CACHE DID FOR IT")
T = {"now": dt.datetime(2026, 9, 24, 10, 0, 50, tzinfo=IST), "clock": 1_789_000_000.0}


def chain(spot=25010.0, ce=130.0, pe=120.0):
    strikes = []
    for k in range(24500, 25550, 50):
        c, p = ce + (25000 - k) * 0.4, pe - (25000 - k) * 0.4
        strikes.append({"strike": float(k), "call_ltp": round(max(c, 1), 2), "put_ltp": round(max(p, 1), 2),
                        "call_bid": round(max(c, 1) - 0.25, 2), "call_ask": round(max(c, 1) + 0.25, 2),
                        "put_bid": round(max(p, 1) - 0.25, 2), "put_ask": round(max(p, 1) + 0.25, 2),
                        "call_oi": 1000, "put_oi": 900})
    return {"available": True, "expiry": "2026-09-24", "spot": spot, "strikes": strikes, "pcr": 0.9}


def rec(name="NIFTY"):
    return {"index": name, "spot": 25010.0, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
            "suggested_strike": 25000, "option_chain": chain(), "technical": {"adx": 24.0}}


class Rules:
    lots, capital, risk_pct = 1, None, 1.0


class Streamer:
    def price(self, tok):
        return None


class FakeFeed:
    def __init__(self, email):
        self.market, self.email, self.key = "nse_index", email, f"{email}#nse_index"
        self.lock = threading.RLock()
        self.tickets = Rules()
        self.streamer, self.dstream, self.faults = Streamer(), None, []
        self.live_at = __import__("time").time()
        self.state = {"feed": "ok", "indices": {n: {"rec": rec(n)} for n in config.instruments_in("nse_index")}}
    def instruments(self):
        return config.instruments_in("nse_index")
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


USAGE = {"input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 4000, "cache_write_tokens": 0, "rounds": 2,
         "looked_at": ["News"]}
NEXT = []


HOOK = []


def fake_decide(kind, index, context, desk, tools_ctx=None, client=None):
    d, meta = NEXT.pop(0)
    if HOOK:
        HOOK.pop(0)()
    return d, dict(USAGE, **meta)


mb.decide = fake_decide
N = [0]


def desk(email=None):
    N[0] += 1
    f = FakeFeed(email or f"cache{N[0]}@example.invalid")
    return f, ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)


f, d = desk()
d._roll_day()
d.enabled = {"NIFTY": True}
check("a fresh day: no cache figure yet (not zero percent - there has been nothing to hit)",
      d.public()["limits"]["cache_hit_pct"] is None
      and d.public()["limits"]["tokens_today"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})

NEXT.append(({"action": "wait", "reason": "nothing clear"}, {}))
r = d._entry("NIFTY", rec(), None)
check("a wait records its usage: tokens, cache read and write, rounds and the hit rate",
      r["usage"] == {"input": 1000, "output": 100, "cache_read": 4000, "cache_write": 0, "rounds": 2, "cache_hit_pct": 80.0}, r.get("usage"))
check("...and it reaches the decision log on disk", json.loads(open(d.decisions_path).read().splitlines()[-1])["usage"]["cache_read"] == 4000)
check("the day's counters add up all four", d.tokens_today == {"input": 1000, "output": 100, "cache_read": 4000, "cache_write": 0}, d.tokens_today)

NEXT.append(({"action": "wait", "reason": "still nothing"}, {"input_tokens": 300, "cache_read_tokens": 0, "cache_write_tokens": 700, "rounds": 1}))
r2 = d._entry("NIFTY", rec(), None)
check("a decision that WROTE the cache and read none says so: a 0% hit and a single round",
      r2["usage"]["cache_hit_pct"] == 0.0 and r2["usage"]["cache_write"] == 700 and r2["usage"]["rounds"] == 1, r2["usage"])
lim = d.public()["limits"]
check("the day's totals and hit rate reach the page: 4000 of 6000 input tokens came from the cache",
      lim["tokens_today"] == {"input": 1300, "output": 200, "cache_read": 4000, "cache_write": 700}
      and lim["cache_hit_pct"] == round(100 * 4000 / 6000, 1), lim)

NEXT.append(({"action": "enter", "option_type": "CE", "strike": 25000, "target": 160, "stop": 110,
              "target_confidence": 60, "bull_case": "trend", "bear_case": "wall", "reason": "trend and vwap agree"}, {}))
r3 = d._entry("NIFTY", rec(), None)
check("an entry records its usage too", r3["action"] == "enter" and r3["usage"]["rounds"] == 2, r3)
check("...and a ticket opened", d._open_trade("NIFTY") is not None)

NEXT.append(({"action": "hold", "reason": "still valid"}, {}))
r4 = d._review("NIFTY", d._open_trade("NIFTY"), rec(), None)
check("a hold records its usage", r4["action"] == "hold" and r4["usage"]["cache_read"] == 4000, r4)
NEXT.append(({"action": "exit", "reason": "momentum gone. more."}, {}))
r5 = d._review("NIFTY", d._open_trade("NIFTY"), rec(), None)
check("an exit records its usage", r5["action"] == "exit" and r5["usage"]["cache_read"] == 4000, r5)

f2, d2 = desk()
d2._roll_day()
d2.enabled = {"NIFTY": True}
NEXT.append(({"action": "enter", "option_type": "CE", "strike": 24000, "target": 160, "stop": 110, "reason": "x"}, {}))
r6 = d2._entry("NIFTY", rec(), None)
check("a proposal the tool rejects still records what the model call cost", r6["action"] == "rejected" and r6["usage"]["input"] == 1000, r6)

f4, d4 = desk()
d4._roll_day()
d4.enabled = {"NIFTY": True}
NEXT.append(({"action": "enter", "option_type": "CE", "strike": 25000, "target": 160, "stop": 110, "reason": "x"}, {}))
HOOK.append(lambda: d4.enabled.update({"NIFTY": False}))          # switched off while the model was thinking
r7 = d4._entry("NIFTY", rec(), None)
check("a decision made after the desk was switched off records what the call cost - the money was spent",
      r7["action"] == "rejected" and "switched off" in r7["rejected_because"] and r7["usage"]["input"] == 1000, r7)

f3, d3 = desk()
d3._roll_day()
check("a call that fails records no usage (there was none) and does not crash",
      "usage" not in d3._record("NIFTY", "entry", "error", "boom"))

print("5b. THE COUNTERS SURVIVE A RESTART, INCLUDING ONE FROM BEFORE THEY HAD CACHE FIELDS")
d._save()
again = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("a restart keeps all four", again.tokens_today == d.tokens_today, again.tokens_today)
old = json.load(open(d.state_path))
old["tokens_today"] = {"input": 900, "output": 60}                 # what a state file looked like yesterday
json.dump(old, open(d.state_path, "w"))
legacy = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("a saved state with only input and output gains the two new counters at zero",
      legacy.tokens_today == {"input": 900, "output": 60, "cache_read": 0, "cache_write": 0}, legacy.tokens_today)
NEXT.append(({"action": "wait", "reason": "x"}, {}))
legacy._roll_day = lambda: None
legacy.day = T["now"].strftime("%Y-%m-%d")
legacy._entry("NIFTY", rec(), None)
check("...and keeps counting from there, without a KeyError", legacy.tokens_today["cache_read"] == 4000 and legacy.tokens_today["input"] == 1900,
      legacy.tokens_today)
hand = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
hand.tokens_today = {"input": 0, "output": 0}                        # a counter set by hand, as older tests do
NEXT.append(({"action": "wait", "reason": "x"}, {}))
hand._entry("NIFTY", rec(), None)
check("a counter that lacks the new keys is tolerated", hand.tokens_today["cache_read"] == 4000, hand.tokens_today)
T["now"] += dt.timedelta(days=1)
stale = d.public()["limits"]
check("the morning after, before anything has rolled the day over, the page is not shown yesterday's counters or hit rate",
      stale["tokens_today"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0} and stale["cache_hit_pct"] is None
      and d.tokens_today["cache_read"] > 0, stale)          # the desk still holds yesterday's; only the page is spared it
d._roll_day()
check("the next day starts every counter again", d.tokens_today == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, d.tokens_today)
check("and the hit rate with them", d.public()["limits"]["cache_hit_pct"] is None)

mb.decide = real_decide
bot_data.call = orig_call


print("6. THE AI TAB SHOWS IT")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the stats row includes the cache tile when there is a figure", "aiCacheStat(L) ? [aiCacheStat(L)] : []" in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own helper", False, "install node to run this section")
else:
    a = SRC.index("function aiCacheStat(L){")
    b = SRC.index("\n}\n", a) + 3
    prog = "const assert = require('assert');\nconst num = (v, d) => Number(v).toLocaleString('en-US', {maximumFractionDigits: d});\n" + SRC[a:b] + r'''
assert.strictEqual(aiCacheStat(null), null, "no limits at all: no tile");
assert.strictEqual(aiCacheStat({}), null, "no counters: no tile");
assert.strictEqual(aiCacheStat({tokens_today: {input: 0, output: 0, cache_read: 0, cache_write: 0}}), null,
                   "nothing requested yet today: no tile, rather than a misleading 0%");
let r = aiCacheStat({tokens_today: {input: 1300, output: 200, cache_read: 4000, cache_write: 700}, cache_hit_pct: 66.7});
assert.strictEqual(r[1], "67%", "the server's hit rate, rounded - " + r[1]);
assert.ok(r[0].includes("4,000 of 6,000"), "the label says what the percentage is of - " + r[0]);
r = aiCacheStat({tokens_today: {input: 100, cache_read: 300, cache_write: 0}});
assert.strictEqual(r[1], "75%", "without the server's figure it is worked out from the counters - " + r[1]);
r = aiCacheStat({tokens_today: {input: 500, cache_read: 0, cache_write: 500}, cache_hit_pct: 0});
assert.strictEqual(r[1], "0%", "a real zero (written, never read) is shown as 0%, not hidden");
r = aiCacheStat({tokens_today: {input: 100, cache_read: 300, cache_write: 0}, cache_hit_pct: 0});
assert.strictEqual(r[1], "0%", "the server's figure wins even when it is 0 - a zero is an answer, not a missing value");
r = aiCacheStat({tokens_today: {input: 10}});
assert.strictEqual(r[1], "0%", "counters missing the cache keys are read as zero, not NaN");
console.log("ok:tile");
'''
    run = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (run.stdout or "") + (run.stderr or "")
    check("the tile's own helper, run for real: hidden until there is something to report, exact when there is",
          "ok:tile" in run.stdout and run.returncode == 0, out[-700:])

print("PROMPT CACHE TEST PASSED" if not fails else f"PROMPT CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
