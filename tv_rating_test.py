#!/usr/bin/env python3
"""The TradingView rating as a live system (tv_rating.py; the user, 13 Oct 2026, "okay" to building it after
tv_ratings_study.py). Its live reading equals the 3-year study's candle for candle on real history (the same ratings()
- the study imports it), with the deeper chart window merged in or not; it enters while the rating STAYS Strong (not
only on a fresh signal, as the Trend Rider does); the recommendation it writes (the Trend Rider's levels:
trend_rider.place); offered on Nifty and Bank Nifty only, chosen by no index until the user picks it; a ticket through
the rule path that always exits the Trend Rider's measured way (rules_exit, no near-target ladder, the step at the
candle close) and re-enters after an exit while it stays Strong; the feed's hook; the card's words; the page."""
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import pandas as pd

import config
import signal_engine as se
import tickets
import trend_rider as tr
import tv_rating as tvr

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE LIVE READING IS THE STUDY'S, CANDLE FOR CANDLE (real history)")
import tv_ratings_study as tvs
hist = os.path.expanduser("~/trading-tool-logs/history")
probe = pd.DataFrame({"Open": [1.0] * 5, "High": [1.0] * 5, "Low": [1.0] * 5, "Close": [1.0] * 5, "Volume": [0.0] * 5})
check("the study computes its ratings with the live function itself", "tv_rating.ratings(df)" in open(
    os.path.join(HERE, "tv_ratings_study.py")).read() and tvs.tv_ratings(probe).equals(tvr.ratings(probe)))
for key in ("NIFTY", "BANKNIFTY"):
    p = os.path.join(hist, f"{key}_15m_3y.csv")
    if not os.path.exists(p):
        check(f"{key} history present", False, p)
        continue
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata")
    df = df.iloc[:2600][["Open", "High", "Low", "Close", "Volume"]]
    full = tvr.ratings(df)["all"].to_numpy()                 # the study's reading: the whole series at once
    strong = [i for i in range(300, len(df) - 1) if abs(full[i]) > 0.5]
    rng = np.random.default_rng(11)
    quiet = [int(i) for i in rng.choice([i for i in range(300, len(df) - 1) if abs(full[i]) <= 0.5], 30, replace=False)]
    bad, n_sig, held = [], 0, 0
    for i in strong[::4][:45] + quiet:
        if df.index[i + 1] - df.index[i] != pd.Timedelta(minutes=15):
            continue                                   # the day's last candle: no forming candle to read it from
        now = df.index[i + 1] + pd.Timedelta(seconds=60)
        ev = tvr.evaluate(key, df.iloc[:i + 2], now=now)
        want = 1 if full[i] > 0.5 else -1 if full[i] < -0.5 else 0
        lo5, hi5 = df["Low"].iloc[i - 4:i + 1].min(), df["High"].iloc[i - 4:i + 1].max()
        want_stop = lo5 if want > 0 else hi5 if want < 0 else None
        n_sig += bool(want)
        held += bool(want) and abs(full[i - 1]) > 0.5 and np.sign(full[i - 1]) == want
        if (ev.get("side") != want or (want and abs(ev["stop"] - want_stop) > 1e-9) or not ev.get("fresh")
                or abs(ev["values"]["all"] - full[i]) > 1e-9):
            bad.append((str(df.index[i]), want, ev.get("side"), ev.get("stop"), want_stop))
    check(f"{key}: {n_sig} Strong closes and quiet ones - the same side, rating and stop as the study", not bad and n_sig > 20,
          bad[:3])
    check(f"{key}: {held} of them were ALREADY Strong a candle earlier and still read as a trade - it enters while it "
          "holds (the Trend Rider only on a fresh signal)", held > 5, held)
    # the deeper chart window merged in behind a short signal window gives the same reading
    i = strong[len(strong) // 2]
    if df.index[i + 1] - df.index[i] == pd.Timedelta(minutes=15):
        now = df.index[i + 1] + pd.Timedelta(seconds=60)
        short = df.iloc[i - 250:i + 2]
        a = tvr.evaluate(key, short, now=now, history=df.iloc[:i - 200])
        b = tvr.evaluate(key, df.iloc[:i + 2], now=now)
        check(f"{key}: 250 signal candles + the chart window = the full history's reading", a["side"] == b["side"]
              and abs(a["values"]["all"] - b["values"]["all"]) < 1e-9 and a["values"]["candles"] == i + 1,
              (a["values"]["all"], b["values"]["all"], a["values"]["candles"]))
        few = tvr.evaluate(key + "-few", df.iloc[i - 100:i + 2], now=now)
        check(f"{key}: under {tvr.MIN_CLOSED} candles and no chart window - no reading, said why",
              not few["ready"] and "200-candle" in few["why"], few)

print("2. THE VOTES, ON A QUICKENING RISE")
idx = pd.date_range("2026-09-01 09:15", periods=400, freq="15min", tz="Asia/Kolkata")
x = np.arange(400.0)
up = pd.Series(22000 + 0.01 * x ** 2 + np.where(x % 2 == 0, 0.4, -0.4), index=idx)   # a small zig-zag: RSI below 100
rise = pd.DataFrame({"Open": up - 1, "High": up + 3, "Low": up - 3, "Close": up, "Volume": 0.0}, index=idx)
rt = tvr.ratings(rise).iloc[-2]                                # an up-tick candle
check("every moving average under the price - Ichimoku included: MA rating +1 from 14 votes (no volume: VWMA left out, "
      "as on TradingView)", rt["ma"] == 1.0 and rt["ma_n"] == 14 and rt["ma_buy"] == 14, (rt["ma"], rt["ma_n"], rt["ma_buy"]))
h_, l_, c_ = rise["High"], rise["Low"], rise["Close"]
don_ = lambda n: (h_.rolling(n).max() + l_.rolling(n).min()) / 2
flat_a = (don_(9) + don_(26)) / 2
check("...Ichimoku reads the cloud as drawn (25 candles back): unshifted, 'base > span A' and 'conversion > base' could "
      "never both hold", not ((don_(26) > flat_a) & (don_(9) > don_(26))).any()
      and "((conv + base) / 2).shift(25), don(52).shift(25)" in open(os.path.join(HERE, "tv_rating.py")).read())
check("11 oscillator votes counted", rt["osc_n"] == 11, rt["osc_n"])
check("TradingView's words: Strong Buy over +0.5, Buy over +0.1, Neutral, Sell, Strong Sell under -0.5",
      [tvr.label(x) for x in (0.51, 0.5, 0.11, 0.1, 0.0, -0.1, -0.11, -0.5, -0.51)]
      == ["Strong Buy", "Buy", "Buy", "Neutral", "Neutral", "Neutral", "Sell", "Sell", "Strong Sell"])

print("3. WHAT IT WRITES INTO THE RECOMMENDATION")
chain = {"available": True, "spot": 22600.0, "expiry": "2026-10-13",
         "strikes": [{"strike": float(k), "call_ltp": 120.0 + (22600 - k) * 0.5, "put_ltp": 110.0 + (k - 22600) * 0.5,
                      "call_bid": 119.5, "call_ask": 120.5, "put_bid": 109.5, "put_ask": 110.5} for k in range(22400, 22850, 50)]}
oi = se.compute_option_chain_signal(chain) if hasattr(se, "compute_option_chain_signal") else chain
oi = dict(oi, available=True, strikes=chain["strikes"])
tech = {"last_close": 22610.0, "last_rsi": 50.0, "last_atr": 30.0, "ema_fast": 22600.0, "ema_slow": 22600.0, "trend_score": 0,
        "macd_score": 0, "macd_hist": 0.0, "rsi_score": 0, "vwap_score": 0, "di_score": 0, "st_score": 0, "vol_score": 0,
        "plus_di": 20.0, "minus_di": 20.0, "adx": 15.0, "adx_ok": False, "vwap": 22600.0, "vwap_gap": 10.0,
        "last_swing_low": 22560.0, "last_swing_high": 22660.0, "total_score": 0, "max_score": 4}
base_rec = lambda: se.build_recommendation("NIFTY", tech, oi, 50)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
vals = {"close": 22605.0, "all": 0.62, "ma": 0.93, "osc": 0.31, "ma_buy": 13, "ma_sell": 1, "ma_n": 14, "osc_buy": 4,
        "osc_sell": 1, "osc_n": 11, "swing_low": 22570.0, "swing_high": 22640.0, "candles": 1200}
ev = {"ready": True, "side": 1, "stop": 22570.0, "close": 22605.0, "fresh": True,
      "bar_close": dt.datetime(2026, 10, 8, 9, 45, tzinfo=IST).isoformat(), "values": vals}
r = tvr.apply(base_rec(), ev, "NIFTY")
R = 22610.0 - 22570.0
check("Strong Buy: a call at the money, its stop the swing low, ONE target 2.75 x the risk - the Trend Rider's levels",
      r["bias"] == "BULLISH" and r["option_type"] == "CE" and r["suggested_strike"] == 22600 and r["index_stop_loss"] == 22570.0
      and abs(r["index_targets"][2] - (22610.0 + 2.75 * R)) < 0.01 and r["target_basis"] == "rule"
      and r["live_ltp"] == 120.0, (r["bias"], r["index_stop_loss"], r["index_targets"]))
check("...its system, its name and the three rows on the card", r["rule"]["system"] == "tv_rating"
      and r["rule"]["label"] == "TradingView rating" and r["rule"]["no_cooldown"]
      and [f["key"] for f in r["rule"]["filters"]] == ["tv_all", "tv_ma", "tv_osc"]
      and r["rule"]["filters"][0]["ok"] and "Strong Buy" in r["rule"]["tag"] and "TradingView rating" in r["action"],
      (r["rule"]["filters"], r["rule"]["tag"], r["action"]))
r2 = tvr.apply(base_rec(), dict(ev, side=0, stop=None, values=dict(vals, all=0.32)), "NIFTY")
check("Buy (not Strong): no trade, and the card says what it needs", r2["bias"] == "NEUTRAL"
      and "+0.32 (Buy)" in r2["action"] and "Strong" in r2["action"] and not r2["rule"]["filters"][0]["ok"], r2["action"])
r3 = tvr.apply(base_rec(), dict(ev, side=-1, stop=22650.0, values=dict(vals, all=-0.7, ma=-1.0, osc=-0.4)), "NIFTY")
check("Strong Sell: a put, stop the swing high", r3["option_type"] == "PE" and r3["index_stop_loss"] == 22650.0
      and r3["rule"]["filters"][0]["ok"], (r3["option_type"], r3["index_stop_loss"]))
r4 = tvr.apply(base_rec(), {"ready": False, "why": "only 120 closed candles"}, "NIFTY")
check("no reading yet: waits, in words", r4["bias"] == "NEUTRAL" and "only 120 closed candles" in r4["action"], r4["action"])
rt0 = tr.apply(base_rec(), dict(ev, values={"close": 22605.0, "ema50": 22550.0, "vwap": 22580.0, "adx": 24.0,
                                            "adx_prev": 22.0, "pdi": 28.0, "mdi": 14.0, "swing_low": 22570.0,
                                            "swing_high": 22640.0, "holding": 1}), "NIFTY")
check("the Trend Rider's own recommendation unchanged by the shared levels (trend_rider.place)",
      rt0["rule"]["system"] == "trend_rider" and rt0["index_stop_loss"] == 22570.0 and rt0["sl_basis"] == "trend_rider"
      and rt0["action"].endswith("Trend Rider: every condition holds, first time, on the 15-minute close"), rt0["action"])

print("4. WHERE IT IS OFFERED")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
b = tickets.TicketBook(market="nse_index", path=path)
check("no index starts on it", [b.system_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")]
      == ["trend_rider", "trend_rider", "rules", "rules"])
b.configure(system="tv_rating", system_index="NIFTY")
b.configure(system="tv_rating", system_index="SENSEX")
b.configure(system="tv_rating", system_index="MIDCPNIFTY")
check("Nifty may be set to it; Sensex and Midcap may not (it measured mixed / worse there)",
      [b.system_for(k) for k in ("NIFTY", "SENSEX", "MIDCPNIFTY")] == ["tv_rating", "rules", "rules"])
check("saved across a restart", tickets.TicketBook(market="nse_index", path=path).system_for("NIFTY") == "tv_rating")
import json
sp = b._settings_path()
s = json.load(open(sp))
s["systems_by"]["SENSEX"] = "tv_rating"                      # a hand-edited or stale settings file
json.dump(s, open(sp, "w"))
check("a saved choice it may not have is ignored - the index keeps its default",
      tickets.TicketBook(market="nse_index", path=path).system_for("SENSEX") == "rules")
ses = b.session()
check("the page gets each index's own choices", ses["system_choices_by"]["NIFTY"] == ["rules", "trend_rider", "tv_rating"]
      and ses["system_choices_by"]["BANKNIFTY"] == ["rules", "trend_rider", "tv_rating"]
      and ses["system_choices_by"]["SENSEX"] == ["rules", "trend_rider", "camarilla"]      # camarilla.py, Sensex only (22 Oct 2026)
      and ses["system_choices_by"]["MIDCPNIFTY"] == ["rules", "trend_rider"], ses["system_choices_by"])

print("5. A TICKET THROUGH THE RULE PATH - THE TREND RIDER'S MEASURED EXIT, AND IN AGAIN WHILE IT HOLDS")
CLOCK = {"now": dt.datetime(2026, 10, 8, 9, 46, 0, tzinfo=IST)}
tickets.now_ist = lambda: CLOCK["now"]
was = (config.TREND_RIDER_EXIT, config.STEP_STOP_AT_CANDLE_CLOSE)
config.TREND_RIDER_EXIT = "ladder"                            # its exit must NOT follow the Trend Rider's switch
config.STEP_STOP_AT_CANDLE_CLOSE = 15
nb = tickets.TicketBook(market="nse_index", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
nb.configure(system="tv_rating", system_index="NIFTY")
nb.fresh_price = lambda index, rec: 120.0
def tv_rec(bar, **kw):
    rr = tvr.apply(base_rec(), dict(ev, bar_close=bar.isoformat(), **kw), "NIFTY")
    rr["index"] = "NIFTY"
    return rr
evs = nb.update("NIFTY", tv_rec(dt.datetime(2026, 10, 8, 9, 45, tzinfo=IST)))
t = nb.books["NIFTY"].trade
check("a ticket: its system, one fixed exit at T3, no reversal exit", evs and t and t["system"] == "tv_rating"
      and t["plain_exit"] and t["rule_strategy"] and t["exit_at"] == "T3", t and {k: t.get(k) for k in
                                                                                   ("system", "plain_exit", "exit_at")})
check("...always the rules' exit, even with the Trend Rider's switch on the ladder: rules_exit, no near-target steps",
      tickets.rules_exit(t) and tickets.near_plan(t) == [] and tickets.stop_steps(t) == [], tickets.near_plan(t))
t1 = t["premium_targets"][0]
CLOCK["now"] = dt.datetime(2026, 10, 8, 9, 52, tzinfo=IST)
nb.tick_price("NIFTY", t1 + 0.5)                             # T1 touched at 09:52
t = nb.books["NIFTY"].trade
check("T1 touched at 09:52: the step waits for that candle's close (10:00), as the Trend Rider's does",
      t["hit"]["T1"] and t["premium_sl"] < t1 and [p["rung"] for p in t.get("stop_pending") or []] == ["T1"],
      (t["premium_sl"], t.get("stop_pending")))
CLOCK["now"] = dt.datetime(2026, 10, 8, 10, 0, 1, tzinfo=IST)
nb.tick_price("NIFTY", t1 + 1.0)
check("at 10:00 its stop is T1", nb.books["NIFTY"].trade["premium_sl"] == t1, nb.books["NIFTY"].trade["premium_sl"])
CLOCK["now"] = dt.datetime(2026, 10, 8, 10, 7, tzinfo=IST)
nb.tick_price("NIFTY", t1 - 1.0)                             # back under T1: closed at the stepped stop
check("a fall under T1 later closes it at T1", nb.books["NIFTY"].trade["status"] != "OPEN",
      nb.books["NIFTY"].trade["status"])
CLOCK["now"] = dt.datetime(2026, 10, 8, 10, 15, 30, tzinfo=IST)
evs = nb.update("NIFTY", tv_rec(dt.datetime(2026, 10, 8, 10, 15, tzinfo=IST)))
t = nb.books["NIFTY"].trade
check("the next close with the rating still Strong Buy: in again - no cooldown, no fresh signal needed",
      evs and t["status"] == "OPEN" and t["system"] == "tv_rating", evs and t["status"])
config.TREND_RIDER_EXIT, config.STEP_STOP_AT_CANDLE_CLOSE = was

print("6. THE FEED'S HOOK")
import feeds
f = feeds.Feed("t:tv", "tv@example.invalid", "nse_index")
f.tickets.configure(system="tv_rating", system_index="NIFTY")
deep = rise.iloc[:50]
f.hist["NIFTY"] = deep
calls = []
tv_eval, tr_eval = tvr.evaluate, tr.evaluate
tvr.evaluate = lambda name, df, now=None, history=None: (calls.append(("tv", name, history is deep)) or dict(ev))
tr.evaluate = lambda name, df, now=None: (calls.append(("tr", name)) or {"ready": False, "why": "x"})
try:
    rn = f._trend_rider("NIFTY", base_rec())
    rb = f._trend_rider("BANKNIFTY", dict(base_rec(), index="BANKNIFTY"))
    rs_ = f._trend_rider("SENSEX", dict(base_rec(), index="SENSEX"))
finally:
    tvr.evaluate, tr.evaluate = tv_eval, tr_eval
check("Nifty set to it: its reading, with the chart window; Bank Nifty still the Trend Rider's; Sensex neither",
      calls == [("tv", "NIFTY", True), ("tr", "BANKNIFTY")] and rn["rule"]["system"] == "tv_rating"
      and rb["rule"]["system"] == "trend_rider" and rs_.get("rule") is None, calls)

print("7. THE CARD'S WORDS")
import explain
x = explain.explain(r)
check("the headline and verdict name the TradingView rating and the exit as it runs", x["headline"] == "BUY CE - TradingView rating"
      and "TradingView's overall rating is Strong Buy" in x["verdict"] and "T1 at T1" in x["verdict"]
      and "steps to T1 and T2" in x["levels"][0], (x["headline"], x["verdict"], x["levels"][:1]))
check("its three rows as votes, with their readings", [v["name"] for v in x["votes"]] == ["Overall rating", "Moving averages",
                                                                                          "Oscillators"]
      and x["votes"][0]["reading"].startswith("+0.62 Strong Buy"), x["votes"])
xt = explain.explain(rt0)
check("the Trend Rider's card says the exit it runs on (TREND_RIDER_EXIT \"rules\"), not the old ladder",
      "T1 at T1" in xt["verdict"] and "close to T2" not in xt["verdict"], xt["verdict"])

print("8. THE PAGE (node)")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
wsrc = open(os.path.join(HERE, "web_server.py")).read()
if not NODE:
    check("node is available", False)
else:
    a = wsrc.index("// Each index's own lots (the user, 7 Oct 2026")
    z = wsrc.index("\n}\n", wsrc.index("function lotsDash(s){")) + 3
    ta = wsrc.index("const trTag = t =>")
    tz = wsrc.index("\n", wsrc.index(': "";', ta)) + 1
    prog = r'''
const assert = require("assert");
const esc = t => String(t);
let LAST = null, LOTS = 1, LOTS_HOLD = 0, CUR = "NIFTY";
function render(){}
const els = {}; const document = {activeElement: null};
function $(id){ return els[id] || null; }
''' + wsrc[a:z] + wsrc[ta:tz] + r'''
els.kdlots = {innerHTML: "", dataset: {}, querySelectorAll(){ return []; }, onchange: null};
const s = {order: ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"], indices: {},
           session: {lots: 1, lots_by: {}, lot_choices: [1, 2], systems_by: {NIFTY: "tv_rating", BANKNIFTY: "trend_rider",
                     SENSEX: "rules", MIDCPNIFTY: "rules"}, system_choices: ["rules", "trend_rider", "tv_rating"],
                     system_choices_by: {NIFTY: ["rules", "trend_rider", "tv_rating"], BANKNIFTY: ["rules", "trend_rider", "tv_rating"],
                                         SENSEX: ["rules", "trend_rider"], MIDCPNIFTY: ["rules", "trend_rider"]}}};
LAST = s;
lotsDash(s);
const h = els.kdlots.innerHTML;
const row = k => h.slice(h.indexOf(`data-sys="${k}"`), h.indexOf("</select>", h.indexOf(`data-sys="${k}"`)));
assert.ok(row("NIFTY").includes(">TradingView rating<") && row("BANKNIFTY").includes(">TradingView rating<"), "offered on Nifty, Bank Nifty");
assert.ok(!row("SENSEX").includes("TradingView") && !row("MIDCPNIFTY").includes("TradingView"), "not on Sensex, Midcap");
assert.ok(trTag({system: "tv_rating"}).includes(">TV<") && trTag({system: "trend_rider"}).includes(">TR<")
          && trTag({system: "rules"}) === "", "the Journal's badges");
const old = {order: ["NIFTY"], indices: {}, session: {lots: 1, lots_by: {}, lot_choices: [1], systems_by: {NIFTY: "rules"},
             system_choices: ["rules", "trend_rider"]}};
els.kdlots.dataset.sig = ""; lotsDash(old);
assert.ok(els.kdlots.innerHTML.includes(">Trend Rider<"), "a server without per-index choices still shows the list");
console.log("ok:tvpage");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (rr.stdout or "") + (rr.stderr or "")
    check("the System selector offers it on Nifty and Bank Nifty only; the TV badge", "ok:tvpage" in rr.stdout
          and rr.returncode == 0, out[-900:])
check("the badge has its own colour", ".jbadge.tv{" in wsrc)

print()
print("TV RATING TEST PASSED" if not fails else f"TV RATING TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
