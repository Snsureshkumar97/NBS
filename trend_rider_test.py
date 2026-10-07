#!/usr/bin/env python3
"""The Trend Rider as a live system (trend_rider.py; the user, 7 Oct 2026: "so apply for which index best apply for
that"). Its live reading equals the 3-year backtest's (pasted_combos_study, combination 2) candle for candle on real
history; the recommendation it writes (an index option: the strike at the money, its live premium, a delta ladder, one
target at 2.75R, the swing stop); each index's system, saved; a ticket through the rule path (the fresh-price hold, no
cooldown, one fixed exit, its system recorded) whose stop never moves - no 2-hour breakeven; the feed's hook only on an
index set to it; the Dashboard's System selector (node)."""
import datetime as dt
import json
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
import pasted_combos_study as pc
import signal_engine as se
import tickets
import trend_rider as tr

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE LIVE READING IS THE BACKTEST'S, CANDLE FOR CANDLE (real history)")
hist = os.path.expanduser("~/trading-tool-logs/history")
for key in ("NIFTY", "SENSEX"):
    p = os.path.join(hist, f"{key}_15m_3y.csv")
    if not os.path.exists(p):
        check(f"{key} history present", False, p)
        continue
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata")
    df = df.iloc[:3000]
    L, S = pc.signals(df[["Open", "High", "Low", "Close", "Volume"]], 2, 20.0)
    fresh_l = np.r_[False, L[1:] & ~L[:-1]]
    fresh_s = np.r_[False, S[1:] & ~S[:-1]]
    sig_bars = [i for i in range(200, len(df) - 1) if fresh_l[i] or fresh_s[i]]
    rng = np.random.default_rng(7)
    quiet = [int(i) for i in rng.choice([i for i in range(200, len(df) - 1) if not (fresh_l[i] or fresh_s[i])], 60, replace=False)]
    bad, n_sig = [], 0
    for i in sig_bars[:80] + quiet:
        if df.index[i + 1] - df.index[i] != pd.Timedelta(minutes=15):
            continue                                   # the day's last candle: no forming candle to read it from
        now = df.index[i + 1] + pd.Timedelta(seconds=60)
        ev = tr.evaluate(key, df.iloc[:i + 2], now=now)
        want = 1 if fresh_l[i] else -1 if fresh_s[i] else 0
        lo5, hi5 = df["Low"].iloc[i - 4:i + 1].min(), df["High"].iloc[i - 4:i + 1].max()
        want_stop = lo5 if want > 0 else hi5 if want < 0 else None
        n_sig += bool(want)
        if ev.get("side") != want or (want and abs(ev["stop"] - want_stop) > 1e-9) or not ev.get("fresh"):
            bad.append((str(df.index[i]), want, ev.get("side"), ev.get("stop"), want_stop))
    check(f"{key}: {n_sig} signals and the quiet candles around them - the same side and the same stop as the backtest",
          not bad and n_sig > 20, bad[:3])

print("2. WHAT IT WRITES INTO THE RECOMMENDATION")
idx = pd.date_range("2026-10-08 09:15", periods=4, freq="15min", tz="Asia/Kolkata")
chain = {"available": True, "spot": 22600.0, "expiry": "2026-10-13",
         "strikes": [{"strike": float(k), "call_ltp": 120.0 + (22600 - k) * 0.5, "put_ltp": 110.0 + (k - 22600) * 0.5,
                      "call_bid": 119.5, "call_ask": 120.5, "put_bid": 109.5, "put_ask": 110.5} for k in range(22400, 22850, 50)]}
oi = se.compute_option_chain_signal(chain) if hasattr(se, "compute_option_chain_signal") else chain
oi = dict(oi, available=True, strikes=chain["strikes"])
tech = {"last_close": 22610.0, "last_rsi": 50.0, "last_atr": 30.0, "ema_fast": 22600.0, "ema_slow": 22600.0, "trend_score": 0,
        "macd_score": 0, "macd_hist": 0.0, "rsi_score": 0, "vwap_score": 0, "di_score": 0, "st_score": 0, "vol_score": 0,
        "plus_di": 20.0, "minus_di": 20.0, "adx": 15.0, "adx_ok": False, "vwap": 22600.0, "vwap_gap": 10.0,
        "last_swing_low": 22560.0, "last_swing_high": 22660.0, "total_score": 0, "max_score": 4}
def base_rec():
    r = se.build_recommendation("NIFTY", tech, oi, 50)
    return r
check("the tool's own engine says no trade on this reading (ADX 15)", base_rec()["bias"] == "NEUTRAL")
ev = {"ready": True, "side": 1, "stop": 22570.0, "close": 22605.0, "fresh": True, "bar_close": idx[1].isoformat(),
      "values": {"close": 22605.0, "ema50": 22550.0, "vwap": 22580.0, "adx": 24.0, "adx_prev": 22.0, "pdi": 28.0, "mdi": 14.0,
                 "swing_low": 22570.0, "swing_high": 22640.0, "holding": 1}}
r = tr.apply(base_rec(), ev, "NIFTY")
R = 22610.0 - 22570.0
check("a fresh signal: a call at the money, its stop the swing low, ONE target 2.75 x the risk",
      r["bias"] == "BULLISH" and r["option_type"] == "CE" and r["suggested_strike"] == 22600 and r["index_stop_loss"] == 22570.0
      and abs(r["index_targets"][2] - (22610.0 + 2.75 * R)) < 0.01 and r["target_basis"] == "rule",
      (r["bias"], r["suggested_strike"], r["index_stop_loss"], r["index_targets"]))
check("...its own live premium and a delta ladder off it", r["live_ltp"] == 120.0 and r["premium_source"] == "live"
      and abs(r["premium_targets"][2] - (120.0 + 2.75 * R * config.APPROX_ATM_DELTA)) < 0.01
      and abs(r["premium_stop_loss"] - (120.0 - R * config.APPROX_ATM_DELTA)) < 0.01, (r["premium_targets"], r["premium_stop_loss"]))
check("...the card's conditions and the system's name ride with it", r["rule"]["system"] == "trend_rider"
      and r["rule"]["no_cooldown"] and [f["ok"] for f in r["rule"]["filters"]] == [True, True, True, True, True], r["rule"]["filters"])
r2 = tr.apply(base_rec(), dict(ev, side=1, stop=22620.0), "NIFTY")
check("a stop the price has already gone through: no trade, in words", r2["bias"] == "NEUTRAL" and "past its stop" in r2["action"], r2["action"])
r3 = tr.apply(base_rec(), dict(ev, side=0, stop=None), "NIFTY")
check("no fresh signal: no trade, and the card says why", r3["bias"] == "NEUTRAL" and r3["option_type"] is None
      and "first one" in r3["action"], r3["action"])
r4 = tr.apply(base_rec(), dict(ev, side=-1, stop=22650.0, values=dict(ev["values"], close=22590.0, ema50=22650.0, vwap=22620.0,
                                                                          pdi=12.0, mdi=27.0)), "NIFTY")
check("a put: below EMA 50 and VWAP, stop the swing high, the put's own premium", r4["option_type"] == "PE"
      and r4["index_stop_loss"] == 22650.0 and r4["live_ltp"] == 110.0, (r4["option_type"], r4["live_ltp"]))
r5 = tr.apply(base_rec(), ev, "NIFTY", avoid_strikes=[(22600.0, "CE")])
check("a strike already traded today is not bought again (the next free one)", r5["suggested_strike"] != 22600
      and r5["strike_swap"] is not None, (r5["suggested_strike"], r5["strike_swap"]))

print("3. EACH INDEX'S SYSTEM")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
b = tickets.TicketBook(market="nse_index", path=path)
check("defaults: Nifty and Bank Nifty on the Trend Rider, Sensex and Midcap on the tool's rules",
      [b.system_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")] == ["trend_rider", "trend_rider", "rules", "rules"])
b.configure(system="rules", system_index="NIFTY")
b.configure(system="trend_rider", system_index="SENSEX")
b.configure(system="nonsense", system_index="BANKNIFTY")
b.configure(system="rules", system_index="NOTANINDEX")
check("one index at a time; an unknown system or index changes nothing",
      [b.system_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX")] == ["rules", "trend_rider", "trend_rider"])
check("saved across a restart", tickets.TicketBook(market="nse_index", path=path).system_for("SENSEX") == "trend_rider")
check("the page gets every index's system and the choices", b.session()["systems_by"]["SENSEX"] == "trend_rider"
      and b.session()["system_choices"] == ["rules", "trend_rider"])
cb = tickets.TicketBook(market="crypto", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
check("crypto: always its own rules, no choice offered", cb.system_for("BTC") == "rules" and cb.session()["system_choices"] == [])

print("4. A TICKET THROUGH THE RULE PATH")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CLOCK = {"now": dt.datetime(2026, 10, 8, 9, 46, 0, tzinfo=IST)}
tickets.now_ist = lambda: CLOCK["now"]
nb = tickets.TicketBook(market="nse_index", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
nb.configure(lots=2, lots_index="NIFTY")
FRESH = {"px": None}
nb.fresh_price = lambda index, rec: FRESH["px"]
def tr_rec(**kw):
    e = dict(ev, bar_close=dt.datetime(2026, 10, 8, 9, 45, tzinfo=IST).isoformat(), **kw)
    rr = tr.apply(base_rec(), e, "NIFTY")
    rr["index"] = "NIFTY"
    return rr
evs = nb.update("NIFTY", tr_rec())
check("its own option's live price not in yet: WAITING FOR PRICE, no ticket", not evs
      and (nb.books["NIFTY"].wait_reason or ("",))[0] == "price_wait", nb.books["NIFTY"].wait_reason)
FRESH["px"] = 120.0
evs = nb.update("NIFTY", tr_rec())
t = nb.books["NIFTY"].trade
check("then a ticket: one fixed exit (T3), no reversal exit, its system, this index's lots",
      evs and t and t["plain_exit"] and t["rule_strategy"] and t["exit_at"] == "T3" and t["system"] == "trend_rider"
      and t["lots"] == 2, t and {k: t.get(k) for k in ("plain_exit", "rule_strategy", "exit_at", "system", "lots")})
check("...and the page's ticket says it is the Trend Rider's", nb.public("NIFTY")["ticket"]["system"] == "trend_rider")
sl0 = t["premium_sl"]
CLOCK["now"] = dt.datetime(2026, 10, 8, 12, 30, tzinfo=IST)          # 2h44m later, T1 never touched
rr = tr_rec(side=0, stop=None)
rr["live_ltp"] = 121.0
nb.update("NIFTY", rr)
t = nb.books["NIFTY"].trade
check("2h44m later, T1 untouched: its stop has NOT moved to breakeven (the Trend Rider was tested without it)",
      t["status"] == "OPEN" and t["premium_sl"] == sl0, (t["status"], t["premium_sl"], sl0))
src = open(os.path.join(HERE, "tickets.py")).read()
check("no wait after an exit for it (its first-close rule is its own spacing)",
      'cd = 0 if info.get("no_cooldown") else _cfg("REENTRY_COOLDOWN_MIN", 0)' in src)

import csv as _csv
import trade_log
old = os.path.join(tempfile.mkdtemp(), "trades.csv")
with open(old, "w", newline="") as fh:                       # a log as it is on the server today: no "system" column
    w = _csv.DictWriter(fh, fieldnames=trade_log.FIELDS[:-1]); w.writeheader()
    w.writerow({"trade_id": "NIFTY-OLD", "event": "OPEN", "date": "2026-10-06", "index": "NIFTY", "entry": "150"})
trade_log.log_open(dict(nb.books["NIFTY"].trade, trade_id="NIFTY-TR"), tr_rec(), CLOCK["now"], path=old)
rows = trade_log._read_rows(old)
hdr = open(old).readline().strip().split(",")
check("a log from before the System column: upgraded in place, the old row intact, the new one saying trend_rider",
      hdr[-1] == "system" and rows[0]["trade_id"] == "NIFTY-OLD" and rows[0]["entry"] == "150" and rows[0].get("system") == ""
      and rows[1]["system"] == "trend_rider", (hdr[-2:], [r.get("system") for r in rows]))

print("5. THE FEED'S HOOK")
import feeds
f = feeds.Feed("t:tr", "tr@example.invalid", "nse_index")
calls = []
tr_eval = tr.evaluate
tr.evaluate = lambda name, df, now=None: (calls.append(name) or dict(ev))
try:
    rn = f._trend_rider("NIFTY", base_rec())
    rs_ = f._trend_rider("SENSEX", dict(base_rec(), index="SENSEX"))
finally:
    tr.evaluate = tr_eval
check("an index on the Trend Rider gets its reading; one on the tool's rules does not",
      calls == ["NIFTY"] and rn["rule"]["system"] == "trend_rider" and rs_.get("rule") is None, calls)
fsrc = open(os.path.join(HERE, "feeds.py")).read()
check("both readings (the refresh and the every-second recompute) pass through it",
      fsrc.count("self._trend_rider(name, rec)") == 2)
check("the page's reading carries its conditions", "out[\"rule\"] = rec.get(\"rule\")             # the Trend Rider's" in fsrc)

print("6. THE DASHBOARD'S SYSTEM SELECTOR (node)")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
wsrc = open(os.path.join(HERE, "web_server.py")).read()
if not NODE:
    check("node is available", False)
else:
    a = wsrc.index("// Each index's own lots (the user, 7 Oct 2026")
    z = wsrc.index("\n}\n", wsrc.index("function lotsDash(s){")) + 3
    prog = r'''
const assert = require("assert");
const esc = t => String(t);
let LAST = null, LOTS = 1, LOTS_HOLD = 0, CUR = "NIFTY";
function render(){}
const posted = []; let reply = null;
global.fetch = async (url, opt) => { posted.push(Object.fromEntries(opt.body)); return {json: async () => reply}; };
class Sel { constructor(attrs, v){ this.dataset = attrs; this.value = v; this.disabled = false; }
  closest(q){ return (q.includes("data-sys") ? ("sys" in this.dataset) : ("k" in this.dataset)) ? this : null; } }
const els = {}; const document = {activeElement: null};
function $(id){ return els[id] || null; }
''' + wsrc[a:z] + r'''
(async () => {
  let SYS = [];
  els.kdlots = {innerHTML: "", dataset: {}, querySelectorAll(q){ return q.includes("data-sys") ? SYS : []; }, onchange: null};
  const s = {order: ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"], indices: {},
             session: {lots: 3, lots_by: {}, lot_choices: [1, 2, 3], systems_by: {NIFTY: "trend_rider", BANKNIFTY: "trend_rider",
                       SENSEX: "rules", MIDCPNIFTY: "rules"}, system_choices: ["rules", "trend_rider"]}, live: {enabled: {NIFTY: true}}};
  LAST = s;
  lotsDash(s);
  const h = els.kdlots.innerHTML;
  assert.ok(h.includes(">System<") && ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"].every(k => h.includes(`data-sys="${k}"`))
            && h.includes(">Trend Rider<") && h.includes(">Current rules<"), "a System selector per index");
  SYS = ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"].map(k => new Sel({sys: k}, "rules"));
  lotsDash(s);
  assert.deepStrictEqual(SYS.map(x => x.value), ["trend_rider", "trend_rider", "rules", "rules"], "each shows its index's system");
  reply = {ok: true, session: {systems_by: {NIFTY: "trend_rider", BANKNIFTY: "trend_rider", SENSEX: "trend_rider", MIDCPNIFTY: "rules"}}};
  els.kdlmsg = {textContent: ""};
  SYS[2].value = "trend_rider";
  await els.kdlots.onchange({target: SYS[2]});
  assert.deepStrictEqual(posted[0], {system: "trend_rider", system_index: "SENSEX"}, "one index's system is sent");
  assert.ok(els.kdlmsg.textContent.includes("SENSEX: traded by Trend Rider"), els.kdlmsg.textContent);
  reply = {ok: false};
  SYS[0].value = "rules";
  await els.kdlots.onchange({target: SYS[0]});
  assert.ok(SYS[0].value === "trend_rider" && els.kdlmsg.textContent.includes("could not be changed"), "a refusal puts it back");
  const crypto = {order: ["BTC"], session: {lots: 0.1, lot_choices: [0.1], systems_by: {}, system_choices: []}};
  els.kdlots.dataset.sig = ""; lotsDash(crypto);
  assert.ok(!els.kdlots.innerHTML.includes(">System<"), "no System row where there is no choice (crypto)");
  console.log("ok:system");
})().catch(e => { console.error(e); process.exit(1); });
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (rr.stdout or "") + (rr.stderr or "")
    check("a System selector per index; one index changed at a time; a refusal put back; none on crypto",
          "ok:system" in rr.stdout and rr.returncode == 0, out[-900:])
check("the TR badge on the Positions table and the open-trade box", "trTag(t) + tradeTag" in wsrc and "${trTag(tk)}${tradeTag(tk)}" in wsrc)

print()
print("TREND RIDER TEST PASSED" if not fails else f"TREND RIDER TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
