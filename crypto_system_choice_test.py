#!/usr/bin/env python3
"""Each account chooses its own crypto strategy per instrument (the user, 14 Oct 2026: "in trade setting add a drop down
to change the startegy for crypt also"; config.CRYPTO_SYSTEMS, TicketBook.system_for / cfd_plan, feeds._cfd_rule,
cfd_rules' plan argument, the Dashboard's System selector). Nothing changes for an account that has not chosen; a
choice is saved; a strategy an instrument does not offer is refused; two accounts on different rules never see each
other's readings; the page offers each coin its own list. Paper only, no network."""
import datetime as dt
import json
import os
import shutil
import subprocess
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

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE LISTS AND THE DEFAULTS")
check("BTC: the tool's votes, RSI-2 bounce, TradingView MA; gold: TradingView MA, the tool's votes, the gold trend rule",
      list(config.CRYPTO_SYSTEMS["BTC"]) == ["engine", "rsi2", "tv_ma"]
      and list(config.CRYPTO_SYSTEMS["GOLD"]) == ["tv_ma", "engine", "gold_trend"])
check("no offered rule carries the retired forward-test guard (it would switch live orders off)",
      all("forward_test" not in (p or {}) for c in config.CRYPTO_SYSTEMS.values() for p in c.values()))
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
b = tickets.TicketBook(market="crypto", path=path)
check("an account that has not chosen runs what runs today: BTC the engine, gold TradingView's MA rating",
      b.system_for("BTC") == "engine" and b.system_for("GOLD") == "tv_ma"
      and b.cfd_plan("BTC") is None and b.cfd_plan("GOLD") is config.CFD_RULES["GOLD"])
ses = b.session()
check("the page gets each coin's system, its own choices and their names", ses["systems_by"] == {"BTC": "engine", "GOLD": "tv_ma"}
      and ses["system_choices_by"] == {"BTC": ["engine", "rsi2", "tv_ma"], "GOLD": ["tv_ma", "engine", "gold_trend"]}
      and ses["system_labels"]["rsi2"] == "RSI-2 bounce", (ses["systems_by"], ses["system_choices_by"]))

print("2. A CHOICE IS ONE ACCOUNT'S, SAVED, AND ONLY FROM THE COIN'S OWN LIST")
b.configure(system="rsi2", system_index="BTC")
b.configure(system="gold_trend", system_index="BTC")            # gold's rule, not offered on BTC
b.configure(system="trend_rider", system_index="GOLD")           # an Indian system
check("BTC set to RSI-2; gold's rule refused on BTC; an Indian system refused on gold",
      b.system_for("BTC") == "rsi2" and b.system_for("GOLD") == "tv_ma")
check("RSI-2's plan, without the guard", (b.cfd_plan("BTC") or {}).get("votes") == ["rsi2"]
      and "forward_test" not in b.cfd_plan("BTC"))
b.configure(system="engine", system_index="GOLD")
check("gold set to the engine: no rule", b.system_for("GOLD") == "engine" and b.cfd_plan("GOLD") is None)
b2 = tickets.TicketBook(market="crypto", path=path)
check("saved across a restart", b2.system_for("BTC") == "rsi2" and b2.system_for("GOLD") == "engine")
other = tickets.TicketBook(market="crypto", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
check("another account is untouched", other.system_for("BTC") == "engine" and other.system_for("GOLD") == "tv_ma")
nb = tickets.TicketBook(market="nse_index", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
check("the Indian market is untouched", nb.session()["system_choices"] == ["rules", "trend_rider", "tv_rating"]
      and nb.cfd_plan("NIFTY") is None)

print("3. THE FEED TRADES EACH ACCOUNT ON ITS OWN RULE")
import feeds
idx = pd.date_range("2026-10-01 00:00", periods=600, freq="15min", tz="UTC")
rng = np.random.default_rng(3)
close = 84000 + np.cumsum(rng.normal(0, 40, len(idx)))
DF = pd.DataFrame({"Open": close, "High": close + 30, "Low": close - 30, "Close": close, "Volume": 100.0}, index=idx)


class Prov:
    def get_ohlc(self, name, interval, lookback_days=None):
        return DF


seen = []
real_eval = cfd_rules.evaluate
cfd_rules.evaluate = lambda name, df, now=None, plan=None: (seen.append((name, (plan or {}).get("label")))
                                                           or {"ready": False, "why": "test"})
try:
    fa = feeds.Feed("t:a", "a@example.invalid", "crypto")
    fa.tickets.configure(system="rsi2", system_index="BTC")
    fb = feeds.Feed("t:b", "b@example.invalid", "crypto")
    for f in (fa, fb):
        f._provider_for = lambda name, provider: Prov()
    ra = fa._cfd_rule("BTC", {"index": "BTC", "spot": 84000.0})
    rb = fb._cfd_rule("BTC", {"index": "BTC", "spot": 84000.0})
    rg = fb._cfd_rule("GOLD", {"index": "GOLD", "spot": 4000.0})
finally:
    cfd_rules.evaluate = real_eval
check("account A (RSI-2) reads BTC with RSI-2; account B (no choice) leaves BTC to the engine; B's gold on TradingView MA",
      seen == [("BTC", "RSI-2 bounce (87%)"), ("GOLD", "TradingView MA rating")]
      and (ra.get("rule") or {}).get("label") == "RSI-2 bounce (87%)" and rb.get("rule") is None
      and (rg.get("rule") or {}).get("label") == "TradingView MA rating", seen)

print("4. TWO RULES ON ONE COIN NEVER SHARE A READING")
now = DF.index[-1] + pd.Timedelta(minutes=1)
plan_a, plan_b = config.cfd_plan("BTC", "rsi2"), config.cfd_plan("BTC", "tv_ma")
ea = cfd_rules.evaluate("BTC", DF, now=now, plan=plan_a)
eb = cfd_rules.evaluate("BTC", DF, now=now, plan=plan_b)
later = DF.index[-1] + pd.Timedelta(minutes=16)                    # the new candle not in yet: each shows its last one
wa = cfd_rules.evaluate("BTC", DF, now=later, plan=plan_a)
wb = cfd_rules.evaluate("BTC", DF, now=later, plan=plan_b)
check("both ready on the close", ea["ready"] and eb["ready"], (ea.get("why"), eb.get("why")))
check("while the next candle loads, each rule shows ITS OWN last reading", wa["values"] == ea["values"]
      and wb["values"] == eb["values"] and "rsi2" in wa["values"] and "rating" in wb["values"]
      and not wa["ready"] and not wb["ready"], (list(wa["values"]), list(wb["values"])))

print("5. THE PAGE (node)")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
wsrc = open(os.path.join(HERE, "web_server.py")).read()
if not NODE:
    check("node is available", False)
else:
    a = wsrc.index("// Each index's own lots (the user, 7 Oct 2026")
    z = wsrc.index("\n}\n", wsrc.index("function lotsDash(s){")) + 3
    sess = json.dumps(dict(b.session(), lots=0.1, lots_by={}, lot_choices=[0.01, 0.1]))
    prog = r'''
const assert = require("assert");
const esc = t => String(t);
let LAST = null, LOTS = 1, LOTS_HOLD = 0, CUR = "BTC";
function render(){}
const els = {}; const document = {activeElement: null};
function $(id){ return els[id] || null; }
''' + wsrc[a:z] + r'''
els.kdlots = {innerHTML: "", dataset: {}, querySelectorAll(){ return []; }, onchange: null};
const s = {order: ["BTC", "GOLD"], indices: {BTC: {cfd: true}, GOLD: {cfd: true}}, session: ''' + sess + r'''};
LAST = s;
lotsDash(s);
const h = els.kdlots.innerHTML;
const row = k => h.slice(h.indexOf(`data-sys="${k}"`), h.indexOf("</select>", h.indexOf(`data-sys="${k}"`)));
assert.ok(h.includes(">System<"), "a System column on crypto");
assert.ok(row("BTC").includes(">RSI-2 bounce<") && row("BTC").includes(">Tool votes (EMA, MACD, VWAP)<")
          && row("BTC").includes(">TradingView MA rating<") && !row("BTC").includes("Gold trend"), "BTC's own list");
assert.ok(row("GOLD").includes(">Gold trend rule<") && !row("GOLD").includes("RSI-2"), "gold's own list");
console.log("ok:crypto-sys");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the Dashboard shows a System dropdown per coin with its own strategies, by name", "ok:crypto-sys" in rr.stdout
          and rr.returncode == 0, (rr.stdout + rr.stderr)[-700:])

print()
print("CRYPTO SYSTEM CHOICE TEST PASSED" if not fails else f"CRYPTO SYSTEM CHOICE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
