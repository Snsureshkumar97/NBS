#!/usr/bin/env python3
"""Gold on TradingView's moving-average rating (the user, 14 Oct 2026: "change gold to trading view moving averages";
config.CFD_RULES["GOLD"], cfd_rules._evaluate_tv / _apply_tv, tickets' ATR trail and reversal). The live reading from
30 days of candles is the 3-year study's (crypto_tv_ratings_study.py) at random bars; the live plan is the study's "as
built" variant, its target 1:1 with the stop since 15 Oct 2026 (3 x ATR - gold_tp_study.py's 3 x ATR; the user: "i want
to make gold target 1:1 risk and reward"); a reading becomes that ticket (stop 3 x ATR, target 3 x ATR, nothing moved on
the way - the trail, from 5 x ATR, never starts before it). With a far target (BTC's TradingView MA option, 10 x ATR)
the trail starts at 5 x ATR in profit and follows 2 x ATR behind the best price, Exness's stop moved only every
quarter of that distance; an opposite Strong reading closes it and trades the other way at the same close; no cooldown;
24 hours at most; BTC stays on the engine; the card's words; the Journal's TV badge. Paper only, no network."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import cfd_rules
import config
import tickets
import tv_rating

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

PLAN = config.CFD_RULES.get("GOLD") or {}
print("1. THE LIVE PLAN IS THE ONE MEASURED")
import crypto_tv_ratings_study as cts
check("gold: TradingView's rating on its MOVING-AVERAGE votes, stop 3 x ATR, target 3 x ATR (1:1) - gold_tp_study's "
      "3 x ATR, trail settings unchanged (5 / 2 x ATR)", PLAN.get("kind") == "tv_rating" and PLAN.get("basis") == "ma"
      and PLAN.get("stop_atr") == 3.0 and tuple(PLAN.get("trail_atr")) == (5.0, 2.0)
      and PLAN.get("target_atr") == 3.0 and 3.0 in __import__("gold_tp_study").TPS
      and not cfd_rules.trail_first(PLAN), PLAN)
FAR = config.CRYPTO_SYSTEMS["BTC"]["tv_ma"]
check("BTC's TradingView MA option keeps the far 10 x ATR target, and its trail starts first",
      FAR.get("target_atr") == 10.0 and cfd_rules.trail_first(FAR) and FAR.get("stop_atr") == 3.0, FAR)
check("BTC stays on the engine (no rule)", config.cfd_rule("BTC") is None and config.cfd_exit_plan("BTC") is not None)
src_study = open(os.path.join(HERE, "crypto_tv_ratings_study.py")).read()
check("the study walks it with these numbers: stop 3 x ATR, trail 5 / 2 x ATR, an opposite Strong reading reverses",
      'e - 3 * atr_tv[i] if s == "CE" else e + 3 * atr_tv[i]' in src_study
      and '5 * c["atr"], 2 * c["atr"], opp, target=tp' in src_study)

print("2. THE LIVE READING IS THE STUDY'S - 30 days of candles, as live, at random bars")
try:
    import exness_data as ed
    full = ed.load("XAUUSD")[["Open", "High", "Low", "Close", "Volume"]]
except Exception:
    full = None
if full is None or not len(full):
    print("  (skipped: no Exness history cache on this machine)")
else:
    ref = tv_rating.ratings(full)
    rng = np.random.default_rng(5)
    start = int(full.index.searchsorted(full.index[0] + pd.Timedelta(days=45)))
    strong = np.flatnonzero(np.abs(ref["ma"].to_numpy()) > 0.5)
    strong = strong[strong > start]
    bars = list(rng.choice(strong[strong < len(full) - 1], 120, replace=False)) + \
        list(rng.choice(np.arange(start, len(full) - 1), 120, replace=False))
    bad, diffs, sb = 0, [], []
    for i in bars:
        t_close = full.index[i] + pd.Timedelta(minutes=15)
        win = full[(full.index > t_close - pd.Timedelta(days=cfd_rules.HISTORY_DAYS)) & (full.index <= full.index[i + 1])]
        ev = cfd_rules.evaluate("GOLD", win, now=t_close + pd.Timedelta(minutes=1))
        x = ref["ma"].iloc[i]
        want = 1 if x > 0.5 else -1 if x < -0.5 else 0
        diffs.append(abs(ev["values"]["rating"] - x))
        if ev["side"] != want:
            bad += 1
            sb.append((str(full.index[i]), round(x, 3), round(ev["values"]["rating"], 3)))
        if abs(ev["atr"] - ref["atr"].iloc[i]) > 1e-6 * max(1.0, ref["atr"].iloc[i]):
            bad += 1
    check(f"buy / sell / none the same as the 3-year reading on {len(bars) - bad} of {len(bars)} bars "
          "(no more than one in a hundred may differ - a 30-day window seeds the 200-candle averages)",
          bad <= len(bars) // 100, sb[:4])
    check("the rating itself within one vote (1/15) everywhere", max(diffs) < 1 / 15 + 1e-9, max(diffs))

print("3. A READING BECOMES THE MEASURED TICKET")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 14, 10, 0, 30, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
ATR, SPOT = 5.0, 4000.0
def reading(side=1, x=None, bar="2026-10-14T04:30:00+00:00", fresh=True, spot=SPOT, ready=True, plan=None):
    x = (0.8 if side > 0 else -0.8 if side < 0 else 0.3) if x is None else x
    ev = {"ready": ready, "votes": {}, "filters": {}, "side": side, "atr": ATR, "close": spot, "bar_close": bar,
          "fresh": fresh, "values": {"rating": x, "basis": "ma", "close": spot, "buy": 13, "sell": 1, "n": 15}}
    if not ready:
        ev = {"ready": False, "why": "only 120 closed candles - 250 needed"}
    return cfd_rules.apply({"index": "GOLD", "spot": spot, "cfd_spread": 0.26, "quote_age_s": 1.0,
                            "reach_to_risk": 3.2, "reach_points": 40.0}, ev, "GOLD", plan)
r = reading()
check("gold, Strong Buy: BUY, stop 3 x ATR (3,985), target 1:1 (4,015; T1 / T2 4,005 / 4,010 on the way)",
      r["bias"] == "BULLISH" and r["option_type"] == "CE" and r["index_stop_loss"] == 3985.0
      and r["index_targets"] == [4005.0, 4010.0, 4015.0] and r["reach_to_risk"] == 1.0
      and r["target_basis"] == "rule", (r["index_stop_loss"], r["index_targets"], r.get("reach_to_risk")))
rf = reading(plan=FAR)
check("a far plan (BTC's option): target 10 x ATR (4,050), the trail frozen in price (25 / 10)",
      rf["index_targets"] == [4016.67, 4033.33, 4050.0] and rf["rule_trail"] == [25.0, 10.0], (rf["index_targets"], rf.get("rule_trail")))
check("...its own system (tv_ma - never the Indian Trend Rider's exit), a reversal, no cooldown, its card rows",
      r["rule"]["system"] == "tv_ma" and r["rule"]["reverse"] and r["rule"]["no_cooldown"]
      and r["rule"]["filters"][0]["ok"] and "Strong Buy" in r["rule"]["tag"] and not tickets.rules_exit(
          {"plain_exit": True, "system": "tv_ma"}), r["rule"]["filters"])
rs = reading(side=-1)
check("Strong Sell: SELL, stop above, target below at 1:1", rs["option_type"] == "PE" and rs["index_stop_loss"] == 4015.0
      and rs["index_targets"][2] == 3985.0)
rq = reading(side=0)
check("Buy (not Strong): no trade, no levels, no trail, and why in words", rq["bias"] == "NEUTRAL"
      and rq["index_stop_loss"] is None and rq.get("rule_trail") is None and rq["rule"]["side"] == 0
      and "+0.30 (Buy)" in rq["action"], rq["action"])
rw = reading(ready=False)
check("no reading yet: waits", rw["bias"] == "NEUTRAL" and "120 closed candles" in rw["action"], rw["action"])

print("4. GOLD'S 1:1 TICKET: ONE STOP, ONE TARGET")
d0 = tempfile.mkdtemp()
gb = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d0, "g.csv"))
gb.update("GOLD", reading())
g = gb.books["GOLD"].trade
check("opened: stop 3,985, target 4,015, exit at T3", g["index_sl"] == 3985.0 and g["index_targets"][2] == 4015.0
      and g["exit_at"] == "T3" and g["system"] == "tv_ma", (g["index_sl"], g["index_targets"]))
gb.tick_price("GOLD", 4011.0)
check("past T1 and T2: the stop does not move, still open", g["index_sl"] == 3985.0 and g["status"] == "OPEN"
      and not g.get("trail_active"), (g["index_sl"], g["status"]))
gb.tick_price("GOLD", 4015.0)
check("at 4,015: closed at the 1:1 target", g["status"] == "CLOSED — T3 hit (full target reached)", g["status"])

print("5. A FAR-TARGET TICKET (BTC's option): TRAIL, REVERSAL, NO COOLDOWN, 24 HOURS")
_far = reading
reading = lambda *a, **k: _far(*a, **dict(k, plan=k.get("plan", FAR)))
d = tempfile.mkdtemp()
bk = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t.csv"))
seen = []
bk.listeners.append(lambda kind, trade: seen.append((kind, trade.get("index_sl"), trade.get("option_type"))))
evs = bk.update("GOLD", reading())
t = bk.books["GOLD"].trade
check("opened: stop 3,985, exit at the far target T3, the trail frozen, best = entry",
      any(e["kind"] == "opened" for e in evs) and t["index_sl"] == 3985.0 and t["exit_at"] == "T3"
      and t["atr_trail"] == [25.0, 10.0] and t["trail_best"] == SPOT and t["system"] == "tv_ma", t and t.get("atr_trail"))
check("...what Exness's executor needs: a direction, the stop and the target", t["option_type"] == "CE"
      and t["index_sl"] is not None and t["index_targets"][2] is not None)
bk.tick_price("GOLD", 4020.0)                                 # +20: under the 25 that starts the trail
check("under 5 x ATR in profit: the stop has not moved", t["index_sl"] == 3985.0 and not t.get("trail_active"))
seen.clear()
bk.tick_price("GOLD", 4026.0)                                 # +26: the trail starts - 10 behind the best
check("5 x ATR in profit: the stop is 10 behind the best (4,016), and Exness is told", t["index_sl"] == 4016.0
      and ("trailed", 4016.0, "CE") in seen, seen)
seen.clear()
bk.tick_price("GOLD", 4027.0)
check("one more dollar: the ticket's stop follows (4,017), Exness is NOT told yet (under a quarter of 10)",
      t["index_sl"] == 4017.0 and not seen, seen)
bk.tick_price("GOLD", 4029.0)
check("2.5 or more past the last stop sent: Exness told again (4,019)", t["index_sl"] == 4019.0
      and ("trailed", 4019.0, "CE") in seen, seen)
bk.tick_price("GOLD", 4024.0)
check("a dip that does not reach it: the stop never loosens", t["index_sl"] == 4019.0 and t["status"] == "OPEN")
bk.tick_price("GOLD", 4018.5)
check("through the trailed stop: closed, called a trailing stop", t["status"] == "CLOSED — trailing stop hit", t["status"])
_now["t"] += dt.timedelta(minutes=15)
evs = bk.update("GOLD", reading(bar="2026-10-14T04:45:00+00:00"))
t2 = bk.books["GOLD"].trade
check("the next close still Strong Buy: in again at once - no cooldown", t2 is not t and t2["status"] == "OPEN"
      and t2["option_type"] == "CE", [e["kind"] for e in evs])
_now["t"] += dt.timedelta(minutes=15)
evs = bk.update("GOLD", reading(bar="2026-10-14T05:00:00+00:00"))
check("a Strong Buy close with the buy still open: nothing closes, nothing reopens (only the OTHER side reverses)",
      bk.books["GOLD"].trade is t2 and t2["status"] == "OPEN" and not evs
      and bk.books["GOLD"].wait_reason[1] == "POSITION OPEN", ([e["kind"] for e in evs], t2["status"]))
seen.clear()
_now["t"] += dt.timedelta(minutes=15)
evs = bk.update("GOLD", reading(side=-1, bar="2026-10-14T05:15:00+00:00", spot=3995.0))
t3 = bk.books["GOLD"].trade
check("an opposite Strong Sell at a close: the buy is CLOSED as reversed and a SELL opens at the same close",
      t2["status"] == "CLOSED — opposite signal (reversed)" and t3 is not t2 and t3["option_type"] == "PE"
      and [e["kind"] for e in evs] == ["closed", "opened"], (t2["status"], [e["kind"] for e in evs]))
check("...Exness hears the close before the new order", [k for k, _, _ in seen][:2] == ["closed", "opened"], seen)
evs = bk.update("GOLD", reading(side=1, bar="2026-10-14T05:15:00+00:00", spot=3995.0))
check("the same close is never decided twice", bk.books["GOLD"].trade is t3 and t3["status"] == "OPEN"
      and not evs, [e["kind"] for e in evs])
evs = bk.update("GOLD", reading(side=1, bar="2026-10-14T05:30:00+00:00", fresh=False, spot=3995.0))
check("an opposite reading past the 2-minute window reverses nothing", t3["status"] == "OPEN")
bk.update("GOLD", reading(side=0, bar="2026-10-14T05:30:00+00:00"))
check("a reading that is not Strong leaves an open ticket alone", t3["status"] == "OPEN")
_now["t"] += dt.timedelta(hours=24)
bk.tick_price("GOLD", 3990.0)
check("24 hours later: closed by the time limit", "time limit" in t3["status"], t3["status"])

reading = _far
print("6. AN OLD-STYLE RULE KEEPS ITS OWN WAYS (no trail, no reversal)")
old = dict(config.CFD_RULES)
config.CFD_RULES = dict(config.CFD_RULES_RETIRED)
_now["t"] = dt.datetime(2026, 10, 15, 10, 0, 30, tzinfo=IST)
ob = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "o.csv"))
ev = {"ready": True, "votes": {"d1_trend": 1, "h1_trend": 1, "roc12": 1}, "filters": {"adx_rising": True, "vol_rising": True},
      "side": 1, "atr": ATR, "close": SPOT, "bar_close": "2026-10-15T04:30:00+00:00", "fresh": True}
ob.update("GOLD", cfd_rules.apply({"index": "GOLD", "spot": SPOT, "cfd_spread": 0.26, "quote_age_s": 1.0}, ev, "GOLD"))
ot = ob.books["GOLD"].trade
ob.tick_price("GOLD", SPOT + 9.0)
check("the retired gold rule: no trail frozen, its stop unmoved", ot["atr_trail"] is None and ot["index_sl"] == 3985.0,
      (ot.get("atr_trail"), ot["index_sl"]))
ev2 = dict(ev, side=-1, votes={"d1_trend": -1, "h1_trend": -1, "roc12": -1}, bar_close="2026-10-15T04:45:00+00:00")
_now["t"] += dt.timedelta(minutes=15)
ob.update("GOLD", cfd_rules.apply({"index": "GOLD", "spot": SPOT, "cfd_spread": 0.26, "quote_age_s": 1.0}, ev2, "GOLD"))
check("...and an opposite reading does not reverse it", ot["status"] == "OPEN"
      and ob.books["GOLD"].wait_reason[1] == "POSITION OPEN", ob.books["GOLD"].wait_reason)
config.CFD_RULES = old

print("7. THE CARD AND THE JOURNAL")
import explain
x = explain.explain(r)
check("gold's card: the rating, ONE target 1:1, the reversal - no trail words", "TradingView MA rating" in x["headline"]
      and "one target at 3 x ATR (1:1 with the stop)" in x["verdict"] and "trades the other way" in x["verdict"]
      and "trails" not in x["verdict"] and "1:1 with the stop" in x["levels"][1] and "far" not in x["levels"][1]
      and "it stays there" in x["levels"][0] and "1:1 with the stop" in r["rule"]["note"], (x["verdict"], x["levels"]))
xf = explain.explain(rf)
check("a far plan's card: the trail, the reversal, the far target", "trails 2 x ATR behind the best price" in xf["verdict"]
      and "trades the other way" in xf["verdict"] and "far target, 10 x ATR" in xf["levels"][1], (xf["verdict"], xf["levels"]))
xq = explain.explain(rq)
check("no trade: the card says what it needs", "Strong Buy (over +0.5)" in xq["verdict"], xq["verdict"])
wsrc = open(os.path.join(HERE, "web_server.py")).read()
check("the Journal badges its trades TV", 't.system === "tv_ma"' in wsrc)

print("8. A FAR TARGET'S LADDER SHOWS THE TRAIL; GOLD'S 1:1 LADDER IS T1 / T2 / T3 AND THE STOP")
d2 = tempfile.mkdtemp()
pb = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d2, "t.csv"))
_now["t"] = dt.datetime(2026, 10, 16, 10, 0, 30, tzinfo=IST)
pb.update("GOLD", reading(bar="2026-10-16T04:30:00+00:00", plan=FAR))
pub = pb.public("GOLD")["ticket"]
check("the open ticket tells the page its trail and whether it is on", pub.get("atr_trail") == [25.0, 10.0]
      and pub.get("trail_active") is False, (pub.get("atr_trail"), pub.get("trail_active")))
pb.tick_price("GOLD", 4026.0)
check("...and when the trail has started", pb.public("GOLD")["ticket"]["trail_active"] is True)
import feeds
pr = feeds._public(dict(reading(plan=FAR), index="GOLD", candles=None), "GOLD")
check("the live reading tells the page the trail too", pr.get("rule_trail") == [25.0, 10.0], pr.get("rule_trail"))
import shutil, subprocess
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    a0 = wsrc.index("const num=")
    z0 = wsrc.index("\n", wsrc.index("\n", a0) + 1) + 1
    a1 = wsrc.index("function trailFirst(")
    z1 = wsrc.index("\n}\n", wsrc.index("function trailRungs(")) + 3
    prog = wsrc[a0:z0] + wsrc[a1:z1] + r'''
const assert = require("assert");
const sell = trailRungs(4000, -1, [61, 24.4], 3634, 4036.6, false);
assert.deepStrictEqual(sell.map(x => x[0]), ["Trail", "T3", "Stop"], "three rungs: trail, target, stop");
assert.strictEqual(sell[0][2], 3939, "a sell's trail starts 61 below the entry");
assert.ok(sell[0][1] === "Trail starts" && sell[1][1] === "Target" && sell[2][1].includes("then trails 24.40 behind"), sell);
const on = trailRungs(4000, 1, [61, 24.4], 4366, 4039.6, true);
assert.ok(on[0][2] === 4061 && on[0][1].includes("trailing") && on[2][1].includes("24.40 behind the best"), on);
assert.strictEqual(trailFirst(4000, 4015, [25, 10]), false, "gold 1:1: target 15 away, trail from 25 - no trail ladder");
assert.strictEqual(trailFirst(4000, 3985, [25, 10]), false, "...a sell too");
assert.strictEqual(trailFirst(4000, 4050, [25, 10]), true, "a far target: the trail ladder");
console.log("ok:trail");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the ladder: 'Trail starts' at entry -/+ the start distance, 'Target', the stop and its gap",
          "ok:trail" in rr.stdout, (rr.stdout + rr.stderr)[-600:])
check("both ladders use it (open ticket and live reading) only when the trail comes before the target",
      "trailRungs(base, sgn, tk.atr_trail" in wsrc and "trailRungs(base, r.option_type" in wsrc
      and "const trl = cfd && tk.atr_trail && trailFirst(base, tg[2], tk.atr_trail);" in wsrc
      and "r.option_type && trailFirst(base, tg[2], r.rule_trail)" in wsrc)

print()
print("CFD TV RULE TEST PASSED" if not fails else f"CFD TV RULE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
